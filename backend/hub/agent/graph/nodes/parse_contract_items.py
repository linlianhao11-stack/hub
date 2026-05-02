# backend/hub/agent/graph/nodes/parse_contract_items.py
"""parse_contract_items — 把 user_message 里的 qty/price 跟 state.products 对齐进 state.items。

thinking on（推理对齐关系）；缺 qty/price 必须 missing_fields，**不**默认填值。
"""
from __future__ import annotations
import json
from decimal import Decimal
from hub.agent.graph.state import ContractState, ContractItem
from hub.agent.llm_client import DeepSeekLLMClient, enable_thinking


PARSE_ITEMS_PROMPT = """你是合同 items 对齐器。读用户消息 + 已解析的产品列表，把数量 / 价格跟 product_id 对齐。

输入（v1.10：优先 items_raw，跨轮安全）：
- products: [{id, name, sku?}]
- 二选一：
    - items_raw: [{hint, qty, price}, ...]  // extract_contract_context 已抽出的原始数据
    - user_message: 用户原文                  // 极端兜底，items_raw 也为空时

输出严格 JSON：
{
  "items": [
    {"product_id": <int>, "qty": <int 或 null>, "price": <number 或 null>},
    ...
  ]
}

规则：
- 优先用 items_raw 做 hint → product_id 模糊匹配（hint 包含/被 name/sku 包含）
- qty / price 是 null 就传 null（不要默认 1 / 不要默认 list_price）
- 数量 0 / 负数 / 价格 0 / 负数 — 都按 null 处理（无效值）
- 用户提一个产品对应一个 item；不要补全没提到的产品
- 只输出 JSON，不要解释
"""


async def parse_contract_items_node(state: ContractState, *, llm: DeepSeekLLMClient) -> ContractState:
    # NOTE: 所有 state 列表字段必须用整字段替换（field reassignment），避免 LangGraph model_fields_set 陷阱。
    if state.candidate_products:
        return state
    if not state.products:
        if "products" not in state.missing_fields:
            state.missing_fields = list(state.missing_fields) + ["products"]
        return state

    products_for_prompt = [{"id": p.id, "name": p.name, "sku": p.sku, "list_price": str(p.list_price) if p.list_price is not None else None} for p in state.products]

    items_raw = (state.extracted_hints or {}).get("items_raw")
    if items_raw:
        parsed = {"items": []}
        for raw in items_raw:
            hint = (raw.get("hint") or "").lower()
            matched = next(
                (p for p in state.products
                 if hint and (hint in p.name.lower() or (p.sku and hint in p.sku.lower()))),
                None,
            )
            if matched:
                # price=null 且产品有 list_price → 用 list_price 作为兜底（报价场景）
                price = raw.get("price")
                if price is None and matched.list_price is not None:
                    price = float(matched.list_price)
                parsed["items"].append({
                    "product_id": matched.id, "qty": raw.get("qty"), "price": price,
                })
        if len(parsed["items"]) != len(items_raw):
            parsed = None
    else:
        parsed = None

    if parsed is None:
        fallback_input = {"products": products_for_prompt}
        if items_raw:
            fallback_input["items_raw"] = items_raw
        else:
            fallback_input["user_message"] = state.user_message
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": PARSE_ITEMS_PROMPT},
                {"role": "user", "content": json.dumps(fallback_input, ensure_ascii=False)},
            ],
            thinking=enable_thinking(),
            temperature=0.0,
            max_tokens=4000,
        )
        try:
            parsed = json.loads(resp.text)
        except json.JSONDecodeError:
            state.errors = list(state.errors) + ["parse_items_json_decode_failed"]
            return state

    name_by_id = {p.id: p.name for p in state.products}
    valid_items: list[ContractItem] = []
    new_missing = list(state.missing_fields)
    new_errors = list(state.errors)
    for raw in parsed.get("items", []):
        pid = raw.get("product_id")
        qty = raw.get("qty")
        price = raw.get("price")
        name = name_by_id.get(pid, str(pid))
        if qty is None or qty <= 0:
            new_missing.append(f"item_qty:{name}")
            continue
        if price is None or price <= 0:
            new_missing.append(f"item_price:{name}")
            continue
        if pid not in name_by_id:
            new_errors.append(f"parse_items_unknown_product_id:{pid}")
            continue
        valid_items.append(ContractItem(
            product_id=pid, name=name, qty=int(qty), price=Decimal(str(price)),
        ))
    state.items = valid_items
    state.missing_fields = new_missing
    state.errors = new_errors
    return state
