"""Plan 6 Task 4：MemoryWriter 测试（11 case）。"""
from __future__ import annotations
import pytest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from hub.agent.memory.writer import MemoryWriter
from hub.agent.memory.persistent import (
    UserMemoryService, CustomerMemoryService, ProductMemoryService,
)


def _make_tool_log(tool_name: str, result_json=None, args_json=None):
    """创建模拟 ToolCallLog 对象（不依赖 DB）。"""
    log = MagicMock()
    log.tool_name = tool_name
    log.args_json = args_json or {}
    log.result_json = result_json or {}
    return log


@pytest.mark.asyncio
async def test_should_extract_returns_false_for_chitchat():
    """tool_call_logs 空 + rounds < 4 → False。"""
    assert MemoryWriter.should_extract(tool_call_logs=[], rounds_count=0) is False
    assert MemoryWriter.should_extract(tool_call_logs=[], rounds_count=3) is False


@pytest.mark.asyncio
async def test_should_extract_returns_true_for_write_tool():
    """tool_call_log 含 create_voucher_draft → True。"""
    logs = [_make_tool_log("create_voucher_draft")]
    assert MemoryWriter.should_extract(tool_call_logs=logs, rounds_count=1) is True


@pytest.mark.asyncio
async def test_should_extract_returns_true_for_generate_tool():
    """含 generate_contract_draft → True。"""
    logs = [_make_tool_log("generate_contract_draft")]
    assert MemoryWriter.should_extract(tool_call_logs=logs, rounds_count=1) is True


@pytest.mark.asyncio
async def test_should_extract_returns_true_for_long_conversation():
    """rounds=5 + 无特殊 tool → True（因为 rounds >= 4）。"""
    logs = [_make_tool_log("search_something")]
    assert MemoryWriter.should_extract(tool_call_logs=logs, rounds_count=5) is True


@pytest.mark.asyncio
async def test_should_extract_returns_true_when_result_has_customer_id():
    """search_customers 返 customer_id → True（I2: EntityExtractor 结构化判断）。"""
    logs = [_make_tool_log("search_customers", result_json={"customer_id": 42, "name": "某客户"})]
    assert MemoryWriter.should_extract(tool_call_logs=logs, rounds_count=1) is True


@pytest.mark.asyncio
async def test_extract_and_write_skips_low_confidence_facts():
    """mock LLM 返 confidence 0.3 → user upsert_facts 不调。"""
    user_svc = AsyncMock(spec=UserMemoryService)
    customer_svc = AsyncMock(spec=CustomerMemoryService)
    product_svc = AsyncMock(spec=ProductMemoryService)
    writer = MemoryWriter(user=user_svc, customer=customer_svc, product=product_svc)

    ai_provider = MagicMock()
    ai_provider.parse_intent = AsyncMock(return_value={
        "user_facts": [
            {"fact": "低置信事实", "confidence": 0.3},  # 低于 0.6，不写
            {"fact": "另一低置信", "confidence": 0.5},  # 低于 0.6，不写
        ],
        "customer_facts": [],
        "product_facts": [],
    })

    logs = [_make_tool_log("create_something")]  # 触发 gate
    await writer.extract_and_write(
        conversation_id="test-conv-1",
        hub_user_id=1,
        tool_call_logs=logs,
        rounds_count=1,
        ai_provider=ai_provider,
    )

    # 因为全部 confidence < 0.6，user upsert_facts 不应被调用
    user_svc.upsert_facts.assert_not_called()
    customer_svc.upsert_facts.assert_not_called()
    product_svc.upsert_facts.assert_not_called()


# ─────────────────────────── I4: 新增 5 个测试 case ───────────────────────────


@pytest.mark.asyncio
async def test_extract_and_write_short_circuits_when_should_extract_false():
    """I4: should_extract=False 时不调 LLM。"""
    user_svc = AsyncMock()
    customer_svc = AsyncMock()
    product_svc = AsyncMock()
    ai_provider = AsyncMock()

    writer = MemoryWriter(user_svc, customer_svc, product_svc)
    await writer.extract_and_write(
        conversation_id="c1",
        hub_user_id=1,
        tool_call_logs=[],  # 空 → should_extract=False
        rounds_count=1,
        ai_provider=ai_provider,
    )
    ai_provider.parse_intent.assert_not_called()


