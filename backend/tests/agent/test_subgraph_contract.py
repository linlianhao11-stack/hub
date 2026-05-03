# backend/tests/agent/test_subgraph_contract.py
import pytest
from hub.agent.graph.state import ContractState


@pytest.mark.asyncio
async def test_contract_subgraph_no_check_inventory_tool():
    """物理保证：contract 子图的 tools 列表不含 check_inventory。"""
    from hub.agent.tools.registry import ToolRegistry
    from hub.agent.tools import register_all_tools
    reg = ToolRegistry()
    register_all_tools(reg)
    schemas = reg.schemas_for_subgraph("contract")
    names = {s["function"]["name"] for s in schemas}
    assert "check_inventory" not in names, f"contract 不应挂 check_inventory：{names}"


@pytest.mark.asyncio
async def test_contract_subgraph_node_set_includes_parse_items():
    """验证 contract 子图节点集合 — 必须含 parse_contract_items（P1-A）。"""
    from hub.agent.graph.subgraphs.contract import build_contract_subgraph
    from unittest.mock import AsyncMock
    compiled = build_contract_subgraph(llm=AsyncMock(), tool_executor=AsyncMock())
    nodes = set(compiled.get_graph().nodes)
    expected = {
        "resolve_customer", "resolve_products", "parse_contract_items",
        "validate_inputs", "ask_user", "generate_contract", "format_response",
    }
    assert expected <= nodes, f"缺节点：{expected - nodes}"


@pytest.mark.asyncio
async def test_generate_contract_keeps_state_for_format_response(monkeypatch):
    """P1 v1.11：generate_contract_node 不清状态 — format_response 还要用 state.customer.name / len(state.items)
    写回执。清状态由 cleanup_after_contract_node 在 format_response 之后做。"""
    from hub.agent.graph.subgraphs import contract as contract_mod
    from hub.agent.graph.subgraphs.contract import generate_contract_node
    from hub.agent.graph.state import ContractState, CustomerInfo, ProductInfo, ContractItem
    from decimal import Decimal
    from unittest.mock import AsyncMock

    # mock 模板查询：避免依赖 DB
    async def _fake_resolve_template():
        return 1
    monkeypatch.setattr(contract_mod, "_resolve_default_template_id", _fake_resolve_template)

    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=10, name="阿里")
    state.products = [ProductInfo(id=1, name="X1")]
    state.items = [ContractItem(product_id=1, name="X1", qty=10, price=Decimal("300"))]
    state.shipping.address = "北京海淀"

    async def fake_executor(name, args):
        return {"draft_id": 999}
    out = await generate_contract_node(state, llm=AsyncMock(), tool_executor=fake_executor)

    assert out.draft_id == 999
    assert out.file_sent is True
    assert out.customer is not None and out.customer.name == "阿里"
    assert out.products == [ProductInfo(id=1, name="X1")]
    assert len(out.items) == 1
    assert out.shipping.address == "北京海淀"


@pytest.mark.asyncio
async def test_generate_contract_payload_validates_against_schema(monkeypatch):
    """钉钉实测 hotfix（task=JIsCqeYj 12:41）：generate_contract_node 构造的 payload
    必须 100% 通过 GENERATE_CONTRACT_DRAFT_SCHEMA 的 jsonschema 验证 — 之前漏 template_id
    + 字段名 contact/phone 错（schema 是 shipping_contact/shipping_phone）→ ToolArgsValidationError。
    """
    from hub.agent.graph.subgraphs import contract as contract_mod
    from hub.agent.graph.subgraphs.contract import generate_contract_node
    from hub.agent.graph.state import ContractState, CustomerInfo, ProductInfo, ContractItem
    from hub.agent.tools.generate_tools import GENERATE_CONTRACT_DRAFT_SCHEMA
    from decimal import Decimal
    from unittest.mock import AsyncMock
    from jsonschema import validate

    async def _fake_resolve_template():
        return 7
    monkeypatch.setattr(contract_mod, "_resolve_default_template_id", _fake_resolve_template)

    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=42, name="翼蓝")
    state.products = [ProductInfo(id=14, name="F1系列")]
    state.items = [ContractItem(product_id=14, name="F1", qty=10, price=Decimal("500"))]
    state.shipping.address = "广州市天河区华穗路406号"
    state.shipping.contact = "林生"
    state.shipping.phone = "13692977880"

    captured = {}

    async def fake_executor(name, args):
        captured["name"] = name
        captured["args"] = args
        return {"draft_id": 1}

    await generate_contract_node(state, llm=AsyncMock(), tool_executor=fake_executor)

    assert captured["name"] == "generate_contract_draft"
    payload = captured["args"]
    # 关键字段就位
    assert payload["template_id"] == 7
    assert payload["customer_id"] == 42
    assert payload["shipping_contact"] == "林生", "字段名必须是 shipping_contact 不是 contact"
    assert payload["shipping_phone"] == "13692977880", "字段名必须是 shipping_phone 不是 phone"
    assert "contact" not in payload, "禁止用旧字段名 contact"
    assert "phone" not in payload, "禁止用旧字段名 phone"
    # strict schema 全部 required 字段都得在
    schema = GENERATE_CONTRACT_DRAFT_SCHEMA["function"]["parameters"]
    validate(instance=payload, schema=schema)  # 不抛 = 通过


