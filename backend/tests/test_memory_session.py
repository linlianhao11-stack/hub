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
