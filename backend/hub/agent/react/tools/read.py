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
