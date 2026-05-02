# backend/hub/agent/graph/subgraphs/chat.py
"""Chat 子图 — 0 tool，temperature=1.3 让回复自然。spec §1.6。"""
from __future__ import annotations

from hub.agent.graph.state import AgentState
from hub.agent.llm_client import DeepSeekLLMClient, disable_thinking
from hub.agent.prompt.subgraph_prompts.chat import CHAT_SYSTEM_PROMPT


async def chat_subgraph(state: AgentState, *, llm: DeepSeekLLMClient) -> AgentState:
    resp = await llm.chat(
        messages=[
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
            {"role": "user", "content": state.user_message},
        ],
        temperature=1.3,
        thinking=disable_thinking(),
        max_tokens=200,
    )
    state.final_response = resp.text
    return state
