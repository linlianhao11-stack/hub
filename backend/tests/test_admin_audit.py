"""admin /audit 路由测试：普通审计 + meta 审计。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from hub.models import AuditLog, MetaAuditLog


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
async def test_list_audit_logs_returns_actor_name(admin_client):
    """普通 audit 列表带 actor_name（display_name）。"""
    ac, actor = admin_client
    await AuditLog.create(
        who_hub_user_id=actor.id, action="assign_roles",
        target_type="hub_user", target_id="42",
        detail={"role_ids": [1]}, ip="127.0.0.1",
    )

    resp = await ac.get("/hub/v1/admin/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["actor_id"] == actor.id
    assert item["actor_name"] == actor.display_name
    assert item["action"] == "assign_roles"
    assert item["target_id"] == "42"


@pytest.mark.asyncio
async def test_list_audit_logs_filters(admin_client):
    ac, actor = admin_client
    await AuditLog.create(
        who_hub_user_id=actor.id, action="assign_roles",
        target_type="hub_user", target_id="1",
    )
    await AuditLog.create(
        who_hub_user_id=actor.id, action="force_unbind",
        target_type="hub_user", target_id="2",
    )

    r1 = await ac.get("/hub/v1/admin/audit", params={"action": "assign_roles"})
    assert r1.json()["total"] == 1
    assert r1.json()["items"][0]["action"] == "assign_roles"

    r2 = await ac.get("/hub/v1/admin/audit", params={"actor_id": actor.id})
    assert r2.json()["total"] == 2

    r3 = await ac.get("/hub/v1/admin/audit", params={"actor_id": 99999})
    assert r3.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_audit_logs_since_hours(admin_client):
    """since_hours 过滤老记录。"""
    ac, actor = admin_client
    old = await AuditLog.create(
        who_hub_user_id=actor.id, action="old_action",
        target_type="hub_user", target_id="1",
    )
    # 把它改成 200h 前
    old.created_at = datetime.now(UTC) - timedelta(hours=200)
    await old.save(update_fields=["created_at"])

    await AuditLog.create(
        who_hub_user_id=actor.id, action="new_action",
        target_type="hub_user", target_id="2",
    )

    resp = await ac.get("/hub/v1/admin/audit", params={"since_hours": 24})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "new_action"


@pytest.mark.asyncio
async def test_list_meta_audit_admin_can_access(admin_client):
    """platform_admin 拥有 platform.audit.system_read。"""
    ac, actor = admin_client
    await MetaAuditLog.create(
        who_hub_user_id=actor.id, viewed_task_id="t-secret", ip="127.0.0.1",
    )

    resp = await ac.get("/hub/v1/admin/audit/meta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["viewed_task_id"] == "t-secret"
    assert item["actor_name"] == actor.display_name


@pytest.mark.asyncio
async def test_list_meta_audit_403_for_ops_role():
    """platform_ops 拥有 audit.read 但没有 audit.system_read → 403。"""
    transport, cookie, _ = await _setup_admin(erp_user_id=2, role_code="platform_ops")
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        # 普通 audit 可以
        r1 = await ac.get("/hub/v1/admin/audit")
        assert r1.status_code == 200
        # meta audit 拒绝
        r2 = await ac.get("/hub/v1/admin/audit/meta")
        assert r2.status_code == 403


@pytest.mark.asyncio
async def test_audit_requires_audit_read_perm():
    """platform_viewer 拥有 audit.read → 200；bot_user_basic 没有 → 403。"""
    transport, cookie, _ = await _setup_admin(erp_user_id=3, role_code="platform_viewer")
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        r = await ac.get("/hub/v1/admin/audit")
        assert r.status_code == 200

    transport2, cookie2, _ = await _setup_admin(erp_user_id=4, role_code="bot_user_basic")
    async with AsyncClient(
        transport=transport2, base_url="http://t",
        cookies={"hub_session": cookie2},
    ) as ac:
        r = await ac.get("/hub/v1/admin/audit")
        assert r.status_code == 403
