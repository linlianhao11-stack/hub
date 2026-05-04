"""ReActAgent — HUB v12 主 agent 类。

封装 langgraph.prebuilt.create_react_agent,对外保持 .run() 接口跟 GraphAgent 兼容,
让现有 DingTalk inbound handler / GraphAgentAdapter 不动。

v11 接通 memory:
  - 入口 invoke 前异步加载 user/customer/product memory,渲染进 SystemMessage
  - run() 结尾 fire-and-forget 触发 MemoryWriter

v12 改造:
  - MemoryWriter 输入维度从 ToolCallLog 切到 LangGraph state messages(对话原文)
  - 新增 ConversationLog 写入(plan 6 task 14 漏的环):每次 run 启动时 create,
    结束时 update tokens_used / rounds / final_status,让 admin dashboard LLM 成本
    指标真实可见
  - [MEM-STAT] 观测日志:input_chars / duration_ms / new_facts / dedup_skipped
    供 1 周后用真实数据评估是否需要 watermark / summarize
"""
from __future__ import annotations
import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError

from hub.agent.memory.loader import MemoryLoader
from hub.agent.memory.writer import MemoryWriter
from hub.agent.prompt.builder import render_memory_section
from hub.agent.react.context import tool_ctx, ToolContext
from hub.agent.react.prompts import SYSTEM_PROMPT
from hub.agent.react.run_logger import (
    RunStats, estimate_rounds, log_conversation, sum_tokens_used,
)


logger = logging.getLogger(__name__)


