"""Plan 6 Task 8：审批 inbox 路由测试（≥12 case）。

覆盖：
 1.  列 voucher pending（默认）
 2.  列 voucher created（filter）
 3.  batch-approve voucher 全成功 phase1+phase2
 4.  batch-approve voucher phase1 部分失败（ERP create 5xx）
 5.  batch-approve voucher phase2 部分失败（batch_approve 部分 fail）
 6.  batch-approve voucher 重试（同 draft_id 二次 batch-approve）— created 跳过 phase1
 7.  batch-approve voucher 含 approved/rejected → 400
 8.  batch-reject voucher pending 全成功
 9.  batch-reject voucher 含 created → 400
10.  崩溃恢复（租约过期）：creating + 10min 前 → batch-approve 重新走 phase1
11.  租约未过期跳过：creating + 2min 前 → in_progress 含此条
12.  全 in_progress 早返回 audit log：3 张全 creating + lease 未过 → audit log 写入 + early_return_reason
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from hub.adapters.downstream.erp4 import ErpSystemError
from hub.models.audit import AuditLog
from hub.models.draft import VoucherDraft

# ============================================================
# 辅助：创建有指定权限的 admin 用户
# ============================================================

ERP_USER_ID = 10


async def _setup_approvals_admin(perm_codes: list[str], erp_user_id: int = ERP_USER_ID):
    """创建具有指定权限的 admin 用户，返回 (transport, cookie, hub_user)。"""
    from hub.auth.erp_session import ErpSessionAuth
    from hub.models import (
        DownstreamIdentity,
        HubPermission,
        HubRole,
        HubUser,
        HubUserRole,
    )
    from hub.seed import run_seed
    from main import app

    await run_seed()

    # 创建 HUB user + ERP 绑定
    user = await HubUser.create(display_name=f"approvals-admin-{erp_user_id}")
    await DownstreamIdentity.create(
        hub_user=user, downstream_type="erp", downstream_user_id=erp_user_id,
    )

    # 创建临时 role 含指定权限
    role_code = f"test_approvals_{erp_user_id}"
    role, _ = await HubRole.get_or_create(
        code=role_code,
        defaults={"name": "Test Approvals", "description": "Test", "is_builtin": False},
    )
    for code in perm_codes:
        perm, _ = await HubPermission.get_or_create(
            code=code,
            defaults={
                "resource": "usecase", "sub_resource": code.split(".")[1],
                "action": "approve", "name": code, "description": "",
            },
        )
        await role.permissions.add(perm)
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    # Mock ERP session auth
    erp = AsyncMock()
    erp.get_me = AsyncMock(return_value={
        "id": erp_user_id, "username": f"admin-{erp_user_id}", "permissions": [],
    })
    auth = ErpSessionAuth(erp_adapter=erp)
    app.state.session_auth = auth
    cookie = auth._encode_cookie({
        "jwt": "tok", "user": {"id": erp_user_id, "username": f"admin-{erp_user_id}"},
    })
    transport = ASGITransport(app=app)
    return transport, cookie, user


VOUCHER_APPROVE_PERMS = ["usecase.create_voucher.approve"]
PRICE_APPROVE_PERMS = ["usecase.adjust_price.approve"]
STOCK_APPROVE_PERMS = ["usecase.adjust_stock.approve"]

SAMPLE_VOUCHER_DATA = {
    "entries": [{"account": "应付账款", "debit": 500, "credit": 0}],
    "total_amount": 500.0,
    "summary": "测试凭证审批",
}


@pytest_asyncio.fixture
async def voucher_admin_client():
    """有 create_voucher.approve 权限的 admin client。"""
    from hub.routers.admin.approvals import set_erp_adapter
    transport, cookie, user = await _setup_approvals_admin(VOUCHER_APPROVE_PERMS)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        yield ac, user
    set_erp_adapter(None)


@pytest_asyncio.fixture
async def price_admin_client():
    """有 adjust_price.approve 权限的 admin client。"""
    from hub.routers.admin.approvals import set_erp_adapter
    transport, cookie, user = await _setup_approvals_admin(
        PRICE_APPROVE_PERMS, erp_user_id=11
    )
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        yield ac, user
    set_erp_adapter(None)


@pytest_asyncio.fixture
async def stock_admin_client():
    """有 adjust_stock.approve 权限的 admin client。"""
    from hub.routers.admin.approvals import set_erp_adapter
    transport, cookie, user = await _setup_approvals_admin(
        STOCK_APPROVE_PERMS, erp_user_id=12
    )
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        yield ac, user
    set_erp_adapter(None)


@pytest.fixture
def mock_erp_for_approvals():
    """注入 mock ERP adapter 给 approvals router。"""
    from hub.routers.admin.approvals import set_erp_adapter
    m = AsyncMock()
    m.create_voucher = AsyncMock(return_value={"id": 999})
    m.batch_approve_vouchers = AsyncMock(return_value={"success": [999], "failed": []})
    m.upsert_customer_price_rule = AsyncMock(return_value={"ok": True})
    set_erp_adapter(m)
    yield m
    set_erp_adapter(None)


# ============================================================
# Case 1: 列 voucher pending（默认）
# ============================================================

@pytest.mark.asyncio
async def test_list_voucher_pending(voucher_admin_client, mock_erp_for_approvals):
    """Case 1: GET /voucher 默认返 pending 列表。"""
    ac, user = voucher_admin_client
    await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="pending", confirmation_action_id="a1",
    )
    await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="approved", confirmation_action_id="a2",
    )
    resp = await ac.get("/hub/v1/admin/approvals/voucher")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "pending"


# ============================================================
# Case 2: 列 voucher created（filter）
# ============================================================

@pytest.mark.asyncio
async def test_list_voucher_created_filter(voucher_admin_client, mock_erp_for_approvals):
    """Case 2: GET /voucher?status=created 按状态过滤。"""
    ac, _ = voucher_admin_client
    await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="pending", confirmation_action_id="b1",
    )
    await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="created", erp_voucher_id=888, confirmation_action_id="b2",
    )
    resp = await ac.get("/hub/v1/admin/approvals/voucher", params={"status": "created"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["erp_voucher_id"] == 888


# ============================================================
# Case 3: batch-approve voucher 全成功 phase1+phase2
# ============================================================

@pytest.mark.asyncio
async def test_batch_approve_voucher_full_success(voucher_admin_client, mock_erp_for_approvals):
    """Case 3: batch-approve 全成功——phase1 ERP create + phase2 batch_approve。"""
    ac, _ = voucher_admin_client
    mock_erp_for_approvals.create_voucher = AsyncMock(return_value={"id": 999})
    mock_erp_for_approvals.batch_approve_vouchers = AsyncMock(
        return_value={"success": [999], "failed": []}
    )

    draft = await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="pending", confirmation_action_id="c1",
    )
    resp = await ac.post(
        "/hub/v1/admin/approvals/voucher/batch-approve",
        json={"draft_ids": [draft.id]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved_count"] == 1
    assert draft.id in body["approved_draft_ids"]
    assert body["creation_failed"] == []   # M2: 改用 creation_failed
    assert body["approve_failed"] == []    # M2: 改用 approve_failed

    # 验证状态
    await draft.refresh_from_db()
    assert draft.status == "approved"
    assert draft.erp_voucher_id == 999


# ============================================================
# Case 4: batch-approve voucher phase1 部分失败（ERP create 5xx）
# ============================================================

@pytest.mark.asyncio
async def test_batch_approve_phase1_partial_failure(voucher_admin_client, mock_erp_for_approvals):
    """Case 4: ERP create_voucher 抛 ErpSystemError → 回滚到 pending + 在 failed 中。"""
    ac, _ = voucher_admin_client
    mock_erp_for_approvals.create_voucher = AsyncMock(
        side_effect=ErpSystemError("ERP 5xx")
    )

    draft = await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="pending", confirmation_action_id="d1",
    )
    resp = await ac.post(
        "/hub/v1/admin/approvals/voucher/batch-approve",
        json={"draft_ids": [draft.id]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved_count"] == 0
    assert len(body["creation_failed"]) == 1   # M2: 改用 creation_failed
    assert body["creation_failed"][0]["draft_id"] == draft.id

    await draft.refresh_from_db()
    # 回滚到 pending
    assert draft.status == "pending"


# ============================================================
# Case 5: batch-approve voucher phase2 部分失败（batch_approve 部分 fail）
# ============================================================

@pytest.mark.asyncio
async def test_batch_approve_phase2_partial_failure(voucher_admin_client, mock_erp_for_approvals):
    """Case 5: ERP batch_approve_vouchers 返 failed 列表 → approve_failures。"""
    ac, _ = voucher_admin_client
    mock_erp_for_approvals.create_voucher = AsyncMock(return_value={"id": 777})
    mock_erp_for_approvals.batch_approve_vouchers = AsyncMock(
        return_value={"success": [], "failed": [{"id": 777, "reason": "会计拒绝"}]}
    )

    draft = await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="pending", confirmation_action_id="e1",
    )
    resp = await ac.post(
        "/hub/v1/admin/approvals/voucher/batch-approve",
        json={"draft_ids": [draft.id]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved_count"] == 0
    assert len(body["approve_failed"]) == 1    # M2: 改用 approve_failed
    assert "会计拒绝" in body["approve_failed"][0]["reason"]

    await draft.refresh_from_db()
    # phase2 失败 → 保持 created（可重试）
    assert draft.status == "created"


# ============================================================
# Case 6: batch-approve voucher 重试（二次 batch-approve，created 跳过 phase1）
# ============================================================

@pytest.mark.asyncio
async def test_batch_approve_retry_created_skips_phase1(voucher_admin_client, mock_erp_for_approvals):
    """Case 6: 已 created 的 draft 二次 batch-approve → 直接走 phase2，不重复调 ERP create。"""
    ac, _ = voucher_admin_client
    mock_erp_for_approvals.batch_approve_vouchers = AsyncMock(
        return_value={"success": [555], "failed": []}
    )

    draft = await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="created", erp_voucher_id=555, confirmation_action_id="f1",
    )
    resp = await ac.post(
        "/hub/v1/admin/approvals/voucher/batch-approve",
        json={"draft_ids": [draft.id]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved_count"] == 1

    # create_voucher 不应被调用（draft 已是 created）
    mock_erp_for_approvals.create_voucher.assert_not_called()

    await draft.refresh_from_db()
    assert draft.status == "approved"


# ============================================================
# Case 7: batch-approve voucher 含 approved/rejected → 400
# ============================================================

@pytest.mark.asyncio
async def test_batch_approve_includes_approved_returns_400(voucher_admin_client, mock_erp_for_approvals):
    """Case 7: draft_ids 含 approved 状态的 draft → 400。"""
    ac, _ = voucher_admin_client
    draft = await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="approved", erp_voucher_id=100, confirmation_action_id="g1",
    )
    resp = await ac.post(
        "/hub/v1/admin/approvals/voucher/batch-approve",
        json={"draft_ids": [draft.id]},
    )
    assert resp.status_code == 400


# ============================================================
# Case 8: batch-reject voucher pending 全成功
# ============================================================

@pytest.mark.asyncio
async def test_batch_reject_voucher_pending(voucher_admin_client, mock_erp_for_approvals):
    """Case 8: batch-reject pending draft → status=rejected。"""
    ac, _ = voucher_admin_client
    d1 = await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="pending", confirmation_action_id="h1",
    )
    d2 = await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="pending", confirmation_action_id="h2",
    )
    resp = await ac.post(
        "/hub/v1/admin/approvals/voucher/batch-reject",
        json={"draft_ids": [d1.id, d2.id], "reason": "金额有误"},
    )
    assert resp.status_code == 200
    assert resp.json()["rejected_count"] == 2

    await d1.refresh_from_db()
    await d2.refresh_from_db()
    assert d1.status == "rejected"
    assert d1.rejection_reason == "金额有误"
    assert d2.status == "rejected"


# ============================================================
# Case 9: batch-reject voucher 含 created → 400
# ============================================================

@pytest.mark.asyncio
async def test_batch_reject_created_returns_400(voucher_admin_client, mock_erp_for_approvals):
    """Case 9: 含 created 状态 draft → 400（需去 ERP 反审）。"""
    ac, _ = voucher_admin_client
    created_draft = await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="created", erp_voucher_id=200, confirmation_action_id="i1",
    )
    resp = await ac.post(
        "/hub/v1/admin/approvals/voucher/batch-reject",
        json={"draft_ids": [created_draft.id], "reason": "要反审"},
    )
    assert resp.status_code == 400
    assert "ERP 反审" in resp.json()["detail"]


# ============================================================
# Case 10: 崩溃恢复（租约过期）
# ============================================================

@pytest.mark.asyncio
async def test_batch_approve_crash_recovery(voucher_admin_client, mock_erp_for_approvals):
    """Case 10: creating 状态 + 10分钟前 → 租约过期，重新走 phase1。"""
    ac, _ = voucher_admin_client
    expired_time = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10)
    mock_erp_for_approvals.create_voucher = AsyncMock(return_value={"id": 300})
    mock_erp_for_approvals.batch_approve_vouchers = AsyncMock(
        return_value={"success": [300], "failed": []}
    )

    # 人为造 creating + 10分钟前
    draft = await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="creating", creating_started_at=expired_time,
        confirmation_action_id="j1",
    )
    resp = await ac.post(
        "/hub/v1/admin/approvals/voucher/batch-approve",
        json={"draft_ids": [draft.id]},
    )
    assert resp.status_code == 200
    body = resp.json()
    # 租约过期后应该重新走 phase1 → 成功
    assert body["approved_count"] == 1
    assert body["in_progress"] == []

    await draft.refresh_from_db()
    assert draft.status == "approved"


# ============================================================
# Case 11: 租约未过期跳过（in_progress）
# ============================================================

@pytest.mark.asyncio
async def test_batch_approve_lease_active_returns_in_progress(voucher_admin_client, mock_erp_for_approvals):
    """Case 11: creating + 2分钟前 → 租约未过期，in_progress 含此条。"""
    ac, _ = voucher_admin_client
    recent_time = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=2)

    draft = await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="creating", creating_started_at=recent_time,
        confirmation_action_id="k1",
    )
    resp = await ac.post(
        "/hub/v1/admin/approvals/voucher/batch-approve",
        json={"draft_ids": [draft.id]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["in_progress"]) == 1
    assert body["in_progress"][0]["draft_id"] == draft.id
    assert "另一位审批员" in body["in_progress"][0]["reason"]  # M7: 友好中文文案
    assert body["approved_count"] == 0


# ============================================================
# Case 12: 全 in_progress 早返回 audit log
# ============================================================

@pytest.mark.asyncio
async def test_all_in_progress_early_return_writes_audit_log(voucher_admin_client, mock_erp_for_approvals):
    """Case 12: 所有 draft 均 creating + lease 未过 → audit log 写入 + early_return_reason。"""
    ac, user = voucher_admin_client
    recent_time = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)

    draft_ids = []
    for i in range(3):
        d = await VoucherDraft.create(
            requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
            status="creating", creating_started_at=recent_time,
            confirmation_action_id=f"l{i}",
        )
        draft_ids.append(d.id)

    resp = await ac.post(
        "/hub/v1/admin/approvals/voucher/batch-approve",
        json={"draft_ids": draft_ids},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved_count"] == 0
    assert len(body["in_progress"]) == 3

    # 验证 audit log 写入
    log = await AuditLog.filter(action="batch_approve_vouchers").first()
    assert log is not None
    assert log.who_hub_user_id == user.id
    assert log.detail.get("early_return_reason") == "all_drafts_in_progress"  # M1: 精确 reason
    # C1: target_id 是短摘要格式
    assert log.target_id == "batch-3"


# ============================================================
# Case 13 (新增): C1 - 20 个 draft 批量审批 audit 不超长
# ============================================================

@pytest.mark.asyncio
async def test_voucher_batch_approve_with_20_drafts_audit_succeeds(
    voucher_admin_client, mock_erp_for_approvals
):
    """Case 13 (C1): 20 个 draft 批量审批，audit target_id 不超 64 字符不挂。"""
    ac, user = voucher_admin_client
    # 设置 mock：20 个不同 erp_voucher_id
    erp_ids = list(range(100, 120))
    mock_erp_for_approvals.create_voucher = AsyncMock(
        side_effect=[{"id": eid} for eid in erp_ids]
    )
    mock_erp_for_approvals.batch_approve_vouchers = AsyncMock(
        return_value={"success": erp_ids, "failed": []}
    )

    draft_ids = []
    for i in range(20):
        d = await VoucherDraft.create(
            requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
            status="pending", confirmation_action_id=f"m-{i}",
        )
        draft_ids.append(d.id)

    resp = await ac.post(
        "/hub/v1/admin/approvals/voucher/batch-approve",
        json={"draft_ids": draft_ids},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved_count"] == 20

    # C1: audit target_id 不超 64 字符，不会抛 DataError
    log = await AuditLog.filter(action="batch_approve_vouchers").first()
    assert log is not None
    assert log.target_id == "batch-20"
    assert len(log.target_id) <= 64


# ============================================================
# Case 14 (新增): I5 - rows_updated==0 (并发抢占) 加入 in_progress
# ============================================================

@pytest.mark.asyncio
async def test_voucher_batch_approve_concurrent_locks_in_progress(
    voucher_admin_client, mock_erp_for_approvals
):
    """Case 14 (I5): phase1 update 返 0（并发抢占）→ in_progress 含此 draft + reason 含并发抢占。

    用 patch "hub.routers.admin.approvals.VoucherDraft" 的 filter 链 → update 返 0
    模拟另一进程已先抢占该 draft。
    """
    from unittest.mock import patch
    ac, _ = voucher_admin_client

    draft = await VoucherDraft.create(
        requester_hub_user_id=1, voucher_data=SAMPLE_VOUCHER_DATA,
        status="pending", confirmation_action_id="n1",
    )

    # 构建一个 fake filter chain，让 phase1 的 filter(id=...).filter(...).update 返 0
    # 其他 filter 调用（初始查询、重新拉 created_drafts、AuditLog.create）走真实路径
    real_filter = VoucherDraft.filter
    phase1_update_call_count = 0

    async def fake_update(**kwargs):
        nonlocal phase1_update_call_count
        if kwargs.get("status") == "creating":
            phase1_update_call_count += 1
            return 0  # 模拟并发抢占
        return 0

    class FakeChainQS:
        """支持 .filter(...).update(...) 链式调用，返回 0。"""
        def filter(self, *args, **kwargs):
            return self

        async def update(self, **kwargs):
            return await fake_update(**kwargs)

    # 只 patch 带 id__in 的首次全量查询用真实 filter；
    # 带单 id= 的 phase1 update 链用 FakeChainQS
    _call_seq = []

    def selective_filter(*args, **kwargs):
        # phase1 update 链：filter(id=d.id)
        if set(kwargs.keys()) == {"id"} and not args:
            return FakeChainQS()
        return real_filter(*args, **kwargs)

    with patch("hub.routers.admin.approvals.VoucherDraft.filter", side_effect=selective_filter):
        resp = await ac.post(
            "/hub/v1/admin/approvals/voucher/batch-approve",
            json={"draft_ids": [draft.id]},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["approved_count"] == 0
    assert len(body["in_progress"]) == 1
    assert body["in_progress"][0]["draft_id"] == draft.id
    assert "并发抢占" in body["in_progress"][0]["reason"]


# ============================================================
# Case 15 (新增): I2 - new_price=None 跳过审批记入 failed
# ============================================================

@pytest.mark.asyncio
async def test_batch_approve_price_none_price_skipped(price_admin_client, mock_erp_for_approvals):
    """Case 15 (I2): new_price=None → failed 含 reason，不 fallback 0.0 写 ERP。"""
    from hub.models.draft import PriceAdjustmentRequest

    ac, _ = price_admin_client

    # 直接插入 new_price=None 的记录
    req = await PriceAdjustmentRequest.create(
        requester_hub_user_id=1, customer_id=10, product_id=20,
        new_price=None, reason="测试", status="pending",
        confirmation_action_id="o1",
    )

    resp = await ac.post(
        "/hub/v1/admin/approvals/price/batch-approve",
        json={"request_ids": [req.id]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved_count"] == 0
    assert len(body["failed"]) == 1
    assert "缺少新价格" in body["failed"][0]["reason"]

    # ERP 没有被调用（不发破坏性写入）
    mock_erp_for_approvals.upsert_customer_price_rule.assert_not_called()
