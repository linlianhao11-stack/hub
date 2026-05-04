# hub/agent/tools/erp_tools/order_queries.py
"""订单系查询 tool：search_orders / get_order_detail。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import hub.agent.tools.erp_tools as _pkg


def current_erp_adapter():
    return _pkg.current_erp_adapter()


# ===== Plan 6 v9 Task 2.3：strict tool schema（spec §1.3 / §5.2）=====

SEARCH_ORDERS_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "search_orders",
        "strict": True,
        "description": "按条件搜订单",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["customer_id", "since_days"],
            "properties": {
                "customer_id": {
                    "type": "integer",
                    "description": "客户 ID（可选过滤）；如不限客户传 0（0 表示不过滤）",
                },
                "since_days": {
                    "type": "integer",
                    "description": "最近几天的订单（默认 30）；如无特别要求传 30",
                },
            },
        },
    },
    "_subgraphs": ["query", "voucher"],
}

GET_ORDER_DETAIL_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "get_order_detail",
        "strict": True,
        "description": "订单详情",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["order_id"],
            "properties": {
                "order_id": {
                    "type": "integer",
                    "description": "ERP 订单 ID",
                },
            },
        },
    },
    "_subgraphs": ["query", "voucher"],
}


# === 2 个订单系读 tool ===

async def search_orders(
    customer_id: int | None = None,
    since_days: int = 30,
    *,
    acting_as_user_id: int,
) -> dict:
    """搜订单（按客户 + 最近 N 天）+ 自动聚合销售/成本/毛利汇总。

    Args:
        customer_id: 客户 ID（可选，strict schema 用 0 表示"不过滤"）
        since_days: 最近几天的订单（默认 30）

    Returns:
        {
          "items": [...原始订单列表...],
          "total": int,
          "summary": {                    # **关键：聚合数据,LLM 不用自己 sum**
            "order_count": int,
            "sales_count": int,           # SALES 类型订单数
            "return_count": int,          # RETURN 退货订单数
            "total_sales": float,         # 销售总额（仅 SALES,不含退货）
            "total_returns": float,       # 退货总额（绝对值）
            "net_amount": float,          # 净额 = total_sales - total_returns
            "total_cost": float,          # 成本汇总
            "total_profit": float,        # 毛利汇总（仅 SALES;退货 profit 一般也是负）
            "gross_margin_pct": float,    # 毛利率(%) = total_profit / total_sales * 100
          }
        }
    """
    # sentinel 归一化（spec §1.3 v3.4）：strict schema 要求 customer_id 为 required，
    # LLM 传 0 表示"不指定客户" → 归一化成 None，不发给 ERP 查询作 ID 过滤条件
    customer_id = customer_id or None
    since = datetime.now(UTC) - timedelta(days=since_days)
    raw = await current_erp_adapter().search_orders(
        customer_id=customer_id, since=since,
        acting_as_user_id=acting_as_user_id,
    )
    items = raw.get("items") or []

    # 聚合销售 / 退货 / 毛利 — LLM 看 summary 就够了,不用自己挨个 sum
    sales_count = 0
    return_count = 0
    total_sales = 0.0
    total_returns = 0.0
    total_cost = 0.0
    total_profit = 0.0
    for it in items:
        amt = float(it.get("total_amount") or 0)
        cost = float(it.get("total_cost") or 0)
        profit = float(it.get("total_profit") or 0)
        otype = it.get("order_type") or ""
        if otype == "SALES" or amt > 0:
            sales_count += 1
            total_sales += amt
            total_cost += cost
            total_profit += profit
        elif otype == "RETURN" or amt < 0:
            return_count += 1
            total_returns += abs(amt)

    net_amount = total_sales - total_returns
    margin_pct = (total_profit / total_sales * 100) if total_sales > 0 else 0.0

    summary = {
        "order_count": len(items),
        "sales_count": sales_count,
        "return_count": return_count,
        # 金额保留 2 位小数,避免 LLM 看到 ...9999.999 之类的浮点 noise
        "total_sales": round(total_sales, 2),
        "total_returns": round(total_returns, 2),
        "net_amount": round(net_amount, 2),
        "total_cost": round(total_cost, 2),
        "total_profit": round(total_profit, 2),
        "gross_margin_pct": round(margin_pct, 2),
        "since_days": since_days,
    }
    # 不破坏原结构,只追加 summary
    return {**raw, "summary": summary}


async def get_order_detail(order_id: int, *, acting_as_user_id: int) -> dict:
    """订单详情。

    Args:
        order_id: 订单 ID
    """
    return await current_erp_adapter().get_order_detail(
        order_id=order_id, acting_as_user_id=acting_as_user_id,
    )
