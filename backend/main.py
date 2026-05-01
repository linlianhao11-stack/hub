"""HUB Gateway 进程入口。"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from hub.config import get_settings
from hub.database import close_db, init_db
from hub.routers import health, internal_callbacks, setup, setup_full
from hub.routers.admin import ai_providers as admin_ai_providers
from hub.routers.admin import approvals as admin_approvals
from hub.routers.admin import audit as admin_audit
from hub.routers.admin import channels as admin_channels
from hub.routers.admin import contract_templates as admin_contract_templates
from hub.routers.admin import conversation as admin_conversation
from hub.routers.admin import dashboard as admin_dashboard
from hub.routers.admin import downstreams as admin_downstreams
from hub.routers.admin import login as admin_login
from hub.routers.admin import system_config as admin_system_config
from hub.routers.admin import tasks as admin_tasks
from hub.routers.admin import users as admin_users

logger = logging.getLogger("hub")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(f"HUB Gateway 启动 - 端口 {settings.gateway_port}")

    # 1. 初始化数据库连接（迁移已由 hub-migrate 容器跑完，这里只连）
    await init_db()

    # 2. 跑种子数据
    from hub.seed import run_seed
    await run_seed()

    # 2.5 装 ERP session 鉴权（admin login 用）
    # ERP 下游配置就绪后才能初始化；初始化向导未完成时为 None，
    # /admin/login 会返 503 提示先完成向导
    from hub.adapters.downstream.erp4 import Erp4Adapter
    from hub.auth.erp_session import ErpSessionAuth
    from hub.crypto import decrypt_secret
    from hub.models import DownstreamSystem
    ds_for_session = await DownstreamSystem.filter(
        downstream_type="erp", status="active",
    ).first()
    # C2: 提前 import agent tool 模块，startup 时统一 wire
    from hub.agent.tools import draft_tools, erp_tools, generate_tools

    if ds_for_session is not None:
        erp_api_key_for_session = decrypt_secret(
            ds_for_session.encrypted_apikey, purpose="config_secrets",
        )
        erp_for_session = Erp4Adapter(
            base_url=ds_for_session.base_url, api_key=erp_api_key_for_session,
        )
        app.state.session_auth = ErpSessionAuth(erp_adapter=erp_for_session)
        app.state._session_erp_adapter = erp_for_session  # 留引用方便 shutdown 关闭
        # 注入 approvals router + agent tool 模块所需的 ERP adapter
        admin_approvals.set_erp_adapter(erp_for_session)
        draft_tools.set_erp_adapter(erp_for_session)   # C2: agent 写草稿 tool
        erp_tools.set_erp_adapter(erp_for_session)     # C2: agent 读 ERP tool
        # C2: generate_tools 需要 sender（钉钉主动 push），gateway 进程不构建 sender，传 None
        # sender 由 worker.py 注入（worker 才持有 DingTalkSender 长连接凭据）
        generate_tools.set_dependencies(sender=None, erp=erp_for_session)
    else:
        app.state.session_auth = None
        admin_approvals.set_erp_adapter(None)
        draft_tools.set_erp_adapter(None)
        erp_tools.set_erp_adapter(None)
        generate_tools.set_dependencies(sender=None, erp=None)

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

    # 4. 装 task_runner（confirm-final 回调 + 入站消息都需要它把 task 投到 Redis Streams）
    from redis.asyncio import Redis

    from hub.adapters.channel.dingtalk_stream import DingTalkStreamAdapter
    from hub.lifecycle import connect_with_reload
    from hub.queue import RedisStreamsRunner
    redis_client = Redis.from_url(settings.redis_url, decode_responses=False)
    runner = RedisStreamsRunner(redis_client=redis_client)
    app.state.redis = redis_client
    app.state.task_runner = runner

    # reload event + state holder：admin 改 ChannelApp 后 set() → 后台 task 重启 adapter
    app.state.dingtalk_reload_event = asyncio.Event()
    app.state.dingtalk_state = {}  # holder：{"adapter": DingTalkStreamAdapter | None}

    # 5. 后台 task：循环模式连接钉钉 Stream（不退出，监听 reload event）
    async def _on_inbound(msg):
        await runner.submit("dingtalk_inbound", {
            "channel_type": msg.channel_type,
            "channel_userid": msg.channel_userid,
            "conversation_id": msg.conversation_id,
            "content": msg.content,
            "timestamp": msg.timestamp,
        })

    connect_task = asyncio.create_task(
        connect_with_reload(
            on_inbound=_on_inbound,
            adapter_factory=DingTalkStreamAdapter,
            reload_event=app.state.dingtalk_reload_event,
            state_holder=app.state.dingtalk_state,
        ),
        name="dingtalk_connect",
    )
    app.state.dingtalk_connect_task = connect_task

    # 6. cron 调度器（Plan 5 Task 10）：每天 03:00 巡检 + 04:00 清理 payload
    #    Plan 6 Task 14：09:00 月度 LLM 预算告警
    from hub.cron.jobs import run_daily_audit, run_payload_cleanup
    from hub.cron.scheduler import CronScheduler
    scheduler = CronScheduler()

    @scheduler.at_hour(3)
    async def _job_audit():
        await run_daily_audit()

    @scheduler.at_hour(4)
    async def _job_cleanup():
        await run_payload_cleanup()

    # Plan 6 Task 14：每天 09:00 检查月度 LLM 预算，超 80% 发钉钉告警
    # 注意：sender 依赖 ChannelApp 配置，若未配置则 cron 内部跳过并记 WARNING
    @scheduler.at_hour(9)
    async def _job_budget_alert():
        from hub.adapters.channel.dingtalk_sender import DingTalkSender
        from hub.cron.budget_alert import run_budget_alert
        from hub.crypto import decrypt_secret
        from hub.models import ChannelApp

        app_rec = await ChannelApp.filter(
            channel_type="dingtalk", status="active",
        ).first()
        if app_rec is None:
            logger.warning("budget_alert cron 跳过：没有 active 状态的 dingtalk ChannelApp")
            return

        try:
            app_key = decrypt_secret(app_rec.encrypted_app_key, purpose="config_secrets")
            app_secret = decrypt_secret(app_rec.encrypted_app_secret, purpose="config_secrets")
            robot_id = app_rec.robot_id or ""
        except Exception:
            logger.exception("budget_alert cron 跳过：ChannelApp 解密失败")
            return

        budget_sender = DingTalkSender(
            app_key=app_key, app_secret=app_secret, robot_code=robot_id,
        )
        try:
            result = await run_budget_alert(sender=budget_sender)
            logger.info(f"budget_alert cron 完成: {result}")
        finally:
            await budget_sender.aclose()

    scheduler.start()
    app.state.scheduler = scheduler

    yield

    logger.info("HUB Gateway 关闭")
    # 关闭顺序：先停 scheduler、再取消连接 task、最后 redis / db
    admin_approvals.set_erp_adapter(None)
    draft_tools.set_erp_adapter(None)     # C2: 清除 stale 引用
    erp_tools.set_erp_adapter(None)       # C2: 清除 stale 引用
    generate_tools.set_dependencies(sender=None, erp=None)  # C2: 清除 stale 引用
    if hasattr(app.state, "scheduler"):
        try:
            await app.state.scheduler.stop()
        except Exception:
            logger.exception("cron scheduler 关闭异常")
    if not connect_task.done():
        connect_task.cancel()
        try:
            await connect_task
        except (asyncio.CancelledError, Exception):
            pass
    sess_adapter = getattr(app.state, "_session_erp_adapter", None)
    if sess_adapter is not None:
        try:
            await sess_adapter.aclose()
        except Exception:
            logger.exception("session ERP adapter 关闭异常")
    await redis_client.aclose()
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
app.include_router(setup_full.router)
app.include_router(internal_callbacks.router)
app.include_router(admin_login.router)
app.include_router(admin_users.router)
app.include_router(admin_downstreams.router)
app.include_router(admin_channels.router)
app.include_router(admin_ai_providers.router)
app.include_router(admin_system_config.router)
app.include_router(admin_tasks.router)
app.include_router(admin_conversation.router)
app.include_router(admin_audit.router)
app.include_router(admin_dashboard.router)
app.include_router(admin_approvals.router)
app.include_router(admin_contract_templates.router)


# ============================================================
# 前端 SPA：StaticFiles + catch-all 必须在所有 router 注册之后
# ============================================================
STATIC_DIR = Path(__file__).parent / "static"
ASSETS_DIR = STATIC_DIR / "assets"

if ASSETS_DIR.exists():
    # /assets/* 直接走 StaticFiles（vite 产物 hash 文件名）
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        """SPA fallback：所有非 API、非 /assets 的路径都返回 index.html，
        让 Vue Router 处理 /setup, /login, /admin/* 等前端路由。

        必须放在所有 app.include_router(...) 之后注册，否则会拦截 /hub/v1/* API 请求。
        """
        # 精确文件请求（如 /favicon.ico, /vite.svg）：如果 static 下有就直接返
        if full_path:
            target = STATIC_DIR / full_path
            if target.is_file():
                return FileResponse(target)
        return FileResponse(STATIC_DIR / "index.html")
else:
    @app.get("/", include_in_schema=False)
    async def no_frontend():
        """前端尚未构建：提示去 frontend/ 跑 npm run build。"""
        return Response(
            "Frontend 未构建。请在 frontend/ 目录跑 npm run build，或用 docker compose build hub-gateway 触发多阶段构建。",
            status_code=503,
        )
