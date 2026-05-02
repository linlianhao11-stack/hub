# backend/hub/agent/graph/nodes/confirm.py
"""confirm_node — 0/1/>1 pending 三分支。spec §6.3。"""
from __future__ import annotations
import re
from hub.agent.graph.state import AgentState
from hub.agent.tools.confirm_gate import ConfirmGate


async def confirm_node(state: AgentState, *, gate: ConfirmGate) -> AgentState:
    pendings = await gate.list_pending_for_context(
        conversation_id=state.conversation_id, hub_user_id=state.hub_user_id,
    )
    if not pendings:
        state.final_response = "您要确认什么？本会话没有待办的操作。"
        return state

    selected = None
    if len(pendings) > 1:
        m = re.search(r"\b(\d+)\b", state.user_message)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(pendings):
                selected = pendings[idx]
        if not selected:
            for p in pendings:
                if p.action_id in state.user_message:
                    selected = p
                    break

        if not selected:
            lines = ["您有以下待确认操作，请回复编号或 action_id："]
            for i, p in enumerate(pendings, 1):
                lines.append(f"{i}) [{p.action_id}] {p.summary}")
            state.final_response = "\n".join(lines)
            return state
    else:
        selected = pendings[0]

    try:
        await gate.claim(
            action_id=selected.action_id, token=selected.token,
            hub_user_id=state.hub_user_id, conversation_id=state.conversation_id,
        )
    except Exception as e:
        state.final_response = f"该确认已失效或属于他人：{e}"
        # 整字段替换 — 避免 LangGraph model_fields_set 陷阱
        state.errors = list(state.errors) + [f"confirm_claim_failed:{e}"]
        return state

    state.errors = []
    state.confirmed_subgraph = selected.subgraph
    state.confirmed_action_id = selected.action_id
    state.confirmed_payload = selected.payload
    return state
