"""合同模板与草稿模型（Plan 6）。

包含两张表：
  - ContractTemplate：合同模板（admin 上传管理）
  - ContractDraft：Agent 生成的合同草稿（发给请求人，不需审批流）
"""
from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


class ContractTemplate(Model):
    """合同模板。

    admin 上传 docx，标记 {{placeholder}} 占位符，由 Agent 在生成合同时填充。
    """

    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=200)  # 模板名称
    template_type = fields.CharField(max_length=50)  # 类型：sales/purchase/framework/etc
    file_storage_key = fields.TextField()  # 存储 docx 文件（第一版：base64 编码，TEXT 无长度限制）
    placeholders = fields.JSONField()  # 占位符定义 [{name, type, required}, ...]
    description = fields.TextField(null=True)  # 模板说明
    is_active = fields.BooleanField(default=True)  # 是否启用
    created_by_hub_user_id = fields.IntField(null=True)  # 创建人（逻辑引用，非 FK 约束）
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "contract_template"


class ContractDraft(Model):
    """Agent 生成的合同草稿。

    发给请求人本人，**不需要审批流**。
    存储目的：1. 后续审计；2. 销售改完发回 HUB 重生成。
    """

    id = fields.IntField(pk=True)
    template_id = fields.IntField(null=True)  # 关联 ContractTemplate（逻辑引用，非 FK 约束）
    requester_hub_user_id = fields.IntField()  # 请求人 HubUser ID
    customer_id = fields.IntField()  # ERP customer_id
    items = fields.JSONField()  # 合同条款/行项目
    rendered_file_storage_key = fields.CharField(max_length=500, null=True)  # 生成的 docx 文件 key
    status = fields.CharField(max_length=20, default="generated")  # generated/sent/superseded
    conversation_id = fields.CharField(max_length=200, null=True)  # 来源对话 ID
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "contract_draft"
