"""MemoryWriter 测试(v12 - 输入维度从 ToolCallLog 改成 LangGraph messages)。"""
from __future__ import annotations

from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from hub.agent.memory.persistent import (
    CustomerMemoryService,
    ProductMemoryService,
    UserMemoryService,
)
from hub.agent.memory.writer import MemoryWriter


# ─────────────────────── helper: 构造 LangGraph messages ───────────────────────


def _ai_msg_with_tool_calls(*tool_calls: dict, content: str = "") -> AIMessage:
    """tool_calls=[{"name": ..., "args": {...}}, ...]"""
    return AIMessage(content=content, tool_calls=[
        {"id": f"call-{i}", "name": tc["name"], "args": tc.get("args", {})}
        for i, tc in enumerate(tool_calls)
    ])


def _tool_msg(name: str, content) -> ToolMessage:
    """ToolMessage.content 可以是 dict / str(json) / 任意 str。"""
    import json
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return ToolMessage(content=content, name=name, tool_call_id="call-0")


# ─────────────────────── should_extract gate ───────────────────────


@pytest.mark.asyncio
async def test_should_extract_returns_false_for_chitchat():
    assert MemoryWriter.should_extract(messages=[], rounds_count=0) is False
    assert MemoryWriter.should_extract(messages=[], rounds_count=3) is False


@pytest.mark.asyncio
async def test_should_extract_returns_true_for_write_tool():
    msgs = [_ai_msg_with_tool_calls({"name": "create_voucher_draft"})]
    assert MemoryWriter.should_extract(messages=msgs, rounds_count=1) is True


@pytest.mark.asyncio
async def test_should_extract_returns_true_for_generate_tool():
    msgs = [_ai_msg_with_tool_calls({"name": "generate_contract_draft"})]
    assert MemoryWriter.should_extract(messages=msgs, rounds_count=1) is True


@pytest.mark.asyncio
async def test_should_extract_returns_true_for_long_conversation():
    """rounds=5 且无写类 tool → True(rounds_count gate 单独触发)。"""
    msgs = [_ai_msg_with_tool_calls({"name": "search_anything"})]
    assert MemoryWriter.should_extract(messages=msgs, rounds_count=5) is True


@pytest.mark.asyncio
async def test_should_extract_returns_true_when_tool_result_has_customer_id():
    """ToolMessage.content json 含 customer_id → 实体抽取触发 True。"""
    msgs = [
        _ai_msg_with_tool_calls({"name": "search_customers"}),
        _tool_msg("search_customers", {"customer_id": 42, "name": "某客户"}),
    ]
    assert MemoryWriter.should_extract(messages=msgs, rounds_count=1) is True


# ─────────────────────── extract_and_write ───────────────────────


@pytest.mark.asyncio
async def test_extract_and_write_short_circuits_when_should_extract_false():
    user_svc = AsyncMock(spec=UserMemoryService)
    customer_svc = AsyncMock(spec=CustomerMemoryService)
    product_svc = AsyncMock(spec=ProductMemoryService)
    ai_provider = AsyncMock()
    writer = MemoryWriter(user_svc, customer_svc, product_svc)
    stats = await writer.extract_and_write(
        conversation_id="c1",
        hub_user_id=1,
        messages=[],   # 空 + rounds=1 → gate fail
        rounds_count=1,
        ai_provider=ai_provider,
    )
    ai_provider.parse_intent.assert_not_called()
    assert stats["skip_reason"] == "gate_failed"


@pytest.mark.asyncio
async def test_extract_and_write_skips_low_confidence_facts():
    """confidence < 0.6 全部不写。"""
    user_svc = AsyncMock(spec=UserMemoryService)
    customer_svc = AsyncMock(spec=CustomerMemoryService)
    product_svc = AsyncMock(spec=ProductMemoryService)
    writer = MemoryWriter(user=user_svc, customer=customer_svc, product=product_svc)

    ai_provider = MagicMock()
    ai_provider.parse_intent = AsyncMock(return_value={
        "user_facts": [
            {"fact": "低置信", "confidence": 0.3},
            {"fact": "低置信2", "confidence": 0.5},
        ],
        "customer_facts": [], "product_facts": [],
    })

    msgs = [_ai_msg_with_tool_calls({"name": "create_something"})]
    stats = await writer.extract_and_write(
        conversation_id="c1", hub_user_id=1,
        messages=msgs, rounds_count=1, ai_provider=ai_provider,
    )
    user_svc.upsert_facts.assert_not_called()
    customer_svc.upsert_facts.assert_not_called()
    product_svc.upsert_facts.assert_not_called()
    assert stats["new_user_facts"] == 0
    assert stats["dedup_skipped"] >= 2  # 2 条都被低置信过滤


