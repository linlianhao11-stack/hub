"""健康检查 endpoint。

四个组件状态：
- postgres：直接 SELECT 1
- redis：从 app.state.redis ping
- dingtalk_stream：看 app.state.dingtalk_state["adapter"] 是否已装载
  （True = 后台 task 已用 ChannelApp 配置连上钉钉 Stream）
- erp_default：DB 是否有 status=active 的 DownstreamSystem(erp)
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Request
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


async def _check_redis(request: Request) -> str:
    """gateway 持有 redis client（task_runner 用），直接 ping。"""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return "unknown"
    try:
        await redis.ping()
        return "ok"
    except Exception:
        return "down"


def _check_dingtalk_stream(request: Request) -> str:
    """Plan 5：app.state.dingtalk_state holder 由 connect_with_reload 维护。

    - adapter 字段非 None = 后台 task 已经用 ChannelApp 配置成功 start adapter
    - adapter 字段是 None / state 是 None = 没配置 ChannelApp 或还在等
    """
    state = getattr(request.app.state, "dingtalk_state", None)
    if state is None:
        return "not_started"
    adapter = state.get("adapter")
    if adapter is None:
        return "waiting_config"
    return "connected"


async def _check_erp_default() -> str:
    """是否有 status=active 的 ERP 下游配置。"""
    try:
        from hub.models import DownstreamSystem
        exists = await DownstreamSystem.filter(
            downstream_type="erp", status="active",
        ).exists()
        return "ok" if exists else "not_configured"
    except Exception:
        return "down"


@router.get("/health")
async def health(request: Request):
    components = {
        "postgres": await _check_postgres(),
        "redis": await _check_redis(request),
        "dingtalk_stream": _check_dingtalk_stream(request),
        "erp_default": await _check_erp_default(),
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
