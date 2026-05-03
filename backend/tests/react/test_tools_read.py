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


@pytest.mark.asyncio
async def test_get_product_detail(fake_ctx):
    from hub.agent.react.tools.read import get_product_detail
    fake_fn = AsyncMock(return_value={
        "id": 1, "name": "X1", "total_stock": 100, "stocks": [{"warehouse": "总仓", "qty": 100}],
    })
    with (
        patch("hub.agent.react.tools.read.erp_tools.get_product_detail", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await get_product_detail.ainvoke({"product_id": 1})
    assert result["stocks"][0]["qty"] == 100
    fake_fn.assert_awaited_once_with(product_id=1, acting_as_user_id=1)


@pytest.mark.asyncio
async def test_check_inventory_single_product(fake_ctx):
    """check_inventory 是单产品库存（不是按 brand 列表）。"""
    from hub.agent.react.tools.read import check_inventory
    fake_fn = AsyncMock(return_value={
        "product_id": 1, "total_stock": 100, "stocks": [{"warehouse": "总仓", "qty": 100}],
    })
    with (
        patch("hub.agent.react.tools.read.erp_tools.check_inventory", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await check_inventory.ainvoke({"product_id": 1})
    assert result["total_stock"] == 100
    fake_fn.assert_awaited_once_with(product_id=1, acting_as_user_id=1)


@pytest.mark.asyncio
async def test_get_customer_history(fake_ctx):
    """get_customer_history 参数顺序 product_id 在前。"""
    from hub.agent.react.tools.read import get_customer_history
    fake_fn = AsyncMock(return_value={
        "items": [{"order_id": 100, "qty": 10, "price": 300, "date": "2026-04-01"}],
    })
    with (
        patch("hub.agent.react.tools.read.erp_tools.get_customer_history", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await get_customer_history.ainvoke(
            {"product_id": 1, "customer_id": 7, "limit": 5},
        )
    fake_fn.assert_awaited_once_with(
        product_id=1, customer_id=7, limit=5, acting_as_user_id=1,
    )
    assert result["items"][0]["price"] == 300
