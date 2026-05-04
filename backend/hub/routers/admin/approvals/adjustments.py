"""调价 / 调库存审批路由（简化第一版）。

list / batch-approve / batch-reject
不做两阶段，直接 ERP 调用 + 状态推进
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request

import hub.routers.admin.approvals as _pkg
from hub.adapters.downstream.erp4 import (
    ErpAdapterError,
    ErpSystemError,
)
from hub.auth.admin_perms import require_hub_perm
from hub.models.audit import AuditLog
from hub.models.draft import (
    PriceAdjustmentRequest,
    StockAdjustmentRequest,
)
from hub.routers.admin.approvals.schemas import (
    BatchApprovePriceRequest,
    BatchRejectPriceRequest,
    BatchRejectStockRequest,
)

router = APIRouter(tags=["admin", "approvals"])


# ============================================================
# Price adjustment inbox（简化）
# ============================================================

@router.get(
    "/price",
    dependencies=[Depends(require_hub_perm("usecase.adjust_price.approve"))],
)
async def list_price_adjustments(status: str = "pending",
                                 limit: int = 100, offset: int = 0):
    qs = PriceAdjustmentRequest.filter(status=status).order_by("-created_at")
    total = await qs.count()
    rows = await qs.offset(offset).limit(limit).all()
    return {"items": [_serialize_price(r) for r in rows], "total": total}


def _serialize_price(r: PriceAdjustmentRequest) -> dict:
    return {
        "id": r.id,
        "requester_hub_user_id": r.requester_hub_user_id,
        "customer_id": r.customer_id,
        "product_id": r.product_id,
        "current_price": float(r.current_price) if r.current_price is not None else None,
        "new_price": float(r.new_price) if r.new_price is not None else None,
        "discount_pct": float(r.discount_pct) if r.discount_pct is not None else None,
        "reason": r.reason,
        "status": r.status,
        "created_at": r.created_at.isoformat(),
    }


@router.post(
    "/price/batch-approve",
    dependencies=[Depends(require_hub_perm("usecase.adjust_price.approve"))],
)
async def batch_approve_price(req: BatchApprovePriceRequest, request: Request):
    """简化第一版：逐条调 ERP upsert_customer_price_rule（Task 18 endpoint 完成后才真生效）。"""
    rows = await PriceAdjustmentRequest.filter(
        id__in=req.request_ids, status="pending",
    ).all()
    if len(rows) != len(req.request_ids):
        raise HTTPException(400, "包含已审/不存在的 request_id")
    actor = request.state.hub_user
    erp = _pkg.current_erp_adapter()

    approved: list[int] = []
    failed: list[dict] = []
    for r in rows:
        # I2: new_price=None 不 fallback 0.0（会破坏性写入 ERP），跳过并记录原因
        if r.new_price is None:
            failed.append({"request_id": r.id, "reason": "缺少新价格，跳过审批"})
            continue
        try:
            await erp.upsert_customer_price_rule(
                customer_id=r.customer_id, product_id=r.product_id,
                new_price=float(r.new_price),
                reason=r.reason,
                client_request_id=f"hub-price-{r.id}",
                acting_as_user_id=request.state.erp_user["id"],
            )
            r.status = "approved"
            r.approved_by_hub_user_id = actor.id
            r.approved_at = dt.datetime.now(dt.UTC)
            await r.save()
            approved.append(r.id)
        except (ErpAdapterError, ErpSystemError) as e:
            failed.append({"request_id": r.id, "reason": str(e)})

    await AuditLog.create(
        who_hub_user_id=actor.id, action="batch_approve_price",
        target_type="price_adjustment_request",
        target_id=f"batch-{len(req.request_ids)}",  # C1: 短摘要
        detail={"request_ids": req.request_ids, "approved": approved, "failed": failed},
    )
    return {"approved_count": len(approved), "approved_ids": approved, "failed": failed}


@router.post(
    "/price/batch-reject",
    dependencies=[Depends(require_hub_perm("usecase.adjust_price.approve"))],
)
async def batch_reject_price(req: BatchRejectPriceRequest, request: Request):
    """批量拒绝价格调整申请（I3: 独立 schema BatchRejectPriceRequest，用 request_ids）。"""
    rows = await PriceAdjustmentRequest.filter(
        id__in=req.request_ids, status="pending",
    ).all()
    if len(rows) != len(req.request_ids):
        raise HTTPException(400, "包含已审/不存在的 request_id")
    actor = request.state.hub_user
    for r in rows:
        r.status = "rejected"
        r.rejection_reason = req.reason
        await r.save()
    await AuditLog.create(
        who_hub_user_id=actor.id, action="batch_reject_price",
        target_type="price_adjustment_request",
        target_id=f"batch-{len(req.request_ids)}",  # C1: 短摘要
        detail={"request_ids": req.request_ids, "reason": req.reason},
    )
    return {"rejected_count": len(rows)}


# ============================================================
# Stock adjustment inbox（简化）
# ============================================================

@router.get(
    "/stock",
    dependencies=[Depends(require_hub_perm("usecase.adjust_stock.approve"))],
)
async def list_stock_adjustments(status: str = "pending",
                                 limit: int = 100, offset: int = 0):
    qs = StockAdjustmentRequest.filter(status=status).order_by("-created_at")
    total = await qs.count()
    rows = await qs.offset(offset).limit(limit).all()
    return {"items": [_serialize_stock(r) for r in rows], "total": total}


def _serialize_stock(r: StockAdjustmentRequest) -> dict:
    return {
        "id": r.id,
        "requester_hub_user_id": r.requester_hub_user_id,
        "product_id": r.product_id,
        "warehouse_id": r.warehouse_id,
        "adjustment_qty": float(r.adjustment_qty) if r.adjustment_qty is not None else None,
        "reason": r.reason,
        "status": r.status,
        "created_at": r.created_at.isoformat(),
    }


@router.post(
    "/stock/batch-approve",
    dependencies=[Depends(require_hub_perm("usecase.adjust_stock.approve"))],
)
async def batch_approve_stock(req: BatchApprovePriceRequest, request: Request):
    """简化：HUB 端标 approved；不调 ERP（管理员手动去 ERP 实操）。"""
    rows = await StockAdjustmentRequest.filter(
        id__in=req.request_ids, status="pending",
    ).all()
    if len(rows) != len(req.request_ids):
        raise HTTPException(400, "包含已审/不存在的 request_id")
    actor = request.state.hub_user
    for r in rows:
        r.status = "approved"
        r.approved_by_hub_user_id = actor.id
        r.approved_at = dt.datetime.now(dt.UTC)
        await r.save()
    await AuditLog.create(
        who_hub_user_id=actor.id, action="batch_approve_stock",
        target_type="stock_adjustment_request",
        target_id=f"batch-{len(req.request_ids)}",  # C1: 短摘要
        detail={"request_ids": req.request_ids, "approved": [r.id for r in rows]},
    )
    return {"approved_count": len(rows), "approved_ids": [r.id for r in rows]}


@router.post(
    "/stock/batch-reject",
    dependencies=[Depends(require_hub_perm("usecase.adjust_stock.approve"))],
)
async def batch_reject_stock(req: BatchRejectStockRequest, request: Request):
    """I3: 独立 schema BatchRejectStockRequest，用 request_ids。"""
    rows = await StockAdjustmentRequest.filter(
        id__in=req.request_ids, status="pending",
    ).all()
    if len(rows) != len(req.request_ids):
        raise HTTPException(400, "包含已审/不存在的 request_id")
    actor = request.state.hub_user
    for r in rows:
        r.status = "rejected"
        r.rejection_reason = req.reason
        await r.save()
    await AuditLog.create(
        who_hub_user_id=actor.id, action="batch_reject_stock",
        target_type="stock_adjustment_request",
        target_id=f"batch-{len(req.request_ids)}",  # C1: 短摘要
        detail={"request_ids": req.request_ids, "reason": req.reason},
    )
    return {"rejected_count": len(rows)}
