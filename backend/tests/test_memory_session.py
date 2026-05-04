"""Plan 6 Task 4 + v8 review #19：SessionMemory 测试，per-user 隔离。"""
from __future__ import annotations

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from hub.agent.memory.session import SessionMemory

REDIS_URL = "redis://localhost:6380/0"
TEST_CONV_PREFIX = "hub:agent:conv:test-session-"

# 测试默认 user
USR = 1


@pytest_asyncio.fixture
async def redis_client():
    """测试用 Redis 连接，测试结束后清理 test- 开头的 key。"""
    client = Redis.from_url(REDIS_URL, decode_responses=False)
    yield client
    keys = await client.keys(f"{TEST_CONV_PREFIX}*")
    if keys:
        await client.delete(*keys)
    await client.aclose()


@pytest_asyncio.fixture
def session(redis_client):
    return SessionMemory(redis_client)


def _conv_id(suffix: str) -> str:
    return f"test-session-{suffix}"


# ==================== 基础持久化（per-user 改造后）====================

@pytest.mark.asyncio
async def test_append_message_persists_with_ttl(session, redis_client):
    """append 后 lrange 拿得到，ttl 约 1800s。"""
    cid = _conv_id("append-ttl")
    await session.append(cid, USR, role="user", content="你好")

    msgs_key = f"hub:agent:conv:{cid}:{USR}:msgs"
    raw = await redis_client.lrange(msgs_key, 0, -1)
    assert len(raw) == 1
    import json
    msg = json.loads(raw[0])
    assert msg["role"] == "user"
    assert msg["content"] == "你好"
    assert msg["tool_call_id"] is None

    ttl = await redis_client.ttl(msgs_key)
    assert 1790 <= ttl <= 1800


@pytest.mark.asyncio
async def test_add_entity_refs_writes_to_redis_sets(session, redis_client):
    """add_entity_refs 后 smembers 含 customer_id/product_id。"""
    cid = _conv_id("entity-refs")
    await session.add_entity_refs(cid, USR, customer_ids={1, 2}, product_ids={10})

    ck = f"hub:agent:conv:{cid}:{USR}:refs:customers"
    pk = f"hub:agent:conv:{cid}:{USR}:refs:products"
    c_members = await redis_client.smembers(ck)
    p_members = await redis_client.smembers(pk)

    assert {int(x) for x in c_members} == {1, 2}
    assert {int(x) for x in p_members} == {10}


@pytest.mark.asyncio
async def test_add_entity_refs_resets_ttl(session, redis_client):
    """多次 add_entity_refs 后 TTL 仍然接近 1800。"""
    cid = _conv_id("refs-ttl")
    await session.add_entity_refs(cid, USR, customer_ids={1})
    await session.add_entity_refs(cid, USR, customer_ids={2})

    ck = f"hub:agent:conv:{cid}:{USR}:refs:customers"
    ttl = await redis_client.ttl(ck)
    assert 1790 <= ttl <= 1800


@pytest.mark.asyncio
async def test_load_returns_full_history_with_refs(session, redis_client):
    """append 多条 + add refs → load() 返完整 ConversationHistory。"""
    cid = _conv_id("load-full")
    await session.append(cid, USR, role="user", content="查一下客户 1")
    await session.append(cid, USR, role="assistant", content="找到了")
    await session.append(cid, USR, role="tool", content="结果数据", tool_call_id="call-123")
    await session.add_entity_refs(cid, USR, customer_ids={1}, product_ids={5, 6})

    history = await session.load(cid, USR)
    assert history.conversation_id == cid
    assert len(history.messages) == 3
    assert history.messages[0].role == "user"
    assert history.messages[2].tool_call_id == "call-123"
    assert history.customer_ids == {1}
    assert history.product_ids == {5, 6}


@pytest.mark.asyncio
async def test_append_handles_special_chars(session, redis_client):
    """M7: 中文 / emoji / 转义引号 / 控制字符都能正确 roundtrip。"""
    cid = _conv_id("special-chars")
    weird = '中文 + emoji 🎉 + 换行\\n + "引号" + \\u0001 控制字符'
    await session.append(cid, USR, role="user", content=weird)
    history = await session.load(cid, USR)
    assert history.messages[0].content == weird


