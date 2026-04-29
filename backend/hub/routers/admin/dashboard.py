"""admin 仪表盘路由：4 个健康卡 + 4 个今日数字 + 24h 桶。

权限：platform.tasks.read（与任务列表同权）

设计要点：
- 健康检查复用 hub.routers.health 的内部探测函数
- dingtalk_stream 状态根据 app.state.dingtalk_state.adapter 是否存在判定
- erp_default 状态根据 app.state.session_auth 是否就绪判定（向导未完成时为 None）
- 24h hourly 桶按"自然小时"返回 24 个 [{hour, total, success, failed}, ...]
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request

from hub.auth.admin_perms import require_hub_perm
from hub.models import TaskLog

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
    }
