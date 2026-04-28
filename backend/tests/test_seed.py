import pytest


@pytest.mark.asyncio
async def test_seed_creates_6_roles():
    from hub.seed import run_seed
    from hub.models import HubRole
    await run_seed()
    roles = await HubRole.all()
    codes = {r.code for r in roles}
    expected = {
        "platform_admin", "platform_ops", "platform_viewer",
        "bot_user_basic", "bot_user_sales", "bot_user_finance",
    }
    assert expected.issubset(codes)


@pytest.mark.asyncio
async def test_seed_creates_all_permissions():
    from hub.seed import run_seed
    from hub.models import HubPermission
    await run_seed()
    perms = await HubPermission.all()
    codes = {p.code for p in perms}
    # 至少包含 spec §7.4 列出的核心权限码
    must_have = {
        "platform.tasks.read", "platform.flags.write", "platform.users.write",
        "platform.alerts.write", "platform.audit.read", "platform.audit.system_read",
        "platform.conversation.monitor", "platform.apikeys.write",
        "downstream.erp.use",
        "usecase.query_product.use", "usecase.query_customer_history.use",
        "channel.dingtalk.use",
    }
    assert must_have.issubset(codes)


@pytest.mark.asyncio
async def test_seed_idempotent():
    """跑两次种子结果不变（不重复创建）。"""
    from hub.seed import run_seed
    from hub.models import HubRole, HubPermission
    await run_seed()
    n_roles_1 = await HubRole.all().count()
    n_perms_1 = await HubPermission.all().count()
    await run_seed()  # 再跑一次
    n_roles_2 = await HubRole.all().count()
    n_perms_2 = await HubPermission.all().count()
    assert n_roles_1 == n_roles_2
    assert n_perms_1 == n_perms_2


@pytest.mark.asyncio
async def test_seed_role_permission_links():
    """platform_admin 应有所有 platform.* 权限。"""
    from hub.seed import run_seed
    from hub.models import HubRole
    await run_seed()
    admin = await HubRole.get(code="platform_admin").prefetch_related("permissions")
    perms = [p async for p in admin.permissions]
    platform_perms = [p for p in perms if p.code.startswith("platform.")]
    assert len(platform_perms) >= 8  # 至少覆盖所有 platform.* 权限码
