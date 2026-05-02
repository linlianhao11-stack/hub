# backend/tests/agent/test_node_resolve_products.py
import pytest
from unittest.mock import AsyncMock
import json
from hub.agent.graph.state import ContractState, ProductInfo
from hub.agent.graph.nodes.resolve_products import resolve_products_node


@pytest.mark.asyncio
async def test_resolve_products_multi_skus_unique_each():
    """故事 4 场景：H5 / F1 / K5 三个 sku 各自唯一命中。subgraph 物理不挂 check_inventory。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": "", "finish_reason": "tool_calls",
        "tool_calls": [{"id": "1", "type": "function",
            "function": {"name": "search_products",
                          "arguments": json.dumps({"query": "H5,F1,K5"})}}]})())
    state = ContractState(user_message="H5 10 个 300, F1 10 个 500, K5 20 个 300",
                            hub_user_id=1, conversation_id="c1",
                            extracted_hints={"product_hints": ["H5", "F1", "K5"]})

    SEARCH_RESULTS = {
        "H5": [{"id": 1, "name": "H5"}],
        "F1": [{"id": 2, "name": "F1"}],
        "K5": [{"id": 3, "name": "K5"}],
    }
    async def fake_executor(name, args):
        assert name == "search_products"
        return SEARCH_RESULTS.get(args["query"], [])
    out = await resolve_products_node(state, llm=llm, tool_executor=fake_executor)

    assert {p.name for p in out.products} == {"H5", "F1", "K5"}
    assert len(out.candidate_products) == 0
    assert out.items == []


@pytest.mark.asyncio
async def test_resolve_products_same_name_ambiguous():
    """同名产品（多个 X1 不同规格）— 写 candidate_products，不默认取 [0]。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": "", "finish_reason": "tool_calls",
        "tool_calls": [{"id": "1", "type": "function",
            "function": {"name": "search_products",
                          "arguments": json.dumps({"query": "X1"})}}]})())
    state = ContractState(user_message="X1 10 个 300", hub_user_id=1, conversation_id="c1",
                            extracted_hints={"product_hints": ["X1"]})
    out = await resolve_products_node(state, llm=llm,
        tool_executor=AsyncMock(return_value=[
            {"id": 1, "name": "X1", "color": "黑", "spec": "5KG"},
            {"id": 2, "name": "X1", "color": "白", "spec": "10KG"},
        ]))
    assert out.products == [] or len(out.products) == 0
    assert "X1" in out.candidate_products
    assert len(out.candidate_products["X1"]) == 2
    assert any("product_choice" in mf for mf in out.missing_fields)


@pytest.mark.asyncio
async def test_resolve_products_not_found():
    """产品没找到 — missing_fields 加 'products'。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": "", "finish_reason": "tool_calls",
        "tool_calls": [{"id": "1", "type": "function",
            "function": {"name": "search_products",
                          "arguments": json.dumps({"query": "未知货"})}}]})())
    state = ContractState(user_message="未知货 10 个 300", hub_user_id=1, conversation_id="c1",
                            extracted_hints={"product_hints": ["未知货"]})
    out = await resolve_products_node(state, llm=llm,
        tool_executor=AsyncMock(return_value=[]))
    assert out.products == []
    # 找不到时 missing_fields 加 'products' 或 'product_not_found:未知货'（VERBATIM 实现走后者）
    assert any("product" in mf for mf in out.missing_fields)


@pytest.mark.asyncio
async def test_multi_group_candidate_rejects_naked_number():
    """P2 v1.10：H5 和 F1 都有候选，用户回"选 2"必须**不**消费任何候选 —
    避免裸编号被同时套到两组。要求 id=N 精确选每组。"""
    state = ContractState(user_message="选 2", hub_user_id=1, conversation_id="c1")
    state.candidate_products = {
        "H5": [ProductInfo(id=10, name="H5", spec="5kg"),
               ProductInfo(id=11, name="H5", spec="10kg")],
        "F1": [ProductInfo(id=20, name="F1", color="黑"),
               ProductInfo(id=21, name="F1", color="白")],
    }
    state.missing_fields = ["product_choice:H5", "product_choice:F1"]
    out = await resolve_products_node(state, llm=AsyncMock(), tool_executor=AsyncMock())
    assert out.products == []
    assert len(out.candidate_products) == 2
    assert "product_choice:H5" in out.missing_fields
    assert "product_choice:F1" in out.missing_fields


@pytest.mark.asyncio
async def test_multi_group_candidate_accepts_id_match():
    """P2 v1.10：多组候选时用 'id=11' 精确选 H5 第二项；F1 仍留 candidate。"""
    state = ContractState(user_message="id=11", hub_user_id=1, conversation_id="c1")
    state.candidate_products = {
        "H5": [ProductInfo(id=10, name="H5", spec="5kg"),
               ProductInfo(id=11, name="H5", spec="10kg")],
        "F1": [ProductInfo(id=20, name="F1", color="黑"),
               ProductInfo(id=21, name="F1", color="白")],
    }
    state.missing_fields = ["product_choice:H5", "product_choice:F1"]
    out = await resolve_products_node(state, llm=AsyncMock(), tool_executor=AsyncMock())
    assert len(out.products) == 1 and out.products[0].id == 11
    assert "H5" not in out.candidate_products
    assert "F1" in out.candidate_products
    assert "product_choice:H5" not in out.missing_fields
    assert "product_choice:F1" in out.missing_fields


@pytest.mark.asyncio
async def test_multi_group_candidate_one_message_multiple_ids():
    """P2-C v1.11：用户一次回 "id=11 id=21" 一次性解决两组候选。"""
    state = ContractState(user_message="id=11 id=21", hub_user_id=1, conversation_id="c1")
    state.candidate_products = {
        "H5": [ProductInfo(id=10, name="H5", spec="5kg"),
               ProductInfo(id=11, name="H5", spec="10kg")],
        "F1": [ProductInfo(id=20, name="F1", color="黑"),
               ProductInfo(id=21, name="F1", color="白")],
    }
    state.missing_fields = ["product_choice:H5", "product_choice:F1"]
    out = await resolve_products_node(state, llm=AsyncMock(), tool_executor=AsyncMock())
    assert {p.id for p in out.products} == {11, 21}
    assert out.candidate_products == {}
    assert "product_choice:H5" not in out.missing_fields
    assert "product_choice:F1" not in out.missing_fields
