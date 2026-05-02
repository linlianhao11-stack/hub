import pytest
import asyncio
from unittest.mock import AsyncMock
from fakeredis.aioredis import FakeRedis

from hub.agent.graph.state import AdjustStockState, AgentState, ProductInfo
from hub.agent.tools.confirm_gate import ConfirmGate
from hub.agent.graph.nodes.confirm import confirm_node


@pytest.fixture
async def redis():
    r = FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


@pytest.fixture
def gate(redis):
    return ConfirmGate(redis)


@pytest.mark.asyncio
async def test_idempotency_key_dedup(gate):
    """同 (conv, user, product, delta) 5 min 内 preview 两次只生成 1 个 pending。"""
    from hub.agent.graph.subgraphs.adjust_stock import preview_adjust_stock_node

    def make_state():
        s = AdjustStockState(user_message="X1 库存 +10", hub_user_id=1, conversation_id="c1")
        s.product = ProductInfo(id=1, name="X1")
        s.delta_qty = 10
        return s

    llm = AsyncMock()
    llm.chat = AsyncMock(
        return_value=type("R", (), {
            "text": "X1 +10 调整预览",
            "tool_calls": [],
            "finish_reason": "stop",
        })()
    )

    s1 = make_state()
    s2 = make_state()
    await preview_adjust_stock_node(s1, llm=llm, gate=gate)
    await preview_adjust_stock_node(s2, llm=llm, gate=gate)
    assert s1.pending_action_id == s2.pending_action_id


@pytest.mark.asyncio
async def test_two_stock_pendings_select_first_only_executes_first(gate):
    """两个 stock pending（不同产品），confirm 选 1 → state.confirmed_payload 是 p1 的。"""
    p1 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_stock", action_prefix="stk",
        summary="X1 库存 +10",
        payload={
            "tool_name": "create_stock_adjustment_request",
            "args": {"product_id": 1, "delta_qty": 10, "reason": ""},
        },
        idempotency_key="stk:c1:1:1:10",
    )
    await asyncio.sleep(0.01)
    p2 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_stock", action_prefix="stk",
        summary="X2 库存 -5",
        payload={
            "tool_name": "create_stock_adjustment_request",
            "args": {"product_id": 2, "delta_qty": -5, "reason": ""},
        },
        idempotency_key="stk:c1:1:2:-5",
    )
    state = AgentState(user_message="1", hub_user_id=1, conversation_id="c1")
    state = await confirm_node(state, gate=gate)
    assert state.confirmed_payload["args"]["product_id"] == 1
    assert state.confirmed_payload["args"]["delta_qty"] == 10
    assert not await gate.is_claimed(p2.action_id)


@pytest.mark.asyncio
async def test_cross_conversation_isolation(gate):
    """同 user 在私聊 c1 起 stock preview，群聊 c2 回"确认"看不到。"""
    p = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_stock", action_prefix="stk",
        summary="X1 库存 +10",
        payload={
            "tool_name": "create_stock_adjustment_request",
            "args": {"product_id": 1, "delta_qty": 10, "reason": ""},
        },
        idempotency_key="stk:c1:1:1:10",
    )
    state = AgentState(user_message="确认", hub_user_id=1, conversation_id="c2")
    state = await confirm_node(state, gate=gate)
    assert "没有待办" in (state.final_response or "")
    assert not await gate.is_claimed(p.action_id)


@pytest.mark.asyncio
async def test_repeat_confirm_after_claim_rejected(gate):
    """同一 pending claim 一次后再"确认"必须不能再次执行（token 单次消费）。"""
    await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_stock", action_prefix="stk",
        summary="X1 库存 +10",
        payload={
            "tool_name": "create_stock_adjustment_request",
            "args": {"product_id": 1, "delta_qty": 10, "reason": ""},
        },
        idempotency_key="stk:c1:1:1:10",
    )
    state1 = AgentState(user_message="确认", hub_user_id=1, conversation_id="c1")
    await confirm_node(state1, gate=gate)
    assert state1.confirmed_payload is not None

    state2 = AgentState(user_message="确认", hub_user_id=1, conversation_id="c1")
    await confirm_node(state2, gate=gate)
    assert "没有待办" in (state2.final_response or "")
