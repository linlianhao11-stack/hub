"""Plan 6 Task 15：草稿催促 cron。

每天 09:00 跑（与 budget_alert 同整点；scheduler 修复后两个 job 都会触发）：
- 找超 7 天未审批的 voucher/price/stock 草稿（status="pending"）
- 按 requester_hub_user_id 分组聚合 → 钉钉提醒请求人"你有 N 张草稿超 7 天未审"

设计取舍：
- 不通知审批人：审批人有"待审批 inbox"页面（Task 12）+ dashboard 计数（Task 14）；
  另外通知会噪音过大。请求人比审批人对自己提的草稿更敏感。
- 同 user_id 同 type 多张草稿合并成 1 条钉钉消息：避免风暴。
- 不写 cooldown：每天提醒 1 次是合理频率；如想 mute 个别可加 follow-up。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from hub.adapters.channel.dingtalk_sender import DingTalkSender
from hub.models.draft import (
    PriceAdjustmentRequest,
    StockAdjustmentRequest,
    VoucherDraft,
)
from hub.models.identity import ChannelUserBinding

logger = logging.getLogger("hub.cron.draft_reminder")

# 7 天阈值（plan §3182）
STALE_DAYS = 7


async def run_draft_reminder(*, sender: DingTalkSender) -> dict:
    """跑一次草稿催促。返回 {sent: int, skipped: int, by_user: dict}。"""
    # M1: datetime.now 提到循环外，避免循环内重复调用
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=STALE_DAYS)

    # 三类草稿合并成 (user_id, draft_type, count) 分组
    by_user: dict[int, dict] = {}

    for draft_type_name, model_class in [
        ("voucher", VoucherDraft),
        ("price", PriceAdjustmentRequest),
        ("stock", StockAdjustmentRequest),
    ]:
        old_drafts = await model_class.filter(
            status="pending", created_at__lte=cutoff,
        ).all()
        for draft in old_drafts:
            user_id = draft.requester_hub_user_id
            if user_id not in by_user:
                by_user[user_id] = {
                    "voucher": 0, "price": 0, "stock": 0,
                    "oldest_age_days": 0,
                }
            by_user[user_id][draft_type_name] += 1
            # M1: 复用循环外的 now
            age_days = (now - draft.created_at).days
            # M2: 用 max() 简化
            by_user[user_id]["oldest_age_days"] = max(
                by_user[user_id]["oldest_age_days"],
                age_days,
            )

    if not by_user:
        return {"sent": 0, "skipped": 0, "by_user": {}}

    # 找钉钉绑定
    user_ids = list(by_user.keys())
    bindings = await ChannelUserBinding.filter(
        hub_user_id__in=user_ids,
        channel_type="dingtalk",
        status="active",
    ).all()
    # I1: 同 user 多绑定时选第一条 + warn，不再静默覆盖
    binding_map: dict[int, str] = {}
    for b in bindings:
        if b.hub_user_id in binding_map:
            logger.warning(
                "用户 %s 有多条 active dingtalk binding（%s + %s），用第一条",
                b.hub_user_id, binding_map[b.hub_user_id], b.channel_userid,
            )
            continue
        binding_map[b.hub_user_id] = b.channel_userid

    sent_count = 0
    skipped_count = 0
    for user_id, summary in by_user.items():
        channel_userid = binding_map.get(user_id)
        if not channel_userid:
            logger.warning(
                "用户 %s 有 %d 张超 %d 天草稿但无钉钉绑定，跳过",
                user_id,
                summary["voucher"] + summary["price"] + summary["stock"],
                STALE_DAYS,
            )
            skipped_count += 1
            continue

        msg = _build_reminder_message(summary)
        try:
            await sender.send_text(channel_userid, msg)
            sent_count += 1
        except Exception:
            logger.exception(
                "钉钉催促发送失败 hub_user=%s",
                user_id,
            )
            skipped_count += 1

    logger.info(
        "草稿催促完成：检查 %d 用户，发送 %d 条，跳过 %d 条",
        len(by_user), sent_count, skipped_count,
    )
    return {
        "sent": sent_count,
        "skipped": skipped_count,
        "by_user": by_user,
    }


def _build_reminder_message(summary: dict) -> str:
    """组织催促文案（中文大白话）。"""
    total = summary["voucher"] + summary["price"] + summary["stock"]
    if total == 0:
        # M8: 防御：调用方应已 early-return 不传空 summary；这里 fail-fast
        raise ValueError("空 summary 不应进入文案生成")
    parts = []
    if summary["voucher"]:
        parts.append(f"凭证 {summary['voucher']} 张")
    if summary["price"]:
        parts.append(f"调价请求 {summary['price']} 张")
    if summary["stock"]:
        parts.append(f"库存调整 {summary['stock']} 张")
    types_str = "、".join(parts)
    return (
        f"📋 您有以下草稿超过 {STALE_DAYS} 天未审批：\n\n"
        f"{types_str}\n"
        f"最早一张已等待 {summary['oldest_age_days']} 天\n\n"
        f"如不再需要，请在 HUB 后台主动撤回；"
        f"如需推进，请联系审批人。"
    )
