"""admin /conversation/history 列表 + 详情 测试。"""
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


async def _create_inbound_task(
    task_id: str, *,
    channel_userid: str = "m1",
    status: str = "success",
    error_summary: str | None = None,
    with_payload: bool = True,
    payload_expired: bool = False,
):
    task = await TaskLog.create(
        task_id=task_id,
        task_type="dingtalk_inbound",
        channel_type="dingtalk",
        channel_userid=channel_userid,
        status=status,
        intent_parser="rule",
        intent_confidence=0.92,
        duration_ms=200,
        finished_at=datetime.now(UTC),
        error_summary=error_summary,
    )
    if with_payload:
        expires = datetime.now(UTC) + (
            timedelta(days=-1) if payload_expired else timedelta(days=30)
        )
        await TaskPayload.create(
            task_log=task,
            encrypted_request=encrypt_secret("查 SKU100", purpose="task_payload"),
            encrypted_response=encrypt_secret("鼠标 ¥120", purpose="task_payload"),
            encrypted_erp_calls=encrypt_secret("[]", purpose="task_payload"),
            expires_at=expires,
        )
    return task


@pytest.mark.asyncio
async def test_history_returns_only_inbound_tasks(admin_client):
    """history 只返回 dingtalk_inbound 类型的 task。"""
    ac, _ = admin_client
    await _create_inbound_task("h-1")
    # 非 inbound 任务不该出现
    await TaskLog.create(
        task_id="other", task_type="dingtalk_outbound",
        channel_type="dingtalk", channel_userid="m1", status="success",
    )

    resp = await ac.get("/hub/v1/admin/conversation/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["task_id"] == "h-1"


@pytest.mark.asyncio
async def test_history_filter_by_channel_userid_and_status(admin_client):
    ac, _ = admin_client
    await _create_inbound_task("h-a", channel_userid="user_x", status="success")
    await _create_inbound_task("h-b", channel_userid="user_y", status="failed_user")

    r1 = await ac.get(
        "/hub/v1/admin/conversation/history",
        params={"channel_userid": "user_x"},
    )
    assert r1.json()["total"] == 1
    assert r1.json()["items"][0]["task_id"] == "h-a"

    r2 = await ac.get(
        "/hub/v1/admin/conversation/history",
        params={"status": "failed_user"},
    )
    assert r2.json()["total"] == 1
    assert r2.json()["items"][0]["task_id"] == "h-b"


@pytest.mark.asyncio
async def test_history_keyword_searches_error_summary_only(admin_client):
    ac, _ = admin_client
    await _create_inbound_task("h-err1", error_summary="ERP 超时")
    await _create_inbound_task("h-err2", error_summary="未识别意图")
    await _create_inbound_task("h-ok", error_summary=None)

    resp = await ac.get(
        "/hub/v1/admin/conversation/history",
        params={"keyword": "超时"},
    )
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["task_id"] == "h-err1"


@pytest.mark.asyncio
async def test_history_detail_decrypts_and_writes_meta_audit(admin_client):
    ac, actor = admin_client
    await _create_inbound_task("h-detail")

    resp = await ac.get("/hub/v1/admin/conversation/history/h-detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_log"]["task_id"] == "h-detail"
    assert body["payload"]["request_text"] == "查 SKU100"
    assert body["payload"]["response"] == "鼠标 ¥120"

    audits = await MetaAuditLog.filter(viewed_task_id="h-detail").all()
    assert len(audits) == 1
    assert audits[0].who_hub_user_id == actor.id


@pytest.mark.asyncio
async def test_history_detail_404(admin_client):
    ac, _ = admin_client
    resp = await ac.get("/hub/v1/admin/conversation/history/no-such")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_history_requires_monitor_perm():
    """platform.conversation.monitor 是必须的。"""
    transport, cookie, _ = await _setup_admin(erp_user_id=2, role_code="platform_viewer")
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/conversation/history")
        assert resp.status_code == 403
