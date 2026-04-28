from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class HubUser(Model):
    id = fields.IntField(pk=True)
    display_name = fields.CharField(max_length=100)
    status = fields.CharField(max_length=20, default="active")  # active / suspended / revoked
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "hub_user"


class ChannelUserBinding(Model):
    id = fields.IntField(pk=True)
    hub_user = fields.ForeignKeyField("models.HubUser", related_name="channel_bindings")
    channel_type = fields.CharField(max_length=30)  # dingtalk / wecom / web
    channel_userid = fields.CharField(max_length=200)
    display_meta = fields.JSONField(default=dict)
    status = fields.CharField(max_length=20, default="active")  # active / revoked
    bound_at = fields.DatetimeField(auto_now_add=True)
    revoked_at = fields.DatetimeField(null=True)
    revoked_reason = fields.CharField(max_length=100, null=True)

    class Meta:
        table = "channel_user_binding"
        unique_together = (("channel_type", "channel_userid"),)


class DownstreamIdentity(Model):
    id = fields.IntField(pk=True)
    hub_user = fields.ForeignKeyField("models.HubUser", related_name="downstream_identities")
    downstream_type = fields.CharField(max_length=30)  # erp / crm / oa
    downstream_user_id = fields.IntField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "downstream_identity"
        unique_together = (("hub_user_id", "downstream_type"),)
