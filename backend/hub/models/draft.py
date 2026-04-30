"""写操作草稿表模型（Plan 6）。

包含三张表：
  - VoucherDraft：凭证草稿（五状态机 + creating 租约防重建）
  - PriceAdjustmentRequest：调价申请（三状态 pending/approved/rejected）
  - StockAdjustmentRequest：库存调整申请（三状态 pending/approved/rejected）

所有写操作草稿都含 confirmation_action_id VARCHAR(64)，用于钉钉确认幂等键。
"""
from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


class VoucherDraft(Model):
    """凭证草稿。

    状态机（五值）：
      pending → creating → created → approved
                         ↘ pending（创建失败回滚）
      pending → rejected
      creating（崩溃残留，5 min 租约过期后 batch 接管）→ created

    confirmation_action_id 为幂等键：写工具在调 ERP API 时以此做"先查后插 + IntegrityError"幂等保护。
    """

    id = fields.IntField(pk=True)
    requester_hub_user_id = fields.IntField()  # 请求人 HubUser ID
    voucher_data = fields.JSONField()  # 凭证内容（科目/金额/摘要）
    rule_matched = fields.CharField(max_length=200, null=True)  # 匹配的凭证模板
    status = fields.CharField(max_length=20, default="pending")  # pending/creating/created/approved/rejected
    creating_started_at = fields.DatetimeField(null=True)  # creating 状态进入时间，用于 5 min 租约判断
    approved_by_hub_user_id = fields.IntField(null=True)  # 审批人（逻辑引用）
    approved_at = fields.DatetimeField(null=True)  # 审批时间
    rejection_reason = fields.CharField(max_length=500, null=True)  # 拒绝原因
    erp_voucher_id = fields.IntField(null=True)  # 落 ERP 后的 voucher ID
    conversation_id = fields.CharField(max_length=200, null=True)  # 来源对话 ID
    confirmation_action_id = fields.CharField(max_length=64, null=True)  # 钉钉确认幂等键
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "voucher_draft"
        indexes = [
            ("status", "created_at"),          # idx_pending：状态筛查
            ("status", "creating_started_at"),  # idx_creating_lease：崩溃恢复扫描
        ]
        # 部分唯一索引在手写迁移中创建（Tortoise 不支持 WHERE 条件的 partial index）
        # idx_voucher_draft_action_id_unique ON voucher_draft (requester_hub_user_id, confirmation_action_id)
        # WHERE confirmation_action_id IS NOT NULL


class PriceAdjustmentRequest(Model):
    """调价申请。

    状态：pending / approved / rejected（三值）。
    confirmation_action_id 为幂等键，防止钉钉重复确认触发重复调用。
    """

    id = fields.IntField(pk=True)
    requester_hub_user_id = fields.IntField()  # 请求人 HubUser ID
    customer_id = fields.IntField()  # ERP customer_id
    product_id = fields.IntField()  # ERP product_id
    current_price = fields.DecimalField(max_digits=12, decimal_places=2, null=True)  # 当前价格
    new_price = fields.DecimalField(max_digits=12, decimal_places=2, null=True)  # 申请调整后价格
    discount_pct = fields.DecimalField(max_digits=5, decimal_places=4, null=True)  # 折扣比例
    reason = fields.TextField(null=True)  # 申请原因
    status = fields.CharField(max_length=20, default="pending")  # pending/approved/rejected
    approved_by_hub_user_id = fields.IntField(null=True)  # 审批人
    approved_at = fields.DatetimeField(null=True)  # 审批时间
    rejection_reason = fields.CharField(max_length=500, null=True)  # 拒绝原因
    conversation_id = fields.CharField(max_length=200, null=True)  # 来源对话 ID
    confirmation_action_id = fields.CharField(max_length=64, null=True)  # 钉钉确认幂等键
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "price_adjustment_request"
        indexes = [
            ("status", "created_at"),  # idx_pending
        ]
        # 部分唯一索引在手写迁移中创建
        # idx_price_adj_action_id_unique ON price_adjustment_request (requester_hub_user_id, confirmation_action_id)
        # WHERE confirmation_action_id IS NOT NULL


class StockAdjustmentRequest(Model):
    """库存调整申请。

    状态：pending / approved / rejected（三值）。
    confirmation_action_id 为幂等键，防止钉钉重复确认触发重复调用。
    """

    id = fields.IntField(pk=True)
    requester_hub_user_id = fields.IntField()  # 请求人 HubUser ID
    product_id = fields.IntField()  # ERP product_id
    warehouse_id = fields.IntField(null=True)  # ERP warehouse_id（可选）
    current_stock = fields.DecimalField(max_digits=12, decimal_places=4, null=True)  # 当前库存
    new_stock = fields.DecimalField(max_digits=12, decimal_places=4, null=True)  # 调整后库存
    adjustment_qty = fields.DecimalField(max_digits=12, decimal_places=4, null=True)  # 调整数量（正增负减）
    reason = fields.TextField(null=True)  # 申请原因
    status = fields.CharField(max_length=20, default="pending")  # pending/approved/rejected
    approved_by_hub_user_id = fields.IntField(null=True)  # 审批人
    approved_at = fields.DatetimeField(null=True)  # 审批时间
    rejection_reason = fields.CharField(max_length=500, null=True)  # 拒绝原因
    conversation_id = fields.CharField(max_length=200, null=True)  # 来源对话 ID
    confirmation_action_id = fields.CharField(max_length=64, null=True)  # 钉钉确认幂等键
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "stock_adjustment_request"
        indexes = [
            ("status", "created_at"),  # idx_pending
        ]
        # 部分唯一索引在手写迁移中创建
        # idx_stock_adj_action_id_unique ON stock_adjustment_request (requester_hub_user_id, confirmation_action_id)
        # WHERE confirmation_action_id IS NOT NULL