@pytest.mark.asyncio
async def test_clear_removes_all_keys(session, redis_client):
    """clear(conv, user) 后 load 返空历史和空引用。"""
    cid = _conv_id("clear")
    await session.append(cid, USR, role="user", content="hello")
    await session.add_entity_refs(cid, USR, customer_ids={99})

    await session.clear(cid, USR)

    history = await session.load(cid, USR)
    assert history.messages == []
    assert history.customer_ids == set()
    assert history.product_ids == set()


# ==================== v8 staging review #19: msgs/refs per-user 隔离 ====================

@pytest.mark.asyncio
async def test_msgs_per_user_isolation(session):
    """**核心安全测试** v8 review #19：群聊同 conv_id 不同用户的对话历史不串。

    模拟群聊：A 用户（id=10）和 B 用户（id=20）在同一钉钉群聊里各自跟 bot 对话。
    A 跟 bot 谈翼蓝合同；B 跟 bot 谈得帆合同。
    A 下一轮 load 必须只拿到自己的对话历史，**不能**看到 B 跟 bot 的对话。
    """
    cid = _conv_id("msgs-isolation")
    await session.append(cid, hub_user_id=10, role="user", content="给翼蓝做合同")
    await session.append(cid, hub_user_id=10, role="assistant", content="翼蓝合同已生成")
    await session.append(cid, hub_user_id=20, role="user", content="给得帆做合同")
    await session.append(cid, hub_user_id=20, role="assistant", content="得帆合同已生成")

    h_a = await session.load(cid, hub_user_id=10)
    h_b = await session.load(cid, hub_user_id=20)

    assert len(h_a.messages) == 2
    assert "翼蓝" in h_a.messages[0].content
    assert "翼蓝" in h_a.messages[1].content
    # **关键安全断言**：A 看不到 B 的对话
    for m in h_a.messages:
        assert "得帆" not in m.content, "A 不应看到 B 跟 bot 的对话"

    assert len(h_b.messages) == 2
    assert "得帆" in h_b.messages[0].content
    for m in h_b.messages:
        assert "翼蓝" not in m.content, "B 不应看到 A 跟 bot 的对话"


@pytest.mark.asyncio
async def test_refs_per_user_isolation(session):
    """**核心安全测试** v8 review #19：群聊场景 entity refs 不串。

    A 查过翼蓝（id=7）+ X5 Pro（id=5030）；B 查过得帆（id=11）+ 翻译耳机（id=5032）。
    各自 get_entity_refs 只看到自己查过的，不能看到对方的。MemoryLoader 据此
    决定加载哪些 customer_memory / product_memory，所以这层隔离也很重要。
    """
    cid = _conv_id("refs-isolation")
    await session.add_entity_refs(cid, hub_user_id=10, customer_ids={7}, product_ids={5030})
    await session.add_entity_refs(cid, hub_user_id=20, customer_ids={11}, product_ids={5032})

    refs_a = await session.get_entity_refs(cid, hub_user_id=10)
    refs_b = await session.get_entity_refs(cid, hub_user_id=20)

    assert refs_a.customer_ids == {7}
    assert refs_a.product_ids == {5030}
    # **关键安全断言**：A 看不到 B 的 refs
    assert 11 not in refs_a.customer_ids, "A 不应看到 B 查过的客户 11（得帆）"
    assert 5032 not in refs_a.product_ids, "A 不应看到 B 查过的商品 5032（翻译耳机）"

    assert refs_b.customer_ids == {11}
    assert refs_b.product_ids == {5032}
    assert 7 not in refs_b.customer_ids, "B 不应看到 A 查过的客户 7（翼蓝）"
    assert 5030 not in refs_b.product_ids, "B 不应看到 A 查过的商品 5030（X5 Pro）"


@pytest.mark.asyncio
async def test_clear_with_user_only_clears_that_user(session, redis_client):
    """clear(cid, hub_user_id=N) 只清这个 user 的 key，不影响其他 user。"""
    cid = _conv_id("clear-per-user")
    await session.append(cid, 10, role="user", content="A says hi")
    await session.append(cid, 20, role="user", content="B says hi")
    await session.add_entity_refs(cid, 10, customer_ids={7})
    await session.add_entity_refs(cid, 20, customer_ids={11})

    await session.clear(cid, hub_user_id=10)

    # A 的全清
    h_a = await session.load(cid, 10)
    assert h_a.messages == []
    assert h_a.customer_ids == set()
    # B 的不动
    h_b = await session.load(cid, 20)
    assert len(h_b.messages) == 1
    assert h_b.customer_ids == {11}


