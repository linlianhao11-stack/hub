"""Session 短期记忆占位。完整实现在 Task 4。
ToolRegistry 测试 mock 这个接口；EntityRefs 写入路径需要这层。"""
from __future__ import annotations
from typing import Protocol


class SessionMemory(Protocol):
    """Session 短期记忆接口（Task 4 完整实现）。"""

    async def add_entity_refs(self, conversation_id: str, *,
                              customer_ids: set[int] | None = None,
                              product_ids: set[int] | None = None) -> None: ...

    async def get_entity_refs(self, conversation_id: str): ...
