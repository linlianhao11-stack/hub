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
from typing import Any, Awaitable, Callable

from langchain_core.tools import tool

from hub.agent.react.context import tool_ctx
from hub.agent.react.tools._confirm_helper import create_pending_action
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

    if (err := await _check_perm(c["hub_user_id"], "usecase.generate_contract.use")):
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
            use_idempotency=True,  # 同 args 重发复用同一 PendingAction,防 LLM 误调
                                    # create_contract_draft 而不是 confirm_action 时
                                    # 创建多条 stale pending
        )
        return {
            "status": "pending_confirmation",
            "action_id": pending.action_id,
            "preview": summary,
        }

    return await _audit_plan_phase(
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
    if (err := await _check_perm(c["hub_user_id"], "usecase.generate_quote.use")):
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
            f"  {_format_items_preview(items)}"
        )
        pending = await create_pending_action(
            subgraph="quote", summary=summary, payload=payload,
            use_idempotency=True,  # 同 args 重发复用同一 PendingAction（同 contract 理由）
        )
        return {"status": "pending_confirmation", "action_id": pending.action_id, "preview": summary}

    return await _audit_plan_phase(
        react_tool_name="create_quote_draft",
        react_tool_args=react_args,
        body=_body,
    )


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
    if (err := await _check_perm(c["hub_user_id"], "usecase.create_voucher.use")):
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

    return await _audit_plan_phase(
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
    if (err := await _check_perm(c["hub_user_id"], "usecase.adjust_price.use")):
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

    return await _audit_plan_phase(
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
    if (err := await _check_perm(c["hub_user_id"], "usecase.adjust_stock.use")):
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

    return await _audit_plan_phase(
        react_tool_name="request_stock_adjustment",
        react_tool_args=react_args,
        body=_body,
    )
