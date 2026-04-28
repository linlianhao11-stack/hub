"""ChainParser：rule → llm 链；低置信度标记 needs_confirm。"""
from __future__ import annotations

from hub.ports import ParsedIntent


class ChainParser:
    parser_name = "chain"

    def __init__(self, rule, llm, *, low_confidence_threshold: float = 0.7):
        self.rule = rule
        self.llm = llm
        self.threshold = low_confidence_threshold

    async def parse(self, text: str, context: dict) -> ParsedIntent:
        rule_intent = await self.rule.parse(text, context)
        if rule_intent.intent_type != "unknown" and rule_intent.confidence >= self.threshold:
            return rule_intent

        llm_intent = await self.llm.parse(text, context)
        if llm_intent.intent_type == "unknown":
            return llm_intent

        # 低置信度标记，由上层渲染确认卡片
        if llm_intent.confidence < self.threshold:
            return ParsedIntent(
                intent_type=llm_intent.intent_type,
                fields=llm_intent.fields,
                confidence=llm_intent.confidence,
                parser=llm_intent.parser,
                notes="low_confidence",
            )
        return llm_intent
