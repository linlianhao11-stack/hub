"""RuleParser：正则匹配常见命令模式。

命中模式：
- 查 <SKU或商品关键字> [给 <客户关键字>] [报价/价格/多少钱]?
- 纯数字（仅在 context["pending_choice"] 时识别为编号选择）
- "是" / "确认" / "yes"（仅在 context["pending_confirm"] 时识别为确认）

不命中 → intent_type="unknown" + confidence=0
"""
from __future__ import annotations

import re

from hub.ports import ParsedIntent

# 查 <sku> [给 <customer>] [价格词]
RE_QUERY = re.compile(
    r"^/?查\s*(?P<sku>\S+?)(?:\s*给\s*(?P<customer>\S+?))?\s*(?:报价|价格|多少钱|几钱)?\s*$",
)
RE_NUMBER = re.compile(r"^\s*(\d{1,3})\s*$")
RE_CONFIRM = re.compile(r"^\s*(是|确认|yes|y)\s*$", re.IGNORECASE)


class RuleParser:
    parser_name = "rule"

    async def parse(self, text: str, context: dict) -> ParsedIntent:
        # 编号选择
        if context.get("pending_choice"):
            m = RE_NUMBER.match(text)
            if m:
                return ParsedIntent(
                    intent_type="select_choice",
                    fields={"choice": int(m.group(1))},
                    confidence=0.95, parser=self.parser_name,
                )

        # 低置信度后的确认
        if context.get("pending_confirm") and RE_CONFIRM.match(text):
            return ParsedIntent(
                intent_type="confirm_yes",
                fields={}, confidence=0.95, parser=self.parser_name,
            )

        m = RE_QUERY.match(text)
        if m:
            sku = m.group("sku")
            customer = m.group("customer")
            if customer:
                return ParsedIntent(
                    intent_type="query_customer_history",
                    fields={"sku_or_keyword": sku, "customer_keyword": customer},
                    confidence=0.95, parser=self.parser_name,
                )
            return ParsedIntent(
                intent_type="query_product",
                fields={"sku_or_keyword": sku, "customer_keyword": None},
                confidence=0.95, parser=self.parser_name,
            )

        return ParsedIntent(
            intent_type="unknown", fields={}, confidence=0.0, parser=self.parser_name,
        )
