"""Read tools — 包装现有 erp_tools / analyze_tools 函数,让 LLM 调。

所有 tool 通过 invoke_business_tool helper 调底层（自动 require_permissions +
log_tool_call + 注入 acting_as_user_id）。
"""
from __future__ import annotations
from langchain_core.tools import tool

from hub.agent.tools import erp_tools, analyze_tools
from hub.agent.react.tools._invoke import invoke_business_tool

# 模块级 import — 让测试 monkeypatch 能命中 read.* 路径
from hub.agent.react.context import tool_ctx
from hub.agent.tools.erp_tools import current_erp_adapter
from hub.error_codes import BizError
from hub.models.contract import ContractDraft
from hub.permissions import require_permissions
from hub.observability.tool_logger import log_tool_call


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
    """搜订单（按客户 + 最近 N 天）+ **自动聚合销售/毛利汇总**。
    customer_id=0 表示不过滤,看全部用户的订单。

    返 {items, total, summary}。
    **summary 字段直接给出聚合数字,问销售额/毛利/净额时直接读 summary 不要自己 sum items**:
      - summary.total_sales: 销售总额（不含退货）
      - summary.total_returns: 退货总额（绝对值）
      - summary.net_amount: 净额 = total_sales - total_returns
      - summary.total_cost: 成本汇总
      - summary.total_profit: 毛利汇总
      - summary.gross_margin_pct: 毛利率(%)
      - summary.order_count / sales_count / return_count: 订单数

    用户问"最近订单怎样" / "翼蓝最近买啥" / "上个月销售额毛利多少" 等时调。
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


async def _query_recent_contract_drafts(
    conversation_id: str, hub_user_id: int, limit: int,
) -> list[dict]:
    """查 ContractDraft sent 的最近 limit 条（**仅 sales 类型**） — 抽成 helper 便于 mock。

    **关键过滤**：ContractDraft 表被合同 + 报价单共用（generate_price_quote 也写 ContractDraft）,
    本 tool 只为"复用上份合同"场景服务,所以必须按 ContractTemplate.template_type='sales'
    过滤,防止用户做过报价后 LLM 误把报价当合同复用。
    """
    from hub.models.contract import ContractTemplate
    sales_template_ids = await ContractTemplate.filter(
        template_type="sales",
    ).values_list("id", flat=True)
    if not sales_template_ids:
        return []  # 无任何 sales 模板,自然没合同草稿
    drafts = await (
        ContractDraft.filter(
            conversation_id=conversation_id,
            requester_hub_user_id=hub_user_id,
            status="sent",
            template_id__in=list(sales_template_ids),
        )
        .order_by("-created_at")
        .limit(limit)
    )
    return [
        {
            "id": d.id,
            "customer_id": d.customer_id,
            "items": d.items or [],
            "extras": d.extras or {},
            "status": d.status,
            "created_at": str(d.created_at),
        }
        for d in drafts
    ]


async def _get_erp_customer_name(customer_id: int, acting_as_user_id: int) -> str:
    """从 ERP 拿客户 name（drafts 表只存 id）。"""
    adapter = current_erp_adapter()
    try:
        detail = await adapter.get_customer(
            customer_id=customer_id, acting_as_user_id=acting_as_user_id,
        )
        return detail.get("name", f"<id={customer_id}>")
    except Exception:
        return f"<id={customer_id}>"


@tool
async def get_recent_drafts(limit: int = 5) -> list[dict]:
    """**关键 tool：当前会话最近的合同草稿(contract only),让 LLM 处理"同样/上次/复用"等表达。**

    返回最近 limit 条 contract 草稿（按 created_at desc 排序),每条含 customer_name /
    items / shipping / payment_terms / tax_rate / created_at。

    使用时机：用户消息提到"和上份一样" / "前面那份给 X 也来一份" / "复制上次合同"
    等表达,先调本 tool 拿 items 和 shipping,再调 search_customer 找新客户,然后
    调 create_contract_draft 提交。

    返 [] 表示当前会话没有合同历史。范围限定为 contract（YAGNI）。
    """
    c = tool_ctx.get()
    if c is None:
        raise RuntimeError("tool_ctx 未 set")

    # BizError 必须转 dict（不能 raise,详见 _invoke.py "关键设计"注释）
    try:
        await require_permissions(c["hub_user_id"], ["usecase.query_recent_drafts.use"])
    except BizError as e:
        # 本 tool 返 list[dict],error 也包成单元素 list 让 LLM 看到
        return [{"error": f"权限不足: {e}"}]

    async with log_tool_call(
        conversation_id=c["conversation_id"],
        hub_user_id=c["hub_user_id"],
        round_idx=0,
        tool_name="get_recent_drafts",
        args={"limit": limit},
    ) as log_ctx:
        raw = await _query_recent_contract_drafts(
            c["conversation_id"],
            c["hub_user_id"],
            limit,
        )

        acting_as = c.get("acting_as") or c["hub_user_id"]
        out: list[dict] = []
        for d in raw:
            cust_name = await _get_erp_customer_name(d["customer_id"], acting_as)
            ext = d.get("extras") or {}
            out.append({
                "draft_id": d["id"],
                "customer_id": d["customer_id"],
                "customer_name": cust_name,
                "items": d.get("items") or [],
                "shipping": {
                    "address": ext.get("shipping_address") or "",
                    "contact": ext.get("shipping_contact") or "",
                    "phone": ext.get("shipping_phone") or "",
                },
                "payment_terms": ext.get("payment_terms") or "",
                "tax_rate": ext.get("tax_rate") or "",
                "created_at": d.get("created_at") or "",
            })
        log_ctx.set_result(out)
        return out