@pytest.mark.asyncio
async def test_extract_and_write_calls_upsert_with_correct_facts():
    user_svc = AsyncMock()
    customer_svc = AsyncMock()
    product_svc = AsyncMock()
    ai_provider = AsyncMock()
    ai_provider.parse_intent.return_value = {
        "user_facts": [{"fact": "用户偏好分期", "confidence": 0.9}],
        "customer_facts": [{"customer_id": 100, "fact": "对方主营华东", "confidence": 0.8}],
        "product_facts": [],
    }
    writer = MemoryWriter(user_svc, customer_svc, product_svc)
    msgs = [_ai_msg_with_tool_calls({"name": "create_voucher_draft"})]
    stats = await writer.extract_and_write(
        conversation_id="c1", hub_user_id=1,
        messages=msgs, rounds_count=2, ai_provider=ai_provider,
    )
    user_svc.upsert_facts.assert_awaited_once()
    customer_svc.upsert_facts.assert_awaited_once_with(100, new_facts=ANY)
    assert stats["new_user_facts"] == 1
    assert stats["new_customer_facts"] == 1
    assert "duration_ms" in stats
    assert stats["input_chars"] > 0


@pytest.mark.asyncio
async def test_extract_and_write_fail_soft_on_llm_error():
    user_svc = AsyncMock()
    ai_provider = AsyncMock()
    ai_provider.parse_intent.side_effect = RuntimeError("LLM 503")
    writer = MemoryWriter(user_svc, AsyncMock(), AsyncMock())
    msgs = [_ai_msg_with_tool_calls({"name": "generate_contract"})]
    stats = await writer.extract_and_write(
        conversation_id="c1", hub_user_id=1,
        messages=msgs, rounds_count=2, ai_provider=ai_provider,
    )
    user_svc.upsert_facts.assert_not_called()
    assert stats["skip_reason"] == "llm_failed"


@pytest.mark.asyncio
async def test_extract_and_write_fail_soft_on_db_error():
    user_svc = AsyncMock()
    user_svc.upsert_facts.side_effect = RuntimeError("DB pool exhausted")
    ai_provider = AsyncMock()
    ai_provider.parse_intent.return_value = {
        "user_facts": [{"fact": "x", "confidence": 0.9}],
        "customer_facts": [], "product_facts": [],
    }
    writer = MemoryWriter(user_svc, AsyncMock(), AsyncMock())
    msgs = [_ai_msg_with_tool_calls({"name": "create_voucher_draft"})]
    stats = await writer.extract_and_write(
        conversation_id="c1", hub_user_id=1,
        messages=msgs, rounds_count=2, ai_provider=ai_provider,
    )
    assert stats["skip_reason"] == "db_failed"


@pytest.mark.asyncio
async def test_extract_and_write_handles_malformed_result():
    user_svc = AsyncMock()
    ai_provider = AsyncMock()
    ai_provider.parse_intent.return_value = {
        "user_facts": "not-a-list",
        "customer_facts": None,
        "product_facts": [
            {"product_id": "not-int", "fact": "x", "confidence": 0.9},
        ],
    }
    writer = MemoryWriter(user_svc, AsyncMock(), AsyncMock())
    msgs = [
        _ai_msg_with_tool_calls({"name": "search_customers"}),
        _tool_msg("search_customers", {"customer_id": 1}),
    ]
    await writer.extract_and_write(
        conversation_id="c1", hub_user_id=1,
        messages=msgs, rounds_count=2, ai_provider=ai_provider,
    )
    user_svc.upsert_facts.assert_not_called()


# ─────────────────────── v12: messages-based extraction input ───────────────────────


@pytest.mark.asyncio
async def test_extraction_input_contains_user_assistant_dialog():
    """v12 关键:抽取输入必须含 HumanMessage / AIMessage 原文,而不只是 tool 调用。"""
    user_svc = AsyncMock()
    customer_svc = AsyncMock()
    product_svc = AsyncMock()
    ai_provider = AsyncMock()
    ai_provider.parse_intent.return_value = {
        "user_facts": [], "customer_facts": [], "product_facts": [],
    }

    msgs = [
        HumanMessage(content="翼蓝以后都用现款付款,记一下"),
        AIMessage(content="好的,记下了"),
    ]
    writer = MemoryWriter(user_svc, customer_svc, product_svc)
    await writer.extract_and_write(
        conversation_id="c1", hub_user_id=1,
        messages=msgs, rounds_count=4,  # rounds gate 触发
        ai_provider=ai_provider,
    )

    # parse_intent 被调,验证传给 LLM 的 text 含用户原话(对比旧实现完全抓不到)
    sent_text = ai_provider.parse_intent.await_args.kwargs["text"]
    assert "翼蓝以后都用现款" in sent_text
    assert "用户:" in sent_text  # 我们加的"用户:"前缀


