from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_resolve_active_user():
    from hub.models import ChannelUserBinding, DownstreamIdentity, HubUser
    from hub.services.identity_service import IdentityService

    user = await HubUser.create(display_name="A")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m1", status="active",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=42)

    erp_cache = AsyncMock()
    erp_cache.is_active = AsyncMock(return_value=True)

    svc = IdentityService(erp_active_cache=erp_cache)
    res = await svc.resolve(dingtalk_userid="m1")

    assert res.found is True
    assert res.erp_active is True
    assert res.hub_user_id == user.id
    assert res.erp_user_id == 42


@pytest.mark.asyncio
async def test_resolve_unbound():
    from hub.services.identity_service import IdentityService

    erp_cache = AsyncMock()
    svc = IdentityService(erp_active_cache=erp_cache)
    res = await svc.resolve(dingtalk_userid="never_bound")

    assert res.found is False
    assert res.erp_active is False
    erp_cache.is_active.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_revoked_binding():
    """status=revoked 视同未绑定。"""
    from hub.models import ChannelUserBinding, HubUser
    from hub.services.identity_service import IdentityService

    user = await HubUser.create(display_name="B")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m2", status="revoked",
    )

    svc = IdentityService(erp_active_cache=AsyncMock())
    res = await svc.resolve(dingtalk_userid="m2")
    assert res.found is False


@pytest.mark.asyncio
async def test_resolve_erp_disabled():
    """绑定有效但 ERP 用户被禁用。"""
    from hub.models import ChannelUserBinding, DownstreamIdentity, HubUser
    from hub.services.identity_service import IdentityService

    user = await HubUser.create(display_name="C")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m3", status="active",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=99)

    erp_cache = AsyncMock()
    erp_cache.is_active = AsyncMock(return_value=False)

    svc = IdentityService(erp_active_cache=erp_cache)
    res = await svc.resolve(dingtalk_userid="m3")

    assert res.found is True
    assert res.erp_active is False  # 关键：ERP 已禁用
    assert res.erp_user_id == 99


@pytest.mark.asyncio
async def test_resolve_no_erp_identity():
    """已绑定 HUB 但没关联 ERP 身份（不该出现的边界情况）。"""
    from hub.models import ChannelUserBinding, HubUser
    from hub.services.identity_service import IdentityService

    user = await HubUser.create(display_name="D")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m4", status="active",
    )
    # 不创建 DownstreamIdentity

    svc = IdentityService(erp_active_cache=AsyncMock())
    res = await svc.resolve(dingtalk_userid="m4")
    assert res.found is True
    assert res.erp_user_id is None
    assert res.erp_active is False
