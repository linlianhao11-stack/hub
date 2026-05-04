"""admin 仪表盘路由：4 个健康卡 + 4 个今日数字 + 24h 桶 + LLM 成本指标。

权限：platform.tasks.read（与任务列表同权）

设计要点：
- 健康检查复用 hub.routers.health 的内部探测函数
- dingtalk_stream 状态根据 app.state.dingtalk_state.adapter 是否存在判定
- erp_default 状态根据 app.state.session_auth 是否就绪判定（向导未完成时为 None）
- 24h hourly 桶按"自然小时"返回 24 个 [{hour, total, success, failed}, ...]
- Plan 6 Task 14：新增 llm_cost 子对象（今日调用 + Token + 成本 + 月度预算进度）
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request

from hub.auth.admin_perms import require_hub_perm
from hub.models import TaskLog

logger = logging.getLogger("hub.routers.admin.dashboard")

router = APIRouter(prefix="/hub/v1/admin/dashboard", tags=["admin-dashboard"])


async def _check_redis(request: Request) -> str:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return "down"
    try:
        await redis.ping()
        return "ok"
    except Exception:
        return "down"


def _check_dingtalk_stream(request: Request) -> str:
    state = getattr(request.app.state, "dingtalk_state", None) or {}
    adapter = state.get("adapter")
    return "connected" if adapter is not None else "not_started"


def _check_erp_default(request: Request) -> str:
    auth = getattr(request.app.state, "session_auth", None)
    return "configured" if auth is not None else "not_configured"


@router.get("", dependencies=[Depends(require_hub_perm("platform.tasks.read"))])
async def dashboard(request: Request):
    """聚合：4 个健康卡 + 4 个今日数字 + 24h 桶。"""
    from hub.routers.health import _check_postgres
    health = {
        "postgres": await _check_postgres(),
        "redis": await _check_redis(request),
        "dingtalk_stream": _check_dingtalk_stream(request),
        "erp_default": _check_erp_default(request),
    }

    # 24h 任务统计
    since = datetime.now(UTC) - timedelta(hours=24)
    total = await TaskLog.filter(created_at__gte=since).count()
    success = await TaskLog.filter(
        created_at__gte=since, status="success",
    ).count()
    failed = await TaskLog.filter(
        created_at__gte=since,
        status__in=["failed_user", "failed_system_final"],
    ).count()
    success_rate = (success / total * 100) if total > 0 else 100.0

    # 活跃用户：去重 channel_userid
    active_user_rows = (
        await TaskLog.filter(created_at__gte=since)
        .distinct()
        .values_list("channel_userid", flat=True)
    )
    active_users = len({u for u in active_user_rows if u})

    # ERP 平均延迟（用 duration_ms 近似）
    duration_rows = await TaskLog.filter(
        created_at__gte=since,
    ).exclude(duration_ms__isnull=True).values_list("duration_ms", flat=True)
    avg_duration_ms = (
        int(sum(duration_rows) / len(duration_rows)) if duration_rows else 0
    )

    # 24h hourly：按自然小时分桶（前端画图）
    hourly = []
    for h in range(24):
        bucket_start = since + timedelta(hours=h)
        bucket_end = bucket_start + timedelta(hours=1)
        bucket_total = await TaskLog.filter(
            created_at__gte=bucket_start, created_at__lt=bucket_end,
        ).count()
        bucket_success = await TaskLog.filter(
            created_at__gte=bucket_start,
            created_at__lt=bucket_end,
            status="success",
        ).count()
        bucket_failed = await TaskLog.filter(
            created_at__gte=bucket_start,
            created_at__lt=bucket_end,
            status__in=["failed_user", "failed_system_final"],
        ).count()
        hourly.append({
            "hour": bucket_start.hour,
            "total": bucket_total,
            "success": bucket_success,
            "failed": bucket_failed,
        })

    # Plan 6 Task 14：LLM 成本指标
    llm_cost = await _get_llm_cost_metrics()

    return {
        "health": health,
        "today": {
            "total": total,
            "success": success,
            "failed": failed,
            "success_rate": round(success_rate, 1),
            "active_users": active_users,
            "avg_duration_ms": avg_duration_ms,
        },
        "hourly": hourly,
        "llm_cost": llm_cost,
    }


# ============================================================
# Plan 6 Task 14：LLM 成本指标 helper 函数
# ============================================================

async def _get_llm_cost_metrics() -> dict:
    """计算 LLM 成本指标（今日 + 本月 + 预算告警）。

    性能提示：本月数据加载到 Python 后聚合（O(N) 内存）；
    今日子集 in-memory 过滤（避免 2 次 DB round-trip）。
    生产数据量大时可改 Tortoise annotate(Sum) 让 DB 算（follow-up）。

    时区：按 UTC 算 today_start / month_start；UI"今日"/"本月"语义按 UTC，
    与项目其他 since-24h 计算（task_log）一致。
    """
    from hub.models.conversation import ConversationLog

    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # 一次拉本月，今日子集 in-memory 过滤
    month_logs = await ConversationLog.filter(started_at__gte=month_start).all()
    today_logs = [log for log in month_logs if log.started_at >= today_start]

    today_calls = len(today_logs)
    today_tokens = sum(log.tokens_used or 0 for log in today_logs)
    today_cost = sum(
        float(log.tokens_cost_yuan) if log.tokens_cost_yuan is not None else 0.0
        for log in today_logs
    )
    month_cost = sum(
        float(log.tokens_cost_yuan) if log.tokens_cost_yuan is not None else 0.0
        for log in month_logs
    )

    # 月度预算（system_config 可调，默认 1000 元）
    budget_yuan = await _get_month_budget()

    budget_used_pct = (month_cost / budget_yuan * 100.0) if budget_yuan > 0 else 0.0
    budget_alert = budget_used_pct >= 80.0

    return {
        "today_llm_calls": today_calls,
        "today_total_tokens": today_tokens,
        "today_cost_yuan": round(today_cost, 4),
        "month_to_date_cost_yuan": round(month_cost, 4),
        "month_budget_yuan": float(budget_yuan),
        "budget_used_pct": round(budget_used_pct, 2),
        "budget_alert": budget_alert,
    }


async def _get_month_budget() -> float:
    """从 system_config 读月预算；默认 1000.0 元。"""
    from hub.models import SystemConfig

    try:
        rec = await SystemConfig.filter(key="month_llm_budget_yuan").first()
        if rec and rec.value is not None:
            val = rec.value
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                return float(val)
            if isinstance(val, dict) and "value" in val:
                return float(val["value"])
    except Exception:
        logger.exception("读 month_llm_budget_yuan 失败，回落默认 1000")
    return 1000.0
