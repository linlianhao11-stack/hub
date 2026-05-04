import pytest
from unittest.mock import AsyncMock
from hub.agent.react.agent import ReActAgent


def test_agent_thread_id_uses_react_namespace():
    """thread_id 必须 = f'react:{conv}:{user}' (避开旧 GraphAgent checkpoint)。"""
    agent = ReActAgent(
        chat_model=AsyncMock(),
        tools=[],
        checkpointer=None,
    )
    config = agent._build_config(conversation_id="cv-1", hub_user_id=42)
    assert config["configurable"]["thread_id"] == "react:cv-1:42"


@pytest.mark.asyncio
async def test_agent_run_returns_friendly_msg_on_recursion_limit():
    """recursion_limit 触发（GraphRecursionError）→ 返友好文本,不抛。"""
    from langgraph.errors import GraphRecursionError
    from hub.agent.react.context import tool_ctx

    fake_compiled = AsyncMock()
    fake_compiled.ainvoke = AsyncMock(side_effect=GraphRecursionError("limit hit"))

    agent = ReActAgent(chat_model=AsyncMock(), tools=[], checkpointer=None)
    agent.compiled_graph = fake_compiled

    reply = await agent.run(
        user_message="x", hub_user_id=1, conversation_id="cv-r",
        acting_as=None, channel_userid="ding-r",
    )
    assert reply is not None
    assert "超限" in reply or "限" in reply
    # ContextVar 仍然 reset
    assert tool_ctx.get() is None


@pytest.mark.asyncio
async def test_agent_run_sets_tool_ctx_and_invokes():
    """run() 应该 set ContextVar + 调 compiled_graph.ainvoke + 提取 last assistant message。"""
    from langchain_core.messages import HumanMessage, AIMessage
    from hub.agent.react.context import tool_ctx

    fake_compiled = AsyncMock()
    fake_compiled.ainvoke = AsyncMock(return_value={
        "messages": [HumanMessage(content="hi"), AIMessage(content="在的~")],
    })

    agent = ReActAgent(
        chat_model=AsyncMock(),
        tools=[],
        checkpointer=None,
    )
    agent.compiled_graph = fake_compiled  # 注入 mock

    # 调用前 ContextVar 是空
    assert tool_ctx.get() is None

    reply = await agent.run(
        user_message="hi",
        hub_user_id=1,
        conversation_id="cv-1",
        acting_as=None,
        channel_userid="ding-1",
    )
    assert reply == "在的~"
    fake_compiled.ainvoke.assert_awaited_once()
    # 调用后 ContextVar 必须 reset 回 None（不污染下一个测试）
    assert tool_ctx.get() is None
