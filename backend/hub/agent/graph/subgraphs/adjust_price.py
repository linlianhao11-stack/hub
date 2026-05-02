# backend/hub/agent/graph/subgraphs/adjust_price.py
"""adjust_price 子图 — preview (thinking on) + pending + commit。spec §1.5 + §6.3。

子图入口先判断是 preview 路径还是 commit 路径：
- state.confirmed_subgraph == "adjust_price" → 走 commit 路径
- 否则 → 走 preview 路径（extract_context → resolve_customer → resolve_products →
                             fetch_history → preview → END）

设计选择：单函数 build_adjust_price_subgraph() + 内部条件路由（而非两个函数）。
理由：两条路径共享同一 AdjustPriceState schema 和 START/END 入口；
用 add_conditional_edges 把 set_origin 分叉比管理两个独立 compiled graph 更清晰，
主图 Phase 7 只需 wire 一个 subgraph 对象。
"""
from __future__ import annotations

import json
from decimal import Decimal
from langgraph.graph import StateGraph, START, END

from hub.agent.graph.state import AdjustPriceState
from hub.agent.graph.nodes.resolve_customer import resolve_customer_node
from hub.agent.graph.nodes.resolve_products import resolve_products_node
from hub.agent.llm_client import DeepSeekLLMClient, enable_thinking, disable_thinking
from hub.agent.prompt.subgraph_prompts.adjust_price import PREVIEW_PROMPT
from hub.agent.tools.confirm_gate import ConfirmGate


async def extract_adjust_price_context_node(state: AdjustPriceState, *, llm) -> AdjustPriceState:
    """从 user_message 抽 customer_name + product_hint + new_price。

    跨轮短消息（"选 N" / "id=N" / "确认"）跳过 LLM — 沿用 extract_contract_context 的跳过规则。
    """
    from hub.agent.graph.nodes.extract_contract_context import (
        _looks_like_pure_selection,
        _looks_like_candidate_id_reference,
    )
    if _looks_like_pure_selection(state.user_message):
        return state
    if _looks_like_candidate_id_reference(state.user_message, state.candidate_products):
        return state

    EXTRACT_PROMPT = """从用户消息抽取调价信息。输出 JSON：
{
  "customer_name": <str 或 null>,
  "product_hints": [<str>],
  "new_price": <number 或 null>
}
只输出 JSON。"""
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
        state.errors = list(state.errors) + ["extract_adjust_price_context_json_failed"]
        return state

    # 整字段替换（field reassignment）— 避免 LangGraph model_fields_set 陷阱
    new_hints = dict(state.extracted_hints)
    if parsed.get("customer_name"):
        new_hints["customer_name"] = parsed["customer_name"]
    if parsed.get("product_hints"):
        new_hints["product_hints"] = parsed["product_hints"]
    state.extracted_hints = new_hints
    if parsed.get("new_price") is not None:
        state.new_price = Decimal(str(parsed["new_price"]))
    return state


async def fetch_history_node(state: AdjustPriceState, *, tool_executor) -> AdjustPriceState:
    """拉客户对该产品的历史成交价 — 写 state.old_price + state.history_prices。"""
    if not state.customer or not state.product:
        return state
    try:
        result = await tool_executor("get_customer_history", {
            "customer_id": state.customer.id,
            "product_id": state.product.id,
            "limit": 5,
        })
        prices: list[Decimal] = []
        for item in (result or []):
            if isinstance(item, dict) and "price" in item:
                prices.append(Decimal(str(item["price"])))
        state.history_prices = prices
        if prices:
            state.old_price = prices[0]
    except Exception as e:
        state.errors = list(state.errors) + [f"fetch_history_failed:{e}"]
    return state


async def preview_adjust_price_node(
    state: AdjustPriceState, *, llm, gate: ConfirmGate
) -> AdjustPriceState:
    """thinking on + canonical_payload 写 ConfirmGate."""
    if not state.customer or not state.product or state.new_price is None:
        # 缺信息，让上游 ask_user 处理（实际 routing 在 build 时决定）
        return state

    payload_summary = {
        "customer": state.customer.name,
        "product": state.product.name,
        "old_price": float(state.old_price) if state.old_price is not None else None,
        "new_price": float(state.new_price),
        "history_prices": [float(p) for p in state.history_prices],
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
        "tool_name": "create_price_adjustment_request",
        "args": {
            "customer_id": state.customer.id,
            "product_id": state.product.id,
            "new_price": float(state.new_price),
            "reason": "",
        },
        "preview_text": resp.text,
    }
    pending = await gate.create_pending(
        hub_user_id=state.hub_user_id,
        conversation_id=state.conversation_id,
        subgraph="adjust_price",
        summary=f"调 {state.customer.name} 的 {state.product.name} 价格 {state.old_price}→{state.new_price}",
        payload=canonical_payload,
        action_prefix="adj",
    )
    state.pending_action_id = pending.action_id
    state.final_response = (
        resp.text
        + f'\n\n回复"确认"执行（action_id: {pending.action_id}）'
    )
    return state


