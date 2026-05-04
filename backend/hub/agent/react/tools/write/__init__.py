"""Write tools — plan-then-execute 模式。

plan 阶段（LLM 调写 tool）：
  1. 校验权限（require_permissions, fail-closed）
  2. 业务参数校验
  3. 内部解析需要的字段（如 contract 的 template_id 从 hub.contract_template 表查）
  4. 构造 canonical payload {tool_name=底层函数名, args=底层签名严格对齐}
  5. create_pending → 拿 PendingAction (含 action_id + token)
  6. 返 {status: "pending_confirmation", action_id, preview}（不返 token）

execute 阶段（LLM 调 confirm_action(action_id)）：
  → 见 confirm.py — list_pending_for_context 反查 PendingAction + claim() 消费 +
    按 payload.tool_name 在 WRITE_TOOL_DISPATCH 找底层函数 + 调用（dispatch 时把
    当前 action_id 作为 confirmation_action_id 传给 voucher / price / stock 三个底层）

关键约定：payload.tool_name = **底层函数名**（如 "generate_contract_draft"）,
不是 React tool 名（"create_contract_draft"）。这样 dispatch 直接按底层函数名查表。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from hub.agent.react.context import tool_ctx
from hub.agent.tools.confirm_gate import CrossContextIdempotency
from hub.error_codes import BizError
from hub.observability.tool_logger import log_tool_call
from hub.permissions import require_permissions


async def _check_perm(hub_user_id: int, perm: str) -> dict | None:
    """权限校验 wrapper —— 命中返 None,不命中返中文 error dict 给 LLM。

    跟 invoke_business_tool 保持一致：BizError 必须转 dict 不能 raise,否则 LangGraph
    ToolNode handle_tool_errors 会吃异常,LLM 看到英文 "Please fix your mistakes"。
    """
    try:
        await require_permissions(hub_user_id, [perm])
        return None
    except BizError as e:
        return {"error": f"权限不足: {e}"}


async def _resolve_default_template_id() -> int | None:
    """选默认销售合同模板：第一条 is_active=True + template_type='sales'。"""
    from hub.models.contract import ContractTemplate
    tpl = (
        await ContractTemplate.filter(is_active=True, template_type="sales")
        .order_by("id").first()
    )
    return tpl.id if tpl else None


def _format_items_preview(items: list[dict]) -> str:
    """把 items 列表渲染成简短预览文本。"""
    if not items:
        return "(无)"
    parts = []
    for i, it in enumerate(items, 1):
        parts.append(
            f"{i}. 商品 id={it.get('product_id')} × {it.get('qty')} 件 @ {it.get('price')}"
        )
    return "\n  ".join(parts)


async def _audit_plan_phase(
    *,
    react_tool_name: str,
    react_tool_args: dict,
    body: Callable[[], Awaitable[dict]],
) -> dict:
    """plan 阶段统一审计 wrapper —— 把 LLM 调用 + 校验结果 + create_pending 全程包到
    `log_tool_call` 里写 tool_call_log。

    跟 invoke_business_tool（read tool 用）模式对齐：require_permissions 在 wrapper
    外面（perm denial 抛 BizError 不在审计里）；validation / create_pending /
    业务返值都被 log_tool_call 捕获。

    **跨 context 幂等保护**：voucher / price / stock 三类写 tool 用 use_idempotency=True
    给 ConfirmGate 传 idempotency_key。如果同 idempotency_key 已经在另一个
    (conversation, hub_user) 里持有 PendingAction,ConfirmGate 抛
    `CrossContextIdempotency`。这里统一捕获,转成稳定的 fail-closed dict 返给 LLM,
    避免异常冒出 LangChain tool 调用链让 ReAct turn 走通用错误兜底（用户看"AI 处理失败"
    比"该申请已在其他会话处理中"差很多）。
    """
    c = tool_ctx.get()
    if c is None:
        return {"error": "tool_ctx 未 set"}
    async with log_tool_call(
        conversation_id=c["conversation_id"],
        hub_user_id=c["hub_user_id"],
        round_idx=0,
        tool_name=react_tool_name,
        args=react_tool_args,
    ) as log_ctx:
        try:
            result = await body()
        except CrossContextIdempotency as e:
            result = {
                "error": (
                    f"该申请已在其他会话处理中,本会话不能复用同一份 pending。"
                    f"如确实是新需求,请稍微改下参数（如金额、备注）后再试。"
                    f"（{type(e).__name__}: {e}）"
                ),
            }
        log_ctx.set_result(result)
        return result


# -- 子模块 import + re-export 所有 @tool 函数 --
from hub.agent.react.tools.write.contract_write import (  # noqa: E402
    create_contract_draft,
    create_quote_draft,
)
from hub.agent.react.tools.write.draft_write import (  # noqa: E402
    create_voucher_draft,
    request_price_adjustment,
    request_stock_adjustment,
)

__all__ = [
    "create_contract_draft",
    "create_quote_draft",
    "create_voucher_draft",
    "request_price_adjustment",
    "request_stock_adjustment",
    "_check_perm",
    "_resolve_default_template_id",
]
