import pytest
from unittest.mock import AsyncMock
from hub.agent.graph.state import AgentState
from hub.agent.graph.nodes.format_response import format_response_node, FORMAT_PROMPTS


@pytest.mark.asyncio
async def test_format_response_uses_prefix_completion():
    """contract 用 '合同已生成：' 开头。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": "X1 10 个，金额 3000 元。"})())
    state = AgentState(user_message="x", hub_user_id=1, conversation_id="c1")
    out = await format_response_node(state, llm=llm, template_key="contract",
                                       summary="客户 阿里, X1 10 个 300")
    kw = llm.chat.await_args.kwargs
    assert kw["prefix_assistant"] == "合同已生成："
    assert kw["thinking"] == {"type": "disabled"}
    assert kw["temperature"] == 0.7
    assert out.final_response.startswith("合同已生成：")
    assert "X1 10 个，金额 3000 元。" in out.final_response


@pytest.mark.asyncio
async def test_format_response_unknown_template_falls_back():
    """未知 template_key 用兜底 '完成：'。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": "ok"})())
    state = AgentState(user_message="x", hub_user_id=1, conversation_id="c1")
    out = await format_response_node(state, llm=llm, template_key="unknown_xxx",
                                       summary="x")
    assert out.final_response.startswith("完成：")


@pytest.mark.asyncio
async def test_format_prompts_cover_all_subgraphs():
    """所有子图 template_key 都有对应 prefix。"""
    expected = {"contract", "quote", "voucher", "adjust_price", "adjust_stock", "confirm_done"}
    assert expected.issubset(FORMAT_PROMPTS.keys())
