from decimal import Decimal
import pytest
from hub.agent.graph.state import (
    AgentState, Intent, ContractState, ContractItem,
    CustomerInfo, ProductInfo, ShippingInfo,
)


def test_agent_state_minimal():
    state = AgentState(
        user_message="hi",
        hub_user_id=1,
        conversation_id="c1",
    )
    assert state.intent is None
    assert state.acting_as is None


def test_intent_lowercase_value():
    assert Intent.CHAT.value == "chat"
    assert Intent.CONTRACT.value == "contract"
    # 必须是 lowercase value，否则 router_node 解析会全部落 UNKNOWN
    assert all(i.value == i.value.lower() for i in Intent)


def test_contract_state_with_items():
    state = ContractState(
        user_message="给阿里做合同 X1 10 个 300",
        hub_user_id=1,
        conversation_id="c1",
        extracted_hints={"customer_name": "阿里"},
    )
    state.customer = CustomerInfo(id=10, name="阿里")
    state.items.append(ContractItem(product_id=1, name="X1", qty=10, price=Decimal("300")))
    assert state.customer.name == "阿里"
    assert state.items[0].price == Decimal("300")


def test_shipping_info_all_optional():
    s = ShippingInfo()
    assert s.address is None and s.contact is None and s.phone is None
