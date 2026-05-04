import pytest
from unittest.mock import AsyncMock, patch
from hub.agent.react.tools._invoke import invoke_business_tool


@pytest.mark.asyncio
async def test_invoke_business_tool_runs_perm_check_and_calls_fn(fake_ctx):
    """invoke_business_tool 必须：(a) require_permissions (b) 调 fn (c) 注入 ctx kwargs。"""
    fake_fn = AsyncMock(return_value={"items": [], "total": 0})

    with patch(
        "hub.agent.react.tools._invoke.require_permissions",
        new=AsyncMock(),
    ) as mock_perm:
        result = await invoke_business_tool(
            tool_name="search_customers",
            perm="usecase.query_customer.use",
            args={"query": "翼蓝"},
            fn=fake_fn,
        )
    assert result == {"items": [], "total": 0}
    mock_perm.assert_awaited_once_with(1, ["usecase.query_customer.use"])
    fake_fn.assert_awaited_once_with(query="翼蓝", acting_as_user_id=1)


@pytest.mark.asyncio
async def test_invoke_business_tool_perm_denied_returns_error_dict(fake_ctx):
    """权限校验 fail → require_permissions 抛 BizError → invoke **必须捕获转 error dict**。

    跟原始设计（透传 BizError）不同：LangGraph ToolNode 默认 handle_tool_errors=True
    会吃掉 raise 出 @tool 的异常,生成英文 ToolMessage "Please fix your mistakes",
    LLM 看到英文不知道是权限问题,胡乱回 / retry / 假装成功。
    所以 _invoke 必须主动捕获 BizError 转中文 error dict 让 LLM 自然语言告知用户。
    """
    from hub.error_codes import BizError, BizErrorCode

    fake_fn = AsyncMock()
    with patch(
        "hub.agent.react.tools._invoke.require_permissions",
        new=AsyncMock(side_effect=BizError(BizErrorCode.PERM_NO_PRODUCT_QUERY)),
    ):
        result = await invoke_business_tool(
            tool_name="search_customers", perm="usecase.x.use",
            args={"query": "x"}, fn=fake_fn,
        )

    # 关键断言：返 error dict 不抛
    assert isinstance(result, dict) and "error" in result, (
        f"BizError 必须转 error dict,实际 {result!r}"
    )
    assert "权限不足" in result["error"], (
        f"中文 error 必须含'权限不足',实际 {result['error']!r}"
    )
    fake_fn.assert_not_awaited()  # 权限挂掉不应该调 fn


@pytest.mark.asyncio
async def test_invoke_business_tool_writes_audit_log(fake_ctx):
    """log_tool_call context manager 必须包住 fn 调用,异常照常上抛。"""
    fake_fn = AsyncMock(return_value={"x": 1})
    with (
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
        patch("hub.agent.react.tools._invoke.log_tool_call") as mock_log,
    ):
        # 让 log_tool_call 返一个真 async context manager
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def fake_log(**kw):
            ctx = AsyncMock()
            ctx.set_result = lambda r: None
            yield ctx
        mock_log.side_effect = fake_log
        result = await invoke_business_tool(
            tool_name="search_customers", perm="usecase.x.use",
            args={"query": "x"}, fn=fake_fn,
        )
    assert result == {"x": 1}
    mock_log.assert_called_once()
    log_kwargs = mock_log.call_args.kwargs
    assert log_kwargs["tool_name"] == "search_customers"
    assert log_kwargs["hub_user_id"] == 1
    assert log_kwargs["conversation_id"] == "test-conv"
