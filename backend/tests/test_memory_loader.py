"""Plan 6 Task 4：MemoryLoader 测试（8 case，用真 PG + redis）。"""
from __future__ import annotations
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from redis.asyncio import Redis

from hub.agent.memory.session import SessionMemory
from hub.agent.memory.persistent import (
    UserMemoryService, CustomerMemoryService, ProductMemoryService,
)
from hub.agent.memory.loader import MemoryLoader, _estimate_tokens, _truncate_facts
from hub.models.memory import (
    UserMemory as UserMemoryModel,
    CustomerMemory as CustomerMemoryModel,
    ProductMemory as ProductMemoryModel,
)


REDIS_URL = "redis://localhost:6380/0"
TEST_CONV_PREFIX = "hub:agent:conv:test-loader-"


@pytest_asyncio.fixture
async def redis_client():
    client = Redis.from_url(REDIS_URL, decode_responses=False)
    yield client
    keys = await client.keys(f"{TEST_CONV_PREFIX}*")
    if keys:
        await client.delete(*keys)
    await client.aclose()


@pytest_asyncio.fixture
def loader(redis_client):
    session = SessionMemory(redis_client)
    return MemoryLoader(
        session=session,
        user=UserMemoryService(),
        customer=CustomerMemoryService(),
        product=ProductMemoryService(),
    )


@pytest_asyncio.fixture
def session_mem(redis_client):
    return SessionMemory(redis_client)


def _conv_id(suffix: str) -> str:
    return f"test-loader-{suffix}"


@pytest.mark.asyncio
async def test_load_empty_session_returns_empty_memory(loader):
    """新 conversation_id → session 空 + user 空 + customers/products 空。"""
    memory = await loader.load(hub_user_id=9999, conversation_id=_conv_id("empty"))
    assert memory.session.messages == []
    assert memory.session.customer_ids == set()
    assert memory.session.product_ids == set()
    assert memory.user["facts"] == []
    assert memory.user["preferences"] == {}
    assert memory.customers == {}
    assert memory.products == {}


@pytest.mark.asyncio
async def test_load_with_user_facts_truncates_at_budget(loader):
    """插入 100 条 user fact → load 后只保留最近 N 条（token < 1000）。"""
    uid = 10001
    # 插入 100 条事实
    facts = [{"fact": f"用户偏好事实{i}，内容详细描述", "confidence": 0.8} for i in range(100)]
    svc = UserMemoryService()
    await svc.upsert_facts(uid, new_facts=facts)

    memory = await loader.load(hub_user_id=uid, conversation_id=_conv_id("truncate-user"))
    result_facts = memory.user["facts"]
    # 结果不应超过 100 条，且估算 token 应在 budget 内
    assert len(result_facts) < 100
    total_tokens = sum(_estimate_tokens(str(f)) for f in result_facts)
    assert total_tokens <= 1000


@pytest.mark.asyncio
async def test_load_referenced_customers_only(loader, session_mem):
    """session 有 customer_id={1,2}，DB 有客户 1/2/3 → 只返 {1, 2}。"""
    cid = _conv_id("ref-customers")
    await session_mem.add_entity_refs(cid, customer_ids={1, 2})

    # 建立客户 1/2/3 的 memory
    svc = CustomerMemoryService()
    await svc.upsert_facts(1, new_facts=[{"fact": "客户1偏好"}])
    await svc.upsert_facts(2, new_facts=[{"fact": "客户2历史"}])
    await svc.upsert_facts(3, new_facts=[{"fact": "客户3数据"}])

    memory = await loader.load(hub_user_id=20001, conversation_id=cid)
    assert set(memory.customers.keys()) == {1, 2}
    assert 3 not in memory.customers


