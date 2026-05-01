"""Plan 6 Task 14：预算告警 cron 测试（≥3 case）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from hub.cron.budget_alert import (
    _mark_alerted,
    _recently_alerted,
    run_budget_alert,
)
from hub.models import SystemConfig
from hub.models.conversation import ConversationLog


# ============================================================
# Case 1：成本 < 80% → 不发告警
# ============================================================

@pytest.mark.asyncio
async def test_budget_alert_below_threshold_skips_send():
    """成本 < 80%（无 ConversationLog）→ 不发告警。"""
    sender = AsyncMock()
    result = await run_budget_alert(sender=sender)
    assert result["sent"] is False
    assert "未达" in result["reason"]
    sender.send_text.assert_not_called()


# ============================================================
# Case 2：成本 ≥ 80% 但 24h 内已告过 → 跳过，不重复发
# ============================================================

@pytest.mark.asyncio
async def test_budget_alert_cooldown_24h():
    """24h 内已告警 → 跳过，不发送。"""
    sender = AsyncMock()

    # 制造高成本（≥80% 预算 1000）
    now = datetime.now(UTC)
    await ConversationLog.create(
        conversation_id="c-cd",
        started_at=now, channel_userid="u1",
        tokens_cost_yuan=Decimal("850.0"),
        rounds_count=1, tokens_used=100,
    )

    # mock _recently_alerted 返 True
    with patch(
        "hub.cron.budget_alert._recently_alerted",
        new=AsyncMock(return_value=True),
    ):
        result = await run_budget_alert(sender=sender)

    assert result["sent"] is False
    assert "24h" in result["reason"] or "已告警" in result["reason"]
    sender.send_text.assert_not_called()


# ============================================================
# Case 3：成本 ≥ 80% 但无 platform_admin 用户 → 跳过
# ============================================================

@pytest.mark.asyncio
async def test_budget_alert_no_admin_users():
    """成本 ≥ 80% 但无 platform_admin 用户 → 返 sent=False reason=无 admin 用户。"""
    sender = AsyncMock()

    now = datetime.now(UTC)
    await ConversationLog.create(
        conversation_id="c-no-admin",
        started_at=now, channel_userid="u2",
        tokens_cost_yuan=Decimal("900.0"),
        rounds_count=1, tokens_used=200,
    )

    # mock _list_platform_admin_user_ids 返空（DB 里没有 platform_admin 角色或用户）
    with patch(
        "hub.cron.budget_alert._list_platform_admin_user_ids",
        new=AsyncMock(return_value=[]),
    ):
        result = await run_budget_alert(sender=sender)

    assert result["sent"] is False
    assert "admin" in result["reason"]
    sender.send_text.assert_not_called()


# ============================================================
# Case 4：成本 ≥ 80% 且有 admin 钉钉绑定 → 发送成功
# ============================================================

@pytest.mark.asyncio
async def test_budget_alert_sends_to_admin_dingtalk():
    """成本 ≥ 80% + admin 有钉钉绑定 → 发告警，记录 cooldown。"""
    from hub.models import ChannelUserBinding, HubRole, HubUser, HubUserRole
    from hub.seed import run_seed

    await run_seed()

    # 创建 platform_admin 用户 + 钉钉绑定
    user = await HubUser.create(display_name="告警 admin")
    role = await HubRole.get(code="platform_admin")
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)
    await ChannelUserBinding.create(
        hub_user=user,
        channel_type="dingtalk",
        channel_userid="admin-dingtalk-id",
        status="active",
    )

    # 制造高成本
    now = datetime.now(UTC)
    await ConversationLog.create(
        conversation_id="c-send",
        started_at=now, channel_userid="u3",
        tokens_cost_yuan=Decimal("850.0"),
        rounds_count=1, tokens_used=300,
    )

    sender = AsyncMock()
    result = await run_budget_alert(sender=sender)

    assert result["sent"] is True
    assert result.get("sent_count", 0) >= 1
    sender.send_text.assert_called_once()
    call_args = sender.send_text.call_args
    assert "admin-dingtalk-id" in call_args[0] or call_args[1].get("dingtalk_userid") == "admin-dingtalk-id"

    # 验证 cooldown 已记录
    rec = await SystemConfig.filter(key="llm_budget_last_alert_at").first()
    assert rec is not None
    assert "at" in rec.value


# ============================================================
# Case 5：_recently_alerted 逻辑（刚告警 < 24h → True）
# ============================================================

@pytest.mark.asyncio
async def test_recently_alerted_returns_true_within_cooldown():
    """刚告警（< 24h）→ _recently_alerted 返 True。"""
    # 写 cooldown key：1 小时前告警
    one_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await SystemConfig.update_or_create(
        key="llm_budget_last_alert_at",
        defaults={"value": {"at": one_hour_ago}},
    )
    result = await _recently_alerted(24)
    assert result is True


# ============================================================
# Case 6：_recently_alerted 逻辑（超过 24h → False）
# ============================================================

@pytest.mark.asyncio
async def test_recently_alerted_returns_false_after_cooldown():
    """25h 前告警 → _recently_alerted 返 False（可以再次发送）。"""
    twenty_five_hours_ago = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    await SystemConfig.update_or_create(
        key="llm_budget_last_alert_at",
        defaults={"value": {"at": twenty_five_hours_ago}},
    )
    result = await _recently_alerted(24)
    assert result is False
