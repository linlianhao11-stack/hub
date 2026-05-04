# hub/agent/tools/erp_tools/customer_queries.py
"""客户系查询 tool：search_customers / get_customer_history / get_customer_balance。"""
from __future__ import annotations

import hub.agent.tools.erp_tools as _pkg


def current_erp_adapter():
    return _pkg.current_erp_adapter()


# ===== Plan 6 v9 Task 2.3：strict tool schema（spec §1.3 / §5.2）=====

SEARCH_CUSTOMERS_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "search_customers",
        "strict": True,
        "description": "按关键字搜索客户",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键字（客户名/联系方式等）",
                },
            },
        },
    },
    "_subgraphs": ["query", "contract", "quote", "adjust_price"],
}

GET_CUSTOMER_HISTORY_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "get_customer_history",
        "strict": True,
        "description": "客户最近 N 次该商品成交价",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["product_id", "customer_id", "limit"],
            "properties": {
                "product_id": {
                    "type": "integer",
                    "description": "ERP 商品 ID",
                },
                "customer_id": {
                    "type": "integer",
                    "description": "ERP 客户 ID",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回最近多少笔（默认 5）；如无特别要求传 5",
                },
            },
        },
    },
    "_subgraphs": ["query"],
}

GET_CUSTOMER_BALANCE_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "get_customer_balance",
        "strict": True,
        "description": "客户余额（应收/已付/未付）",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["customer_id"],
            "properties": {
                "customer_id": {
                    "type": "integer",
                    "description": "ERP 客户 ID",
                },
            },
        },
    },
    "_subgraphs": ["query"],
}


# === 3 个客户系读 tool ===

async def search_customers(query: str, *, acting_as_user_id: int) -> dict:
    """搜索客户（中英自动分词）。

    Args:
        query: 关键字
    """
    # sentinel 归一化（spec §1.3 v3.4）：query 为必填，不归一化
    return await current_erp_adapter().search_customers(
        query=query, acting_as_user_id=acting_as_user_id,
    )


async def get_customer_history(
    product_id: int,
    customer_id: int,
    *,
    limit: int = 5,
    acting_as_user_id: int,
) -> dict:
    """客户最近 N 次该商品成交价（用于谈判参考）。

    Args:
        product_id: 商品 ID
        customer_id: 客户 ID
        limit: 返回最近多少笔（默认 5）
    """
    return await current_erp_adapter().get_product_customer_prices(
        product_id=product_id, customer_id=customer_id,
        limit=limit, acting_as_user_id=acting_as_user_id,
    )


async def get_customer_balance(customer_id: int, *, acting_as_user_id: int) -> dict:
    """客户余额（应收 / 已付 / 未付）。

    Args:
        customer_id: 客户 ID
    """
    return await current_erp_adapter().get_customer_balance(
        customer_id=customer_id, acting_as_user_id=acting_as_user_id,
    )
