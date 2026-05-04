# hub/agent/tools/erp_tools.py
"""Plan 6 Task 3：ERP 读 tool（9 个）+ ToolRegistry register_all 入口。

依赖：
- `set_erp_adapter(adapter)` 必须在 app startup 时调（main.py lifespan）
- `register_all(registry)` 在 ChainAgent 初始化时调一次

Plan 6 v9 Task 2.3：9 个读 tool strict schema + sentinel 归一化（spec §1.3 / §5.2）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hub.adapters.downstream.erp4 import Erp4Adapter
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools.types import ToolType

_erp_adapter: Erp4Adapter | None = None


def set_erp_adapter(adapter: Erp4Adapter | None) -> None:
    """app startup 时挂 adapter；shutdown 时建议传 None 防 stale 引用。

    用法（Task 6 集成时在 main.py lifespan 实施）：
        # startup
        adapter = Erp4Adapter(...)
        set_erp_adapter(adapter)
        ...
        # shutdown
        set_erp_adapter(None)
    """
    global _erp_adapter
    _erp_adapter = adapter


def current_erp_adapter() -> Erp4Adapter:
    """tool 内部访问当前 adapter；未挂时显式抛错。"""
    if _erp_adapter is None:
        raise RuntimeError("ERP adapter 未初始化（startup 必须先调 set_erp_adapter）")
    return _erp_adapter


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

# 所有读 tool schema 的聚合列表（供测试 / Task 2.5 导入）
ALL_READ_SCHEMAS: list[dict] = [
    SEARCH_PRODUCTS_SCHEMA,
    SEARCH_CUSTOMERS_SCHEMA,
    GET_PRODUCT_DETAIL_SCHEMA,
    GET_CUSTOMER_HISTORY_SCHEMA,
    CHECK_INVENTORY_SCHEMA,
    SEARCH_ORDERS_SCHEMA,
    GET_ORDER_DETAIL_SCHEMA,
    GET_CUSTOMER_BALANCE_SCHEMA,
    GET_INVENTORY_AGING_SCHEMA,
]


# === 9 个读 tool ===

async def search_products(query: str, *, acting_as_user_id: int) -> dict:
    """搜索商品（中英自动分词）。

    Args:
        query: 关键字
    """
    # sentinel 归一化（spec §1.3 v3.4）：query 为必填，不归一化
    return await current_erp_adapter().search_products(
        query=query, acting_as_user_id=acting_as_user_id,
    )


async def search_customers(query: str, *, acting_as_user_id: int) -> dict:
    """搜索客户（中英自动分词）。

    Args:
        query: 关键字
    """
    # sentinel 归一化（spec §1.3 v3.4）：query 为必填，不归一化
    return await current_erp_adapter().search_customers(
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


async def get_customer_balance(customer_id: int, *, acting_as_user_id: int) -> dict:
    """客户余额（应收 / 已付 / 未付）。

    Args:
        customer_id: 客户 ID
    """
    return await current_erp_adapter().get_customer_balance(
        customer_id=customer_id, acting_as_user_id=acting_as_user_id,
    )


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


# === register 入口 ===

def register_all(registry: ToolRegistry) -> None:
    """把 9 个 ERP 读 tool 全部注册到 registry。

    双轨注册策略（Plan 6 v9 Task 2.3）：
    1. 旧式函数注册（_tools 表）：供 registry.call() 权限校验 + 实际调用
    2. dict-schema 注册（_schema_registry 表）：供 subgraph 过滤 / Task 2.5 enforce_strict

    perm 命名约定：`usecase.<verb>.<resource>`；READ 类不需声明 confirmation_action_id。
    聚合 tool（analyze_top_customers / analyze_slow_moving_products）在 Task 9 注册。

    ⚠️ Cross-task 依赖：本函数引用的 9 个 perm 码（usecase.query_orders.use 等）
       由 Task 17 (`backend/hub/seed.py`) 写入 seed 数据。Task 17 完成前生产环境
       has_permission 会全返 False 导致 LLM 看不到这些 tool。集成顺序：
       Task 17 (seed) → Task 6 (ChainAgent 调 register_all) → 跑通。
    """
    # ── 路径 1：旧式函数注册（供 registry.call() 走权限校验 + fn 调用）──
    registry.register(
        "search_products", search_products,
        perm="usecase.query_product.use",
        tool_type=ToolType.READ,
        description="按关键字搜索商品（中英自动分词）",
    )
    registry.register(
        "search_customers", search_customers,
        perm="usecase.query_customer.use",
        tool_type=ToolType.READ,
        description="按关键字搜索客户",
    )
    registry.register(
        "get_product_detail", get_product_detail,
        perm="usecase.query_product.use",
        tool_type=ToolType.READ,
        description="商品详情（含库存明细）",
    )
    registry.register(
        "get_customer_history", get_customer_history,
        perm="usecase.query_customer_history.use",
        tool_type=ToolType.READ,
        description="客户最近 N 次该商品成交价",
    )
    registry.register(
        "check_inventory", check_inventory,
        perm="usecase.query_inventory.use",
        tool_type=ToolType.READ,
        description="商品库存简查",
    )
    registry.register(
        "search_orders", search_orders,
        perm="usecase.query_orders.use",
        tool_type=ToolType.READ,
        description="按条件搜订单",
    )
    registry.register(
        "get_order_detail", get_order_detail,
        perm="usecase.query_orders.use",
        tool_type=ToolType.READ,
        description="订单详情",
    )
    registry.register(
        "get_customer_balance", get_customer_balance,
        perm="usecase.query_customer_balance.use",
        tool_type=ToolType.READ,
        description="客户余额（应收/已付/未付）",
    )
    registry.register(
        "get_inventory_aging", get_inventory_aging,
        perm="usecase.query_inventory_aging.use",
        tool_type=ToolType.READ,
        description="库龄超 N 天的滞销商品",
    )

    # ── 路径 2：dict-schema 注册（供 subgraph 过滤 + Task 2.5 enforce_strict）──
    for schema in ALL_READ_SCHEMAS:
        registry.register(schema)
