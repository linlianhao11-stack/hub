# backend/hub/agent/graph/subgraphs/adjust_stock.py
"""adjust_stock 子图 — preview (thinking on) + pending + commit。spec §6.3。

子图入口路由：
- state.confirmed_subgraph == "adjust_stock" → commit 路径
- 否则 → preview 路径
"""
from __future__ import annotations

import json
from langgraph.graph import StateGraph, START, END

from hub.agent.graph.state import AdjustStockState
from hub.agent.graph.nodes.resolve_products import resolve_products_node
from hub.agent.llm_client import enable_thinking, disable_thinking
from hub.agent.prompt.subgraph_prompts.adjust_stock import (
    PREVIEW_PROMPT, EXTRACT_PROMPT,
)
from hub.agent.tools.confirm_gate import ConfirmGate


async def extract_adjust_stock_context_node(state: AdjustStockState, *, llm) -> AdjustStockState:
    """从 user_message 抽取 product_hints + delta_qty + absolute_qty + reason。

    跨轮短消息（"选 N" / "id=N" / "确认"）跳过 LLM。
    """
    from hub.agent.graph.nodes.extract_contract_context import (
        _looks_like_pure_selection,
        _looks_like_candidate_id_reference,
    )
    if _looks_like_pure_selection(state.user_message):
        return state
    if _looks_like_candidate_id_reference(state.user_message, state.candidate_products):
        return state

    resp = await llm.chat(
        messages=[
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user", "content": state.user_message},
        ],
        thinking=disable_thinking(),
        temperature=0.0,
        max_tokens=200,
    )
    try:
        parsed = json.loads(resp.text)
    except (json.JSONDecodeError, TypeError):
        state.errors.append("extract_adjust_stock_context_json_failed")
        return state

    if parsed.get("product_hints"):
        state.extracted_hints["product_hints"] = parsed["product_hints"]
    if parsed.get("delta_qty") is not None:
        state.delta_qty = int(parsed["delta_qty"])
    if parsed.get("absolute_qty") is not None:
        state.extracted_hints["absolute_qty"] = int(parsed["absolute_qty"])
    if parsed.get("reason"):
        state.reason = parsed["reason"]
    return state


async def fetch_inventory_node(state: AdjustStockState, *, tool_executor) -> AdjustStockState:
    """拉当前库存。如果 absolute_qty 已知，从 current_qty 计算 delta_qty。"""
    if not state.product:
        return state
    try:
        result = await tool_executor("check_inventory", {
            "product_id": state.product.id,
            "warehouse_id": 0,
        })
        current_qty = None
        if isinstance(result, list) and result:
            current_qty = result[0].get("qty")
        elif isinstance(result, dict):
            current_qty = result.get("qty")
        if current_qty is not None:
            state.extracted_hints["current_qty"] = int(current_qty)
            # 用户说的是 absolute_qty → 算 delta
            absolute_qty = state.extracted_hints.get("absolute_qty")
            if absolute_qty is not None and state.delta_qty is None:
                state.delta_qty = int(absolute_qty) - int(current_qty)
    except Exception as e:
        state.errors.append(f"fetch_inventory_failed:{e}")
    return state


async def preview_adjust_stock_node(
    state: AdjustStockState, *, llm, gate: ConfirmGate
) -> AdjustStockState:
    """thinking on + canonical_payload 写 ConfirmGate。"""
    if not state.product or state.delta_qty is None:
        return state

    payload_summary = {
        "product": state.product.name,
        "current_qty": state.extracted_hints.get("current_qty"),
        "delta_qty": state.delta_qty,
        "reason": state.reason,
    }
    resp = await llm.chat(
        messages=[
            {"role": "system", "content": PREVIEW_PROMPT},
            {"role": "user", "content": json.dumps(payload_summary, ensure_ascii=False, default=str)},
        ],
        thinking=enable_thinking(),
        temperature=0.0,
        max_tokens=400,
    )
    canonical_payload = {
        "tool_name": "create_stock_adjustment_request",
        "args": {
            "product_id": state.product.id,
            "delta_qty": int(state.delta_qty),
            "reason": state.reason or "",
        },
        "preview_text": resp.text,
    }
    idempotency_key = (
        f"stk:{state.conversation_id}:{state.hub_user_id}:{state.product.id}:{state.delta_qty}"
    )
    pending = await gate.create_pending(
        hub_user_id=state.hub_user_id,
        conversation_id=state.conversation_id,
        subgraph="adjust_stock",
        action_prefix="stk",
        summary=(
            f"调 {state.product.name} 库存 "
            f"{'+' if state.delta_qty > 0 else ''}{state.delta_qty}"
        ),
        payload=canonical_payload,
        ttl_seconds=600,
        idempotency_key=idempotency_key,
    )
    state.pending_action_id = pending.action_id
    state.final_response = (
        resp.text + f'\n\n回复"确认"执行（action_id: {pending.action_id}）'
    )
    return state


