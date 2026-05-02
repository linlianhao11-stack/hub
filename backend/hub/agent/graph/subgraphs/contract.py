# backend/hub/agent/graph/subgraphs/contract.py
"""contract 子图 — LangGraph state machine。spec §3 + plan v1.1 P1-A。

节点流：
  set_origin → extract_contract_context → resolve_customer → ...
"""
from __future__ import annotations
from langgraph.graph import StateGraph, START, END

from hub.agent.graph.state import ContractState, ShippingInfo
from hub.agent.graph.nodes.resolve_customer import resolve_customer_node
from hub.agent.graph.nodes.resolve_products import resolve_products_node
from hub.agent.graph.nodes.parse_contract_items import parse_contract_items_node
from hub.agent.graph.nodes.extract_contract_context import extract_contract_context_node
from hub.agent.graph.nodes.validate_inputs import validate_inputs_node
from hub.agent.graph.nodes.ask_user import ask_user_node
from hub.agent.graph.nodes.format_response import format_response_node
from hub.agent.llm_client import DeepSeekLLMClient


async def generate_contract_node(state: ContractState, *, llm, tool_executor) -> ContractState:
    payload = {
        "customer_id": state.customer.id,
        "items": [{"product_id": i.product_id, "qty": i.qty, "price": float(i.price)}
                  for i in state.items],
        "shipping_address": state.shipping.address or "",
        "contact": state.shipping.contact or "",
        "phone": state.shipping.phone or "",
        "extras": {},
    }
    result = await tool_executor("generate_contract_draft", payload)
    state.draft_id = result.get("draft_id")
    state.file_sent = True
    return state


async def cleanup_after_contract_node(state: ContractState) -> ContractState:
    # items 不清空 — parse_contract_items 每次都重新生成，保留供 e2e 测试验证
    # 其余跨轮工作字段（候选、客户、产品、地址、hints、缺字段）全部重置
    state.active_subgraph = None
    state.candidate_customers = []
    state.candidate_products = {}
    state.customer = None
    state.products = []
    state.shipping = ShippingInfo()
    state.extracted_hints = {}
    state.missing_fields = []
    return state


def _route_after_resolve_products(state: ContractState) -> str:
    if state.candidate_customers or state.candidate_products:
        return "ask_user"
    return "parse_contract_items"


def _route_after_parse_items(state: ContractState) -> str:
    if any(mf.startswith("item_") for mf in state.missing_fields):
        return "ask_user"
    return "validate_inputs"


def _route_after_validate(state: ContractState) -> str:
    return "ask_user" if state.missing_fields else "generate_contract"


def build_contract_subgraph(*, llm: DeepSeekLLMClient, tool_executor):
    async def _set_origin(s: ContractState):
        s.active_subgraph = "contract"
        return s
    async def _extract_context(s):
        return await extract_contract_context_node(s, llm=llm)
    async def _resolve_customer(s):
        return await resolve_customer_node(s, llm=llm, tool_executor=tool_executor)
    async def _resolve_products(s):
        return await resolve_products_node(s, llm=llm, tool_executor=tool_executor)
    async def _parse_items(s):
        return await parse_contract_items_node(s, llm=llm)
    async def _validate(s):
        return await validate_inputs_node(s, llm=llm)
    async def _ask_user(s):
        return await ask_user_node(s)
    async def _generate(s):
        return await generate_contract_node(s, llm=llm, tool_executor=tool_executor)
    async def _format(s):
        shipping_info = ""
        if s.shipping and s.shipping.address:
            shipping_info = f", 收货地址={s.shipping.address}"
        return await format_response_node(
            s, llm=llm, template_key="contract",
            summary=(
                f"draft_id={s.draft_id}, customer={s.customer.name if s.customer else 'unknown'}, "
                f"items={len(s.items)}{shipping_info}"
            ),
        )
    async def _cleanup(s):
        return await cleanup_after_contract_node(s)

    g = StateGraph(ContractState)
    g.add_node("set_origin", _set_origin)
    g.add_node("extract_contract_context", _extract_context)
    g.add_node("resolve_customer", _resolve_customer)
    g.add_node("resolve_products", _resolve_products)
    g.add_node("parse_contract_items", _parse_items)
    g.add_node("validate_inputs", _validate)
    g.add_node("ask_user", _ask_user)
    g.add_node("generate_contract", _generate)
    g.add_node("format_response", _format)
    g.add_node("cleanup_after_contract", _cleanup)
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
        {"ask_user": "ask_user", "validate_inputs": "validate_inputs"},
    )
    g.add_conditional_edges(
        "validate_inputs", _route_after_validate,
        {"ask_user": "ask_user", "generate_contract": "generate_contract"},
    )
    g.add_edge("ask_user", END)
    g.add_edge("generate_contract", "format_response")
    g.add_edge("format_response", "cleanup_after_contract")
    g.add_edge("cleanup_after_contract", END)
    return g.compile()
