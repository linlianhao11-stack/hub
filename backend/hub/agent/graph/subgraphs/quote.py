# backend/hub/agent/graph/subgraphs/quote.py
"""quote 子图 — 4 节点（set_origin/resolve_customer/resolve_products/parse_items/generate_quote）。

v1.6 P1-A + v1.7 P2-B：必须有 set_origin 节点写 active_subgraph="quote"，
generate_quote 成功后清候选 + active_subgraph，与 contract 子图同模式。
"""
from __future__ import annotations
from langgraph.graph import StateGraph, START, END

from hub.agent.graph.state import QuoteState, ShippingInfo
from hub.agent.graph.nodes.extract_contract_context import extract_contract_context_node
from hub.agent.graph.nodes.resolve_customer import resolve_customer_node
from hub.agent.graph.nodes.resolve_products import resolve_products_node
from hub.agent.graph.nodes.parse_contract_items import parse_contract_items_node
from hub.agent.graph.nodes.ask_user import ask_user_node
from hub.agent.graph.nodes.format_response import format_response_node
from hub.agent.llm_client import DeepSeekLLMClient


async def generate_quote_node(state: QuoteState, *, llm, tool_executor) -> QuoteState:
    """调 generate_price_quote — strict + 写 tool fail closed。

    cleanup 在 cleanup_after_quote_node 做，不在这里 — 让 format_response 先用
    state.customer.name / len(state.items) 写回执。
    """
    payload = {
        "customer_id": state.customer.id,
        "items": [{"product_id": i.product_id, "qty": i.qty, "price": float(i.price)}
                  for i in state.items],
    }
    result = await tool_executor("generate_price_quote", payload)
    state.quote_id = result.get("quote_id")
    state.file_sent = True
    return state


async def cleanup_after_quote_node(state: QuoteState) -> QuoteState:
    """P1 v1.11：报价流程完成后清完整工作上下文，放在 format_response 后。"""
    state.active_subgraph = None
    state.candidate_customers = []
    state.candidate_products = {}
    state.customer = None
    state.products = []
    state.items = []
    state.shipping = ShippingInfo()
    state.extracted_hints = {}
    state.missing_fields = []
    return state


def _route_after_resolve_products(state: QuoteState) -> str:
    if state.candidate_customers or state.candidate_products:
        return "ask_user"
    return "parse_contract_items"


def _route_after_parse_items(state: QuoteState) -> str:
    if any(mf.startswith("item_") for mf in state.missing_fields):
        return "ask_user"
    return "generate_quote"


def build_quote_subgraph(*, llm: DeepSeekLLMClient, tool_executor):
    async def _set_origin(s: QuoteState):
        s.active_subgraph = "quote"
        return s
    async def _extract_context(s):
        return await extract_contract_context_node(s, llm=llm)
    async def _resolve_customer(s):
        return await resolve_customer_node(s, llm=llm, tool_executor=tool_executor)
    async def _resolve_products(s):
        return await resolve_products_node(s, llm=llm, tool_executor=tool_executor)
    async def _parse_items(s):
        return await parse_contract_items_node(s, llm=llm)
    async def _ask_user(s):
        return await ask_user_node(s)
    async def _generate(s):
        return await generate_quote_node(s, llm=llm, tool_executor=tool_executor)
    async def _format(s):
        return await format_response_node(
            s, llm=llm, template_key="quote",
            summary=f"quote_id={s.quote_id}, customer={s.customer.name if s.customer else 'unknown'}, items={len(s.items)}",
        )
    async def _cleanup(s):
        return await cleanup_after_quote_node(s)

    g = StateGraph(QuoteState)
    g.add_node("set_origin", _set_origin)
    g.add_node("extract_contract_context", _extract_context)
    g.add_node("resolve_customer", _resolve_customer)
    g.add_node("resolve_products", _resolve_products)
    g.add_node("parse_contract_items", _parse_items)
    g.add_node("ask_user", _ask_user)
    g.add_node("generate_quote", _generate)
    g.add_node("format_response", _format)
    g.add_node("cleanup_after_quote", _cleanup)
    g.add_edge(START, "set_origin")
    g.add_edge("set_origin", "extract_contract_context")
    g.add_edge("extract_contract_context", "resolve_customer")
    g.add_conditional_edges(
        "resolve_customer",
        lambda s: "ask_user" if s.candidate_customers or "customer" in s.missing_fields else "resolve_products",
        {"ask_user": "ask_user", "resolve_products": "resolve_products"},
    )
    g.add_conditional_edges(
        "resolve_products", _route_after_resolve_products,
        {"ask_user": "ask_user", "parse_contract_items": "parse_contract_items"},
    )
    g.add_conditional_edges(
        "parse_contract_items", _route_after_parse_items,
        {"ask_user": "ask_user", "generate_quote": "generate_quote"},
    )
    g.add_edge("ask_user", END)
    g.add_edge("generate_quote", "format_response")
    g.add_edge("format_response", "cleanup_after_quote")
    g.add_edge("cleanup_after_quote", END)
    return g.compile()
