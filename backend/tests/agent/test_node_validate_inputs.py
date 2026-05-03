import pytest
from unittest.mock import AsyncMock
from decimal import Decimal
from hub.agent.graph.state import ContractState, ContractItem, CustomerInfo, ProductInfo
from hub.agent.graph.nodes.validate_inputs import validate_inputs_node


@pytest.mark.asyncio
async def test_validate_inputs_thinking_enabled():
    """validate_inputs 必须 thinking enabled — spec §1.5 表。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text":
        '{"missing_fields": [], "warnings": []}', "finish_reason": "stop", "tool_calls": []})())
    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=1, name="阿里")
    state.products = [ProductInfo(id=1, name="X1")]
    state.items = [ContractItem(product_id=1, name="X1", qty=10, price=Decimal("300"))]
    state.shipping.address = "北京海淀"
    out = await validate_inputs_node(state, llm=llm)
    kw = llm.chat.await_args.kwargs
    assert kw["thinking"] == {"type": "enabled"}
    assert out.missing_fields == []


@pytest.mark.asyncio
async def test_validate_inputs_detects_missing_address():
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text":
        '{"missing_fields": ["shipping_address"], "warnings": []}', "finish_reason": "stop",
        "tool_calls": []})())
    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=1, name="阿里")
    state.products = [ProductInfo(id=1, name="X1")]
    state.items = [ContractItem(product_id=1, name="X1", qty=10, price=Decimal("300"))]
    out = await validate_inputs_node(state, llm=llm)
    assert "shipping_address" in out.missing_fields


@pytest.mark.asyncio
async def test_validate_inputs_filters_non_whitelist_field_names(caplog):
    """钉钉实测 hotfix（task=fyFOm_hd 12:53）：LLM 自由发挥输出非约束字段名
    （如 `customer_address` / `customer_phone`，这是合同 docx 模板占位符），
    用户钉钉看到英文代号违反"中文大白话"原则。validate_inputs 必须按白名单
    过滤 LLM 输出，丢弃非合法字段名（同时 warning log 留痕）。
    """
    import logging
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text":
        '{"missing_fields": ["shipping_address", "customer_address", "customer_phone", "garbage_field"], '
        '"warnings": []}',
        "finish_reason": "stop", "tool_calls": []})())
    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=1, name="阿里")
    state.products = [ProductInfo(id=1, name="X1")]
    state.items = [ContractItem(product_id=1, name="X1", qty=10, price=Decimal("300"))]

    with caplog.at_level(logging.WARNING):
        out = await validate_inputs_node(state, llm=llm)

    # 合法字段保留
    assert out.missing_fields == ["shipping_address"]
    # 非白名单字段必须被丢弃（不让用户看到 customer_address / customer_phone / garbage_field）
    assert "customer_address" not in out.missing_fields
    assert "customer_phone" not in out.missing_fields
    assert "garbage_field" not in out.missing_fields
    # 必须有 warning log 留痕
    assert any(
        "非白名单 missing_fields" in r.message and "customer_address" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_validate_inputs_dynamic_prefixes_pass_filter():
    """item_qty:HINT / item_price:HINT / product_choice:HINT / customer_choice:NAME /
    product_not_found:HINT 这些动态前缀字段都是合法的，不应被白名单过滤掉。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text":
        '{"missing_fields": ["item_qty:F1", "item_price:H5", "product_not_found:XYZ", '
        '"product_choice:K5", "customer_choice:阿里"], "warnings": []}',
        "finish_reason": "stop", "tool_calls": []})())
    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    out = await validate_inputs_node(state, llm=llm)
    assert out.missing_fields == [
        "item_qty:F1", "item_price:H5", "product_not_found:XYZ",
        "product_choice:K5", "customer_choice:阿里",
    ]
