"""Plan 6 Task 4：MemoryLoader 组装四层 + token 上限截断。"""
from __future__ import annotations

import logging

from hub.agent.memory.persistent import (
    CustomerMemoryService,
    ProductMemoryService,
    UserMemoryService,
)
from hub.agent.memory.session import SessionMemory
from hub.agent.memory.types import Memory

logger = logging.getLogger("hub.agent.memory.loader")


# token 上限（spec §3.3）
SESSION_TOKEN_BUDGET = 4000
USER_TOKEN_BUDGET = 1000
CUSTOMER_TOKEN_BUDGET = 500   # 单个客户
PRODUCT_TOKEN_BUDGET = 200    # 单个商品

# M1: 模块级 encoder 缓存，避免每次 import + get_encoding 开销
_ENCODER = None
_ENCODER_FAILED = False


def _get_encoder():
    """懒加载 + 缓存 tiktoken encoder；ImportError 后标记失败不重试。"""
    global _ENCODER, _ENCODER_FAILED
    if _ENCODER is not None or _ENCODER_FAILED:
        return _ENCODER
    try:
        import tiktoken  # noqa: PLC0415
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    except ImportError:
        _ENCODER_FAILED = True
        logger.warning("tiktoken 未安装，token 估算 fallback（中文偏低 3x）。建议 pip install tiktoken>=0.5")
    return _ENCODER


def _estimate_tokens(text: str) -> int:
    """估算 token 数。

    首选 tiktoken cl100k_base；fallback：CJK 按 1.5 char/token，ASCII 按 4 char/token
    （比原来的 len//3 更贴近中文实际）。
    """
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    # M1 优化 fallback：区分中文字符与 ASCII
    cjk_count = sum(1 for c in text if ord(c) >= 0x3000)
    ascii_count = len(text) - cjk_count
    return int(cjk_count / 1.5 + ascii_count / 4)


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
        """按 spec §3.3 组装：session 全量 + user 当前用户 + customers/products 仅 referenced。

        v8 review #19：session.load 必传 hub_user_id（per-user 隔离）。
        """
        session = await self.session.load(conversation_id, hub_user_id)
        user = await self.user.load(hub_user_id)

        customer_map = await self.customer.load_referenced(session.customer_ids)
        product_map = await self.product.load_referenced(session.product_ids)

        # 按 token budget 截断
        return Memory(
            # I5: Plan 6 Task 4 决策：session 全量传递；token 4K 上限的截断由 Task 6 ContextBuilder
            # 处理（按 round/messages 边界裁），这里只截 user/customer/product facts 列表。
            # spec §3.3 总预算 10K = 4K (session) + 1K (user) + 5×500 (customers) + 5×200 (products)
            # 仍然有效，session 那 4K 不在本层 enforce。
            session=session,
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
