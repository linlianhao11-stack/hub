"""Plan 6 Task 8：写草稿 tool 测试（≥18 case）。

覆盖：
 1-3:   voucher / price / stock 基础成功
 4-6:   入参校验（voucher 缺字段 / price 价格=0 / stock qty=0）
 7-9:   voucher 超额上限 / price fetch 当前价 fail-soft / stock warehouse_id=None
10:     多 action_id 创 12 条 voucher
11-13:  同 (user, action_id) 第二次返 idempotent_replay（voucher / price / stock）
14-16:  并发同 (user, action_id) asyncio.gather（3 个 tool）
17:     IntegrityError 回查不到 reraise（voucher）
18:     register_all 不抛 ToolRegistrationError（3 tool 均声明 confirmation_action_id）
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from tortoise.exceptions import IntegrityError

from hub.agent.tools.draft_tools import (
    create_price_adjustment_request,
    create_stock_adjustment_request,
    create_voucher_draft,
    set_erp_adapter,
)
from hub.models.draft import (
    PriceAdjustmentRequest,
    StockAdjustmentRequest,
    VoucherDraft,
)


# ============================================================
# Fixtures
# ============================================================

VALID_VOUCHER = {
    "entries": [{"account": "应付账款", "debit": 1000, "credit": 0}],
    "total_amount": 1000.0,
    "summary": "测试凭证",
}

BASE_CTX = {
    "hub_user_id": 1,
    "conversation_id": "conv-test",
    "acting_as_user_id": 101,
}


@pytest.fixture
def mock_erp():
    """注入 mock ERP adapter，测试结束清除。"""
    m = AsyncMock()
    m.get_product_customer_prices = AsyncMock(return_value={"items": [{"price": 100.0}]})
    set_erp_adapter(m)
    yield m
    set_erp_adapter(None)


@pytest.fixture
def mock_erp_no_price():
    """ERP 返回空历史价（fail-soft 场景）。"""
    m = AsyncMock()
    m.get_product_customer_prices = AsyncMock(return_value={"items": []})
    set_erp_adapter(m)
    yield m
    set_erp_adapter(None)


# ============================================================
# Case 1-3: 基础成功
# ============================================================

@pytest.mark.asyncio
async def test_create_voucher_draft_success(mock_erp):
    """Case 1: voucher 基础成功创建。"""
    result = await create_voucher_draft(
        voucher_data=VALID_VOUCHER,
        **BASE_CTX,
        confirmation_action_id="action-v-1",
    )
    assert result["idempotent_replay"] is False
    assert result["status"] == "pending"
    assert "draft_id" in result
    assert "/admin/approvals/voucher#" in result["approval_url"]
    draft = await VoucherDraft.get(id=result["draft_id"])
    assert draft.voucher_data["total_amount"] == 1000.0


@pytest.mark.asyncio
async def test_create_price_adjustment_success(mock_erp):
    """Case 2: 调价请求基础成功创建。"""
    result = await create_price_adjustment_request(
        customer_id=10, product_id=20, new_price=88.0,
        reason="客户长期合作折扣",
        **BASE_CTX,
        confirmation_action_id="action-p-1",
    )
    assert result["idempotent_replay"] is False
    assert result["status"] == "pending"
    assert result["new_price"] == 88.0
    # current_price 来自 mock ERP 返回的 100.0
    assert result["current_price"] == 100.0
    assert "request_id" in result


@pytest.mark.asyncio
async def test_create_stock_adjustment_success(mock_erp):
    """Case 3: 库存调整请求基础成功创建。"""
    result = await create_stock_adjustment_request(
        product_id=30, adjustment_qty=-5.0,
        reason="盘点减少",
        warehouse_id=2,
        **BASE_CTX,
        confirmation_action_id="action-s-1",
    )
    assert result["idempotent_replay"] is False
    assert result["status"] == "pending"
    assert result["adjustment_qty"] == -5.0
    assert result["warehouse_id"] == 2


# ============================================================
# Case 4-6: 入参校验
# ============================================================

@pytest.mark.asyncio
async def test_voucher_missing_required_fields(mock_erp):
    """Case 4: voucher_data 缺字段抛 ToolArgsValidationError。"""
    from hub.agent.tools.types import ToolArgsValidationError
    with pytest.raises(ToolArgsValidationError, match="缺少必填字段"):
        await create_voucher_draft(
            voucher_data={"entries": [], "total_amount": 100},  # 缺 summary
            **BASE_CTX,
            confirmation_action_id="action-v-bad",
        )


@pytest.mark.asyncio
async def test_price_zero_raises(mock_erp):
    """Case 5: new_price=0 抛 ToolArgsValidationError。"""
    from hub.agent.tools.types import ToolArgsValidationError
    with pytest.raises(ToolArgsValidationError, match="大于 0"):
        await create_price_adjustment_request(
            customer_id=10, product_id=20, new_price=0.0,
            **BASE_CTX,
            confirmation_action_id="action-p-bad",
        )


@pytest.mark.asyncio
async def test_stock_zero_qty_raises(mock_erp):
    """Case 6: adjustment_qty=0 抛 ToolArgsValidationError。"""
    from hub.agent.tools.types import ToolArgsValidationError
    with pytest.raises(ToolArgsValidationError, match="不能为 0"):
        await create_stock_adjustment_request(
            product_id=30, adjustment_qty=0,
            **BASE_CTX,
            confirmation_action_id="action-s-bad",
        )


# ============================================================
# Case 7-9: 边界场景
# ============================================================

@pytest.mark.asyncio
async def test_voucher_exceeds_max_amount():
    """Case 7: voucher total_amount 超限抛 ToolArgsValidationError。"""
    from hub.agent.tools.types import ToolArgsValidationError
    m = AsyncMock()
    set_erp_adapter(m)
    try:
        with pytest.raises(ToolArgsValidationError, match="超过单笔上限"):
            await create_voucher_draft(
                voucher_data={
                    "entries": [],
                    "total_amount": 2_000_000.0,
                    "summary": "超额",
                },
                **BASE_CTX,
                confirmation_action_id="action-over",
            )
    finally:
        set_erp_adapter(None)


@pytest.mark.asyncio
async def test_price_fetch_fail_soft(mock_erp_no_price):
    """Case 8: ERP 历史价为空 → current_price=None（fail-soft）。"""
    result = await create_price_adjustment_request(
        customer_id=10, product_id=20, new_price=99.0,
        **BASE_CTX,
        confirmation_action_id="action-p-nohistory",
    )
    assert result["current_price"] is None
    assert result["discount_pct"] is None
    assert result["idempotent_replay"] is False


@pytest.mark.asyncio
async def test_stock_no_warehouse(mock_erp):
    """Case 9: warehouse_id=None 正常创建。"""
    result = await create_stock_adjustment_request(
        product_id=31, adjustment_qty=10.0,
        **BASE_CTX,
        confirmation_action_id="action-s-nw",
    )
    assert result["warehouse_id"] is None
    assert result["idempotent_replay"] is False


# ============================================================
# Case 10: 多 action_id 创 12 条 voucher
# ============================================================

@pytest.mark.asyncio
async def test_create_12_vouchers_different_action_ids(mock_erp):
    """Case 10: 12 个不同 action_id → 12 条独立 draft。"""
    results = []
    for i in range(12):
        r = await create_voucher_draft(
            voucher_data={**VALID_VOUCHER, "summary": f"凭证{i}"},
            **BASE_CTX,
            confirmation_action_id=f"action-v-batch-{i}",
        )
        results.append(r)
    assert all(r["idempotent_replay"] is False for r in results)
    ids = {r["draft_id"] for r in results}
    assert len(ids) == 12
    total = await VoucherDraft.filter(requester_hub_user_id=1).count()
    assert total == 12


# ============================================================
# Case 11-13: 幂等回放
# ============================================================

@pytest.mark.asyncio
async def test_voucher_idempotent_replay(mock_erp):
    """Case 11: 同 (user, action_id) 第二次调 voucher → idempotent_replay=True。"""
    r1 = await create_voucher_draft(
        voucher_data=VALID_VOUCHER,
        **BASE_CTX,
        confirmation_action_id="action-v-dup",
    )
    r2 = await create_voucher_draft(
        voucher_data=VALID_VOUCHER,
        **BASE_CTX,
        confirmation_action_id="action-v-dup",
    )
    assert r1["draft_id"] == r2["draft_id"]
    assert r2["idempotent_replay"] is True
    count = await VoucherDraft.filter(confirmation_action_id="action-v-dup").count()
    assert count == 1


@pytest.mark.asyncio
async def test_price_idempotent_replay(mock_erp):
    """Case 12: 同 (user, action_id) 第二次调 price → idempotent_replay=True。"""
    r1 = await create_price_adjustment_request(
        customer_id=10, product_id=20, new_price=75.0,
        **BASE_CTX,
        confirmation_action_id="action-p-dup",
    )
    r2 = await create_price_adjustment_request(
        customer_id=10, product_id=20, new_price=75.0,
        **BASE_CTX,
        confirmation_action_id="action-p-dup",
    )
    assert r1["request_id"] == r2["request_id"]
    assert r2["idempotent_replay"] is True


@pytest.mark.asyncio
async def test_stock_idempotent_replay(mock_erp):
    """Case 13: 同 (user, action_id) 第二次调 stock → idempotent_replay=True。"""
    r1 = await create_stock_adjustment_request(
        product_id=30, adjustment_qty=3.0,
        **BASE_CTX,
        confirmation_action_id="action-s-dup",
    )
    r2 = await create_stock_adjustment_request(
        product_id=30, adjustment_qty=3.0,
        **BASE_CTX,
        confirmation_action_id="action-s-dup",
    )
    assert r1["request_id"] == r2["request_id"]
    assert r2["idempotent_replay"] is True


# ============================================================
# Case 14-16: 并发同 (user, action_id) asyncio.gather
# ============================================================

@pytest.mark.asyncio
async def test_voucher_concurrent_same_action_id(mock_erp):
    """Case 14: 并发 voucher 同 action_id → 只创建 1 条，均返回。"""
    tasks = [
        create_voucher_draft(
            voucher_data=VALID_VOUCHER,
            **BASE_CTX,
            confirmation_action_id="action-v-concurrent",
        )
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    ids = {r["draft_id"] for r in results}
    assert len(ids) == 1
    count = await VoucherDraft.filter(confirmation_action_id="action-v-concurrent").count()
    assert count == 1


@pytest.mark.asyncio
async def test_price_concurrent_same_action_id(mock_erp):
    """Case 15: 并发 price 同 action_id → 只创建 1 条，均返回。"""
    tasks = [
        create_price_adjustment_request(
            customer_id=10, product_id=20, new_price=60.0,
            **BASE_CTX,
            confirmation_action_id="action-p-concurrent",
        )
        for _ in range(4)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    ids = {r["request_id"] for r in results}
    assert len(ids) == 1


@pytest.mark.asyncio
async def test_stock_concurrent_same_action_id(mock_erp):
    """Case 16: 并发 stock 同 action_id → 只创建 1 条，均返回。"""
    tasks = [
        create_stock_adjustment_request(
            product_id=30, adjustment_qty=2.0,
            **BASE_CTX,
            confirmation_action_id="action-s-concurrent",
        )
        for _ in range(4)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    ids = {r["request_id"] for r in results}
    assert len(ids) == 1


# ============================================================
# Case 17: IntegrityError 回查不到 → reraise
# ============================================================

@pytest.mark.asyncio
async def test_voucher_integrity_error_reraise(mock_erp):
    """Case 17: IntegrityError 发生但回查不到记录 → 重新抛出。"""
    # patch VoucherDraft.create 抛 IntegrityError；patch filter 返回 None
    with patch("hub.agent.tools.draft_tools.VoucherDraft.create",
               side_effect=IntegrityError("unique constraint")):
        with patch("hub.agent.tools.draft_tools.VoucherDraft.filter") as mock_filter:
            mock_qs = AsyncMock()
            mock_qs.first = AsyncMock(return_value=None)
            mock_filter.return_value = mock_qs
            with pytest.raises(IntegrityError):
                await create_voucher_draft(
                    voucher_data=VALID_VOUCHER,
                    **BASE_CTX,
                    confirmation_action_id="action-v-reraise",
                )


# ============================================================
# Case 18: register_all 不抛 ToolRegistrationError
# ============================================================

@pytest.mark.asyncio
async def test_register_all_succeeds():
    """Case 18: register_all 注册 3 个 WRITE_DRAFT tool 不抛异常（签名均声明 confirmation_action_id）。"""
    from unittest.mock import MagicMock
    from hub.agent.tools.draft_tools import register_all
    from hub.agent.tools.registry import ToolRegistry

    mock_registry = MagicMock(spec=ToolRegistry)
    # 验证 register 被调用了 3 次（不抛 ToolRegistrationError）
    register_all(mock_registry)
    assert mock_registry.register.call_count == 3
    call_names = [call.args[0] for call in mock_registry.register.call_args_list]
    assert "create_voucher_draft" in call_names
    assert "create_price_adjustment_request" in call_names
    assert "create_stock_adjustment_request" in call_names
