"""HUB Gateway 进程入口。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hub.config import get_settings
from hub.database import close_db, init_db
from hub.routers import health, internal_callbacks, setup

logger = logging.getLogger("hub")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(f"HUB Gateway 启动 - 端口 {settings.gateway_port}")

    # 1. 初始化数据库连接（迁移已由 hub-migrate 容器跑完，这里只连）
    await init_db()

    # 2. 跑种子数据（Task 9 创建 stub，Task 11 替换为真实实现）
    from hub.seed import run_seed
    await run_seed()

    # 3. 首启动检测：未初始化（system_initialized=false）且无未使用 token → 生成并打印
    from datetime import datetime

    from hub.models import BootstrapToken, SystemConfig
    initialized = await SystemConfig.filter(key="system_initialized", value=True).exists()
    if not initialized:
        active_token = await BootstrapToken.filter(
            used_at__isnull=True, expires_at__gt=datetime.now(UTC),
        ).exists()
        if not active_token:
            from hub.auth.bootstrap_token import generate_token
            plaintext = await generate_token(ttl_seconds=settings.setup_token_ttl_seconds)
            ttl_min = settings.setup_token_ttl_seconds // 60
            logger.warning(
                f"\n{'='*60}\n"
                f"  HUB 首次启动 - 初始化模式\n\n"
                f"  请用浏览器打开：\n"
                f"      http://<HUB-host>:{settings.gateway_port}/setup\n\n"
                f"  一次性初始化 Token（{ttl_min} 分钟内有效）：\n\n"
                f"      {plaintext}\n\n"
                f"  将此 token 粘贴到向导第一步。完成初始化或超时后自动失效。\n"
                f"{'='*60}"
            )

    yield

    logger.info("HUB Gateway 关闭")
    await close_db()


app = FastAPI(
    title="HUB Gateway",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",  # Plan 5 加 admin 鉴权
)

# CORS（仅内网访问，宽松配置即可）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 内网部署，安全靠 ApiKey + ERP session
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册
app.include_router(health.router)
app.include_router(setup.router)
app.include_router(internal_callbacks.router)
