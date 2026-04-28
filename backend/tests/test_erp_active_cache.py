from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_cache_hit_within_ttl():
    """TTL 内不调 ERP，直接返回缓存。"""
    from hub.models import ErpUserStateCache, HubUser
    from hub.services.erp_active_cache import ErpActiveCache

    user = await HubUser.create(display_name="x")
    await ErpUserStateCache.create(
        hub_user_id=user.id, erp_active=True,
        checked_at=datetime.now(UTC),
    )

    erp_adapter = AsyncMock()
    cache = ErpActiveCache(erp_adapter=erp_adapter, ttl_seconds=600)

    result = await cache.is_active(hub_user=user, erp_user_id=42)
    assert result is True
    erp_adapter.get_user_active_state.assert_not_called()


@pytest.mark.asyncio
async def test_cache_miss_calls_erp_and_caches():
    """缓存过期 → 调 ERP → 写回缓存。"""
    from hub.models import ErpUserStateCache, HubUser
    from hub.services.erp_active_cache import ErpActiveCache

    user = await HubUser.create(display_name="y")

    erp_adapter = AsyncMock()
    erp_adapter.get_user_active_state = AsyncMock(return_value={"is_active": True, "username": "y"})

    cache = ErpActiveCache(erp_adapter=erp_adapter, ttl_seconds=600)
    result = await cache.is_active(hub_user=user, erp_user_id=42)
    assert result is True
    erp_adapter.get_user_active_state.assert_awaited_once_with(42)

    # 写回了缓存
    cached = await ErpUserStateCache.filter(hub_user_id=user.id).first()
    assert cached is not None
    assert cached.erp_active is True


@pytest.mark.asyncio
async def test_cache_expired_refreshes():
    """缓存超过 TTL → 重新调 ERP。"""
    from hub.models import ErpUserStateCache, HubUser
    from hub.services.erp_active_cache import ErpActiveCache

    user = await HubUser.create(display_name="z")
    old_time = datetime.now(UTC) - timedelta(seconds=700)
    await ErpUserStateCache.create(hub_user_id=user.id, erp_active=True, checked_at=old_time)

    erp_adapter = AsyncMock()
    erp_adapter.get_user_active_state = AsyncMock(return_value={"is_active": False, "username": "z"})

    cache = ErpActiveCache(erp_adapter=erp_adapter, ttl_seconds=600)
    result = await cache.is_active(hub_user=user, erp_user_id=42)
    assert result is False
    erp_adapter.get_user_active_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache():
    """force_refresh=True 跳过 TTL，强制调 ERP。"""
    from hub.models import ErpUserStateCache, HubUser
    from hub.services.erp_active_cache import ErpActiveCache

    user = await HubUser.create(display_name="a")
    await ErpUserStateCache.create(
        hub_user_id=user.id, erp_active=True, checked_at=datetime.now(UTC),
    )

    erp_adapter = AsyncMock()
    erp_adapter.get_user_active_state = AsyncMock(return_value={"is_active": False})

    cache = ErpActiveCache(erp_adapter=erp_adapter, ttl_seconds=600)
    result = await cache.is_active(hub_user=user, erp_user_id=42, force_refresh=True)
    assert result is False
    erp_adapter.get_user_active_state.assert_awaited_once()
