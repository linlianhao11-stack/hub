import httpx
import pytest
from httpx import MockTransport, Response


@pytest.fixture
def erp_url():
    return "http://erp.test.local"


@pytest.mark.asyncio
async def test_act_as_user_call_includes_headers(erp_url):
    """业务调用必须带 X-API-Key + X-Acting-As-User-Id 头，且参数名 keyword（非 q）。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter

    captured_headers = {}
    captured_query = {}

    def handler(request: httpx.Request) -> Response:
        captured_headers.update(request.headers)
        captured_query.update(dict(request.url.params))
        return Response(200, json={"items": []})

    adapter = Erp4Adapter(
        base_url=erp_url, api_key="test-key-xyz",
        transport=MockTransport(handler),
    )
    await adapter.search_products(query="x", acting_as_user_id=42)

    assert captured_headers.get("x-api-key") == "test-key-xyz"
    assert captured_headers.get("x-acting-as-user-id") == "42"
    assert captured_query.get("keyword") == "x"


@pytest.mark.asyncio
async def test_act_as_call_without_user_id_raises():
    """ErpAdapter 强制要求 acting_as_user_id；缺失抛 RuntimeError。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter

    adapter = Erp4Adapter(
        base_url="http://x", api_key="k",
        transport=MockTransport(lambda r: Response(200)),
    )
    with pytest.raises(RuntimeError, match="acting_as_user_id"):
        await adapter.search_products(query="x", acting_as_user_id=None)


@pytest.mark.asyncio
async def test_system_call_no_acting_as(erp_url):
    """系统级调用（生成绑定码）不带 X-Acting-As-User-Id。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter

    captured_headers = {}
    def handler(request):
        captured_headers.update(request.headers)
        return Response(200, json={"code": "123456", "expires_in": 300})

    adapter = Erp4Adapter(
        base_url=erp_url, api_key="test-key", transport=MockTransport(handler),
    )
    result = await adapter.generate_binding_code(erp_username="zhangsan", dingtalk_userid="m1")
    assert result["code"] == "123456"
    # 系统接口不应该带 Acting-As 头
    assert "x-acting-as-user-id" not in captured_headers


@pytest.mark.asyncio
async def test_health_check_returns_bool(erp_url):
    from hub.adapters.downstream.erp4 import Erp4Adapter
    # 200 + {"status": "ready"} → True
    adapter = Erp4Adapter(
        base_url=erp_url, api_key="k",
        transport=MockTransport(lambda r: Response(200, json={"status": "ready"})),
    )
    assert await adapter.health_check() is True

    # 200 但 SPA fallback 返 HTML（无 status 字段）→ False（避免误判）
    adapter_html = Erp4Adapter(
        base_url=erp_url, api_key="k",
        transport=MockTransport(lambda r: Response(200, text="<html>")),
    )
    assert await adapter_html.health_check() is False

    # 503 → False
    adapter_down = Erp4Adapter(
        base_url=erp_url, api_key="k",
        transport=MockTransport(lambda r: Response(503)),
    )
    assert await adapter_down.health_check() is False


@pytest.mark.asyncio
async def test_403_translated_to_permission_error():
    """ERP 返回 403 → 抛 PermissionError 给上游。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter, ErpPermissionError

    adapter = Erp4Adapter(
        base_url="http://x", api_key="k",
        transport=MockTransport(lambda r: Response(403, json={"detail": "no perm"})),
    )
    with pytest.raises(ErpPermissionError):
        await adapter.search_products(query="x", acting_as_user_id=1)


@pytest.mark.asyncio
async def test_search_products_uses_keyword_param():
    """ERP 搜索参数必须是 keyword（与 ERP-4 实际接口对齐），不是 q。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter

    captured = {}

    def handler(req):
        captured["url"] = str(req.url)
        return Response(200, json={"items": []})

    adapter = Erp4Adapter(
        base_url="http://x", api_key="k", transport=MockTransport(handler),
    )
    await adapter.search_products(query="SKU100", acting_as_user_id=1)
    assert "keyword=SKU100" in captured["url"]
    assert "q=SKU100" not in captured["url"]


@pytest.mark.asyncio
async def test_search_customers_uses_keyword_param():
    from hub.adapters.downstream.erp4 import Erp4Adapter

    captured = {}

    def handler(req):
        captured["url"] = str(req.url)
        return Response(200, json={"items": []})

    adapter = Erp4Adapter(
        base_url="http://x", api_key="k", transport=MockTransport(handler),
    )
    await adapter.search_customers(query="阿里", acting_as_user_id=1)
    assert "keyword=" in captured["url"]
    assert "%E9%98%BF%E9%87%8C" in captured["url"] or "阿里" in captured["url"]


@pytest.mark.asyncio
async def test_circuit_opens_after_repeated_failures():
    from hub.adapters.downstream.erp4 import Erp4Adapter
    from hub.circuit_breaker import CircuitOpenError

    def handler(req):
        return Response(503)

    adapter = Erp4Adapter(
        base_url="http://x", api_key="k", transport=MockTransport(handler),
    )
    for _ in range(5):
        with pytest.raises(Exception):
            await adapter.search_products(query="x", acting_as_user_id=1)
    with pytest.raises(CircuitOpenError):
        await adapter.search_products(query="x", acting_as_user_id=1)


@pytest.mark.asyncio
async def test_customer_prices_timeout_raises_system_error():
    """历史价查询走 3s 超时；mock 慢响应 → 抛 ErpSystemError。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter, ErpSystemError

    def handler(req):
        raise httpx.TimeoutException("timeout")

    adapter = Erp4Adapter(
        base_url="http://x", api_key="k", transport=MockTransport(handler),
    )
    with pytest.raises(ErpSystemError):
        await adapter.get_product_customer_prices(
            product_id=1, customer_id=2, acting_as_user_id=42,
        )


@pytest.mark.asyncio
async def test_adapter_get_customer_url(erp_url):
    """get_customer 调 /api/v1/customers/{id} + acting_as header。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter

    captured: dict = {}

    def handler(req: httpx.Request) -> Response:
        captured["url"] = str(req.url)
        captured["acting_as"] = req.headers.get("x-acting-as-user-id")
        return Response(200, json={"id": 100, "name": "测试客户", "address": "上海"})

    adapter = Erp4Adapter(
        base_url=erp_url, api_key="test-key", transport=MockTransport(handler),
    )
    result = await adapter.get_customer(customer_id=100, acting_as_user_id=42)

    assert "/api/v1/customers/100" in captured["url"]
    assert captured["acting_as"] == "42"
    assert result["name"] == "测试客户"
