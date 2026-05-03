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
from langchain_core.tools import tool

from hub.agent.react.context import tool_ctx
from hub.agent.react.tools._confirm_helper import create_pending_action
from hub.permissions import require_permissions


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


@tool
async def create_contract_draft(
    customer_id: int,
    items: list[dict],
    shipping_address: str,
    shipping_contact: str,
    shipping_phone: str,
    payment_terms: str = "",
    tax_rate: str = "",
) -> dict:
    """**plan 阶段**: 提交销售合同生成请求。本 tool **不直接生成 docx**,
    而是把请求落到 ConfirmGate pending,返 preview 给用户看,等用户确认后由
    confirm_action tool 真正执行 + 渲染 + 发钉钉。

    参数：
      customer_id: ERP 客户 ID（必须先 search_customer 拿到真实 id）
      items: [{"product_id": int, "qty": int, "price": number}, ...]
      shipping_address: 收货地址
      shipping_contact: 收货联系人
      shipping_phone: 收货电话
      payment_terms: 付款方式（默认空,admin 后台审批时补）
      tax_rate: 税率字符串（默认空）

    返 {status, action_id, preview} 三件套。
    """
    c = tool_ctx.get()
    if c is None:
        return {"error": "tool_ctx 未 set"}

    await require_permissions(c["hub_user_id"], ["usecase.generate_contract.use"])

    if not items:
        return {"error": "items 不能为空,合同至少要有一项商品"}
    if not customer_id:
        return {"error": "customer_id 必须传"}

    template_id = await _resolve_default_template_id()
    if template_id is None:
        return {"error": "未启用销售合同模板,请联系管理员到 admin 后台上传"}

    payload = {
        "tool_name": "generate_contract_draft",
        "args": {
            "template_id": template_id,
            "customer_id": customer_id,
            "items": items,
            "shipping_address": shipping_address,
            "shipping_contact": shipping_contact,
            "shipping_phone": shipping_phone,
            "payment_terms": payment_terms,
            "tax_rate": tax_rate,
        },
    }

    summary = (
        f"将给客户 id={customer_id} 生成销售合同：\n"
        f"  {_format_items_preview(items)}\n"
        f"  收货：{shipping_address} / {shipping_contact} / {shipping_phone}"
    )
    pending = await create_pending_action(
        subgraph="contract", summary=summary, payload=payload,
    )
    return {
        "status": "pending_confirmation",
        "action_id": pending.action_id,
        "preview": summary,
    }
