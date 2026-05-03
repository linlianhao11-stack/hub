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
