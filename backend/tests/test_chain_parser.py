from unittest.mock import AsyncMock

import pytest

from hub.ports import ParsedIntent


@pytest.mark.asyncio
async def test_rule_hit_skips_llm():
    from hub.intent.chain_parser import ChainParser

    rule = AsyncMock()
    rule.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_product", fields={"sku_or_keyword": "SKU100", "customer_keyword": None},
        confidence=0.95, parser="rule",
    ))
    llm = AsyncMock()

    chain = ChainParser(rule=rule, llm=llm, low_confidence_threshold=0.7)
    intent = await chain.parse("查 SKU100", context={})
    assert intent.parser == "rule"
    llm.parse.assert_not_called()


@pytest.mark.asyncio
async def test_rule_miss_falls_through_to_llm():
    from hub.intent.chain_parser import ChainParser

    rule = AsyncMock()
    rule.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="unknown", fields={}, confidence=0.0, parser="rule",
    ))
    llm = AsyncMock()
    llm.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_product", fields={"sku_or_keyword": "X"},
        confidence=0.85, parser="llm",
    ))

    chain = ChainParser(rule=rule, llm=llm, low_confidence_threshold=0.7)
    intent = await chain.parse("帮我看下 X 多少钱", context={})
    assert intent.parser == "llm"


@pytest.mark.asyncio
async def test_llm_low_confidence_marked_pending_confirm():
    """LLM 返回 confidence < 阈值 → 保留意图但 needs_confirm=True。"""
    from hub.intent.chain_parser import ChainParser

    rule = AsyncMock()
    rule.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="unknown", fields={}, confidence=0.0, parser="rule",
    ))
    llm = AsyncMock()
    llm.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_product", fields={"sku_or_keyword": "X"},
        confidence=0.5, parser="llm",
    ))

    chain = ChainParser(rule=rule, llm=llm, low_confidence_threshold=0.7)
    intent = await chain.parse("xxx", context={})
    assert intent.parser == "llm"
    assert intent.confidence == 0.5
    assert intent.notes == "low_confidence"


@pytest.mark.asyncio
async def test_llm_returns_unknown_passes_through():
    from hub.intent.chain_parser import ChainParser

    rule = AsyncMock()
    rule.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="unknown", fields={}, confidence=0.0, parser="rule",
    ))
    llm = AsyncMock()
    llm.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="unknown", fields={}, confidence=0.0, parser="llm",
    ))

    chain = ChainParser(rule=rule, llm=llm, low_confidence_threshold=0.7)
    intent = await chain.parse("???", context={})
    assert intent.intent_type == "unknown"
