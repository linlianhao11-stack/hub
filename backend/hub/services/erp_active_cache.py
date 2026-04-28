"""ERP 用户启用状态缓存（spec §8.4 ERP 用户禁用同步）。"""
from __future__ import annotations

from datetime import UTC, datetime

from hub.models import ErpUserStateCache, HubUser


class ErpActiveCache:
    def __init__(self, erp_adapter, *, ttl_seconds: int = 600):
        self.erp = erp_adapter
        self.ttl = ttl_seconds

    async def is_active(
        self, hub_user: HubUser, erp_user_id: int, *, force_refresh: bool = False,
    ) -> bool:
        """查 ERP 用户启用状态：优先缓存，TTL 过期或 force 时调 ERP。"""
        now = datetime.now(UTC)

        if not force_refresh:
            cache = await ErpUserStateCache.filter(hub_user_id=hub_user.id).first()
            if cache and (now - cache.checked_at).total_seconds() < self.ttl:
                return cache.erp_active

        # 缓存过期或 force → 调 ERP
        result = await self.erp.get_user_active_state(erp_user_id)
        is_active = bool(result.get("is_active", False))

        # 写回缓存（upsert）— 用显式 update / create 而非 .save()，
        # 因为 Tortoise OneToOneField(pk=True) 的 save() 生成的 WHERE 子句
        # 误用模型字段名而非数据库列名，导致 column does not exist
        rows = await ErpUserStateCache.filter(hub_user_id=hub_user.id).update(
            erp_active=is_active, checked_at=now,
        )
        if rows == 0:
            await ErpUserStateCache.create(
                hub_user_id=hub_user.id, erp_active=is_active, checked_at=now,
            )

        return is_active
