"""HUB Gateway 进程入口。"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hub.config import get_settings
from hub.database import close_db, init_db
from hub.routers import health, internal_callbacks, setup

logger = logging.getLogger("hub")


async def _start_dingtalk_stream(app: FastAPI) -> None:
    """装配钉钉 Stream + sender，把入站消息转 dingtalk_inbound 任务投到 Redis Streams。

    缺配置不报错，只记 warn——首启动时还没人配钉钉是正常的。
    """
    from hub.ports import InboundMessage
    from hub.runtime import bootstrap_dingtalk_clients
    clients = await bootstrap_dingtalk_clients(with_stream=True)
    app.state.dingtalk_clients = clients

    if clients.dingtalk_stream is None:
        logger.warning("DingTalkStreamAdapter 未启动（钉钉应用未配置）")
        return

    runner = app.state.task_runner

    async def _on_message(msg: InboundMessage) -> None:
        await runner.submit("dingtalk_inbound", {
            "channel_userid": msg.channel_userid,
            "conversation_id": msg.conversation_id,
            "content": msg.content,
            "content_type": msg.content_type,
            "timestamp": msg.timestamp,
        })

    clients.dingtalk_stream.on_message(_on_message)
    # Stream.start() 会阻塞跑长连接；放后台任务里跑
    app.state.dingtalk_stream_task = asyncio.create_task(
        clients.dingtalk_stream.start(), name="dingtalk_stream",
    )
    logger.info("DingTalkStreamAdapter 已启动（后台 task）")


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

    # 4. 装配 task_runner（confirm-final 回调 + 入站消息都需要它把 task 投到 Redis Streams）
    from redis.asyncio import Redis

    from hub.queue import RedisStreamsRunner
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    app.state.redis = redis
    app.state.task_runner = RedisStreamsRunner(redis_client=redis)

    # 5. 已初始化才启钉钉 Stream（首启动时还没钉钉配置，无意义）
    if initialized:
        await _start_dingtalk_stream(app)
    else:
        app.state.dingtalk_clients = None

    yield

    logger.info("HUB Gateway 关闭")
    # 反向关：Stream → sender/erp_adapter → redis → db
    stream_task = getattr(app.state, "dingtalk_stream_task", None)
    if stream_task is not None:
        stream_task.cancel()
        try:
            await stream_task
        except (asyncio.CancelledError, Exception):
            pass
    clients = getattr(app.state, "dingtalk_clients", None)
    if clients is not None:
        await clients.aclose()
    redis = getattr(app.state, "redis", None)
    if redis is not None:
        await redis.aclose()
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
