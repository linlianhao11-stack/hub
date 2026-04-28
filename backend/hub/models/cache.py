from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class ErpUserStateCache(Model):
    """缓存 hub_user 对应 ERP 是否启用（10 分钟 TTL）。"""
    hub_user = fields.OneToOneField("models.HubUser", pk=True, related_name="erp_state_cache")
    erp_active = fields.BooleanField()
    checked_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "erp_user_state_cache"
