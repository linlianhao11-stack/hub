# hub/agent/tools/erp_tools/product_queries.py
"""商品系查询 tool：search_products / get_product_detail / check_inventory / get_inventory_aging。"""
from __future__ import annotations

import hub.agent.tools.erp_tools as _pkg


def current_erp_adapter():
    return _pkg.current_erp_adapter()


# ===== Plan 6 v9 Task 2.3：strict tool schema（spec §1.3 / §5.2）=====

SEARCH_PRODUCTS_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "search_products",
        "strict": True,
        "description": "按关键字搜索商品（中英自动分词）",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键字（商品名/SKU/规格等）",
                },
            },
        },
    },
    "_subgraphs": ["query", "contract", "quote", "adjust_price", "adjust_stock"],
}

GET_PRODUCT_DETAIL_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "get_product_detail",
        "strict": True,
        "description": "商品详情（含库存明细）",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["product_id"],
            "properties": {
                "product_id": {
                    "type": "integer",
                    "description": "ERP 商品 ID",
                },
            },
        },
    },
    "_subgraphs": ["query"],
}

CHECK_INVENTORY_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "check_inventory",
        "strict": True,
        "description": "商品库存简查",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["product_id"],
            "properties": {
                "product_id": {
                    "type": "integer",
                    "description": "ERP 商品 ID",
                },
            },
        },
    },
    "_subgraphs": ["query", "adjust_stock"],
}

GET_INVENTORY_AGING_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "get_inventory_aging",
        "strict": True,
        "description": "库龄超 N 天的滞销商品",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["threshold_days"],
            "properties": {
                "threshold_days": {
                    "type": "integer",
                    "description": "库龄阈值（天，默认 90）；如无特别要求传 90",
                },
            },
        },
    },
    "_subgraphs": ["query"],
}


# === 4 个商品系读 tool ===

async def search_products(query: str, *, acting_as_user_id: int) -> dict:
    """搜索商品（中英自动分词）。

    Args:
        query: 关键字
    """
    # sentinel 归一化（spec §1.3 v3.4）：query 为必填，不归一化
    return await current_erp_adapter().search_products(
        query=query, acting_as_user_id=acting_as_user_id,
    )


async def get_product_detail(product_id: int, *, acting_as_user_id: int) -> dict:
    """商品详情（含库存明细）。

    Args:
        product_id: ERP 商品 ID
    """
    return await current_erp_adapter().get_product(
        product_id=product_id, acting_as_user_id=acting_as_user_id,
    )


async def check_inventory(product_id: int, *, acting_as_user_id: int) -> dict:
    """商品库存简查（封装 get_product_detail，只返库存字段）。

    Args:
        product_id: 商品 ID
    """
    detail = await current_erp_adapter().get_product(
        product_id=product_id, acting_as_user_id=acting_as_user_id,
    )
    return {
        "product_id": product_id,
        "total_stock": detail.get("total_stock", 0),
        "stocks": detail.get("stocks", []),
    }


async def get_inventory_aging(
    threshold_days: int = 90,
    *,
    acting_as_user_id: int,
) -> dict:
    """库龄超 N 天的滞销商品（⏳ Task 18 ERP endpoint 才完整）。

    Args:
        threshold_days: 库龄阈值（天，默认 90）
    """
    return await current_erp_adapter().get_inventory_aging(
        threshold_days=threshold_days, acting_as_user_id=acting_as_user_id,
    )
