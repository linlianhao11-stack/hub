from __future__ import annotations

import logging

from tortoise.exceptions import IntegrityError

from hub.agent.tools.types import ToolArgsValidationError
import hub.agent.tools.draft_tools as _pkg

logger = logging.getLogger("hub.agent.tools.draft_tools")


CREATE_STOCK_ADJUSTMENT_REQUEST_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "create_stock_adjustment_request",
        "strict": True,
        "description": "创建库存调整请求（挂库管/财务审批 inbox）",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "product_id",
                "adjustment_qty",
                "reason",
                "warehouse_id",
            ],
            "properties": {
                "product_id": {
                    "type": "integer",
                    "description": "ERP 商品 ID",
                },
                "adjustment_qty": {
                    "type": "number",
                    "description": "调整数量（正数增加，负数减少，不能为 0）",
                },
                "reason": {
                    "type": "string",
                    "description": "调整原因（可选）；如无传 ''",
                },
                "warehouse_id": {
                    "type": "integer",
                    "description": "仓库 ID（可选）；如无传 0（0 表示不指定仓库）",
                },
            },
        },
    },
    "_subgraphs": ["adjust_stock"],
}


# ============================================================
# 辅助
# ============================================================

def _approval_url_stock(request_id: int) -> str:
    return f"/admin/approvals/stock#{request_id}"


# ============================================================
# Tool 3：库存调整申请
# ============================================================

async def create_stock_adjustment_request(
    product_id: int,
    adjustment_qty: float,
    reason: str | None = None,
    warehouse_id: int | None = None,
    *,
    hub_user_id: int,
    conversation_id: str,
    acting_as_user_id: int,
    confirmation_action_id: str,
) -> dict:
    """创建库存调整请求，挂库管/财务审批 inbox。

    Args:
        product_id: ERP 商品 ID
        adjustment_qty: 调整数量（正数增加，负数减少）
        reason: 调整原因（可选）
        warehouse_id: 仓库 ID（可选）

    M11 类型不变量：本 tool 仅写 HUB 草稿表（_pkg.StockAdjustmentRequest），不调 ERP 写接口；
    ERP 落地由 admin 审批端点（admin/approvals/stock/batch-approve）完成。
    未来重构不要把 ERP 写调用挪到此处。

    M12 参数命名：plan §2522 字面写 delta_quantity: int，但实际模型字段
    （_pkg.StockAdjustmentRequest.adjustment_qty）以及 ERP 接口都用 adjustment_qty: float。
    本 tool 与模型字段保持一致。
    """
    # sentinel 归一化（spec §1.3 v3.4）：LLM 传 "" 当 optional str → None；
    # warehouse_id: int | None — 0 视为"未传"（schema 描述约定 0 = 不指定仓库），归一化成 None
    reason = reason or None
    if warehouse_id is not None and warehouse_id == 0:
        warehouse_id = None

    if adjustment_qty == 0:
        raise ToolArgsValidationError("调整数量不能为 0")

    # 幂等先查
    existing = await _pkg.StockAdjustmentRequest.filter(
        requester_hub_user_id=hub_user_id,
        confirmation_action_id=confirmation_action_id,
    ).first()
    if existing is not None:
        logger.info(
            "create_stock_adjustment_request idempotent_replay: "
            "user=%s action_id=%s req_id=%s",
            hub_user_id, confirmation_action_id, existing.id,
        )
        return {
            "request_id": existing.id,
            "status": existing.status,
            "approval_url": _approval_url_stock(existing.id),
            "idempotent_replay": True,
        }

    # INSERT
    try:
        req = await _pkg.StockAdjustmentRequest.create(
            requester_hub_user_id=hub_user_id,
            product_id=product_id,
            warehouse_id=warehouse_id,
            adjustment_qty=adjustment_qty,
            reason=reason,
            status="pending",
            conversation_id=conversation_id,
            confirmation_action_id=confirmation_action_id,
        )
    except IntegrityError:
        existing = await _pkg.StockAdjustmentRequest.filter(
            requester_hub_user_id=hub_user_id,
            confirmation_action_id=confirmation_action_id,
        ).first()
        if existing is not None:
            logger.info(
                "create_stock_adjustment_request concurrent IntegrityError → replay: "
                "user=%s action_id=%s req_id=%s",
                hub_user_id, confirmation_action_id, existing.id,
            )
            return {
                "request_id": existing.id,
                "status": existing.status,
                "approval_url": _approval_url_stock(existing.id),
                "idempotent_replay": True,
            }
        raise

    return {
        "request_id": req.id,
        "status": req.status,
        "approval_url": _approval_url_stock(req.id),
        "product_id": product_id,
        "adjustment_qty": adjustment_qty,
        "warehouse_id": warehouse_id,
        "idempotent_replay": False,
        "message": "库存调整请求已创建，等待库管/财务审批。",
    }
