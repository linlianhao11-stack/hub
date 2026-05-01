# hub/agent/tools/analyze_tools.py
"""Plan 6 Task 9：聚合分析 tool（高级能力）。

设计要点：
- 内部组合多个读 tool（多次 erp.search_orders 拉数据 + HUB 端聚合）
- **不直连 ERP DB**（坚持 HUB → ERP HTTP 边界）
- Bounded pagination：MAX_ORDERS=1000 / MAX_PERIOD_DAYS=90 / PER_PAGE=200
- 数据超上限或 period 超上限时返 partial_result=True + notes 字段说明
- LLM 看到 partial_result=True 必须在最终回复中告知用户"数据不完整"
  （system prompt 行为准则已强制；spec §3.5 提）

长期改进：ERP 加 analytics endpoint 直接 GROUP BY 出聚合结果（spec §14 备注，不在 Plan 6 范围）。
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from hub.agent.tools.erp_tools import current_erp_adapter
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools.types import ToolType

MAX_ORDERS = 1000
MAX_PERIOD_DAYS = 90
PER_PAGE = 200
DEFAULT_PERIOD = "last_month"


def _parse_period_days(period: str | None) -> int:
    """解析自然语言周期为天数。

    支持：
    - "last_week" / "last_7_days" / "近一周" / "近 7 天" → 7
    - "last_month" / "last_30_days" / "近一月" / "近 30 天" → 30
    - "last_quarter" / "last_90_days" / "近三月" / "近季度" → 90
    - "last_year" / "近一年" / "今年" → 365（会被 caller 截断到 MAX_PERIOD_DAYS）
    - "近 N 天" / "last N days" / "Nd" → N
    - 默认（None / 未识别）→ 30 天

    注意：时间词被粗略映射为 7/30/90/365；
    "去年"和"今年"都返 365（用 MAX_PERIOD_DAYS 截断兜底）。
    最终调用方都受 MAX_PERIOD_DAYS=90 截断；超 90 天的 period 触发
    partial_result=True + notes 提示。

    返回原始天数（不在这里截断；caller 决定）。
    """
    if not period:
        return 30
    s = period.strip().lower()
    # 数字 N 天
    m = re.search(r"(\d+)\s*(d|天|day|days)", s)
    if m:
        return int(m.group(1))
    # 关键字
    if any(w in s for w in ("week", "一周", "7天", "7 天")):
        return 7
    if any(w in s for w in ("month", "一月", "本月", "30天", "30 天")):
        return 30
    if any(w in s for w in ("quarter", "三月", "季度", "90天", "90 天")):
        return 90
    if any(w in s for w in ("year", "一年", "今年", "去年", "365")):
        return 365
    return 30  # fallback


# ===== analyze_top_customers =====

async def analyze_top_customers(
    period: str = DEFAULT_PERIOD,
    top_n: int = 10,
    *,
    acting_as_user_id: int,
) -> dict:
    """近 N 天客户销售排行（bounded）。

    Args:
        period: 时间窗口（"last_week" / "last_month" / "近一月" / "近 30 天" 等）
        top_n: 返回前 N 名（默认 10，最大 100）

    Returns:
        {
          "items": [{"customer_id", "total", "order_count", "avg_order"}, ...],
          "partial_result": bool,
          "data_window": "近 N 天，M 单",
          "notes": str | None,
        }
    """
    top_n = max(1, min(top_n, 100))
    raw_days = _parse_period_days(period)
    days = min(raw_days, MAX_PERIOD_DAYS)
    partial_period = raw_days > MAX_PERIOD_DAYS

    erp = current_erp_adapter()
    since = datetime.now(UTC) - timedelta(days=days)
    orders: list[dict] = []
    truncated = False
    page = 1
    resp: dict = {}

    while len(orders) < MAX_ORDERS:
        resp = await erp.search_orders(
            since=since, page=page, page_size=PER_PAGE,
            acting_as_user_id=acting_as_user_id,
        )
        items = resp.get("items") or []
        orders.extend(items)
        if len(items) < PER_PAGE:
            break
        page += 1
        # 防御：若 ERP 异常返回大于 PER_PAGE 也退出
        if page > (MAX_ORDERS // PER_PAGE) + 2:
            break

    hit_cap = len(orders) >= MAX_ORDERS
    if hit_cap:
        # 还可能有更多 → 标记 truncated
        # ERP 缺 total 字段时保守标 truncated（防漏报）
        last_total = resp.get("total") if isinstance(resp, dict) else None
        truncated = last_total is None or last_total > MAX_ORDERS
        orders = orders[:MAX_ORDERS]

    # 聚合 group by customer
    customer_totals: dict[int, dict] = {}
    for o in orders:
        cid = o.get("customer_id")
        if not isinstance(cid, int):
            continue
        try:
            total_amount = float(o.get("total") or o.get("amount") or 0)
        except (TypeError, ValueError):
            total_amount = 0.0
        if cid not in customer_totals:
            customer_totals[cid] = {
                "customer_id": cid,
                "customer_name": o.get("customer_name") or f"客户{cid}",
                "total": 0.0,
                "order_count": 0,
            }
        customer_totals[cid]["total"] += total_amount
        customer_totals[cid]["order_count"] += 1

    aggregated = sorted(
        customer_totals.values(),
        key=lambda x: x["total"],
        reverse=True,
    )[:top_n]
    for item in aggregated:
        if item["order_count"] > 0:
            item["avg_order"] = round(item["total"] / item["order_count"], 2)
        else:
            item["avg_order"] = 0.0

    notes_parts: list[str] = []
    if truncated:
        notes_parts.append(
            f"实际订单超 {MAX_ORDERS} 单，仅基于最近 {MAX_ORDERS} 单聚合"
        )
    if partial_period:
        notes_parts.append(
            f"请求 period 超 {MAX_PERIOD_DAYS} 天，已截断到最近 {MAX_PERIOD_DAYS} 天"
        )
    notes = "；".join(notes_parts) if notes_parts else None

    return {
        "items": aggregated,
        "partial_result": bool(truncated or partial_period),
        "data_window": f"近 {days} 天，{len(orders)} 单",
        "notes": notes,
    }


# ===== analyze_slow_moving_products =====

async def analyze_slow_moving_products(
    threshold_days: int = 90,
    top_n: int = 50,
    *,
    acting_as_user_id: int,
) -> dict:
    """库龄超 N 天的滞销商品。

    依赖 ERP `/api/v1/inventory/aging`（Task 18 ERP 端实现）。

    Args:
        threshold_days: 库龄阈值（天，默认 90）
        top_n: 最多返多少（默认 50）

    Returns:
        {
          "items": [{"product_id", "sku", "name", "total_stock", "age_days", "stock_value"}, ...],
          "partial_result": False,  # ERP /aging 端已聚合，HUB 不再二次裁
          "data_window": "...",
          "notes": str | None,
        }
    """
    threshold_days = max(1, threshold_days)
    top_n = max(1, min(top_n, 200))

    erp = current_erp_adapter()
    aging = await erp.get_inventory_aging(
        threshold_days=threshold_days,
        acting_as_user_id=acting_as_user_id,
    )

    items = aging.get("items") or []
    # ERP /aging 已经按阈值过滤；HUB 这层做防御 + 排序
    filtered = [
        p for p in items
        if isinstance(p, dict) and (p.get("age_days") or 0) >= threshold_days
    ]
    filtered.sort(
        key=lambda p: float(p.get("stock_value") or p.get("value") or 0),
        reverse=True,
    )
    items = filtered[:top_n]

    return {
        "items": items,
        "partial_result": False,
        "data_window": f"库龄阈值 {threshold_days} 天，命中 {len(filtered)} 项（取前 {len(items)}）",
        "notes": None,
    }


# ===== register =====

def register_all(registry: ToolRegistry) -> None:
    """2 个 READ 类聚合 tool 注册（不需 confirmation_action_id）。

    perm:
    - analyze_top_customers: usecase.analyze.use（与"客户销售分析"对应）
    - analyze_slow_moving_products: usecase.analyze.use
    """
    registry.register(
        "analyze_top_customers", analyze_top_customers,
        perm="usecase.analyze.use",
        tool_type=ToolType.READ,
        description="分析近 N 天客户销售排行（top N 客户 + 总额/订单数/均单）",
    )
    registry.register(
        "analyze_slow_moving_products", analyze_slow_moving_products,
        perm="usecase.analyze.use",
        tool_type=ToolType.READ,
        description="找库龄超 N 天的滞销商品（按库存价值倒序）",
    )
