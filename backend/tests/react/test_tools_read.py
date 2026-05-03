import pytest
from unittest.mock import AsyncMock, patch
from hub.agent.react.tools.read import search_customer, search_product


@pytest.mark.asyncio
async def test_search_customer_calls_erp_tools_via_invoke(fake_ctx):
    """search_customer 必须通过 invoke_business_tool 调 erp_tools.search_customers
    （不是直接 adapter）— 拿到权限校验 + 审计 log 自动两件套。"""
    fake_search = AsyncMock(return_value={
        "items": [{"id": 7, "name": "翼蓝", "phone": "138..."}], "total": 1,
    })
    with (
        patch("hub.agent.react.tools.read.erp_tools.search_customers", new=fake_search),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()) as perm,
    ):
        result = await search_customer.ainvoke({"query": "翼蓝"})
    assert result == [{"id": 7, "name": "翼蓝", "phone": "138..."}]
    # 权限被校验
    perm.assert_awaited_once_with(1, ["usecase.query_customer.use"])
    # 底层函数收到正确 kwargs
    fake_search.assert_awaited_once_with(query="翼蓝", acting_as_user_id=1)


@pytest.mark.asyncio
async def test_search_product_unwraps_items(fake_ctx):
    """ERP 返 {items, total} dict → tool 解包成 list 给 LLM。"""
    fake_search = AsyncMock(return_value={
        "items": [{"id": 1, "name": "X1", "sku": "MAT01"}], "total": 1,
    })
    with (
        patch("hub.agent.react.tools.read.erp_tools.search_products", new=fake_search),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await search_product.ainvoke({"query": "X1"})
    assert isinstance(result, list)
    assert result[0]["id"] == 1
