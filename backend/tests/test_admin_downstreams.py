"""HUB 后台 downstreams 路由测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


async def _setup_admin(erp_user_id: int = 1, role_code: str = "platform_admin"):
    from hub.auth.erp_session import ErpSessionAuth
    from hub.models import DownstreamIdentity, HubRole, HubUser, HubUserRole
    from hub.seed import run_seed
    from main import app
    await run_seed()

    user = await HubUser.create(display_name=f"u{erp_user_id}")
    await DownstreamIdentity.create(
        hub_user=user, downstream_type="erp", downstream_user_id=erp_user_id,
    )
    role = await HubRole.get(code=role_code)
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    erp = AsyncMock()
    erp.get_me = AsyncMock(return_value={
        "id": erp_user_id, "username": f"u{erp_user_id}", "permissions": [],
    })
    auth = ErpSessionAuth(erp_adapter=erp)
    app.state.session_auth = auth
    cookie = auth._encode_cookie({
        "jwt": "tok", "user": {"id": erp_user_id, "username": f"u{erp_user_id}"},
    })
    transport = ASGITransport(app=app)
    return transport, cookie, user


@pytest_asyncio.fixture
async def admin_client():
    transport, cookie, user = await _setup_admin()
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        yield ac, user


@pytest.mark.asyncio
async def test_create_downstream_encrypts_apikey(admin_client):
    """POST /downstreams：明文 api_key 加密入库；不出现在响应里。"""
    from hub.crypto import decrypt_secret
    from hub.models import DownstreamSystem

    ac, _ = admin_client
    body = {
        "downstream_type": "erp",
        "name": "ERP-4 主库",
        "base_url": "http://erp.local",
        "api_key": "plain-secret-123",
        "apikey_scopes": ["system_calls", "act_as_user"],
    }
    r = await ac.post("/hub/v1/admin/downstreams", json=body)
    assert r.status_code == 200
    data = r.json()
    assert "id" in data
    assert data["name"] == "ERP-4 主库"
    assert "api_key" not in data
    assert "encrypted_apikey" not in data

    rec = await DownstreamSystem.get(id=data["id"])
    assert rec.encrypted_apikey != b"plain-secret-123"
    assert decrypt_secret(rec.encrypted_apikey, purpose="config_secrets") == "plain-secret-123"
    assert rec.apikey_scopes == ["system_calls", "act_as_user"]


@pytest.mark.asyncio
async def test_list_downstreams_hides_secret(admin_client):
    """GET /downstreams：不返 encrypted_apikey 明文，只 apikey_set 提示。"""
    from hub.crypto import encrypt_secret
    from hub.models import DownstreamSystem

    await DownstreamSystem.create(
        downstream_type="erp", name="X", base_url="http://x",
        encrypted_apikey=encrypt_secret("k", purpose="config_secrets"),
        apikey_scopes=[], status="active",
    )
    ac, _ = admin_client
    r = await ac.get("/hub/v1/admin/downstreams")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["apikey_set"] is True
    assert "encrypted_apikey" not in items[0]
    assert "api_key" not in items[0]


@pytest.mark.asyncio
async def test_update_apikey_replaces_secret(admin_client):
    """PUT /downstreams/{id}/apikey：rotate 后明文变更。"""
    from hub.crypto import decrypt_secret, encrypt_secret
    from hub.models import AuditLog, DownstreamSystem

    ds = await DownstreamSystem.create(
        downstream_type="erp", name="X", base_url="http://x",
        encrypted_apikey=encrypt_secret("old", purpose="config_secrets"),
        apikey_scopes=["a"], status="active",
    )
    ac, _ = admin_client
    r = await ac.put(
        f"/hub/v1/admin/downstreams/{ds.id}/apikey",
        json={"api_key": "new-secret", "apikey_scopes": ["b", "c"]},
    )
    assert r.status_code == 200
    refreshed = await DownstreamSystem.get(id=ds.id)
    assert decrypt_secret(refreshed.encrypted_apikey, purpose="config_secrets") == "new-secret"
    assert refreshed.apikey_scopes == ["b", "c"]
    audits = await AuditLog.filter(action="update_downstream_apikey").all()
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_update_apikey_404(admin_client):
    """PUT 不存在的 id → 404。"""
    ac, _ = admin_client
    r = await ac.put(
        "/hub/v1/admin/downstreams/9999/apikey",
        json={"api_key": "x"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_test_connection_non_erp_400(admin_client):
    """test-connection：非 erp 类型返 400。"""
    from hub.crypto import encrypt_secret
    from hub.models import DownstreamSystem

    ds = await DownstreamSystem.create(
        downstream_type="crm", name="X", base_url="http://x",
        encrypted_apikey=encrypt_secret("k", purpose="config_secrets"),
        apikey_scopes=[], status="active",
    )
    ac, _ = admin_client
    r = await ac.post(f"/hub/v1/admin/downstreams/{ds.id}/test-connection")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_test_connection_erp_calls_health_check(admin_client, monkeypatch):
    """test-connection：erp 类型下 Erp4Adapter.health_check 被调用，aclose 也调用。"""
    from hub.crypto import encrypt_secret
    from hub.models import DownstreamSystem

    ds = await DownstreamSystem.create(
        downstream_type="erp", name="X", base_url="http://x",
        encrypted_apikey=encrypt_secret("k", purpose="config_secrets"),
        apikey_scopes=[], status="active",
    )

    health_mock = AsyncMock(return_value=True)
    aclose_mock = AsyncMock()

    class _FakeAdapter:
        def __init__(self, *, base_url, api_key):
            assert base_url == "http://x"
            assert api_key == "k"
        health_check = health_mock
        aclose = aclose_mock

    monkeypatch.setattr(
        "hub.adapters.downstream.erp4.Erp4Adapter", _FakeAdapter, raising=True,
    )

    ac, _ = admin_client
    r = await ac.post(f"/hub/v1/admin/downstreams/{ds.id}/test-connection")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    health_mock.assert_called_once()
    aclose_mock.assert_called_once()


@pytest.mark.asyncio
async def test_disable_downstream(admin_client):
    """POST /downstreams/{id}/disable → status=disabled + audit。"""
    from hub.crypto import encrypt_secret
    from hub.models import AuditLog, DownstreamSystem

    ds = await DownstreamSystem.create(
        downstream_type="erp", name="X", base_url="http://x",
        encrypted_apikey=encrypt_secret("k", purpose="config_secrets"),
        apikey_scopes=[], status="active",
    )
    ac, _ = admin_client
    r = await ac.post(f"/hub/v1/admin/downstreams/{ds.id}/disable")
    assert r.status_code == 200
    refreshed = await DownstreamSystem.get(id=ds.id)
    assert refreshed.status == "disabled"
    audits = await AuditLog.filter(action="disable_downstream").all()
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_no_perm_user_blocked():
    """没有 platform.apikeys.write 的用户调用应 403。"""
    transport, cookie, _ = await _setup_admin(erp_user_id=2, role_code="platform_viewer")
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        r = await ac.get("/hub/v1/admin/downstreams")
        assert r.status_code == 403
