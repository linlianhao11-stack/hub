# backend/tests/agent/test_subgraph_query.py
import pytest
from unittest.mock import AsyncMock
from hub.agent.graph.state import AgentState, Intent
from hub.agent.graph.subgraphs.query import query_subgraph


@pytest.mark.asyncio
async def test_query_only_uses_query_subgraph_tools():
    """query 子图只挂 11 个读 tool，不应包含 generate_contract_draft 等写 tool。"""
    llm = AsyncMock()
    # 第一轮：tool_calls=[check_inventory]
    llm.chat = AsyncMock(side_effect=[
        type("R", (), {"text": "", "tool_calls": [{"id": "1", "type": "function",
                       "function": {"name": "check_inventory", "arguments": '{"sku_pattern": "SKG"}'}}],
                       "finish_reason": "tool_calls"})(),
        # 第二轮：finalized text
        type("R", (), {"text": "| SKU | 库存 |\n| X1 | 100 |", "tool_calls": [],
                       "finish_reason": "stop"})(),
    ])
    # 用 register_all_tools 拿真的 ToolRegistry
    from hub.agent.tools.registry import ToolRegistry
    from hub.agent.tools import register_all_tools
    reg = ToolRegistry()
    register_all_tools(reg)

    state = AgentState(user_message="查 SKG 库存", hub_user_id=1, conversation_id="c1",
                        intent=Intent.QUERY)
    out = await query_subgraph(state, llm=llm, registry=reg, tool_executor=AsyncMock(return_value=[
        {"sku": "X1", "qty": 100},
    ]))
    # 验证传给 llm.chat 的 tools 列表只含读 tool
    first_call_kwargs = llm.chat.await_args_list[0].kwargs
    tool_names = {t["function"]["name"] for t in first_call_kwargs["tools"]}
    assert "generate_contract_draft" not in tool_names  # 写 tool 物理不挂
    assert "check_inventory" in tool_names
    assert out.final_response  # 有最终输出