@pytest.mark.asyncio
async def test_generate_contract_no_template_fails_gracefully(monkeypatch):
    """库里没启用模板时（_resolve_default_template_id 返 None），节点不抛异常，
    走 missing_fields=['template'] 让 format_response/ask_user 给用户友好提示。
    """
    from hub.agent.graph.subgraphs import contract as contract_mod
    from hub.agent.graph.subgraphs.contract import generate_contract_node
    from hub.agent.graph.state import ContractState, CustomerInfo, ProductInfo, ContractItem
    from decimal import Decimal
    from unittest.mock import AsyncMock

    async def _no_template():
        return None
    monkeypatch.setattr(contract_mod, "_resolve_default_template_id", _no_template)

    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=42, name="翼蓝")
    state.products = [ProductInfo(id=14, name="F1")]
    state.items = [ContractItem(product_id=14, name="F1", qty=10, price=Decimal("500"))]

    fake_executor = AsyncMock()  # 不应该被调用
    out = await generate_contract_node(state, llm=AsyncMock(), tool_executor=fake_executor)

    fake_executor.assert_not_called()
    assert "no_active_contract_template" in out.errors
    assert "template" in out.missing_fields
    assert out.draft_id is None
    assert out.file_sent is False


@pytest.mark.asyncio
async def test_cleanup_after_contract_clears_complete_working_state():
    """P1 v1.11：cleanup_after_contract_node 把所有跨轮工作字段清空。"""
    from hub.agent.graph.subgraphs.contract import cleanup_after_contract_node
    from hub.agent.graph.state import ContractState, CustomerInfo, ProductInfo, ContractItem
    from decimal import Decimal

    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=10, name="阿里")
    state.products = [ProductInfo(id=1, name="X1")]
    state.items = [ContractItem(product_id=1, name="X1", qty=10, price=Decimal("300"))]
    state.shipping.address = "北京海淀"
    state.shipping.contact = "张三"
    state.extracted_hints = {"customer_name": "阿里", "items_raw": [{"hint": "X1", "qty": 10, "price": 300}]}
    state.active_subgraph = "contract"
    state.missing_fields = []
    state.draft_id = 999
    state.file_sent = True

    out = await cleanup_after_contract_node(state)

    assert out.customer is None
    assert out.products == []
    # review issue 3：items 也必须清空（与 quote cleanup 对齐），
    # 防止下一轮 chat/query/admin/eval snapshot 误显示上一单 items；
    # eval items_count 改从 generate_contract_draft tool args 取，不再依赖 state.items
    assert out.items == [], f"items 应该被清空，实际 {out.items}"
    assert out.shipping.address is None
    assert out.shipping.contact is None
    assert out.shipping.phone is None
    assert out.extracted_hints == {}
    assert out.candidate_customers == []
    assert out.candidate_products == {}
    assert out.active_subgraph is None
    assert out.missing_fields == []
    # 业务结果保留
    assert out.draft_id == 999
    assert out.file_sent is True
