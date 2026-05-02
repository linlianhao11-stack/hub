import pytest
import asyncio
from datetime import datetime, timedelta
from fakeredis.aioredis import FakeRedis
from hub.agent.tools.confirm_gate import (
    ConfirmGate, PendingAction, CrossContextClaim,
)


@pytest.fixture
async def redis():
    r = FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


@pytest.fixture
def gate(redis):
    return ConfirmGate(redis)


@pytest.mark.asyncio
async def test_pending_action_must_carry_conversation_id(gate):
    p = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_price", summary="阿里 X1 → 280",
        payload={"tool_name": "adjust_price_request",
                 "args": {"customer_id": 10, "product_id": 1, "new_price": 280.0, "reason": ""}},
        action_prefix="adj",
    )
    assert p.conversation_id == "c1"
    # P2-G：action_id 必须是完整 32-hex（含前缀）
    assert p.action_id.startswith("adj-")
    assert len(p.action_id.split("-", 1)[1]) == 32
    # P1-C：payload 必须落库
    assert p.payload["tool_name"] == "adjust_price_request"
    assert p.payload["args"]["customer_id"] == 10


# Test helper：测试用短 action_id 便于断言；production 必须经过自动生成
def _make_test_pending_kwargs(**overrides) -> dict:
    """测试 fixture：补齐 payload 必填字段；overrides 可覆盖 action_id 等。"""
    base = {
        "subgraph": "adjust_price",
        "summary": "test pending",
        "payload": {"tool_name": "adjust_price_request",
                     "args": {"customer_id": 1, "product_id": 1, "new_price": 1.0, "reason": ""}},
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_list_pending_for_context_filters_by_both(gate):
    """同一 user 在两个 conversation 各有 pending — list_for_context 只返回当前 context 的。"""
    await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-1", hub_user_id=1, conversation_id="c1-private", summary="A",
    ))
    await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-2", hub_user_id=1, conversation_id="c2-group", summary="B",
    ))
    in_c1 = await gate.list_pending_for_context(conversation_id="c1-private", hub_user_id=1)
    in_c2 = await gate.list_pending_for_context(conversation_id="c2-group", hub_user_id=1)
    assert {p.action_id for p in in_c1} == {"adj-1"}
    assert {p.action_id for p in in_c2} == {"adj-2"}


@pytest.mark.asyncio
async def test_claim_rejects_cross_conversation(gate):
    """伪造 token 跨会话 claim 必须 raise CrossContextClaim。"""
    p = await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-1", hub_user_id=1, conversation_id="c1", summary="A",
    ))
    with pytest.raises(CrossContextClaim):
        await gate.claim(action_id="adj-1", token=p.token,
                         hub_user_id=1, conversation_id="c2")  # 错的 conv


@pytest.mark.asyncio
async def test_claim_rejects_wrong_user(gate):
    """user A 的 pending 不能被 user B claim。"""
    p = await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-1", hub_user_id=1, conversation_id="c1", summary="A",
    ))
    with pytest.raises(CrossContextClaim):
        await gate.claim(action_id="adj-1", token=p.token,
                         hub_user_id=2, conversation_id="c1")


@pytest.mark.asyncio
async def test_claim_single_use_token(gate):
    """token 单次消费 — 第二次 claim 失败。"""
    p = await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-1", hub_user_id=1, conversation_id="c1", summary="A",
    ))
    ok = await gate.claim(action_id="adj-1", token=p.token, hub_user_id=1, conversation_id="c1")
    assert ok
    with pytest.raises(Exception):  # 已消费
        await gate.claim(action_id="adj-1", token=p.token, hub_user_id=1, conversation_id="c1")


@pytest.mark.asyncio
async def test_list_pending_order_stable_by_created_at(gate):
    """P2-C v1.3：list_pending_for_context 必须按 created_at asc 稳定排序。
    多 pending 时 confirm_node 看到的"1)/2)/3)"和下一轮"1" 必须绑同一 action。"""
    p_first = await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-first", hub_user_id=1, conversation_id="c1", summary="先创建",
    ))
    await asyncio.sleep(0.01)  # 确保不同 created_at
    p_second = await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-second", hub_user_id=1, conversation_id="c1", summary="后创建",
    ))
    await asyncio.sleep(0.01)
    p_third = await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-third", hub_user_id=1, conversation_id="c1", summary="最后创建",
    ))

    # 连续读 5 次顺序必须一致（不靠 Redis scan 偶然顺序）
    orders = []
    for _ in range(5):
        pendings = await gate.list_pending_for_context(conversation_id="c1", hub_user_id=1)
        orders.append([p.action_id for p in pendings])
    # 所有 5 次顺序相同
    assert all(o == orders[0] for o in orders)
    # 顺序与 created_at asc 一致
    assert orders[0] == ["adj-first", "adj-second", "adj-third"]


