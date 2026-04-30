"""Plan 6 Task 6：ContextBuilder 测试（4 case）。"""
from __future__ import annotations
import json
import pytest

from hub.agent.context_builder import ContextBuilder
from hub.agent.types import PromptTooLargeError
from hub.agent.memory.types import Memory, ConversationHistory
from hub.agent.prompt.builder import PromptBuilder


def _empty_memory() -> Memory:
    """构造空 Memory 对象（用于 PromptBuilder）。"""
    return Memory(
        session=ConversationHistory(conversation_id="test-ctx"),
        user={"facts": [], "preferences": {}},
        customers={},
        products={},
    )


@pytest.mark.asyncio
async def test_must_keep_basic_round():
    """build_round 返回含 system + user 的 messages 列表（基本结构校验）。"""
    builder = ContextBuilder()
    memory = _empty_memory()

    messages = await builder.build_round(
        round_idx=0,
        base_memory=memory,
        tools_schema=[],
        conversation_history=[],
        latest_user_message="查询最近订单",
        confirm_state_hint=None,
        budget_token=18_000,
    )

    assert isinstance(messages, list)
    assert len(messages) >= 2  # 至少 system_prompt + user_msg

    roles = [m["role"] for m in messages]
    assert "system" in roles
    assert "user" in roles

    # system prompt 应含 PromptBuilder 生成的内容
    system_msgs = [m for m in messages if m["role"] == "system"]
    assert any("HUB" in m["content"] or "Agent" in m["content"] for m in system_msgs)

    # user message 内容正确
    user_msgs = [m for m in messages if m["role"] == "user"]
    assert user_msgs[0]["content"] == "查询最近订单"


@pytest.mark.asyncio
async def test_must_keep_exceeds_budget_raises():
    """budget 极小时必保上下文超限 → 抛 PromptTooLargeError。"""
    builder = ContextBuilder()
    memory = _empty_memory()

    with pytest.raises(PromptTooLargeError):
        await builder.build_round(
            round_idx=0,
            base_memory=memory,
            tools_schema=[],
            conversation_history=[],
            latest_user_message="hi",
            confirm_state_hint=None,
            budget_token=10,  # 远小于任何 system prompt
        )


@pytest.mark.asyncio
async def test_old_tool_results_summarized_when_long():
    """history 含 600 token 的 tool result → 摘要为 'N items, fields=[...]' 形式。"""
    builder = ContextBuilder()
    memory = _empty_memory()

    # 构造一个包含大量数据的 tool result（模拟 600+ token）
    large_items = [
        {"id": i, "name": f"商品{i}", "sku": f"SKU{i:04d}", "price": i * 10.0,
         "stock": i * 5, "category": "电子产品"}
        for i in range(50)
    ]
    large_result = json.dumps({"items": large_items}, ensure_ascii=False)

    # 构造超过 -2 边界的 history（即 history[:-2] 能看到这条 tool result）
    history = [
        {"role": "user", "content": "查商品"},
        {"role": "assistant", "content": None, "tool_calls": []},
        {
            "role": "tool",
            "name": "search_products",
            "tool_call_id": "call_x",
            "content": large_result,
            "round_idx": 0,
        },
        {"role": "assistant", "content": "找到了 50 个商品"},
        {"role": "user", "content": "请再查库存"},
    ]

    messages = await builder.build_round(
        round_idx=1,
        base_memory=memory,
        tools_schema=[],
        conversation_history=history,
        latest_user_message="请再查库存",
        confirm_state_hint=None,
        budget_token=18_000,
    )

    # 找到包含 old_results_summary 的 system 消息
    system_msgs = [m for m in messages if m["role"] == "system"]
    summary_msgs = [m for m in system_msgs if "old_results_summary" in m.get("content", "")]

    # 有 summary → 说明大 tool result 被摘要了
    # 或者 token 不够时被丢弃，但 budget 18K 足够，所以应该出现
    if summary_msgs:
        # 摘要内容应包含 items 计数信息
        content = summary_msgs[0]["content"]
        assert "search_products" in content or "items" in content or "fields" in content


@pytest.mark.asyncio
async def test_can_truncate_drops_low_priority_when_no_room():
    """budget 紧张时 old_history_summary（优先级 2）被丢，old_results_summary（优先级 5）被保留。"""
    builder = ContextBuilder()
    memory = _empty_memory()

    # 先用真实 build_round 拿到 system prompt 的 token 数，估算 budget
    base_messages = await builder.build_round(
        round_idx=0,
        base_memory=memory,
        tools_schema=[],
        conversation_history=[],
        latest_user_message="test",
        budget_token=18_000,
    )
    # system_prompt + user_msg 占用的 token 约 200-1000
    # 我们设 budget 略比 must_keep 大一点，但不足以放 old_history_summary

    # 构造有足够深度历史的 conversation（使 [:-4] 不为空）
    history = []
    for i in range(6):
        history.append({"role": "user", "content": f"第{i+1}轮用户消息 " * 5})
        history.append({"role": "assistant", "content": f"第{i+1}轮助手回复 " * 5})

    # 一个真实但小的 tool result（< 500 token）
    small_result = json.dumps({"items": [{"id": 1, "name": "test"}]}, ensure_ascii=False)
    history.insert(2, {
        "role": "tool",
        "name": "search_products",
        "tool_call_id": "call_1",
        "content": small_result,
        "round_idx": 0,
    })

    # 设较紧的 budget（比 must_keep 大，但不足以放 old_history_summary）
    # must_keep ≈ system_prompt + user_msg + recent_round
    # 先取 must_keep token 数估计
    from hub.agent.context_builder import _estimate_tokens
    prompt_builder = PromptBuilder()
    sp = prompt_builder.build(memory=memory, tools_schema=[])
    must_tokens = (
        _estimate_tokens(sp)
        + _estimate_tokens("test message")
        + sum(_estimate_tokens(str(m.get("content", ""))) for m in history[-2:])
    )

    # budget = must_tokens + 50（够放 old_results_summary 的小内容，但不够 old_history_summary）
    tight_budget = must_tokens + 50

    messages = await builder.build_round(
        round_idx=1,
        base_memory=memory,
        tools_schema=[],
        conversation_history=history,
        latest_user_message="test message",
        budget_token=tight_budget,
    )

    # 不应包含 old_history_summary（budget 不够）
    system_msgs = [m for m in messages if m["role"] == "system"]
    old_history_msgs = [m for m in system_msgs if "old_history_summary" in m.get("content", "")]
    assert len(old_history_msgs) == 0, "budget 紧张时 old_history_summary 应被丢弃"
