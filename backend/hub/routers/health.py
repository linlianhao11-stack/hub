"""健康检查 endpoint。"""
from __future__ import annotations
import time
from fastapi import APIRouter
from tortoise import connections
from hub import __version__

router = APIRouter(prefix="/hub/v1", tags=["health"])

_app_started_at = time.time()


async def _check_postgres() -> str:
    try:
        conn = connections.get("default")
        await conn.execute_query("SELECT 1")
        return "ok"
    except Exception:
        return "down"


async def _check_redis() -> str:
    try:
        from hub.queue import RedisStreamsRunner  # noqa
        # Redis 检查推迟到 worker 真正连接时；gateway 这里返回 unknown
        return "unknown"
    except Exception:
        return "down"


@router.get("/health")
async def health():
    components = {
        "postgres": await _check_postgres(),
        "redis": await _check_redis(),
        "dingtalk_stream": "not_started",  # Plan 3 启用
        "erp_default": "not_configured",  # Plan 3 启用
    }
    bad = [k for k, v in components.items() if v == "down"]
    status = "healthy" if not bad else "degraded"
    if "postgres" in bad:
        status = "unhealthy"
    return {
        "status": status,
        "components": components,
        "uptime_seconds": int(time.time() - _app_started_at),
        "version": __version__,
    }
