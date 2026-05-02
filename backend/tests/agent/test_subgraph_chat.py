# backend/tests/agent/test_subgraph_chat.py
import pytest
from unittest.mock import AsyncMock
from hub.agent.graph.state import AgentState, Intent
from hub.agent.graph.subgraphs.chat import chat_subgraph


@pytest.mark.asyncio
async def test_chat_subgraph_no_tools_temperature_1_3():
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": "你好呀"})())
    state = AgentState(user_message="你好", hub_user_id=1, conversation_id="c1",
                        intent=Intent.CHAT)
    out = await chat_subgraph(state, llm=llm)
    kwargs = llm.chat.await_args.kwargs
    assert "tools" not in kwargs or kwargs["tools"] is None
    assert kwargs["temperature"] == 1.3
    assert kwargs["thinking"] == {"type": "disabled"}
    assert out.final_response == "你好呀"
