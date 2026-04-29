"""admin tasks 路由测试：列表 + 详情（解密 + meta_audit）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from hub.crypto import encrypt_secret
from hub.models import MetaAuditLog, TaskLog, TaskPayload


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


async def _create_task(
    task_id: str = "t1",
    *,
    status: str = "success",
    channel_userid: str = "m1",
    task_type: str = "dingtalk_inbound",
    with_payload: bool = True,
    payload_expired: bool = False,
):
    task = await TaskLog.create(
        task_id=task_id,
        task_type=task_type,
        channel_type="dingtalk",
        channel_userid=channel_userid,
        status=status,
        intent_parser="rule",
        intent_confidence=0.95,
        duration_ms=120,
        finished_at=datetime.now(UTC),
    )
    if with_payload:
        expires_at = datetime.now(UTC) + (
            timedelta(days=-1) if payload_expired else timedelta(days=30)
        )
        await TaskPayload.create(
            task_log=task,
            encrypted_request=encrypt_secret("查 SKU100", purpose="task_payload"),
            encrypted_response=encrypt_secret("鼠标 ¥120", purpose="task_payload"),
            encrypted_erp_calls=encrypt_secret("[]", purpose="task_payload"),
            expires_at=expires_at,
        )
    return task


@pytest.mark.asyncio
async def test_list_tasks_returns_items(admin_client):
    ac, _ = admin_client
    await _create_task("t-list-1")
    await _create_task("t-list-2", channel_userid="m2", status="failed_user")

    resp = await ac.get("/hub/v1/admin/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    task_ids = {it["task_id"] for it in body["items"]}
    assert {"t-list-1", "t-list-2"} == task_ids


@pytest.mark.asyncio
async def test_list_tasks_filters(admin_client):
    ac, _ = admin_client
    await _create_task("t-a", channel_userid="user_alice", status="success")
    await _create_task("t-b", channel_userid="user_bob", status="failed_user")

    resp = await ac.get("/hub/v1/admin/tasks", params={"user_id": "user_alice"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["task_id"] == "t-a"

    resp2 = await ac.get("/hub/v1/admin/tasks", params={"status": "failed_user"})
    assert resp2.json()["total"] == 1
    assert resp2.json()["items"][0]["task_id"] == "t-b"


@pytest.mark.asyncio
async def test_list_tasks_pagination(admin_client):
    ac, _ = admin_client
    for i in range(25):
        await _create_task(f"t-p-{i}")
    resp = await ac.get("/hub/v1/admin/tasks", params={"page": 1, "page_size": 10})
    body = resp.json()
    assert body["total"] == 25
    assert len(body["items"]) == 10
    assert body["page"] == 1


@pytest.mark.asyncio
async def test_get_task_detail_decrypts_payload_and_writes_meta_audit(admin_client):
    ac, actor = admin_client
    await _create_task("t-detail-1")

    resp = await ac.get("/hub/v1/admin/tasks/t-detail-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_log"]["task_id"] == "t-detail-1"
    assert body["payload"]["request_text"] == "查 SKU100"
    assert body["payload"]["response"] == "鼠标 ¥120"
    assert body["payload"]["erp_calls"] == []

    audits = await MetaAuditLog.filter(viewed_task_id="t-detail-1").all()
    assert len(audits) == 1
    assert audits[0].who_hub_user_id == actor.id


@pytest.mark.asyncio
async def test_get_task_detail_skips_expired_payload(admin_client):
    """payload 超期 → 不解密返 None，也不触发 meta_audit。"""
    ac, _ = admin_client
    await _create_task("t-expired", payload_expired=True)

    resp = await ac.get("/hub/v1/admin/tasks/t-expired")
    assert resp.status_code == 200
    assert resp.json()["payload"] is None
    audits = await MetaAuditLog.filter(viewed_task_id="t-expired").all()
    assert len(audits) == 0


@pytest.mark.asyncio
async def test_get_task_detail_404(admin_client):
    ac, _ = admin_client
    resp = await ac.get("/hub/v1/admin/tasks/no-such-task")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_tasks_requires_perm():
    """没有 platform.tasks.read 权限 → 403。"""
    transport, cookie, _ = await _setup_admin(erp_user_id=2, role_code="bot_user_basic")
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/tasks")
        assert resp.status_code == 403
