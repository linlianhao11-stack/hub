"""tool_logger context manager 行为测试（5 case）。

测试覆盖：
  1. 成功调用 → 写一行 tool_call_log（断言 args_json/result_json/duration_ms）
  2. tool 抛异常 → tool_call_log.error 有值且异常上抛
  3. 大 result（> 10KB）→ result_json 被截断（保留 keys + _truncated: true）
  4. conversation_id 不存在于 conversation_log 表 → tool_call_log 写入仍成功（无 FK 约束）
  5. 并发同 conversation 多 tool → 各自独立行
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from hub.models.conversation import ConversationLog, ToolCallLog
from hub.observability.tool_logger import log_tool_call


async def _make_conversation(conversation_id: str = "conv-test") -> ConversationLog:
    """辅助函数：创建一条 ConversationLog 父记录（供测试复用）。"""
    return await ConversationLog.create(
        conversation_id=conversation_id,
        hub_user_id=1,
        channel_userid="user-001",
        started_at=datetime.now(UTC),
    )


# ─── Case 1: 成功调用 ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_success_writes_tool_call_log():
    """正常退出 → 写一行 tool_call_log，字段正确。"""
    await _make_conversation("conv-case1")

    async with log_tool_call(
        conversation_id="conv-case1",
        round_idx=0,
        tool_name="query_product",
        args={"sku": "SKU100"},
    ) as ctx:
        ctx.set_result({"name": "鼠标", "price": 120.0})

    row = await ToolCallLog.get(conversation_id="conv-case1", tool_name="query_product")
    assert row.round_idx == 0
    assert row.args_json == {"sku": "SKU100"}
    assert row.result_json is not None
    # result_json 应包含 name 字段（小结果不截断）
    assert "name" in row.result_json
    assert row.duration_ms is not None
    assert row.duration_ms >= 0
    assert row.error is None


# ─── Case 2: 抛异常 ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_exception_logs_error_and_reraises():
    """tool 抛异常 → tool_call_log.error 非空，且异常原样上抛。"""
    await _make_conversation("conv-case2")

    with pytest.raises(ValueError, match="模拟 ERP 超时"):
        async with log_tool_call(
            conversation_id="conv-case2",
            round_idx=1,
            tool_name="create_voucher",
            args={"amount": 500},
        ) as _ctx:
            raise ValueError("模拟 ERP 超时")

    row = await ToolCallLog.get(conversation_id="conv-case2", tool_name="create_voucher")
    assert row.error is not None
    assert "模拟 ERP 超时" in row.error
    # 即使出错，duration_ms 仍然写入
    assert row.duration_ms is not None


# ─── Case 3: 大 result 截断 ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_large_result_is_truncated():
    """result_json > 10KB → 自动截断，含 _truncated: true 标记。"""
    await _make_conversation("conv-case3")

    # 构造超 10KB 的 list result
    big_list = [{"id": i, "name": "item" * 50, "data": "x" * 100} for i in range(200)]

    async with log_tool_call(
        conversation_id="conv-case3",
        round_idx=0,
        tool_name="query_history",
        args={"customer_id": 99},
    ) as ctx:
        ctx.set_result(big_list)

    row = await ToolCallLog.get(conversation_id="conv-case3", tool_name="query_history")
    result = row.result_json
    assert result is not None
    # 截断标记必须存在
    assert result.get("_truncated") is True
    # 原始数量记录在 _original_count
    assert result.get("_original_count") == 200
    # 截断后 items 数量少于原始
    assert len(result.get("items", [])) < 200


# ─── Case 4: conversation_id 不存在，无 FK 约束，写入仍成功 ─────────────────
@pytest.mark.asyncio
async def test_tool_call_log_without_parent_conversation():
    """conversation_id 不存在于 conversation_log 表 → tool_call_log 写入仍成功。

    验证 ToolCallLog.conversation_id 是普通字符串字段，不存在数据库层 FK 约束。
    无需先建 ConversationLog 父记录即可写入。
    """
    orphan_id = "never-existed-conversation-id-xyz"

    # 确认父记录确实不存在
    assert not await ConversationLog.filter(conversation_id=orphan_id).exists()

    # 直接写 tool_call_log，不建父记录 —— 不应抛任何异常
    async with log_tool_call(
        conversation_id=orphan_id,
        round_idx=2,
        tool_name="get_customer_info",
        args={"customer_id": 42},
    ) as ctx:
        ctx.set_result({"customer_name": "测试客户", "credit_limit": 10000})

    # 写入成功
    assert await ToolCallLog.filter(conversation_id=orphan_id).exists()
    row = await ToolCallLog.get(conversation_id=orphan_id, tool_name="get_customer_info")
    assert row.round_idx == 2
    assert row.result_json is not None
    assert row.result_json.get("customer_name") == "测试客户"


# ─── Case 5: 并发同 conversation 多 tool ────────────────────────────────────
@pytest.mark.asyncio
async def test_concurrent_tool_calls_write_independent_rows():
    """并发 5 个 log_tool_call → 各自独立写一行，共 5 行，不互相干扰。"""
    await _make_conversation("conv-case5")

    async def _call_tool(idx: int):
        async with log_tool_call(
            conversation_id="conv-case5",
            round_idx=idx,
            tool_name=f"tool_{idx}",
            args={"idx": idx},
        ) as ctx:
            ctx.set_result({"done": idx})

    # 并发执行 5 次 tool 调用
    await asyncio.gather(*[_call_tool(i) for i in range(5)])

    # 断言共写入 5 行
    rows = await ToolCallLog.filter(conversation_id="conv-case5").all()
    assert len(rows) == 5

    # 每行工具名和 round_idx 对应
    tool_names = {row.tool_name for row in rows}
    assert tool_names == {f"tool_{i}" for i in range(5)}

    for row in rows:
        assert row.error is None
        assert row.duration_ms is not None


# ─── Case 6: Decimal/datetime 不被静默吞日志 ─────────────────────────────────
@pytest.mark.asyncio
async def test_truncate_handles_decimal_and_datetime():
    """v1 加固：result 含 Decimal/datetime 不应让 tool_call_log 静默丢失。"""
    from datetime import UTC, datetime
    from decimal import Decimal

    conv_id = "test-decimal-conv"
    # 不建父 ConversationLog，验 FK 不存在仍写入

    erp_like_result = {
        "order_id": 12345,
        "amount": Decimal("100.50"),
        "created_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        "items": [
            {"sku": "X1", "price": Decimal("9.99")},
            {"sku": "X2", "price": Decimal("19.99")},
        ],
    }

    async with log_tool_call(
        conversation_id=conv_id, round_idx=0,
        tool_name="search_orders", args={},
    ) as ctx:
        ctx.set_result(erp_like_result)

    # 关键：必须真写入了一行（不是 ToolCallLog.create 抛 TypeError 被吞掉）
    rows = await ToolCallLog.filter(conversation_id=conv_id).all()
    assert len(rows) == 1
    # result_json 里的 Decimal/datetime 都应转成 str
    assert rows[0].result_json["amount"] == "100.50"
    assert "2026-01-01" in rows[0].result_json["created_at"]
    assert rows[0].result_json["items"][0]["price"] == "9.99"
