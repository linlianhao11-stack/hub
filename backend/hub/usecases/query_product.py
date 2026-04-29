"""查商品（无客户场景）：模糊匹配 → 唯一/多/无 → 渲染 + 回钉钉。"""
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

logger = logging.getLogger("hub.usecase.query_product")


class QueryProductUseCase:
    def __init__(self, *, erp, pricing, sender, state, max_show: int = 5):
        self.erp = erp
        self.pricing = pricing
        self.sender = sender
        self.state = state
        self.max_show = max_show
        self.matcher = MatchResolver()

    async def execute(
        self, *, sku_or_keyword: str, dingtalk_userid: str, acting_as: int,
    ) -> None:
        """模糊搜索入口：调 ERP 搜商品 → 唯一/多/无 → 渲染。"""
        try:
            resp = await self.erp.search_products(
                query=sku_or_keyword, acting_as_user_id=acting_as,
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

        candidates = [
            {
                "id": p["id"],
                "sku": p.get("sku"),
                "name": p.get("name", str(p["id"])),
                "label": p.get("name", str(p["id"])),
                "subtitle": f"SKU {p.get('sku', '-')}",
                "retail_price": p.get("retail_price"),
                "stock": p.get("total_stock"),
            }
            for p in resp.get("items", [])
        ]
        result = self.matcher.resolve(
            keyword=sku_or_keyword, resource="商品", candidates=candidates, max_show=self.max_show,
        )

        if result.outcome == MatchOutcome.NONE:
            msg = build_user_message(BizErrorCode.MATCH_NOT_FOUND, keyword=sku_or_keyword, resource="商品")
            await self._send(dingtalk_userid, msg)
            return

        if result.outcome == MatchOutcome.MULTI:
            await self.state.save(dingtalk_userid, {
                "intent_type": "query_product",
                "resource": "商品",
                "candidates": result.choices,
                "pending_choice": "yes",
            })
            card = cards.multi_match_select_card(
                keyword=sku_or_keyword, resource="商品", items=result.choices,
            )
            await self._send_message(dingtalk_userid, card)
            return

        prod = result.selected
        await self._render_unique(prod, dingtalk_userid, acting_as)

    async def execute_selected(
        self, *, product: dict, dingtalk_userid: str, acting_as: int,
    ) -> None:
        """编号选择后直接用候选项渲染，**不再二次模糊搜索**。"""
        await self._render_unique(product, dingtalk_userid, acting_as)

    async def _render_unique(
        self, prod: dict, dingtalk_userid: str, acting_as: int,
    ) -> None:
        info = await self.pricing.get_price(
            product_id=prod["id"], customer_id=None, acting_as=acting_as,
            fallback_retail_price=prod.get("retail_price"),
        )
        card = cards.product_simple_card(prod, retail_price=info.unit_price)
        await self._send_message(dingtalk_userid, card)

    async def _send(self, userid: str, text: str) -> None:
        """发送失败让异常上抛，由 WorkerRuntime 转入死信流，不静默 ACK。

        与 dingtalk_inbound._send_text 行为对齐——早期版本 try/except 吞异常会让
        钉钉短暂故障时用户收不到回复但任务被 ACK 掉，问题被掩盖。
        """
        await self.sender.send_text(dingtalk_userid=userid, text=text)

    async def _send_message(self, userid: str, msg: OutboundMessage) -> None:
        """同 _send：sender 异常向上抛，不吞掉。"""
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
