# backend/hub/agent/graph/nodes/format_response.py
"""format_response — prefix 强制 BOT 回执风格。spec §1.2 应用 B/C。"""
from __future__ import annotations
from hub.agent.graph.state import AgentState
from hub.agent.llm_client import DeepSeekLLMClient, disable_thinking

FORMAT_PROMPTS = {
    "contract": "合同已生成：",
    "quote": "报价单已生成：",
    "voucher": "凭证已起草：",
    "adjust_price": "调价已申请，等待审核：",
    "adjust_stock": "库存调整已申请：",
    "confirm_done": "已为您处理：",
}


async def format_response_node(
    state: AgentState,
    *,
    llm: DeepSeekLLMClient,
    template_key: str,
    summary: str,
) -> AgentState:
    """LLM + prefix 强制开头风格。temperature=0.7 让回执有点变化但不啰嗦。"""
    prefix = FORMAT_PROMPTS.get(template_key, "完成：")
    resp = await llm.chat(
        messages=[
            {"role": "system", "content": "你是 ERP 业务回执生成器。把内部 summary 转成给钉钉用户看的简短回执，1-2 行。"},
            {"role": "user", "content": summary},
        ],
        prefix_assistant=prefix,
        thinking=disable_thinking(),
        temperature=0.7,
        max_tokens=200,
    )
    state.final_response = prefix + resp.text
    return state
