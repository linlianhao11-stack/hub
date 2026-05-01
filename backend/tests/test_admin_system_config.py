"""HUB 后台 system_config 路由测试。"""
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
async def test_get_known_key_returns_null_when_unset(admin_client):
    """GET 未写入的已知 key → value=null。"""
    ac, _ = admin_client
    r = await ac.get("/hub/v1/admin/config/alert_receivers")
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "alert_receivers"
    assert body["value"] is None


@pytest.mark.asyncio
async def test_set_and_get_known_key(admin_client):
    """PUT 写入 + GET 读出 list[str]。"""
    from hub.models import SystemConfig

    ac, _ = admin_client
    r = await ac.put(
        "/hub/v1/admin/config/alert_receivers",
        json={"value": ["uid1", "uid2"]},
    )
    assert r.status_code == 200
    rec = await SystemConfig.get(key="alert_receivers")
    assert rec.value == ["uid1", "uid2"]

    r2 = await ac.get("/hub/v1/admin/config/alert_receivers")
    assert r2.status_code == 200
    assert r2.json()["value"] == ["uid1", "uid2"]


@pytest.mark.asyncio
async def test_set_unknown_key_400(admin_client):
    """PUT 未知 key → 400。"""
    ac, _ = admin_client
    r = await ac.put(
        "/hub/v1/admin/config/random_unknown_key",
        json={"value": "anything"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_unknown_key_400(admin_client):
    """GET 未知 key → 400（不让前端瞎读）。"""
    ac, _ = admin_client
    r = await ac.get("/hub/v1/admin/config/random_unknown_key")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_set_wrong_type_rejected(admin_client):
    """alert_receivers 期望 list；给个 str → 400。"""
    ac, _ = admin_client
    r = await ac.put(
        "/hub/v1/admin/config/alert_receivers",
        json={"value": "single_string"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_set_int_for_int_key(admin_client):
    """task_payload_ttl_days 期望 int → 通过。"""
    from hub.models import SystemConfig

    ac, _ = admin_client
    r = await ac.put(
        "/hub/v1/admin/config/task_payload_ttl_days",
        json={"value": 7},
    )
    assert r.status_code == 200
    rec = await SystemConfig.get(key="task_payload_ttl_days")
    assert rec.value == 7


@pytest.mark.asyncio
async def test_set_writes_audit_log(admin_client):
    """PUT 触发 audit_log（who + action + target_id）。"""
    from hub.models import AuditLog

    ac, user = admin_client
    r = await ac.put(
        "/hub/v1/admin/config/daily_audit_hour",
        json={"value": 9},
    )
    assert r.status_code == 200
    audits = await AuditLog.filter(action="update_system_config").all()
    assert len(audits) == 1
    audit = audits[0]
    assert audit.who_hub_user_id == user.id
    assert audit.target_type == "system_config"
    assert audit.target_id == "daily_audit_hour"
    assert audit.detail == {"value": 9}


@pytest.mark.asyncio
async def test_admin_can_update_month_llm_budget(admin_client):
    """v2 加固（review I1）：admin 可通过 system_config UI 配置月预算。"""
    from hub.models import SystemConfig

    ac, _ = admin_client
    resp = await ac.put(
        "/hub/v1/admin/config/month_llm_budget_yuan",
        json={"value": 5000},
    )
    assert resp.status_code == 200

    # 验证值已写入 DB
    rec = await SystemConfig.get(key="month_llm_budget_yuan")
    assert float(rec.value) == 5000.0

    # 验证 dashboard 读到新值
    resp2 = await ac.get("/hub/v1/admin/dashboard")
    assert resp2.status_code == 200
    assert resp2.json()["llm_cost"]["month_budget_yuan"] == 5000.0
