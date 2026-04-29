"""cron jobs 端到端：run_daily_audit 触发 → revoke 离职 binding；
run_payload_cleanup 删过期记录。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from hub.cron.jobs import run_daily_audit, run_payload_cleanup
from hub.crypto import encrypt_secret
from hub.models import ChannelApp, ChannelUserBinding, HubUser, TaskPayload


@pytest.mark.asyncio
async def test_run_daily_audit_revokes_offboarded_binding(monkeypatch):
    """完整链路：DB 里有 ChannelApp + 1 active binding；
    OpenAPI 返回 userid 列表不含该 binding → 应被 revoke。"""
    # 1. 准备 ChannelApp（加密真实凭据）
    await ChannelApp.create(
        channel_type="dingtalk",
        name="test-app",
        encrypted_app_key=encrypt_secret("ak123", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("as123", purpose="config_secrets"),
        robot_id="robot_x",
        status="active",
    )

    # 2. 准备 binding：m_offboarded 离职，m_active 在职
    u1 = await HubUser.create(display_name="离职员工")
    u2 = await HubUser.create(display_name="在职员工")
    await ChannelUserBinding.create(
        hub_user=u1,
        channel_type="dingtalk",
        channel_userid="m_offboarded",
        status="active",
    )
    await ChannelUserBinding.create(
        hub_user=u2,
        channel_type="dingtalk",
        channel_userid="m_active",
        status="active",
    )

    # 3. mock OpenAPI：根部门 → 无子部门；部门 1 只含 m_active
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/gettoken":
            return httpx.Response(200, json={
                "errcode": 0, "access_token": "tk", "expires_in": 7200,
            })
        if req.url.path == "/topapi/v2/department/listsub":
            return httpx.Response(200, json={"errcode": 0, "result": []})
        if req.url.path == "/topapi/user/listid":
            return httpx.Response(200, json={
                "errcode": 0,
                "result": {"userid_list": ["m_active"]},
            })
        return httpx.Response(404)

    # 替换 client 构造，注入 MockTransport
    from hub.cron import jobs as jobs_mod
    real_cls = jobs_mod.DingTalkUserClient

    def make_with_mock(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(jobs_mod, "DingTalkUserClient", make_with_mock)

    # 4. 触发 cron job
    stats = await run_daily_audit()

    # 5. 验证：m_offboarded 已 revoke，m_active 保持 active
    b1 = await ChannelUserBinding.filter(channel_userid="m_offboarded").first()
    b2 = await ChannelUserBinding.filter(channel_userid="m_active").first()
    assert b1.status == "revoked"
    assert b1.revoked_reason == "daily_audit"
    assert b2.status == "active"
    assert stats == {
        "active_dingtalk_userids": 1,
        "active_bindings_before": 2,
        "revoked": 1,
    }


@pytest.mark.asyncio
async def test_run_daily_audit_skips_when_no_channel_app(caplog):
    """没有 active dingtalk ChannelApp → 跳过 + WARN，不抛异常。"""
    import logging
    caplog.set_level(logging.WARNING, logger="hub.cron.jobs")
    result = await run_daily_audit()
    assert result is None
    assert any(
        "没有 active 状态的 dingtalk ChannelApp" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_run_daily_audit_retries_on_openapi_error(monkeypatch):
    """OpenAPI 失败 1 次 → 重试 → 第 2 次成功仍能拿到结果。"""
    await ChannelApp.create(
        channel_type="dingtalk",
        name="t",
        encrypted_app_key=encrypt_secret("ak", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("as", purpose="config_secrets"),
        status="active",
    )

    attempts = [0]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/gettoken":
            attempts[0] += 1
            if attempts[0] == 1:
                return httpx.Response(500, text="server error")
            return httpx.Response(200, json={
                "errcode": 0, "access_token": "tk", "expires_in": 7200,
            })
        if req.url.path == "/topapi/v2/department/listsub":
            return httpx.Response(200, json={"errcode": 0, "result": []})
        if req.url.path == "/topapi/user/listid":
            return httpx.Response(200, json={
                "errcode": 0, "result": {"userid_list": []},
            })
        return httpx.Response(404)

    from hub.cron import jobs as jobs_mod
    real_cls = jobs_mod.DingTalkUserClient

    def make_with_mock(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(jobs_mod, "DingTalkUserClient", make_with_mock)
    # sleep 跳过实际等待
    monkeypatch.setattr("hub.cron.jobs.asyncio.sleep", AsyncMock())

    stats = await run_daily_audit()
    assert stats is not None
    assert attempts[0] == 2  # 第 1 次 500 触发重试，第 2 次成功


@pytest.mark.asyncio
async def test_run_daily_audit_returns_none_after_2_failures(monkeypatch, caplog):
    """2 次 OpenAPI 都失败 → 返回 None，不抛异常炸 scheduler。"""
    import logging
    caplog.set_level(logging.ERROR, logger="hub.cron.jobs")

    await ChannelApp.create(
        channel_type="dingtalk",
        name="t",
        encrypted_app_key=encrypt_secret("ak", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("as", purpose="config_secrets"),
        status="active",
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="always fail")

    from hub.cron import jobs as jobs_mod
    real_cls = jobs_mod.DingTalkUserClient

    def make_with_mock(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(jobs_mod, "DingTalkUserClient", make_with_mock)
    monkeypatch.setattr("hub.cron.jobs.asyncio.sleep", AsyncMock())

    result = await run_daily_audit()
    assert result is None
    assert any(
        "重试 2 次仍失败" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_run_payload_cleanup_deletes_expired():
    """run_payload_cleanup 删过期 task_payload，未过期的保留。

    TaskPayload 有 OneToOneField task_log，必须先建 TaskLog 父记录再绑。
    """
    from hub.models import TaskLog
    now = datetime.now(UTC)
    log_a = await TaskLog.create(
        task_id="t-expired",
        task_type="dingtalk_inbound",
        channel_type="dingtalk",
        channel_userid="u1",
        status="ok",
    )
    log_b = await TaskLog.create(
        task_id="t-fresh",
        task_type="dingtalk_inbound",
        channel_type="dingtalk",
        channel_userid="u2",
        status="ok",
    )
    await TaskPayload.create(
        task_log=log_a,
        encrypted_request=b"enc-req-a",
        encrypted_response=b"enc-resp-a",
        expires_at=now - timedelta(days=1),
    )
    await TaskPayload.create(
        task_log=log_b,
        encrypted_request=b"enc-req-b",
        encrypted_response=b"enc-resp-b",
        expires_at=now + timedelta(days=1),
    )
    n = await run_payload_cleanup()
    assert n == 1
    remaining = await TaskPayload.all().count()
    assert remaining == 1
    # 留下来的一定是 fresh 那条
    survivor = await TaskPayload.first()
    assert (await survivor.task_log).task_id == "t-fresh"


@pytest.mark.asyncio
async def test_run_payload_cleanup_swallows_exceptions(monkeypatch, caplog):
    """DB 异常不能炸 scheduler — 返回 0 + ERROR 日志。"""
    import logging
    caplog.set_level(logging.ERROR, logger="hub.cron.jobs")

    async def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "hub.cron.jobs.cleanup_expired_task_payloads", boom,
    )
    n = await run_payload_cleanup()
    assert n == 0
    assert any("payload cleanup 失败" in r.message for r in caplog.records)
