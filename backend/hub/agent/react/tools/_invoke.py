"""ReAct tool 调用底层业务函数的统一包装。

ReAct @tool 函数都通过本 helper 调 erp_tools / analyze_tools / generate_tools /
draft_tools 真业务函数。统一做：
  1. require_permissions(perm) — 权限 fail-closed（无权限抛 BizError → 本 helper 捕获,
     转中文 error dict 返给 LLM,LLM 自然语言告诉用户"权限不足: ..."）
  2. log_tool_call — 写 tool_call_log（admin 决策链审计）
  3. 注入 ctx kwargs — acting_as_user_id 来自 ContextVar tool_ctx
  4. v11: tool 调用结果中提取 customer_id / product_id 写入 SessionMemory.entity_refs,
     供下一轮 MemoryLoader 拉取对应 customer/product memory 注入 prompt

⚠️ **关键设计**：BizError 必须由 wrapper 捕获,转 error dict。
原因：ReAct LangGraph ToolNode 默认 `handle_tool_errors=True`,会把任何 raise 出 @tool
的异常吃掉,生成英文 ToolMessage `"Error: BizError(...) Please fix your mistakes."`。
这样 dingtalk_inbound 层的 `except BizError → 中文翻译` 路径**永远走不到** —
LLM 看到英文 ToolMessage 会胡乱回 / retry / 假装权限够,而不是中文友好告知用户。
fix：本 helper 主动捕获 BizError 转 dict,LLM 看到 {"error": "权限不足: ..."} 自然
转给用户。

不通过 ToolRegistry.call 是因为：
  - ReAct write tool 的 confirm 流程跟 ToolRegistry 内置的两步协议不兼容
  - ToolRegistry.call 会再做 strict schema 校验,跟 LangChain @tool 自带 schema 重复
"""
from __future__ import annotations
import logging
from typing import Any, Awaitable, Callable

from hub.agent.memory.session import SessionMemory
from hub.agent.react.context import tool_ctx
from hub.agent.tools.entity_extractor import EntityExtractor
from hub.error_codes import BizError
from hub.observability.tool_logger import log_tool_call
from hub.permissions import require_permissions

logger = logging.getLogger("hub.agent.react.tools._invoke")

# 模块级单例 — worker.py 启动时通过 set_session_memory 注入;
# 未注入时 entity_refs 不写入(向后兼容,不影响业务)。
_session_memory: SessionMemory | None = None
_entity_extractor = EntityExtractor()


def set_session_memory(session: SessionMemory | None) -> None:
    """worker.py / gateway 启动时注入 SessionMemory 单例。

    传 None 显式禁用 entity_refs 写入(测试场景)。
    """
    global _session_memory
    _session_memory = session


async def invoke_business_tool(
    *,
    tool_name: str,
    perm: str,
    args: dict,
    fn: Callable[..., Awaitable[Any]],
    extra_ctx_kwargs: dict | None = None,
) -> Any:
    """统一调底层业务函数。

    Args:
        tool_name: 写 tool_call_log 用的名字（按底层函数名,如 "search_customers"）。
        perm: 权限 code（如 "usecase.query_customer.use"）。
        args: LLM 传的业务 args（不含 hub_user_id 等 ctx 字段）。
        fn: 底层业务函数（如 erp_tools.search_customers）。函数必须接
            `acting_as_user_id` kwarg；其它 ctx kwargs 通过 extra_ctx_kwargs 加。
        extra_ctx_kwargs: write 类底层需要的额外 ctx（如 hub_user_id /
            conversation_id / confirmation_action_id）。read 类一般为 None。

    Returns:
        fn 的返值;权限/业务异常时返 {"error": "..."} dict。
    """
    c = tool_ctx.get()
    if c is None:
        raise RuntimeError("tool_ctx 未 set — react agent 入口必须先 set 才能调 tool")

    # 1. 权限 fail-closed — BizError 必须捕获转 dict（详见模块 docstring "关键设计"）
    try:
        await require_permissions(c["hub_user_id"], [perm])
    except BizError as e:
        return {"error": f"权限不足: {e}"}

    # 2. 审计 log + 调 fn
    async with log_tool_call(
        conversation_id=c["conversation_id"],
        hub_user_id=c["hub_user_id"],
        round_idx=0,
        tool_name=tool_name,
        args=args,
    ) as log_ctx:
        kwargs = {
            **args,
            "acting_as_user_id": c.get("acting_as") or c["hub_user_id"],
        }
        if extra_ctx_kwargs:
            kwargs.update(extra_ctx_kwargs)
        try:
            result = await fn(**kwargs)
        except BizError as e:
            # 业务级 BizError（如 ERP 拒访问） — 同样转 dict 让 LLM 友好返
            result = {"error": str(e)}
        log_ctx.set_result(result)

        # v11: 提取 customer_id / product_id 写入 SessionMemory entity_refs
        # 失败仅 log,不影响业务返回(memory 是增量功能)
        if _session_memory is not None and isinstance(result, (dict, list)):
            try:
                refs = _entity_extractor.extract(result)
                if refs.customer_ids or refs.product_ids:
                    await _session_memory.add_entity_refs(
                        c["conversation_id"],
                        c["hub_user_id"],
                        customer_ids=refs.customer_ids,
                        product_ids=refs.product_ids,
                    )
            except Exception:
                logger.exception(
                    "SessionMemory.add_entity_refs 失败 conv=%s tool=%s（不影响业务）",
                    c.get("conversation_id"), tool_name,
                )

        return result
