"""钉钉员工同步：C 路径（每日巡检，本 Plan 实现） + A 路径函数预留。

详见 spec §8.3 离职/踢出钉钉自动同步。
A 路径（实时事件订阅）的 SDK topic / callback 集成需要 dingtalk-stream SDK
具体版本验证，移到 Plan 5；handle_offboard_event() 已就绪，未来订阅生效后立即可用。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from hub.models import ChannelUserBinding

logger = logging.getLogger("hub.cron.dingtalk_user_sync")


async def daily_employee_audit(dingtalk_client) -> dict:
    """每日凌晨调用：拉钉钉企业全员 → 对比 binding → 已离职的标记 revoked。

    Args:
        dingtalk_client: 提供 fetch_active_userids() -> set[str] 的对象

    Returns: 统计字典
    """
    active_userids = await dingtalk_client.fetch_active_userids()
    logger.info(f"钉钉企业现役 userid 数量: {len(active_userids)}")

    bindings = await ChannelUserBinding.filter(
        channel_type="dingtalk", status="active",
    )
    revoked_count = 0
    for b in bindings:
        if b.channel_userid not in active_userids:
            b.status = "revoked"
            b.revoked_at = datetime.now(UTC)
            b.revoked_reason = "daily_audit"
            await b.save()
            revoked_count += 1
            logger.info(f"daily_audit revoke: dingtalk_userid={b.channel_userid}")

    return {
        "active_dingtalk_userids": len(active_userids),
        "active_bindings_before": len(bindings),
        "revoked": revoked_count,
    }


async def handle_offboard_event(dingtalk_userid: str) -> None:
    """钉钉事件订阅 A 路径：实时收到离职事件 → 立即 revoke。"""
    binding = await ChannelUserBinding.filter(
        channel_type="dingtalk", channel_userid=dingtalk_userid, status="active",
    ).first()
    if binding is None:
        return
    binding.status = "revoked"
    binding.revoked_at = datetime.now(UTC)
    binding.revoked_reason = "dingtalk_offboard"
    await binding.save()
    logger.info(f"event-driven revoke: dingtalk_userid={dingtalk_userid}")
