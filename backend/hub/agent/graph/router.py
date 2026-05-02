# backend/hub/agent/graph/router.py
"""Router node — prefix JSON + Intent enum + ValueError 兜底。spec §6.2。"""
from __future__ import annotations

from hub.agent.graph.state import AgentState, Intent
from hub.agent.llm_client import DeepSeekLLMClient, disable_thinking
from hub.agent.prompt.intent_router import ROUTER_SYSTEM_PROMPT


async def router_node(state: AgentState, *, llm: DeepSeekLLMClient) -> AgentState:
    """轻量 LLM 调用做意图分类。"""
    resp = await llm.chat(
        messages=[
            {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": state.user_message},
        ],
        prefix_assistant='{"intent": "',
        stop=['",'],
        max_tokens=20,
        temperature=0.0,
        thinking=disable_thinking(),
    )
    intent_str = resp.text.split('"')[0].strip().lower()
    # 注意：Intent.__members__ 是大写名，不是续写出的小写 value；必须用 Intent(value) 构造 + ValueError 兜底
    try:
        state.intent = Intent(intent_str)
    except ValueError:
        state.intent = Intent.UNKNOWN
    return state
