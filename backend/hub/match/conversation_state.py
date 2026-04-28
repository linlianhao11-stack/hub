"""多轮会话状态（多命中选编号 / 低置信度确认 等待用户回复）。

存储：Redis key `hub:conv:<dingtalk_userid>`，TTL 5 分钟。
"""
from __future__ import annotations

import json

from redis.asyncio import Redis


class ConversationStateRepository:
    KEY_PREFIX = "hub:conv:"

    def __init__(self, redis: Redis, *, ttl_seconds: int = 300):
        self.redis = redis
        self.ttl = ttl_seconds

    def _key(self, dingtalk_userid: str) -> str:
        return f"{self.KEY_PREFIX}{dingtalk_userid}"

    async def save(self, dingtalk_userid: str, state: dict) -> None:
        await self.redis.set(
            self._key(dingtalk_userid),
            json.dumps(state, ensure_ascii=False),
            ex=self.ttl,
        )

    async def load(self, dingtalk_userid: str) -> dict | None:
        raw = await self.redis.get(self._key(dingtalk_userid))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    async def clear(self, dingtalk_userid: str) -> None:
        await self.redis.delete(self._key(dingtalk_userid))
