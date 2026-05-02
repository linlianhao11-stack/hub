import pytest
from hub.agent.graph.state import ContractState, CustomerInfo, ProductInfo
from hub.agent.graph.nodes.ask_user import ask_user_node


@pytest.mark.asyncio
async def test_ask_user_lists_candidate_customers():
    """多候选客户：列编号 + id + 名称。"""
    state = ContractState(user_message="给阿里做合同", hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=11, name="阿里云"),
    ]
    state.missing_fields = ["customer_choice"]
    out = await ask_user_node(state)
    assert "阿里巴巴" in out.final_response
    assert "id=10" in out.final_response
    assert "id=11" in out.final_response
    assert "阿里云" in out.final_response


@pytest.mark.asyncio
async def test_ask_user_lists_candidate_products_single_group():
    """单组多候选产品：列编号 + id + 名称（含 spec / color）。"""
    state = ContractState(user_message="X1 10 个 300", hub_user_id=1, conversation_id="c1")
    state.candidate_products = {
        "X1": [
            ProductInfo(id=1, name="X1", color="黑", spec="5KG"),
            ProductInfo(id=2, name="X1", color="白", spec="10KG"),
        ],
    }
    state.missing_fields = ["product_choice:X1"]
    out = await ask_user_node(state)
    assert "X1" in out.final_response
    assert "id=1" in out.final_response and "id=2" in out.final_response
    assert "黑" in out.final_response and "白" in out.final_response


@pytest.mark.asyncio
async def test_ask_user_multi_group_products_requires_id():
    """多组候选：必须提示用 id= 精确选（避免裸编号"选 2"被套到所有组）。"""
    state = ContractState(user_message="H5 10 F1 5", hub_user_id=1, conversation_id="c1")
    state.candidate_products = {
        "H5": [ProductInfo(id=10, name="H5"), ProductInfo(id=11, name="H5")],
        "F1": [ProductInfo(id=20, name="F1"), ProductInfo(id=21, name="F1")],
    }
    state.missing_fields = ["product_choice:H5", "product_choice:F1"]
    out = await ask_user_node(state)
    assert "id=" in out.final_response
    assert "id=10" in out.final_response  # 提示中用第一组第一个 id 作样例


@pytest.mark.asyncio
async def test_ask_user_missing_shipping_fields():
    """缺地址 / 联系人 / 电话：用中文标签。"""
    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.missing_fields = ["shipping_address", "contact", "phone"]
    out = await ask_user_node(state)
    assert "收货地址" in out.final_response
    assert "联系人" in out.final_response
    assert "电话" in out.final_response


@pytest.mark.asyncio
async def test_ask_user_missing_item_qty_with_product_name():
    """item_qty:H5 → '产品「H5」的数量'。"""
    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.missing_fields = ["item_qty:H5", "item_price:F1"]
    out = await ask_user_node(state)
    assert "「H5」的数量" in out.final_response
    assert "「F1」的单价" in out.final_response


@pytest.mark.asyncio
async def test_ask_user_no_missing_fields_fallback():
    """都齐全时返兜底文案。"""
    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    out = await ask_user_node(state)
    assert "请补充信息后再试。" in out.final_response
