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


# ─────────────── review issue 2: idempotency 原子 + stale check ───────────────


@pytest.mark.asyncio
async def test_idempotency_concurrent_same_context_creates_only_one_action(gate):
    """review issue 2：并发 create_pending 同 idempotency_key 同 context 必须只创建 1 个 action。

    旧实现是 GET → 检查 → SET，并发会创建 2 个 pending（hash 里 2 个 entry）。
    新实现用 Lua 原子 check-and-set，2 个并发只有 1 个能创建。
    """
    async def worker():
        return await gate.create_pending(
            hub_user_id=1, conversation_id="c1",
            subgraph="voucher", summary="SO-1 出库",
            payload={"tool_name": "create_voucher_draft", "args": {"order_id": 1}},
            action_prefix="vch",
            idempotency_key="vch:1:outbound",
            ttl_seconds=600,
        )

    # 并发跑 5 个 worker
    results = await asyncio.gather(*[worker() for _ in range(5)])
    # 5 个返回的 action_id 应全相同（只创建 1 个）
    action_ids = {p.action_id for p in results}
    assert len(action_ids) == 1, (
        f"并发同 idempotency_key 应只创建 1 个 action，实际创建了 {len(action_ids)} 个: {action_ids}"
    )
    # 验 pending hash 只有 1 个 entry
    pendings = await gate.list_pending_for_context(conversation_id="c1", hub_user_id=1)
    assert len(pendings) == 1, f"pending hash 应只有 1 个 entry，实际 {len(pendings)}"
    assert pendings[0].action_id == results[0].action_id


@pytest.mark.asyncio
async def test_idempotency_concurrent_cross_context_one_succeeds_one_raises(gate):
    """review issue 2：并发 create_pending 同 key 跨 context 必须 1 成功 / 其他 raise。"""
    async def worker(conv: str, user: int):
        try:
            return await gate.create_pending(
                hub_user_id=user, conversation_id=conv,
                subgraph="voucher", summary="x",
                payload={"tool_name": "create_voucher_draft", "args": {"order_id": 1}},
                action_prefix="vch",
                idempotency_key="vch:1:outbound",
                ttl_seconds=600,
            )
        except Exception as e:
            return e

    # A 在 c1-private 跑 / B 在 c2-group 跑
    results = await asyncio.gather(
        worker("c1-private", 1),
        worker("c2-group", 2),
    )
    # 必须正好 1 个 PendingAction + 1 个 CrossContextIdempotency
    from hub.agent.tools.confirm_gate import CrossContextIdempotency
    pending_count = sum(1 for r in results if isinstance(r, PendingAction))
    cross_count = sum(1 for r in results if isinstance(r, CrossContextIdempotency))
    assert pending_count == 1, f"应正好 1 个并发成功，实际 {pending_count}"
    assert cross_count == 1, f"应正好 1 个并发被 CrossContextIdempotency 拒，实际 {cross_count}"


@pytest.mark.asyncio
async def test_idempotency_stale_record_is_replaced_not_returned(gate):
    """review issue 2：idempotency_key 命中但对应 action_id 已不在 pending hash（被 claim/过期/手动清）
    必须被识别为 stale，重新创建一个新的 PendingAction，而不是返回死 action_id。
    """
    # 先创建一个 pending
    p1 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="x",
        payload={"tool_name": "create_voucher_draft", "args": {"order_id": 1}},
        action_prefix="vch",
        idempotency_key="vch:1:outbound",
        ttl_seconds=600,
    )

    # 模拟 stale：从 pending hash 中删除该 action_id（保留 idempotency_key 不动）
    pending_key = gate._pending_key("c1", 1)
    await gate.redis.hdel(pending_key, p1.action_id)
    # 此时 idempotency_key 还在，但指向的 action_id 已不在 pending hash → stale

    # 再次 create_pending：应识别 stale，创建新的 action_id（不返回旧死 id）
    p2 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="x",
        payload={"tool_name": "create_voucher_draft", "args": {"order_id": 1}},
        action_prefix="vch",
        idempotency_key="vch:1:outbound",
        ttl_seconds=600,
    )
    assert p2.action_id != p1.action_id, (
        f"stale idempotency_key 应被替换；旧 {p1.action_id} 不应再返回"
    )
    # 新的 action_id 必须真在 pending hash 里
    pendings = await gate.list_pending_for_context(conversation_id="c1", hub_user_id=1)
    assert len(pendings) == 1
    assert pendings[0].action_id == p2.action_id


