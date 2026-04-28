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
    adapter = Erp4Adapter(
        base_url=erp_url, api_key="k",
        transport=MockTransport(lambda r: Response(200, json={"status": "ok"})),
    )
    assert await adapter.health_check() is True

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