@pytest.mark.asyncio
async def test_clear_without_user_clears_all(session, redis_client):
    """clear(cid) 不传 hub_user_id 时清整个 conv 下所有 user 的 key（admin 重置）。"""
    cid = _conv_id("clear-all")
    await session.append(cid, 10, role="user", content="A")
    await session.append(cid, 20, role="user", content="B")
    await session.add_entity_refs(cid, 10, customer_ids={7})
    await session.set_round_state(cid, 30, {"x": 1})

    await session.clear(cid)  # 不传 hub_user_id

    # 全空
    h_a = await session.load(cid, 10)
    assert h_a.messages == []
    h_b = await session.load(cid, 20)
    assert h_b.messages == []
    rs = await session.get_round_state(cid, 30)
    assert rs is None


# ==================== v8 staging review #16: round_state per-user 隔离 ====================

@pytest.mark.asyncio
async def test_round_state_set_get_roundtrip(session):
    """set_round_state 写入后，同 (conv, user) get 能取回。"""
    cid = _conv_id("rs-roundtrip")
    state = {
        "customers_seen": [{"id": 7, "name": "翼蓝"}],
        "products_seen": [{"id": 5030, "name": "X5 Pro"}],
        "last_intent": {"tool": "generate_contract_draft"},
    }
    await session.set_round_state(cid, hub_user_id=1, state=state)
    got = await session.get_round_state(cid, hub_user_id=1)
    assert got == state


@pytest.mark.asyncio
async def test_round_state_per_user_isolation(session):
    """**核心安全测试** v8 review #16：round_state 群聊不串。"""
    cid = _conv_id("rs-per-user")
    state_a = {
        "customers_seen": [{"id": 7, "name": "北京翼蓝科技发展有限公司"}],
        "products_seen": [{"id": 5030, "name": "X5 Pro"}],
        "last_intent": {"tool": "generate_contract_draft",
                        "args": {"customer_id": 7, "items": [{"product_id": 5030}]}},
    }
    state_b = {
        "customers_seen": [{"id": 11, "name": "广州市得帆计算机科技有限公司"}],
        "products_seen": [{"id": 5032, "name": "讯飞 AI 翻译耳机"}],
        "last_intent": {"tool": "generate_contract_draft",
                        "args": {"customer_id": 11, "items": [{"product_id": 5032}]}},
    }
    await session.set_round_state(cid, hub_user_id=10, state=state_a)
    await session.set_round_state(cid, hub_user_id=20, state=state_b)

    got_a = await session.get_round_state(cid, hub_user_id=10)
    got_b = await session.get_round_state(cid, hub_user_id=20)
    assert got_a == state_a
    assert got_b == state_b

    a_cust_ids = {c["id"] for c in got_a.get("customers_seen", [])}
    a_prod_ids = {p["id"] for p in got_a.get("products_seen", [])}
    assert 11 not in a_cust_ids
    assert 5032 not in a_prod_ids

    b_cust_ids = {c["id"] for c in got_b.get("customers_seen", [])}
    b_prod_ids = {p["id"] for p in got_b.get("products_seen", [])}
    assert 7 not in b_cust_ids
    assert 5030 not in b_prod_ids

    got_c = await session.get_round_state(cid, hub_user_id=99)
    assert got_c is None


@pytest.mark.asyncio
async def test_round_state_clear_removes_all_users(session, redis_client):
    """clear(cid) 不传 user 时应该清掉所有 user 的 round_state（scan 模糊删）。"""
    cid = _conv_id("rs-clear")
    await session.set_round_state(cid, 1, {"a": 1})
    await session.set_round_state(cid, 2, {"b": 2})
    await session.set_round_state(cid, 3, {"c": 3})

    keys = await redis_client.keys(f"hub:agent:conv:{cid}:*:round_state")
    assert len(keys) == 3

    await session.clear(cid)

    keys = await redis_client.keys(f"hub:agent:conv:{cid}:*:round_state")
    assert len(keys) == 0


@pytest.mark.asyncio
async def test_round_state_empty_state_skipped(session):
    """空 state（{}）不写入。"""
    cid = _conv_id("rs-empty")
    await session.set_round_state(cid, 1, {})
    got = await session.get_round_state(cid, 1)
    assert got is None
