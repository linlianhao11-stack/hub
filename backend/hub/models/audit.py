from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class TaskLog(Model):
    """元数据，长保留 365 天。"""
    id = fields.IntField(pk=True)
    task_id = fields.CharField(max_length=64, unique=True)
    task_type = fields.CharField(max_length=80)
    channel_type = fields.CharField(max_length=30)
    channel_userid = fields.CharField(max_length=200)
    hub_user_id = fields.IntField(null=True)
    status = fields.CharField(max_length=40)
    intent_parser = fields.CharField(max_length=20, null=True)  # rule / llm
    intent_confidence = fields.FloatField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    finished_at = fields.DatetimeField(null=True)
    duration_ms = fields.IntField(null=True)
    error_classification = fields.CharField(max_length=50, null=True)
    error_summary = fields.CharField(max_length=500, null=True)
    retry_count = fields.IntField(default=0)

    class Meta:
        table = "task_log"


class TaskPayload(Model):
    """敏感数据，加密 + 短保留 30 天。"""
    id = fields.IntField(pk=True)
    task_log = fields.OneToOneField("models.TaskLog", related_name="payload", on_delete=fields.CASCADE)
    encrypted_request = fields.BinaryField()
    encrypted_erp_calls = fields.BinaryField(null=True)
    encrypted_response = fields.BinaryField()
    created_at = fields.DatetimeField(auto_now_add=True)
    expires_at = fields.DatetimeField()

    class Meta:
        table = "task_payload"


class AuditLog(Model):
    """admin 操作审计（创建 ApiKey / 解绑 / 改角色等）。"""
    id = fields.IntField(pk=True)
    who_hub_user_id = fields.IntField()
    action = fields.CharField(max_length=80)
    target_type = fields.CharField(max_length=50, null=True)
    target_id = fields.CharField(max_length=64, null=True)
    detail = fields.JSONField(default=dict)
    ip = fields.CharField(max_length=45, null=True)
    user_agent = fields.CharField(max_length=500, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "audit_log"


class MetaAuditLog(Model):
    """看 payload 留痕（"谁在监控监控员"）。"""
    id = fields.IntField(pk=True)
    who_hub_user_id = fields.IntField()
    viewed_task_id = fields.CharField(max_length=64)
    viewed_at = fields.DatetimeField(auto_now_add=True)
    ip = fields.CharField(max_length=45, null=True)

    class Meta:
        table = "meta_audit_log"
