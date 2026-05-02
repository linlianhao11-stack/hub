# backend/tests/agent/conftest.py
"""Plan 6 v9 Task 8.1 — 真 LLM 端到端 acceptance 测试 fixture。

build_real_graph_agent：
  - FakeRedis（不需要真 Redis 进程）
  - ConfirmGate（真实隔离逻辑）
  - 真 DeepSeekLLMClient（需要 DEEPSEEK_API_KEY）
  - StubToolExecutor（16 个工具的罐头数据，不挂 Postgres）
  - GraphAgent（完整主图）
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest
from fakeredis.aioredis import FakeRedis

from hub.agent.graph.agent import GraphAgent
from hub.agent.llm_client import DeepSeekLLMClient
from hub.agent.tools.confirm_gate import ConfirmGate
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools import register_all_tools


# ──────────────────────────────────────────────────────────────────────────────
# StubToolExecutor — 16 工具罐头数据（不挂 Postgres）
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class StubToolExecutor:
    """Stub tool executor — 返回预设数据，记录所有调用。

    生产路径会真正访问 ERP DB；Phase 8 acceptance 测试只需要
    可预测的假数据来驱动 LLM 跑完整个 subgraph 流程。
    """
    log: list[tuple[str, dict]] = field(default_factory=list)

    async def __call__(self, name: str, args: dict):
        self.log.append((name, args))

        # ── 读工具（11 个）─────────────────────────────────────────────
        if name == "search_customers":
            q = (args.get("query") or "").strip()
            if "阿里" in q:
                return [{"id": 10, "name": "阿里", "address": None,
                         "tax_id": None, "phone": None}]
            if "翼蓝" in q:
                return [{"id": 20, "name": "翼蓝", "address": "广州市天河区",
                         "tax_id": None, "phone": None}]
            if "百度" in q:
                return [{"id": 30, "name": "百度", "address": None,
                         "tax_id": None, "phone": None}]
            return []

        if name == "search_products":
            q = (args.get("query") or "")
            # 针对合同/报价常用名返合理默认；其余 hash 生成 ID
            _KNOWN = {
                "X1": {"id": 101, "name": "X1", "sku": "SKG-X1", "list_price": 300},
                "H5": {"id": 102, "name": "H5", "sku": "SKG-H5", "list_price": 300},
                "F1": {"id": 103, "name": "F1", "sku": "SKG-F1", "list_price": 500},
                "K5": {"id": 104, "name": "K5", "sku": "SKG-K5", "list_price": 300},
                "SKG": {"id": 100, "name": "SKG 通用", "sku": "SKG-GEN", "list_price": 300},
            }
            # 精确匹配或包含检查
            for key, val in _KNOWN.items():
                if key in q:
                    return [val]
            prod_id = abs(hash(q)) % 1000 + 200
            return [{"id": prod_id, "name": q, "sku": q.upper(), "list_price": 300}]

        if name == "search_orders":
            return []

        if name == "get_order_detail":
            return {
                "order_id": args.get("order_id"),
                "status": "approved",
                "outbound_voucher_count": 0,
                "inbound_voucher_count": 0,
                "items": [{"product_id": 1, "qty": 10}],
            }

        if name == "check_inventory":
            q = (args.get("query") or args.get("product_id") or "SKG")
            return [
                {"sku": "SKG-X1", "name": "X1", "qty": 100},
                {"sku": "SKG-H5", "name": "H5", "qty": 50},
                {"sku": "SKG-F1", "name": "F1", "qty": 30},
                {"sku": "SKG-K5", "name": "K5", "qty": 80},
            ]

        if name == "get_customer_balance":
            return {
                "customer_id": args.get("customer_id"),
                "balance": 12345.67,
            }

        if name == "get_customer_history":
            return [
                {"date": "2024-01-01", "product": "X1", "price": 290.0, "qty": 100},
                {"date": "2024-02-01", "product": "X1", "price": 295.0, "qty": 50},
            ]

        if name == "get_product_detail":
            return {
                "id": args.get("product_id"),
                "name": "X1",
                "list_price": 300,
                "sku": "SKG-X1",
            }

        if name == "get_inventory_aging":
            return []

        if name == "analyze_top_customers":
            return [
                {"customer_id": 10, "name": "阿里", "total": 99999.0},
                {"customer_id": 20, "name": "翼蓝", "total": 55000.0},
            ]

        if name == "analyze_slow_moving_products":
            return []

        # ── 写工具（5 个）─────────────────────────────────────────────
        if name == "generate_contract_draft":
            return {"draft_id": 999, "file_url": "https://example.com/draft/999"}

        if name == "generate_price_quote":
            return {"quote_id": 888, "file_url": "https://example.com/quote/888"}

        if name == "create_voucher_draft":
            return {"voucher_id": 777}

        if name == "create_price_adjustment_request":
            return {"adjust_id": 666, "status": "pending_approval"}

        if name == "create_stock_adjustment_request":
            return {"adjust_id": 555, "status": "pending_approval"}

        # 兜底 — 未知工具返 None
        return None


# ──────────────────────────────────────────────────────────────────────────────
# real_graph_agent_factory fixture
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
async def real_graph_agent_factory():
    """构造真 LLM 端到端 GraphAgent。

    yields (agent, tool_log, gate)：
      - agent: GraphAgent（FakeRedis + ConfirmGate + 真 LLM + StubToolExecutor）
      - tool_log: list[tuple[str, dict]]，StubToolExecutor 记录的所有调用
      - gate: ConfirmGate（可独立查 pending 状态）
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        pytest.skip("需要真 DEEPSEEK_API_KEY")

    redis = FakeRedis(decode_responses=False)
    gate = ConfirmGate(redis=redis)

    llm = DeepSeekLLMClient(api_key=api_key, model="deepseek-v4-flash")
    registry = ToolRegistry()
    register_all_tools(registry)
    session_memory = AsyncMock()  # 本轮不依赖持久记忆

    tool_executor = StubToolExecutor()

    agent = GraphAgent(
        llm=llm,
        registry=registry,
        confirm_gate=gate,
        session_memory=session_memory,
        tool_executor=tool_executor,
    )

    yield agent, tool_executor.log, gate

    await llm.aclose()
    await redis.aclose()