async def commit_adjust_price_node(state: AdjustPriceState, *, tool_executor) -> AdjustPriceState:
    """从 state.confirmed_payload 执行 — 不依赖当前 state.customer/product。"""
    if not state.confirmed_payload:
        state.errors = list(state.errors) + ["commit_adjust_price_no_payload"]
        state.final_response = "执行失败：没有找到确认的预览参数"
        return state
    args = state.confirmed_payload["args"]
    try:
        await tool_executor(state.confirmed_payload["tool_name"], args)
    except Exception as e:
        state.errors = list(state.errors) + [f"commit_adjust_price_failed:{e}"]
        state.final_response = f"调价提交失败：{e}"
        return state
    state.final_response = "调价已申请，等待审核"
    return state


async def pick_product_from_products_node(state: AdjustPriceState) -> AdjustPriceState:
    """从 resolve_products 写到 state.products 的列表里取第一个写入 state.product。

    AdjustPriceState 用单值 product (调价针对单个产品)；resolve_products 复用
    AgentState.products 列表 — 这个节点是 state machine 桥接。
    """
    if state.products and not state.product:
        state.product = state.products[0]
    return state


def _route_entry(state: AdjustPriceState) -> str:
    """子图入口路由：confirmed → commit；否则 preview 链。"""
    if state.confirmed_subgraph == "adjust_price" and state.confirmed_payload:
        return "commit"
    return "extract_context"


def build_adjust_price_subgraph(*, llm, gate: ConfirmGate, tool_executor):
    """构建 adjust_price StateGraph — 单函数双路径。

    preview 路径：set_origin → extract_context → resolve_customer → resolve_products
                  → fetch_history → preview → END
    commit 路径：set_origin → commit → END
    """
    async def _set_origin(s: AdjustPriceState) -> AdjustPriceState:
        s.active_subgraph = "adjust_price"
        return s

    async def _extract_context(s: AdjustPriceState) -> AdjustPriceState:
        return await extract_adjust_price_context_node(s, llm=llm)

    async def _resolve_customer(s: AdjustPriceState) -> AdjustPriceState:
        return await resolve_customer_node(s, llm=llm, tool_executor=tool_executor)

    async def _resolve_products(s: AdjustPriceState) -> AdjustPriceState:
        return await resolve_products_node(s, llm=llm, tool_executor=tool_executor)

    async def _pick_product(s: AdjustPriceState) -> AdjustPriceState:
        return await pick_product_from_products_node(s)

    async def _fetch_history(s: AdjustPriceState) -> AdjustPriceState:
        return await fetch_history_node(s, tool_executor=tool_executor)

    async def _preview(s: AdjustPriceState) -> AdjustPriceState:
        return await preview_adjust_price_node(s, llm=llm, gate=gate)

    async def _commit(s: AdjustPriceState) -> AdjustPriceState:
        return await commit_adjust_price_node(s, tool_executor=tool_executor)

    g: StateGraph = StateGraph(AdjustPriceState)
    g.add_node("set_origin", _set_origin)
    g.add_node("extract_context", _extract_context)
    g.add_node("resolve_customer", _resolve_customer)
    g.add_node("resolve_products", _resolve_products)
    g.add_node("pick_product", _pick_product)
    g.add_node("fetch_history", _fetch_history)
    g.add_node("preview", _preview)
    g.add_node("commit", _commit)

    g.add_edge(START, "set_origin")
    g.add_conditional_edges(
        "set_origin",
        _route_entry,
        {"commit": "commit", "extract_context": "extract_context"},
    )

    # preview 路径
    g.add_edge("extract_context", "resolve_customer")
    g.add_edge("resolve_customer", "resolve_products")
    g.add_edge("resolve_products", "pick_product")
    g.add_edge("pick_product", "fetch_history")
    g.add_edge("fetch_history", "preview")
    g.add_edge("preview", END)

    # commit 路径
    g.add_edge("commit", END)

    return g.compile()
