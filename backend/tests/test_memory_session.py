"""Plan 6 Task 4：SessionMemory 测试（5 case，用真 redis:6380）。"""
from __future__ import annotations

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from hub.agent.memory.session import SessionMemory

REDIS_URL = "redis://localhost:6380/0"
TEST_CONV_PREFIX = "hub:agent:conv:test-session-"


@pytest_asyncio.fixture
async def redis_client():
    """测试用 Redis 连接，测试结束后清理 test- 开头的 key。"""
    client = Redis.from_url(REDIS_URL, decode_responses=False)
    yield client
    # 清理所有测试 key
    keys = await client.keys(f"{TEST_CONV_PREFIX}*")
    if keys:
        await client.delete(*keys)
    await client.aclose()


@pytest_asyncio.fixture
def session(redis_client):
    return SessionMemory(redis_client)


def _conv_id(suffix: str) -> str:
    return f"test-session-{suffix}"


@pytest.mark.asyncio
async def test_append_message_persists_with_ttl(session, redis_client):
    """append 后 lrange 拿得到，ttl 约 1800s。"""
    cid = _conv_id("append-ttl")
    await session.append(cid, role="user", content="你好")

    msgs_key = f"hub:agent:conv:{cid}:msgs"
    raw = await redis_client.lrange(msgs_key, 0, -1)
    assert len(raw) == 1
    import json
    msg = json.loads(raw[0])
    assert msg["role"] == "user"
    assert msg["content"] == "你好"
    assert msg["tool_call_id"] is None

    ttl = await redis_client.ttl(msgs_key)
    # TTL 应该在 1790 ~ 1800 之间
    assert 1790 <= ttl <= 1800


@pytest.mark.asyncio
async def test_add_entity_refs_writes_to_redis_sets(session, redis_client):
    """add_entity_refs 后 smembers 含 customer_id/product_id。"""
    cid = _conv_id("entity-refs")
    await session.add_entity_refs(cid, customer_ids={1, 2}, product_ids={10})

    ck = f"hub:agent:conv:{cid}:refs:customers"
    pk = f"hub:agent:conv:{cid}:refs:products"
    c_members = await redis_client.smembers(ck)
    p_members = await redis_client.smembers(pk)

    assert {int(x) for x in c_members} == {1, 2}
    assert {int(x) for x in p_members} == {10}


@pytest.mark.asyncio
async def test_add_entity_refs_resets_ttl(session, redis_client):
    """多次 add_entity_refs 后 TTL 仍然接近 1800。"""
    cid = _conv_id("refs-ttl")
    await session.add_entity_refs(cid, customer_ids={1})
    await session.add_entity_refs(cid, customer_ids={2})

    ck = f"hub:agent:conv:{cid}:refs:customers"
    ttl = await redis_client.ttl(ck)
    assert 1790 <= ttl <= 1800


@pytest.mark.asyncio
async def test_load_returns_full_history_with_refs(session, redis_client):
    """append 多条 + add refs → load() 返完整 ConversationHistory。"""
    cid = _conv_id("load-full")
    await session.append(cid, role="user", content="查一下客户 1")
    await session.append(cid, role="assistant", content="找到了")
    await session.append(cid, role="tool", content="结果数据", tool_call_id="call-123")
    await session.add_entity_refs(cid, customer_ids={1}, product_ids={5, 6})

    history = await session.load(cid)
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
    await session.append(cid, role="user", content=weird)
    history = await session.load(cid)
    assert history.messages[0].content == weird


@pytest.mark.asyncio
async def test_clear_removes_all_keys(session, redis_client):
    """clear 后 load 返空历史和空引用。"""
    cid = _conv_id("clear")
    await session.append(cid, role="user", content="hello")
    await session.add_entity_refs(cid, customer_ids={99})

    await session.clear(cid)

    history = await session.load(cid)
    assert history.messages == []
    assert history.customer_ids == set()
    assert history.product_ids == set()


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
    """**核心安全测试** v8 review #16：群聊场景同 conv_id 不同 hub_user_id 不互相注入。

    模拟：A 用户（hub_user_id=10）和 B 用户（hub_user_id=20）在同一群聊
    （同一 conversation_id）各自跟 agent 对话；
    A 上轮做了"翼蓝合同 X5 Pro 3900"，B 上轮做了"得帆合同翻译耳机 2000"。
    B 这一轮 get_round_state 必须**只**拿到 B 自己的状态，**不能**看到 A 的客户。
    """
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

    # A 拿 A 的，B 拿 B 的
    got_a = await session.get_round_state(cid, hub_user_id=10)
    got_b = await session.get_round_state(cid, hub_user_id=20)
    assert got_a == state_a
    assert got_b == state_b

    # **关键安全断言**：A 不能看到 B 的客户/商品
    a_cust_ids = {c["id"] for c in got_a.get("customers_seen", [])}
    a_prod_ids = {p["id"] for p in got_a.get("products_seen", [])}
    assert 11 not in a_cust_ids, "A 用户不应看到 B 的客户 11（得帆）"
    assert 5032 not in a_prod_ids, "A 用户不应看到 B 的商品 5032（翻译耳机）"

    # 反向同理：B 不能看到 A 的
    b_cust_ids = {c["id"] for c in got_b.get("customers_seen", [])}
    b_prod_ids = {p["id"] for p in got_b.get("products_seen", [])}
    assert 7 not in b_cust_ids, "B 用户不应看到 A 的客户 7（翼蓝）"
    assert 5030 not in b_prod_ids, "B 用户不应看到 A 的商品 5030（X5 Pro）"

    # 第三个用户 C（未写入）拿到 None
    got_c = await session.get_round_state(cid, hub_user_id=99)
    assert got_c is None


@pytest.mark.asyncio
async def test_round_state_clear_removes_all_users(session, redis_client):
    """clear() 应该清掉同 conversation 下所有 user 的 round_state（scan 模式）。"""
    cid = _conv_id("rs-clear")
    await session.set_round_state(cid, 1, {"a": 1})
    await session.set_round_state(cid, 2, {"b": 2})
    await session.set_round_state(cid, 3, {"c": 3})

    # 清前 3 个 key 都在
    keys = await redis_client.keys(f"hub:agent:conv:{cid}:round_state:*")
    assert len(keys) == 3

    await session.clear(cid)

    # 清后全空
    keys = await redis_client.keys(f"hub:agent:conv:{cid}:round_state:*")
    assert len(keys) == 0


@pytest.mark.asyncio
async def test_round_state_empty_state_skipped(session):
    """空 state（None / {} ）不写入。"""
    cid = _conv_id("rs-empty")
    await session.set_round_state(cid, 1, {})
    got = await session.get_round_state(cid, 1)
    assert got is None
