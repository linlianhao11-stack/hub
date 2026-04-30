"""Plan 6 Task 8：写草稿 tool（凭证 / 调价 / 库存）。

三个写草稿 tool，均为 ToolType.WRITE_DRAFT，ToolRegistry register fail-fast 会
校验签名必须声明 confirmation_action_id。

幂等保护（每个 tool 均实现）：
  先查 (requester_hub_user_id, confirmation_action_id) 是否已存在记录
  → 若存在直接返回（idempotent_replay）
  → 若不存在 INSERT；IntegrityError catch + 回查
  → 回查到就返；回查不到则 reraise（极罕见 DB 故障）
"""
from __future__ import annotations

import logging
from tortoise.exceptions import IntegrityError

from hub.adapters.downstream.erp4 import (
    Erp4Adapter,
    ErpAdapterError,
    ErpNotFoundError,
    ErpSystemError,
)
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools.types import ToolArgsValidationError, ToolType
from hub.models.draft import (
    PriceAdjustmentRequest,
    StockAdjustmentRequest,
    VoucherDraft,
)

logger = logging.getLogger("hub.agent.tools.draft_tools")

# ============================================================
# 模块级 ERP adapter（同 erp_tools 模式）
# ============================================================

_erp_adapter: Erp4Adapter | None = None


def set_erp_adapter(adapter: Erp4Adapter | None) -> None:
    """app startup 挂；测试 fixture 注入 mock。"""
    global _erp_adapter
    _erp_adapter = adapter


def current_erp_adapter() -> Erp4Adapter:
    if _erp_adapter is None:
        raise RuntimeError("ERP adapter 未初始化（startup 必须先调 set_erp_adapter）")
    return _erp_adapter


# ============================================================
# 常量 & 辅助
# ============================================================

async def _get_max_voucher_amount() -> int:
    """从 system_config 读金额上限；默认 1_000_000。"""
    from hub.models.config import SystemConfig
    rec = await SystemConfig.filter(key="max_voucher_amount").first()
    if rec and rec.value is not None:
        try:
            return int(rec.value)
        except (ValueError, TypeError):
            pass
    return 1_000_000


async def _fetch_current_price(customer_id: int, product_id: int,
                                acting_as_user_id: int) -> float | None:
    """调 ERP get_product_customer_prices 取最近成交价（fail-soft）。

    I1: 只吞 ERP 网络/熔断/404 错误；RuntimeError（adapter 未初始化）让它上抛暴露 startup bug。
    """
    try:
        erp = current_erp_adapter()
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


def _validate_voucher_data(voucher_data: dict) -> None:
    """检查 voucher_data 含必填字段 (entries / total_amount / summary)。"""
    required = {"entries", "total_amount", "summary"}
    missing = required - set(voucher_data.keys())
    if missing:
        raise ToolArgsValidationError(
            f"voucher_data 缺少必填字段：{sorted(missing)}"
        )


def _validate_amount_within_limit(total_amount: float, max_amount: int) -> None:
    """total_amount 超限 → 抛 ToolArgsValidationError。"""
    if total_amount > max_amount:
        raise ToolArgsValidationError(
            f"凭证金额 {total_amount} 超过单笔上限 {max_amount}，"
            "请联系管理员或拆分处理。"
        )


def _approval_url(draft_id: int) -> str:
    return f"/admin/approvals/voucher#{draft_id}"


def _approval_url_price(request_id: int) -> str:
    return f"/admin/approvals/price#{request_id}"


def _approval_url_stock(request_id: int) -> str:
    return f"/admin/approvals/stock#{request_id}"


# ============================================================
# Tool 1：凭证草稿
# ============================================================