# ====== Task 5.1：confirm_node 三分支测试 ======

import pytest
from hub.agent.graph.state import AgentState
from hub.agent.graph.nodes.confirm import confirm_node


@pytest.mark.asyncio
async def test_confirm_node_zero_pending(gate):
    """0 pending → 提示用户没有待办。"""
    state = AgentState(user_message="确认", hub_user_id=1, conversation_id="c1")
    out = await confirm_node(state, gate=gate)
    assert "没有待办" in (out.final_response or "")
    assert out.confirmed_subgraph is None
    assert out.confirmed_action_id is None


@pytest.mark.asyncio
async def test_confirm_node_one_pending_claims(gate):
    """1 pending → claim 成功，state 写 confirmed_*。"""
    p = await gate.create_pending(
        action_prefix="adj", hub_user_id=1, conversation_id="c1",
        subgraph="adjust_price", summary="阿里 X1 → 280",
        payload={"tool_name": "adjust_price_request", "args": {"customer_id": 10, "product_id": 1, "new_price": 280.0, "reason": ""}},
    )
    state = AgentState(user_message="确认", hub_user_id=1, conversation_id="c1")
    out = await confirm_node(state, gate=gate)
    assert out.confirmed_subgraph == "adjust_price"
    assert out.confirmed_action_id == p.action_id
    assert out.confirmed_payload["args"]["customer_id"] == 10
    assert out.errors == []


@pytest.mark.asyncio
async def test_confirm_node_multi_pending_lists_does_not_claim(gate):
    """多个 pending + 用户没说编号 → 列出来不 claim。"""
    p1 = await gate.create_pending(
        action_prefix="adj", hub_user_id=1, conversation_id="c1",
        subgraph="adjust_price", summary="阿里 X1 → 280",
        payload={"tool_name": "adjust_price_request", "args": {}},
    )
    p2 = await gate.create_pending(
        action_prefix="vch", hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="SO-001 出库",
        payload={"tool_name": "create_voucher_draft", "args": {}},
    )
    state = AgentState(user_message="确认", hub_user_id=1, conversation_id="c1")
    out = await confirm_node(state, gate=gate)
    assert "1)" in out.final_response and "2)" in out.final_response
    # 都未 claim
    assert not await gate.is_claimed(p1.action_id)
    assert not await gate.is_claimed(p2.action_id)


@pytest.mark.asyncio
async def test_confirm_node_multi_pending_select_first_claims_only_first(gate):
    """P1-C：多个 pending，用户回 "1" → 只 claim p1，p2 仍 pending；
    state.confirmed_payload 必须是 p1 的 payload，不是 state 当前内容。"""
    p1 = await gate.create_pending(
        action_prefix="adj", hub_user_id=1, conversation_id="c1",
        subgraph="adjust_price", summary="阿里 X1 → 280",
        payload={"tool_name": "adjust_price_request", "args": {"customer_id": 10, "product_id": 1, "new_price": 280.0, "reason": ""}},
    )
    p2 = await gate.create_pending(
        action_prefix="adj", hub_user_id=1, conversation_id="c1",
        subgraph="adjust_price", summary="百度 Y1 → 350",
        payload={"tool_name": "adjust_price_request", "args": {"customer_id": 20, "product_id": 5, "new_price": 350.0, "reason": ""}},
    )
    state = AgentState(user_message="1", hub_user_id=1, conversation_id="c1")
    out = await confirm_node(state, gate=gate)
    assert out.confirmed_action_id == p1.action_id
    assert out.confirmed_payload["args"]["customer_id"] == 10
    assert out.confirmed_payload["args"]["new_price"] == 280.0
    # p2 仍 pending
    assert not await gate.is_claimed(p2.action_id)
