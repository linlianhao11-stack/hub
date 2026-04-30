"""Agent 三层 Memory 模型（Plan 6）。

包含三张表：
  - UserMemory：用户级记忆（以 hub_user_id 为主键）
  - CustomerMemory：客户级记忆（以 ERP customer_id 为主键）
  - ProductMemory：商品级记忆（以 ERP product_id 为主键）

facts 字段为 JSONB 列表，每条记录格式：
  {fact, source_conversation, confidence, created_at}
"""
from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


class UserMemory(Model):
    """用户级 Agent 记忆。

    保存用户的操作偏好、合同条款偏好等，跨对话持久化。
    """

    hub_user_id = fields.IntField(pk=True)  # 关联 HubUser（逻辑引用，非 FK 约束）
    facts = fields.JSONField(default=list)  # 事实列表 [{fact, source_conversation, confidence, created_at}]
    preferences = fields.JSONField(default=dict)  # 偏好配置（合同模板/付款条款等）
    updated_at = fields.DatetimeField(auto_now=True)  # 最后更新时间

    class Meta:
        table = "user_memory"


class CustomerMemory(Model):
    """客户级 Agent 记忆。

    保存客户的议价习惯、付款记录摘要等，跨对话持久化。
    """

    erp_customer_id = fields.IntField(pk=True)  # ERP 系统 customer ID
    facts = fields.JSONField(default=list)  # 事实列表 [{fact, source_conversation, confidence, created_at}]
    last_referenced_at = fields.DatetimeField(null=True)  # 最后被引用时间
    updated_at = fields.DatetimeField(auto_now=True)  # 最后更新时间

    class Meta:
        table = "customer_memory"


class ProductMemory(Model):
    """商品级 Agent 记忆。

    保存商品的断货/停产/替代品等信息，跨对话持久化。
    """

    erp_product_id = fields.IntField(pk=True)  # ERP 系统 product ID
    facts = fields.JSONField(default=list)  # 事实列表 [{fact, source_conversation, confidence, created_at}]
    updated_at = fields.DatetimeField(auto_now=True)  # 最后更新时间

    class Meta:
        table = "product_memory"
