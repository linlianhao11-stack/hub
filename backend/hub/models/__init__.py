"""HUB 数据模型聚合。

注意：所有模型必须在此模块（或被它 import）下注册，
让 Tortoise.init(modules={"models": ["hub.models"]}) 一次扫描全部。
"""
from hub.models.audit import AuditLog, MetaAuditLog, TaskLog, TaskPayload
from hub.models.bootstrap import BootstrapToken
from hub.models.cache import ErpUserStateCache
from hub.models.config import AIProvider, ChannelApp, DownstreamSystem, SystemConfig
from hub.models.consumed_token import ConsumedBindingToken
from hub.models.identity import ChannelUserBinding, DownstreamIdentity, HubUser
from hub.models.rbac import HubPermission, HubRole, HubUserRole

# Plan 6 新增模型
from hub.models.conversation import ConversationLog, ToolCallLog
from hub.models.memory import CustomerMemory, ProductMemory, UserMemory
from hub.models.contract import ContractDraft, ContractTemplate
from hub.models.draft import PriceAdjustmentRequest, StockAdjustmentRequest, VoucherDraft

__all__ = [
    "HubUser", "ChannelUserBinding", "DownstreamIdentity",
    "HubRole", "HubPermission", "HubUserRole",
    "DownstreamSystem", "ChannelApp", "AIProvider", "SystemConfig",
    "TaskLog", "TaskPayload", "AuditLog", "MetaAuditLog",
    "ErpUserStateCache",
    "BootstrapToken",
    "ConsumedBindingToken",
    # Plan 6
    "ConversationLog", "ToolCallLog",
    "UserMemory", "CustomerMemory", "ProductMemory",
    "ContractTemplate", "ContractDraft",
    "VoucherDraft", "PriceAdjustmentRequest", "StockAdjustmentRequest",
]
