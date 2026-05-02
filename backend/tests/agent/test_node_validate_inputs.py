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
