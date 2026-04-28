"""CapabilityProvider Protocol：通用能力（AI / OCR / SMS 等）。"""
from __future__ import annotations
from typing import Protocol


class CapabilityProvider(Protocol):
    capability_type: str


class AICapability(CapabilityProvider):
    """AI 能力（聊天 + 意图解析）。"""

    async def parse_intent(self, text: str, schema: dict) -> dict:
        """根据 schema 把自然语言解析成结构化字段。"""
        ...

    async def chat(self, messages: list[dict], **kwargs) -> str:
        """通用对话（system / user / assistant 消息列表）。"""
        ...
