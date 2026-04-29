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


async def connect_with_reload(
    *,
    on_inbound: Callable[[object], Awaitable[None]],
    adapter_factory: Callable[..., object],
    reload_event: asyncio.Event,
    poll_interval_seconds: float = 30.0,
    state_holder: dict | None = None,
) -> None:
    """循环模式：连接 → 等 reload event → 停止 → 重新连接。

    与 connect_dingtalk_stream_when_ready 的区别：连接后不退出，
    监听 reload_event；event 被 set → 关掉 adapter → 重读 ChannelApp →
    （若 status active 则重连，否则继续等下一次 reload）。

    Args:
        on_inbound: 入站消息回调
        adapter_factory: 构造 adapter 的工厂
        reload_event: asyncio.Event；channels.py update/disable 后 set() 触发重连
        poll_interval_seconds: 配置未就绪时的轮询间隔
        state_holder: 可选 dict，连接成功后写入 {"adapter": adapter}，
            供测试 / lifespan 关闭时拿到当前 adapter 调 stop

    永不返回（除非被 cancel）。
    """
    current_adapter: object | None = None
    while True:
        try:
            channel_app = await ChannelApp.filter(
                channel_type="dingtalk", status="active",
            ).first()
            if channel_app is None:
                if current_adapter is not None:
                    logger.info("ChannelApp 已 disabled，停止现有 Stream")
                    try:
                        await current_adapter.stop()
                    except Exception:
                        logger.exception("旧 adapter stop 失败，忽略")
                    current_adapter = None
                    if state_holder is not None:
                        state_holder["adapter"] = None
                # 等下一次 reload event 或轮询周期
                try:
                    await asyncio.wait_for(
                        reload_event.wait(), timeout=poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
                reload_event.clear()
                continue

            # 有可用 ChannelApp → 启动新 adapter
            if current_adapter is not None:
                logger.info("ChannelApp 配置变更，停止旧 Stream")
                try:
                    await current_adapter.stop()
                except Exception:
                    logger.exception("旧 adapter stop 失败，忽略")
                current_adapter = None

            app_key = decrypt_secret(channel_app.encrypted_app_key, purpose="config_secrets")
            app_secret = decrypt_secret(channel_app.encrypted_app_secret, purpose="config_secrets")
            adapter = adapter_factory(
                app_key=app_key, app_secret=app_secret, robot_id=channel_app.robot_id,
            )
            adapter.on_message(on_inbound)
            # ❗ adapter.start() 是 SDK 长连接 block 调用永不返回（WebSocket 循环），
            #    所以**必须在 start() 之前**就把 adapter 装入 state_holder，否则 health
            #    endpoint 永远看不到 connected 状态
            current_adapter = adapter
            if state_holder is not None:
                state_holder["adapter"] = adapter
            logger.info("钉钉 Stream 配置加载，开始长连接")

            # adapter.start() 与 reload event 并行：reload event 触发时取消 start()
            start_task = asyncio.create_task(adapter.start(), name="dingtalk_adapter_start")
            reload_task = asyncio.create_task(reload_event.wait(), name="dingtalk_reload_wait")
            done, pending = await asyncio.wait(
                {start_task, reload_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            if reload_task in done:
                logger.info("收到 reload 信号，准备重新加载 ChannelApp")
                reload_event.clear()
                # 取消正在跑的 start_task；adapter.stop() 在循环顶部处理
                if not start_task.done():
                    start_task.cancel()
                    try:
                        await start_task
                    except (asyncio.CancelledError, Exception):
                        pass
            else:
                # adapter.start() 自己结束了（异常断连）→ 让 except 路径重试
                if not reload_task.done():
                    reload_task.cancel()
                    try:
                        await reload_task
                    except (asyncio.CancelledError, Exception):
                        pass
                # 取出 start_task 的异常（如果有）触发外层 except 重连
                exc = start_task.exception()
                if exc is not None:
                    raise exc
                logger.info("钉钉 Stream 长连接结束，准备重连")

        except asyncio.CancelledError:
            logger.info("connect_with_reload 被取消，关闭 Stream")
            if current_adapter is not None:
                try:
                    await current_adapter.stop()
                except Exception:
                    pass
            return
        except Exception:
            logger.exception("connect_with_reload 异常，下一轮重试")
            await asyncio.sleep(poll_interval_seconds)
