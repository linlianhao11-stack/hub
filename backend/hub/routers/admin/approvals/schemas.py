from __future__ import annotations

from pydantic import BaseModel, Field


class VoucherDraftRow(BaseModel):
    """voucher list row（admin 后台展示）。"""
    id: int
    requester_hub_user_id: int
    voucher_data: dict
    rule_matched: str | None
    status: str
    created_at: str
    creating_started_at: str | None
    erp_voucher_id: int | None
    rejection_reason: str | None


class BatchApproveVoucherRequest(BaseModel):
    draft_ids: list[int] = Field(..., min_length=1, max_length=50)


class BatchRejectVoucherRequest(BaseModel):
    draft_ids: list[int] = Field(..., min_length=1, max_length=50)
    reason: str = Field(..., min_length=1, max_length=500)


class BatchApprovePriceRequest(BaseModel):
    request_ids: list[int] = Field(..., min_length=1, max_length=50)


class BatchRejectPriceRequest(BaseModel):
    """I3: 独立 schema（用 request_ids 与 approve 保持一致）。"""
    request_ids: list[int] = Field(..., min_length=1, max_length=50)
    reason: str = Field(..., min_length=1, max_length=500)


class BatchRejectStockRequest(BaseModel):
    """I3: 独立 schema（用 request_ids 与 approve 保持一致）。"""
    request_ids: list[int] = Field(..., min_length=1, max_length=50)
    reason: str = Field(..., min_length=1, max_length=500)
