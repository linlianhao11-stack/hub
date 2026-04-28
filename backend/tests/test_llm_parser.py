from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_llm_parser_calls_capability():
    from hub.intent.llm_parser import LLMParser

    cap = AsyncMock()
    cap.parse_intent = AsyncMock(return_value={
        "intent_type": "query_product",
        "fields": {"sku_or_keyword": "SKU100", "customer_keyword": "阿里"},
        "confidence": 0.85,
    })

    p = LLMParser(ai=cap)
    intent = await p.parse("帮我看下阿里那个 SKU100 多少钱", context={})
    assert intent.intent_type == "query_product"
    assert intent.fields["sku_or_keyword"] == "SKU100"
    assert intent.confidence == 0.85
    assert intent.parser == "llm"


@pytest.mark.asyncio
async def test_llm_parser_returns_unknown_on_service_error():
    """LLM 服务异常 → 降级 unknown，不向上抛。"""
    from hub.capabilities.deepseek import LLMServiceError
    from hub.intent.llm_parser import LLMParser

    cap = AsyncMock()
    cap.parse_intent = AsyncMock(side_effect=LLMServiceError("503"))
    p = LLMParser(ai=cap)
    intent = await p.parse("xxx", context={})
    assert intent.intent_type == "unknown"
    assert intent.confidence == 0.0


@pytest.mark.asyncio
async def test_llm_parser_returns_unknown_on_parse_error():
    from hub.capabilities.deepseek import LLMParseError
    from hub.intent.llm_parser import LLMParser

    cap = AsyncMock()
    cap.parse_intent = AsyncMock(side_effect=LLMParseError("not json"))
    p = LLMParser(ai=cap)
    intent = await p.parse("xxx", context={})
    assert intent.intent_type == "unknown"


@pytest.mark.asyncio
async def test_llm_parser_clamps_invalid_confidence():
    """LLM 返回 confidence > 1 或 < 0 时 clamp。"""
    from hub.intent.llm_parser import LLMParser
    cap = AsyncMock()
    cap.parse_intent = AsyncMock(return_value={
        "intent_type": "query_product",
        "fields": {"sku_or_keyword": "X"}, "confidence": 1.5,
    })
    p = LLMParser(ai=cap)
    intent = await p.parse("x", context={})
    assert 0.0 <= intent.confidence <= 1.0


@pytest.mark.asyncio
async def test_llm_parser_no_capability_returns_unknown():
    """ai=None（未配置 AI 提供商）时直接返回 unknown，不报错。"""
    from hub.intent.llm_parser import LLMParser
    p = LLMParser(ai=None)
    intent = await p.parse("x", context={})
    assert intent.intent_type == "unknown"


@pytest.mark.asyncio
async def test_llm_parser_missing_required_fields_falls_back_to_unknown():
    """LLM 返回 intent_type=query_product 但缺 sku_or_keyword → 降级 unknown。"""
    from hub.intent.llm_parser import LLMParser
    cap = AsyncMock()
    cap.parse_intent = AsyncMock(return_value={
        "intent_type": "query_product", "fields": {}, "confidence": 0.9,
    })
    p = LLMParser(ai=cap)
    intent = await p.parse("x", context={})
    assert intent.intent_type == "unknown"
    assert intent.confidence == 0.0


@pytest.mark.asyncio
async def test_llm_parser_query_customer_history_missing_customer_keyword():
    """query_customer_history 缺 customer_keyword → 降级。"""
    from hub.intent.llm_parser import LLMParser
    cap = AsyncMock()
    cap.parse_intent = AsyncMock(return_value={
        "intent_type": "query_customer_history",
        "fields": {"sku_or_keyword": "SKU100"},  # 缺 customer_keyword
        "confidence": 0.9,
    })
    p = LLMParser(ai=cap)
    intent = await p.parse("x", context={})
    assert intent.intent_type == "unknown"


@pytest.mark.asyncio
async def test_llm_parser_fields_not_dict_falls_back():
    """LLM 返回 fields 不是 dict → 降级。"""
    from hub.intent.llm_parser import LLMParser
    cap = AsyncMock()
    cap.parse_intent = AsyncMock(return_value={
        "intent_type": "query_product", "fields": "not a dict", "confidence": 0.9,
    })
    p = LLMParser(ai=cap)
    intent = await p.parse("x", context={})
    assert intent.intent_type == "unknown"


@pytest.mark.asyncio
async def test_llm_parser_confidence_non_numeric_falls_back_to_zero():
    """LLM 返回 confidence 非数字（None / "high" / 字符串）→ 不抛 TypeError，降级 0.0。"""
    from hub.intent.llm_parser import LLMParser

    for bad_conf in [None, "high", "0.8x", [], {"x": 1}]:
        cap = AsyncMock()
        cap.parse_intent = AsyncMock(return_value={
            "intent_type": "query_product",
            "fields": {"sku_or_keyword": "X"},
            "confidence": bad_conf,
        })
        p = LLMParser(ai=cap)
        intent = await p.parse("x", context={})
        assert intent.confidence == 0.0
        assert intent.intent_type in ("query_product", "unknown")
