"""Plan 6 Task 4：Memory 类型定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EntityRefs:
    """从 tool result 提取的实体 ID 引用集合。
    I1: 从 tools/entity_extractor.py 移到此处，消除 memory → tools 的向上依赖。
    """
    customer_ids: set[int] = field(default_factory=set)
    product_ids: set[int] = field(default_factory=set)

    def has_any(self) -> bool:
        return bool(self.customer_ids or self.product_ids)


@dataclass
class ConversationMessage:
    """单条对话消息。"""
    role: str  # "user" / "assistant" / "tool"
    content: str
    tool_call_id: str | None = None  # role=tool 时引用的 tool_call


@dataclass
class ConversationHistory:
    """会话历史 + referenced_entities。"""
    conversation_id: str
    messages: list[ConversationMessage] = field(default_factory=list)
    customer_ids: set[int] = field(default_factory=set)
    product_ids: set[int] = field(default_factory=set)


@dataclass
class Memory:
    """组装好的完整 memory 上下文，供 PromptBuilder 注入。"""
    session: ConversationHistory
    user: dict[str, Any]  # {"facts": [...], "preferences": {...}}
    customers: dict[int, dict[str, Any]]  # {customer_id: {"facts": [...]}}
    products: dict[int, dict[str, Any]]   # {product_id: {"facts": [...]}}