class ReActAgent:
    """ReAct agent 主类。"""

    def __init__(
        self,
        *,
        chat_model: BaseChatModel,
        tools: list[BaseTool],
        checkpointer: BaseCheckpointSaver | None,
        recursion_limit: int = 15,
        memory_loader: MemoryLoader | None = None,
        memory_writer: MemoryWriter | None = None,
        ai_provider: Any = None,
    ):
        self.chat_model = chat_model
        self.tools = tools
        self.checkpointer = checkpointer
        self.recursion_limit = recursion_limit
        self._memory_loader = memory_loader
        self._memory_writer = memory_writer
        self._ai_provider = ai_provider

        prompt_param: Any = (
            self._dynamic_prompt if memory_loader is not None else SYSTEM_PROMPT
        )
        self.compiled_graph = create_react_agent(
            model=chat_model, tools=tools, prompt=prompt_param,
            checkpointer=checkpointer,
        )

    async def _dynamic_prompt(self, state: dict) -> list:
        """LangGraph prompt callable: 每轮 invoke 前从 memory 渲染 SystemMessage。"""
        base = SystemMessage(content=SYSTEM_PROMPT)
        messages = state.get("messages", [])
        if self._memory_loader is None:
            return [base] + messages

        ctx = tool_ctx.get()
        if ctx is None:
            return [base] + messages

        try:
            memory = await self._memory_loader.load(
                hub_user_id=ctx["hub_user_id"],
                conversation_id=ctx["conversation_id"],
            )
        except Exception:
            logger.exception(
                "MemoryLoader.load 失败，回落静态 SYSTEM_PROMPT conv=%s",
                ctx.get("conversation_id"),
            )
            return [base] + messages

        mem_section = render_memory_section(memory)
        if not mem_section:
            return [base] + messages
        return [SystemMessage(content=SYSTEM_PROMPT + "\n\n" + mem_section)] + messages

    def _build_config(self, *, conversation_id: str, hub_user_id: int) -> dict:
        return {
            "configurable": {
                "thread_id": f"react:{conversation_id}:{hub_user_id}",
            },
            "recursion_limit": self.recursion_limit,
        }

    async def run(
        self,
        *,
        user_message: str,
        hub_user_id: int,
        conversation_id: str,
        acting_as: int | None = None,
        channel_userid: str = "",
    ) -> str | None:
        """跑一轮对话,返 LLM 最终自然语言回复。"""
        config = self._build_config(
            conversation_id=conversation_id, hub_user_id=hub_user_id,
        )
        ctx: ToolContext = {
            "hub_user_id": hub_user_id,
            "acting_as": acting_as,
            "conversation_id": conversation_id,
            "channel_userid": channel_userid,
        }
        token = tool_ctx.set(ctx)
        stats = RunStats(
            conversation_id=conversation_id,
            hub_user_id=hub_user_id,
            channel_userid=channel_userid,
            started_at=datetime.now(UTC),
        )
        try:
            result = await self.compiled_graph.ainvoke(
                {"messages": [HumanMessage(content=user_message)]},
                config=config,
            )
            messages = result.get("messages", [])
            reply: str | None = None
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    reply = msg.content
                    break

            stats.rounds_count = estimate_rounds(messages)
            stats.tokens_used = sum_tokens_used(messages)
            stats.final_status = "success"

            self._fire_memory_extraction(
                conversation_id=conversation_id,
                hub_user_id=hub_user_id,
                messages=messages,
                rounds_count=stats.rounds_count,
            )
            return reply
        except GraphRecursionError:
            logger.warning(
                "ReActAgent recursion_limit 触发 conv=%s user=%s msg=%r",
                conversation_id, hub_user_id, user_message[:200],
            )
            stats.final_status = "fallback_to_rule"
            stats.error_summary = "recursion_limit"
            return "推理步骤超限,请简化请求或联系管理员。"
        except Exception as e:
            logger.exception(
                "ReActAgent 抛异常 conv=%s user=%s msg=%r",
                conversation_id, hub_user_id, user_message[:200],
            )
            stats.final_status = "failed_system_final"
            stats.error_summary = str(e)[:500]
            raise
        finally:
            tool_ctx.reset(token)
            stats.ended_at = datetime.now(UTC)
            self._fire_log_conversation(stats)

    def _fire_memory_extraction(
        self, *, conversation_id: str, hub_user_id: int,
        messages: list, rounds_count: int,
    ) -> None:
        """fire-and-forget 触发 MemoryWriter；fail-soft，不阻塞业务。"""
        if self._memory_writer is None or self._ai_provider is None:
            return
        try:
            asyncio.create_task(
                self._extract_memory_async(
                    conversation_id=conversation_id,
                    hub_user_id=hub_user_id,
                    messages=messages,
                    rounds_count=rounds_count,
                )
            )
        except RuntimeError:
            logger.warning("MemoryWriter create_task 失败（无 running loop），跳过抽取")

    async def _extract_memory_async(
        self, *, conversation_id: str, hub_user_id: int,
        messages: list, rounds_count: int,
    ) -> None:
        """直接传 messages 给 writer(不再查 ToolCallLog),并打 [MEM-STAT] 观测日志。"""
        try:
            stats = await self._memory_writer.extract_and_write(
                conversation_id=conversation_id,
                hub_user_id=hub_user_id,
                messages=messages,
                rounds_count=rounds_count,
                ai_provider=self._ai_provider,
            )
            logger.info(
                "[MEM-STAT] cid=%s rounds=%d input_chars=%d duration_ms=%d "
                "new_user=%d new_customer=%d new_product=%d "
                "dedup_skipped=%d skip_reason=%s",
                conversation_id[-12:], rounds_count,
                stats.get("input_chars", 0), stats.get("duration_ms", 0),
                stats.get("new_user_facts", 0),
                stats.get("new_customer_facts", 0),
                stats.get("new_product_facts", 0),
                stats.get("dedup_skipped", 0),
                stats.get("skip_reason", "ok"),
            )
        except Exception:
            logger.exception(
                "MemoryWriter 异步抽取整体失败 conv=%s（不影响业务）",
                conversation_id,
            )

    def _fire_log_conversation(self, stats: RunStats) -> None:
        """fire-and-forget 写 ConversationLog;失败仅 log,不阻塞业务。"""
        try:
            asyncio.create_task(log_conversation(stats))
        except RuntimeError:
            logger.warning(
                "ConversationLog create_task 失败（无 running loop），跳过写入",
            )
