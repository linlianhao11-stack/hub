# backend/tests/agent/test_node_extract_contract_context.py
import pytest
import json
from unittest.mock import AsyncMock
from hub.agent.graph.state import ContractState
from hub.agent.graph.nodes.extract_contract_context import extract_contract_context_node


def _llm_returning_json(text):
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {
        "text": text, "finish_reason": "stop", "tool_calls": [],
    })())
    return llm


@pytest.mark.asyncio
async def test_extract_full_contract_request():
    """v1.8 P1-A+B：第一轮就把 customer_name + product_hints + items_raw + shipping 全抽进 state。"""
    state = ContractState(
        user_message="给阿里做合同 H5 10 个 300，F1 10 个 500，K5 20 个 300，"
                       "地址广州市天河区华穗路406号中景B座，林生，13692977880",
        hub_user_id=1, conversation_id="c1",
    )
    llm = _llm_returning_json(json.dumps({
        "customer_name": "阿里",
        "product_hints": ["H5", "F1", "K5"],
        "items_raw": [
            {"hint": "H5", "qty": 10, "price": 300},
            {"hint": "F1", "qty": 10, "price": 500},
            {"hint": "K5", "qty": 20, "price": 300},
        ],
        "shipping": {
            "address": "广州市天河区华穗路406号中景B座",
            "contact": "林生",
            "phone": "13692977880",
        },
    }))
    out = await extract_contract_context_node(state, llm=llm)
    assert out.extracted_hints["customer_name"] == "阿里"
    assert out.extracted_hints["product_hints"] == ["H5", "F1", "K5"]
    assert len(out.extracted_hints["items_raw"]) == 3
    assert out.extracted_hints["items_raw"][1] == {"hint": "F1", "qty": 10, "price": 500}
    assert out.shipping.address.startswith("广州市天河区")
    assert out.shipping.contact == "林生"
    assert out.shipping.phone == "13692977880"


@pytest.mark.asyncio
async def test_extract_skip_only_on_pure_selection_messages():
    """P1-B v1.9：只对明确选择/确认类消息跳过抽取，保护 hints 不被覆盖。
    "选 2" / "1" / "id=10" / "确认" → 跳过；
    "北京海淀" / "张三" / "13800001111" → 不跳过（用户在补字段）。"""
    base_state = lambda msg: ContractState(user_message=msg, hub_user_id=1, conversation_id="c1")

    SKIP_MESSAGES = [
        "选 2", "选2", "1", "  2 ", "第二个",
        "id=10", "id=12",
        # v1.12 P1-B：多 id 也要算 selection（避免 LLM 把 "id=11 id=21" 误抽成新 hints）
        "id=11 id=21", "id=11, id=21", "id=11、id=21", "id=11,id=21,id=33",
        "确认", "是", "好的",
    ]
    for msg in SKIP_MESSAGES:
        state = base_state(msg)
        state.extracted_hints = {"customer_name": "阿里"}
        llm = AsyncMock()
        llm.chat = AsyncMock()
        await extract_contract_context_node(state, llm=llm)
        llm.chat.assert_not_awaited(), f"消息 {msg!r} 应跳过 LLM 但调了"


@pytest.mark.asyncio
async def test_extract_short_field_supplement_still_runs_llm():
    """P1-B v1.9：用户上轮缺地址，本轮回"北京海淀，张三 13800001111" — **不能**跳过抽取。"""
    state = ContractState(user_message="北京海淀，张三 13800001111",
                           hub_user_id=1, conversation_id="c1")
    state.extracted_hints = {"customer_name": "阿里", "product_hints": ["X1"]}
    # state.shipping 全空（上轮没抽到）
    llm = _llm_returning_json(json.dumps({
        "customer_name": None,
        "product_hints": [],
        "items_raw": [],
        "shipping": {"address": "北京海淀", "contact": "张三", "phone": "13800001111"},
    }))
    out = await extract_contract_context_node(state, llm=llm)
    llm.chat.assert_awaited()  # 必须跑 LLM
    # 抽到的 shipping 写进 state
    assert out.shipping.address == "北京海淀"
    assert out.shipping.contact == "张三"
    assert out.shipping.phone == "13800001111"
    # 上轮 hints 保留（because抽到 customer_name=None 时不覆盖）
    assert out.extracted_hints["customer_name"] == "阿里"


