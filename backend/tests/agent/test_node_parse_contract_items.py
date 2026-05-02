# backend/tests/agent/test_node_parse_contract_items.py
import pytest
from unittest.mock import AsyncMock
import json
from decimal import Decimal
from hub.agent.graph.state import ContractState, ProductInfo
from hub.agent.graph.nodes.parse_contract_items import parse_contract_items_node


def _llm_returning_json(text):
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {
        "text": text, "finish_reason": "stop", "tool_calls": [],
    })())
    return llm


@pytest.mark.asyncio
async def test_parse_items_three_products_full_qty_price():
    """故事 4 场景：H5 10 个 300 / F1 10 个 500 / K5 20 个 300 — 三个 item 都齐。"""
    state = ContractState(user_message="H5 10 个 300, F1 10 个 500, K5 20 个 300",
                            hub_user_id=1, conversation_id="c1")
    state.products = [
        ProductInfo(id=1, name="H5"), ProductInfo(id=2, name="F1"), ProductInfo(id=3, name="K5"),
    ]
    llm = _llm_returning_json(json.dumps({"items": [
        {"product_id": 1, "qty": 10, "price": 300},
        {"product_id": 2, "qty": 10, "price": 500},
        {"product_id": 3, "qty": 20, "price": 300},
    ]}))
    out = await parse_contract_items_node(state, llm=llm)
    kw = llm.chat.await_args.kwargs
    assert kw["thinking"] == {"type": "enabled"}
    assert len(out.items) == 3
    assert out.items[0].qty == 10 and out.items[0].price == Decimal("300")
    assert "item_qty" not in str(out.missing_fields)
    assert "item_price" not in str(out.missing_fields)


@pytest.mark.asyncio
async def test_parse_items_missing_price_does_not_default():
    """用户没说价格 — 不能默认 0 / list_price，必须 missing_fields。"""
    state = ContractState(user_message="X1 10 个", hub_user_id=1, conversation_id="c1")
    state.products = [ProductInfo(id=1, name="X1")]
    llm = _llm_returning_json(json.dumps({"items": [
        {"product_id": 1, "qty": 10, "price": None},
    ]}))
    out = await parse_contract_items_node(state, llm=llm)
    assert out.items == []
    assert any("item_price" in mf for mf in out.missing_fields)


@pytest.mark.asyncio
async def test_parse_items_missing_qty():
    state = ContractState(user_message="X1 300 块", hub_user_id=1, conversation_id="c1")
    state.products = [ProductInfo(id=1, name="X1")]
    llm = _llm_returning_json(json.dumps({"items": [
        {"product_id": 1, "qty": None, "price": 300},
    ]}))
    out = await parse_contract_items_node(state, llm=llm)
    assert out.items == []
    assert any("item_qty" in mf for mf in out.missing_fields)


@pytest.mark.asyncio
async def test_parse_items_skip_when_products_ambiguous():
    """resolve_products 留下 candidate_products 时本节点不应执行（必须等用户先选产品）。"""
    state = ContractState(user_message="X1 10 个 300", hub_user_id=1, conversation_id="c1")
    state.candidate_products["X1"] = [ProductInfo(id=1, name="X1"), ProductInfo(id=2, name="X1")]
    state.missing_fields.append("product_choice:X1")
    llm = AsyncMock()
    llm.chat = AsyncMock()
    out = await parse_contract_items_node(state, llm=llm)
    llm.chat.assert_not_awaited()
    assert out.items == []


@pytest.mark.asyncio
async def test_parse_items_uses_extracted_hints_fast_path_no_llm():
    """v1.8 快路径 + v1.9 P2-B：state.extracted_hints['items_raw'] 已存在时
    本地 hint→product 模糊匹配，**不**调 LLM。"""
    state = ContractState(user_message="选 2", hub_user_id=1, conversation_id="c1")
    state.products = [
        ProductInfo(id=1, name="H5"),
        ProductInfo(id=2, name="F1"),
        ProductInfo(id=3, name="K5"),
    ]
    state.extracted_hints = {
        "items_raw": [
            {"hint": "H5", "qty": 10, "price": 300},
            {"hint": "F1", "qty": 10, "price": 500},
            {"hint": "K5", "qty": 20, "price": 300},
        ],
    }
    llm = AsyncMock()
    llm.chat = AsyncMock()
    out = await parse_contract_items_node(state, llm=llm)
    llm.chat.assert_not_awaited()
    assert len(out.items) == 3
    assert {(i.product_id, i.qty, int(i.price)) for i in out.items} == {
        (1, 10, 300), (2, 10, 500), (3, 20, 300),
    }


@pytest.mark.asyncio
async def test_parse_items_falls_back_to_llm_when_hint_mismatch():
    """v1.9 P2-B：items_raw hint 在 state.products 里找不到 → fallback LLM thinking on。"""
    state = ContractState(user_message="X1 10 个 300", hub_user_id=1, conversation_id="c1")
    state.products = [ProductInfo(id=1, name="H5"), ProductInfo(id=2, name="F1")]
    state.extracted_hints = {
        "items_raw": [
            {"hint": "Z9", "qty": 10, "price": 300},
        ],
    }
    llm = _llm_returning_json(json.dumps({"items": [
        {"product_id": 1, "qty": 10, "price": 300},
    ]}))
    out = await parse_contract_items_node(state, llm=llm)
    llm.chat.assert_awaited()
    assert len(out.items) == 1 and out.items[0].product_id == 1


@pytest.mark.asyncio
async def test_parse_items_fallback_uses_items_raw_not_user_message():
    """P1-B v1.10：跨轮 user_message='选 2' + items_raw 非空时
    fallback prompt 必须传 items_raw，不传 user_message（避免 LLM 看到"选 2"无法对齐）。"""
    state = ContractState(user_message="选 2", hub_user_id=1, conversation_id="c1")
    state.products = [ProductInfo(id=1, name="H5"), ProductInfo(id=2, name="F1")]
    state.extracted_hints = {
        "items_raw": [
            {"hint": "M5", "qty": 50, "price": 300},
        ],
    }
    captured = {}
    async def fake_chat(*, messages, **_):
        captured["user_payload"] = json.loads(messages[1]["content"])
        return type("R", (), {"text": json.dumps({"items": [
            {"product_id": 1, "qty": 50, "price": 300},
        ]}), "finish_reason": "stop", "tool_calls": []})()
    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=fake_chat)

    out = await parse_contract_items_node(state, llm=llm)
    assert "user_message" not in captured["user_payload"], \
        f"fallback 不能传 user_message='选 2'，实际：{captured['user_payload']}"
    assert captured["user_payload"]["items_raw"] == [
        {"hint": "M5", "qty": 50, "price": 300},
    ]
    assert len(out.items) == 1 and out.items[0].qty == 50 and int(out.items[0].price) == 300
