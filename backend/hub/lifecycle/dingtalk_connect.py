"""gateway 启动后台 task：等钉钉应用配置就绪后连 Stream。

为什么独立成模块：
- 首次部署 docker compose up → ChannelApp 还没人写 → gateway 不能"启动时查一次就放弃"，
  否则向导写完用户发 /绑定 永远进不到 Redis。改后台轮询：30 秒查一次，连上即退出。
- 抽出来便于注入 fake adapter + 短轮询间隔做单测，不依赖 dingtalk-stream SDK 真启长连接。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from hub.crypto import decrypt_secret
from hub.models import ChannelApp

logger = logging.getLogger("hub.lifecycle.dingtalk_connect")


async def connect_dingtalk_stream_when_ready(
    *,
    on_inbound: Callable[[object], Awaitable[None]],
    adapter_factory: Callable[..., object],
    poll_interval_seconds: float = 30.0,
) -> object | None:
    """轮询 ChannelApp 配置 → 就绪后用 adapter_factory 建 adapter → start。

    Args:
        on_inbound: 入站消息回调（投递任务到 queue 等）
        adapter_factory: 构造 adapter 的工厂（生产用 DingTalkStreamAdapter，
            测试可注入 fake，便于断言 start 被调用）
        poll_interval_seconds: 轮询间隔（生产 30，测试 0.05）

    Returns: 已 start 的 adapter，None 表示被取消
    """
    while True:
        try:
            channel_app = await ChannelApp.filter(
                channel_type="dingtalk", status="active",
            ).first()
            if channel_app is None:
                logger.info("钉钉应用配置尚未就绪，下一轮重试")
                await asyncio.sleep(poll_interval_seconds)
                continue

            app_key = decrypt_secret(channel_app.encrypted_app_key, purpose="config_secrets")
            app_secret = decrypt_secret(channel_app.encrypted_app_secret, purpose="config_secrets")
            adapter = adapter_factory(
                app_key=app_key, app_secret=app_secret, robot_id=channel_app.robot_id,
            )
            adapter.on_message(on_inbound)
            await adapter.start()
            logger.info("钉钉 Stream 已连接")
            return adapter
        except asyncio.CancelledError:
            logger.info("connect task 被取消")
            return None
        except Exception:
            logger.exception("钉钉 Stream 连接失败，下一轮重试")
            await asyncio.sleep(poll_interval_seconds)
