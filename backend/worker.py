"""HUB Worker 进程入口。"""
from __future__ import annotations

import asyncio
import logging

from hub.config import get_settings
from hub.database import close_db, init_db
from hub.worker_runtime import WorkerRuntime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hub.worker")


async def main():
    await init_db()
    settings = get_settings()

    from redis.asyncio import Redis

    from hub.adapters.channel.dingtalk_sender import DingTalkSender
    from hub.adapters.downstream.erp4 import Erp4Adapter
    from hub.crypto import decrypt_secret
    from hub.handlers.dingtalk_inbound import handle_inbound
    from hub.handlers.dingtalk_outbound import handle_outbound
    from hub.models import ChannelApp, DownstreamSystem
    from hub.services.binding_service import BindingService
    from hub.services.erp_active_cache import ErpActiveCache
    from hub.services.identity_service import IdentityService

    redis_client = Redis.from_url(settings.redis_url, decode_responses=False)

    # 钉钉 + ERP 配置必须**双双就绪**才能注册 dingtalk_inbound handler——
    # 否则 worker 启动 + 收到入站消息时 binding_service=None，handler 直接 return，
    # WorkerRuntime 会 ACK 掉消息，**用户消息被静默丢弃**。
    channel_app: ChannelApp | None = None
    ds: DownstreamSystem | None = None
    while channel_app is None or ds is None:
        if channel_app is None:
            channel_app = await ChannelApp.filter(
                channel_type="dingtalk", status="active",
            ).first()
        if ds is None:
            ds = await DownstreamSystem.filter(
                downstream_type="erp", status="active",
            ).first()
        missing = []
        if channel_app is None:
            missing.append("钉钉应用")
        if ds is None:
            missing.append("ERP 下游")
        if missing:
            logger.info(
                f"等待初始化向导完成 [{', '.join(missing)}] 配置 ...（30 秒后重试）",
            )
            await asyncio.sleep(30)

    dt_app_key = decrypt_secret(channel_app.encrypted_app_key, purpose="config_secrets")
    dt_app_secret = decrypt_secret(channel_app.encrypted_app_secret, purpose="config_secrets")
    sender = DingTalkSender(
        app_key=dt_app_key, app_secret=dt_app_secret,
        robot_code=channel_app.robot_id or "",
    )

    erp_api_key = decrypt_secret(ds.encrypted_apikey, purpose="config_secrets")
    erp_adapter = Erp4Adapter(base_url=ds.base_url, api_key=erp_api_key)
    erp_active_cache = ErpActiveCache(erp_adapter=erp_adapter, ttl_seconds=600)
    identity_service = IdentityService(erp_active_cache=erp_active_cache)
    binding_service = BindingService(erp_adapter=erp_adapter)

    runtime = WorkerRuntime(redis_client=redis_client)

    async def dingtalk_inbound_handler(task_data):
        # 进 worker.run() 之前已经轮询确认 binding_service 和 identity_service 都就绪。
        # 此处不再有 None 兜底——一旦走到这里就必须真处理；handler 内部异常由
        # WorkerRuntime 转入死信（不静默 ACK）。
        await handle_inbound(
            task_data,
            binding_service=binding_service,
            identity_service=identity_service,
            sender=sender,
        )

    async def dingtalk_outbound_handler(task_data):
        await handle_outbound(task_data, sender=sender)

    runtime.register("dingtalk_inbound", dingtalk_inbound_handler)
    runtime.register("dingtalk_outbound", dingtalk_outbound_handler)

    try:
        await runtime.run()
    finally:
        await erp_adapter.aclose()
        await sender.aclose()
        await redis_client.aclose()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
