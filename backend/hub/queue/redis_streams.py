"""Redis Streams + 消费组 + ACK + 死信的 TaskRunner 实现。

Stream 设计：
- hub:tasks:default 为主流
- hub:tasks:dead 为死信流（手动重试 / 告警）
- 消费组名约定：hub-workers

每条消息字段：
  task_id（uuid 字符串）
  task_type
  payload_json
  retry_count
  submitted_at
"""
from __future__ import annotations
import json
import secrets
import time
from typing import Any
from redis.asyncio import Redis


class RedisStreamsRunner:
    def __init__(
        self,
        redis_client: Redis,
        stream_name: str = "hub:tasks:default",
        dead_stream_name: str = "hub:tasks:dead",
        max_len: int = 100000,
    ):
        self.redis = redis_client
        self.stream = stream_name
        self.dead_stream = dead_stream_name
        self.max_len = max_len

    async def submit(self, task_type: str, payload: dict) -> str:
        task_id = secrets.token_urlsafe(16)
        await self.redis.xadd(
            self.stream,
            {
                "task_id": task_id,
                "task_type": task_type,
                "payload_json": json.dumps(payload, ensure_ascii=False),
                "retry_count": "0",
                "submitted_at": str(int(time.time())),
            },
            maxlen=self.max_len, approximate=True,
        )
        return task_id

    async def ensure_consumer_group(self, group: str) -> None:
        try:
            await self.redis.xgroup_create(self.stream, group, id="0", mkstream=True)
        except Exception:
            pass  # 已存在

    async def read_one(self, group: str, consumer: str, *, block_ms: int = 5000) -> list[tuple[str, dict]]:
        result = await self.redis.xreadgroup(
            group, consumer, {self.stream: ">"}, count=1, block=block_ms,
        )
        if not result:
            return []
        out = []
        for stream_name, messages in result:
            for msg_id, data in messages:
                # data 字段 bytes → str
                decoded = {k.decode(): v.decode() for k, v in data.items()}
                if "payload_json" in decoded:
                    decoded["payload"] = json.loads(decoded.pop("payload_json"))
                out.append((msg_id.decode(), decoded))
        return out

    async def ack(self, group: str, msg_id: str) -> None:
        await self.redis.xack(self.stream, group, msg_id)

    async def pending_count(self, group: str) -> int:
        info = await self.redis.xpending(self.stream, group)
        return info.get("pending", 0) if isinstance(info, dict) else info[0]

    async def mark_failed(self, msg_id: str, msg_data: dict) -> None:
        """单次失败 → 不 ack，留在 PEL 等下次 claim。retry_count 由 worker 在 claim 时更新。"""
        # 这里不主动 inc retry_count，因为消息体不可变；retry_count 由 worker 进程内逻辑维护
        # 也可以写到一个独立的 hash 表里跟踪每个 msg_id 的重试次数
        pass

    async def move_to_dead(self, group: str, msg_id: str, msg_data: dict) -> None:
        await self.redis.xadd(
            self.dead_stream,
            {
                "original_msg_id": msg_id,
                "task_id": msg_data.get("task_id", ""),
                "task_type": msg_data.get("task_type", ""),
                "payload_json": json.dumps(msg_data.get("payload", {}), ensure_ascii=False),
                "moved_at": str(int(time.time())),
            },
        )
        await self.ack(group, msg_id)  # 主流 ACK 让消息从 PEL 出去
