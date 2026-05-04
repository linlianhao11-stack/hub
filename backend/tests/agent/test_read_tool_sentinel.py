"""Plan 6 v9 Task 2.3 — 读 tool sentinel 归一化。spec §1.3 v3.4。

验证：handler 入口 x = x or None（空字符串/0 → None/DEFAULT_PERIOD）。
覆盖 3 个代表性 tool：
  - search_orders（int 可选字段：customer_id=0 → None）
  - analyze_top_customers（str 可选字段：period="" → DEFAULT_PERIOD）
  - search_products / search_customers（必填，无 sentinel 需要验证，仅 schema 验证）
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hub.agent.tools import erp_tools, analyze_tools as at_module
from hub.agent.tools.erp_tools import search_orders, set_erp_adapter
from hub.agent.tools.analyze_tools import analyze_top_customers, DEFAULT_PERIOD


# ============================================================
# search_orders: customer_id=0 → None（不发给 ERP 作 ID 过滤）
# ============================================================

@pytest.mark.asyncio
async def test_search_orders_sentinel_customer_id_zero_to_none():
    """customer_id=0 必须归一化成 None — spec §1.3 v3.4。

    strict schema 中 customer_id 为 required int，LLM 传 0 表示"不指定客户"。
    handler 应将 0 归一化成 None，不发给 ERP 作 customer_id=0 的精确过滤条件。
    """
    captured: dict = {}

    async def fake_search_orders(*, customer_id, since, acting_as_user_id, **kw):
        captured["customer_id"] = customer_id
        return {"items": [], "total": 0}

    mock_erp = AsyncMock()
    mock_erp.search_orders = AsyncMock(side_effect=fake_search_orders)
    set_erp_adapter(mock_erp)

    try:
        await search_orders(
            customer_id=0,       # sentinel：0 表示"不限客户"
            since_days=30,
            acting_as_user_id=1,
        )
        assert captured["customer_id"] is None, (
            f"customer_id=0 应归一化成 None，实际传给 ERP: {captured['customer_id']!r}"
        )
    finally:
        set_erp_adapter(None)


@pytest.mark.asyncio
async def test_search_orders_sentinel_customer_id_none_passthrough():
    """customer_id=None（Python 原生 None）直接传 None 给 ERP。"""
    captured: dict = {}

    async def fake_search_orders(*, customer_id, since, acting_as_user_id, **kw):
        captured["customer_id"] = customer_id
        return {"items": [], "total": 0}

    mock_erp = AsyncMock()
    mock_erp.search_orders = AsyncMock(side_effect=fake_search_orders)
    set_erp_adapter(mock_erp)

    try:
        await search_orders(
            customer_id=None,
            since_days=7,
            acting_as_user_id=1,
        )
        assert captured["customer_id"] is None
    finally:
        set_erp_adapter(None)


@pytest.mark.asyncio
async def test_search_orders_sentinel_valid_customer_id_unchanged():
    """customer_id=42 正常值 → 原样传给 ERP，不归一化。"""
    captured: dict = {}

    async def fake_search_orders(*, customer_id, since, acting_as_user_id, **kw):
        captured["customer_id"] = customer_id
        return {"items": [], "total": 0}

    mock_erp = AsyncMock()
    mock_erp.search_orders = AsyncMock(side_effect=fake_search_orders)
    set_erp_adapter(mock_erp)

    try:
        await search_orders(
            customer_id=42,
            since_days=14,
            acting_as_user_id=1,
        )
        assert captured["customer_id"] == 42, (
            f"合法 customer_id=42 不应被归一化，实际: {captured['customer_id']!r}"
        )
    finally:
        set_erp_adapter(None)


# ============================================================
# analyze_top_customers: period="" → DEFAULT_PERIOD
# ============================================================

@pytest.mark.asyncio
async def test_analyze_top_customers_sentinel_empty_period_to_default():
    """period='' 必须归一化成 DEFAULT_PERIOD — spec §1.3 v3.4。

    strict schema 中 period 为 required str，LLM 可能传空字符串表示"用默认"。
    handler 应将 '' 归一化成 DEFAULT_PERIOD（'last_month'），
    避免 _parse_period_days('') 返回 30 这一隐式行为（语义更清晰）。
    """
    from hub.adapters.downstream.erp4 import Erp4Adapter

    mock_erp = AsyncMock(spec=Erp4Adapter)
    mock_erp.search_orders = AsyncMock(
        return_value={"items": [], "total": 0}
    )
    erp_tools.set_erp_adapter(mock_erp)

    try:
        result = await analyze_top_customers(
            period="",      # sentinel：空字符串应归一化成 DEFAULT_PERIOD
            top_n=5,
            acting_as_user_id=1,
        )
        # 正常完成（无异常）
        assert "items" in result
        assert isinstance(result["partial_result"], bool)
        # data_window 应包含实际使用的天数（DEFAULT_PERIOD=last_month → 30 天）
        assert "30" in result["data_window"], (
            f"period='' 应归一化成 'last_month'（30天），data_window={result['data_window']!r}"
        )
    finally:
        erp_tools.set_erp_adapter(None)


@pytest.mark.asyncio
async def test_analyze_top_customers_sentinel_valid_period_unchanged():
    """period='last_week' 正常值 → 使用 7 天窗口，不被归一化。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter

    mock_erp = AsyncMock(spec=Erp4Adapter)
    mock_erp.search_orders = AsyncMock(
        return_value={"items": [], "total": 0}
    )
    erp_tools.set_erp_adapter(mock_erp)

    try:
        result = await analyze_top_customers(
            period="last_week",
            top_n=3,
            acting_as_user_id=1,
        )
        assert "7" in result["data_window"], (
            f"period='last_week' 应用 7 天，data_window={result['data_window']!r}"
        )
    finally:
        erp_tools.set_erp_adapter(None)


