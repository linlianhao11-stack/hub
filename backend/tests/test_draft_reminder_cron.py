"""Plan 6 Task 15：草稿催促 cron 测试（≥5 case）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from hub.cron.draft_reminder import STALE_DAYS, run_draft_reminder
from hub.models.draft import (
    PriceAdjustmentRequest,
    StockAdjustmentRequest,
    VoucherDraft,
)
from hub.models.identity import ChannelUserBinding, HubUser


# ============================================================
# Case 1：无超 7 天草稿 → sent=0
# ============================================================

@pytest.mark.asyncio
async def test_no_stale_drafts_returns_zero():
    """无超 7 天草稿 → sent=0，skipped=0，不调用 send_text。"""
    sender = AsyncMock()
    result = await run_draft_reminder(sender=sender)
    assert result["sent"] == 0
    assert result["skipped"] == 0
    sender.send_text.assert_not_called()


# ============================================================
# Case 2：有超 7 天 voucher + 用户绑定钉钉 → 发提醒
# ============================================================

@pytest.mark.asyncio
async def test_stale_voucher_with_binding_sends():
    """有超 7 天 pending voucher + 用户有钉钉绑定 → 发催促消息。"""
    sender = AsyncMock()
    old_time = datetime.now(UTC) - timedelta(days=10)

    user = await HubUser.create(display_name="请求人A")
    await VoucherDraft.create(
        requester_hub_user_id=user.id,
        voucher_data={"total_amount": "100"},
        rule_matched="差旅",
        confirmation_action_id="a" * 32,
        status="pending",
        created_at=old_time,
    )
    await ChannelUserBinding.create(
        hub_user=user,
        channel_type="dingtalk",
        channel_userid="DT_U1",
        status="active",
    )

    result = await run_draft_reminder(sender=sender)
    assert result["sent"] == 1
    assert result["skipped"] == 0
    sender.send_text.assert_awaited_once()
    call_args = sender.send_text.call_args
    assert call_args[0][0] == "DT_U1"
    msg = call_args[0][1]
    assert "凭证 1 张" in msg
    assert "10 天" in msg


# ============================================================
# Case 3：有超 7 天草稿但用户没钉钉绑定 → 跳过
# ============================================================

@pytest.mark.asyncio
async def test_stale_drafts_without_binding_skipped():
    """有超 7 天草稿但用户无钉钉绑定 → skipped=1，不发消息。"""
    sender = AsyncMock()
    old_time = datetime.now(UTC) - timedelta(days=10)

    user = await HubUser.create(display_name="请求人B")
    await VoucherDraft.create(
        requester_hub_user_id=user.id,
        voucher_data={"total_amount": "100"},
        rule_matched="x",
        confirmation_action_id="b" * 32,
        status="pending",
        created_at=old_time,
    )
    # 不创建 ChannelUserBinding

    result = await run_draft_reminder(sender=sender)
    assert result["sent"] == 0
    assert result["skipped"] == 1
    sender.send_text.assert_not_called()


# ============================================================
# Case 4：6 天前草稿（未超 7 天）不催促
# ============================================================

@pytest.mark.asyncio
async def test_recent_drafts_not_reminded():
    """6 天前草稿（未超 7 天）不催促。"""
    sender = AsyncMock()
    recent_time = datetime.now(UTC) - timedelta(days=6)

    user = await HubUser.create(display_name="请求人C")
    await VoucherDraft.create(
        requester_hub_user_id=user.id,
        voucher_data={"total_amount": "100"},
        rule_matched="x",
        confirmation_action_id="c" * 32,
        status="pending",
        created_at=recent_time,
    )
    await ChannelUserBinding.create(
        hub_user=user,
        channel_type="dingtalk",
        channel_userid="DT_U3",
        status="active",
    )

    result = await run_draft_reminder(sender=sender)
    assert result["sent"] == 0
    sender.send_text.assert_not_called()


# ============================================================
# Case 5：已审批（status != pending）不催促
# ============================================================

@pytest.mark.asyncio
async def test_approved_drafts_not_reminded():
    """已审批草稿（status=approved）不催促。"""
    sender = AsyncMock()
    old_time = datetime.now(UTC) - timedelta(days=10)

    user = await HubUser.create(display_name="请求人D")
    await VoucherDraft.create(
        requester_hub_user_id=user.id,
        voucher_data={"total_amount": "100"},
        rule_matched="x",
        confirmation_action_id="d" * 32,
        status="approved",
        created_at=old_time,
        approved_by_hub_user_id=99,
    )
    await ChannelUserBinding.create(
        hub_user=user,
        channel_type="dingtalk",
        channel_userid="DT_U4",
        status="active",
    )

    result = await run_draft_reminder(sender=sender)
    assert result["sent"] == 0


# ============================================================
# Case 6：同用户 voucher+price+stock 多张草稿合并成 1 条消息
# ============================================================

@pytest.mark.asyncio
async def test_multi_type_drafts_aggregated_into_one_message():
    """同用户的 voucher+price+stock 多张草稿合并成 1 条钉钉消息。"""
    sender = AsyncMock()
    old_time = datetime.now(UTC) - timedelta(days=10)

    user = await HubUser.create(display_name="请求人E")
    await VoucherDraft.create(
        requester_hub_user_id=user.id,
        voucher_data={"total_amount": "100"},
        rule_matched="x",
        confirmation_action_id="e" * 32,
        status="pending",
        created_at=old_time,
    )
    await PriceAdjustmentRequest.create(
        requester_hub_user_id=user.id,
        customer_id=1,
        product_id=1,
        new_price=100.0,
        status="pending",
        confirmation_action_id="f" * 32,
        created_at=old_time,
    )
    await StockAdjustmentRequest.create(
        requester_hub_user_id=user.id,
        product_id=1,
        adjustment_qty=10,
        reason="盘盈",
        confirmation_action_id="g" * 32,
        status="pending",
        created_at=old_time,
    )
    await ChannelUserBinding.create(
        hub_user=user,
        channel_type="dingtalk",
        channel_userid="DT_U5",
        status="active",
    )

    result = await run_draft_reminder(sender=sender)
    assert result["sent"] == 1  # 一条聚合消息
    sender.send_text.assert_awaited_once()
    msg = sender.send_text.call_args[0][1]
    assert "凭证 1 张" in msg
    assert "调价请求 1 张" in msg
    assert "库存调整 1 张" in msg


# ============================================================
# Case 7：钉钉发送失败计入 skipped
# ============================================================

@pytest.mark.asyncio
async def test_send_failure_counts_as_skipped():
    """钉钉发送失败 → skipped 计数，不影响程序正常结束。"""
    sender = AsyncMock()
    sender.send_text.side_effect = Exception("钉钉宕机")
    old_time = datetime.now(UTC) - timedelta(days=10)

    user = await HubUser.create(display_name="请求人F")
    await VoucherDraft.create(
        requester_hub_user_id=user.id,
        voucher_data={"total_amount": "100"},
        rule_matched="x",
        confirmation_action_id="h" * 32,
        status="pending",
        created_at=old_time,
    )
    await ChannelUserBinding.create(
        hub_user=user,
        channel_type="dingtalk",
        channel_userid="DT_U6",
        status="active",
    )

    result = await run_draft_reminder(sender=sender)
    assert result["sent"] == 0
    assert result["skipped"] == 1
