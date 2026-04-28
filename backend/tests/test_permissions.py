import pytest


@pytest.mark.asyncio
async def test_user_with_all_perms_passes():
    from hub.models import HubRole, HubUser, HubUserRole
    from hub.permissions import has_permission, require_permissions
    from hub.seed import run_seed
    await run_seed()

    user = await HubUser.create(display_name="A")
    role = await HubRole.get(code="bot_user_basic")
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    assert await has_permission(user.id, "channel.dingtalk.use") is True
    assert await has_permission(user.id, "usecase.query_product.use") is True
    assert await has_permission(user.id, "downstream.erp.use") is True

    await require_permissions(user.id, [
        "channel.dingtalk.use",
        "usecase.query_product.use",
        "downstream.erp.use",
    ])  # 不抛


@pytest.mark.asyncio
async def test_missing_permission_raises():
    from hub.error_codes import BizError, BizErrorCode
    from hub.models import HubUser
    from hub.permissions import require_permissions

    user = await HubUser.create(display_name="B")  # 没绑任何角色

    with pytest.raises(BizError) as exc:
        await require_permissions(user.id, ["usecase.query_product.use"])
    assert exc.value.code == BizErrorCode.PERM_NO_PRODUCT_QUERY


@pytest.mark.asyncio
async def test_admin_role_has_all():
    from hub.models import HubRole, HubUser, HubUserRole
    from hub.permissions import has_permission
    from hub.seed import run_seed
    await run_seed()

    user = await HubUser.create(display_name="Adm")
    role = await HubRole.get(code="platform_admin")
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    for code in ["usecase.query_product.use", "downstream.erp.use",
                 "platform.tasks.read", "channel.dingtalk.use"]:
        assert await has_permission(user.id, code) is True


@pytest.mark.asyncio
async def test_permission_to_error_code_mapping():
    """缺权限按 code 映射到具体 BizErrorCode。"""
    from hub.error_codes import BizErrorCode
    from hub.permissions import BIZ_DEFAULT, _permission_to_error_code

    assert _permission_to_error_code("usecase.query_product.use") == BizErrorCode.PERM_NO_PRODUCT_QUERY
    assert _permission_to_error_code("usecase.query_customer_history.use") == BizErrorCode.PERM_NO_CUSTOMER_HISTORY
    assert _permission_to_error_code("downstream.erp.use") == BizErrorCode.PERM_DOWNSTREAM_DENIED
    assert _permission_to_error_code("unknown.x.y") == BIZ_DEFAULT