@pytest.mark.asyncio
async def test_idempotency_stale_cleanup_uses_cas_and_does_not_clobber_concurrent_reservation(gate):
    """review round 2 / P1：stale 删除必须 CAS（仅当当前值 == 自己读到的旧值才 DEL）。

    场景：A 协程读到 stale 旧 record；在 A DEL 之前，B 协程已经清理 stale 并成功
    SET NX 创建新 reservation；A 不能盲目 DEL B 的新 reservation 然后再创建第二份
    pending（同 idempotency_key 重复创建）。
    """
    # Step 1：先制造一份 stale 记录（A 即将读到的）
    p1 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="x",
        payload={"tool_name": "create_voucher_draft", "args": {"order_id": 7}},
        action_prefix="vch",
        idempotency_key="vch:7:outbound",
        ttl_seconds=600,
    )
    pending_key = gate._pending_key("c1", 1)
    # 让 p1 在 pending hash 中消失，但 idem_key 仍指向它（stale 状态）
    await gate.redis.hdel(pending_key, p1.action_id)

    # Step 2：A 读到 stale 旧 record（保留快照）
    idem_key = gate._idempotency_key("vch:7:outbound")
    a_seen_raw = await gate.redis.get(idem_key)
    assert a_seen_raw is not None, "前置：idem_key 必须存在 stale 旧值"

    # Step 3：B 抢先：删除 stale + SET NX 写新 reservation（模拟 B 的 create_pending 完成 stale-recreate）
    p2 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="x",
        payload={"tool_name": "create_voucher_draft", "args": {"order_id": 7}},
        action_prefix="vch",
        idempotency_key="vch:7:outbound",
        ttl_seconds=600,
    )
    assert p2.action_id != p1.action_id  # B 应已创建新的

    # Step 4：A 现在尝试 stale-cleanup 自己最初读到的那份；
    # 必须用 CAS（仅当当前值 == a_seen_raw 才 DEL）→ B 的新值不能被 A 删
    cas_deleted = await gate._cas_delete_idempotency_key(idem_key, a_seen_raw)
    assert cas_deleted is False, (
        "A 看到的 stale value 已被 B 替换，CAS DEL 必须返 False（不能误删 B 的新 reservation）"
    )

    # Step 5：B 的 reservation 仍存活；只有一个 pending
    pendings = await gate.list_pending_for_context(conversation_id="c1", hub_user_id=1)
    assert len(pendings) == 1, f"应只剩 B 的 pending，实际 {len(pendings)}"
    assert pendings[0].action_id == p2.action_id


@pytest.mark.asyncio
async def test_cas_delete_idempotency_key_succeeds_when_value_unchanged(gate):
    """review round 2 / P1：CAS 删除在值未变时正常成功（保证正常 stale 清理路径仍走得通）。"""
    p = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="x",
        payload={"tool_name": "create_voucher_draft", "args": {"order_id": 99}},
        action_prefix="vch",
        idempotency_key="vch:99:outbound",
        ttl_seconds=600,
    )
    idem_key = gate._idempotency_key("vch:99:outbound")
    cur = await gate.redis.get(idem_key)
    assert cur is not None
    deleted = await gate._cas_delete_idempotency_key(idem_key, cur)
    assert deleted is True
    assert await gate.redis.get(idem_key) is None