async def create_voucher_draft(
    voucher_data: dict,
    rule_matched: str | None = None,
    *,
    hub_user_id: int,
    conversation_id: str,
    acting_as_user_id: int,
    confirmation_action_id: str,
) -> dict:
    """创建凭证草稿，挂会计审批 inbox。

    Args:
        voucher_data: 凭证内容，必须含 entries / total_amount / summary
        rule_matched: 匹配到的凭证模板名（可选）

    M11 类型不变量：本 tool 仅写 HUB 草稿表（VoucherDraft），不调 ERP 写接口；
    ERP 落地由 admin 审批端点（admin/approvals/voucher/batch-approve）完成。
    未来重构不要把 ERP 写调用挪到此处。
    """
    # M3: 先幂等查，再校验（回放路径不重新 query system_config）
    # 1. 幂等先查
    existing = await VoucherDraft.filter(
        requester_hub_user_id=hub_user_id,
        confirmation_action_id=confirmation_action_id,
    ).first()
    if existing is not None:
        logger.info(
            "create_voucher_draft idempotent_replay: user=%s action_id=%s draft_id=%s",
            hub_user_id, confirmation_action_id, existing.id,
        )
        return {
            "draft_id": existing.id,
            "status": existing.status,
            "approval_url": _approval_url(existing.id),
            "idempotent_replay": True,
        }

    # 2. 入参校验（回放路径跳过，不重新 query system_config）
    _validate_voucher_data(voucher_data)
    total_amount = float(voucher_data.get("total_amount", 0))
    max_amount = await _get_max_voucher_amount()
    _validate_amount_within_limit(total_amount, max_amount)

    # 3. INSERT（可能 IntegrityError）
    try:
        draft = await VoucherDraft.create(
            requester_hub_user_id=hub_user_id,
            voucher_data=voucher_data,
            rule_matched=rule_matched,
            status="pending",
            conversation_id=conversation_id,
            confirmation_action_id=confirmation_action_id,
        )
    except IntegrityError:
        # 并发竞争：回查
        existing = await VoucherDraft.filter(
            requester_hub_user_id=hub_user_id,
            confirmation_action_id=confirmation_action_id,
        ).first()
        if existing is not None:
            logger.info(
                "create_voucher_draft concurrent IntegrityError → replay: "
                "user=%s action_id=%s draft_id=%s",
                hub_user_id, confirmation_action_id, existing.id,
            )
            return {
                "draft_id": existing.id,
                "status": existing.status,
                "approval_url": _approval_url(existing.id),
                "idempotent_replay": True,
            }
        # 回查不到：其他原因的 IntegrityError，reraise
        raise

    return {
        "draft_id": draft.id,
        "status": draft.status,
        "approval_url": _approval_url(draft.id),
        "idempotent_replay": False,
        "message": "凭证草稿已创建，等待会计审批。",
    }


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

    M11 类型不变量：本 tool 仅写 HUB 草稿表（PriceAdjustmentRequest），不调 ERP 写接口；
    ERP 落地由 admin 审批端点（admin/approvals/price/batch-approve）完成。
    未来重构不要把 ERP 写调用挪到此处。
    """
    if new_price <= 0:
        raise ToolArgsValidationError("调价价格必须大于 0")

    # 幂等先查
    existing = await PriceAdjustmentRequest.filter(
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
        req = await PriceAdjustmentRequest.create(
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
        existing = await PriceAdjustmentRequest.filter(
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

    M11 类型不变量：本 tool 仅写 HUB 草稿表（StockAdjustmentRequest），不调 ERP 写接口；
    ERP 落地由 admin 审批端点（admin/approvals/stock/batch-approve）完成。
    未来重构不要把 ERP 写调用挪到此处。

    M12 参数命名：plan §2522 字面写 delta_quantity: int，但实际模型字段
    （StockAdjustmentRequest.adjustment_qty）以及 ERP 接口都用 adjustment_qty: float。
    本 tool 与模型字段保持一致。
    """
    if adjustment_qty == 0:
        raise ToolArgsValidationError("调整数量不能为 0")

    # 幂等先查
    existing = await StockAdjustmentRequest.filter(
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
        req = await StockAdjustmentRequest.create(
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
        existing = await StockAdjustmentRequest.filter(
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


# ============================================================
# register_all
# ============================================================

def register_all(registry: ToolRegistry) -> None:
    """3 个 WRITE_DRAFT 类 tool 注册（必须声明 confirmation_action_id；register fail-fast 校验）。"""
    registry.register(
        "create_voucher_draft", create_voucher_draft,
        perm="usecase.create_voucher.use",
        tool_type=ToolType.WRITE_DRAFT,
        description="创建凭证草稿（挂会计审批 inbox）",
    )
    registry.register(
        "create_price_adjustment_request", create_price_adjustment_request,
        perm="usecase.adjust_price.use",
        tool_type=ToolType.WRITE_DRAFT,
        description="创建调价请求（挂销售主管审批 inbox）",
    )
    registry.register(
        "create_stock_adjustment_request", create_stock_adjustment_request,
        perm="usecase.adjust_stock.use",
        tool_type=ToolType.WRITE_DRAFT,
        description="创建库存调整请求（挂库管/财务审批 inbox）",
    )
