"""admin /dashboard 路由测试：4 健康卡 + today 数字 + 24h hourly。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fakeredis import aioredis as fakeredis_aio
from httpx import ASGITransport, AsyncClient

from hub.models import TaskLog


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
    return app, cookie


@pytest_asyncio.fixture
async def fake_redis():
    c = fakeredis_aio.FakeRedis()
    yield c
    await c.aclose()


async def _create_inbound_task(
    task_id: str, *,
    status: str = "success",
    channel_userid: str = "m1",
    duration_ms: int = 100,
    minutes_ago: int = 0,
):
    task = await TaskLog.create(
        task_id=task_id,
        task_type="dingtalk_inbound",
        channel_type="dingtalk",
        channel_userid=channel_userid,
        status=status,
        duration_ms=duration_ms,
        finished_at=datetime.now(UTC),
    )
    if minutes_ago:
        task.created_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
        await task.save(update_fields=["created_at"])
    return task


@pytest.mark.asyncio
async def test_dashboard_returns_health_today_hourly_sections(fake_redis):
    """返回 health + today + hourly 三段。"""
    app, cookie = await _setup_admin(erp_user_id=20)
    app.state.redis = fake_redis
    app.state.dingtalk_state = {"adapter": object()}

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/dashboard")
        assert resp.status_code == 200
        body = resp.json()
        assert "health" in body
        assert "today" in body
        assert "hourly" in body
        # 4 个健康卡
        assert set(body["health"].keys()) == {
            "postgres", "redis", "dingtalk_stream", "erp_default",
        }
        assert body["health"]["postgres"] == "ok"
        assert body["health"]["redis"] == "ok"
        assert body["health"]["dingtalk_stream"] == "connected"
        assert body["health"]["erp_default"] == "configured"


@pytest.mark.asyncio
async def test_dashboard_today_counts_accurate(fake_redis):
    """24h 内任务统计 + success_rate 计算正确。"""
    app, cookie = await _setup_admin(erp_user_id=21)
    app.state.redis = fake_redis
    app.state.dingtalk_state = {}

    # 24h 内 10 条：8 success / 2 failed_user
    for i in range(8):
        await _create_inbound_task(f"d-ok-{i}", status="success")
    for i in range(2):
        await _create_inbound_task(f"d-fail-{i}", status="failed_user")
    # 24h 之外 1 条不计入
    await _create_inbound_task("d-old", status="success", minutes_ago=24 * 60 + 30)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/dashboard")
        body = resp.json()
        assert body["today"]["total"] == 10
        assert body["today"]["success"] == 8
        assert body["today"]["failed"] == 2
        assert body["today"]["success_rate"] == 80.0
        # 健康卡降级（无 dingtalk_state）
        assert body["health"]["dingtalk_stream"] == "not_started"


@pytest.mark.asyncio
async def test_dashboard_active_users_counts_distinct(fake_redis):
    """active_users 用 channel_userid 去重。"""
    app, cookie = await _setup_admin(erp_user_id=22)
    app.state.redis = fake_redis

    await _create_inbound_task("u-a-1", channel_userid="user_a")
    await _create_inbound_task("u-a-2", channel_userid="user_a")
    await _create_inbound_task("u-b-1", channel_userid="user_b")
    await _create_inbound_task("u-c-1", channel_userid="user_c")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        body = (await ac.get("/hub/v1/admin/dashboard")).json()
        assert body["today"]["active_users"] == 3
        # avg_duration_ms 平均 100
        assert body["today"]["avg_duration_ms"] == 100


@pytest.mark.asyncio
async def test_dashboard_hourly_has_24_buckets(fake_redis):
    """hourly 数组永远 24 个元素，每元素含 hour/total/success/failed。"""
    app, cookie = await _setup_admin(erp_user_id=23)
    app.state.redis = fake_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        body = (await ac.get("/hub/v1/admin/dashboard")).json()
        assert len(body["hourly"]) == 24
        for bucket in body["hourly"]:
            assert set(bucket.keys()) == {"hour", "total", "success", "failed"}
            assert 0 <= bucket["hour"] < 24


@pytest.mark.asyncio
async def test_dashboard_redis_down_returns_down(fake_redis):
    """redis ping 抛错 → health.redis = down。"""
    app, cookie = await _setup_admin(erp_user_id=24)

    class BrokenRedis:
        async def ping(self):
            raise RuntimeError("redis is down")

    app.state.redis = BrokenRedis()

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        body = (await ac.get("/hub/v1/admin/dashboard")).json()
        assert body["health"]["redis"] == "down"


@pytest.mark.asyncio
async def test_dashboard_requires_tasks_read():
    """没有 platform.tasks.read 权限 → 403。"""
    app, cookie = await _setup_admin(erp_user_id=25, role_code="bot_user_basic")
    app.state.redis = None

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/dashboard")
        assert resp.status_code == 403
