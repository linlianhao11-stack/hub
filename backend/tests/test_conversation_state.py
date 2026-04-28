import pytest
import pytest_asyncio
from fakeredis import aioredis as fakeredis_aio


@pytest_asyncio.fixture
async def fake_redis():
    c = fakeredis_aio.FakeRedis()
    yield c
    await c.aclose()


@pytest.mark.asyncio
async def test_save_and_load(fake_redis):
    from hub.match.conversation_state import ConversationStateRepository
    repo = ConversationStateRepository(redis=fake_redis, ttl_seconds=300)

    state = {
        "intent_type": "query_product",
        "candidates": [{"id": 1, "label": "A"}, {"id": 2, "label": "B"}],
        "resource": "商品",
    }
    await repo.save("user1", state)
    loaded = await repo.load("user1")
    assert loaded == state


@pytest.mark.asyncio
async def test_load_missing_returns_none(fake_redis):
    from hub.match.conversation_state import ConversationStateRepository
    repo = ConversationStateRepository(redis=fake_redis, ttl_seconds=300)
    assert await repo.load("nobody") is None


@pytest.mark.asyncio
async def test_clear(fake_redis):
    from hub.match.conversation_state import ConversationStateRepository
    repo = ConversationStateRepository(redis=fake_redis, ttl_seconds=300)
    await repo.save("u", {"x": 1})
    await repo.clear("u")
    assert await repo.load("u") is None


@pytest.mark.asyncio
async def test_ttl_expires(fake_redis):
    """TTL 过期后无法读取（fakeredis 支持 ttl）。"""
    import asyncio
    from hub.match.conversation_state import ConversationStateRepository
    repo = ConversationStateRepository(redis=fake_redis, ttl_seconds=1)
    await repo.save("u", {"x": 1})
    assert await repo.load("u") is not None
    await fake_redis.expire("hub:conv:u", 0)  # 强制过期
    await asyncio.sleep(0.05)
    assert await repo.load("u") is None
