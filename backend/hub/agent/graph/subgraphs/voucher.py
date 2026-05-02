# backend/hub/agent/graph/subgraphs/voucher.py
"""voucher 子图 — preview (thinking on) + 强幂等 + commit。spec §6.3。"""
from __future__ import annotations
import json
from langgraph.graph import StateGraph, START, END

from hub.agent.graph.state import VoucherState
from hub.agent.llm_client import DeepSeekLLMClient, enable_thinking, disable_thinking
from hub.agent.prompt.subgraph_prompts.voucher import (
    VOUCHER_SYSTEM_PROMPT, PREVIEW_PROMPT, EXTRACT_PROMPT,
)
from hub.agent.tools.confirm_gate import ConfirmGate, CrossContextIdempotency


VALID_VOUCHER_TYPES = {"outbound", "inbound"}


def _resolve_voucher_type(state: VoucherState) -> str | None:
    """P1-B v1.4：从 state.voucher_type / extracted_hints / user_message 解析。
    返回 None 表示无法判定（让 caller 走 ask_user）。"""
    if state.voucher_type in VALID_VOUCHER_TYPES:
        return state.voucher_type
    hint = (state.extracted_hints or {}).get("voucher_type")
    if hint in VALID_VOUCHER_TYPES:
        return hint
    msg = state.user_message or ""
    if "入库" in msg or "收货" in msg:
        return "inbound"
    if "出库" in msg or "发货" in msg:
        return "outbound"
    return None


async def extract_voucher_context_node(state: VoucherState, *, llm) -> VoucherState:
    from hub.agent.graph.nodes.extract_contract_context import _looks_like_pure_selection
    if _looks_like_pure_selection(state.user_message):
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
    except json.JSONDecodeError:
        state.errors.append("extract_voucher_context_json_failed")
        return state
    if parsed.get("order_id") is not None and state.order_id is None:
        state.order_id = int(parsed["order_id"])
    if parsed.get("voucher_type") and not state.voucher_type:
        state.voucher_type = parsed["voucher_type"]
    return state


async def preview_voucher_node(state: VoucherState, *, llm, gate, tool_executor) -> VoucherState:
    voucher_type = _resolve_voucher_type(state)
    if voucher_type is None:
        if "voucher_type" not in state.missing_fields:
            state.missing_fields.append("voucher_type")
        state.final_response = "请问要做出库凭证还是入库凭证？"
        return state
    state.voucher_type = voucher_type

    if state.order_id is None:
        state.missing_fields.append("order_id")
        state.final_response = "请提供订单号"
        return state

    order = await tool_executor("get_order_detail", {"order_id": state.order_id})
    if not order or order.get("status") != "approved":
        state.errors.append(f"voucher_order_not_approved:{state.order_id}")
        state.final_response = f"订单 {state.order_id} 未审批，不能出凭证"
        return state
    if order.get(f"{voucher_type}_voucher_count", 0) > 0:
        type_label = "出库" if voucher_type == "outbound" else "入库"
        state.errors.append(f"voucher_already_exists:{state.order_id}:{voucher_type}")
        state.final_response = f"订单 {state.order_id} 已有{type_label}凭证，不重复出"
        return state

    payload_summary = {
        "order_id": state.order_id,
        "voucher_type": voucher_type,
        "items": order.get("items", []),
    }
    resp = await llm.chat(
        messages=[
            {"role": "system", "content": PREVIEW_PROMPT},
            {"role": "user", "content": json.dumps(payload_summary, ensure_ascii=False, default=str)},
        ],
        thinking=enable_thinking(),
        temperature=0.0,
        max_tokens=500,
    )

    canonical_payload = {
        "tool_name": "create_voucher_draft",
        "args": {
            "order_id": state.order_id,
            "voucher_type": voucher_type,
            "items": order.get("items", []),
            "remark": "",
        },
        "preview_text": resp.text,
    }
    idempotency_key = f"vch:{state.order_id}:{voucher_type}"
    type_label = "出库" if voucher_type == "outbound" else "入库"
    try:
        pending = await gate.create_pending(
            hub_user_id=state.hub_user_id,
            conversation_id=state.conversation_id,
            subgraph="voucher",
            action_prefix="vch",
            summary=f"{type_label}凭证 SO-{state.order_id}",
            payload=canonical_payload,
            ttl_seconds=43200,  # 12 小时
            idempotency_key=idempotency_key,
        )
    except CrossContextIdempotency:
        # P1-B v1.3：跨 context 幂等命中必须 fail closed
        state.errors.append(f"voucher_pending_in_other_context:{state.order_id}:{voucher_type}")
        state.final_response = (
            f"订单 {state.order_id} 的{type_label}凭证已有凭证申请待确认/处理中，"
            f"请联系发起人或等待其完成。"
        )
        return state
    state.pending_action_id = pending.action_id
    state.final_response = resp.text + f'\n\n回复"确认"提交凭证（action_id: {pending.action_id}）'
    return state


async def commit_voucher_node(state: VoucherState, *, tool_executor) -> VoucherState:
    if not state.confirmed_payload:
        state.errors.append("commit_voucher_no_payload")
        state.final_response = "执行失败：没有找到确认的预览参数"
        return state
    args = state.confirmed_payload["args"]
    try:
        result = await tool_executor(state.confirmed_payload["tool_name"], args)
    except Exception as e:
        state.errors.append(f"commit_voucher_failed:{e}")
        state.final_response = f"凭证提交失败：{e}"
        return state
    state.voucher_id = (result or {}).get("voucher_id")
    state.final_response = f"凭证已提交（{state.voucher_id}），等待审批"
    return state


def _route_entry(state: VoucherState) -> str:
    if state.confirmed_subgraph == "voucher" and state.confirmed_payload:
        return "commit"
    return "extract_context"


def build_voucher_subgraph(*, llm, gate, tool_executor):
    async def _set_origin(s: VoucherState):
        s.active_subgraph = "voucher"
        return s
    async def _extract_context(s):
        return await extract_voucher_context_node(s, llm=llm)
    async def _preview(s):
        return await preview_voucher_node(s, llm=llm, gate=gate, tool_executor=tool_executor)
    async def _commit(s):
        return await commit_voucher_node(s, tool_executor=tool_executor)

    g = StateGraph(VoucherState)
    g.add_node("set_origin", _set_origin)
    g.add_node("extract_context", _extract_context)
    g.add_node("preview", _preview)
    g.add_node("commit", _commit)

    g.add_edge(START, "set_origin")
    g.add_conditional_edges(
        "set_origin", _route_entry,
        {"commit": "commit", "extract_context": "extract_context"},
    )
    g.add_edge("extract_context", "preview")
    g.add_edge("preview", END)
    g.add_edge("commit", END)
    return g.compile()
