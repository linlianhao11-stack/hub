"""Plan 6 Task 7：生成型 tool（合同 / 报价 / Excel）。

特点：
- ToolType.GENERATE（不需 confirmation_action_id；register-time 不强制）
- 输出对请求人本人（生成 docx/xlsx + 钉钉发文件）
- 重复调用允许（每次新 draft；不强制幂等）
"""
from __future__ import annotations

import logging

from hub.adapters.channel.dingtalk_sender import DingTalkSender
from hub.adapters.channel.dingtalk_sender import DingTalkSendError as DingTalkSendError
from hub.adapters.downstream.erp4 import Erp4Adapter
from hub.adapters.downstream.erp4 import ErpAdapterError as ErpAdapterError
from hub.adapters.downstream.erp4 import ErpNotFoundError as ErpNotFoundError

# ── re-export models for sub-module test-patch compatibility ──
from hub.models.contract import ContractDraft as ContractDraft
from hub.models.contract import ContractTemplate as ContractTemplate
from hub.models.identity import ChannelUserBinding as ChannelUserBinding

logger = logging.getLogger("hub.agent.tools.generate_tools")

# 模块单例（与 erp_tools 同模式）
_dingtalk_sender: DingTalkSender | None = None
_erp_adapter: Erp4Adapter | None = None


def set_dependencies(
    *,
    sender: DingTalkSender | None,
    erp: Erp4Adapter | None,
) -> None:
    """app startup 调；测试 fixture 注入 mock。"""
    global _dingtalk_sender, _erp_adapter
    _dingtalk_sender = sender
    _erp_adapter = erp


def current_sender() -> DingTalkSender:
    if _dingtalk_sender is None:
        raise RuntimeError(
            "DingTalkSender 未初始化（startup 必须先调 set_dependencies）"
        )
    return _dingtalk_sender


def current_erp_adapter() -> Erp4Adapter:
    if _erp_adapter is None:
        raise RuntimeError("Erp4Adapter 未初始化（startup 必须先调 set_dependencies）")
    return _erp_adapter


# ── sub-module imports (MUST be after model re-exports + dep functions above) ──
# ruff: noqa: I001
from hub.agent.tools.generate_tools.contract_gen import (  # noqa: E402
    GENERATE_CONTRACT_DRAFT_SCHEMA as GENERATE_CONTRACT_DRAFT_SCHEMA,
    _compute_contract_fingerprint as _compute_contract_fingerprint,
    _normalize_for_fingerprint as _normalize_for_fingerprint,
    generate_contract_draft as generate_contract_draft,
)
from hub.agent.tools.generate_tools.quote_gen import (  # noqa: E402
    GENERATE_PRICE_QUOTE_SCHEMA as GENERATE_PRICE_QUOTE_SCHEMA,
    export_to_excel as export_to_excel,
    generate_price_quote as generate_price_quote,
)
from hub.agent.tools.registry import ToolRegistry  # noqa: E402
from hub.agent.tools.types import ToolType  # noqa: E402

# ===== register =====


def register_all(registry: ToolRegistry) -> None:
    """3 个 GENERATE 类 tool 注册。

    GENERATE 类不强制 confirmation_action_id（register fail-fast 不触发）。
    """
    registry.register(
        "generate_contract_draft",
        generate_contract_draft,
        perm="usecase.generate_contract.use",
        tool_type=ToolType.GENERATE,
        description="生成销售合同草稿 docx 并发到钉钉",
    )
    registry.register(
        "generate_price_quote",
        generate_price_quote,
        perm="usecase.generate_quote.use",
        tool_type=ToolType.GENERATE,
        description="生成客户报价单 docx 并发到钉钉",
    )
    registry.register(
        "export_to_excel",
        export_to_excel,
        perm="usecase.export.use",
        tool_type=ToolType.GENERATE,
        description="把表格数据导出成 .xlsx 发到钉钉",
    )
