"""Plan 6 Task 4：MemoryLoader 组装四层 + token 上限截断。"""
from __future__ import annotations
import logging

from hub.agent.memory.session import SessionMemory
from hub.agent.memory.persistent import (
    UserMemoryService, CustomerMemoryService, ProductMemoryService,
)
from hub.agent.memory.types import Memory


logger = logging.getLogger("hub.agent.memory.loader")


# token 上限（spec §3.3）
SESSION_TOKEN_BUDGET = 4000
USER_TOKEN_BUDGET = 1000
CUSTOMER_TOKEN_BUDGET = 500   # 单个客户
PRODUCT_TOKEN_BUDGET = 200    # 单个商品


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文 ~1.5 char/token，英文 ~4 char/token）。

    Plan 6 简化：用 tiktoken 的 cl100k_base 估算；fallback 到 len(text)//3。
    """
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 3


def _truncate_facts(facts: list[dict], budget: int) -> list[dict]:
    """从最新的 fact 倒序保留，直到累计 token 接近 budget。"""
    if not facts:
        return []
    out = []
    used = 0
    for f in reversed(facts):  # 最新优先（list 末尾是新的）
        ft = _estimate_tokens(str(f))
        if used + ft > budget:
            break
        out.append(f)
        used += ft
    return list(reversed(out))


class MemoryLoader:
    """对话开始时调；返完整 Memory 上下文给 PromptBuilder 用。"""

    def __init__(self, session: SessionMemory,
                 user: UserMemoryService,
                 customer: CustomerMemoryService,
                 product: ProductMemoryService):
        self.session = session
        self.user = user
        self.customer = customer
        self.product = product

    async def load(self, *, hub_user_id: int, conversation_id: str) -> Memory:
        """按 spec §3.3 组装：session 全量 + user 当前用户 + customers/products 仅 referenced。"""
        session = await self.session.load(conversation_id)
        user = await self.user.load(hub_user_id)

        customer_map = await self.customer.load_referenced(session.customer_ids)
        product_map = await self.product.load_referenced(session.product_ids)

        # 按 token budget 截断
        return Memory(
            session=session,  # session 暂不截断（消息层在 ContextBuilder Task 6 二次处理）
            user={
                "facts": _truncate_facts(user["facts"], USER_TOKEN_BUDGET),
                "preferences": user["preferences"],
            },
            customers={
                cid: {"facts": _truncate_facts(m["facts"], CUSTOMER_TOKEN_BUDGET)}
                for cid, m in customer_map.items()
            },
            products={
                pid: {"facts": _truncate_facts(m["facts"], PRODUCT_TOKEN_BUDGET)}
                for pid, m in product_map.items()
            },
        )
