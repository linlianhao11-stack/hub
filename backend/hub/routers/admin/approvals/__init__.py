"""审批 inbox 路由包（voucher / price / stock）。

拆分自原 approvals.py，按两个独立状态机组织：
- voucher.py：凭证审批（两阶段提交）
- adjustments.py：调价 / 调库存审批（简化同步）

共享状态（_erp_adapter / LEASE_TIMEOUT）在本模块定义，子模块通过包引用获取。
"""
from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter

from hub.adapters.downstream.erp4 import Erp4Adapter
from hub.models.draft import (
    PriceAdjustmentRequest,
    StockAdjustmentRequest,
    VoucherDraft,
)

logger = logging.getLogger("hub.routers.admin.approvals")

LEASE_TIMEOUT = dt.timedelta(minutes=5)

# 模块级注入（main.py lifespan 设）
_erp_adapter: Erp4Adapter | None = None


def set_erp_adapter(adapter: Erp4Adapter | None) -> None:
    global _erp_adapter
    _erp_adapter = adapter


def current_erp_adapter() -> Erp4Adapter:
    if _erp_adapter is None:
        raise RuntimeError("admin.approvals: ERP adapter 未初始化")
    return _erp_adapter


# 主 router：include 两个子路由（共享同一个 prefix）
router = APIRouter(prefix="/hub/v1/admin/approvals", tags=["admin", "approvals"])

from hub.routers.admin.approvals import adjustments, voucher  # noqa: E402

router.include_router(voucher.router)
router.include_router(adjustments.router)

# re-export：main.py 和测试直接从包顶层导入
__all__ = [
    "router",
    "set_erp_adapter",
    "current_erp_adapter",
    "LEASE_TIMEOUT",
    "logger",
    "VoucherDraft",
    "PriceAdjustmentRequest",
    "StockAdjustmentRequest",
]
