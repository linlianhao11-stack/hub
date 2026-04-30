"""Agent 对话日志模型（Plan 6）。

包含两张表：
  - ConversationLog：用户单次对话的元数据
  - ToolCallLog：对话内每次 tool 调用的明细（无 FK 约束，用字符串 conversation_id 关联）
"""
from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


class ConversationLog(Model):
    """Agent 单次对话日志。

    conversation_id 与 Redis session 同 ID，用于跨模块关联。
    """

    id = fields.BigIntField(pk=True)
    conversation_id = fields.CharField(max_length=200, unique=True)  # 对话唯一标识
    hub_user_id = fields.IntField(null=True)  # 关联 HubUser（逻辑引用，非 FK 约束）
    channel_userid = fields.CharField(max_length=200)  # 钉钉/来源渠道用户 ID
    started_at = fields.DatetimeField()  # 对话开始时间
    ended_at = fields.DatetimeField(null=True)  # 对话结束时间（成功或失败后写）
    rounds_count = fields.IntField(default=0)  # LLM round 数量
    tokens_used = fields.IntField(default=0)  # 累计 token 消耗
    tokens_cost_yuan = fields.DecimalField(max_digits=10, decimal_places=4, null=True)  # 估算成本（元）
    final_status = fields.CharField(max_length=50, null=True)  # success/failed_user/failed_system/fallback_to_rule
    error_summary = fields.CharField(max_length=500, null=True)  # 错误摘要

    class Meta:
        table = "conversation_log"
        indexes = [
            ("hub_user_id", "started_at"),  # idx_user_started
        ]


class ToolCallLog(Model):
    """Agent 对话内单次 tool 调用日志。

    conversation_id 为字符串，**不设 FK 约束**，以支持"conversation_id 不存在也能写入"的观测需求。
    """

    id = fields.BigIntField(pk=True)
    conversation_id = fields.CharField(max_length=200)  # 逻辑关联 ConversationLog.conversation_id（无 FK）
    round_idx = fields.IntField()  # 在第几个 round 调用
    tool_name = fields.CharField(max_length=100)  # 工具名称
    args_json = fields.JSONField(null=True)  # 调用参数
    result_json = fields.JSONField(null=True)  # 调用结果（超 10KB 自动截断）
    duration_ms = fields.IntField(null=True)  # 耗时（毫秒）
    error = fields.CharField(max_length=500, null=True)  # 异常信息（截断到 500 字符）
    called_at = fields.DatetimeField(auto_now_add=True)  # 调用时间（写入时自动填充，无需显式传入）

    class Meta:
        table = "tool_call_log"
        indexes = [
            ("conversation_id", "round_idx"),  # idx_conv
        ]