@pytest.mark.asyncio
async def test_extraction_input_includes_tool_calls_and_results():
    """messages 含 tool_calls 和 ToolMessage 时,extraction input 都要包含。"""
    user_svc = AsyncMock()
    ai_provider = AsyncMock()
    ai_provider.parse_intent.return_value = {
        "user_facts": [], "customer_facts": [], "product_facts": [],
    }
    msgs = [
        HumanMessage(content="查阿里订单"),
        _ai_msg_with_tool_calls({"name": "search_orders", "args": {"customer_id": 10}}),
        _tool_msg("search_orders", {"items": [], "summary": {"total_sales": 0}}),
        AIMessage(content="阿里近期无订单"),
    ]
    writer = MemoryWriter(user_svc, AsyncMock(), AsyncMock())
    await writer.extract_and_write(
        conversation_id="c1", hub_user_id=1,
        messages=msgs, rounds_count=4, ai_provider=ai_provider,
    )
    sent = ai_provider.parse_intent.await_args.kwargs["text"]
    assert "search_orders" in sent
    assert "customer_id" in sent
    assert "阿里近期无订单" in sent


# ─────────────────────── v11: customer/product 保留 kind 字段 ───────────────────────


@pytest.mark.asyncio
async def test_upsert_preserves_kind_for_customer_and_product_facts():
    """v11 bug fix 仍然有效:customer/product fact 入库必须保留 kind 字段。"""
    user_svc = AsyncMock()
    customer_svc = AsyncMock()
    product_svc = AsyncMock()
    ai_provider = AsyncMock()
    ai_provider.parse_intent.return_value = {
        "user_facts": [],
        "customer_facts": [
            {"customer_id": 7, "fact": "翼蓝享 95 折", "kind": "decision", "confidence": 0.85},
            {"customer_id": 7, "fact": "翼蓝偏现款", "kind": "reference", "confidence": 0.8},
        ],
        "product_facts": [
            {"product_id": 200, "fact": "X5 春节断货 2 周", "kind": "reference", "confidence": 0.9},
        ],
    }
    writer = MemoryWriter(user_svc, customer_svc, product_svc)
    msgs = [_ai_msg_with_tool_calls({"name": "create_voucher_draft"})]
    await writer.extract_and_write(
        conversation_id="c-kind", hub_user_id=1,
        messages=msgs, rounds_count=2, ai_provider=ai_provider,
    )
    assert customer_svc.upsert_facts.await_count == 2
    persisted = []
    for call in customer_svc.upsert_facts.await_args_list:
        persisted.extend(call.kwargs["new_facts"])
    decision = [f for f in persisted if f.get("fact") == "翼蓝享 95 折"]
    assert decision[0]["kind"] == "decision"
    ref = [f for f in persisted if f.get("fact") == "翼蓝偏现款"]
    assert ref[0]["kind"] == "reference"
    product_call = product_svc.upsert_facts.await_args_list[0]
    assert product_call.kwargs["new_facts"][0]["kind"] == "reference"


@pytest.mark.asyncio
async def test_upsert_defaults_kind_to_reference_when_missing_or_invalid():
    user_svc = AsyncMock()
    customer_svc = AsyncMock()
    product_svc = AsyncMock()
    ai_provider = AsyncMock()
    ai_provider.parse_intent.return_value = {
        "user_facts": [],
        "customer_facts": [
            {"customer_id": 7, "fact": "无 kind", "confidence": 0.8},
            {"customer_id": 7, "fact": "非法 kind", "kind": "garbage", "confidence": 0.7},
        ],
        "product_facts": [],
    }
    writer = MemoryWriter(user_svc, customer_svc, product_svc)
    msgs = [_ai_msg_with_tool_calls({"name": "create_voucher_draft"})]
    await writer.extract_and_write(
        conversation_id="c-default-kind", hub_user_id=1,
        messages=msgs, rounds_count=2, ai_provider=ai_provider,
    )
    persisted = []
    for call in customer_svc.upsert_facts.await_args_list:
        persisted.extend(call.kwargs["new_facts"])
    assert all(f["kind"] == "reference" for f in persisted)


# ─────────────────────── select_for_update 路径仍然有效 ───────────────────────


@pytest.mark.asyncio
async def test_upsert_uses_select_for_update(monkeypatch):
    """select_for_update lock 路径被走过(防并发覆盖)。"""
    select_for_update_calls = []

    class _FakeQS:
        def select_for_update(self):
            select_for_update_calls.append(1)
            return self
        def using_db(self, conn):
            return self
        async def all(self):
            return []

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

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_in_transaction(_alias):
        yield None

    monkeypatch.setattr(
        "hub.agent.memory.persistent.UserMemoryModel", _FakeModel,
    )
    monkeypatch.setattr(
        "hub.agent.memory.persistent.in_transaction", _fake_in_transaction,
    )

    from hub.agent.memory.persistent import UserMemoryService
    svc = UserMemoryService()
    await svc.upsert_facts(1, new_facts=[
        {"fact": "x", "confidence": 0.9},
    ])
    assert len(select_for_update_calls) >= 1
