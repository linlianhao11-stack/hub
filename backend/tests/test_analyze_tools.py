"""Plan 6 Task 9：聚合分析 tool 测试（≥6 case）。"""
import os
import pytest
from unittest.mock import AsyncMock
from datetime import datetime, UTC

from hub.agent.tools.analyze_tools import (
    analyze_top_customers, analyze_slow_moving_products,
    register_all, _parse_period_days,
    MAX_ORDERS, MAX_PERIOD_DAYS,
)
from hub.agent.tools import erp_tools


@pytest.fixture
def mock_erp():
    """注入 mock erp adapter；teardown 清空全局。"""
    m = AsyncMock()
    erp_tools.set_erp_adapter(m)
    yield m
    erp_tools.set_erp_adapter(None)


@pytest.fixture
async def redis_client():
    """真 redis 客户端（decode_responses=True 让 Lua 返回 str）。"""
    import redis.asyncio as redis_async
    url = os.environ.get("HUB_REDIS_URL", "redis://localhost:6380/0")
    client = redis_async.Redis.from_url(url, decode_responses=True)
    yield client
    # 清掉测试期间产生的 hub:agent:* keys 防测试间污染
    async for key in client.scan_iter("hub:agent:*"):
        await client.delete(key)
    await client.aclose()


# ===== _parse_period_days 单元测试 =====

def test_parse_period_days_keywords():
    assert _parse_period_days("last_week") == 7
    assert _parse_period_days("last_month") == 30
    assert _parse_period_days("last_quarter") == 90
    assert _parse_period_days("last_year") == 365


def test_parse_period_days_chinese():
    assert _parse_period_days("近一周") == 7
    assert _parse_period_days("近一月") == 30
    assert _parse_period_days("近三月") == 90
    assert _parse_period_days("今年") == 365


def test_parse_period_days_numeric():
    assert _parse_period_days("近 14 天") == 14
    assert _parse_period_days("last 60 days") == 60
    assert _parse_period_days("7d") == 7


def test_parse_period_days_default():
    assert _parse_period_days(None) == 30
    assert _parse_period_days("") == 30
    assert _parse_period_days("xyz_unknown") == 30


# ===== analyze_top_customers =====

async def test_analyze_top_customers_basic(mock_erp):
    """≤ 200 单 → 一页拉完，partial_result=False。"""
    mock_erp.search_orders.return_value = {
        "items": [
            {"customer_id": 1, "customer_name": "A", "total": 100.0},
            {"customer_id": 1, "customer_name": "A", "total": 200.0},
            {"customer_id": 2, "customer_name": "B", "total": 50.0},
        ],
        "total": 3,
    }
    result = await analyze_top_customers(
        period="last_month", top_n=5, acting_as_user_id=99,
    )
    assert result["partial_result"] is False
    assert result["notes"] is None
    assert len(result["items"]) == 2
    # A 总 300，B 总 50
    assert result["items"][0]["customer_id"] == 1
    assert result["items"][0]["total"] == 300.0
    assert result["items"][0]["order_count"] == 2
    assert result["items"][0]["avg_order"] == 150.0


async def test_analyze_top_customers_truncates_at_max_orders(mock_erp):
    """1500 单 → 拉到 1000 截断，partial_result=True。"""
    page_state = {"page": 0}

    async def fake_search(**kw):
        page_state["page"] += 1
        if page_state["page"] <= 7:  # 7 * 200 = 1400 > MAX_ORDERS=1000
            return {
                "items": [
                    {"customer_id": (i % 3) + 1, "customer_name": f"C{i % 3}",
                     "total": 100.0}
                    for i in range(200)
                ],
                "total": 1500,
            }
        return {"items": [], "total": 1500}

    mock_erp.search_orders = fake_search
    result = await analyze_top_customers(
        period="last_month", top_n=10, acting_as_user_id=99,
    )
    assert result["partial_result"] is True
    assert "1000" in (result["notes"] or "")


async def test_analyze_top_customers_period_truncated(mock_erp):
    """period=last_year（365 天）→ 截断到 90 天，partial_result=True。"""
    mock_erp.search_orders.return_value = {"items": [], "total": 0}
    result = await analyze_top_customers(
        period="last_year", top_n=5, acting_as_user_id=99,
    )
    assert result["partial_result"] is True
    assert "90" in (result["notes"] or "")
    # 验证传给 ERP 的 since 是 90 天前而非 365
    call_args = mock_erp.search_orders.call_args
    since = call_args.kwargs["since"]
    delta_days = (datetime.now(UTC) - since).total_seconds() / 86400
    assert delta_days == pytest.approx(90, abs=1)


async def test_top_customers_top_n_clamped_to_100(mock_erp):
    """v2 加固（review I2）：top_n=999 clamp 到 100；mock 150 客户验证真截断。"""
    mock_erp.search_orders.return_value = {
        "items": [
            {"customer_id": cid, "total": 100.0 - cid}  # 不同金额防排序歧义
            for cid in range(1, 151)  # 150 个客户
        ],
        "total": 150,
    }
    result = await analyze_top_customers(
        period="last_month", top_n=999, acting_as_user_id=99,
    )
    assert result["partial_result"] is False
    assert len(result["items"]) == 100  # 真 clamp 生效


