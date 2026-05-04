# hub/agent/tools/erp_tools/__init__.py
"""Plan 6 Task 3：ERP 读 tool（9 个）+ ToolRegistry register_all 入口。

依赖：
- `set_erp_adapter(adapter)` 必须在 app startup 时调（main.py lifespan）
- `register_all(registry)` 在 ChainAgent 初始化时调一次

Plan 6 v9 Task 2.3：9 个读 tool strict schema + sentinel 归一化（spec §1.3 / §5.2）。
"""
from __future__ import annotations

from hub.agent.tools.erp_tools._adapter import (
    set_erp_adapter,
    current_erp_adapter,
)
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools.types import ToolType

# ── 子查询族 ──
from hub.agent.tools.erp_tools.product_queries import (
    SEARCH_PRODUCTS_SCHEMA,
    GET_PRODUCT_DETAIL_SCHEMA,
    CHECK_INVENTORY_SCHEMA,
    GET_INVENTORY_AGING_SCHEMA,
    search_products,
    get_product_detail,
    check_inventory,
    get_inventory_aging,
)
from hub.agent.tools.erp_tools.customer_queries import (
    SEARCH_CUSTOMERS_SCHEMA,
    GET_CUSTOMER_HISTORY_SCHEMA,
    GET_CUSTOMER_BALANCE_SCHEMA,
    search_customers,
    get_customer_history,
    get_customer_balance,
)
from hub.agent.tools.erp_tools.order_queries import (
    SEARCH_ORDERS_SCHEMA,
    GET_ORDER_DETAIL_SCHEMA,
    search_orders,
    get_order_detail,
)


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


# === __all__：确保 from hub.agent.tools.erp_tools import xxx 仍然可用 ===
__all__ = [
    "set_erp_adapter",
    "current_erp_adapter",
    "register_all",
    "ALL_READ_SCHEMAS",
    # schemas
    "SEARCH_PRODUCTS_SCHEMA",
    "SEARCH_CUSTOMERS_SCHEMA",
    "GET_PRODUCT_DETAIL_SCHEMA",
    "GET_CUSTOMER_HISTORY_SCHEMA",
    "CHECK_INVENTORY_SCHEMA",
    "SEARCH_ORDERS_SCHEMA",
    "GET_ORDER_DETAIL_SCHEMA",
    "GET_CUSTOMER_BALANCE_SCHEMA",
    "GET_INVENTORY_AGING_SCHEMA",
    # functions
    "search_products",
    "search_customers",
    "get_product_detail",
    "get_customer_history",
    "check_inventory",
    "search_orders",
    "get_order_detail",
    "get_customer_balance",
    "get_inventory_aging",
]


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
