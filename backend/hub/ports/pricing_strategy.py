"""PricingStrategy Protocol：价格策略（fallback 链可插拔）。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


@dataclass
class PriceInfo:
    unit_price: str  # Decimal as str（避免 float 精度丢失）
    source: str  # retail / customer_history / customer_special / fallback_default
    customer_id: int | None = None
    notes: str | None = None


class PricingStrategy(Protocol):
    async def get_price(
        self, product_id: int, customer_id: int | None, *, acting_as: int,
    ) -> PriceInfo: ...
