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
async def test_idempotency_create_is_atomic_transaction_post_state_consistent(gate):
    """review round 3 / P1：idem_key SET 和 pending HSET 必须是同一原子事务（WATCH/MULTI/EXEC）。

    旧实现先 SET NX 再 HSET：T1 SET 后 yield → T2 GET 看到 T1 idem 但 HEXISTS pending 为
    False（T1 还没 HSET）→ T2 把 T1 当 stale 删掉 → T2 自己 SET 成功 → T1 继续 HSET → 双 pending。
    新实现：SET + HSET + EXPIRE 在 WATCH/MULTI/EXEC 内原子提交，不存在中间状态。

    本测试验证后置不变量：任意时刻 idem_key 指向的 action_id 必须真在 pending hash 里
    （即"reservation"和"实际 pending"必须同生同灭）。
    """
    # 跑 5 个并发 worker（同 context 同 idempotency_key）
    async def worker():
        return await gate.create_pending(
            hub_user_id=1, conversation_id="c1",
            subgraph="voucher", summary="x",
            payload={"tool_name": "create_voucher_draft", "args": {"order_id": 11}},
            action_prefix="vch",
            idempotency_key="vch:11:outbound",
            ttl_seconds=600,
        )

    results = await asyncio.gather(*[worker() for _ in range(5)])

    # 全部返同一个 action_id
    action_ids = {p.action_id for p in results}
    assert len(action_ids) == 1, (
        f"5 concurrent same-context creates 应该只创建 1 个 action，实际 {action_ids}"
    )

    # 关键不变量：idem_key 指向的 action_id 必须真在 pending hash 里
    import json
    idem_key = gate._idempotency_key("vch:11:outbound")
    idem_raw = await gate.redis.get(idem_key)
    assert idem_raw is not None
    idem_record = json.loads(idem_raw)
    pending_key = gate._pending_key("c1", 1)
    assert await gate.redis.hexists(pending_key, idem_record["action_id"]), (
        "idem_key 指向的 action_id 必须真在 pending hash 里 "
        "（原子事务保证 reservation 和 pending 同生同灭）"
    )

    # pending hash 总数必须只有 1 条
    pendings = await gate.list_pending_for_context(conversation_id="c1", hub_user_id=1)
    assert len(pendings) == 1


@pytest.mark.asyncio
async def test_idempotency_no_double_pending_when_yield_between_set_and_hset(gate, monkeypatch):
    """review round 3 / P1：强制 yield 在 idem set 与 pending hset 之间也不能双创。

    旧实现：每个 redis 操作都是独立 await，T1 SET idem 之后 yield → T2 进入 create_pending →
    T2 SET NX 失败 → GET 见 T1 idem → HEXISTS pending T1.aid 为 False（T1 还没 HSET）→
    判 stale → DEL idem → T2 SET 成功 → T2 HSET → T1 continues HSET → pending 双条。

    新实现：把 reservation + HSET + EXPIRE 全放 WATCH/MULTI/EXEC，T1 commit 之前 idem
    根本不可观察；T2 WATCH+GET 要么看到 None（T1 未 commit）要么看到 T1 record（T1 已
    commit 且 pending HSET 也已生效），不存在中间窗口。

    通过 monkeypatch 在第一次 redis.hset 调用前强制 yield 多轮，给另一个 worker 跑完整
    create_pending 的机会。新实现下：(a) pipeline 路径根本不调顶层 redis.hset；(b) 即便
    调到，原子事务内的状态对其他 task 也不可观察。
    """
    real_hset = gate.redis.hset
    hset_yielded = {"done": False}

    async def slow_hset(*args, **kwargs):
        if not hset_yielded["done"]:
            hset_yielded["done"] = True
            # 多次 yield 让另一个 task 完整跑一遍 create_pending
            for _ in range(8):
                await asyncio.sleep(0)
        return await real_hset(*args, **kwargs)

    monkeypatch.setattr(gate.redis, "hset", slow_hset)

    async def worker():
        return await gate.create_pending(
            hub_user_id=1, conversation_id="c1",
            subgraph="voucher", summary="x",
            payload={"tool_name": "create_voucher_draft", "args": {"order_id": 12}},
            action_prefix="vch",
            idempotency_key="vch:12:outbound",
            ttl_seconds=600,
        )

    # 双 worker 并发，强制 yield 让交错发生
    results = await asyncio.gather(worker(), worker())
    action_ids = {p.action_id for p in results}
    assert len(action_ids) == 1, (
        f"强制 yield 后并发双 worker 仍应只产生 1 个 action，实际 {action_ids}"
    )

    pendings = await gate.list_pending_for_context(conversation_id="c1", hub_user_id=1)
    assert len(pendings) == 1, (
        f"pending hash 应只有 1 条（不能有 SET+HSET 中间窗口造成的双 pending），实际 {len(pendings)}"
    )


