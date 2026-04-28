"""查商品 + 客户历史价：先解客户，再解商品，最后取价格。"""
from __future__ import annotations

import logging

from hub import cards
from hub.adapters.downstream.erp4 import (
    ErpAdapterError,
    ErpPermissionError,
    ErpSystemError,
)
from hub.circuit_breaker import CircuitOpenError
from hub.error_codes import BizErrorCode, build_user_message
from hub.match.resolver import MatchOutcome, MatchResolver
from hub.ports import OutboundMessage

logger = logging.getLogger("hub.usecase.query_customer_history")


class QueryCustomerHistoryUseCase:
    def __init__(self, *, erp, pricing, sender, state, max_show: int = 5):
        self.erp = erp
        self.pricing = pricing
        self.sender = sender
        self.state = state
        self.max_show = max_show
        self.matcher = MatchResolver()

    async def execute(
        self, *, sku_or_keyword: str, customer_keyword: str,
        dingtalk_userid: str, acting_as: int,
    ) -> None:
        # 1. 解客户
        try:
            cust_resp = await self.erp.search_customers(
                query=customer_keyword, acting_as_user_id=acting_as,
            )
        except ErpPermissionError:
            await self._send(dingtalk_userid, build_user_message(BizErrorCode.PERM_DOWNSTREAM_DENIED))
            return
        except CircuitOpenError:
            await self._send(dingtalk_userid, build_user_message(BizErrorCode.ERP_CIRCUIT_OPEN))
            return
        except (ErpSystemError, ErpAdapterError):
            await self._send(dingtalk_userid, build_user_message(BizErrorCode.ERP_TIMEOUT))
            return

        cust_candidates = [
            {"id": c["id"], "label": c.get("name", str(c["id"])),
             "subtitle": f"客户编号 {c['id']}"}
            for c in cust_resp.get("items", [])
        ]
        cust_result = self.matcher.resolve(
            keyword=customer_keyword, resource="客户",
            candidates=cust_candidates, max_show=self.max_show,
        )

        if cust_result.outcome == MatchOutcome.NONE:
            await self._send(dingtalk_userid, build_user_message(
                BizErrorCode.MATCH_NOT_FOUND, keyword=customer_keyword, resource="客户",
            ))
            return

        if cust_result.outcome == MatchOutcome.MULTI:
            await self.state.save(dingtalk_userid, {
                "intent_type": "query_customer_history",
                "resource": "客户",
                "candidates": cust_result.choices,
                "sku_or_keyword": sku_or_keyword,
                "pending_choice": "yes",
            })
            card = cards.multi_match_select_card(
                keyword=customer_keyword, resource="客户", items=cust_result.choices,
            )
            await self._send_message(dingtalk_userid, card)
            return

        customer = cust_result.selected

        # 2. 解商品
        await self._resolve_product_and_render(
            customer=customer, sku_or_keyword=sku_or_keyword,
            dingtalk_userid=dingtalk_userid, acting_as=acting_as,
        )

    async def execute_selected_customer(
        self, *, customer: dict, sku_or_keyword: str,
        dingtalk_userid: str, acting_as: int,
    ) -> None:
        """客户多命中后选定一个 → 用 customer dict 直接进入第二步（解商品）。"""
        await self._resolve_product_and_render(
            customer=customer, sku_or_keyword=sku_or_keyword,
            dingtalk_userid=dingtalk_userid, acting_as=acting_as,
        )

    async def execute_selected_product(
        self, *, product: dict, customer_id: int, customer_name: str,
        dingtalk_userid: str, acting_as: int,
    ) -> None:
        """商品多命中后选定一个 + 客户已确定 → 直接渲染历史价。"""
        await self._render_history(
            product=product,
            customer={"id": customer_id, "label": customer_name},
            dingtalk_userid=dingtalk_userid, acting_as=acting_as,
        )

    async def _resolve_product_and_render(
        self, *, customer: dict, sku_or_keyword: str,
        dingtalk_userid: str, acting_as: int,
    ) -> None:
        try:
            prod_resp = await self.erp.search_products(
                query=sku_or_keyword, acting_as_user_id=acting_as,
            )
        except (ErpPermissionError, CircuitOpenError, ErpSystemError, ErpAdapterError) as e:
            code = (
                BizErrorCode.PERM_DOWNSTREAM_DENIED if isinstance(e, ErpPermissionError)
                else BizErrorCode.ERP_CIRCUIT_OPEN if isinstance(e, CircuitOpenError)
                else BizErrorCode.ERP_TIMEOUT
            )
            await self._send(dingtalk_userid, build_user_message(code))
            return

        prod_candidates = [
            {
                "id": p["id"], "sku": p.get("sku"),
                "name": p.get("name", str(p["id"])),
                "label": p.get("name", str(p["id"])),
                "subtitle": f"SKU {p.get('sku', '-')}",
                "retail_price": p.get("retail_price"),
                "stock": p.get("total_stock"),
            }
            for p in prod_resp.get("items", [])
        ]
        prod_result = self.matcher.resolve(
            keyword=sku_or_keyword, resource="商品",
            candidates=prod_candidates, max_show=self.max_show,
        )

        if prod_result.outcome == MatchOutcome.NONE:
            await self._send(dingtalk_userid, build_user_message(
                BizErrorCode.MATCH_NOT_FOUND, keyword=sku_or_keyword, resource="商品",
            ))
            return

        if prod_result.outcome == MatchOutcome.MULTI:
            await self.state.save(dingtalk_userid, {
                "intent_type": "query_customer_history",
                "resource": "商品",
                "candidates": prod_result.choices,
                "customer_id": customer["id"],
                "customer_name": customer.get("label") or customer.get("name", ""),
                "pending_choice": "yes",
            })
            card = cards.multi_match_select_card(
                keyword=sku_or_keyword, resource="商品", items=prod_result.choices,
            )
            await self._send_message(dingtalk_userid, card)
            return

        await self._render_history(
            product=prod_result.selected, customer=customer,
            dingtalk_userid=dingtalk_userid, acting_as=acting_as,
        )

    async def _render_history(
        self, *, product: dict, customer: dict,
        dingtalk_userid: str, acting_as: int,
    ) -> None:
        try:
            info = await self.pricing.get_price(
                product_id=product["id"], customer_id=customer["id"], acting_as=acting_as,
                fallback_retail_price=product.get("retail_price"),
            )
        except ErpPermissionError:
            await self._send(dingtalk_userid, build_user_message(BizErrorCode.PERM_NO_CUSTOMER_HISTORY))
            return

        try:
            history_resp = await self.erp.get_product_customer_prices(
                product_id=product["id"], customer_id=customer["id"], limit=5,
                acting_as_user_id=acting_as,
            )
            history = history_resp.get("records", [])
        except ErpPermissionError:
            await self._send(dingtalk_userid, build_user_message(BizErrorCode.PERM_NO_CUSTOMER_HISTORY))
            return
        except (ErpSystemError, ErpAdapterError):
            history = []

        card = cards.product_with_customer_history_card(
            product=product,
            customer={
                "id": customer["id"],
                "name": customer.get("name") or customer.get("label", ""),
            },
            history=history, retail_price=info.unit_price,
        )
        await self._send_message(dingtalk_userid, card)

    async def _send(self, userid: str, text: str) -> None:
        try:
            await self.sender.send_text(dingtalk_userid=userid, text=text)
        except Exception:
            logger.exception(f"send_text 失败 userid={userid}")

    async def _send_message(self, userid: str, msg: OutboundMessage) -> None:
        try:
            if msg.type.value == "text":
                await self.sender.send_text(dingtalk_userid=userid, text=msg.text or "")
            elif msg.type.value == "markdown":
                await self.sender.send_markdown(
                    dingtalk_userid=userid, title="HUB", markdown=msg.markdown or "",
                )
            else:
                await self.sender.send_action_card(
                    dingtalk_userid=userid, actioncard=msg.actioncard or {},
                )
        except Exception:
            logger.exception(f"send 失败 userid={userid}")
