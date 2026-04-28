from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class HubRole(Model):
    id = fields.IntField(pk=True)
    code = fields.CharField(max_length=80, unique=True)
    name = fields.CharField(max_length=100)  # UI 显示中文名
    description = fields.TextField(null=True)
    is_builtin = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)

    permissions: fields.ManyToManyRelation["HubPermission"] = fields.ManyToManyField(
        "models.HubPermission", related_name="roles", through="hub_role_permission",
    )

    class Meta:
        table = "hub_role"


class HubPermission(Model):
    id = fields.IntField(pk=True)
    code = fields.CharField(max_length=120, unique=True)  # 三段式 platform.tasks.read
    resource = fields.CharField(max_length=40)
    sub_resource = fields.CharField(max_length=40)
    action = fields.CharField(max_length=20)  # read / write / use / admin
    name = fields.CharField(max_length=100)  # UI 中文名
    description = fields.TextField(null=True)

    roles: fields.ManyToManyRelation["HubRole"]

    class Meta:
        table = "hub_permission"


class HubUserRole(Model):
    """中间表显式建模便于带审计字段（assigned_by / assigned_at）。"""
    id = fields.IntField(pk=True)
    hub_user = fields.ForeignKeyField("models.HubUser", related_name="user_roles")
    role = fields.ForeignKeyField("models.HubRole", related_name="user_roles")
    assigned_at = fields.DatetimeField(auto_now_add=True)
    assigned_by_hub_user_id = fields.IntField(null=True)

    class Meta:
        table = "hub_user_role"
        unique_together = (("hub_user_id", "role_id"),)