async def commit_adjust_stock_node(
    state: AdjustStockState, *, tool_executor
) -> AdjustStockState:
    """从 state.confirmed_payload 执行 — 不依赖当前 state.product/delta_qty。"""
    if not state.confirmed_payload:
        state.errors.append("commit_adjust_stock_no_payload")
        state.final_response = "执行失败：没有找到确认的预览参数"
        return state
    args = state.confirmed_payload["args"]
    try:
        await tool_executor(state.confirmed_payload["tool_name"], args)
    except Exception as e:
        state.errors.append(f"commit_adjust_stock_failed:{e}")
        state.final_response = f"库存调整提交失败：{e}"
        return state
    state.final_response = "库存调整已申请，等待审核"
    return state


def _route_entry(state: AdjustStockState) -> str:
    """子图入口路由：confirmed → commit；否则 preview 链。"""
    if state.confirmed_subgraph == "adjust_stock" and state.confirmed_payload:
        return "commit"
    return "extract_context"


def build_adjust_stock_subgraph(*, llm, gate: ConfirmGate, tool_executor):
    """构建 adjust_stock StateGraph — 单函数双路径。

    preview 路径：set_origin → extract_context → resolve_products → pick_product
                  → fetch_inventory → preview → END
    commit 路径：set_origin → commit → END
    """
    async def _set_origin(s: AdjustStockState) -> AdjustStockState:
        s.active_subgraph = "adjust_stock"
        return s

    async def _extract_context(s: AdjustStockState) -> AdjustStockState:
        return await extract_adjust_stock_context_node(s, llm=llm)

    async def _resolve_products(s: AdjustStockState) -> AdjustStockState:
        return await resolve_products_node(s, llm=llm, tool_executor=tool_executor)

    async def _pick_product(s: AdjustStockState) -> AdjustStockState:
        # AdjustStockState.product is single — pick first from state.products
        if s.products and not s.product:
            s.product = s.products[0]
        return s

    async def _fetch_inventory(s: AdjustStockState) -> AdjustStockState:
        return await fetch_inventory_node(s, tool_executor=tool_executor)

    async def _preview(s: AdjustStockState) -> AdjustStockState:
        return await preview_adjust_stock_node(s, llm=llm, gate=gate)

    async def _commit(s: AdjustStockState) -> AdjustStockState:
        return await commit_adjust_stock_node(s, tool_executor=tool_executor)

    g: StateGraph = StateGraph(AdjustStockState)
    g.add_node("set_origin", _set_origin)
    g.add_node("extract_context", _extract_context)
    g.add_node("resolve_products", _resolve_products)
    g.add_node("pick_product", _pick_product)
    g.add_node("fetch_inventory", _fetch_inventory)
    g.add_node("preview", _preview)
    g.add_node("commit", _commit)

    g.add_edge(START, "set_origin")
    g.add_conditional_edges(
        "set_origin",
        _route_entry,
        {"commit": "commit", "extract_context": "extract_context"},
    )

    # preview 路径
    g.add_edge("extract_context", "resolve_products")
    g.add_edge("resolve_products", "pick_product")
    g.add_edge("pick_product", "fetch_inventory")
    g.add_edge("fetch_inventory", "preview")
    g.add_edge("preview", END)

    # commit 路径
    g.add_edge("commit", END)

    return g.compile()
