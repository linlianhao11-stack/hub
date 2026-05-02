# backend/hub/agent/graph/subgraphs/query.py
"""Query 子图 — 11 tool 只读 + format 输出。spec §3 / §5.1。

简化为 2 节点循环：query_loop（LLM 自行选 tool 调，最多 N 轮）→ format_response
"""
from __future__ import annotations

import json
from typing import Callable, Awaitable

from hub.agent.graph.state import AgentState
from hub.agent.llm_client import DeepSeekLLMClient, ToolClass, disable_thinking
from hub.agent.prompt.subgraph_prompts.query import QUERY_SYSTEM_PROMPT
from hub.agent.tools.registry import ToolRegistry


MAX_TOOL_LOOPS = 4


async def query_subgraph(
    state: AgentState,
    *,
    llm: DeepSeekLLMClient,
    registry: ToolRegistry | None = None,
    tool_executor: Callable[[str, dict], Awaitable[object]] | None = None,
) -> AgentState:
    tools = registry.schemas_for_subgraph("query") if registry else []
    messages = [
        {"role": "system", "content": QUERY_SYSTEM_PROMPT},
        {"role": "user", "content": state.user_message},
    ]
    for _ in range(MAX_TOOL_LOOPS):
        resp = await llm.chat(
            messages=messages,
            tools=tools,
            tool_choice="auto",
            thinking=disable_thinking(),
            temperature=0.0,
            tool_class=ToolClass.READ,
        )
        if not resp.tool_calls:
            state.final_response = resp.text
            return state
        # 执行 tool calls，把结果作为 tool message append
        messages.append({"role": "assistant", "content": resp.text or "",
                          "tool_calls": resp.tool_calls})
        for tc in resp.tool_calls:
            args = json.loads(tc["function"]["arguments"])
            result = await tool_executor(tc["function"]["name"], args)
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                              "content": json.dumps(result, ensure_ascii=False, default=str)})
    state.final_response = "（查询轮数过多，未拿到稳定结果，请精简问题再试）"
    state.errors.append("query_max_loops")
    return state
