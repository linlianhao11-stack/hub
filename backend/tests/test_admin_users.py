from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


async def _setup_admin(erp_user_id: int = 1, role_code: str = "platform_admin"):
    """构造 admin 身份 + 注入 session_auth，返回 (client, hub_user)。"""
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
async def test_list_hub_users(admin_client):
    ac, _ = admin_client
    resp = await ac.get("/hub/v1/admin/hub-users")
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio
async def test_get_hub_user_detail(admin_client):
    ac, user = admin_client
    resp = await ac.get(f"/hub/v1/admin/hub-users/{user.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == user.id
    assert "channel_bindings" in body
    assert "roles" in body


@pytest.mark.asyncio
async def test_list_hub_roles(admin_client):
    ac, _ = admin_client
    resp = await ac.get("/hub/v1/admin/hub-roles")
    assert resp.status_code == 200
    codes = {r["code"] for r in resp.json()["items"]}
    assert "platform_admin" in codes
    assert "bot_user_basic" in codes


@pytest.mark.asyncio
async def test_list_hub_permissions(admin_client):
    ac, _ = admin_client
    resp = await ac.get("/hub/v1/admin/hub-permissions")
    body = resp.json()
    perm_codes = {p["code"] for p in body["items"]}
    assert "platform.tasks.read" in perm_codes
    for p in body["items"]:
        assert p["name"]
        assert p["code"] not in p["name"]


@pytest.mark.asyncio
async def test_assign_user_roles_writes_audit(admin_client):
    from hub.models import AuditLog, HubRole, HubUser
    ac, actor = admin_client
    target = await HubUser.create(display_name="目标用户")
    role = await HubRole.get(code="bot_user_basic")

    resp = await ac.put(
        f"/hub/v1/admin/hub-users/{target.id}/roles",
        json={"role_ids": [role.id]},
    )
    assert resp.status_code == 200
    audits = await AuditLog.filter(action="assign_roles").all()
    assert len(audits) >= 1


@pytest.mark.asyncio
async def test_force_unbind(admin_client):
    from hub.models import ChannelUserBinding, HubUser
    ac, _ = admin_client
    target = await HubUser.create(display_name="被解绑")
    await ChannelUserBinding.create(
        hub_user=target, channel_type="dingtalk",
        channel_userid="m_target", status="active",
    )
    resp = await ac.post(
        f"/hub/v1/admin/hub-users/{target.id}/force-unbind",
        params={"channel_type": "dingtalk"},
    )
    assert resp.status_code == 200
    binding = await ChannelUserBinding.filter(channel_userid="m_target").first()
    assert binding.status == "revoked"
    assert binding.revoked_reason == "admin_force"


@pytest.mark.asyncio
async def test_no_perm_user_blocked():
    """没有 platform.users.write 权限的 hub_user 调用应 403。"""
    transport, cookie, _ = await _setup_admin(erp_user_id=2, role_code="platform_viewer")
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/hub-users")
        assert resp.status_code == 403