@pytest.mark.asyncio
async def test_load_referenced_products_truncated_per_product(loader, session_mem):
    """每个 product 单独 truncate 到 200 token。"""
    cid = _conv_id("ref-products")
    await session_mem.add_entity_refs(cid, product_ids={100, 101})

    svc = ProductMemoryService()
    # 每个商品插 50 条 fact（超过 200 token 预算）
    for pid in (100, 101):
        facts = [{"fact": f"商品{pid}事实{i}，内容较长的描述文字，包含规格参数价格等信息"} for i in range(50)]
        await svc.upsert_facts(pid, new_facts=facts)

    memory = await loader.load(hub_user_id=20002, conversation_id=cid)
    assert 100 in memory.products
    assert 101 in memory.products
    for pid in (100, 101):
        facts = memory.products[pid]["facts"]
        total_tokens = sum(_estimate_tokens(str(f)) for f in facts)
        assert total_tokens <= 200


@pytest.mark.asyncio
async def test_load_session_history_preserved(loader, session_mem):
    """session 5 条消息 → memory.session.messages 长度 5。"""
    cid = _conv_id("session-history")
    for i in range(5):
        await session_mem.append(cid, role="user" if i % 2 == 0 else "assistant",
                                 content=f"消息{i}")

    memory = await loader.load(hub_user_id=20003, conversation_id=cid)
    assert len(memory.session.messages) == 5


@pytest.mark.asyncio
async def test_truncate_facts_keeps_newest():
    """facts list 旧到新排，截断从尾部（最新）开始保留。"""
    facts = [{"fact": f"事实{i}"} for i in range(20)]
    result = _truncate_facts(facts, budget=100)
    # 应该保留的是末尾的（最新的），而不是头部的（最旧的）
    assert len(result) > 0
    assert len(result) <= 20
    if len(result) < 20:
        # 保留的最后一条应是 facts 列表最新的 fact
        assert result[-1]["fact"] == "事实19"


@pytest.mark.asyncio
async def test_load_multiple_users_isolated(loader):
    """user A 的 memory 不会进 user B。"""
    svc = UserMemoryService()
    await svc.upsert_facts(30001, new_facts=[{"fact": "用户A偏好", "confidence": 0.9}])
    await svc.upsert_facts(30002, new_facts=[{"fact": "用户B偏好", "confidence": 0.9}])

    mem_a = await loader.load(hub_user_id=30001, conversation_id=_conv_id("user-a"))
    mem_b = await loader.load(hub_user_id=30002, conversation_id=_conv_id("user-b"))

    facts_a = [f["fact"] for f in mem_a.user["facts"]]
    facts_b = [f["fact"] for f in mem_b.user["facts"]]

    assert "用户A偏好" in facts_a
    assert "用户B偏好" not in facts_a
    assert "用户B偏好" in facts_b
    assert "用户A偏好" not in facts_b


@pytest.mark.asyncio
async def test_estimate_tokens_fallback():
    """M1: tiktoken 不可用时用 CJK-aware fallback 回退（强制 _ENCODER_FAILED）。"""
    import hub.agent.memory.loader as loader_mod

    # 强制 fallback 分支（保留原 _ENCODER / _ENCODER_FAILED 值用于还原）
    orig_encoder = loader_mod._ENCODER
    orig_failed = loader_mod._ENCODER_FAILED
    try:
        loader_mod._ENCODER = None
        loader_mod._ENCODER_FAILED = True  # 模拟 tiktoken 不可用

        text_ascii = "hello world test"
        result = _estimate_tokens(text_ascii)
        # 纯 ASCII：cjk_count=0，ascii_count=16 → int(0/1.5 + 16/4) = 4
        expected = int(0 / 1.5 + len(text_ascii) / 4)
        assert result == expected, f"expected {expected}, got {result}"

        # 中文字符（CJK >= 0x3000）走 CJK 分支
        text_cjk = "你好世界"
        result_cjk = _estimate_tokens(text_cjk)
        # 4 个 CJK → int(4/1.5) = 2
        assert result_cjk == int(len(text_cjk) / 1.5)
    finally:
        loader_mod._ENCODER = orig_encoder
        loader_mod._ENCODER_FAILED = orig_failed
