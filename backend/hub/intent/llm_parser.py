"""LLMParser：用 AICapability schema-guided 解析。"""
from __future__ import annotations

import logging

from hub.capabilities.deepseek import LLMParseError, LLMServiceError
from hub.ports import ParsedIntent

logger = logging.getLogger("hub.intent.llm")


_INTENT_SCHEMA = {
    "intent_type": "query_product | query_customer_history | unknown",
    "fields": {
        "sku_or_keyword": "string (商品 SKU 或关键字)",
        "customer_keyword": "string | null (客户关键字)",
    },
    "confidence": "float 0.0~1.0",
}


# 每种 intent_type 的必填字段。LLM 返回缺失任一必填 → 降级 unknown 防 handler KeyError。
_REQUIRED_FIELDS = {
    "query_product": ["sku_or_keyword"],
    "query_customer_history": ["sku_or_keyword", "customer_keyword"],
}


def _unknown(parser_name: str) -> ParsedIntent:
    return ParsedIntent(
        intent_type="unknown", fields={}, confidence=0.0, parser=parser_name,
    )


class LLMParser:
    parser_name = "llm"

    def __init__(self, ai):
        self.ai = ai  # CapabilityProvider 实现 (可为 None)

    async def parse(self, text: str, context: dict) -> ParsedIntent:
        if self.ai is None:
            return _unknown(self.parser_name)
        try:
            raw = await self.ai.parse_intent(text, _INTENT_SCHEMA)
        except (LLMServiceError, LLMParseError) as e:
            logger.warning(f"LLM 解析降级: {e}")
            return _unknown(self.parser_name)
        except Exception:
            logger.exception("LLM 调用异常")
            return _unknown(self.parser_name)

        intent_type = str(raw.get("intent_type", "unknown"))
        fields = raw.get("fields") or {}
        if not isinstance(fields, dict):
            logger.warning(f"LLM 返回 fields 非 dict: {type(fields)}")
            return _unknown(self.parser_name)

        # **schema 必填字段校验**：缺任一必填 → 降级 unknown，避免下游 KeyError
        required = _REQUIRED_FIELDS.get(intent_type)
        if required is not None:
            for f in required:
                value = fields.get(f)
                if not value:  # None / "" / 0 都视为缺失
                    logger.warning(
                        f"LLM intent_type={intent_type} 缺必填字段 {f}，降级 unknown",
                    )
                    return _unknown(self.parser_name)

        # confidence 字段防御：LLM 可能返回 None / "high" / 非数字 → 降级 0.0
        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            logger.warning(f"LLM 返回 confidence 非数字: {raw.get('confidence')!r}，降级 0.0")
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        return ParsedIntent(
            intent_type=intent_type,
            fields=dict(fields),
            confidence=confidence,
            parser=self.parser_name,
        )