@pytest.mark.asyncio
async def test_extract_and_write_calls_upsert_with_correct_facts():
    """I4: 高 confidence user fact → user_svc.upsert_facts 被调，customer_svc 也对。"""
    user_svc = AsyncMock()
    customer_svc = AsyncMock()
    product_svc = AsyncMock()
    ai_provider = AsyncMock()
    ai_provider.parse_intent.return_value = {
        "user_facts": [{"fact": "用户偏好分期付款", "confidence": 0.9}],
        "customer_facts": [{"customer_id": 100, "fact": "对方主营华东", "confidence": 0.8}],
        "product_facts": [],
    }

    writer = MemoryWriter(user_svc, customer_svc, product_svc)
    await writer.extract_and_write(
        conversation_id="c1",
        hub_user_id=1,
        tool_call_logs=[_make_tool_log("create_voucher_draft")],
        rounds_count=2,
        ai_provider=ai_provider,
    )
    user_svc.upsert_facts.assert_awaited_once()
    customer_svc.upsert_facts.assert_awaited_once_with(100, new_facts=ANY)


@pytest.mark.asyncio
async def test_extract_and_write_fail_soft_on_llm_error():
    """I4 / C2: LLM parse_intent 抛错 → logger.exception，不 raise。"""
    user_svc = AsyncMock()
    ai_provider = AsyncMock()
    ai_provider.parse_intent.side_effect = RuntimeError("LLM 503")

    writer = MemoryWriter(user_svc, AsyncMock(), AsyncMock())
    # 不应抛
    await writer.extract_and_write(
        conversation_id="c1",
        hub_user_id=1,
        tool_call_logs=[_make_tool_log("generate_contract")],
        rounds_count=2,
        ai_provider=ai_provider,
    )
    user_svc.upsert_facts.assert_not_called()


@pytest.mark.asyncio
async def test_extract_and_write_fail_soft_on_db_error():
    """I4 / C2: _upsert_all DB 抛错 → logger.exception，不 raise（不阻塞 asyncio.create_task）。"""
    user_svc = AsyncMock()
    user_svc.upsert_facts.side_effect = RuntimeError("DB pool exhausted")
    ai_provider = AsyncMock()
    ai_provider.parse_intent.return_value = {
        "user_facts": [{"fact": "某偏好", "confidence": 0.9}],
        "customer_facts": [],
        "product_facts": [],
    }

    writer = MemoryWriter(user_svc, AsyncMock(), AsyncMock())
    # 不应抛
    await writer.extract_and_write(
        conversation_id="c1",
        hub_user_id=1,
        tool_call_logs=[_make_tool_log("create_voucher_draft")],
        rounds_count=2,
        ai_provider=ai_provider,
    )


@pytest.mark.asyncio
async def test_extract_and_write_handles_malformed_result():
    """I4 / C2: parse_intent 返回非 list / 类型错 → 不挂，不调 upsert。"""
    user_svc = AsyncMock()
    ai_provider = AsyncMock()
    ai_provider.parse_intent.return_value = {
        "user_facts": "not-a-list",            # 格式错：string 而非 list
        "customer_facts": None,                 # 格式错：None
        "product_facts": [
            {"product_id": "not-int", "fact": "x", "confidence": 0.9},  # cid 类型错
        ],
    }

    writer = MemoryWriter(user_svc, AsyncMock(), AsyncMock())
    await writer.extract_and_write(
        conversation_id="c1",
        hub_user_id=1,
        tool_call_logs=[_make_tool_log("search_customers",
                                       result_json={"customer_id": 1})],
        rounds_count=2,
        ai_provider=ai_provider,
    )
    # user_facts 是 string → _safe_list 返 []，不调
    user_svc.upsert_facts.assert_not_called()


# ─────────────────────── C3: upsert_facts select_for_update 路径测试 ───────────────────────


@pytest.mark.asyncio
async def test_upsert_uses_select_for_update(monkeypatch):
    """C3: UserMemoryService.upsert_facts 在写时确实经过 in_transaction + select_for_update 路径。

    用 monkeypatch 计数 select_for_update 调用，验证 lock 路径被走过。
    """
    from hub.agent.memory import persistent as _p

    select_for_update_calls = []

    class _FakeQS:
        def select_for_update(self):
            select_for_update_calls.append(1)
            return self

        def using_db(self, conn):
            return self

        async def all(self):
            return []  # 空 → 走 create 分支

    class _FakeModel:
        @staticmethod
        def filter(**_kwargs):
            return _FakeQS()

        @staticmethod
        async def create(**kwargs):
            obj = MagicMock()
            obj.facts = None
            obj.preferences = None
            obj.updated_at = None
            obj.save = AsyncMock()
            return obj

    # 用假 context manager 替代 in_transaction
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_transaction(db_name):
        yield "fake_conn"

    monkeypatch.setattr(_p, "UserMemoryModel", _FakeModel)
    monkeypatch.setattr(_p, "in_transaction", _fake_transaction)

    svc = _p.UserMemoryService()
    await svc.upsert_facts(1, new_facts=[{"fact": "测试", "confidence": 0.9}])

    assert len(select_for_update_calls) >= 1, "select_for_update 未被调用，C3 锁路径未走"
