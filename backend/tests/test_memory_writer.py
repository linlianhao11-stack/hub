"""Plan 6 Task 4：MemoryWriter 测试（6 case）。"""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from hub.agent.memory.writer import MemoryWriter
from hub.agent.memory.persistent import (
    UserMemoryService, CustomerMemoryService, ProductMemoryService,
)


def _make_tool_log(tool_name: str, result_json=None):
    """创建模拟 ToolCallLog 对象（不依赖 DB）。"""
    log = MagicMock()
    log.tool_name = tool_name
    log.args_json = {}
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
    """search_customers 返 customer_id → True。"""
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
