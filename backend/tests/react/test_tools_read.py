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


@pytest.mark.asyncio
async def test_get_customer_balance(fake_ctx):
    from hub.agent.react.tools.read import get_customer_balance
    fake_fn = AsyncMock(return_value={"balance": 1000.50, "credit_limit": 50000})
    with (
        patch("hub.agent.react.tools.read.erp_tools.get_customer_balance", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await get_customer_balance.ainvoke({"customer_id": 7})
    assert result["balance"] == 1000.50


@pytest.mark.asyncio
async def test_search_orders(fake_ctx):
    from hub.agent.react.tools.read import search_orders
    fake_fn = AsyncMock(return_value={"items": [{"id": 100}], "total": 1})
    with (
        patch("hub.agent.react.tools.read.erp_tools.search_orders", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await search_orders.ainvoke({"customer_id": 7, "since_days": 30})
    fake_fn.assert_awaited_once_with(customer_id=7, since_days=30, acting_as_user_id=1)


@pytest.mark.asyncio
async def test_get_order_detail(fake_ctx):
    from hub.agent.react.tools.read import get_order_detail
    fake_fn = AsyncMock(return_value={"id": 100, "customer_id": 7, "items": []})
    with (
        patch("hub.agent.react.tools.read.erp_tools.get_order_detail", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await get_order_detail.ainvoke({"order_id": 100})
    assert result["id"] == 100


@pytest.mark.asyncio
async def test_analyze_top_customers(fake_ctx):
    from hub.agent.react.tools.read import analyze_top_customers
    fake_fn = AsyncMock(return_value={
        "items": [{"customer_id": 7, "total": 50000}],
        "data_window": "近一月,3 单",
    })
    with (
        patch("hub.agent.react.tools.read.analyze_tools.analyze_top_customers", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await analyze_top_customers.ainvoke({"period": "近一月", "top_n": 10})
    fake_fn.assert_awaited_once_with(period="近一月", top_n=10, acting_as_user_id=1)
    assert result["items"][0]["customer_id"] == 7


@pytest.mark.asyncio
async def test_get_recent_drafts(fake_ctx, monkeypatch):
    """contract 类型,调 _query_recent_contract_drafts helper 拿草稿,反向填到 result。"""
    from hub.agent.react.tools.read import get_recent_drafts
    from unittest.mock import AsyncMock

    fake_drafts = [{
        "id": 20,
        "customer_id": 7,
        "requester_hub_user_id": 1,
        "conversation_id": "test-conv",
        "items": [{"product_id": 1, "qty": 10, "price": 300}],
        "extras": {
            "shipping_address": "北京海淀", "shipping_contact": "张三",
            "shipping_phone": "138...",
            "payment_terms": "30 天", "tax_rate": "13%",
        },
        "status": "sent",
        "created_at": "2026-05-03T10:00:00",
    }]
    async def _fake_query(conv_id, hub_user_id, limit):
        # 防御性断言：实施者不能误删 helper 的 conversation_id / hub_user_id 过滤
        assert conv_id == "test-conv"
        assert hub_user_id == 1
        return fake_drafts
    monkeypatch.setattr(
        "hub.agent.react.tools.read._query_recent_contract_drafts", _fake_query,
    )
    monkeypatch.setattr(
        "hub.agent.react.tools.read._get_erp_customer_name",
        AsyncMock(return_value="翼蓝"),
    )
    monkeypatch.setattr(
        "hub.agent.react.tools.read.require_permissions", AsyncMock(),
    )
    from contextlib import asynccontextmanager

    class _FakeLogCtx:
        def set_result(self, _r): ...

    @asynccontextmanager
    async def _fake_log_tool_call(**kwargs):
        yield _FakeLogCtx()

    monkeypatch.setattr(
        "hub.agent.react.tools.read.log_tool_call", _fake_log_tool_call,
    )

    result = await get_recent_drafts.ainvoke({"limit": 5})

    assert len(result) == 1
    assert result[0]["draft_id"] == 20
    assert result[0]["customer_name"] == "翼蓝"
    assert result[0]["items"][0]["product_id"] == 1
    assert result[0]["shipping"]["address"] == "北京海淀"
    assert result[0]["payment_terms"] == "30 天"
