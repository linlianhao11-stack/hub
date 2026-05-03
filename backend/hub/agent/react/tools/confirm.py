"""confirm_action tool — 用户确认 pending action 后真正执行业务。

PendingAction API 工作流（v9 路径）：
  1. list_pending_for_context() 找当前 (conv, user) 下 action_id 对应 PendingAction
  2. 用 PendingAction.token 调 gate.claim() 原子消费（HDEL pending）
  3. 按 PendingAction.payload["tool_name"] 在 WRITE_TOOL_DISPATCH 找业务函数 dispatch
  4. 调用底层函数（通过 invoke_business_tool 拿权限校验 + 审计 log）

⚠️ **不**用旧 ChainAgent claim_action / mark_confirmed / restore_action 协议。
失败语义：claim 已消费（HDEL 不可逆）→ 不能 restore。靠业务函数自身幂等
（generate_contract_draft 已有 fingerprint 幂等; voucher/price/stock 用
confirmation_action_id 做 DB 唯一约束）+ 用户重发请求触发新 pending 来恢复。

**confirmation_action_id 注入**：voucher / price / stock 三个底层函数的 kwargs
必填 confirmation_action_id 作为 DB 幂等 key。dispatch 时把当前 action_id 注入。
contract / quote 的底层不需要这个 kwarg（用 fingerprint 做幂等）。
"""
from __future__ import annotations
from typing import Any, Awaitable, Callable

from langchain_core.tools import tool

from hub.agent.react.context import tool_ctx
from hub.agent.react.tools._confirm_helper import _gate
from hub.agent.react.tools._invoke import invoke_business_tool
from hub.agent.tools import generate_tools, draft_tools
from hub.agent.tools.confirm_gate import CrossContextClaim


# Dispatch 表：payload.tool_name = 底层函数名 → (perm, fn, needs_action_id)
WRITE_TOOL_DISPATCH: dict[str, tuple[str, Callable[..., Awaitable[Any]], bool]] = {
    "generate_contract_draft": (
        "usecase.generate_contract.use",
        generate_tools.generate_contract_draft,
        False,
    ),
    "generate_price_quote": (
        "usecase.generate_quote.use",
        generate_tools.generate_price_quote,
        False,
    ),
    "create_voucher_draft": (
        "usecase.create_voucher.use",
        draft_tools.create_voucher_draft,
        True,
    ),
    "create_price_adjustment_request": (
        "usecase.adjust_price.use",
        draft_tools.create_price_adjustment_request,
        True,
    ),
    "create_stock_adjustment_request": (
        "usecase.adjust_stock.use",
        draft_tools.create_stock_adjustment_request,
        True,
    ),
}


@tool
async def confirm_action(action_id: str) -> dict:
    """**用户确认上一条 pending action 后调本 tool 真正执行。**

    使用时机：上一轮某个写 tool 返回了 {status: "pending_confirmation", action_id, preview},
    把 preview 自然语言告诉用户后,用户回"是" / "确认" / "好的" 等确认词 → LLM 调本 tool,
    传 action_id 真正触发执行。

    成功返业务结果（如 {draft_id, file_sent}）；
    失败返 {error: "..."} (action_id 失效 / 不在 dispatch 表 / 业务执行异常等)。
    """
    c = tool_ctx.get()
    if c is None:
        return {"error": "tool_ctx 未 set"}

    gate = _gate()

    pendings = await gate.list_pending_for_context(
        conversation_id=c["conversation_id"],
        hub_user_id=c["hub_user_id"],
    )
    pending = next((p for p in pendings if p.action_id == action_id), None)
    if pending is None:
        return {"error": f"action_id {action_id} 不存在或已过期 — 请重新发起请求"}

    try:
        await gate.claim(
            action_id=action_id,
            token=pending.token,
            hub_user_id=c["hub_user_id"],
            conversation_id=c["conversation_id"],
        )
    except CrossContextClaim as e:
        return {"error": f"action 失效: {e}"}

    payload = pending.payload
    tool_name = payload.get("tool_name") or ""
    args: dict = dict(payload.get("args") or {})
    entry = WRITE_TOOL_DISPATCH.get(tool_name)
    if entry is None:
        return {"error": f"不支持的 tool_name: {tool_name}"}
    perm, fn, needs_action_id = entry

    extra_ctx = {
        "hub_user_id": c["hub_user_id"],
        "conversation_id": c["conversation_id"],
    }
    if needs_action_id:
        extra_ctx["confirmation_action_id"] = action_id

    try:
        return await invoke_business_tool(
            tool_name=tool_name,
            perm=perm,
            args=args,
            fn=fn,
            extra_ctx_kwargs=extra_ctx,
        )
    except Exception as e:
        return {
            "error": f"执行失败: {type(e).__name__}: {e}（请重发请求生成新草稿）"
        }
