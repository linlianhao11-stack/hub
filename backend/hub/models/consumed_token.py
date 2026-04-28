"""ERP confirm-final 调用 HUB 时携带的 token_id 防 replay 表。"""
from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


class ConsumedBindingToken(Model):
    id = fields.IntField(pk=True)
    erp_token_id = fields.IntField(unique=True)  # 唯一约束 = 防 replay 物理保证
    hub_user_id = fields.IntField()
    consumed_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "consumed_binding_token"
