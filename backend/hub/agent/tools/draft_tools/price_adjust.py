from __future__ import annotations

import logging

from tortoise.exceptions import IntegrityError

from hub.adapters.downstream.erp4 import (
    ErpAdapterError,
    ErpNotFoundError,
    ErpSystemError,
)
import hub.agent.tools.draft_tools as _pkg
from hub.agent.tools.types import ToolArgsValidationError

logger = logging.getLogger("hub.agent.tools.draft_tools")


CREATE_PRICE_ADJUSTMENT_REQUEST_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "create_price_adjustment_request",
        "strict": True,
        "description": "创建调价请求（挂销售主管审批 inbox）",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "customer_id",
                "product_id",
                "new_price",
                "reason",
            ],
            "properties": {
                "customer_id": {
                    "type": "integer",
                    "description": "ERP 客户 ID",
                },
                "product_id": {
                    "type": "integer",
                    "description": "ERP 商品 ID",
                },
                "new_price": {
                    "type": "number",
                    "description": "申请调整后的价格（必须大于 0）",
                },
                "reason": {
                    "type": "string",
                    "description": "调价原因（可选）；如无传 ''",
                },
            },
        },
    },
    "_subgraphs": ["adjust_price"],
}


# ============================================================
# 辅助
# ============================================================

async def _fetch_current_price(customer_id: int, product_id: int,
                                acting_as_user_id: int) -> float | None:
    """调 ERP get_product_customer_prices 取最近成交价（fail-soft）。

    I1: 只吞 ERP 网络/熔断/404 错误；RuntimeError（adapter 未初始化）让它上抛暴露 startup bug。
    """
    try:
        erp = _pkg.current_erp_adapter()
        resp = await erp.get_product_customer_prices(
            product_id=product_id, customer_id=customer_id,
            limit=1, acting_as_user_id=acting_as_user_id,
        )
        items = resp.get("items", [])
        if items:
            # M4: 0.0 也是合法价格，不用 or None
            return float(items[0].get("price", 0))
    except (ErpAdapterError, ErpSystemError, ErpNotFoundError):
        # 仅吞 ERP 网络/熔断/404；RuntimeError(adapter 未初始化) 让它上抛暴露 startup bug
        pass
    return None


def _approval_url_price(request_id: int) -> str:
    return f"/admin/approvals/price#{request_id}"


# ============================================================
# Tool 2：调价申请
# ============================================================

async def create_price_adjustment_request(
    customer_id: int,
    product_id: int,
    new_price: float,
    reason: str | None = None,
    *,
    hub_user_id: int,
    conversation_id: str,
    acting_as_user_id: int,
    confirmation_action_id: str,
) -> dict:
    """创建调价请求，挂销售主管审批 inbox。

    Args:
        customer_id: ERP 客户 ID
        product_id: ERP 商品 ID
        new_price: 申请调整后的价格
        reason: 调价原因（可选）

    M11 类型不变量：本 tool 仅写 HUB 草稿表（_pkg.PriceAdjustmentRequest），不调 ERP 写接口；
    ERP 落地由 admin 审批端点（admin/approvals/price/batch-approve）完成。
    未来重构不要把 ERP 写调用挪到此处。
    """
    # sentinel 归一化（spec §1.3 v3.4）：LLM 传 "" 当 optional → 归一化成 None
    reason = reason or None

    if new_price <= 0:
        raise ToolArgsValidationError("调价价格必须大于 0")

    # 幂等先查
    existing = await _pkg.PriceAdjustmentRequest.filter(
        requester_hub_user_id=hub_user_id,
        confirmation_action_id=confirmation_action_id,
    ).first()
    if existing is not None:
        logger.info(
            "create_price_adjustment_request idempotent_replay: "
            "user=%s action_id=%s req_id=%s",
            hub_user_id, confirmation_action_id, existing.id,
        )
        return {
            "request_id": existing.id,
            "status": existing.status,
            "approval_url": _approval_url_price(existing.id),
            "idempotent_replay": True,
        }

    # 获取当前价（fail-soft）
    current_price = await _fetch_current_price(
        customer_id=customer_id,
        product_id=product_id,
        acting_as_user_id=acting_as_user_id,
    )

    # 计算折扣比例（如果有当前价）
    # M5: clamp 到 DecimalField(max_digits=5, decimal_places=4) 上限 9.9999，防溢出
    discount_pct = None
    if current_price and current_price > 0 and new_price > 0:
        raw = new_price / current_price
        discount_pct = max(0.0, min(round(raw, 4), 9.9999))

    # INSERT
    try:
        req = await _pkg.PriceAdjustmentRequest.create(
            requester_hub_user_id=hub_user_id,
            customer_id=customer_id,
            product_id=product_id,
            current_price=current_price,
            new_price=new_price,
            discount_pct=discount_pct,
            reason=reason,
            status="pending",
            conversation_id=conversation_id,
            confirmation_action_id=confirmation_action_id,
        )
    except IntegrityError:
        existing = await _pkg.PriceAdjustmentRequest.filter(
            requester_hub_user_id=hub_user_id,
            confirmation_action_id=confirmation_action_id,
        ).first()
        if existing is not None:
            logger.info(
                "create_price_adjustment_request concurrent IntegrityError → replay: "
                "user=%s action_id=%s req_id=%s",
                hub_user_id, confirmation_action_id, existing.id,
            )
            return {
                "request_id": existing.id,
                "status": existing.status,
                "approval_url": _approval_url_price(existing.id),
                "idempotent_replay": True,
            }
        raise

    return {
        "request_id": req.id,
        "status": req.status,
        "approval_url": _approval_url_price(req.id),
        "current_price": current_price,
        "new_price": new_price,
        "discount_pct": discount_pct,
        "idempotent_replay": False,
        "message": "调价请求已创建，等待销售主管审批。",
    }