@pytest.mark.asyncio
async def test_extract_skip_when_candidate_products_with_hint_id_reply():
    """P1 v1.13：上轮留了 candidate_products + 本轮"H5 用 id=10，F1 用 id=22"按 ask_user 文案回复 →
    必须**不**调 LLM（否则 LLM 会把 H5/F1 重新抽成新 product_hints / items_raw 覆盖第一轮）。"""
    from hub.agent.graph.state import ProductInfo
    HINT_ID_REPLIES = [
        "H5 用 id=10，F1 用 id=22",   # ask_user 提示原话
        "H5 id=10, F1 id=22",        # 简化
        "H5: id=10  F1: id=22",       # 冒号变体
        "H5=10 F1=22",                # 极简（不含 'id'）— 不命中也行（用户大概率会写 id=N）
    ]
    for msg in HINT_ID_REPLIES[:3]:  # 前 3 个含 id=N，必须命中
        state = ContractState(user_message=msg, hub_user_id=1, conversation_id="c1")
        # 候选 products 里的 id 包含 10 和 22
        state.candidate_products = {
            "H5": [ProductInfo(id=10, name="H5"), ProductInfo(id=11, name="H5")],
            "F1": [ProductInfo(id=22, name="F1"), ProductInfo(id=23, name="F1")],
        }
        # 第一轮已抽到的 items_raw（要保护不被覆盖）
        state.extracted_hints = {
            "items_raw": [
                {"hint": "H5", "qty": 10, "price": 300},
                {"hint": "F1", "qty": 5, "price": 500},
            ],
        }
        llm = AsyncMock()
        llm.chat = AsyncMock()  # 不应被调
        out = await extract_contract_context_node(state, llm=llm)
        llm.chat.assert_not_awaited(), f"消息 {msg!r} 应跳过 LLM"
        # 第一轮 items_raw 仍保留
        assert out.extracted_hints["items_raw"][0]["qty"] == 10
        assert out.extracted_hints["items_raw"][1]["price"] == 500


@pytest.mark.asyncio
async def test_extract_does_not_skip_phone_number_with_no_candidates():
    """P1 v1.13 边界：用户回 "13800001111"（不是候选 id）→ **不**应跳过 LLM。
    电话号刚好是 11 位数字，但没"id="前缀也不是候选 id → safety check OK。"""
    state = ContractState(user_message="13800001111", hub_user_id=1, conversation_id="c1")
    state.extracted_hints = {"customer_name": "阿里"}
    # 没 candidate_products，candidate id reference 不命中
    llm = _llm_returning_json(json.dumps({
        "customer_name": None, "product_hints": [], "items_raw": [],
        "shipping": {"address": None, "contact": None, "phone": "13800001111"},
    }))
    out = await extract_contract_context_node(state, llm=llm)
    llm.chat.assert_awaited()  # 必须跑 LLM 抽 phone
    assert out.shipping.phone == "13800001111"


@pytest.mark.asyncio
async def test_extract_partial_only_customer():
    """用户只说"给阿里做合同 X1" — 抽到 customer_name + product_hints；qty/price/shipping 全 null。"""
    state = ContractState(user_message="给阿里做合同 X1", hub_user_id=1, conversation_id="c1")
    llm = _llm_returning_json(json.dumps({
        "customer_name": "阿里",
        "product_hints": ["X1"],
        "items_raw": [{"hint": "X1", "qty": None, "price": None}],
        "shipping": {"address": None, "contact": None, "phone": None},
    }))
    out = await extract_contract_context_node(state, llm=llm)
    assert out.extracted_hints["customer_name"] == "阿里"
    assert out.extracted_hints["product_hints"] == ["X1"]
    assert out.shipping.address is None
    assert out.shipping.phone is None


@pytest.mark.asyncio
async def test_extract_does_not_overwrite_existing_with_none():
    """v1.8：本轮抽到 None 时**不**覆盖 state 里上轮已有值（保护跨轮信息）。"""
    state = ContractState(
        user_message="顺便把电话也加上 13900002222",
        hub_user_id=1, conversation_id="c1",
    )
    state.shipping.address = "北京海淀"
    state.shipping.contact = "张三"
    llm = _llm_returning_json(json.dumps({
        "customer_name": None,
        "product_hints": [],
        "items_raw": [],
        "shipping": {"address": None, "contact": None, "phone": "13900002222"},
    }))
    out = await extract_contract_context_node(state, llm=llm)
    # address / contact 保留上轮，phone 写新的
    assert out.shipping.address == "北京海淀"
    assert out.shipping.contact == "张三"
    assert out.shipping.phone == "13900002222"
