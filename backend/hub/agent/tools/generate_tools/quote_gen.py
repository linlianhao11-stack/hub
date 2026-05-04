"""报价单 / Excel 导出 tool（docx 报价 + xlsx 表格 → 钉钉发文件）。"""
from __future__ import annotations

import logging

import hub.agent.tools.generate_tools as _gt  # noqa: E402
from hub.adapters.channel.dingtalk_sender import DingTalkSendError
from hub.agent.document.excel import ExcelExporter

logger = logging.getLogger("hub.agent.tools.generate_tools")


def _current_sender():
    return _gt.current_sender()

GENERATE_PRICE_QUOTE_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "generate_price_quote",
        "strict": True,
        "description": "生成客户报价单 docx 并发到钉钉",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "customer_id",
                "items",
                "extras",
            ],
            "properties": {
                "customer_id": {
                    "type": "integer",
                    "description": "ERP 客户 ID，必须用 search_customers 真返过的 id",
                },
                "items": {
                    "type": "array",
                    "description": "商品列表，每项含 product_id/name/qty/price；如无传 []",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["product_id", "qty", "price"],
                        "properties": {
                            "product_id": {
                                "type": "integer",
                                "description": "ERP 商品 ID（必须用 search_products 真返过的 id）",
                            },
                            "qty": {
                                "type": "number",
                                "description": "数量（必须大于 0）",
                            },
                            "price": {
                                "type": "number",
                                "description": "单价（必须大于 0）",
                            },
                        },
                    },
                },
                "extras": {
                    "type": "object",
                    "description": "模板自定义占位符（极少用），如无传 {}",
                    "additionalProperties": True,
                },
            },
        },
    },
    "_subgraphs": ["quote"],
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
    # sentinel 归一化（spec §1.3 v3.4）：LLM 传 {} 当 optional → 归一化成 {} 已 ok；extras={} 保持
    extras = extras or {}

    template = await _gt.ContractTemplate.filter(
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
    return await _gt.generate_contract_draft(
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

    sender = _current_sender()
    binding = await _gt.ChannelUserBinding.filter(
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
