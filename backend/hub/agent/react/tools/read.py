"""Read tools — 包装现有 erp_tools / analyze_tools 函数,让 LLM 调。

所有 tool 通过 invoke_business_tool helper 调底层（自动 require_permissions +
log_tool_call + 注入 acting_as_user_id）。
"""
from __future__ import annotations
from langchain_core.tools import tool

from hub.agent.tools import erp_tools, analyze_tools
from hub.agent.react.tools._invoke import invoke_business_tool


@tool
async def search_customer(query: str) -> list[dict]:
    """按名称/电话搜客户。返回客户列表 [{id, name, phone, address, ...}]。
    用户提到客户时（"翼蓝" / "广州得帆" / "13800..."）调本 tool 搜。
    """
    result = await invoke_business_tool(
        tool_name="search_customers",
        perm="usecase.query_customer.use",
        args={"query": query},
        fn=erp_tools.search_customers,
    )
    if isinstance(result, dict):
        return result.get("items", [])
    return result or []


@tool
async def search_product(query: str) -> list[dict]:
    """按名称/SKU/品牌搜商品。返回 [{id, name, sku, brand, list_price, ...}]。
    用户提到商品时（"X1" / "F1 系列" / "MAT0130104"）调本 tool 搜。
    """
    result = await invoke_business_tool(
        tool_name="search_products",
        perm="usecase.query_product.use",
        args={"query": query},
        fn=erp_tools.search_products,
    )
    if isinstance(result, dict):
        return result.get("items", [])
    return result or []


@tool
async def get_product_detail(product_id: int) -> dict:
    """获取商品详情（含各仓库存明细 + 库龄）。需要展示完整商品规格时调。"""
    return await invoke_business_tool(
        tool_name="get_product_detail",
        perm="usecase.query_product.use",
        args={"product_id": product_id},
        fn=erp_tools.get_product_detail,
    )


@tool
async def check_inventory(product_id: int) -> dict:
    """单个商品库存查询(返 {product_id, total_stock, stocks: [...]})。
    需要看某品牌全库存的,先用 search_product(brand) 拿 product_id 列表,再逐个 check_inventory。
    """
    return await invoke_business_tool(
        tool_name="check_inventory",
        perm="usecase.query_inventory.use",
        args={"product_id": product_id},
        fn=erp_tools.check_inventory,
    )


@tool
async def get_customer_history(
    product_id: int, customer_id: int, limit: int = 5,
) -> dict:
    """客户最近 N 笔某商品成交（含数量 / 价格 / 日期,用于报价 / 谈判参考）。
    用户问"上次买这个什么价" / "翼蓝最近 X1 成交怎样" 等历史成交问题时调。
    """
    return await invoke_business_tool(
        tool_name="get_customer_history",
        perm="usecase.query_customer_history.use",
        args={"product_id": product_id, "customer_id": customer_id, "limit": limit},
        fn=erp_tools.get_customer_history,
    )


@tool
async def get_customer_balance(customer_id: int) -> dict:
    """客户欠款 / 余额 / 信用额度 / rebate 余额。
    用户问"还欠多少" / "信用够吗" / "余额怎样"时调。
    """
    return await invoke_business_tool(
        tool_name="get_customer_balance",
        perm="usecase.query_customer_balance.use",
        args={"customer_id": customer_id},
        fn=erp_tools.get_customer_balance,
    )


@tool
async def search_orders(customer_id: int = 0, since_days: int = 30) -> dict:
    """搜订单（按客户 + 最近 N 天）。customer_id=0 表示不过滤,看全部用户的订单。
    用户问"最近订单怎样" / "翼蓝最近买啥"时调。
    """
    return await invoke_business_tool(
        tool_name="search_orders",
        perm="usecase.query_orders.use",
        args={"customer_id": customer_id, "since_days": since_days},
        fn=erp_tools.search_orders,
    )


@tool
async def get_order_detail(order_id: int) -> dict:
    """订单详情（含每行商品 / 数量 / 价格）。"""
    return await invoke_business_tool(
        tool_name="get_order_detail",
        perm="usecase.query_orders.use",
        args={"order_id": order_id},
        fn=erp_tools.get_order_detail,
    )


@tool
async def analyze_top_customers(period: str = "近一月", top_n: int = 10) -> dict:
    """近 N 天客户销售排行。period 取 "近一周" / "近一月" / "近一季" / "近一年" 等中文表达。
    用户问"哪些大客户" / "本月销售排行"调。返 {items: [...], data_window: ...}。
    """
    return await invoke_business_tool(
        tool_name="analyze_top_customers",
        perm="usecase.analyze.use",
        args={"period": period, "top_n": top_n},
        fn=analyze_tools.analyze_top_customers,
    )
