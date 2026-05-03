import pytest
from hub.agent.react.context import tool_ctx, ToolContext


def test_tool_ctx_default_is_none():
    """未 set 时 .get() 返默认 None — tool 函数据此判断 'react agent 入口必须先 set'。"""
    ctx = tool_ctx.get()
    assert ctx is None


def test_tool_ctx_set_and_get():
    token = tool_ctx.set(ToolContext(
        hub_user_id=42,
        acting_as=None,
        conversation_id="cv-1",
        channel_userid="ding-u-1",
    ))
    try:
        ctx = tool_ctx.get()
        assert ctx["hub_user_id"] == 42
        assert ctx["conversation_id"] == "cv-1"
        assert ctx["channel_userid"] == "ding-u-1"
        assert ctx["acting_as"] is None
    finally:
        tool_ctx.reset(token)
