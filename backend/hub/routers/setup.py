"""初始化向导路由（仅在 system_initialized=false 时可用）。

本 Plan 实现 step 1（自检）+ token 校验骨架。
其余步骤（注册 ERP / 创建 admin / 钉钉 / AI / 完成）由 Plan 5 实现。
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel
from tortoise import connections

from hub.auth.bootstrap_token import verify_and_consume_token
from hub.config import get_settings
from hub.models import SystemConfig

router = APIRouter(prefix="/hub/v1/setup", tags=["setup"])


# 简单进程级 session 存储（PoC 阶段；Plan 5 升级为真正的 session）
_active_setup_sessions: dict[str, bool] = {}


async def _is_initialized() -> bool:
    cfg = await SystemConfig.filter(key="system_initialized").first()
    return bool(cfg and cfg.value is True)


@router.get("/welcome")
async def welcome(request: Request):
    """步骤 1：系统自检。"""
    if await _is_initialized():
        raise HTTPException(status_code=404, detail="HUB 已完成初始化")

    settings = get_settings()
    checks = {
        "master_key": "ok" if settings.master_key else "missing",
    }
    # Postgres
    try:
        conn = connections.get("default")
        await conn.execute_query("SELECT 1")
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"down: {e}"

    # Redis（简化：检查配置，不实际连）
    checks["redis"] = "configured" if settings.redis_url else "missing"

    return {
        "checks": checks,
        "next_step": "verify-token",
    }


class VerifyTokenRequest(BaseModel):
    token: str


@router.post("/verify-token")
async def verify_token_endpoint(payload: VerifyTokenRequest = Body(...)):
    """步骤 1.5：原子校验 + 消费初始化 token，通过后建立 setup session。

    并发安全：使用 verify_and_consume_token，两个并发请求同 token 只有一个赢。
    """
    if await _is_initialized():
        raise HTTPException(status_code=404, detail="HUB 已完成初始化")

    if not await verify_and_consume_token(payload.token):
        raise HTTPException(status_code=401, detail="初始化 Token 错误或已过期")

    session_id = secrets.token_urlsafe(16)
    _active_setup_sessions[session_id] = True
    return {"session": session_id}


@router.get("/status")
async def setup_status():
    """查询当前初始化进度（前端轮询用）。"""
    return {"initialized": await _is_initialized()}