@pytest.mark.asyncio
async def test_idempotency_expired_pending_treated_as_stale_not_reused(gate):
    """review round 4 / P1：pending action 逻辑过期后必须被识别为 stale。

    旧实现：is_alive 仅 HEXISTS pending hash，不检查 PendingAction.is_expired() →
    短 ttl_seconds 的 action 物理 TTL 由 max(ttl_seconds, self.TTL) 撑长，hash 里
    还在 → 同 idempotency_key 新请求会复用过期 action_id（死 action 给用户）。
    新实现：is_alive 检查 HEXISTS + is_expired() 双重，过期 → 走 stale 重建。
    """
    p1 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="x",
        payload={"tool_name": "create_voucher_draft", "args": {"order_id": 1}},
        action_prefix="vch",
        idempotency_key="vch:expire-test:outbound",
        ttl_seconds=1,  # 极短 — 1s 后逻辑过期
    )
    # 等 p1 逻辑过期（is_expired() True）
    await asyncio.sleep(1.2)

    # 同 idempotency_key 再来 — 必须识别为 stale 重建（不复用 p1）
    p2 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="x",
        payload={"tool_name": "create_voucher_draft", "args": {"order_id": 1}},
        action_prefix="vch",
        idempotency_key="vch:expire-test:outbound",
        ttl_seconds=600,  # 重新长 TTL
    )
    assert p2.action_id != p1.action_id, (
        f"过期 action 必须被替换，不能复用；旧 {p1.action_id}, 新 {p2.action_id}"
    )


@pytest.mark.asyncio
async def test_list_pending_for_context_filters_and_gcs_expired(gate):
    """review round 4 / P1：list_pending_for_context 必须过滤过期 action 并清理。

    旧实现：直接返回 hash 中所有 entry，过期 action 也会被列出，confirm_node 会
    把过期 action 列在 "您有以下待确认操作"，用户选 "1" claim → 死 action。
    新实现：过滤 is_expired()，并 HDEL 清理过期项（lazy GC）。
    """
    p_short = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="short",
        payload={"tool_name": "x", "args": {"order_id": 1}},
        action_prefix="vch",
        ttl_seconds=1,
    )
    p_long = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_price", summary="long",
        payload={"tool_name": "x", "args": {"customer_id": 1}},
        action_prefix="adj",
        ttl_seconds=600,
    )
    await asyncio.sleep(1.2)

    pendings = await gate.list_pending_for_context(conversation_id="c1", hub_user_id=1)
    aids = {p.action_id for p in pendings}
    assert p_short.action_id not in aids, "过期 action 必须被过滤"
    assert p_long.action_id in aids, "未过期 action 应保留"

    # GC：过期 entry 应已被 HDEL 清理
    pending_key = gate._pending_key("c1", 1)
    raw = await gate.redis.hgetall(pending_key)
    keys_after = {(k.decode() if isinstance(k, bytes) else k) for k in (raw or {}).keys()}
    assert p_short.action_id not in keys_after, (
        "过期 action 应被 lazy GC 清理出 hash"
    )
    assert p_long.action_id in keys_after


@pytest.mark.asyncio
async def test_claim_rejects_expired_pending(gate):
    """review round 4 / P1：claim 已逻辑过期的 pending 必须被拒绝（不可执行写操作）。"""
    p = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="x",
        payload={"tool_name": "create_voucher_draft", "args": {"order_id": 1}},
        action_prefix="vch",
        ttl_seconds=1,
    )
    await asyncio.sleep(1.2)

    with pytest.raises(CrossContextClaim, match="过期|expired"):
        await gate.claim(
            action_id=p.action_id, token=p.token,
            hub_user_id=1, conversation_id="c1",
        )

    # 过期 entry 应已被清理
    pending_key = gate._pending_key("c1", 1)
    assert not await gate.redis.hexists(pending_key, p.action_id)


