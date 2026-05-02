"""validate_inputs — thinking on，推理价格合理性 / items 完整性 / 缺失字段。spec §1.5。"""
from __future__ import annotations
import json
from hub.agent.graph.state import ContractState
from hub.agent.llm_client import DeepSeekLLMClient, enable_thinking


VALIDATE_INPUTS_PROMPT = """你是合同输入校验器。看 state，判断：
1. items 是否完整（每个产品都有 qty / price）
2. 价格是否合理（不为 0 / 不为负数 / 不极端低）
3. 必要字段是否齐全（customer / shipping_address / contact / phone — 部分可选）

输出 JSON：
{
  "missing_fields": ["shipping_address", ...],
  "warnings": ["价格 X1=0.01 异常低", ...]
}

只输出 JSON，不要解释。
"""


async def validate_inputs_node(state: ContractState, *, llm: DeepSeekLLMClient) -> ContractState:
    state_summary = {
        "customer": state.customer.model_dump() if state.customer else None,
        "products": [p.model_dump() for p in state.products],
        "items": [i.model_dump() for i in state.items],
        "shipping": state.shipping.model_dump(),
    }
    resp = await llm.chat(
        messages=[
            {"role": "system", "content": VALIDATE_INPUTS_PROMPT},
            {"role": "user", "content": json.dumps(state_summary, default=str, ensure_ascii=False)},
        ],
        thinking=enable_thinking(),
        temperature=0.0,
        max_tokens=300,
    )
    try:
        parsed = json.loads(resp.text)
        state.missing_fields = parsed.get("missing_fields", [])
        if parsed.get("warnings"):
            state.errors.extend(parsed["warnings"])
    except json.JSONDecodeError:
        state.errors.append("validate_inputs_json_decode_failed")
    return state
