"""ReActAgent.run 的会话级观测落库测试 (v12 新增)。

覆盖:
- ConversationLog 通过 update_or_create 写入,字段映射正确
- estimate_rounds / sum_tokens_used 从 LangGraph messages 算对
- log_conversation 异常 fail-soft 不抛
- ReActAgent.run 三种结束状态(success / fallback_to_rule / failed_system_final)
  都触发 ConversationLog 写入
"""
from __future__ import annotations

import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from hub.agent.react.agent import ReActAgent
from hub.agent.react.run_logger import (
    RunStats, estimate_rounds, log_conversation, sum_tokens_used,
)


def test_estimate_rounds_counts_ai_messages_only():
    msgs = [
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
        ToolMessage(content="x", name="t", tool_call_id="1"),
        AIMessage(content="ok"),
    ]
    assert estimate_rounds(msgs) == 2


def test_sum_tokens_used_aggregates_usage_metadata():
    """LangChain AIMessage.usage_metadata 是 dict {input/output/total_tokens}。"""
    a1 = AIMessage(content="x")
    a1.usage_metadata = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
    a2 = AIMessage(content="y")
    a2.usage_metadata = {"input_tokens": 200, "output_tokens": 80, "total_tokens": 280}
    a3 = AIMessage(content="z")  # 无 usage_metadata,跳过
    assert sum_tokens_used([a1, a2, a3]) == 430


def test_sum_tokens_used_handles_object_attribute_form():
    """有些 LLM 返回 usage_metadata 是对象不是 dict。"""
    msg = AIMessage(content="x")
    usage_obj = MagicMock()
    usage_obj.total_tokens = 99
    msg.usage_metadata = usage_obj
    # MagicMock 不是 dict,走 getattr 分支
    assert sum_tokens_used([msg]) == 99


def test_sum_tokens_used_returns_zero_when_no_metadata():
    msgs = [AIMessage(content="x"), HumanMessage(content="y")]
    assert sum_tokens_used(msgs) == 0


@pytest.mark.asyncio
async def test_log_conversation_calls_update_or_create():
    stats = RunStats(
        conversation_id="cv-1",
        hub_user_id=42,
        channel_userid="ding-1",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        rounds_count=3,
        tokens_used=500,
        final_status="success",
    )
    fake_model = MagicMock()
    fake_model.update_or_create = AsyncMock()

    with patch("hub.models.conversation.ConversationLog", fake_model):
        await log_conversation(stats)

    fake_model.update_or_create.assert_awaited_once()
    kwargs = fake_model.update_or_create.await_args.kwargs
    assert kwargs["conversation_id"] == "cv-1"
    assert kwargs["hub_user_id"] == 42
    defaults = kwargs["defaults"]
    assert defaults["rounds_count"] == 3
    assert defaults["tokens_used"] == 500
    assert defaults["final_status"] == "success"
    assert defaults["channel_userid"] == "ding-1"


@pytest.mark.asyncio
async def test_log_conversation_swallows_db_errors():
    """DB 异常不应抛(不阻塞业务)。"""
    stats = RunStats(
        conversation_id="cv-x", hub_user_id=1, channel_userid="d",
        started_at=datetime.now(UTC),
    )
    fake_model = MagicMock()
    fake_model.update_or_create = AsyncMock(side_effect=RuntimeError("DB down"))

    with patch("hub.models.conversation.ConversationLog", fake_model):
        # 不应抛
        await log_conversation(stats)


@pytest.mark.asyncio
async def test_react_run_writes_conversation_log_on_success():
    """run() 成功结束 → log_conversation 被异步调,final_status=success。"""
    import asyncio

    fake_compiled = AsyncMock()
    a1 = AIMessage(content="ok")
    a1.usage_metadata = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
    fake_compiled.ainvoke = AsyncMock(return_value={
        "messages": [HumanMessage(content="hi"), a1],
    })

    agent = ReActAgent(chat_model=AsyncMock(), tools=[], checkpointer=None)
    agent.compiled_graph = fake_compiled

    seen_stats: list[RunStats] = []

    async def _capture(stats):
        seen_stats.append(stats)

    with patch("hub.agent.react.agent.log_conversation", _capture):
        reply = await agent.run(
            user_message="hi", hub_user_id=42,
            conversation_id="cv-success", acting_as=None,
            channel_userid="ding-success",
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert reply == "ok"
    assert len(seen_stats) == 1
    s = seen_stats[0]
    assert s.conversation_id == "cv-success"
    assert s.hub_user_id == 42
    assert s.channel_userid == "ding-success"
    assert s.final_status == "success"
    assert s.rounds_count == 1
    assert s.tokens_used == 150
    assert s.error_summary is None


@pytest.mark.asyncio
async def test_react_run_writes_conversation_log_on_recursion_limit():
    """run() recursion 超限 → log_conversation 仍被调,final_status=fallback_to_rule。"""
    import asyncio

    fake_compiled = AsyncMock()
    fake_compiled.ainvoke = AsyncMock(side_effect=GraphRecursionError("limit"))

    agent = ReActAgent(chat_model=AsyncMock(), tools=[], checkpointer=None)
    agent.compiled_graph = fake_compiled
    seen: list[RunStats] = []

    async def _cap(s):
        seen.append(s)

    with patch("hub.agent.react.agent.log_conversation", _cap):
        await agent.run(
            user_message="x", hub_user_id=1, conversation_id="cv-rec",
            acting_as=None, channel_userid="ding-r",
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert len(seen) == 1
    assert seen[0].final_status == "fallback_to_rule"
    assert seen[0].error_summary == "recursion_limit"


@pytest.mark.asyncio
async def test_react_run_writes_conversation_log_on_unhandled_exception():
    """run() 抛非 recursion 异常 → log_conversation 仍触发,final_status=failed_system_final。"""
    import asyncio

    fake_compiled = AsyncMock()
    fake_compiled.ainvoke = AsyncMock(side_effect=ValueError("boom"))

    agent = ReActAgent(chat_model=AsyncMock(), tools=[], checkpointer=None)
    agent.compiled_graph = fake_compiled
    seen: list[RunStats] = []

    async def _cap(s):
        seen.append(s)

    with patch("hub.agent.react.agent.log_conversation", _cap):
        with pytest.raises(ValueError):
            await agent.run(
                user_message="x", hub_user_id=1, conversation_id="cv-fail",
                acting_as=None, channel_userid="ding-f",
            )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert len(seen) == 1
    assert seen[0].final_status == "failed_system_final"
    assert "boom" in (seen[0].error_summary or "")
