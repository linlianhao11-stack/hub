# backend/tests/agent/test_graph_router.py
import pytest
from unittest.mock import AsyncMock
from hub.agent.graph.state import AgentState, Intent
from hub.agent.graph.router import router_node


@pytest.mark.asyncio
async def test_router_lowercase_value_resolved():
    """模型续写 'contract' 应能正确解析（不是落 UNKNOWN）— 核心修复点。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": 'contract"'})())
    state = AgentState(user_message="给阿里做合同", hub_user_id=1, conversation_id="c1")
    out = await router_node(state, llm=llm)
    assert out.intent == Intent.CONTRACT


@pytest.mark.asyncio
async def test_router_unknown_value_falls_back_to_unknown():
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": 'foobar"'})())
    state = AgentState(user_message="???", hub_user_id=1, conversation_id="c1")
    out = await router_node(state, llm=llm)
    assert out.intent == Intent.UNKNOWN


@pytest.mark.asyncio
async def test_router_passes_thinking_disabled():
    """spec §1.5：router 必须显式 thinking={'type':'disabled'}。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": 'chat"'})())
    state = AgentState(user_message="hi", hub_user_id=1, conversation_id="c1")
    await router_node(state, llm=llm)
    kwargs = llm.chat.await_args.kwargs
    assert kwargs["thinking"] == {"type": "disabled"}
    assert kwargs["temperature"] == 0.0
    assert kwargs["prefix_assistant"] == '{"intent": "'
    assert kwargs["stop"] == ['",']
