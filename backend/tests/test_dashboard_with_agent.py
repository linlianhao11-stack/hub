"""Plan 6 Task 14：dashboard LLM 成本指标测试（≥6 case）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fakeredis import aioredis as fakeredis_aio
from httpx import ASGITransport, AsyncClient

from hub.models import SystemConfig
from hub.models.conversation import ConversationLog

# ============================================================
# 共用 helper：复用 test_admin_dashboard.py 的 _setup_admin 模式
# ============================================================

async def _setup_admin(erp_user_id: int = 1, role_code: str = "platform_admin"):
    from hub.auth.erp_session import ErpSessionAuth
    from hub.models import DownstreamIdentity, HubRole, HubUser, HubUserRole
    from hub.seed import run_seed
    from main import app

    await run_seed()
    user = await HubUser.create(display_name=f"dash-u{erp_user_id}")
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


# ============================================================
# Case 1：dashboard 返回含 llm_cost 字段
# ============================================================

@pytest.mark.asyncio
async def test_dashboard_includes_llm_cost_metrics(fake_redis):
    """dashboard 返回含 llm_cost 字段，且含 7 个子项。"""
    app, cookie = await _setup_admin(erp_user_id=100)
    app.state.redis = fake_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/dashboard")
        assert resp.status_code == 200
        body = resp.json()
        assert "llm_cost" in body
        cost = body["llm_cost"]
        expected_keys = {
            "today_llm_calls", "today_total_tokens", "today_cost_yuan",
            "month_to_date_cost_yuan", "month_budget_yuan",
            "budget_used_pct", "budget_alert",
        }
        assert set(cost.keys()) == expected_keys


# ============================================================
# Case 2：今日数据正确聚合
# ============================================================

@pytest.mark.asyncio
async def test_llm_cost_today_aggregation(fake_redis):
    """今日数据正确聚合：calls / tokens / cost。"""
    app, cookie = await _setup_admin(erp_user_id=101)
    app.state.redis = fake_redis

    now = datetime.now(UTC)
    await ConversationLog.create(
        conversation_id="c1", started_at=now, channel_userid="u1",
        rounds_count=1, tokens_used=1000, tokens_cost_yuan=Decimal("0.0120"),
    )
    await ConversationLog.create(
        conversation_id="c2", started_at=now, channel_userid="u1",
        rounds_count=2, tokens_used=2000, tokens_cost_yuan=Decimal("0.0240"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/dashboard")
        cost = resp.json()["llm_cost"]
        assert cost["today_llm_calls"] == 2
        assert cost["today_total_tokens"] == 3000
        assert abs(cost["today_cost_yuan"] - 0.036) < 1e-6


# ============================================================
# Case 3：昨天的数据不算今日
# ============================================================

@pytest.mark.asyncio
async def test_llm_cost_excludes_yesterday(fake_redis):
    """昨天的数据不算今日（today_llm_calls 为 0）。"""
    app, cookie = await _setup_admin(erp_user_id=102)
    app.state.redis = fake_redis

    yesterday = datetime.now(UTC) - timedelta(days=1, hours=2)
    await ConversationLog.create(
        conversation_id="c-yesterday", started_at=yesterday, channel_userid="u2",
        rounds_count=1, tokens_used=5000, tokens_cost_yuan=Decimal("0.05"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/dashboard")
        cost = resp.json()["llm_cost"]
        # 今日字段必须为 0（昨天的数据不算今日）
        assert cost["today_llm_calls"] == 0
        assert cost["today_total_tokens"] == 0
        assert cost["today_cost_yuan"] == 0.0
        # 月累计：若今天是月初第 1 天（yesterday 跨月），则 month_to_date 可能也是 0；
        # 若是月中（yesterday 仍在本月），则 month_to_date 含那条记录。
        # 只断言不含今日数据（yesterday 不在"今日"时间段）—已由 today_llm_calls==0 覆盖。


# ============================================================
# Case 4：本月累计聚合正确
# ============================================================

@pytest.mark.asyncio
async def test_llm_cost_month_to_date_aggregation(fake_redis):
    """本月累计含本月所有数据。"""
    app, cookie = await _setup_admin(erp_user_id=103)
    app.state.redis = fake_redis

    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # 本月内取较晚的时间点（避免 month_start 本身在未来的边界情况）
    within_month = max(month_start, now - timedelta(days=5))

    await ConversationLog.create(
        conversation_id="c-mtd", started_at=within_month, channel_userid="u3",
        rounds_count=1, tokens_used=1000, tokens_cost_yuan=Decimal("0.01"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/dashboard")
        cost = resp.json()["llm_cost"]
        assert cost["month_to_date_cost_yuan"] >= 0.01


# ============================================================
# Case 5：月成本 < 80% 预算 → budget_alert=False
# ============================================================

@pytest.mark.asyncio
async def test_llm_cost_budget_alert_below_80pct(fake_redis):
    """月成本 < 80% 预算（默认 1000 元）→ budget_alert=False。"""
    app, cookie = await _setup_admin(erp_user_id=104)
    app.state.redis = fake_redis

    # 写 100 元（远低于 1000 * 80% = 800）
    now = datetime.now(UTC)
    await ConversationLog.create(
        conversation_id="c-low", started_at=now, channel_userid="u4",
        rounds_count=1, tokens_used=100, tokens_cost_yuan=Decimal("100.0"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/dashboard")
        cost = resp.json()["llm_cost"]
        assert cost["budget_alert"] is False
        assert cost["budget_used_pct"] < 80.0


# ============================================================
# Case 6：月成本 ≥ 80% 预算 → budget_alert=True
# ============================================================

@pytest.mark.asyncio
async def test_llm_cost_budget_alert_above_80pct(fake_redis):
    """月成本 ≥ 80% 预算 → budget_alert=True。"""
    app, cookie = await _setup_admin(erp_user_id=105)
    app.state.redis = fake_redis

    # 默认预算 1000，写 850 元 ≥ 80%
    now = datetime.now(UTC)
    await ConversationLog.create(
        conversation_id="c-budget", started_at=now, channel_userid="u5",
        rounds_count=1, tokens_used=10000, tokens_cost_yuan=Decimal("850.0"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/dashboard")
        cost = resp.json()["llm_cost"]
        assert cost["budget_alert"] is True
        assert cost["budget_used_pct"] >= 80.0


# ============================================================
# Case 7：自定义预算从 system_config 读取
# ============================================================

@pytest.mark.asyncio
async def test_llm_cost_custom_budget_from_system_config(fake_redis):
    """system_config 中设置自定义预算，budget_used_pct 按新预算计算。"""
    app, cookie = await _setup_admin(erp_user_id=106)
    app.state.redis = fake_redis

    # 把预算设为 100 元，然后写 90 元成本（90% ≥ 80%）
    await SystemConfig.update_or_create(
        key="month_llm_budget_yuan",
        defaults={"value": 100},
    )
    now = datetime.now(UTC)
    await ConversationLog.create(
        conversation_id="c-custom-budget", started_at=now, channel_userid="u6",
        rounds_count=1, tokens_used=500, tokens_cost_yuan=Decimal("90.0"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/dashboard")
        cost = resp.json()["llm_cost"]
        assert cost["month_budget_yuan"] == 100.0
        assert cost["budget_used_pct"] >= 80.0
        assert cost["budget_alert"] is True


# ============================================================
# Case 8：既有 dashboard 字段保留（Plan 5 contract 不破坏）
# ============================================================

@pytest.mark.asyncio
async def test_llm_cost_preserves_existing_dashboard_fields(fake_redis):
    """既有 dashboard 字段（health / today / hourly）保留，不破坏 Plan 5 contract。"""
    app, cookie = await _setup_admin(erp_user_id=107)
    app.state.redis = fake_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/dashboard")
        assert resp.status_code == 200
        body = resp.json()
        # 既有字段必须存在
        assert "health" in body
        assert "today" in body
        assert "hourly" in body
        # 新增字段
        assert "llm_cost" in body
        # health 四个子项
        assert set(body["health"].keys()) == {
            "postgres", "redis", "dingtalk_stream", "erp_default",
        }
        # today 子项
        assert "total" in body["today"]
        assert "success_rate" in body["today"]
        # hourly 为列表
        assert isinstance(body["hourly"], list)


# ============================================================
# Case 9：恰好 80% 边界 → budget_alert=True（review M10）
# ============================================================

@pytest.mark.asyncio
async def test_llm_cost_budget_alert_exactly_80pct(fake_redis):
    """v2 加固（review M10）：cost = budget * 80% 恰好边界 → budget_alert=True。"""
    app, cookie = await _setup_admin(erp_user_id=108)
    app.state.redis = fake_redis

    now = datetime.now(UTC)
    # 默认 budget=1000；800.0 = 80%
    await ConversationLog.create(
        conversation_id="c-exact-80",
        started_at=now, channel_userid="u-exact-80",
        rounds_count=1, tokens_used=10000,
        tokens_cost_yuan=Decimal("800.0"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/dashboard")
        assert resp.status_code == 200
        cost = resp.json()["llm_cost"]
        assert cost["budget_alert"] is True
        assert cost["budget_used_pct"] >= 80.0
