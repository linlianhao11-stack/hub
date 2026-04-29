"""实时对话流：基于 Redis Pub/Sub 解耦 worker 与 gateway。

- worker 进程在 task_logger 退出时调 LiveStreamPublisher.publish
- gateway 进程的 SSE endpoint 用 LiveStreamSubscriber.stream 订阅
- channel: conversation:live；payload: JSON 字符串（脱敏后的 task 摘要）
"""
from __future__ import annotations

import json
import logging

from redis.asyncio import Redis

logger = logging.getLogger("hub.observability.live_stream")

CHANNEL = "conversation:live"


class LiveStreamPublisher:
    """worker 调用：把 task_logger 的事件 publish 到 Redis 频道。"""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def publish(self, event: dict) -> None:
        await self.redis.publish(
            CHANNEL,
            json.dumps(event, ensure_ascii=False, default=str),
        )


class LiveStreamSubscriber:
    """gateway SSE endpoint 调用：subscribe 频道并逐条 yield 给 StreamingResponse。"""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def stream(self):
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(CHANNEL)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                yield data
        finally:
            try:
                await pubsub.unsubscribe(CHANNEL)
            except Exception:
                logger.warning("pubsub unsubscribe 失败", exc_info=True)
            try:
                await pubsub.aclose()
            except Exception:
                logger.warning("pubsub aclose 失败", exc_info=True)
