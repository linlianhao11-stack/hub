"""Plan 6 Task 7：生成型 tool（合同 / 报价 / Excel）。

特点：
- ToolType.GENERATE（不需 confirmation_action_id；register-time 不强制）
- 输出对请求人本人（生成 docx/xlsx + 钉钉发文件）
- 重复调用允许（每次新 draft；不强制幂等）
"""
from __future__ import annotations

import logging
from datetime import date

from hub.adapters.channel.dingtalk_sender import DingTalkSender, DingTalkSendError
from hub.adapters.downstream.erp4 import Erp4Adapter
from hub.agent.document.contract import (
    ContractRenderer,
    TemplateNotFoundError,
    TemplateRenderError,
)
from hub.agent.document.excel import ExcelExporter
from hub.agent.document.storage import DocumentStorage
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools.types import ToolType
from hub.models.contract import ContractDraft, ContractTemplate
from hub.models.identity import ChannelUserBinding

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


# ===== 3 个 tool =====


async def generate_contract_draft(
    template_id: int,
    customer_id: int,
    items: list[dict],
    extras: dict | None = None,
    *,
    hub_user_id: int,
    conversation_id: str,
    acting_as_user_id: int,
) -> dict:
    """生成销售合同草稿 docx 并发到钉钉。

    Args:
        template_id: ContractTemplate.id
        customer_id: ERP 客户 ID
        items: [{"product_id", "name", "qty", "price"}]
        extras: 额外占位符如 contract_no / payment_terms

    Returns:
        {"draft_id", "file_sent", "file_name"}
    """
    # 1. 拉客户信息（ERP 无 get_customer；用 search_customers 后按 id 过滤）
    erp = current_erp_adapter()
    search_resp = await erp.search_customers(
        query=str(customer_id),
        acting_as_user_id=acting_as_user_id,
    )
    customers = search_resp.get("items", []) if isinstance(search_resp, dict) else []
    customer = next(
        (c for c in customers if c.get("id") == customer_id),
        None,
    )
    if not customer:
        # fallback：只有 id，人工字段空
        customer = {"id": customer_id, "name": f"客户{customer_id}", "address": ""}

    # 2. 渲染 docx（可能抛 TemplateNotFoundError / TemplateRenderError）
    renderer = ContractRenderer()
    docx_bytes = await renderer.render(
        template_id=template_id,
        customer=customer,
        items=items,
        extras=extras or {},
    )

    # 3. 加密存储 + 持久化 ContractDraft
    storage = DocumentStorage()
    await storage.put(docx_bytes, encrypted=True)  # 生产场景写 bytea；第一版不落额外字段

    draft = await ContractDraft.create(
        template_id=template_id,
        requester_hub_user_id=hub_user_id,
        customer_id=customer_id,
        items=items,
        rendered_file_storage_key=str(template_id),  # 简化：用 template_id 作 key
        conversation_id=conversation_id,
    )

    # 4. 发钉钉
    sender = current_sender()
    binding = await ChannelUserBinding.filter(
        hub_user_id=hub_user_id,
        status="active",
    ).first()
    if not binding:
        logger.warning("hub_user %s 无 active 钉钉绑定，跳过 send_file", hub_user_id)
        return {
            "draft_id": draft.id,
            "file_sent": False,
            "file_name": None,
            "warning": "用户未绑定钉钉，文件未发送",
        }

    file_name = (
        f"销售合同_{customer.get('name')}_{date.today().isoformat()}.docx"
    )
    try:
        await sender.send_file(
            dingtalk_userid=binding.channel_userid,
            file_bytes=docx_bytes,
            file_name=file_name,
            file_type="docx",
        )
    except DingTalkSendError:
        # 草稿已持久化，send_file 失败：让 worker 转死信重试
        logger.exception("send_file 失败 draft_id=%s", draft.id)
        raise

    return {
        "draft_id": draft.id,
        "file_sent": True,
        "file_name": file_name,
    }


async def generate_price_quote(
    customer_id: int,
    items: list[dict],
    extras: dict | None = None,
    *,
    hub_user_id: int,
    conversation_id: str,
    acting_as_user_id: int,
) -> dict:
    """生成报价单 docx（同 generate_contract_draft 模式但用报价模板）。

    简化第一版：自动找第一个 active 的 quote 类型模板。
    """
    template = await ContractTemplate.filter(
        template_type="quote",
        is_active=True,
    ).first()
    if not template:
        return {
            "draft_id": None,
            "file_sent": False,
            "error": "未配置报价模板，请先在管理后台创建 template_type=quote 的模板",
        }
    # 复用 generate_contract_draft 实现
    return await generate_contract_draft(
        template_id=template.id,
        customer_id=customer_id,
        items=items,
        extras=extras,
        hub_user_id=hub_user_id,
        conversation_id=conversation_id,
        acting_as_user_id=acting_as_user_id,
    )


async def export_to_excel(
    table_data: list[dict],
    file_name: str,
    *,
    hub_user_id: int,
    conversation_id: str,
    acting_as_user_id: int,
) -> dict:
    """把 list[dict] 导出 .xlsx 并发到钉钉。

    Args:
        table_data: 表格数据（key = 列名）
        file_name: 文件名（自动补 .xlsx 后缀）
    """
    if not file_name.endswith(".xlsx"):
        file_name += ".xlsx"

    exporter = ExcelExporter()
    xlsx_bytes = await exporter.export(table_data=table_data)

    sender = current_sender()
    binding = await ChannelUserBinding.filter(
        hub_user_id=hub_user_id,
        status="active",
    ).first()
    if not binding:
        return {
            "file_sent": False,
            "warning": "用户未绑定钉钉，文件未发送",
        }

    try:
        await sender.send_file(
            dingtalk_userid=binding.channel_userid,
            file_bytes=xlsx_bytes,
            file_name=file_name,
            file_type="xlsx",
        )
    except DingTalkSendError:
        logger.exception("export_to_excel send_file 失败 file=%s", file_name)
        raise

    return {
        "file_sent": True,
        "file_name": file_name,
        "rows_count": len(table_data),
    }


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
