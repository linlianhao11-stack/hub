import pytest
from decimal import Decimal
from unittest.mock import AsyncMock
from fakeredis.aioredis import FakeRedis

from hub.agent.graph.state import AdjustPriceState, AgentState, CustomerInfo, ProductInfo
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
async def test_preview_writes_canonical_payload_to_pending(gate):
    """preview 把 customer_id/product_id/new_price 完整写进 pending.payload."""
    from hub.agent.graph.subgraphs.adjust_price import preview_adjust_price_node

    state = AdjustPriceState(user_message="把阿里 X1 价格调到 280", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=10, name="阿里")
    state.product = ProductInfo(id=1, name="X1")
    state.new_price = Decimal("280")
    state.old_price = Decimal("300")

    llm = AsyncMock()
    llm.chat = AsyncMock(
        return_value=type("R", (), {
            "text": "调价预览：阿里 X1 300→280",
            "tool_calls": [],
            "finish_reason": "stop",
        })()
    )

    out = await preview_adjust_price_node(state, llm=llm, gate=gate)

    pendings = await gate.list_pending_for_context(conversation_id="c1", hub_user_id=1)
    assert len(pendings) == 1
    p = pendings[0]
    assert p.payload["tool_name"] == "create_price_adjustment_request"
    assert p.payload["args"]["customer_id"] == 10
    assert p.payload["args"]["product_id"] == 1
    assert p.payload["args"]["new_price"] == 280.0
    assert p.action_id.startswith("adj-")


@pytest.mark.asyncio
async def test_two_pendings_select_first_only_executes_first_payload(gate):
    """P1-C：两个 pending，confirm 选 1，state.confirmed_payload 是 p1 的，p2 仍 pending。"""
    p1 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1", subgraph="adjust_price",
        summary="阿里 X1 → 280", action_prefix="adj",
        payload={
            "tool_name": "create_price_adjustment_request",
            "args": {"customer_id": 10, "product_id": 1, "new_price": 280.0, "reason": ""},
        },
    )
    p2 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1", subgraph="adjust_price",
        summary="百度 Y1 → 350", action_prefix="adj",
        payload={
            "tool_name": "create_price_adjustment_request",
            "args": {"customer_id": 20, "product_id": 5, "new_price": 350.0, "reason": ""},
        },
    )
    state = AgentState(user_message="1", hub_user_id=1, conversation_id="c1")
    state = await confirm_node(state, gate=gate)
    assert state.confirmed_action_id == p1.action_id
    assert state.confirmed_payload["args"]["customer_id"] == 10
    assert state.confirmed_payload["args"]["new_price"] == 280.0
    assert not await gate.is_claimed(p2.action_id)


@pytest.mark.asyncio
async def test_action_id_is_full_32_hex(gate):
    """P2-G：action_id 必须是完整 32-hex（含前缀）— 不允许 8 位。"""
    p = await gate.create_pending(
        hub_user_id=1, conversation_id="c1", subgraph="adjust_price",
        summary="x", action_prefix="adj",
        payload={"tool_name": "x", "args": {}},
    )
    assert p.action_id.startswith("adj-")
    hex_part = p.action_id.split("-", 1)[1]
    assert len(hex_part) == 32, (
        f"action_id hex 必须 32 位，实际 {len(hex_part)}: {p.action_id}"
    )


@pytest.mark.asyncio
async def test_pick_product_bridges_products_to_product():
    """v1.16 fix: resolve_products 写 state.products → pick_product 节点把 products[0] 写到 state.product。"""
    from hub.agent.graph.subgraphs.adjust_price import pick_product_from_products_node
    state = AdjustPriceState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.products = [ProductInfo(id=1, name="X1")]
    out = await pick_product_from_products_node(state)
    assert out.product is not None
    assert out.product.id == 1


@pytest.mark.asyncio
async def test_adjust_price_subgraph_includes_pick_product_node():
    """v1.16 fix: 子图必须有 pick_product 节点，否则 fetch_history 永远跳过。"""
    from hub.agent.graph.subgraphs.adjust_price import build_adjust_price_subgraph
    compiled = build_adjust_price_subgraph(llm=AsyncMock(), gate=AsyncMock(), tool_executor=AsyncMock())
    nodes = set(compiled.get_graph().nodes)
    assert "pick_product" in nodes


@pytest.mark.asyncio
async def test_commit_uses_confirmed_payload_not_state(gate):
    """commit 节点必须从 state.confirmed_payload 取参数，不能从 state.customer/product 取。"""
    from hub.agent.graph.subgraphs.adjust_price import commit_adjust_price_node

    state = AdjustPriceState(user_message="确认", hub_user_id=1, conversation_id="c1")
    # 故意把 state.customer/product/new_price 设成不同值 — commit 不应使用这些
    state.customer = CustomerInfo(id=99, name="不该用的客户")
    state.product = ProductInfo(id=99, name="不该用的产品")
    state.new_price = Decimal("999")
    # confirmed_payload 才是真正要执行的
    state.confirmed_payload = {
        "tool_name": "create_price_adjustment_request",
        "args": {"customer_id": 10, "product_id": 1, "new_price": 280.0, "reason": ""},
    }

    captured: dict = {}

    async def fake_executor(name, args):
        captured["name"] = name
        captured["args"] = args
        return {"adjust_id": 1}

    out = await commit_adjust_price_node(state, tool_executor=fake_executor)
    assert captured["name"] == "create_price_adjustment_request"
    assert captured["args"]["customer_id"] == 10   # 来自 payload
    assert captured["args"]["new_price"] == 280.0
    assert captured["args"]["customer_id"] != 99   # 不是 state.customer.id