# ============================================================
# Schema 结构验证：strict=True + additionalProperties=False + required 完整
# ============================================================

def test_erp_read_schemas_strict():
    """9 个 ERP 读 tool schema 均须 strict=True + additionalProperties=False。"""
    from hub.agent.tools.erp_tools import ALL_READ_SCHEMAS
    for schema in ALL_READ_SCHEMAS:
        fn = schema["function"]
        name = fn["name"]
        assert fn.get("strict") is True, f"{name}: strict 必须为 True"
        params = fn["parameters"]
        assert params.get("additionalProperties") is False, (
            f"{name}: additionalProperties 必须为 False"
        )
        # required 必须包含所有 properties（strict mode 规定）
        props = set(params.get("properties", {}).keys())
        required = set(params.get("required", []))
        assert props == required, (
            f"{name}: strict schema 要求所有 properties 均在 required 中，"
            f"差集={props - required}"
        )


def test_analyze_schemas_strict():
    """2 个分析 tool schema 均须 strict=True + additionalProperties=False。"""
    from hub.agent.tools.analyze_tools import ALL_ANALYZE_SCHEMAS
    for schema in ALL_ANALYZE_SCHEMAS:
        fn = schema["function"]
        name = fn["name"]
        assert fn.get("strict") is True, f"{name}: strict 必须为 True"
        params = fn["parameters"]
        assert params.get("additionalProperties") is False, (
            f"{name}: additionalProperties 必须为 False"
        )
        props = set(params.get("properties", {}).keys())
        required = set(params.get("required", []))
        assert props == required, (
            f"{name}: strict schema 要求所有 properties 均在 required 中，"
            f"差集={props - required}"
        )


def test_erp_read_schemas_subgraphs():
    """所有 ERP 读 tool schema 均须有 _subgraphs 字段且包含 'query'。"""
    from hub.agent.tools.erp_tools import ALL_READ_SCHEMAS
    for schema in ALL_READ_SCHEMAS:
        name = schema["function"]["name"]
        subgraphs = schema.get("_subgraphs", [])
        assert isinstance(subgraphs, list) and len(subgraphs) > 0, (
            f"{name}: _subgraphs 不能为空"
        )
        assert "query" in subgraphs, (
            f"{name}: 所有读 tool 必须属于 query subgraph"
        )


def test_analyze_schemas_subgraphs():
    """2 个分析 tool schema 均须有 _subgraphs=['query']。"""
    from hub.agent.tools.analyze_tools import ALL_ANALYZE_SCHEMAS
    for schema in ALL_ANALYZE_SCHEMAS:
        name = schema["function"]["name"]
        subgraphs = schema.get("_subgraphs", [])
        assert "query" in subgraphs, f"{name}: 分析 tool 必须属于 query subgraph"


def test_subgraph_counts_match_task25_expectations():
    """Task 2.5 预期 subgraph 数量验证（spec §5.1 plan 核对）。

    query: 11 read tools
    contract: search_customers, search_products = 2 (read) + generate_contract_draft(写) = 3 → 本任务 2 个
    quote: search_customers, search_products = 2 (read) + generate_price_quote(写) = 3 → 本任务 2 个
    voucher: search_orders, get_order_detail = 2 (read) + create_voucher_draft(写) = 3 → 本任务 2 个
    adjust_price: search_customers, search_products = 2 (read) + create_price_adjustment_request(写) = 3 → 本任务 2 个
    adjust_stock: search_products, check_inventory = 2 (read) + create_stock_adjustment_request(写) = 3 → 本任务 2 个
    """
    from hub.agent.tools.erp_tools import ALL_READ_SCHEMAS
    from hub.agent.tools.analyze_tools import ALL_ANALYZE_SCHEMAS

    all_schemas = ALL_READ_SCHEMAS + ALL_ANALYZE_SCHEMAS

    def count_subgraph(sg: str) -> int:
        return sum(1 for s in all_schemas if sg in s.get("_subgraphs", []))

    assert count_subgraph("query") == 11, (
        f"query subgraph 应有 11 个 read tool，实际: {count_subgraph('query')}"
    )
    # contract 读部分
    assert count_subgraph("contract") == 2, (
        f"contract subgraph 读 tool 应为 2（search_customers + search_products），"
        f"实际: {count_subgraph('contract')}"
    )
    # quote 读部分
    assert count_subgraph("quote") == 2, (
        f"quote subgraph 读 tool 应为 2（search_customers + search_products），"
        f"实际: {count_subgraph('quote')}"
    )
    # voucher 读部分
    assert count_subgraph("voucher") == 2, (
        f"voucher subgraph 读 tool 应为 2（search_orders + get_order_detail），"
        f"实际: {count_subgraph('voucher')}"
    )
    # adjust_price 读部分
    assert count_subgraph("adjust_price") == 2, (
        f"adjust_price subgraph 读 tool 应为 2（search_customers + search_products），"
        f"实际: {count_subgraph('adjust_price')}"
    )
    # adjust_stock 读部分
    assert count_subgraph("adjust_stock") == 2, (
        f"adjust_stock subgraph 读 tool 应为 2（search_products + check_inventory），"
        f"实际: {count_subgraph('adjust_stock')}"
    )
