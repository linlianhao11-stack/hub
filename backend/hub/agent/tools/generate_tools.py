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
from hub.adapters.downstream.erp4 import Erp4Adapter, ErpAdapterError, ErpNotFoundError
from hub.agent.document.contract import (
    ContractRenderer,
    TemplateNotFoundError,
    TemplateRenderError,
)
from hub.agent.document.excel import ExcelExporter
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
        extras: 额外占位符字段（合同号 / 付款条款等）；
               值类型应为 str/int/float/bool；嵌套 dict/list 可能让模板渲染丢失。
               注意：extras 字段会出现在 LLM 看到的 schema 中（GENERATE 类不含
               confirmation_action_id 等内部字段，但其他业务参数全部对 LLM 可见）。

    Returns:
        {"draft_id", "file_sent", "file_name"}

    已知运维限制（plan 6 第一版）：send_file 抛 DingTalkSendError 后
    ContractDraft 已持久化 + 抛错让 worker 重试。worker 重试时会**重新执行整段
    流程**导致创建第 2 条 ContractDraft —— 用户语义上"我请求 1 次"会得到"DB 多条"。
    监控运维上需注意：失败重试场景下 ContractDraft 行数 != 用户请求次数。
    完整幂等待 Plan 6 follow-up 加 (hub_user_id, conversation_id, template_id, ...) 唯一索引。
    """
    # 1. 拉客户信息（精确 get_customer 避免 keyword 搜索几乎必走 fallback 的问题）
    erp = current_erp_adapter()
    try:
        customer = await erp.get_customer(
            customer_id=customer_id, acting_as_user_id=acting_as_user_id,
        )
    except (ErpNotFoundError, ErpAdapterError) as e:
        logger.warning(
            "get_customer %s 失败（fallback 用 id 占位）conv=%s err=%s",
            customer_id, conversation_id, e,
        )
        customer = {"id": customer_id, "name": f"客户{customer_id}", "address": ""}

    # 2. 渲染 docx（可能抛 TemplateNotFoundError / TemplateRenderError）
    renderer = ContractRenderer()
    try:
        docx_bytes = await renderer.render(
            template_id=template_id,
            customer=customer,
            items=items,
            extras=extras or {},
        )
    except TemplateNotFoundError:
        logger.warning("合同模板 %s 不存在 conv=%s", template_id, conversation_id)
        return {
            "draft_id": None,
            "file_sent": False,
            "error": f"合同模板 {template_id} 不存在或未启用，请联系管理员",
        }
    except TemplateRenderError as e:
        logger.exception("合同模板 %s 渲染失败 conv=%s", template_id, conversation_id)
        return {
            "draft_id": None,
            "file_sent": False,
            "error": f"合同模板渲染失败: {e}（可能是 items 数据缺字段）",
        }

    # 3. 持久化 ContractDraft（metadata 审计用）
    # 第一版：文件不持久化 bytes（待 Plan 11 admin 后台 + 文件存储真正落地后改）。
    # 当前流程：渲染 → 直接 send_file 到钉钉 → 钉钉端有完整 docx；
    # ContractDraft 仅记录 metadata（template_id, items, conversation_id）以便审计。
    # 加密路径（DocumentStorage）已就绪，等加 ContractDraft.rendered_file_bytes BinaryField 后启用。
    draft = await ContractDraft.create(
        template_id=template_id,
        requester_hub_user_id=hub_user_id,
        customer_id=customer_id,
        items=items,
        rendered_file_storage_key=None,  # 第一版不存文件 bytes
        conversation_id=conversation_id,
    )

    # 4. 发钉钉
    sender = current_sender()
    binding = await ChannelUserBinding.filter(
        hub_user_id=hub_user_id,
        channel_type="dingtalk",
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

    draft.status = "sent"
    await draft.save(update_fields=["status"])

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
        logger.warning(
            "用户 hub_user_id=%s 调 generate_price_quote 但无 quote 模板 conv=%s",
            hub_user_id, conversation_id,
        )
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
        channel_type="dingtalk",
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
