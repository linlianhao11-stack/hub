"""凭证/调价/调库存写操作 — create_voucher_draft, request_price_adjustment, request_stock_adjustment。"""
from __future__ import annotations

from langchain_core.tools import tool

import hub.agent.react.tools.write as _w
from hub.agent.react.context import tool_ctx
from hub.agent.react.tools._confirm_helper import create_pending_action


@tool
async def create_voucher_draft(
    voucher_data: dict, rule_matched: str = "",
) -> dict:
    """**plan 阶段**: 提交财务凭证草稿（挂会计审批 inbox）。

    voucher_data 必须含字段：
      - entries: list[{account, debit, credit, ...}] 借贷分录
      - total_amount: number 凭证总额
      - summary: str 凭证摘要

    rule_matched 可选,凭证模板名（如 "sales_template"）。

    LLM 看用户消息（如"做一个销售凭证 1000 元"）后,自己构造 voucher_data dict。
    本 tool **不直接写 ERP**,只写 hub VoucherDraft 表挂 admin 审批。
    """
    c = tool_ctx.get()
    if c is None:
        return {"error": "tool_ctx 未 set"}
    if (err := await _w._check_perm(c["hub_user_id"], "usecase.create_voucher.use")):
        return err

    react_args = {"voucher_data": voucher_data, "rule_matched": rule_matched}

    async def _body() -> dict:
        if not isinstance(voucher_data, dict) or not voucher_data:
            return {"error": "voucher_data 必须是非空 dict（含 entries / total_amount / summary）"}

        payload = {
            "tool_name": "create_voucher_draft",
            "args": {
                "voucher_data": voucher_data,
                "rule_matched": rule_matched or None,
            },
        }
        total = voucher_data.get("total_amount", "?")
        desc = voucher_data.get("summary", "<无摘要>")
        summary = f"将提交财务凭证草稿:总额 {total},摘要：{desc}"
        pending = await create_pending_action(
            subgraph="voucher", summary=summary, payload=payload,
            use_idempotency=True,
        )
        return {"status": "pending_confirmation", "action_id": pending.action_id, "preview": summary}

    return await _w._audit_plan_phase(
        react_tool_name="create_voucher_draft",
        react_tool_args=react_args,
        body=_body,
    )


@tool
async def request_price_adjustment(
    customer_id: int, product_id: int, new_price: float, reason: str,
) -> dict:
    """**plan 阶段**: 提交客户专属价调整请求。新价 + 原因。需 admin 后台审批。"""
    c = tool_ctx.get()
    if c is None:
        return {"error": "tool_ctx 未 set"}
    if (err := await _w._check_perm(c["hub_user_id"], "usecase.adjust_price.use")):
        return err

    react_args = {
        "customer_id": customer_id, "product_id": product_id,
        "new_price": new_price, "reason": reason,
    }

    async def _body() -> dict:
        if not customer_id or not product_id:
            return {"error": "customer_id 和 product_id 必须传"}
        if new_price <= 0:
            return {"error": "new_price 必须 > 0"}
        if not reason or len(reason) < 2:
            return {"error": "reason 必须填写（≥2 字）"}

        payload = {
            "tool_name": "create_price_adjustment_request",
            "args": {
                "customer_id": customer_id,
                "product_id": product_id,
                "new_price": new_price,
                "reason": reason,
            },
        }
        summary = (
            f"将提交客户 id={customer_id} 商品 id={product_id} 调价至 {new_price}。\n"
            f"理由：{reason}\n等 admin 审批后生效。"
        )
        pending = await create_pending_action(
            subgraph="adjust_price", summary=summary, payload=payload,
            use_idempotency=True,
        )
        return {"status": "pending_confirmation", "action_id": pending.action_id, "preview": summary}

    return await _w._audit_plan_phase(
        react_tool_name="request_price_adjustment",
        react_tool_args=react_args,
        body=_body,
    )


@tool
async def request_stock_adjustment(
    product_id: int, adjustment_qty: float, reason: str,
    warehouse_id: int = 0,
) -> dict:
    """**plan 阶段**: 提交库存调整请求。adjustment_qty 正数加,负数减。
    warehouse_id=0 表示不指定仓库。需 admin 审批。
    """
    c = tool_ctx.get()
    if c is None:
        return {"error": "tool_ctx 未 set"}
    if (err := await _w._check_perm(c["hub_user_id"], "usecase.adjust_stock.use")):
        return err

    react_args = {
        "product_id": product_id, "adjustment_qty": adjustment_qty,
        "reason": reason, "warehouse_id": warehouse_id,
    }

    async def _body() -> dict:
        if not product_id:
            return {"error": "product_id 必须传"}
        if adjustment_qty == 0:
            return {"error": "adjustment_qty 不能为 0"}
        if not reason or len(reason) < 2:
            return {"error": "reason 必须填写（≥2 字）"}

        args = {
            "product_id": product_id,
            "adjustment_qty": adjustment_qty,
            "reason": reason,
        }
        if warehouse_id:
            args["warehouse_id"] = warehouse_id

        payload = {
            "tool_name": "create_stock_adjustment_request",
            "args": args,
        }
        sign = "+" if adjustment_qty > 0 else ""
        summary = (
            f"将提交商品 id={product_id} 库存调整 {sign}{adjustment_qty}。\n"
            f"理由：{reason}\n等 admin 审批后生效。"
        )
        pending = await create_pending_action(
            subgraph="adjust_stock", summary=summary, payload=payload,
            use_idempotency=True,
        )
        return {"status": "pending_confirmation", "action_id": pending.action_id, "preview": summary}

    return await _w._audit_plan_phase(
        react_tool_name="request_stock_adjustment",
        react_tool_args=react_args,
        body=_body,
    )
