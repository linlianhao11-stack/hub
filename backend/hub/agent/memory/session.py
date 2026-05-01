"""Plan 6 Task 4：会话层 Redis Memory。

负责：
- 对话历史 append（role + content）
- referenced_entities 集合（customer_ids / product_ids）
- 30 min TTL 自动清理
"""
from __future__ import annotations

import json
from collections.abc import Iterable

from redis.asyncio import Redis

from hub.agent.memory.types import ConversationHistory, ConversationMessage, EntityRefs


class SessionMemory:
    """会话层（Redis）。

    Redis 数据结构：
    - `hub:agent:conv:<id>:msgs` LIST：对话消息（每条 JSON）
    - `hub:agent:conv:<id>:refs:customers` SET：customer_id 整数集合
    - `hub:agent:conv:<id>:refs:products`  SET：product_id 整数集合

    所有 key TTL 30 min；任何写操作都会重置 TTL。
    """
    KEY_PREFIX = "hub:agent:conv:"
    TTL = 1800  # 30 min

    def __init__(self, redis: Redis):
        self.redis = redis

    def _msgs_key(self, conversation_id: str) -> str:
        return f"{self.KEY_PREFIX}{conversation_id}:msgs"

    def _refs_customers_key(self, conversation_id: str) -> str:
        return f"{self.KEY_PREFIX}{conversation_id}:refs:customers"

    def _refs_products_key(self, conversation_id: str) -> str:
        return f"{self.KEY_PREFIX}{conversation_id}:refs:products"

    def _round_state_key(self, conversation_id: str, hub_user_id: int) -> str:
        """v8 staging review #13 (B 方案 state reducer) + #16 (per-user 隔离)：
        每 turn 末尾持久化一份"本轮已确认实体 + 价格 + 数量"摘要 JSON。

        v8 review #16：key 复合 (conversation_id, hub_user_id)。钉钉群聊里多人共享
        同一个 conversation_id，如果只用 conversation_id 做 key，B 用户下一轮会
        看到 A 用户上轮的客户/商品/价格，造成数据泄露 + "按之前要求"误用他人参数。
        """
        return f"{self.KEY_PREFIX}{conversation_id}:round_state:{hub_user_id}"

    async def append(self, conversation_id: str, *,
                     role: str, content: str,
                     tool_call_id: str | None = None) -> None:
        """追加一条消息到会话历史。"""
        msg = json.dumps({
            "role": role,
            "content": content,
            "tool_call_id": tool_call_id,
        }, ensure_ascii=False)
        key = self._msgs_key(conversation_id)
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.rpush(key, msg)
            pipe.expire(key, self.TTL)
            await pipe.execute()

    async def add_entity_refs(self, conversation_id: str, *,
                              customer_ids: Iterable[int] | None = None,
                              product_ids: Iterable[int] | None = None) -> None:
        """ToolRegistry 提取后调用。"""
        cids = list(customer_ids or ())
        pids = list(product_ids or ())
        if not cids and not pids:
            return
        async with self.redis.pipeline(transaction=False) as pipe:
            if cids:
                ck = self._refs_customers_key(conversation_id)
                pipe.sadd(ck, *[str(i) for i in cids])
                pipe.expire(ck, self.TTL)
            if pids:
                pk = self._refs_products_key(conversation_id)
                pipe.sadd(pk, *[str(i) for i in pids])
                pipe.expire(pk, self.TTL)
            await pipe.execute()

    async def get_entity_refs(self, conversation_id: str) -> EntityRefs:
        """读 customer_ids / product_ids 集合。"""
        ck = self._refs_customers_key(conversation_id)
        pk = self._refs_products_key(conversation_id)
        c_raw = await self.redis.smembers(ck)
        p_raw = await self.redis.smembers(pk)
        return EntityRefs(
            customer_ids={int(x) for x in (c_raw or set())},
            product_ids={int(x) for x in (p_raw or set())},
        )

    async def load(self, conversation_id: str) -> ConversationHistory:
        """整体加载（消息 + 实体引用）。"""
        key = self._msgs_key(conversation_id)
        raw_msgs = await self.redis.lrange(key, 0, -1)
        messages = [
            ConversationMessage(**json.loads(m if isinstance(m, str) else m.decode()))
            for m in (raw_msgs or [])
        ]
        refs = await self.get_entity_refs(conversation_id)
        return ConversationHistory(
            conversation_id=conversation_id,
            messages=messages,
            customer_ids=refs.customer_ids,
            product_ids=refs.product_ids,
        )

    async def set_round_state(
        self, conversation_id: str, hub_user_id: int, state: dict,
    ) -> None:
        """v8 staging review #13：写本轮状态摘要（state reducer 模式）。

        v8 review #16：key 加 hub_user_id 实现群聊场景下 per-user 隔离。

        state 结构示例：
          {
            "customers_seen": [{"id": 7, "name": "北京翼蓝", "phone": "..."}],
            "products_seen": [{"id": 5030, "name": "X5 Pro", "sku": "SKU50139"}],
            "last_intent": {"tool": "generate_contract_draft",
                            "args": {"customer_id": 7, "items": [...]}},
          }
        """
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
        """读上轮状态摘要（下一轮 ContextBuilder 加载用）。

        v8 review #16：用 (conversation_id, hub_user_id) 复合 key 读，
        群聊里 B 用户只能读自己的 round_state，看不到 A 用户上轮的实体。
        """
        key = self._round_state_key(conversation_id, hub_user_id)
        raw = await self.redis.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw if isinstance(raw, str) else raw.decode())
        except (json.JSONDecodeError, ValueError):
            return None

    async def clear(self, conversation_id: str) -> None:
        """显式清理（场景：管理员手动重置 / 测试）。

        Plan 6 Task 4 加：管理员重置 / 测试清理；不在 plan 文字 spec 范围但是合理实用方法。

        v8 review #16：round_state 现在按 hub_user_id 拆 key，群聊场景下一个
        conversation 可能有多个 round_state key——用 scan + 模式删除。
        """
        # 固定 key（消息 / 实体引用）
        await self.redis.delete(
            self._msgs_key(conversation_id),
            self._refs_customers_key(conversation_id),
            self._refs_products_key(conversation_id),
        )
        # 模糊 key（per-user round_state）
        pattern = f"{self.KEY_PREFIX}{conversation_id}:round_state:*"
        async for key in self.redis.scan_iter(match=pattern):
            await self.redis.delete(key)
