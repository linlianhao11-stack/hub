# backend/tests/agent/test_subgraph_quote.py
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock
from hub.agent.graph.state import QuoteState, CustomerInfo, ProductInfo, ContractItem


@pytest.mark.asyncio
async def test_quote_subgraph_no_check_inventory_tool():
    """物理保证：quote 子图的 tools 列表不含 check_inventory / generate_contract_draft。"""
    from hub.agent.tools.registry import ToolRegistry
    from hub.agent.tools import register_all_tools
    reg = ToolRegistry()
    register_all_tools(reg)
    schemas = reg.schemas_for_subgraph("quote")
    names = {s["function"]["name"] for s in schemas}
    assert "check_inventory" not in names
    assert "generate_contract_draft" not in names  # 物理隔离 — 报价不应能生成合同
    assert "generate_price_quote" in names


@pytest.mark.asyncio
async def test_quote_subgraph_set_origin_active_subgraph():
    """v1.6 P1-A：报价子图入口必须有 set_origin 节点。"""
    from hub.agent.graph.subgraphs.quote import build_quote_subgraph
    compiled = build_quote_subgraph(llm=AsyncMock(), tool_executor=AsyncMock())
    nodes = set(compiled.get_graph().nodes)
    assert "set_origin" in nodes
    assert "generate_quote" in nodes
    assert "extract_contract_context" in nodes  # v1.9 P1-A
    assert "cleanup_after_quote" in nodes  # v1.11 P1


@pytest.mark.asyncio
async def test_generate_quote_keeps_state_for_format_response():
    """P1 v1.11：generate_quote_node 不清状态 — format_response 还要用 state.customer.name / len(state.items)。"""
    from hub.agent.graph.subgraphs.quote import generate_quote_node

    state = QuoteState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=10, name="阿里")
    state.products = [ProductInfo(id=1, name="X1")]
    state.items = [ContractItem(product_id=1, name="X1", qty=50, price=Decimal("300"))]

    async def fake_executor(name, args):
        return {"quote_id": 888}

    out = await generate_quote_node(state, llm=AsyncMock(), tool_executor=fake_executor)

    assert out.quote_id == 888
    assert out.file_sent is True
    assert out.customer is not None and out.customer.name == "阿里"
    assert len(out.items) == 1


@pytest.mark.asyncio
async def test_cleanup_after_quote_clears_complete_working_state():
    """P1 v1.11：cleanup_after_quote_node 把所有跨轮工作字段清空。"""
    from hub.agent.graph.subgraphs.quote import cleanup_after_quote_node

    state = QuoteState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=10, name="阿里")
    state.products = [ProductInfo(id=1, name="X1")]
    state.items = [ContractItem(product_id=1, name="X1", qty=50, price=Decimal("300"))]
    state.extracted_hints = {"customer_name": "阿里"}
    state.active_subgraph = "quote"
    state.quote_id = 888
    state.file_sent = True

    out = await cleanup_after_quote_node(state)

    assert out.customer is None
    assert out.products == []
    assert out.items == []
    assert out.extracted_hints == {}
    assert out.candidate_customers == []
    assert out.candidate_products == {}
    assert out.active_subgraph is None
    assert out.missing_fields == []
    # 业务结果保留
    assert out.quote_id == 888
    assert out.file_sent is True
