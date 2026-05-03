"""validate_inputs — thinking on，推理价格合理性 / items 完整性 / 缺失字段。spec §1.5。"""
from __future__ import annotations
import json
import logging
from hub.agent.graph.state import ContractState
from hub.agent.llm_client import DeepSeekLLMClient, enable_thinking

logger = logging.getLogger(__name__)


# Plan 6 v9 hotfix（task=fyFOm_hd 12:53 用户看到 "还差 customer_address、customer_phone"）：
# LLM 之前自由发挥输出非约束字段名（合同 docx 模板占位符），用户钉钉看到英文代号违反
# CLAUDE.md "中文大白话" 原则。同时 customer.address/phone/tax_id 这些是 ERP 客户档案
# 字段，缺了应在 _build_context 里用空串兜底，agent 不该再去 ask 用户。
# 限制：missing_fields 只能从下面这个白名单出（动态部分用前缀认）。
VALID_MISSING_FIELDS_FIXED = frozenset({
    "customer",            # 没解析到客户
    "items",               # 没 items
    "products",            # 没产品
    "shipping_address",    # 缺收货地址
    "shipping_contact",    # 缺收货联系人
    "shipping_phone",      # 缺收货电话
    "template",            # 没启用合同模板
})
# 动态前缀（合法）：item_qty:HINT / item_price:HINT / product_not_found:HINT /
#                  product_choice:HINT / customer_choice:NAME
VALID_MISSING_FIELDS_PREFIXES = (
    "item_qty:", "item_price:",
    "product_not_found:", "product_choice:", "customer_choice:",
)


def _is_valid_missing_field(mf: str) -> bool:
    if not isinstance(mf, str):
        return False
    if mf in VALID_MISSING_FIELDS_FIXED:
        return True
    return any(mf.startswith(p) for p in VALID_MISSING_FIELDS_PREFIXES)


VALIDATE_INPUTS_PROMPT = """你是合同输入校验器。看 state，判断 items / 价格 / 必要字段。

**输出 missing_fields 只能从下面集合里选**（不许自创）：
- "customer"           — state.customer 是 None
- "items"              — state.items 是空
- "shipping_address"   — state.shipping.address 为空且用户消息里没提到地址
- "shipping_contact"   — state.shipping.contact 为空且用户消息里没提到收货联系人
- "shipping_phone"     — state.shipping.phone 为空且用户消息里没提到电话
- "item_qty:产品名"    — 某 item 数量缺
- "item_price:产品名"  — 某 item 单价缺

**绝对不要输出**：customer_address / customer_phone / customer_tax_id /
customer_bank_name 等 — 这些是 ERP 客户档案字段，缺了由系统空白兜底，
不该让用户在钉钉里补。

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
        max_tokens=1500,
    )
    try:
        parsed = json.loads(resp.text)
        raw_missing = parsed.get("missing_fields", []) or []
        valid: list[str] = []
        invalid: list[str] = []
        for mf in raw_missing:
            if _is_valid_missing_field(mf):
                valid.append(mf)
            else:
                invalid.append(mf)
        if invalid:
            logger.warning(
                "validate_inputs LLM 输出非白名单 missing_fields，已丢弃：%s（保留合法：%s）",
                invalid, valid,
            )
        # 整字段替换 — 避免 LangGraph model_fields_set 陷阱
        state.missing_fields = valid
        if parsed.get("warnings"):
            state.errors = list(state.errors) + list(parsed["warnings"])
    except json.JSONDecodeError:
        state.errors = list(state.errors) + ["validate_inputs_json_decode_failed"]
    return state
