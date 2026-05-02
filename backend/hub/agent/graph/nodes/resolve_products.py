# backend/hub/agent/graph/nodes/resolve_products.py
"""resolve_products — 只解析产品身份；多命中歧义不默认取 [0]；不填 items。

P1-A 关键边界：items（含 qty / price / product_id 对齐）由 parse_contract_items 填，
本节点只负责"用户提到的产品名 → ProductInfo"。
"""
from __future__ import annotations
import json
from typing import Awaitable, Callable
from hub.agent.graph.state import ContractState, ProductInfo
from hub.agent.llm_client import DeepSeekLLMClient, ToolClass, disable_thinking
from hub.agent.tools.erp_tools import SEARCH_PRODUCTS_SCHEMA


RESOLVE_PRODUCTS_PROMPT = """根据用户消息找产品。强制调 search_products 一次或多次，
参数用 extracted_hints.product_hints 里每个 hint。多产品时合并搜或多次搜都可。
"""


def _try_consume_product_selection(message: str, candidates: list) -> "ProductInfo | None":
    """P2-C v1.2 / P1-B v1.5：识别"选 N" / "1" / "id=X" / 名字 — 同 customer 选择逻辑。"""
    if not candidates:
        return None
    import re
    msg = message.strip()
    m = re.search(r"选\s*([1-9])", msg) or re.search(r"\b([1-9])\b", msg)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
    m = re.search(r"id\s*[=:：]?\s*(\d+)", msg, re.IGNORECASE)
    if m:
        target = int(m.group(1))
        for p in candidates:
            if p.id == target:
                return p
    return None


async def resolve_products_node(
    state: ContractState,
    *,
    llm: DeepSeekLLMClient,
    tool_executor: Callable[[str, dict], Awaitable[object]],
) -> ContractState:
    if state.products and not state.candidate_products:
        return state

    if state.candidate_products:
        groups = list(state.candidate_products.items())
        # 单组候选：照旧消费（"选 N" / 名字 / id=N 都允许）
        if len(groups) == 1:
            hint, candidates = groups[0]
            chosen = _try_consume_product_selection(state.user_message, candidates)
            if chosen:
                state.products.append(chosen)
                state.missing_fields = [
                    m for m in state.missing_fields if m != f"product_choice:{hint}"
                ]
                state.candidate_products = {}
            return state

        # 多组候选：仅允许 product_id 精确选
        import re as _re
        msg = state.user_message or ""
        ids_in_msg = {int(x) for x in _re.findall(r"id\s*[=:：]?\s*(\d+)", msg, _re.IGNORECASE)}
        new_candidate_products: dict = {}
        for hint, candidates in groups:
            chosen = next((p for p in candidates if p.id in ids_in_msg), None)
            if chosen:
                state.products.append(chosen)
                state.missing_fields = [
                    m_ for m_ in state.missing_fields if m_ != f"product_choice:{hint}"
                ]
            else:
                new_candidate_products[hint] = candidates
        state.candidate_products = new_candidate_products
        return state

    resp = await llm.chat(
        messages=[
            {"role": "system", "content": RESOLVE_PRODUCTS_PROMPT},
            {"role": "user", "content": f"消息：{state.user_message}\nhints: {state.extracted_hints.get('product_hints', [])}"},
        ],
        tools=[SEARCH_PRODUCTS_SCHEMA],
        tool_choice="required",
        thinking=disable_thinking(),
        temperature=0.0,
        tool_class=ToolClass.READ,
    )
    if not resp.tool_calls:
        state.errors.append("resolve_products_no_tool_call")
        state.missing_fields.append("products")
        return state

    hints = state.extracted_hints.get("product_hints") or []
    if not hints:
        # 兜底：单次合并搜
        args = json.loads(resp.tool_calls[0]["function"]["arguments"])
        results = await tool_executor("search_products", args)
        if not results:
            state.missing_fields.append("products")
            return state
        if len(results) == 1:
            r = results[0]
            state.products.append(ProductInfo(id=r["id"], name=r["name"],
                                                sku=r.get("sku"), color=r.get("color"),
                                                spec=r.get("spec"), list_price=r.get("list_price")))
        else:
            state.candidate_products["__merged__"] = [
                ProductInfo(id=r["id"], name=r["name"], sku=r.get("sku"),
                              color=r.get("color"), spec=r.get("spec"))
                for r in results
            ]
            state.missing_fields.append("product_choice:__merged__")
        return state

    # 每个 hint 单独搜
    for hint in hints:
        results = await tool_executor("search_products", {"query": hint})
        if len(results) == 0:
            state.missing_fields.append(f"product_not_found:{hint}")
            continue
        if len(results) == 1:
            r = results[0]
            state.products.append(ProductInfo(
                id=r["id"], name=r["name"], sku=r.get("sku"),
                color=r.get("color"), spec=r.get("spec"),
                list_price=r.get("list_price"),
            ))
            continue
        state.candidate_products[hint] = [
            ProductInfo(id=r["id"], name=r["name"], sku=r.get("sku"),
                          color=r.get("color"), spec=r.get("spec"),
                          list_price=r.get("list_price"))
            for r in results
        ]
        state.missing_fields.append(f"product_choice:{hint}")

    if not state.products and not state.candidate_products:
        state.missing_fields.append("products")
    return state
