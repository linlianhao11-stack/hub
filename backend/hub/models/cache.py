from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


class ErpUserStateCache(Model):
    """缓存 hub_user 对应 ERP 是否启用（10 分钟 TTL）。"""
    hub_user = fields.OneToOneField("models.HubUser", pk=True, related_name="erp_state_cache")
    erp_active = fields.BooleanField()
    # service 层显式管理 checked_at（不用 auto_now，方便测试构造老时间，
    # 也避免 force_refresh 时 save() 触发 auto_now 误打覆盖）
    checked_at = fields.DatetimeField()

    class Meta:
        table = "erp_user_state_cache"
