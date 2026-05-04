"""合同/报价写操作 — create_contract_draft, create_quote_draft。"""
from __future__ import annotations

from langchain_core.tools import tool

import hub.agent.react.tools.write as _w
from hub.agent.react.context import tool_ctx
from hub.agent.react.tools._confirm_helper import create_pending_action


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

    if (err := await _w._check_perm(c["hub_user_id"], "usecase.generate_contract.use")):
        return err

    react_args = {
        "customer_id": customer_id,
        "items": items,
        "shipping_address": shipping_address,
        "shipping_contact": shipping_contact,
        "shipping_phone": shipping_phone,
        "payment_terms": payment_terms,
        "tax_rate": tax_rate,
    }

    async def _body() -> dict:
        if not items:
            return {"error": "items 不能为空,合同至少要有一项商品"}
        if not customer_id:
            return {"error": "customer_id 必须传"}

        template_id = await _w._resolve_default_template_id()
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
            f"  {_w._format_items_preview(items)}\n"
            f"  收货：{shipping_address} / {shipping_contact} / {shipping_phone}"
        )
        pending = await create_pending_action(
            subgraph="contract", summary=summary, payload=payload,
            use_idempotency=True,  # 同 args 重发复用同一 PendingAction,防 LLM 误调
                                    # create_contract_draft 而不是 confirm_action 时
                                    # 创建多条 stale pending
        )
        return {
            "status": "pending_confirmation",
            "action_id": pending.action_id,
            "preview": summary,
        }

    return await _w._audit_plan_phase(
        react_tool_name="create_contract_draft",
        react_tool_args=react_args,
        body=_body,
    )


@tool
async def create_quote_draft(
    customer_id: int,
    items: list[dict],
    shipping_address: str = "",
    shipping_contact: str = "",
    shipping_phone: str = "",
) -> dict:
    """**plan 阶段**: 提交报价单生成请求。返 pending_confirmation。
    shipping 字段对报价单可选（报价单不一定有收货地址）。
    """
    c = tool_ctx.get()
    if c is None:
        return {"error": "tool_ctx 未 set"}
    if (err := await _w._check_perm(c["hub_user_id"], "usecase.generate_quote.use")):
        return err

    react_args = {
        "customer_id": customer_id,
        "items": items,
        "shipping_address": shipping_address,
        "shipping_contact": shipping_contact,
        "shipping_phone": shipping_phone,
    }

    async def _body() -> dict:
        if not items:
            return {"error": "items 不能为空"}
        if not customer_id:
            return {"error": "customer_id 必须传"}

        extras: dict = {}
        if shipping_address:
            extras["shipping_address"] = shipping_address
        if shipping_contact:
            extras["shipping_contact"] = shipping_contact
        if shipping_phone:
            extras["shipping_phone"] = shipping_phone

        payload = {
            "tool_name": "generate_price_quote",
            "args": {
                "customer_id": customer_id,
                "items": items,
                "extras": extras,
            },
        }
        summary = (
            f"将给客户 id={customer_id} 生成报价单：\n"
            f"  {_w._format_items_preview(items)}"
        )
        pending = await create_pending_action(
            subgraph="quote", summary=summary, payload=payload,
            use_idempotency=True,  # 同 args 重发复用同一 PendingAction（同 contract 理由）
        )
        return {"status": "pending_confirmation", "action_id": pending.action_id, "preview": summary}

    return await _w._audit_plan_phase(
        react_tool_name="create_quote_draft",
        react_tool_args=react_args,
        body=_body,
    )
