import pytest
from unittest.mock import AsyncMock
from fakeredis.aioredis import FakeRedis
from hub.agent.graph.state import VoucherState, AgentState
from hub.agent.tools.confirm_gate import ConfirmGate
from hub.agent.graph.nodes.confirm import confirm_node
from hub.agent.graph.subgraphs.voucher import preview_voucher_node


@pytest.fixture
async def redis():
    r = FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


@pytest.fixture
def gate(redis):
    return ConfirmGate(redis)


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": "凭证预览：SO-1 出库 / 1 项明细", "tool_calls": [], "finish_reason": "stop"})())
    return llm


@pytest.mark.asyncio
async def test_voucher_rejects_unapproved_order(gate, mock_llm):
    """未审批订单不允许出凭证（fail closed）。"""
    state = VoucherState(user_message="出库 SO-1", hub_user_id=1, conversation_id="c1", order_id=1)
    async def fake_executor(name, args):
        return {"order_id": 1, "status": "draft"}
    out = await preview_voucher_node(state, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    assert "未审批" in (out.final_response or "")
    assert state.pending_action_id is None


@pytest.mark.asyncio
async def test_voucher_rejects_already_outbound_voucher(gate, mock_llm):
    """订单已有 outbound 凭证 → 出库请求拒。"""
    state = VoucherState(user_message="出库 SO-1", hub_user_id=1, conversation_id="c1", order_id=1)
    async def fake_executor(name, args):
        return {"order_id": 1, "status": "approved",
                "outbound_voucher_count": 1, "inbound_voucher_count": 0,
                "items": [{"product_id": 1, "qty": 10}]}
    out = await preview_voucher_node(state, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    assert "已有出库凭证" in (out.final_response or "")
    assert state.pending_action_id is None


@pytest.mark.asyncio
async def test_voucher_rejects_already_inbound_voucher(gate, mock_llm):
    """订单已有 inbound 凭证 → 入库请求拒。"""
    state = VoucherState(user_message="入库 SO-1", hub_user_id=1, conversation_id="c1", order_id=1)
    async def fake_executor(name, args):
        return {"order_id": 1, "status": "approved",
                "outbound_voucher_count": 0, "inbound_voucher_count": 1,
                "items": [{"product_id": 1, "qty": 10}]}
    out = await preview_voucher_node(state, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    assert "已有入库凭证" in (out.final_response or "")
    assert state.pending_action_id is None


@pytest.mark.asyncio
async def test_voucher_idempotent_same_context_reuses(gate, mock_llm):
    """同 (conv, user) 同订单 12 小时内 preview 两次复用同一 pending。"""
    state1 = VoucherState(user_message="出库 SO-1", hub_user_id=1, conversation_id="c1", order_id=1)
    state2 = VoucherState(user_message="出库 SO-1", hub_user_id=1, conversation_id="c1", order_id=1)
    async def fake_executor(name, args):
        return {"order_id": 1, "status": "approved",
                "outbound_voucher_count": 0, "inbound_voucher_count": 0,
                "items": [{"product_id": 1, "qty": 10}]}
    await preview_voucher_node(state1, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    await preview_voucher_node(state2, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    assert state1.pending_action_id == state2.pending_action_id


@pytest.mark.asyncio
async def test_voucher_idempotent_cross_context_fails_closed(gate, mock_llm):
    """A 私聊创建 SO-1 voucher pending；B 群聊同订单 → fail closed，不拿到 A 的 action_id。"""
    state_a = VoucherState(user_message="出库 SO-1", hub_user_id=1,
                             conversation_id="c1-private", order_id=1)
    async def fake_executor(name, args):
        return {"order_id": 1, "status": "approved",
                "outbound_voucher_count": 0, "inbound_voucher_count": 0,
                "items": [{"product_id": 1, "qty": 10}]}
    await preview_voucher_node(state_a, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    assert state_a.pending_action_id is not None

    state_b = VoucherState(user_message="出库 SO-1", hub_user_id=2,
                             conversation_id="c2-group", order_id=1)
    out_b = await preview_voucher_node(state_b, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    assert state_b.pending_action_id is None
    resp = out_b.final_response or ""
    assert "处理中" in resp or "已有凭证" in resp


@pytest.mark.asyncio
async def test_voucher_outbound_explicit(gate, mock_llm):
    """用户说"出库 SO-1" → voucher_type 必须解析为 outbound 写入 payload + key。"""
    state = VoucherState(user_message="出库 SO-1", hub_user_id=1, conversation_id="c1", order_id=1)
    async def fake_executor(name, args):
        return {"order_id": 1, "status": "approved",
                "outbound_voucher_count": 0, "inbound_voucher_count": 0,
                "items": [{"product_id": 1, "qty": 10}]}
    out = await preview_voucher_node(state, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    assert out.voucher_type == "outbound"
    pending = await gate.get_pending_by_id(out.pending_action_id)
    assert pending.payload["args"]["voucher_type"] == "outbound"
    assert pending.idempotency_key == "vch:1:outbound"


@pytest.mark.asyncio
async def test_voucher_inbound_does_not_collide_with_outbound(gate, mock_llm):
    """同订单 outbound 和 inbound 是 2 个独立 pending（不同 idempotency_key）。"""
    state_out = VoucherState(user_message="出库 SO-1", hub_user_id=1, conversation_id="c1", order_id=1)
    async def fake_executor(name, args):
        return {"order_id": 1, "status": "approved",
                "outbound_voucher_count": 0, "inbound_voucher_count": 0,
                "items": [{"product_id": 1, "qty": 10}]}
    await preview_voucher_node(state_out, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    assert state_out.voucher_type == "outbound"

    state_in = VoucherState(user_message="入库 SO-1", hub_user_id=1, conversation_id="c1", order_id=1)
    await preview_voucher_node(state_in, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    assert state_in.voucher_type == "inbound"
    assert state_in.pending_action_id != state_out.pending_action_id
    pending_in = await gate.get_pending_by_id(state_in.pending_action_id)
    assert pending_in.payload["args"]["voucher_type"] == "inbound"
    assert pending_in.idempotency_key == "vch:1:inbound"


@pytest.mark.asyncio
async def test_voucher_type_unresolved_asks_user(gate, mock_llm):
    """用户没说出库 / 入库 → ask_user 问，不创建 pending。"""
    state = VoucherState(user_message="给 SO-1 出凭证", hub_user_id=1, conversation_id="c1", order_id=1)
    out = await preview_voucher_node(state, llm=mock_llm, gate=gate, tool_executor=AsyncMock())
    assert state.pending_action_id is None
    resp = out.final_response or ""
    assert "出库" in resp and "入库" in resp
    assert "voucher_type" in out.missing_fields


@pytest.mark.asyncio
async def test_voucher_two_pendings_different_orders_select_correctly(gate):
    """两个不同订单的 voucher pending — 用户选 2，只执行第 2 个 payload。"""
    p1 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1", subgraph="voucher", action_prefix="vch",
        summary="SO-1 出库", idempotency_key="vch:1:outbound",
        payload={"tool_name": "create_voucher_draft",
                 "args": {"order_id": 1, "voucher_type": "outbound", "items": [], "remark": ""}},
    )
    import asyncio
    await asyncio.sleep(0.01)
    p2 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1", subgraph="voucher", action_prefix="vch",
        summary="SO-2 出库", idempotency_key="vch:2:outbound",
        payload={"tool_name": "create_voucher_draft",
                 "args": {"order_id": 2, "voucher_type": "outbound", "items": [], "remark": ""}},
    )
    state = AgentState(user_message="2", hub_user_id=1, conversation_id="c1")
    state = await confirm_node(state, gate=gate)
    assert state.confirmed_payload["args"]["order_id"] == 2
    assert not await gate.is_claimed(p1.action_id)
