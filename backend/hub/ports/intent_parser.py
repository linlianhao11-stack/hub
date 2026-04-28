"""IntentParser Protocol：意图解析。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ParsedIntent:
    intent_type: str  # query_product / query_customer_history / generate_contract / ...
    fields: dict = field(default_factory=dict)
    confidence: float = 0.0  # 0.0 ~ 1.0
    parser: str = "unknown"  # rule / llm
    notes: str | None = None


class IntentParser(Protocol):
    async def parse(self, text: str, context: dict) -> ParsedIntent: ...
