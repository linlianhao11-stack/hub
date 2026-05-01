"""Plan 6 Task 17：seed.py 升级测试（13 新权限 + 2 新角色 + 业务词典）。"""
from __future__ import annotations
import pytest

from hub.seed import (
    PERMISSIONS, ROLES, run_seed,
    DEFAULT_BUSINESS_DICT_SEED, _seed_business_dict,
)
from hub.models import HubPermission, HubRole
from hub.models.config import SystemConfig


# ===== 静态 schema 检查（不需 DB） =====

def test_plan6_new_permissions_present():
    """Plan §3255-3284 列出的 13 个权限码都在 PERMISSIONS 列表（去掉重复的 contract_templates.write）。"""
    codes = {p[0] for p in PERMISSIONS}
    expected = {
        "usecase.query_customer.use",
        "usecase.query_inventory.use",
        "usecase.query_orders.use",
        "usecase.query_customer_balance.use",
        "usecase.query_inventory_aging.use",
        "usecase.analyze.use",
        "usecase.generate_quote.use",
        "usecase.export.use",
        "usecase.adjust_price.use",
        "usecase.adjust_price.approve",
        "usecase.adjust_stock.use",
        "usecase.adjust_stock.approve",
        "usecase.create_voucher.approve",
    }
    missing = expected - codes
    assert not missing, f"缺权限码: {missing}"


def test_lead_roles_added():
    """两个 lead 角色都加了。"""
    assert "bot_user_sales_lead" in ROLES
    assert "bot_user_finance_lead" in ROLES


def test_sales_lead_has_approve_perm():
    """销售主管比销售员多 adjust_price.approve。"""
    sales_perms = set(ROLES["bot_user_sales"]["permissions"])
    lead_perms = set(ROLES["bot_user_sales_lead"]["permissions"])
    assert "usecase.adjust_price.approve" in lead_perms
    assert "usecase.adjust_price.approve" not in sales_perms
    # 主管包含销售员所有权限
    assert sales_perms.issubset(lead_perms)


def test_finance_lead_has_approve_perms():
    """会计主管比会计员多 create_voucher.approve + adjust_stock.approve。"""
    finance_perms = set(ROLES["bot_user_finance"]["permissions"])
    lead_perms = set(ROLES["bot_user_finance_lead"]["permissions"])
    assert "usecase.create_voucher.approve" in lead_perms
    assert "usecase.adjust_stock.approve" in lead_perms
    assert "usecase.create_voucher.approve" not in finance_perms
    assert "usecase.adjust_stock.approve" not in finance_perms


def test_existing_roles_upgraded_with_plan6_perms():
    """既有角色（basic / sales / finance）加了 Plan 6 新权限。"""
    basic = set(ROLES["bot_user_basic"]["permissions"])
    assert "usecase.query_customer.use" in basic
    assert "usecase.query_inventory.use" in basic
    assert "usecase.query_orders.use" in basic

    sales = set(ROLES["bot_user_sales"]["permissions"])
    assert "usecase.generate_quote.use" in sales
    assert "usecase.export.use" in sales
    assert "usecase.adjust_price.use" in sales

    finance = set(ROLES["bot_user_finance"]["permissions"])
    assert "usecase.adjust_stock.use" in finance
    assert "usecase.export.use" in finance


def test_no_role_references_undefined_perm():
    """所有角色引用的权限码都在 PERMISSIONS 列表（防数据一致性 bug）。"""
    perm_codes = {p[0] for p in PERMISSIONS}
    for role_code, info in ROLES.items():
        for pcode in info["permissions"]:
            assert pcode in perm_codes, (
                f"角色 {role_code} 引用未定义权限 {pcode}"
            )


def test_business_dict_seed_count():
    """业务词典 seed ≥30 条。"""
    assert len(DEFAULT_BUSINESS_DICT_SEED) >= 30


def test_business_dict_required_terms():
    """plan 列出的关键术语都在 seed。"""
    required = ["压货", "周转", "回款", "上次价格", "差旅", "套餐"]
    for term in required:
        assert term in DEFAULT_BUSINESS_DICT_SEED, f"缺业务术语 {term}"


# ===== 真 DB 集成测试 =====

@pytest.mark.asyncio
async def test_run_seed_creates_all_permissions():
    """run_seed 后 DB 中所有 PERMISSIONS 都存在。"""
    await run_seed()
    for code, *_ in PERMISSIONS:
        rec = await HubPermission.filter(code=code).first()
        assert rec is not None, f"权限码 {code} seed 后仍不存在"


@pytest.mark.asyncio
async def test_run_seed_creates_lead_roles_with_correct_perms():
    """run_seed 后 lead 角色含正确权限。"""
    await run_seed()

    sales_lead = await HubRole.filter(code="bot_user_sales_lead").first()
    assert sales_lead is not None
    sales_lead_perms = {p.code async for p in sales_lead.permissions}
    assert "usecase.adjust_price.approve" in sales_lead_perms

    finance_lead = await HubRole.filter(code="bot_user_finance_lead").first()
    assert finance_lead is not None
    finance_lead_perms = {p.code async for p in finance_lead.permissions}
    assert "usecase.create_voucher.approve" in finance_lead_perms
    assert "usecase.adjust_stock.approve" in finance_lead_perms


@pytest.mark.asyncio
async def test_run_seed_idempotent():
    """run_seed 多次跑不重复 / 不报错。"""
    await run_seed()
    count_first = await HubPermission.all().count()
    await run_seed()
    count_second = await HubPermission.all().count()
    assert count_first == count_second


@pytest.mark.asyncio
async def test_run_seed_writes_business_dict():
    """run_seed 后 SystemConfig.business_dict 存在。"""
    await run_seed()
    rec = await SystemConfig.filter(key="business_dict").first()
    assert rec is not None
    assert isinstance(rec.value, dict)
    assert "压货" in rec.value


@pytest.mark.asyncio
async def test_seed_business_dict_does_not_override_manual_edit():
    """如果 admin 手动改过 business_dict，再跑 seed 不应覆盖。"""
    custom = {"自定义术语": "管理员加的"}
    await SystemConfig.create(key="business_dict", value=custom)

    await _seed_business_dict()

    rec = await SystemConfig.filter(key="business_dict").first()
    assert rec.value == custom  # 没覆盖
