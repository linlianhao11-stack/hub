"""Plan 6 Task 6：ChainAgent — LLM tool-calling 多 round 主循环 + RE_CONFIRM 链路。"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from hub.agent.context_builder import ContextBuilder
from hub.agent.llm_client import AgentLLMClient
from hub.agent.memory.loader import MemoryLoader
from hub.agent.memory.session import SessionMemory
from hub.agent.tools.confirm_gate import ConfirmGate
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools.types import (
    ClaimFailedError,
    MissingConfirmationError,
)
from hub.agent.types import (
    AgentMaxRoundsError,
    AgentResult,
    PromptTooLargeError,
)
from hub.capabilities.deepseek import LLMParseError, LLMServiceError
from hub.error_codes import BizError
from hub.models.conversation import ConversationLog

logger = logging.getLogger("hub.agent.chain_agent")


class ChainAgent:
    """Plan 6 核心：LLM tool-calling 多 round 主循环。

    职责：
    - 装载 memory → 拼 system prompt
    - 多 round LLM 调用 + tool 调度
    - 写门禁失败分流（MissingConfirmation 加 pending；ClaimFailed 不加）
    - 写 conversation_log + tokens 累计
    - RE_CONFIRM 链路：user_just_confirmed=True → confirm_all_pending → confirm_hint
    """

    # v8 staging review #9/#10：合同/调价/批量凭证等复杂写操作单条消息内
    # 常需 5-10 次 tool 调用（搜客户 + 搜商品 N 次 + 拉历史价 + 验库存 + 拉账套 +
    # 拉商品详情 + 写草稿 dry-run + confirm 后再调）。
    # 调成 20 给写场景充足余量；正常 query 场景 2-3 round 完成不受影响（提前 break）。
    # 极端场景 20 × 5s = 100s 延迟，但 ChainAgent 看 LLM 不再调 tool 即输出 final text
    # break，平均跑满概率极低；老 round 已自动摘要不会撞 token budget。
    MAX_ROUNDS = 20
    # deepseek-v4-flash 上下文 128K，留充足余量 + 不影响 attention 质量；
    # 32K 是 v8 review P3：18K 在用户首次试用时频繁撞上界，搭配 prompt 输出收紧后翻 ~80% 余量
    # 单次成本变化：18K→32K 约 +78%（按 ¥0.001/K input 算 +¥0.014/call），可接受
    MAX_PROMPT_TOKEN = 32_000
    # 长 prompt 需要更长 TTFB；30s 在 18K 时刚好，32K 时偶尔擦边——给到 45s
    LLM_TIMEOUT = 45.0

    def __init__(self, *,
                 llm: AgentLLMClient,
                 registry: ToolRegistry,
                 confirm_gate: ConfirmGate,
                 session_memory: SessionMemory,
                 memory_loader: MemoryLoader,
                 context_builder: ContextBuilder | None = None):
        self.llm = llm
        self.registry = registry
        self.confirm_gate = confirm_gate
        self.session_memory = session_memory
        self.memory_loader = memory_loader
        self.context_builder = context_builder or ContextBuilder()

    async def run(self, user_message: str, *,
                  hub_user_id: int,
                  conversation_id: str,
                  acting_as: int,
                  channel_userid: str = "",
                  user_just_confirmed: bool = False) -> AgentResult:
        """运行一轮对话。

        ⚠️ 已知限制（Task 10/19 的待办）：
           SessionMemory 当前只持久化 user / tool / assistant text；assistant.tool_calls
           原始结构（plan §2185 raw_message）未持久化。后果：单次 run() 内多 round 完整
           OK；但跨进程恢复（kill -9 + 重启）后 session_memory.load() 返回的 history
           可能含孤儿 tool 消息，**不能直接喂回 LLM**。
           Task 10 接 inbound 时如需跨 turn 恢复多 round 上下文，需扩展 ConversationMessage
           或在 load 路径过滤孤儿 tool。本任务只保证单次 run() 内的协议完整。

        Args:
            user_message: 用户输入
            hub_user_id: 当前用户 hub id（用于 memory 加载 + 权限）
            conversation_id: 会话 id
            acting_as: ERP user_id（tool 用 X-Acting-As-User-Id 注入）
            channel_userid: 钉钉 staffId（写 conversation_log 用）
            user_just_confirmed: inbound 识别"是/确认"时传 True
        """
        started_at = datetime.now(UTC)
        conv_log = await self._open_conversation_log(
            conversation_id=conversation_id,
            hub_user_id=hub_user_id,
            channel_userid=channel_userid,
            started_at=started_at,
        )

        # ❶ 处理 user_just_confirmed
        confirm_hint = None
        if user_just_confirmed:
            confirmed_actions = await self.confirm_gate.confirm_all_pending(
                conversation_id, hub_user_id,
            )
            if confirmed_actions:
                confirm_hint = self._build_confirm_hint(confirmed_actions)

        # ❷ 装载 memory + tools schema
        try:
            memory = await self.memory_loader.load(
                hub_user_id=hub_user_id, conversation_id=conversation_id,
            )
            tools_schema = await self.registry.schema_for_user(hub_user_id)
        except Exception:
            logger.exception("memory/tools 加载失败 conv=%s", conversation_id)
            await self._close_conversation_log(
                conv_log, "failed_system", error="memory/tools 加载失败",
                rounds_count=0, tokens_used=0,
            )
            return AgentResult.error_result("内部错误，请稍后重试")

        # ❸ 主循环
        history: list[dict] = []
        # 初始化 history：从 session memory 加载已有对话
        try:
            session_history = await self.session_memory.load(conversation_id)
            for m in session_history.messages:
                msg: dict = {"role": m.role, "content": m.content}
                if m.tool_call_id:
                    msg["tool_call_id"] = m.tool_call_id
                history.append(msg)
        except Exception:
            logger.exception("session_memory.load 失败（用空 history 兜底）conv=%s", conversation_id)

        # v8 staging review #13：加载上轮 round_state（state reducer 模式）
        # 这是跨轮"已确认实体 + 上轮意图"摘要，注入 must_keep 让 LLM 看到，
        # 不用重新 search 拿 ID（避免数字幻觉、避免重复确认）
        prev_round_state: dict | None = None
        try:
            prev_round_state = await self.session_memory.get_round_state(conversation_id)
        except Exception:
            logger.exception("session_memory.get_round_state 失败 conv=%s", conversation_id)

        total_prompt_tokens = 0
        total_completion_tokens = 0
        final_status = "success"
        error_summary: str | None = None
        round_idx = -1  # 显式初始化，finally 里 rounds_count 用

        try:
            # 用户消息先 append session
            try:
                await self.session_memory.append(
                    conversation_id, role="user", content=user_message,
                )
            except Exception:
                logger.exception("session_memory.append user 失败 conv=%s", conversation_id)

            for round_idx in range(self.MAX_ROUNDS):
                try:
                    messages = await self.context_builder.build_round(
                        round_idx=round_idx,
                        base_memory=memory,
                        tools_schema=tools_schema,
                        conversation_history=history,
                        round_state=prev_round_state,
                        latest_user_message=user_message if round_idx == 0 else None,
                        confirm_state_hint=confirm_hint if round_idx == 0 else None,
                        budget_token=self.MAX_PROMPT_TOKEN,
                    )
                except PromptTooLargeError as e:
                    # Plan §1949 提"PromptTooLargeError → ChainAgent fallback rule"。
                    # 当前简化为返 error_result；fallback 到 RuleParser 的实际逻辑由 Task 10
                    # inbound handler 接收 error_result 后决定（agent 失败时降级回 rule）。
                    final_status = "failed_system"
                    error_summary = f"prompt 超 budget: {e}"
                    return AgentResult.error_result("对话太复杂，请简化后重发")

                try:
                    llm_resp = await asyncio.wait_for(
                        self.llm.chat(messages, tools=tools_schema, temperature=0.0),
                        timeout=self.LLM_TIMEOUT,
                    )
                except TimeoutError:
                    final_status = "failed_system"
                    error_summary = "LLM 超时 30s"
                    return AgentResult.error_result("AI 响应超时，请稍后重试")
                except (LLMServiceError, LLMParseError) as e:
                    # v2 加固（review I-3）：LLMParseError（格式异常）也统一返 error_result
                    # 符合"LLM 异常都翻译成用户友好错误"原则
                    final_status = "failed_system"
                    error_summary = f"LLM 服务错: {e}"
                    return AgentResult.error_result("AI 服务暂不可用")

                total_prompt_tokens += llm_resp.usage_prompt_tokens
                total_completion_tokens += llm_resp.usage_completion_tokens

                # 把 LLM 的 assistant message 加入 history
                if llm_resp.raw_message:
                    history.append(llm_resp.raw_message)

                # ❹ 处理 tool_calls
                if llm_resp.is_tool_call:
                    for call in llm_resp.tool_calls:
                        try:
                            result = await self.registry.call(
                                call.name, call.args,
                                hub_user_id=hub_user_id, acting_as=acting_as,
                                conversation_id=conversation_id, round_idx=round_idx,
                            )
                            tool_result_content = json.dumps(result, ensure_ascii=False, default=str)
                            history.append({
                                "role": "tool",
                                "tool_call_id": call.id,
                                "name": call.name,
                                "content": tool_result_content,
                                "round_idx": round_idx,
                            })
                            # v8 staging review #4：tool 消息**不**持久化到 SessionMemory。
                            # 原因：assistant.tool_calls 消息没法持久化（SessionMemory.append
                            # signature 不支持 tool_calls 字段）。只持久化 tool 结果会让下一轮
                            # 加载历史时出现 "tool 消息孤儿"——DeepSeek 协议直接拒（400）。
                            # 跨轮上下文只保 user + assistant_final 文本，tool 调用链局限在
                            # 单轮 in-memory history 内即可。下一轮 LLM 看不到上轮 tool 细节，
                            # 但能看到 assistant 的总结，已足够支撑大多数对话。
                        except MissingConfirmationError as e:
                            await self.confirm_gate.add_pending(
                                conversation_id, hub_user_id, call.name, call.args,
                            )
                            history.append({
                                "role": "tool",
                                "tool_call_id": call.id,
                                "name": call.name,
                                "content": json.dumps({
                                    "error": str(e),
                                    "next_action": "preview_and_wait_for_user_confirm",
                                }, ensure_ascii=False),
                                "round_idx": round_idx,
                            })
                        except ClaimFailedError as e:
                            history.append({
                                "role": "tool",
                                "tool_call_id": call.id,
                                "name": call.name,
                                "content": json.dumps({
                                    "error": str(e),
                                    "next_action": "re_preview_and_request_user_reconfirm",
                                    "hint": "不要复用旧 token；旧 token 已失效或被篡改。",
                                }, ensure_ascii=False),
                                "round_idx": round_idx,
                            })
                        except BizError:
                            # v2 加固（review I-4）：plan §1859 明确"权限不足 BizError 上抛由 handler 翻译"
                            # 让上层 inbound handler 决定如何向用户展示（避免 LLM 复述 permission code）
                            final_status = "failed_user"
                            error_summary = "权限拒绝"
                            raise
                        except Exception as e:
                            # tool 其他内部抛错（ERP 5xx / 网络错等）→ 注入错误让 LLM 决策
                            logger.exception(
                                "tool %s 调用抛错 conv=%s round=%s", call.name,
                                conversation_id, round_idx,
                            )
                            history.append({
                                "role": "tool",
                                "tool_call_id": call.id,
                                "name": call.name,
                                "content": json.dumps({
                                    "error": str(e)[:500],
                                }, ensure_ascii=False),
                                "round_idx": round_idx,
                            })
                    continue  # 进下一 round 让 LLM 看到 tool 结果再决策

                # ❺ 终态（text / clarification）
                final_text = llm_resp.text or "（无回复）"
                try:
                    await self.session_memory.append(
                        conversation_id, role="assistant", content=final_text,
                    )
                except Exception:
                    logger.exception("session.append assistant 失败")

                # v8 review #13 (B 方案 state reducer)：
                # 从本轮 in-memory history 抽 entity state 写入 Redis，
                # 下一轮 ContextBuilder 加进 must_keep 让 LLM 看到上下文。
                try:
                    state = self._extract_round_state(history, user_message)
                    if state:
                        await self.session_memory.set_round_state(conversation_id, state)
                except Exception:
                    logger.exception("session.set_round_state 失败 conv=%s", conversation_id)

                if llm_resp.is_clarification:
                    return AgentResult.clarification(final_text)
                return AgentResult.text_result(final_text)

            # 5 round 后还在调 tool
            final_status = "failed_system"
            error_summary = f"超 {self.MAX_ROUNDS} round 仍未收敛"
            raise AgentMaxRoundsError(error_summary)

        finally:
            rounds = round_idx + 1 if round_idx >= 0 else 0
            await self._close_conversation_log(
                conv_log, final_status, error=error_summary,
                rounds_count=min(rounds, self.MAX_ROUNDS),
                tokens_used=total_prompt_tokens + total_completion_tokens,
            )

    @staticmethod
    def _build_confirm_hint(confirmed_actions: list[dict]) -> str:
        lines = [
            f"用户已确认 {len(confirmed_actions)} 个写操作。请按下表重新调用对应 tool，"
            "**每次调用必须同时传 confirmation_action_id 和 confirmation_token 两个字段**："
        ]
        for a in confirmed_actions:
            args_summary = json.dumps(a["args"], ensure_ascii=False)[:200]
            lines.append(
                f"  • {a['tool_name']}: args={args_summary} "
                f"→ confirmation_action_id=\"{a['action_id']}\" "
                f"confirmation_token=\"{a['token']}\""
            )
        lines.append(
            "注意：每对 (action_id, token) 只能用 1 次。失败时（tool 抛错）token 会被 HUB 还原，可重试同对；"
            "成功后 HUB 会原子消费，不可再用。"
        )
        return "\n".join(lines)

    @staticmethod
    def _extract_round_state(history: list[dict], user_message: str | None) -> dict:
        """v8 staging review #13：从单轮 in-memory history 抽 entity state 摘要。

        策略（不调 LLM，纯规则提取）：
          - 扫 history 里的 tool 消息：
            * search_customers result → customer.id/name/phone/...
            * search_products result → products[].id/name/spec/color
            * get_customer_history result → 历史成交价（last_price/last_qty）
            * check_inventory result → 库存
          - 扫最近的 assistant.tool_calls 看 generate_contract_draft / create_voucher_draft 的
            args（含 customer_id / items[] 含 qty/price）→ 这是用户已确认的最终意图
          - 输出紧凑 JSON 给下一轮 LLM 看

        注：取最后一次出现作为"最新"（用户改了主意时新值覆盖老值）。
        """
        state: dict = {}
        recent_user_msgs: list[str] = []
        if user_message:
            recent_user_msgs.append(user_message)

        # 收集 customers / products
        customers_seen: dict[int, dict] = {}  # id -> data
        products_seen: dict[int, dict] = {}
        # history_quotes 暂未实现（要回查 assistant.tool_calls 拿 customer_id+product_id），follow-up
        last_pending_args: dict | None = None  # 最后一次 generate / create 写 tool 的参数

        for msg in history:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "tool":
                tool_name = msg.get("name") or msg.get("tool_name", "")
                content = msg.get("content", "")
                try:
                    parsed = json.loads(content) if isinstance(content, str) else content
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(parsed, dict):
                    continue
                items = parsed.get("items") or []
                if tool_name == "search_customers":
                    for it in items[:5]:  # 最多 5 个
                        if isinstance(it, dict) and it.get("id"):
                            customers_seen[it["id"]] = {
                                "id": it["id"],
                                "name": it.get("name") or "",
                                "phone": it.get("phone") or "",
                            }
                elif tool_name == "search_products":
                    for it in items[:8]:
                        if isinstance(it, dict) and it.get("id"):
                            products_seen[it["id"]] = {
                                "id": it["id"],
                                "name": it.get("name") or "",
                                "spec": it.get("spec") or "",
                                "color": it.get("color") or "",
                                "sku": it.get("sku") or "",
                            }
                elif tool_name == "get_customer_history":
                    # 找 args（在前一个 assistant.tool_calls 里）
                    pass  # 简化：暂不抽 history quote
            elif role == "assistant":
                # 看 tool_calls 里有没有 generate_contract_draft / create_voucher_draft
                for tc in msg.get("tool_calls") or []:
                    fn = (tc.get("function") or {})
                    name = fn.get("name", "")
                    if name in ("generate_contract_draft", "create_voucher_draft", "generate_price_quote"):
                        try:
                            args_raw = fn.get("arguments", "{}")
                            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                            last_pending_args = {"tool": name, "args": args}
                        except (json.JSONDecodeError, TypeError):
                            pass

        # 装填 state
        if customers_seen:
            # 取最近一个（dict 保插入序）
            state["customers_seen"] = list(customers_seen.values())
        if products_seen:
            state["products_seen"] = list(products_seen.values())
        if last_pending_args:
            state["last_intent"] = last_pending_args

        return state

    @staticmethod
    async def _open_conversation_log(*, conversation_id: str, hub_user_id: int,
                                     channel_userid: str, started_at: datetime):
        """打开/获取 ConversationLog。

        语义（v2 文档化 review M-8）：conversation_id 是 UNIQUE，**一个 conversation 一条 log**。
        多次调 run() 同一 conv_id 时第二次取已有记录、final_status / tokens_used / rounds_count
        会被 finally 块覆盖（累计在内存 history 不同步落库）。
        如果产品需要"每 turn 一条 log"语义（区分多次对话回合），需要扩展 model 加 turn_idx
        或改 conversation_id 加上 turn 后缀。Plan 6 当前为简化设计，单条聚合表达。
        """
        # 同 conv_id 多次 run（第 2 turn）会撞 UNIQUE，是预期路径——直接 get 已存在的，
        # 而不是先 create 再 catch（避免 ERROR 日志噪声）
        existing = await ConversationLog.filter(conversation_id=conversation_id).first()
        if existing is not None:
            return existing
        try:
            return await ConversationLog.create(
                conversation_id=conversation_id,
                hub_user_id=hub_user_id,
                channel_userid=channel_userid,
                started_at=started_at,
            )
        except Exception:
            # race（极小概率：两次 run 并发 create）或真异常都走这里——
            # 先尝试取已存在记录；取不到再放弃（不阻塞业务，但记 exception 留痕）
            try:
                race = await ConversationLog.filter(conversation_id=conversation_id).first()
                if race is not None:
                    return race
            except Exception:
                pass
            logger.exception("ConversationLog.create 失败 conv=%s", conversation_id)
            return None

    @staticmethod
    async def _close_conversation_log(log, final_status: str, *,
                                      error: str | None,
                                      rounds_count: int,
                                      tokens_used: int) -> None:
        if log is None:
            return
        try:
            log.ended_at = datetime.now(UTC)
            log.final_status = final_status
            log.error_summary = error
            log.rounds_count = rounds_count
            log.tokens_used = tokens_used
            await log.save()
        except Exception:
            logger.exception("ConversationLog.save 失败")
