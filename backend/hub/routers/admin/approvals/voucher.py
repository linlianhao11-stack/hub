"""凭证审批路由（两阶段提交）。

phase1: pending → creating（5 min 租约）→ ERP create_voucher（含 client_request_id 幂等）→ created
phase2: created → ERP batch_approve_vouchers → approved
崩溃恢复：creating 租约过期可重新拿锁
in_progress 暴露：未过租约的 creating 不进 phase1，单独返让 UI 显示"处理中"
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from tortoise.expressions import Q

import hub.routers.admin.approvals as _pkg
from hub.adapters.downstream.erp4 import (
    ErpAdapterError,
    ErpSystemError,
)
from hub.auth.admin_perms import require_hub_perm
from hub.models.audit import AuditLog
from hub.models.draft import VoucherDraft
from hub.routers.admin.approvals.schemas import (
    BatchApproveVoucherRequest,
    BatchRejectVoucherRequest,
    VoucherDraftRow,
)

router = APIRouter(tags=["admin", "approvals"])


@router.get(
    "/voucher",
    dependencies=[Depends(require_hub_perm("usecase.create_voucher.approve"))],
)
async def list_voucher_drafts(status: str = "pending",
                              limit: int = 100, offset: int = 0):
    """列出 voucher_draft（按 status 筛选 + 分页）。"""
    qs = VoucherDraft.filter(status=status).order_by("-created_at")
    total = await qs.count()
    rows = await qs.offset(offset).limit(limit).all()
    return {
        "items": [VoucherDraftRow(
            id=r.id,
            requester_hub_user_id=r.requester_hub_user_id,
            voucher_data=r.voucher_data,
            rule_matched=r.rule_matched,
            status=r.status,
            created_at=r.created_at.isoformat(),
            creating_started_at=r.creating_started_at.isoformat() if r.creating_started_at else None,
            erp_voucher_id=r.erp_voucher_id,
            rejection_reason=r.rejection_reason,
        ).model_dump() for r in rows],
        "total": total,
    }


@router.post(
    "/voucher/batch-approve",
    dependencies=[Depends(require_hub_perm("usecase.create_voucher.approve"))],
)
async def batch_approve_vouchers(req: BatchApproveVoucherRequest, request: Request):
    """两阶段提交 + creating 崩溃恢复。"""
    now = dt.datetime.now(dt.UTC)  # M8: 统一取 now，phase1/2 时间戳一致
    lease_cutoff = now - _pkg.LEASE_TIMEOUT

    drafts = await VoucherDraft.filter(
        id__in=req.draft_ids,
        status__in=["pending", "creating", "created"],
    ).all()
    if len(drafts) != len(req.draft_ids):
        raise HTTPException(400, "包含已审/已拒/不存在的 draft_id")

    # 把 creating 但租约未过期的标"in_progress"，单独返让 UI 显示"处理中"
    in_progress: list[dict] = []
    actionable_drafts: list = []
    for d in drafts:
        if d.status == "creating" and (
            d.creating_started_at is None or d.creating_started_at >= lease_cutoff
        ):
            in_progress.append({
                "draft_id": d.id,
                "since": d.creating_started_at.isoformat() if d.creating_started_at else None,
                "lease_expires_at": (
                    (d.creating_started_at + _pkg.LEASE_TIMEOUT).isoformat()
                    if d.creating_started_at else None
                ),
                "reason": "另一会话正在处理此草稿，请稍后重试或等租约自动过期",
            })
            # M7: 友好中文说明
            in_progress[-1]["reason"] = "该凭证正在被另一位审批员处理（5 分钟内自动释放）"
        else:
            actionable_drafts.append(d)

    actor = request.state.hub_user
    creation_failures: list[dict] = []
    erp = _pkg.current_erp_adapter()

    # ========== Phase 1: pending / creating(过期) → creating → ERP create → created ==========
    todo_drafts = [d for d in actionable_drafts if d.status in ("pending", "creating")]
    for d in todo_drafts:
        rows_updated = await VoucherDraft.filter(id=d.id).filter(
            Q(status="pending")
            | (Q(status="creating") & Q(creating_started_at__lt=lease_cutoff)),
        ).update(
            status="creating",
            creating_started_at=now,
        )
        if rows_updated == 0:
            # I5: 并发抢占，加入 in_progress 让 UI 告知用户
            in_progress.append({
                "draft_id": d.id,
                "since": None,
                "lease_expires_at": None,
                "reason": "并发抢占：另一位审批员刚刚开始处理此凭证",
            })
            continue

        try:
            erp_resp = await erp.create_voucher(
                voucher_data=d.voucher_data,
                client_request_id=f"hub-draft-{d.id}",
                acting_as_user_id=request.state.erp_user["id"],
            )
            d.erp_voucher_id = erp_resp["id"]
            d.status = "created"
            d.creating_started_at = None
            await d.save()
        except (ErpAdapterError, ErpSystemError) as e:
            await VoucherDraft.filter(id=d.id).update(
                status="pending", creating_started_at=None,
            )
            creation_failures.append({"draft_id": d.id, "reason": str(e)})

    # 重新拉一次：含 phase1 新创建 + 入参原本 status=created
    created_drafts = await VoucherDraft.filter(
        id__in=req.draft_ids, status="created",
    ).all()

    if not created_drafts:
        # 早返回：写 audit log
        # M1: 拆两种 early_return_reason
        if in_progress and len(in_progress) == len(req.draft_ids):
            early_return_reason = "all_drafts_in_progress"
        else:
            early_return_reason = "all_phase1_creates_failed"
        await AuditLog.create(
            who_hub_user_id=actor.id, action="batch_approve_vouchers",
            target_type="voucher_draft",
            target_id=f"batch-{len(req.draft_ids)}",  # C1: 短摘要，防 13+ ID 超 64 字符
            detail={
                "draft_ids": req.draft_ids,  # 完整 ids 在 detail（无长度限制）
                "approved": [],
                "creation_failed": creation_failures,
                "approve_failed": [],
                "in_progress": [p["draft_id"] for p in in_progress],
                "early_return_reason": early_return_reason,
            },
        )
        return {
            "approved_count": 0, "approved_draft_ids": [],
            "creation_failed": creation_failures,  # M2: 拆 creation_failed
            "approve_failed": [],                   # M2: 拆 approve_failed
            "in_progress": in_progress,
        }

    # ========== Phase 2: created → ERP batch_approve → approved ==========
    erp_voucher_ids = [d.erp_voucher_id for d in created_drafts]
    try:
        result = await erp.batch_approve_vouchers(
            voucher_ids=erp_voucher_ids, acting_as_user_id=request.state.erp_user["id"],
        )
        approved_set = set(result.get("success") or [])
        failed_map = {f["id"]: f.get("reason", "ERP 拒绝") for f in (result.get("failed") or [])}
    except (ErpAdapterError, ErpSystemError) as e:
        # 整个 batch-approve 失败：所有 created 保持 created（可重试 approve）
        approved_set = set()
        failed_map = {vid: f"ERP batch-approve 异常: {e}" for vid in erp_voucher_ids}

    approved_draft_ids: list[int] = []
    approve_failures: list[dict] = []
    for d in created_drafts:
        if d.erp_voucher_id in approved_set:
            d.status = "approved"
            d.approved_by_hub_user_id = actor.id
            d.approved_at = now  # M8: 用函数顶部统一取的 now
            await d.save()
            approved_draft_ids.append(d.id)
        else:
            approve_failures.append({
                "draft_id": d.id, "erp_voucher_id": d.erp_voucher_id,
                "reason": failed_map.get(d.erp_voucher_id, "ERP 拒绝"),
            })

    await AuditLog.create(
        who_hub_user_id=actor.id, action="batch_approve_vouchers",
        target_type="voucher_draft",
        target_id=f"batch-{len(req.draft_ids)}",  # C1: 短摘要，防 13+ ID 超 64 字符
        detail={
            "draft_ids": req.draft_ids,  # 完整 ids 在 detail（无长度限制）
            "approved": approved_draft_ids,
            "creation_failed": creation_failures,
            "approve_failed": approve_failures,
            "in_progress": [p["draft_id"] for p in in_progress],
        },
    )
    return {
        "approved_count": len(approved_draft_ids),
        "approved_draft_ids": approved_draft_ids,
        "creation_failed": creation_failures,   # M2: 拆 creation_failed
        "approve_failed": approve_failures,      # M2: 拆 approve_failed
        "in_progress": in_progress,
    }


@router.post(
    "/voucher/batch-reject",
    dependencies=[Depends(require_hub_perm("usecase.create_voucher.approve"))],
)
async def batch_reject_vouchers(req: BatchRejectVoucherRequest, request: Request):
    """仅 pending 状态可拒；created 已落 ERP 不能在 HUB 端拒（要去 ERP 反审）。"""
    drafts = await VoucherDraft.filter(
        id__in=req.draft_ids, status="pending",
    ).all()
    if len(drafts) != len(req.draft_ids):
        raise HTTPException(
            400, "包含 created/已拒/已通过/不存在的 draft（请到 ERP 反审）",
        )
    actor = request.state.hub_user
    for d in drafts:
        d.status = "rejected"
        d.rejection_reason = req.reason
        await d.save()
    await AuditLog.create(
        who_hub_user_id=actor.id, action="batch_reject_vouchers",
        target_type="voucher_draft",
        target_id=f"batch-{len(req.draft_ids)}",  # C1: 短摘要
        detail={"rejected": req.draft_ids, "reason": req.reason},
    )
    return {"rejected_count": len(drafts)}
