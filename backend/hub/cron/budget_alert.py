"""Plan 6 Task 14：月度 LLM 预算超 80% 钉钉告警 cron。

每天 09:00 跑（scheduler at_hour=9，整点触发）；
若本月累计 cost ≥ budget * 80% 且 24h 内未告过警 → 钉钉发所有 platform_admin 用户。

四个分支：
1. 成本 < 80% → 返 sent=False / reason="未达 80% 阈值"
2. 成本 ≥ 80% 但 24h 内已告过 → 返 sent=False / reason="24h 内已告警，跳过"
3. 成本 ≥ 80% 且无 admin 钉钉绑定 → 返 sent=False / reason="无 admin 钉钉绑定"
4. 成本 ≥ 80% 且发送成功 → 返 sent=True / sent_count=N
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from hub.adapters.channel.dingtalk_sender import DingTalkSender

logger = logging.getLogger("hub.cron.budget_alert")

_LAST_ALERT_KEY = "llm_budget_last_alert_at"
_ALERT_COOLDOWN_HOURS = 24


async def run_budget_alert(*, sender: DingTalkSender) -> dict:
    """跑一次预算告警检查。返回 {sent: bool, reason: str}。

    供 cron 调度调用 + 测试单元复用。

    设计取舍：
    - cooldown 在 send 之后写：发送失败时不消耗 cooldown，能立即重试。
      副作用：手动复跑 + 并发场景理论上能发出双告警；cron 一天 1 次实际几乎不触发。
    - 部分成功也写 cooldown：N 个 admin 中 1 个成功就视为已通知；
      失败的 N-1 个不重试。理由：失败大概率是结构性（access_token 失效），
      24h 内不重发避免风暴；admin 应通过 audit log 看到失败再修。
    """
    from hub.routers.admin.dashboard import _get_llm_cost_metrics

    metrics = await _get_llm_cost_metrics()
    if not metrics["budget_alert"]:
        return {"sent": False, "reason": "未达 80% 阈值"}

    # 检查 cooldown：24h 内已告过 → 跳过
    if await _recently_alerted(_ALERT_COOLDOWN_HOURS):
        return {"sent": False, "reason": "24h 内已告警，跳过"}

    # 找所有 platform_admin 角色用户的钉钉绑定
    admin_user_ids = await _list_platform_admin_user_ids()
    if not admin_user_ids:
        logger.warning("无 platform_admin 用户，无法发预算告警")
        return {"sent": False, "reason": "无 admin 用户"}

    from hub.models.identity import ChannelUserBinding

    bindings = await ChannelUserBinding.filter(
        hub_user_id__in=admin_user_ids,
        channel_type="dingtalk",
        status="active",
    ).all()
    if not bindings:
        logger.warning("admin 用户均未绑定钉钉，无法发预算告警")
        return {"sent": False, "reason": "无 admin 钉钉绑定"}

    # 拼文案
    msg = (
        f"⚠️ HUB LLM 月度预算告警\n\n"
        f"本月已用：¥{metrics['month_to_date_cost_yuan']:.2f}\n"
        f"月度预算：¥{metrics['month_budget_yuan']:.2f}\n"
        f"占用比例：{metrics['budget_used_pct']:.2f}%\n\n"
        f"请尽快调整使用量或上调预算。"
    )

    sent_count = 0
    for binding in bindings:
        try:
            await sender.send_text(binding.channel_userid, msg)
            sent_count += 1
        except Exception:
            logger.exception(
                "钉钉告警发送失败 hub_user_id=%s channel_userid=%s",
                binding.hub_user_id,
                binding.channel_userid,
            )

    if sent_count > 0:
        await _mark_alerted()

    return {
        "sent": sent_count > 0,
        "reason": f"已通知 {sent_count} 个 admin",
        "sent_count": sent_count,
    }


async def _list_platform_admin_user_ids() -> list[int]:
    """找有 platform_admin 角色的所有 hub_user_id。"""
    from hub.models.rbac import HubRole, HubUserRole

    admin_role = await HubRole.filter(code="platform_admin").first()
    if not admin_role:
        return []
    user_role_links = await HubUserRole.filter(role_id=admin_role.id).all()
    return [link.hub_user_id for link in user_role_links]


async def _recently_alerted(hours: int) -> bool:
    """读 system_config 看上次告警是否在 cooldown 内。"""
    from hub.models import SystemConfig

    rec = await SystemConfig.filter(key=_LAST_ALERT_KEY).first()
    if not rec or not rec.value:
        return False
    try:
        val = rec.value
        if isinstance(val, str):
            last_iso = val
        elif isinstance(val, dict):
            last_iso = val.get("at", "")
        else:
            return False
        last_dt = datetime.fromisoformat(last_iso)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=UTC)
        return (datetime.now(UTC) - last_dt) < timedelta(hours=hours)
    except (ValueError, TypeError, AttributeError):
        return False


async def _mark_alerted() -> None:
    """在 system_config 记录本次告警时间。"""
    from hub.models import SystemConfig

    now_iso = datetime.now(UTC).isoformat()
    await SystemConfig.update_or_create(
        key=_LAST_ALERT_KEY,
        defaults={"value": {"at": now_iso}},
    )
