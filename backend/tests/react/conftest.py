"""React 测试共享 fixture。**必须在 Task 2.0 创建** —— Task 2.0 / 2.1 / 2.2 / ...
所有 react 单测都用 fake_ctx,如果只在 Task 2.1 创建,Task 2.0 的 test_invoke.py
跑时会 ERROR fixture 'fake_ctx' not found（不是 FAIL）,后续 task 链式 broken。
"""
import pytest
from hub.agent.react.context import tool_ctx, ToolContext


@pytest.fixture
def fake_ctx():
    """提供一个 set 好的 ToolContext，测试结束自动 reset。

    fake hub_user_id=1, conversation_id="test-conv", acting_as=None。
    测试断言权限调用 / 审计 log / fn kwargs 时按这个值算。
    """
    token = tool_ctx.set(ToolContext(
        hub_user_id=1,
        acting_as=None,
        conversation_id="test-conv",
        channel_userid="ding-test",
    ))
    yield {"hub_user_id": 1, "conversation_id": "test-conv"}
    tool_ctx.reset(token)
