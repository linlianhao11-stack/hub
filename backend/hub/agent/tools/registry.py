# hub/agent/tools/registry.py
from __future__ import annotations
import inspect
import logging
import time
from typing import Any, Callable, get_type_hints

from hub.permissions import require_permissions, has_permission
from hub.observability.tool_logger import log_tool_call
from hub.agent.tools.types import (
    ToolType, ToolDef,
    UnconfirmedWriteToolError, MissingConfirmationError, ClaimFailedError,
    ToolNotFoundError, ToolArgsValidationError, ToolRegistrationError,
)
from hub.agent.tools.confirm_gate import ConfirmGate
from hub.agent.tools.entity_extractor import EntityExtractor
from hub.agent.memory.session import SessionMemory

logger = logging.getLogger("hub.agent.tools.registry")


class ToolRegistry:
    SCHEMA_CACHE_TTL = 300  # 5 min

    def __init__(self, *, confirm_gate: ConfirmGate, session_memory: SessionMemory):
        self._tools: dict[str, ToolDef] = {}
        self._user_schema_cache: dict[int, tuple[float, list[dict]]] = {}
        self.confirm_gate = confirm_gate
        self.session_memory = session_memory
        self.entity_extractor = EntityExtractor()

    def register(self, name: str, fn: Callable, *, perm: str, description: str,
                 tool_type: ToolType):
        """注册 tool；tool_type 必填。

        v9 round 2 P1：写类 tool fn 必须在签名声明 confirmation_action_id 参数（fail fast）。
        理由：ToolRegistry.call 在 claim_action 之后才注入这个参数；如果 fn 没声明，
        调用时会 RuntimeError，但此时 claim 已原子删除 confirmed+pending，
        且错误发生在 restore try/except 之前 → 用户的确认态被消费且无法 restore。
        把检查放到 register() 时做，启动期就能发现，绝不进入运行期消费确认态的窗口。
        """
        sig = inspect.signature(fn)
        hints = get_type_hints(fn)

        # ✱ register-time 硬校验：写类 tool 必须声明 confirmation_action_id（v9 round 2 P1-#1）
        if tool_type in (ToolType.WRITE_DRAFT, ToolType.WRITE_ERP):
            if "confirmation_action_id" not in sig.parameters:
                raise ToolRegistrationError(
                    f"写类 tool '{name}' 必须在函数签名声明 confirmation_action_id: str 参数，"
                    "且实现内部用它做幂等查询/唯一约束。"
                    "ToolRegistry.restore_action 依赖这一点保证失败重试不重复副作用。"
                )

        params = self._build_json_schema(sig, hints)

        self._tools[name] = ToolDef(
            name=name, fn=fn, perm=perm,
            description=description,
            tool_type=tool_type,
            schema={
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": params,
                },
            },
        )

    # 这些参数由 ToolRegistry 注入内部 context；不暴露给 LLM（schema 排除）
    # confirmation_action_id 双重身份（v8 round 2 P1）：
    #   - LLM 调写 tool 时必须在 args 里带它（用于 ToolRegistry 入口 claim_action 校验）
    #   - 进 fn 时由 ToolRegistry 注入（被 pop 出 args 后从 inject_ctx 重新塞回）
    #   - schema 不含它：LLM 是从 ChainAgent confirm hint 学到该传，不通过 schema 提示
    _INTERNAL_CTX_PARAMS = (
        "self", "ctx",
        "acting_as_user_id", "hub_user_id", "conversation_id",
        "confirmation_token", "confirmation_action_id",
    )

    def _build_json_schema(self, sig, hints):
        properties = {}
        required = []
        for pname, param in sig.parameters.items():
            if pname in self._INTERNAL_CTX_PARAMS:
                continue
            # 过滤 *args（VAR_POSITIONAL）和 **kwargs（VAR_KEYWORD）类型参数
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            ptype = hints.get(pname, str)
            properties[pname] = {"type": self._py_to_json_type(ptype)}
            if param.default == inspect.Parameter.empty:
                required.append(pname)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,  # v11 round 2 I-4：strict mode 防 LLM 塞垃圾字段
        }

    def _py_to_json_type(self, t):
        """Python type → OpenAI function schema type。"""
        if t is int: return "integer"
        if t is str: return "string"
        if t is float: return "number"
        if t is bool: return "boolean"
        # typing.List / typing.Dict / typing.Optional[X] 等需要解 origin
        origin = getattr(t, "__origin__", None)
        if origin is list: return "array"
        if origin is dict: return "object"
        if origin is type(None) or t is type(None): return "null"
        # Optional[X] 取 X
        args = getattr(t, "__args__", ())
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return self._py_to_json_type(non_none[0])
        return "string"  # 默认 fallback

    async def schema_for_user(self, hub_user_id: int) -> list[dict]:
        """按用户权限过滤 + 5 min cache。"""
        cached = self._user_schema_cache.get(hub_user_id)
        if cached and time.monotonic() < cached[0]:
            return cached[1]

        schemas = []
        for tool in self._tools.values():
            if await has_permission(hub_user_id, tool.perm):
                schemas.append(tool.schema)
        self._user_schema_cache[hub_user_id] = (
            time.monotonic() + self.SCHEMA_CACHE_TTL, schemas,
        )
        return schemas

    async def call(self, name: str, args: dict, *, hub_user_id: int,
                   acting_as: int, conversation_id: str, round_idx: int) -> Any:
        """统一入口：权限 → schema 校验 → claim（写类）→ 调 fn → 失败 restore / 成功直接走 → 提取实体。

        v6 round 2 P1 加固：
        1. require_permissions / _validate_args 移到 claim **之前**（review v6 P1-#1）
        2. claim_action 现在原子同时 HDEL confirmed + pending（持久终态，review v6 P1-#2）
        3. 失败 restore 也用 Lua 原子还回 confirmed + pending

        v7 round 2 P1 加固：
        4. **入口处复制 args**（review v7 P1-#1）—— 不污染 ChainAgent 持有的原 args dict；
           tests/concurrent path 复用 payload 不会因为第一次 pop 把 token 删掉而第二次失败
        5. **MissingConfirmationError vs ClaimFailedError 两个 subclass**（review v7 P1-#2）：
           - 两者都是 UnconfirmedWriteToolError（向后兼容）
           - 但语义不同：missing → ChainAgent 应 add_pending；claim_failed → 不 add（避免重复 pending）
        """
        tool = self._tools.get(name)
        if not tool:
            raise ToolNotFoundError(name)

        # ❶ v7 round 2 P1-#1：入口复制 args，所有 pop/inject/log/fn 都用副本，原 args 不被污染
        tool_args = dict(args)

        # ❷ 写类 tool：从副本 pop confirmation 字段（schema 校验不含它们）
        is_write = tool.tool_type in (ToolType.WRITE_DRAFT, ToolType.WRITE_ERP)
        action_id: str | None = None
        token: str | None = None
        had_confirmation_fields = False
        if is_write:
            action_id = tool_args.pop("confirmation_action_id", None)
            token = tool_args.pop("confirmation_token", None)
            had_confirmation_fields = bool(action_id) or bool(token)

        # ❸ 权限 + args schema 校验（v6 round 2 P1-#1：移到 claim 前；失败不消费 confirmed token）
        await require_permissions(hub_user_id, [tool.perm])
        self._validate_args(tool_args, tool.schema)

        # ❹ 写类 tool 硬门禁：原子 claim（claim 内已含 token / tool_name / args 一致性校验）
        bundle: dict | None = None
        if is_write:
            bundle = await self.confirm_gate.claim_action(
                conversation_id, hub_user_id, action_id, token, name, tool_args,
            )
            if bundle is None:
                # v7 round 2 P1-#2：分两类失败 → ChainAgent 据此决定是否 add_pending
                if not had_confirmation_fields:
                    # 第一次调写 tool，没传 confirmation 字段 → ChainAgent 应 add_pending + 让 LLM 出预览
                    await self._log_blocked_call(conversation_id, round_idx, name, tool_args,
                                                  reason="missing_confirmation")
                    raise MissingConfirmationError(
                        f"写类 tool '{name}' 还未经用户确认。请用 text 把操作预览发给用户，"
                        "用户回'是'后由 ChainAgent 自动注入 (action_id, token) 重试。"
                    )
                else:
                    # 传了 confirmation 字段但 claim 失败（错 token / 跨 action 复用 / 并发输家 / stale）
                    # → ChainAgent **不应**再 add_pending（避免重复 pending）；
                    #   只让 LLM 重新出预览让用户重新确认（claim 内部已对篡改场景做 restore）
                    await self._log_blocked_call(conversation_id, round_idx, name, tool_args,
                                                  reason="claim_failed")
                    raise ClaimFailedError(
                        f"写类 tool '{name}' 的 confirmation_token/action_id 无效"
                        "（已被消费 / token 不匹配 / args 被改 / 跨 action 复用）。"
                        "请用 text 重新发预览给用户并请用户重新确认；不要复用旧 token。"
                    )

        # ❺ 调 tool（注入内部 context）+ 记 log + 失败 restore_action（confirmed + pending 一起还）
        async with log_tool_call(
            conversation_id=conversation_id, round_idx=round_idx,
            tool_name=name, args=tool_args,
        ) as ctx:
            inject_ctx = {
                "acting_as_user_id": acting_as,
                "hub_user_id": hub_user_id,
                "conversation_id": conversation_id,
            }
            # v8 round 2 P1：写类 tool 把 action_id 作为内部 idempotency key 注入；
            # tool fn 用它查 / 写 DB 唯一索引保证 restore 后重试是真幂等的（DB 副作用也能去重）。
            # 重要：claim 已校验 LLM 传入 confirmation_action_id 的一致性；这里注入的就是同一个值。
            # 写类 tool 的签名校验在 register() 时已 fail-fast（v9 round 2 P1-#1），
            # 这里不再二次校验 —— 任何 register 通过的写 tool 一定声明了 confirmation_action_id 参数。
            if is_write and action_id is not None:
                inject_ctx["confirmation_action_id"] = action_id

            # 只注入 fn 实际接受的参数（避免 TypeError unexpected keyword）
            sig = inspect.signature(tool.fn)
            kwargs = {**tool_args}
            for k, v in inject_ctx.items():
                if k in sig.parameters:
                    kwargs[k] = v

            try:
                result = await tool.fn(**kwargs)
            except Exception:
                # ❻ 写类 tool 执行失败 → restore_action（让用户/重试用同 token 再来）
                # 安全前提（v8 round 2 P1）：写 tool fn 必须用 confirmation_action_id 做幂等键，
                # DB 唯一约束 + 查/插冲突回查保证重试不重复副作用。
                if is_write and bundle is not None and action_id is not None:
                    try:
                        await self.confirm_gate.restore_action(
                            conversation_id, hub_user_id, action_id, bundle,
                        )
                    except Exception:
                        # restore 本身也失败：罕见的 Redis 故障；记日志让 30 min TTL 自然清理。
                        # 这里不重抛 restore 错误，向上抛原始 tool exception 以保留语义。
                        logger.exception(
                            "restore_action failed (conv=%s user=%s action=%s); "
                            "TTL will eventually clean up, but user may need to reconfirm",
                            conversation_id, hub_user_id, action_id,
                        )
                raise

            ctx.set_result(result)

            # 写类 tool 成功路径：claim 时已原子删除 confirmed + pending，无需任何 cleanup（持久终态）

            # ❼ 提取实体引用写回 session memory（review P2-#8）
            refs = self.entity_extractor.extract(result)
            if refs.has_any():
                await self.session_memory.add_entity_refs(
                    conversation_id,
                    customer_ids=refs.customer_ids,
                    product_ids=refs.product_ids,
                )
            return result

    def _validate_args(self, args: dict, schema: dict):
        """jsonschema validate；不符抛 ToolArgsValidationError。"""
        from jsonschema import validate, ValidationError
        try:
            validate(instance=args, schema=schema["function"]["parameters"])
        except ValidationError as e:
            raise ToolArgsValidationError(str(e)) from e

    async def _log_blocked_call(self, conversation_id, round_idx, name, args, *, reason):
        """拦截掉的 tool call 也写一条 tool_call_log（error 字段标 reason）。

        可观测性不阻塞业务：DB 写入失败仅记 logger.exception，不向上抛（review v11 round 2 I-1）。
        """
        try:
            from hub.models.conversation import ToolCallLog
            from hub.observability.tool_logger import truncate_for_log
            await ToolCallLog.create(
                conversation_id=conversation_id, round_idx=round_idx,
                tool_name=name, args_json=truncate_for_log(args, max_size_kb=10),
                error=f"blocked: {reason}",
            )
        except Exception:
            logger.exception(
                "_log_blocked_call 写入失败（不阻塞业务）"
                " conv=%s tool=%s reason=%s",
                conversation_id, name, reason,
            )
