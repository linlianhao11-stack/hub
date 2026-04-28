import pytest


@pytest.mark.asyncio
async def test_query_product_simple():
    from hub.intent.rule_parser import RuleParser
    p = RuleParser()
    intent = await p.parse("查 SKU100", context={})
    assert intent.intent_type == "query_product"
    assert intent.fields["sku_or_keyword"] == "SKU100"
    assert intent.fields.get("customer_keyword") is None
    assert intent.confidence >= 0.9
    assert intent.parser == "rule"


@pytest.mark.asyncio
async def test_query_product_with_price_word():
    from hub.intent.rule_parser import RuleParser
    p = RuleParser()
    intent = await p.parse("查 SKU100 多少钱", context={})
    assert intent.intent_type == "query_product"


@pytest.mark.asyncio
async def test_query_customer_history():
    from hub.intent.rule_parser import RuleParser
    p = RuleParser()
    intent = await p.parse("查 SKU100 给阿里", context={})
    assert intent.intent_type == "query_customer_history"
    assert intent.fields["sku_or_keyword"] == "SKU100"
    assert intent.fields["customer_keyword"] == "阿里"


@pytest.mark.asyncio
async def test_select_number_in_pending_choice_context():
    """有待选编号上下文时，纯数字输入被识别为编号选择。"""
    from hub.intent.rule_parser import RuleParser
    p = RuleParser()
    intent = await p.parse("2", context={"pending_choice": "yes"})
    assert intent.intent_type == "select_choice"
    assert intent.fields["choice"] == 2


@pytest.mark.asyncio
async def test_no_match_returns_none_intent():
    from hub.intent.rule_parser import RuleParser
    p = RuleParser()
    intent = await p.parse("今天天气怎么样", context={})
    assert intent.intent_type == "unknown"
    assert intent.confidence == 0.0


@pytest.mark.asyncio
async def test_confirm_yes_in_pending_confirm_context():
    """低置信度后用户回复"是"被识别为确认。"""
    from hub.intent.rule_parser import RuleParser
    p = RuleParser()
    intent = await p.parse("是", context={"pending_confirm": "yes"})
    assert intent.intent_type == "confirm_yes"
