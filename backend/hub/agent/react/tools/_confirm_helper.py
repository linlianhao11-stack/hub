"""写 tool 共用 ConfirmGate helper（PendingAction API,v9 路径）。

write tool plan 阶段流程:
1. 业务参数校验
2. 构造 canonical payload {tool_name, args}
3. 调 gate.create_pending(subgraph, summary, payload) → PendingAction
4. 返 {status: "pending_confirmation", action_id, preview}（**不返 token**,token 在
   confirm_action 内部从 list_pending_for_context 反查）

execute 阶段（confirm_action tool 内调用）见 confirm.py：
1. list_pending_for_context 找当前 (conv, user) 的 PendingAction
2. 用 PendingAction.token 调 gate.claim() 原子消费
3. dispatch 到 WRITE_TOOL_DISPATCH 真正业务函数

⚠️ 不用旧 claim_action / restore_action / mark_confirmed —— 那是 ChainAgent 两步协议。
"""
from __future__ import annotations
from typing import Any

from hub.agent.react.context import tool_ctx
from hub.agent.tools.confirm_gate import ConfirmGate, PendingAction


_CONFIRM_GATE: ConfirmGate | None = None


def set_confirm_gate(gate: ConfirmGate | None) -> None:
    """worker.py 启动时调,注入 ConfirmGate 单例。测试也用它注入 fake gate / 真 fakeredis gate。"""
    global _CONFIRM_GATE
    _CONFIRM_GATE = gate


def _gate() -> ConfirmGate:
    if _CONFIRM_GATE is None:
        raise RuntimeError(
            "ConfirmGate 未注入 — 应在 worker startup 调 set_confirm_gate(gate)"
        )
    return _CONFIRM_GATE


def _canonical_idempotency_key(payload: dict) -> str:
    """对同一 (conv, user) 重复发同样 args 的写请求,生成稳定 idempotency_key。

    payload = {"tool_name": str, "args": dict}。args 内部 dict / list 用 sort_keys
    canonicalize,所以 LLM 哪怕字段顺序换、空格变化也能命中复用。

    ConfirmGate.create_pending 内部把 idempotency_key 跟 (conv, user) 一起
    HSET → 命中复用,跨 context 命中 fail-closed (CrossContextIdempotency)。

    场景：用户连续两次发"做凭证..." → React tool 第二次 create_pending 时
    idempotency_key 命中第一次 → 复用同一 PendingAction → 同一 action_id →
    confirm_action 一次成功后第二次 claim 拒（pending HDEL）。
    """
    import hashlib
    import json
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, default=str,
    )
    return f"react-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]}"


async def create_pending_action(
    *, subgraph: str, summary: str, payload: dict,
    use_idempotency: bool = False,
) -> PendingAction:
    """写 ConfirmGate pending,返 PendingAction（含 action_id + token）。

    Args:
        subgraph: 业务分类（"contract" / "quote" / "voucher" /
            "adjust_price" / "adjust_stock"）— ConfirmGate.create_pending 必填字段。
        summary: 给用户看的预览文案。
        payload: {"tool_name": str, "args": dict} canonical 格式。confirm_action
            从 PendingAction.payload 解出来 dispatch。
        use_idempotency: 是否给 ConfirmGate 传 canonical idempotency_key。
            voucher / price / stock 三个写 tool **必须传 True**。
    """
    c = tool_ctx.get()
    if c is None:
        raise RuntimeError("tool_ctx 未 set — react agent 入口必须先 set 才能调 tool")
    gate = _gate()
    kwargs = dict(
        hub_user_id=c["hub_user_id"],
        conversation_id=c["conversation_id"],
        subgraph=subgraph,
        summary=summary,
        payload=payload,
    )
    if use_idempotency:
        kwargs["idempotency_key"] = _canonical_idempotency_key(payload)
    return await gate.create_pending(**kwargs)
