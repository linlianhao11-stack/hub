"""Plan 6 Task 8：写草稿 tool（凭证 / 调价 / 库存）。

三个写草稿 tool，均为 ToolType.WRITE_DRAFT，ToolRegistry register fail-fast 会
校验签名必须声明 confirmation_action_id。

幂等保护（每个 tool 均实现）：
  先查 (requester_hub_user_id, confirmation_action_id) 是否已存在记录
  → 若存在直接返回（idempotent_replay）
  → 若不存在 INSERT；IntegrityError catch + 回查
  → 回查到就返；回查不到则 reraise（极罕见 DB 故障）
"""
from __future__ import annotations

from hub.agent.tools.draft_tools._adapter import (
    current_erp_adapter,
    set_erp_adapter,
)
from hub.agent.tools.draft_tools.price_adjust import (
    CREATE_PRICE_ADJUSTMENT_REQUEST_SCHEMA,
    _fetch_current_price,
    create_price_adjustment_request,
)
from hub.agent.tools.draft_tools.stock_adjust import (
    CREATE_STOCK_ADJUSTMENT_REQUEST_SCHEMA,
    create_stock_adjustment_request,
)
from hub.agent.tools.draft_tools.voucher_draft import (
    CREATE_VOUCHER_DRAFT_SCHEMA,
    create_voucher_draft,
)
from hub.agent.tools.draft_tools.voucher_draft import (
    _get_max_voucher_amount as _get_max_voucher_amount,
)
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools.types import ToolType

# re-export 模型类（测试 patch 路径 hub.agent.tools.draft_tools.VoucherDraft 等需要它们存在于包命名空间）
from hub.models.draft import PriceAdjustmentRequest as PriceAdjustmentRequest
from hub.models.draft import StockAdjustmentRequest as StockAdjustmentRequest
from hub.models.draft import VoucherDraft as VoucherDraft

# ===== register_all =====

def register_all(registry: ToolRegistry) -> None:
    """3 个 WRITE_DRAFT 类 tool 注册（必须声明 confirmation_action_id；register fail-fast 校验）。"""
    registry.register(
        "create_voucher_draft", create_voucher_draft,
        perm="usecase.create_voucher.use",
        tool_type=ToolType.WRITE_DRAFT,
        description="创建凭证草稿（挂会计审批 inbox）",
    )
    registry.register(
        "create_price_adjustment_request", create_price_adjustment_request,
        perm="usecase.adjust_price.use",
        tool_type=ToolType.WRITE_DRAFT,
        description="创建调价请求（挂销售主管审批 inbox）",
    )
    registry.register(
        "create_stock_adjustment_request", create_stock_adjustment_request,
        perm="usecase.adjust_stock.use",
        tool_type=ToolType.WRITE_DRAFT,
        description="创建库存调整请求（挂库管/财务审批 inbox）",
    )


__all__ = [
    # adapter 管理
    "set_erp_adapter",
    "current_erp_adapter",
    # 注册
    "register_all",
    # voucher
    "CREATE_VOUCHER_DRAFT_SCHEMA",
    "create_voucher_draft",
    # price
    "CREATE_PRICE_ADJUSTMENT_REQUEST_SCHEMA",
    "create_price_adjustment_request",
    # stock
    "CREATE_STOCK_ADJUSTMENT_REQUEST_SCHEMA",
    "create_stock_adjustment_request",
    # 辅助（测试引用）
    "_fetch_current_price",
]
