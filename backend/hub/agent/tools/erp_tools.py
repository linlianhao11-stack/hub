# hub/agent/tools/erp_tools.py
"""Plan 6 Task 3：ERP 读 tool（9 个）+ ToolRegistry register_all 入口。

依赖：
- `set_erp_adapter(adapter)` 必须在 app startup 时调（main.py lifespan）
- `register_all(registry)` 在 ChainAgent 初始化时调一次
"""
from __future__ import annotations

from datetime import datetime, timedelta, UTC

from hub.adapters.downstream.erp4 import Erp4Adapter
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools.types import ToolType


_erp_adapter: Erp4Adapter | None = None


def set_erp_adapter(adapter: Erp4Adapter | None) -> None:
    """app startup 时挂 adapter；传 None 仅用于测试 cleanup。"""
    global _erp_adapter
    _erp_adapter = adapter


def current_erp_adapter() -> Erp4Adapter:
    """tool 内部访问当前 adapter；未挂时显式抛错。"""
    if _erp_adapter is None:
        raise RuntimeError("ERP adapter 未初始化（startup 必须先调 set_erp_adapter）")
    return _erp_adapter


# === 9 个读 tool ===

async def search_products(query: str, *, acting_as_user_id: int) -> dict:
    """搜索商品（中英自动分词）。

    Args:
        query: 关键字
    """
    return await current_erp_adapter().search_products(
        query=query, acting_as_user_id=acting_as_user_id,
    )


async def search_customers(query: str, *, acting_as_user_id: int) -> dict:
    """搜索客户（中英自动分词）。

    Args:
        query: 关键字
    """
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
    """搜订单（按客户 + 最近 N 天）。

    Args:
        customer_id: 客户 ID（可选）
        since_days: 最近几天的订单（默认 30）
    """
    since = datetime.now(UTC) - timedelta(days=since_days)
    return await current_erp_adapter().search_orders(
        customer_id=customer_id, since=since,
        acting_as_user_id=acting_as_user_id,
    )


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

    perm 命名约定：`usecase.<verb>.<resource>`；READ 类不需声明 confirmation_action_id。
    聚合 tool（analyze_top_customers / analyze_slow_moving_products）在 Task 9 注册。
    """
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
