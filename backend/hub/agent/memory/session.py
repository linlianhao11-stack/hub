"""Plan 6 Task 4：会话层 Redis Memory（per-user 隔离版本）。

负责：
- 对话历史 append（role + content）
- referenced_entities 集合（customer_ids / product_ids）
- round_state 摘要（state reducer 模式）
- 30 min TTL 自动清理

v8 staging review #16/#19：所有 redis key 按 (conversation_id, hub_user_id) 隔离
（钉钉群聊里多人共享同一个 conversation_id，必须避免 A 看到 B 的对话历史 / 实体 refs / state）。
"""
from __future__ import annotations

import json
from collections.abc import Iterable

from redis.asyncio import Redis

from hub.agent.memory.types import ConversationHistory, ConversationMessage, EntityRefs


class SessionMemory:
    """会话层（Redis），所有 key 按 (conversation_id, hub_user_id) 隔离。

    Redis 数据结构：
    - `hub:agent:conv:<conv>:<user>:msgs` LIST：对话消息
    - `hub:agent:conv:<conv>:<user>:refs:customers` SET
    - `hub:agent:conv:<conv>:<user>:refs:products`  SET
    - `hub:agent:conv:<conv>:<user>:round_state`    STRING（JSON）

    所有 key TTL 30 min；任何写操作都会重置 TTL。
    """
    KEY_PREFIX = "hub:agent:conv:"
    TTL = 1800  # 30 min

    def __init__(self, redis: Redis):
        self.redis = redis

    def _user_prefix(self, conversation_id: str, hub_user_id: int) -> str:
        """v8 review #19：所有 key 按 (conv_id, hub_user_id) 二维隔离。"""
        return f"{self.KEY_PREFIX}{conversation_id}:{hub_user_id}"

    def _msgs_key(self, conversation_id: str, hub_user_id: int) -> str:
        return f"{self._user_prefix(conversation_id, hub_user_id)}:msgs"

    def _refs_customers_key(self, conversation_id: str, hub_user_id: int) -> str:
        return f"{self._user_prefix(conversation_id, hub_user_id)}:refs:customers"

    def _refs_products_key(self, conversation_id: str, hub_user_id: int) -> str:
        return f"{self._user_prefix(conversation_id, hub_user_id)}:refs:products"

    def _round_state_key(self, conversation_id: str, hub_user_id: int) -> str:
        """v8 review #13 (state reducer) + #16/#19 (per-user 隔离)。"""
        return f"{self._user_prefix(conversation_id, hub_user_id)}:round_state"

    async def append(self, conversation_id: str, hub_user_id: int, *,
                     role: str, content: str,
                     tool_call_id: str | None = None) -> None:
        """追加一条消息到会话历史（per-user 隔离）。"""
        msg = json.dumps({
            "role": role,
            "content": content,
            "tool_call_id": tool_call_id,
        }, ensure_ascii=False)
        key = self._msgs_key(conversation_id, hub_user_id)
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.rpush(key, msg)
            pipe.expire(key, self.TTL)
            await pipe.execute()

    async def add_entity_refs(self, conversation_id: str, hub_user_id: int, *,
                              customer_ids: Iterable[int] | None = None,
                              product_ids: Iterable[int] | None = None) -> None:
        """ToolRegistry 提取后调用，per-user 隔离。"""
        cids = list(customer_ids or ())
        pids = list(product_ids or ())
        if not cids and not pids:
            return
        async with self.redis.pipeline(transaction=False) as pipe:
            if cids:
                ck = self._refs_customers_key(conversation_id, hub_user_id)
                pipe.sadd(ck, *[str(i) for i in cids])
                pipe.expire(ck, self.TTL)
            if pids:
                pk = self._refs_products_key(conversation_id, hub_user_id)
                pipe.sadd(pk, *[str(i) for i in pids])
                pipe.expire(pk, self.TTL)
            await pipe.execute()

    async def get_entity_refs(
        self, conversation_id: str, hub_user_id: int,
    ) -> EntityRefs:
        """读 customer_ids / product_ids 集合（per-user 隔离）。"""
        ck = self._refs_customers_key(conversation_id, hub_user_id)
        pk = self._refs_products_key(conversation_id, hub_user_id)
        c_raw = await self.redis.smembers(ck)
        p_raw = await self.redis.smembers(pk)
        return EntityRefs(
            customer_ids={int(x) for x in (c_raw or set())},
            product_ids={int(x) for x in (p_raw or set())},
        )

    async def load(
        self, conversation_id: str, hub_user_id: int,
    ) -> ConversationHistory:
        """整体加载（消息 + 实体引用），per-user 隔离。"""
        key = self._msgs_key(conversation_id, hub_user_id)
        raw_msgs = await self.redis.lrange(key, 0, -1)
        messages = [
            ConversationMessage(**json.loads(m if isinstance(m, str) else m.decode()))
            for m in (raw_msgs or [])
        ]
        refs = await self.get_entity_refs(conversation_id, hub_user_id)
        return ConversationHistory(
            conversation_id=conversation_id,
            messages=messages,
            customer_ids=refs.customer_ids,
            product_ids=refs.product_ids,
        )

    async def set_round_state(
        self, conversation_id: str, hub_user_id: int, state: dict,
    ) -> None:
        """写本轮状态摘要（state reducer 模式 + per-user 隔离）。"""
        if not state:
            return
        key = self._round_state_key(conversation_id, hub_user_id)
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.set(key, json.dumps(state, ensure_ascii=False))
            pipe.expire(key, self.TTL)
            await pipe.execute()

    async def get_round_state(
        self, conversation_id: str, hub_user_id: int,
    ) -> dict | None:
        """读上轮状态摘要。"""
        key = self._round_state_key(conversation_id, hub_user_id)
        raw = await self.redis.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw if isinstance(raw, str) else raw.decode())
        except (json.JSONDecodeError, ValueError):
            return None

    async def clear(
        self, conversation_id: str, hub_user_id: int | None = None,
    ) -> None:
        """显式清理（管理员重置 / 测试）。

        v8 review #19：
          - hub_user_id 传具体 ID → 只清这个 user 在这个 conv 的所有 key
          - hub_user_id=None → 清整个 conv 下所有 user 的 key（admin 群聊重置场景）
        """
        if hub_user_id is not None:
            await self.redis.delete(
                self._msgs_key(conversation_id, hub_user_id),
                self._refs_customers_key(conversation_id, hub_user_id),
                self._refs_products_key(conversation_id, hub_user_id),
                self._round_state_key(conversation_id, hub_user_id),
            )
            return
        # 全 conv 清理：用 scan 模糊删（所有 user）
        pattern = f"{self.KEY_PREFIX}{conversation_id}:*"
        async for key in self.redis.scan_iter(match=pattern):
            await self.redis.delete(key)