@pytest.mark.asyncio
async def test_idempotency_key_ttl_matches_pending_physical_ttl(gate):
    """review round 4 / P1：idem_key TTL 不应短于 pending 物理 hash TTL，
    否则 idem 先过期 → 同 key 新请求时旧 action 仍在物理 hash 里但 idem 已消失
    → 走"创建"路径而不是 stale 替换路径 → 双创。
    """
    await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="x",
        payload={"tool_name": "x", "args": {"order_id": 1}},
        action_prefix="vch",
        idempotency_key="vch:ttl-sync-test:outbound",
        ttl_seconds=600,  # 比 self.TTL (1800) 短
    )

    idem_key = gate._idempotency_key("vch:ttl-sync-test:outbound")
    pending_key = gate._pending_key("c1", 1)
    idem_ttl = await gate.redis.ttl(idem_key)
    pending_ttl = await gate.redis.ttl(pending_key)
    assert idem_ttl >= pending_ttl - 2, (  # -2 容忍 round-off
        f"idem TTL ({idem_ttl}s) 必须 >= pending 物理 TTL ({pending_ttl}s)；"
        f"idem 不能比 pending 先过期，否则同 key 新请求会绕过 stale 检查"
    )


@pytest.mark.asyncio
async def test_pending_hash_ttl_only_extends_never_shrinks_with_idempotency(gate):
    """review round 5 / P1：同会话先长 TTL voucher 再短 TTL adjust_price，
    pending_key 物理 TTL 必须只延长不缩短。

    旧实现：每次 create_pending 都 EXPIRE pending_key max_pending_ttl，
    后建的短 TTL action 会把整个 hash key 的 TTL 缩短到 max(short_ttl, self.TTL=1800)，
    长 TTL voucher 物理过期后旧 idem 还活着 → 同 idempotency_key 新请求把它当
    stale 删除并重新创建 → 同订单同类型双 pending，破坏 12h 幂等承诺。

    新实现：用 `EXPIRE ... GT` 只在新 TTL 大于现有 TTL 时更新；
    多 action 在同一 hash 时长 TTL 永远不被短 TTL 覆盖。
    """
    # 1. 长 TTL voucher (12h)
    await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="SO-1 出库",
        payload={"tool_name": "create_voucher_draft", "args": {"order_id": 1}},
        action_prefix="vch",
        idempotency_key="vch:1:outbound",
        ttl_seconds=43200,  # 12h
    )
    pending_key = gate._pending_key("c1", 1)
    long_ttl = await gate.redis.ttl(pending_key)
    assert long_ttl >= 43000, f"voucher 创建后 hash TTL 应≥43000s，实际 {long_ttl}"

    # 2. 同会话再来一个短 TTL adjust_price (10min)
    await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_price", summary="阿里 X1 → 280",
        payload={"tool_name": "create_price_adjustment_request",
                 "args": {"customer_id": 10, "product_id": 1, "new_price": 280.0}},
        action_prefix="adj",
        idempotency_key="adj:c1:1:1:10:1:280",
        ttl_seconds=600,  # 10min；max_pending_ttl 取 max(600, 1800)=1800s
    )

    # 关键断言：hash TTL 不被缩短
    after_ttl = await gate.redis.ttl(pending_key)
    assert after_ttl >= 43000, (
        f"短 TTL action 不能缩短 voucher 长 TTL：voucher 12h 后建短 adjust_price 10min，"
        f"hash TTL 现在 {after_ttl}s（应仍 ≥43000s）"
    )


