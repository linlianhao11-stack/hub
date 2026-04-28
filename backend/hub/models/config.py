from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class DownstreamSystem(Model):
    id = fields.IntField(pk=True)
    downstream_type = fields.CharField(max_length=30)
    name = fields.CharField(max_length=100)
    base_url = fields.CharField(max_length=500)
    encrypted_apikey = fields.BinaryField()
    apikey_scopes = fields.JSONField(default=list)
    status = fields.CharField(max_length=20, default="active")
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "downstream_system"


class ChannelApp(Model):
    id = fields.IntField(pk=True)
    channel_type = fields.CharField(max_length=30)
    name = fields.CharField(max_length=100)
    encrypted_app_key = fields.BinaryField()
    encrypted_app_secret = fields.BinaryField()
    robot_id = fields.CharField(max_length=200, null=True)
    status = fields.CharField(max_length=20, default="active")
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "channel_app"


class AIProvider(Model):
    id = fields.IntField(pk=True)
    provider_type = fields.CharField(max_length=30)  # deepseek / qwen / claude
    name = fields.CharField(max_length=100)
    encrypted_api_key = fields.BinaryField()
    base_url = fields.CharField(max_length=500)
    model = fields.CharField(max_length=100)
    config = fields.JSONField(default=dict)
    status = fields.CharField(max_length=20, default="active")

    class Meta:
        table = "ai_provider"


class SystemConfig(Model):
    """key-value 配置表（告警接收人、TTL、运行时常量等）。"""
    key = fields.CharField(max_length=100, pk=True)
    value = fields.JSONField()
    description = fields.TextField(null=True)
    updated_at = fields.DatetimeField(auto_now=True)
    updated_by_hub_user_id = fields.IntField(null=True)

    class Meta:
        table = "system_config"
