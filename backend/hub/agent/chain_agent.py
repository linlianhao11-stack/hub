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

    MAX_ROUNDS = 5
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
                            try:
                                await self.session_memory.append(
                                    conversation_id, role="tool",
                                    content=tool_result_content,
                                    tool_call_id=call.id,
                                )
                            except Exception:
                                logger.exception("session.append tool 失败")
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