@pytest.mark.asyncio
async def test_voucher_idempotency_survives_short_ttl_neighbor_in_same_conv(gate):
    """review round 5 / P1：voucher 12h 幂等承诺不被同会话短 TTL 邻居破坏（端到端）。

    模拟用户行为：
      t=0: create voucher pending (12h)
      t≈0: 同会话 create adjust_price pending (10min)
      若 hash TTL 被缩短到 30min，voucher 物理 entry 在 30min 后消失
      但 voucher idem_key 仍活 12h → 同订单同类型新请求会"恢复"创建 → 双 pending
    """
    # 长 TTL voucher
    p1_voucher = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="SO-1 出库",
        payload={"tool_name": "create_voucher_draft", "args": {"order_id": 1}},
        action_prefix="vch",
        idempotency_key="vch:1:outbound",
        ttl_seconds=43200,
    )
    # 短 TTL adjust_price
    await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_price", summary="x",
        payload={"tool_name": "x", "args": {}},
        action_prefix="adj",
        idempotency_key="adj:c1:1:1:1:1:1",
        ttl_seconds=600,
    )

    # voucher pending 物理 TTL 应仍接近 12h（不被 adjust_price 缩短）
    pending_key = gate._pending_key("c1", 1)
    after_ttl = await gate.redis.ttl(pending_key)
    assert after_ttl >= 43000, (
        f"voucher 12h 幂等承诺被破坏：hash TTL={after_ttl}s（应仍接近 43200s）"
    )

    # 同 idempotency_key 重新发起 → 必须复用 p1_voucher（不是创建新的）
    p2_voucher = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="SO-1 出库",
        payload={"tool_name": "create_voucher_draft", "args": {"order_id": 1}},
        action_prefix="vch",
        idempotency_key="vch:1:outbound",
        ttl_seconds=43200,
    )
    assert p2_voucher.action_id == p1_voucher.action_id, (
        f"voucher idempotency 必须复用同一 action_id；旧 {p1_voucher.action_id} 新 {p2_voucher.action_id}"
    )


@pytest.mark.asyncio
async def test_create_pending_record_no_idempotency_path_also_extends_only(gate):
    """review round 5 / P1：无幂等路径（_create_pending_record）的 EXPIRE 也必须只延长。

    没传 idempotency_key 时走 _create_pending_record；同样 EXPIRE pending_key 不能
    把已有的长 TTL hash 缩短。
    """
    # 1. 同会话先有一个长 TTL（通过 idem 路径建）
    await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="x",
        payload={"tool_name": "x", "args": {"order_id": 1}},
        action_prefix="vch",
        idempotency_key="vch:1:outbound",
        ttl_seconds=43200,
    )
    pending_key = gate._pending_key("c1", 1)
    assert await gate.redis.ttl(pending_key) >= 43000

    # 2. 同会话用无 idempotency_key 路径建短 TTL action
    await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_price", summary="x",
        payload={"tool_name": "x", "args": {}},
        action_prefix="adj",
        ttl_seconds=600,  # 短
        # 不传 idempotency_key → 走 _create_pending_record
    )
    after_ttl = await gate.redis.ttl(pending_key)
    assert after_ttl >= 43000, (
        f"_create_pending_record 路径也应只延长 hash TTL，不缩短；实际 {after_ttl}s"
    )


@pytest.mark.asyncio
async def test_set_or_extend_pending_ttl_helper_three_cases(gate):
    """review round 6 / P2-1：_set_or_extend_pending_ttl 必须正确处理 3 种情况：
    1. 无 TTL key → NX 兜底设置 TTL（真 Redis EXPIRE GT 单独在无 TTL key 上是 no-op）
    2. 已有 TTL 但更短 → GT 延长到目标
    3. 已有 TTL 且更长 → 不动（不缩短）
    """
    # Case 1：无 TTL → 应被设置
    await gate.redis.hset("hub:agent:test:no_ttl", "f", "v")
    assert await gate.redis.ttl("hub:agent:test:no_ttl") == -1, "前置：应无 TTL"
    await gate._set_or_extend_pending_ttl("hub:agent:test:no_ttl", 600)
    ttl_after = await gate.redis.ttl("hub:agent:test:no_ttl")
    assert ttl_after > 0, f"NX 应为无 TTL key 设置 TTL，实际 ttl={ttl_after}"
    assert ttl_after <= 600

    # Case 2：已有较短 TTL → 延长
    await gate.redis.hset("hub:agent:test:short_ttl", "f", "v")
    await gate.redis.expire("hub:agent:test:short_ttl", 60)
    await gate._set_or_extend_pending_ttl("hub:agent:test:short_ttl", 1200)
    assert await gate.redis.ttl("hub:agent:test:short_ttl") > 60

    # Case 3：已有更长 TTL → 不缩短
    await gate.redis.hset("hub:agent:test:long_ttl", "f", "v")
    await gate.redis.expire("hub:agent:test:long_ttl", 7200)
    await gate._set_or_extend_pending_ttl("hub:agent:test:long_ttl", 600)
    ttl_long = await gate.redis.ttl("hub:agent:test:long_ttl")
    assert ttl_long > 600, f"已有更长 TTL 不应被缩短，实际 ttl={ttl_long}"


