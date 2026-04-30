"""tool_logger context manager 行为测试（5 case）。

测试覆盖：
  1. 成功调用 → 写一行 tool_call_log（断言 args_json/result_json/duration_ms）
  2. tool 抛异常 → tool_call_log.error 有值且异常上抛
  3. 大 result（> 10KB）→ result_json 被截断（保留 keys + _truncated: true）
  4. 先建 conversation_log，再写 tool_call_log → FK 逻辑关联不报错
  5. 并发同 conversation 多 tool → 各自独立行
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from hub.models.conversation import ConversationLog, ToolCallLog
from hub.observability.tool_logger import log_tool_call, _truncate_for_log


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
        ) as ctx:
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


# ─── Case 4: conversation_id 存在，FK 逻辑关联 ───────────────────────────────
@pytest.mark.asyncio
async def test_tool_call_log_with_existing_conversation():
    """先建 conversation_log，再写 tool_call_log → 逻辑关联正常，无 FK 约束报错。"""
    # 1. 先建父记录
    conv = await _make_conversation("conv-case4")
    assert conv.conversation_id == "conv-case4"

    # 2. 再写 tool_call_log
    async with log_tool_call(
        conversation_id="conv-case4",
        round_idx=2,
        tool_name="get_customer_info",
        args={"customer_id": 42},
    ) as ctx:
        ctx.set_result({"customer_name": "测试客户", "credit_limit": 10000})

    row = await ToolCallLog.get(conversation_id="conv-case4", tool_name="get_customer_info")
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