# ===== analyze_slow_moving_products =====

async def test_analyze_slow_moving_products_basic(mock_erp):
    """正常返回 + 按 stock_value 倒序。"""
    mock_erp.get_inventory_aging.return_value = {
        "items": [
            {"product_id": 1, "sku": "P1", "name": "A",
             "age_days": 100, "stock_value": 5000.0},
            {"product_id": 2, "sku": "P2", "name": "B",
             "age_days": 120, "stock_value": 8000.0},
            {"product_id": 3, "sku": "P3", "name": "C",
             "age_days": 50, "stock_value": 1000.0},  # < threshold
        ],
    }
    result = await analyze_slow_moving_products(
        threshold_days=90, acting_as_user_id=99,
    )
    assert result["partial_result"] is False
    assert len(result["items"]) == 2  # C 被过滤
    assert result["items"][0]["product_id"] == 2  # B value 8000 排第一
    assert result["items"][1]["product_id"] == 1


async def test_analyze_slow_moving_products_top_n_limit(mock_erp):
    """top_n=10 → 仅返前 10。"""
    mock_erp.get_inventory_aging.return_value = {
        "items": [
            {"product_id": i, "age_days": 100, "stock_value": 1000.0 - i}
            for i in range(50)
        ],
    }
    result = await analyze_slow_moving_products(
        threshold_days=90, top_n=10, acting_as_user_id=99,
    )
    assert len(result["items"]) == 10


# ===== register_all =====

async def test_register_all_registers_2_analyze_tools(redis_client):
    """v2 加固：用真 ToolRegistry 验 register fail-fast 通过（READ 类不需 confirmation_action_id）。"""
    from hub.agent.tools.registry import ToolRegistry
    from hub.agent.tools.confirm_gate import ConfirmGate
    from hub.agent.tools.types import ToolType
    cg = ConfirmGate(redis_client)
    sm = AsyncMock()
    reg = ToolRegistry(confirm_gate=cg, session_memory=sm)
    register_all(reg)
    assert "analyze_top_customers" in reg._tools
    assert "analyze_slow_moving_products" in reg._tools
    # v2 加固（review M2）：验证 tool_type + perm
    assert reg._tools["analyze_top_customers"].tool_type == ToolType.READ
    assert reg._tools["analyze_top_customers"].perm == "usecase.analyze.use"
    assert reg._tools["analyze_slow_moving_products"].tool_type == ToolType.READ
    assert reg._tools["analyze_slow_moving_products"].perm == "usecase.analyze.use"


async def test_top_customers_truncated_when_erp_omits_total(mock_erp):
    """v2 加固（review I1）：ERP 缺 total 字段时仍保守标 truncated。"""
    page_state = {"page": 0}

    async def fake_search(**kw):
        page_state["page"] += 1
        if page_state["page"] <= 6:  # 6 * 200 = 1200 触 cap
            return {"items": [
                {"customer_id": (i % 3) + 1, "total": 100.0}
                for i in range(200)
            ]}  # 故意不返 total 字段
        return {"items": []}

    mock_erp.search_orders = fake_search
    result = await analyze_top_customers(
        period="last_month", top_n=5, acting_as_user_id=99,
    )
    assert result["partial_result"] is True
    assert "1000" in (result["notes"] or "")


async def test_top_customers_notes_concat_when_both_partial(mock_erp):
    """v2 加固（review I3）：truncated + partial_period 同时触发时 notes 拼接两条。"""
    page_state = {"page": 0}

    async def fake_search(**kw):
        page_state["page"] += 1
        if page_state["page"] <= 7:
            return {"items": [
                {"customer_id": (i % 3) + 1, "total": 100.0}
                for i in range(200)
            ], "total": 1500}
        return {"items": [], "total": 1500}

    mock_erp.search_orders = fake_search
    result = await analyze_top_customers(
        period="last_year",  # 365 天 → 截断到 90
        top_n=5, acting_as_user_id=99,
    )
    assert result["partial_result"] is True
    assert "1000" in result["notes"]
    assert "90" in result["notes"]
    assert "；" in result["notes"]  # 两条 notes 用中文分号拼接


async def test_top_customers_group_by_skips_invalid_customer_id(mock_erp):
    """v2 加固（review M3）：customer_id 缺失/非 int 跳过；total/amount 字段名 fallback。"""
    mock_erp.search_orders.return_value = {
        "items": [
            {"customer_id": 1, "total": 100.0},       # 正常 total
            {"customer_id": 2, "amount": 200.0},      # 用 amount 字段
            {"customer_id": "3", "total": 50.0},      # 非 int → 跳过
            {"customer_id": None, "total": 30.0},     # None → 跳过
            {"total": 999.0},                         # 缺 customer_id → 跳过
        ],
        "total": 5,
    }
    result = await analyze_top_customers(
        period="last_month", top_n=10, acting_as_user_id=99,
    )
    items = result["items"]
    assert len(items) == 2  # 仅 customer_id=1, customer_id=2
    cids = {item["customer_id"] for item in items}
    assert cids == {1, 2}
    # 验证 amount 字段 fallback 生效（customer_id=2 的 total 应为 200.0）
    item_2 = next(i for i in items if i["customer_id"] == 2)
    assert item_2["total"] == 200.0