@pytest.mark.asyncio
async def test_create_pending_sets_ttl_via_nx_path_for_fresh_hash(gate, monkeypatch):
    """review round 6 / P2-1：模拟真 Redis "GT 在无 TTL key 上 no-op"，
    create_pending 必须仍能给 fresh pending hash 设置 TTL（靠 NX 兜底）。

    fakeredis 默认与真 Redis 不一致 — fakeredis 的 GT 在无 TTL key 上会"成功"设置 TTL；
    真 Redis 把无 TTL 当无穷大，GT 是 no-op。本测试 monkeypatch redis.expire 让
    gt=True 在无 TTL key 上变 no-op（模拟真 Redis），验证创建后 hash 仍有 TTL。
    """
    real_expire = gate.redis.expire
    expire_calls: list[dict] = []

    async def realistic_expire(key, seconds, *args, **kwargs):
        nx = kwargs.get("nx", False)
        gt = kwargs.get("gt", False)
        # 模拟真 Redis：GT 在无 TTL key 上 no-op
        current_ttl = await gate.redis.ttl(key)
        expire_calls.append({"key": key, "seconds": seconds, "nx": nx, "gt": gt, "ttl_before": current_ttl})
        if gt and current_ttl == -1:
            return False  # 真 Redis 行为：no-op
        return await real_expire(key, seconds, *args, **kwargs)

    monkeypatch.setattr(gate.redis, "expire", realistic_expire)

    # Fresh create_pending — 应通过 NX 路径（不仅仅 GT）确保 TTL 设置
    await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="voucher", summary="x",
        payload={"tool_name": "x", "args": {}},
        action_prefix="vch",
        ttl_seconds=600,
    )
    pending_key = gate._pending_key("c1", 1)
    ttl = await gate.redis.ttl(pending_key)
    assert ttl > 0, (
        f"create_pending 必须给 fresh pending hash 设置 TTL（即便 GT 在真 Redis 上 no-op）；"
        f"实际 ttl={ttl}（-1 = 无 TTL，意味着 hash 永不过期，会泄漏内存）。"
        f"调用记录：{expire_calls}"
    )


@pytest.mark.asyncio
async def test_compute_restore_pending_ttl_honors_record_ttl_seconds(gate):
    """review round 6 / P2-2：restore_action 用的 pending TTL 必须从 record.ttl_seconds 解析，
    至少为 max(record_ttl, self.TTL)，不能写死 self.TTL（30min）让 12h voucher 缩短。
    """
    import json

    # 12h voucher record → 应返 43200
    voucher_raw = json.dumps({
        "action_id": "vch-x", "ttl_seconds": 43200,
        "conversation_id": "c1", "hub_user_id": 1,
        "subgraph": "voucher", "summary": "x", "payload": {},
        "created_at": "2026-05-03T00:00:00+00:00",
    }, ensure_ascii=False)
    assert gate._compute_restore_pending_ttl(voucher_raw) == 43200

    # 短 TTL record (10min) → 至少 self.TTL (30min)
    short_raw = json.dumps({"ttl_seconds": 600})
    assert gate._compute_restore_pending_ttl(short_raw) == gate.TTL  # 1800

    # 空 / None → self.TTL 兜底
    assert gate._compute_restore_pending_ttl(None) == gate.TTL
    assert gate._compute_restore_pending_ttl("") == gate.TTL

    # 损坏 JSON → self.TTL 兜底（不抛错）
    assert gate._compute_restore_pending_ttl("not-json") == gate.TTL


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
