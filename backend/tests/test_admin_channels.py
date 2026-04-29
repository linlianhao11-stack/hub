"""HUB 后台 channels 路由测试。"""
from __future__ import annotations

import asyncio
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
    return transport, cookie, user, app


@pytest_asyncio.fixture
async def admin_client():
    transport, cookie, user, app_ = await _setup_admin()
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        # 让测试可以访问 app（ASGITransport 没有 .app 属性）
        ac.app = app_
        yield ac, user


@pytest.mark.asyncio
async def test_create_channel_encrypts_secrets(admin_client):
    """POST /channels：app_key/app_secret 加密入库；不外露明文。"""
    from hub.crypto import decrypt_secret
    from hub.models import ChannelApp

    ac, _ = admin_client
    body = {
        "channel_type": "dingtalk",
        "name": "钉钉机器人",
        "app_key": "ak123",
        "app_secret": "as456",
        "robot_id": "robot_x",
    }
    r = await ac.post("/hub/v1/admin/channels", json=body)
    assert r.status_code == 200
    data = r.json()
    assert "id" in data

    rec = await ChannelApp.get(id=data["id"])
    assert decrypt_secret(rec.encrypted_app_key, purpose="config_secrets") == "ak123"
    assert decrypt_secret(rec.encrypted_app_secret, purpose="config_secrets") == "as456"
    assert rec.robot_id == "robot_x"
    assert rec.status == "active"


@pytest.mark.asyncio
async def test_list_channels_hides_secret(admin_client):
    """GET /channels：不返 encrypted_*；secret_set=true 提示已配置。"""
    from hub.crypto import encrypt_secret
    from hub.models import ChannelApp

    await ChannelApp.create(
        channel_type="dingtalk", name="t",
        encrypted_app_key=encrypt_secret("k", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("s", purpose="config_secrets"),
        status="active",
    )
    ac, _ = admin_client
    r = await ac.get("/hub/v1/admin/channels")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["secret_set"] is True
    assert "encrypted_app_key" not in items[0]
    assert "encrypted_app_secret" not in items[0]
    assert "app_key" not in items[0]
    assert "app_secret" not in items[0]


@pytest.mark.asyncio
async def test_update_channel_replaces_secrets(admin_client):
    """PUT /channels/{id}：rotate secrets + audit。"""
    from hub.crypto import decrypt_secret, encrypt_secret
    from hub.models import AuditLog, ChannelApp

    ca = await ChannelApp.create(
        channel_type="dingtalk", name="t",
        encrypted_app_key=encrypt_secret("ak_old", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("as_old", purpose="config_secrets"),
        robot_id="r1",
        status="active",
    )
    ac, _ = admin_client
    r = await ac.put(
        f"/hub/v1/admin/channels/{ca.id}",
        json={"app_secret": "as_new", "robot_id": "r2"},
    )
    assert r.status_code == 200
    refreshed = await ChannelApp.get(id=ca.id)
    # app_key 没改 → 保留旧值
    assert decrypt_secret(refreshed.encrypted_app_key, purpose="config_secrets") == "ak_old"
    # app_secret 改了
    assert decrypt_secret(refreshed.encrypted_app_secret, purpose="config_secrets") == "as_new"
    assert refreshed.robot_id == "r2"
    audits = await AuditLog.filter(action="update_channel").all()
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_disable_channel(admin_client):
    """POST /channels/{id}/disable → status=disabled + audit。"""
    from hub.crypto import encrypt_secret
    from hub.models import AuditLog, ChannelApp

    ca = await ChannelApp.create(
        channel_type="dingtalk", name="t",
        encrypted_app_key=encrypt_secret("k", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("s", purpose="config_secrets"),
        status="active",
    )
    ac, _ = admin_client
    r = await ac.post(f"/hub/v1/admin/channels/{ca.id}/disable")
    assert r.status_code == 200
    refreshed = await ChannelApp.get(id=ca.id)
    assert refreshed.status == "disabled"
    audits = await AuditLog.filter(action="disable_channel").all()
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_update_channel_sets_reload_event(admin_client):
    """PUT /channels/{id} 修改 app_secret → app.state.dingtalk_reload_event 被 set。"""
    from hub.crypto import encrypt_secret
    from hub.models import ChannelApp

    ca = await ChannelApp.create(
        channel_type="dingtalk", name="t",
        encrypted_app_key=encrypt_secret("k", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("s", purpose="config_secrets"),
        status="active",
    )
    ac, _ = admin_client
    evt = asyncio.Event()
    ac.app.state.dingtalk_reload_event = evt

    r = await ac.put(f"/hub/v1/admin/channels/{ca.id}", json={"app_secret": "new"})
    assert r.status_code == 200
    assert evt.is_set()


@pytest.mark.asyncio
async def test_disable_channel_sets_reload_event(admin_client):
    """POST /channels/{id}/disable → reload event 被 set，让运行中的 Stream 自动停掉。"""
    from hub.crypto import encrypt_secret
    from hub.models import ChannelApp

    ca = await ChannelApp.create(
        channel_type="dingtalk", name="t",
        encrypted_app_key=encrypt_secret("k", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("s", purpose="config_secrets"),
        status="active",
    )
    ac, _ = admin_client
    evt = asyncio.Event()
    ac.app.state.dingtalk_reload_event = evt

    r = await ac.post(f"/hub/v1/admin/channels/{ca.id}/disable")
    assert r.status_code == 200
    assert evt.is_set()


@pytest.mark.asyncio
async def test_update_channel_404(admin_client):
    """PUT 不存在 id → 404。"""
    ac, _ = admin_client
    r = await ac.put("/hub/v1/admin/channels/9999", json={"app_secret": "x"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_no_perm_user_blocked():
    """没有 platform.apikeys.write 的用户调用应 403。"""
    transport, cookie, _, _ = await _setup_admin(erp_user_id=2, role_code="platform_viewer")
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        r = await ac.get("/hub/v1/admin/channels")
        assert r.status_code == 403
