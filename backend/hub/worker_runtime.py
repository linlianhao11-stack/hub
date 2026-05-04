"""Worker 运行时：消费 Redis Streams，按 task_type 路由到 handler。

设计要点（为可测试性优化）：
- block_ms 可注入：测试用短 block 防止 xreadgroup 长阻塞
- redis_client 可注入：测试用 fakeredis；生产 None 时由 hub.config 创建
- 自创建 redis 才在退出时关闭；外部注入的不动
- 提供 run_once()：测试单步消费，避免 stop() 无法中断长 block 的问题
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from hub.queue import RedisStreamsRunner

logger = logging.getLogger("hub.worker")


TaskHandler = Callable[[dict], Awaitable[None]]


class WorkerRuntime:
    def __init__(
        self,
        *,
        group: str = "hub-workers",
        consumer: str | None = None,
        block_ms: int = 5000,
        redis_client=None,  # 注入 fakeredis 用于测试；None 时由 config 创建
    ):
        self.group = group
        self.consumer = consumer or f"worker-{id(self)}"
        self.block_ms = block_ms
        self._handlers: dict[str, TaskHandler] = {}
        self._stop = False
        self._redis_client = redis_client
        self._owns_redis = redis_client is None  # 自己 new 的才负责关

    def register(self, task_type: str, handler: TaskHandler) -> None:
        self._handlers[task_type] = handler

    def _get_redis(self):
        if self._redis_client is not None:
            return self._redis_client
        from redis.asyncio import Redis

        from hub.config import get_settings
        return Redis.from_url(get_settings().redis_url, decode_responses=False)

    async def _process_one(self, runner: RedisStreamsRunner) -> bool:
        """单步消费：拉一条消息并分发给 handler。

        Returns: True 如有消息处理，False 如 block 超时空返回。
        """
        msgs = await runner.read_one(self.group, self.consumer, block_ms=self.block_ms)
        if not msgs:
            return False
        msg_id, data = msgs[0]
        task_type = data.get("task_type")
        handler = self._handlers.get(task_type)
        if not handler:
            logger.warning(f"无 handler 处理 task_type={task_type}, msg_id={msg_id}")
            await runner.move_to_dead(self.group, msg_id, msg_data=data)
            return True
        try:
            await handler(data)
            await runner.ack(self.group, msg_id)
        except Exception as e:
            logger.exception(f"task {task_type} 失败: {e}")
            # 简化：直接死信；Plan 4 加重试次数控制
            await runner.move_to_dead(self.group, msg_id, msg_data=data)
        return True

    async def run_once(self, runner: RedisStreamsRunner | None = None) -> bool:
        """跑一轮消费（测试用）。"""
        if runner is None:
            redis = self._get_redis()
            runner = RedisStreamsRunner(redis_client=redis)
            await runner.ensure_consumer_group(self.group)
        return await self._process_one(runner)

    async def run(self) -> None:
        redis = self._get_redis()
        runner = RedisStreamsRunner(redis_client=redis)
        await runner.ensure_consumer_group(self.group)

        logger.info(f"Worker {self.consumer} 启动，已注册 task_types: {list(self._handlers)}")

        try:
            while not self._stop:
                try:
                    await self._process_one(runner)
                except Exception as e:
                    # v8 review #24：Redis 被 FLUSHDB / 重启 / stream key TTL 过期等场景下
                    # consumer group 会消失，触发 NOGROUP 错误 → worker 死循环。
                    # 自愈：检测 NOGROUP 关键字时主动重建 stream + group，1s 重试。
                    if "NOGROUP" in str(e) or "consumer group" in str(e).lower():
                        logger.warning(
                            "检测到 consumer group 缺失（Redis 可能被 FLUSHDB 或重启），"
                            "自动重建 group=%s",
                            self.group,
                        )
                        try:
                            await runner.ensure_consumer_group(self.group)
                        except Exception:
                            logger.exception("自动重建 consumer group 失败")
                    else:
                        logger.exception("worker loop 错误，1 秒后重试")
                    await asyncio.sleep(1)
        finally:
            if self._owns_redis:
                await redis.aclose()

    async def stop(self):
        self._stop = True
