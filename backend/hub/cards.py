"""钉钉文本卡片模板（OutboundMessageType.TEXT 多行格式化文本）。

Plan 4 全部用 TEXT，函数仍命名 xxx_card——"卡片"=语义性命名（=多行格式化文本 + 编号列表）。
"""
from __future__ import annotations

from hub.ports import OutboundMessage, OutboundMessageType


def multi_match_select_card(
    keyword: str, resource: str, items: list[dict],
) -> OutboundMessage:
    """模糊匹配多命中 → 让用户回复编号选择。

    items: [{"label": "阿里巴巴集团", "subtitle": "客户编号 12345", "ref": <内部 ref>}, ...]
    """
    lines = [f"找到多个匹配「{keyword}」的{resource}，请回复编号选择："]
    for i, it in enumerate(items, start=1):
        sub = f"（{it['subtitle']}）" if it.get("subtitle") else ""
        lines.append(f"{i}. {it['label']}{sub}")
    lines.append("\n（输入编号，例如：1）")
    return OutboundMessage(type=OutboundMessageType.TEXT, text="\n".join(lines))


def product_simple_card(product: dict, retail_price: str) -> OutboundMessage:
    """无客户场景：商品基本信息 + 系统零售价。"""
    text = (
        f"📦 {product['name']}\n"
        f"SKU：{product.get('sku', '-')}\n"
        f"系统零售价：¥{retail_price}\n"
    )
    if product.get("stock") is not None:
        text += f"当前库存：{product['stock']}\n"
    return OutboundMessage(type=OutboundMessageType.TEXT, text=text)


def product_with_customer_history_card(
    product: dict, customer: dict, history: list[dict], retail_price: str,
) -> OutboundMessage:
    """带客户场景：商品 + 客户最近 N 次成交价。"""
    lines = [
        f"📦 {product['name']}（SKU {product.get('sku', '-')}）",
        f"🏢 客户：{customer['name']}",
        f"系统零售价：¥{retail_price}",
        "",
    ]
    if not history:
        lines.append("该客户暂无该商品的历史成交价")
    else:
        lines.append(f"最近 {len(history)} 次成交价：")
        for rec in history:
            lines.append(
                f"• ¥{rec['unit_price']} · {rec.get('order_date', '')[:10]} "
                f"· 单号 {rec.get('order_no', '-')}"
            )
    return OutboundMessage(type=OutboundMessageType.TEXT, text="\n".join(lines))


def low_confidence_confirm_card(parsed_summary: str) -> OutboundMessage:
    """AI 解析低置信度 → 让用户确认或重新表达。"""
    return OutboundMessage(
        type=OutboundMessageType.TEXT,
        text=(
            f"我大概理解为：{parsed_summary}\n\n"
            "如果是这个意思请回复「是」继续，否则请用更明确的方式重新描述。"
        ),
    )
