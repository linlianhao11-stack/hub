"""Plan 6 Task 3：ERP 读 tool 测试（9 tool × 4 case = 36 + 4 adapter 新方法 unit = ~40 case）。

测试分两部分：
1. Erp4Adapter 4 个新方法的单元测试（URL / params / acting_as 验证，httpx MockTransport 拦请求）
2. 9 个 tool fn 的 4-case 矩阵（成功 / 权限拒绝通过 registry / 4xx 透传 / 5xx 透传）
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import MockTransport, Response

from hub.adapters.downstream.erp4 import (
    Erp4Adapter,
    ErpNotFoundError,
    ErpPermissionError,
    ErpSystemError,
)
from hub.agent.tools.confirm_gate import ConfirmGate
from hub.agent.tools.erp_tools import (
    check_inventory,
    current_erp_adapter,
    get_customer_balance,
    get_customer_history,
    get_inventory_aging,
    get_order_detail,
    get_product_detail,
    register_all,
    search_customers,
    search_orders,
    search_products,
    set_erp_adapter,
)
from hub.agent.tools.registry import ToolRegistry
from hub.error_codes import BizError

# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_adapter():
    """每条 test 拿到干净的 mock adapter；cleanup 时重置全局。"""
    mock = AsyncMock(spec=Erp4Adapter)
    set_erp_adapter(mock)
    yield mock
    set_erp_adapter(None)


@pytest.fixture
def erp_url():
    return "http://erp.test.local"


def _make_real_adapter(erp_url: str, handler) -> Erp4Adapter:
    """创建使用 MockTransport 的真实 adapter（用于 URL/header 断言）。"""
    return Erp4Adapter(
        base_url=erp_url, api_key="test-api-key",
        transport=MockTransport(handler),
    )


@pytest.fixture
async def redis_client():
    """真 redis（用于 ToolRegistry + ConfirmGate）。"""
    import redis.asyncio as redis_async
    client = redis_async.Redis.from_url("redis://localhost:6380/0", decode_responses=True)
    yield client
    async for key in client.scan_iter("hub:agent:*"):
        await client.delete(key)
    await client.aclose()


@pytest.fixture
async def registry(redis_client):
    """注册了全部 9 个 ERP 读 tool 的 ToolRegistry。"""
    session_memory = AsyncMock()
    session_memory.get_entity_refs = AsyncMock(return_value=None)
    session_memory.add_entity_refs = AsyncMock()
    gate = ConfirmGate(redis_client)
    reg = ToolRegistry(confirm_gate=gate, session_memory=session_memory)
    register_all(reg)
    return reg


# ============================================================
# Part 1: Erp4Adapter 4 个新方法 — URL / params / headers 验证
# ============================================================

@pytest.mark.asyncio
async def test_adapter_search_orders_url_and_params(erp_url):
    """search_orders 拼对 URL + params + Acting-As-User-Id header。"""
    captured: dict = {}

    def handler(req: httpx.Request) -> Response:
        captured["url_path"] = req.url.path
        captured["params"] = dict(req.url.params)
        captured["headers"] = dict(req.headers)
        return Response(200, json={"items": [], "total": 0})

    adapter = _make_real_adapter(erp_url, handler)
    since = datetime(2026, 3, 1, tzinfo=UTC)
    await adapter.search_orders(
        customer_id=42, since=since, status="confirmed",
        page=2, page_size=50, acting_as_user_id=99,
    )

    assert captured["url_path"] == "/api/v1/orders"
    assert captured["params"]["customer_id"] == "42"
    assert captured["params"]["page"] == "2"
    assert captured["params"]["page_size"] == "50"
    assert captured["params"]["status"] == "confirmed"
    assert "2026-03-01" in captured["params"]["since"]
    assert captured["headers"].get("x-acting-as-user-id") == "99"
    assert captured["headers"].get("x-api-key") == "test-api-key"


@pytest.mark.asyncio
async def test_adapter_get_order_detail_url(erp_url):
    """get_order_detail 路径含 order_id + header 正确。"""
    captured: dict = {}

    def handler(req: httpx.Request) -> Response:
        captured["path"] = req.url.path
        captured["acting_as"] = req.headers.get("x-acting-as-user-id")
        return Response(200, json={"id": 123, "status": "confirmed"})

    adapter = _make_real_adapter(erp_url, handler)
    result = await adapter.get_order_detail(order_id=123, acting_as_user_id=7)

    assert captured["path"] == "/api/v1/orders/123"
    assert captured["acting_as"] == "7"
    assert result["id"] == 123


@pytest.mark.asyncio
async def test_adapter_get_customer_balance_url(erp_url):
    """get_customer_balance 路径含 customer_id + header 正确。"""
    captured: dict = {}

    def handler(req: httpx.Request) -> Response:
        captured["path"] = req.url.path
        captured["acting_as"] = req.headers.get("x-acting-as-user-id")
        return Response(200, json={"receivable": 1000, "paid": 800, "outstanding": 200})

    adapter = _make_real_adapter(erp_url, handler)
    result = await adapter.get_customer_balance(customer_id=55, acting_as_user_id=3)

    assert captured["path"] == "/api/v1/finance/customer-statement/55"
    assert captured["acting_as"] == "3"
    assert result["outstanding"] == 200


@pytest.mark.asyncio
async def test_adapter_get_inventory_aging_url_and_params(erp_url):
    """get_inventory_aging 拼对 URL + threshold_days + product_id/warehouse_id 可选 params。"""
    captured: dict = {}

    def handler(req: httpx.Request) -> Response:
        captured["path"] = req.url.path
        captured["params"] = dict(req.url.params)
        captured["acting_as"] = req.headers.get("x-acting-as-user-id")
        return Response(200, json={"items": []})

    adapter = _make_real_adapter(erp_url, handler)
    await adapter.get_inventory_aging(
        threshold_days=120, product_id=10, warehouse_id=2, acting_as_user_id=5,
    )

    assert captured["path"] == "/api/v1/inventory/aging"
    assert captured["params"]["threshold_days"] == "120"
    assert captured["params"]["product_id"] == "10"
    assert captured["params"]["warehouse_id"] == "2"
    assert captured["acting_as"] == "5"


@pytest.mark.asyncio
async def test_adapter_search_orders_optional_params_omitted(erp_url):
    """search_orders 不传可选参数时，URL 里不含 customer_id / since / status。"""
    captured: dict = {}

    def handler(req: httpx.Request) -> Response:
        captured["params"] = dict(req.url.params)
        return Response(200, json={"items": []})

    adapter = _make_real_adapter(erp_url, handler)
    await adapter.search_orders(acting_as_user_id=1)

    assert "customer_id" not in captured["params"]
    assert "since" not in captured["params"]
    assert "status" not in captured["params"]
    assert captured["params"]["page"] == "1"
    assert captured["params"]["page_size"] == "200"


# ============================================================
# Part 2: 9 个 tool — 4-case 矩阵
# 工具函数: success / perm_denied_via_registry / erp_4xx / erp_5xx
# ============================================================

# ---------- 辅助：权限 patch helper ----------

def _perm_allow():
    return (
        patch("hub.agent.tools.registry.has_permission", AsyncMock(return_value=True)),
        patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)),
    )


def _perm_deny():
    return (
        patch("hub.agent.tools.registry.has_permission", AsyncMock(return_value=False)),
        patch("hub.agent.tools.registry.require_permissions",
              AsyncMock(side_effect=BizError("PERM_DOWNSTREAM_DENIED"))),
    )


# ---------- search_products ----------

@pytest.mark.asyncio
async def test_search_products_success(mock_adapter):
    mock_adapter.search_products.return_value = {"items": [{"id": 1, "name": "商品A"}]}
    result = await search_products(query="商品", acting_as_user_id=99)
    assert result["items"][0]["id"] == 1
    mock_adapter.search_products.assert_called_once_with(query="商品", acting_as_user_id=99)


@pytest.mark.asyncio
async def test_search_products_perm_denied(registry, mock_adapter):
    """权限不足 → BizError（通过 registry 走完整链路）。"""
    p1, p2 = _perm_deny()
    with p1, p2:
        with pytest.raises(BizError):
            await registry.call(
                "search_products", {"query": "x"},
                hub_user_id=1, acting_as=99,
                conversation_id="conv-1", round_idx=0,
            )


@pytest.mark.asyncio
async def test_search_products_erp_4xx(mock_adapter):
    mock_adapter.search_products.side_effect = ErpPermissionError("403")
    with pytest.raises(ErpPermissionError):
        await search_products(query="x", acting_as_user_id=99)


@pytest.mark.asyncio
async def test_search_products_erp_5xx(mock_adapter):
    mock_adapter.search_products.side_effect = ErpSystemError("503")
    with pytest.raises(ErpSystemError):
        await search_products(query="x", acting_as_user_id=99)


# ---------- search_customers ----------

@pytest.mark.asyncio
async def test_search_customers_success(mock_adapter):
    mock_adapter.search_customers.return_value = {"items": [{"id": 2, "name": "客户B"}]}
    result = await search_customers(query="客户", acting_as_user_id=99)
    assert result["items"][0]["id"] == 2
    mock_adapter.search_customers.assert_called_once_with(query="客户", acting_as_user_id=99)


@pytest.mark.asyncio
async def test_search_customers_perm_denied(registry, mock_adapter):
    p1, p2 = _perm_deny()
    with p1, p2:
        with pytest.raises(BizError):
            await registry.call(
                "search_customers", {"query": "y"},
                hub_user_id=1, acting_as=99,
                conversation_id="conv-2", round_idx=0,
            )


@pytest.mark.asyncio
async def test_search_customers_erp_4xx(mock_adapter):
    mock_adapter.search_customers.side_effect = ErpPermissionError("401")
    with pytest.raises(ErpPermissionError):
        await search_customers(query="y", acting_as_user_id=99)


@pytest.mark.asyncio
async def test_search_customers_erp_5xx(mock_adapter):
    mock_adapter.search_customers.side_effect = ErpSystemError("500")
    with pytest.raises(ErpSystemError):
        await search_customers(query="y", acting_as_user_id=99)


# ---------- get_product_detail ----------

@pytest.mark.asyncio
async def test_get_product_detail_success(mock_adapter):
    mock_adapter.get_product.return_value = {"id": 10, "name": "商品X", "total_stock": 100}
    result = await get_product_detail(product_id=10, acting_as_user_id=99)
    assert result["id"] == 10
    mock_adapter.get_product.assert_called_once_with(product_id=10, acting_as_user_id=99)


@pytest.mark.asyncio
async def test_get_product_detail_perm_denied(registry, mock_adapter):
    p1, p2 = _perm_deny()
    with p1, p2:
        with pytest.raises(BizError):
            await registry.call(
                "get_product_detail", {"product_id": 10},
                hub_user_id=1, acting_as=99,
                conversation_id="conv-3", round_idx=0,
            )


@pytest.mark.asyncio
async def test_get_product_detail_erp_4xx(mock_adapter):
    mock_adapter.get_product.side_effect = ErpNotFoundError("404")
    with pytest.raises(ErpNotFoundError):
        await get_product_detail(product_id=99, acting_as_user_id=1)


@pytest.mark.asyncio
async def test_get_product_detail_erp_5xx(mock_adapter):
    mock_adapter.get_product.side_effect = ErpSystemError("500")
    with pytest.raises(ErpSystemError):
        await get_product_detail(product_id=10, acting_as_user_id=1)


# ---------- get_customer_history ----------

@pytest.mark.asyncio
async def test_get_customer_history_success(mock_adapter):
    mock_adapter.get_product_customer_prices.return_value = {
        "prices": [{"price": 99.0, "date": "2026-01-01"}]
    }
    result = await get_customer_history(
        product_id=1, customer_id=2, limit=5, acting_as_user_id=99,
    )
    assert result["prices"][0]["price"] == 99.0
    mock_adapter.get_product_customer_prices.assert_called_once_with(
        product_id=1, customer_id=2, limit=5, acting_as_user_id=99,
    )


@pytest.mark.asyncio
async def test_get_customer_history_perm_denied(registry, mock_adapter):
    p1, p2 = _perm_deny()
    with p1, p2:
        with pytest.raises(BizError):
            await registry.call(
                "get_customer_history", {"product_id": 1, "customer_id": 2},
                hub_user_id=1, acting_as=99,
                conversation_id="conv-4", round_idx=0,
            )


@pytest.mark.asyncio
async def test_get_customer_history_erp_4xx(mock_adapter):
    mock_adapter.get_product_customer_prices.side_effect = ErpPermissionError("403")
    with pytest.raises(ErpPermissionError):
        await get_customer_history(product_id=1, customer_id=2, acting_as_user_id=99)


@pytest.mark.asyncio
async def test_get_customer_history_erp_5xx(mock_adapter):
    mock_adapter.get_product_customer_prices.side_effect = ErpSystemError("502")
    with pytest.raises(ErpSystemError):
        await get_customer_history(product_id=1, customer_id=2, acting_as_user_id=99)


# ---------- check_inventory ----------

@pytest.mark.asyncio
async def test_check_inventory_success(mock_adapter):
    """check_inventory 从 get_product 结果里提取库存字段。"""
    mock_adapter.get_product.return_value = {
        "id": 5, "name": "商品Y",
        "total_stock": 250,
        "stocks": [{"warehouse": "A", "qty": 250}],
    }
    result = await check_inventory(product_id=5, acting_as_user_id=99)
    assert result == {
        "product_id": 5,
        "total_stock": 250,
        "stocks": [{"warehouse": "A", "qty": 250}],
    }


@pytest.mark.asyncio
async def test_check_inventory_perm_denied(registry, mock_adapter):
    p1, p2 = _perm_deny()
    with p1, p2:
        with pytest.raises(BizError):
            await registry.call(
                "check_inventory", {"product_id": 5},
                hub_user_id=1, acting_as=99,
                conversation_id="conv-5", round_idx=0,
            )


@pytest.mark.asyncio
async def test_check_inventory_erp_4xx(mock_adapter):
    mock_adapter.get_product.side_effect = ErpNotFoundError("404")
    with pytest.raises(ErpNotFoundError):
        await check_inventory(product_id=5, acting_as_user_id=99)


@pytest.mark.asyncio
async def test_check_inventory_erp_5xx(mock_adapter):
    mock_adapter.get_product.side_effect = ErpSystemError("503")
    with pytest.raises(ErpSystemError):
        await check_inventory(product_id=5, acting_as_user_id=99)


# ---------- search_orders ----------

@pytest.mark.asyncio
async def test_search_orders_success(mock_adapter):
    mock_adapter.search_orders.return_value = {"items": [{"id": 100, "status": "confirmed"}]}
    result = await search_orders(customer_id=3, since_days=7, acting_as_user_id=99)
    assert result["items"][0]["id"] == 100
    # 验证 since 参数有传（datetime 计算，不需精确对比值）
    call_kwargs = mock_adapter.search_orders.call_args.kwargs
    assert call_kwargs["customer_id"] == 3
    assert call_kwargs["acting_as_user_id"] == 99
    since_actual = call_kwargs["since"]
    expected_delta = (datetime.now(UTC) - since_actual).total_seconds()
    assert expected_delta == pytest.approx(7 * 86400, abs=10)  # 7 天 ± 10 秒


@pytest.mark.asyncio
async def test_search_orders_perm_denied(registry, mock_adapter):
    p1, p2 = _perm_deny()
    with p1, p2:
        with pytest.raises(BizError):
            await registry.call(
                "search_orders", {},
                hub_user_id=1, acting_as=99,
                conversation_id="conv-6", round_idx=0,
            )


@pytest.mark.asyncio
async def test_search_orders_erp_4xx(mock_adapter):
    mock_adapter.search_orders.side_effect = ErpPermissionError("403")
    with pytest.raises(ErpPermissionError):
        await search_orders(acting_as_user_id=99)


@pytest.mark.asyncio
async def test_search_orders_erp_5xx(mock_adapter):
    mock_adapter.search_orders.side_effect = ErpSystemError("500")
    with pytest.raises(ErpSystemError):
        await search_orders(acting_as_user_id=99)


# ---------- get_order_detail ----------

@pytest.mark.asyncio
async def test_get_order_detail_success(mock_adapter):
    mock_adapter.get_order_detail.return_value = {
        "id": 200, "status": "shipped", "lines": [{"sku": "A001", "qty": 5}]
    }
    result = await get_order_detail(order_id=200, acting_as_user_id=99)
    assert result["id"] == 200
    assert result["status"] == "shipped"
    mock_adapter.get_order_detail.assert_called_once_with(order_id=200, acting_as_user_id=99)


@pytest.mark.asyncio
async def test_get_order_detail_perm_denied(registry, mock_adapter):
    p1, p2 = _perm_deny()
    with p1, p2:
        with pytest.raises(BizError):
            await registry.call(
                "get_order_detail", {"order_id": 200},
                hub_user_id=1, acting_as=99,
                conversation_id="conv-7", round_idx=0,
            )


@pytest.mark.asyncio
async def test_get_order_detail_erp_4xx(mock_adapter):
    mock_adapter.get_order_detail.side_effect = ErpNotFoundError("404")
    with pytest.raises(ErpNotFoundError):
        await get_order_detail(order_id=200, acting_as_user_id=99)


@pytest.mark.asyncio
async def test_get_order_detail_erp_5xx(mock_adapter):
    mock_adapter.get_order_detail.side_effect = ErpSystemError("500")
    with pytest.raises(ErpSystemError):
        await get_order_detail(order_id=200, acting_as_user_id=99)


# ---------- get_customer_balance ----------

@pytest.mark.asyncio
async def test_get_customer_balance_success(mock_adapter):
    mock_adapter.get_customer_balance.return_value = {
        "receivable": 5000.0, "paid": 3000.0, "outstanding": 2000.0,
    }
    result = await get_customer_balance(customer_id=8, acting_as_user_id=99)
    assert result["outstanding"] == 2000.0
    mock_adapter.get_customer_balance.assert_called_once_with(
        customer_id=8, acting_as_user_id=99,
    )


@pytest.mark.asyncio
async def test_get_customer_balance_perm_denied(registry, mock_adapter):
    p1, p2 = _perm_deny()
    with p1, p2:
        with pytest.raises(BizError):
            await registry.call(
                "get_customer_balance", {"customer_id": 8},
                hub_user_id=1, acting_as=99,
                conversation_id="conv-8", round_idx=0,
            )


@pytest.mark.asyncio
async def test_get_customer_balance_erp_4xx(mock_adapter):
    mock_adapter.get_customer_balance.side_effect = ErpPermissionError("403")
    with pytest.raises(ErpPermissionError):
        await get_customer_balance(customer_id=8, acting_as_user_id=99)


@pytest.mark.asyncio
async def test_get_customer_balance_erp_5xx(mock_adapter):
    mock_adapter.get_customer_balance.side_effect = ErpSystemError("502")
    with pytest.raises(ErpSystemError):
        await get_customer_balance(customer_id=8, acting_as_user_id=99)


# ---------- get_inventory_aging ----------

@pytest.mark.asyncio
async def test_get_inventory_aging_success(mock_adapter):
    mock_adapter.get_inventory_aging.return_value = {
        "items": [{"product_id": 9, "days_in_stock": 120, "qty": 50}]
    }
    result = await get_inventory_aging(threshold_days=90, acting_as_user_id=99)
    assert result["items"][0]["days_in_stock"] == 120
    mock_adapter.get_inventory_aging.assert_called_once_with(
        threshold_days=90, acting_as_user_id=99,
    )


@pytest.mark.asyncio
async def test_get_inventory_aging_perm_denied(registry, mock_adapter):
    p1, p2 = _perm_deny()
    with p1, p2:
        with pytest.raises(BizError):
            await registry.call(
                "get_inventory_aging", {},
                hub_user_id=1, acting_as=99,
                conversation_id="conv-9", round_idx=0,
            )


@pytest.mark.asyncio
async def test_get_inventory_aging_erp_4xx(mock_adapter):
    mock_adapter.get_inventory_aging.side_effect = ErpPermissionError("403")
    with pytest.raises(ErpPermissionError):
        await get_inventory_aging(acting_as_user_id=99)


@pytest.mark.asyncio
async def test_get_inventory_aging_erp_5xx(mock_adapter):
    mock_adapter.get_inventory_aging.side_effect = ErpSystemError("503")
    with pytest.raises(ErpSystemError):
        await get_inventory_aging(acting_as_user_id=99)


# ============================================================
# Part 3: register_all + 模块级单例保护
# ============================================================

@pytest.mark.asyncio
async def test_register_all_registers_9_tools(registry):
    """register_all 注册 9 个 tool，schema_for_user 在全权限时返 9 条。"""
    p1, p2 = _perm_allow()
    with p1, p2:
        schemas = await registry.schema_for_user(hub_user_id=1)
    names = {s["function"]["name"] for s in schemas}
    expected = {
        "search_products", "search_customers", "get_product_detail",
        "get_customer_history", "check_inventory",
        "search_orders", "get_order_detail", "get_customer_balance",
        "get_inventory_aging",
    }
    assert names == expected


def test_current_erp_adapter_raises_when_unset():
    """未调 set_erp_adapter 时 current_erp_adapter 应抛 RuntimeError。"""
    set_erp_adapter(None)  # 确保 None
    with pytest.raises(RuntimeError, match="ERP adapter 未初始化"):
        current_erp_adapter()


# ============================================================
# Part 4: Erp4Adapter 4 个新方法 — 4xx / 5xx 错误透传（I-2）
# 使用真实 Erp4Adapter + MockTransport 拦 HTTP，确认错误类正确映射
# ============================================================

@pytest.fixture
def erp_adapter_with_mock(erp_url):
    """返回 (adapter, set_handler) 二元组：set_handler 动态替换 MockTransport 响应。"""
    handler_ref: list = [lambda req: Response(200, json={})]

    def dispatch(req: httpx.Request) -> Response:
        return handler_ref[0](req)

    adapter = _make_real_adapter(erp_url, dispatch)

    def set_handler(fn):
        handler_ref[0] = fn

    return adapter, set_handler


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected_exc", [
    (404, ErpNotFoundError),
    (503, ErpSystemError),
])
@pytest.mark.parametrize("method,kwargs", [
    ("search_orders", {"acting_as_user_id": 99}),
    ("get_order_detail", {"order_id": 1, "acting_as_user_id": 99}),
    ("get_customer_balance", {"customer_id": 1, "acting_as_user_id": 99}),
    ("get_inventory_aging", {"acting_as_user_id": 99}),
])
async def test_adapter_new_methods_propagate_http_errors(
    method, kwargs, status, expected_exc, erp_adapter_with_mock,
):
    """4 个 adapter 新方法 × (404, 503) = 8 case；验证 _act_as_get 错误 chain 不被新方法短路。"""
    adapter, set_handler = erp_adapter_with_mock
    set_handler(lambda req: Response(status, json={"detail": "mock error"}))
    with pytest.raises(expected_exc):
        await getattr(adapter, method)(**kwargs)
