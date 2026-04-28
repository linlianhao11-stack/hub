"""DefaultPricingStrategy：客户历史价 → 零售价 fallback。

零售价 fallback 策略（按优先级）：
1. 调用方传入的 `fallback_retail_price`（候选项已含，最快）
2. `erp.get_product(product_id)` 精确反查
3. 都失败 → unit_price="0" + notes 标记

权限错误（ErpPermissionError）**向上抛**——上游翻译为 PERM_NO_CUSTOMER_HISTORY
（不能默默降级成零售价隐藏权限问题）。仅 ErpSystemError / 网络超时降级。
"""
from __future__ import annotations

import logging

from hub.adapters.downstream.erp4 import ErpPermissionError, ErpSystemError
from hub.ports import PriceInfo

logger = logging.getLogger("hub.strategy.pricing")


class DefaultPricingStrategy:
    def __init__(self, erp_adapter):
        self.erp = erp_adapter

    async def get_price(
        self, product_id: int, customer_id: int | None, *, acting_as: int,
        fallback_retail_price: str | None = None,
    ) -> PriceInfo:
        notes = None

        if customer_id is not None:
            try:
                resp = await self.erp.get_product_customer_prices(
                    product_id=product_id, customer_id=customer_id, limit=1,
                    acting_as_user_id=acting_as,
                )
                records = resp.get("records", [])
                if records:
                    return PriceInfo(
                        unit_price=str(records[0]["unit_price"]),
                        source="customer_history",
                        customer_id=customer_id,
                        notes=None,
                    )
            except ErpPermissionError:
                # 权限不足必须向上抛，不能降级隐藏；上游翻译为 PERM_NO_CUSTOMER_HISTORY
                raise
            except ErpSystemError as e:
                logger.warning(f"历史价查询系统错误，降级零售价: {e}")
                notes = "历史价查询暂不可用"

        # fallback 1：调用方传入（业务用例已经从模糊搜索结果拿到）
        if fallback_retail_price is not None:
            return PriceInfo(
                unit_price=str(fallback_retail_price),
                source="retail", customer_id=customer_id, notes=notes,
            )

        # fallback 2：用 ID 精确反查商品（不依赖 keyword 搜索）
        try:
            prod = await self.erp.get_product(
                product_id=product_id, acting_as_user_id=acting_as,
            )
            if prod and prod.get("retail_price") is not None:
                return PriceInfo(
                    unit_price=str(prod["retail_price"]),
                    source="retail", customer_id=customer_id, notes=notes,
                )
        except Exception as e:
            logger.warning(f"商品详情查询失败: {e}")

        return PriceInfo(
            unit_price="0", source="fallback_default",
            customer_id=customer_id, notes=notes or "价格暂不可用",
        )
