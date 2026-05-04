"""React 测试共享 fixture。**必须在 Task 2.0 创建** —— Task 2.0 / 2.1 / 2.2 / ...
所有 react 单测都用 fake_ctx,如果只在 Task 2.1 创建,Task 2.0 的 test_invoke.py
跑时会 ERROR fixture 'fake_ctx' not found（不是 FAIL）,后续 task 链式 broken。
"""
import pytest
from hub.agent.react.context import tool_ctx, ToolContext


@pytest.fixture
def fake_ctx():
    """提供一个 set 好的 ToolContext，测试结束自动 reset。

    fake hub_user_id=1, conversation_id="test-conv", acting_as=None。
    测试断言权限调用 / 审计 log / fn kwargs 时按这个值算。
    """
    token = tool_ctx.set(ToolContext(
        hub_user_id=1,
        acting_as=None,
        conversation_id="test-conv",
        channel_userid="ding-test",
    ))
    yield {"hub_user_id": 1, "conversation_id": "test-conv"}
    tool_ctx.reset(token)


# ─────────────────────────────────────────────────────────────────────────────
# Task 5.3: 真 LLM eval — release gate hooks + real_react_agent_factory fixture
# ─────────────────────────────────────────────────────────────────────────────
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock


def _release_gate_active() -> bool:
    return os.environ.get("HUB_REACT_RELEASE_GATE") == "1"


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """release gate 模式下展示横幅 + 列出 skipped。**不修改 exit code（这里改不动）**。"""
    if not _release_gate_active():
        return
    skipped = terminalreporter.stats.get("skipped", [])
    if not skipped:
        return
    terminalreporter.write_sep("=", "RELEASE GATE FAILED", red=True, bold=True)
    terminalreporter.write_line(
        f"❌ HUB_REACT_RELEASE_GATE=1 下不允许 skipped (本次 {len(skipped)} 个 skipped):",
    )
    for sk in skipped[:10]:
        terminalreporter.write_line(f"  - {sk.nodeid}: {sk.longreprtext[:200]}")


def pytest_sessionfinish(session, exitstatus):
    """release gate 模式下: skipped > 0 → 强制 session.exitstatus 非 0。"""
    if not _release_gate_active():
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    skipped = reporter.stats.get("skipped", [])
    if skipped and session.exitstatus == 0:
        session.exitstatus = 1


# ─── real_react_agent_factory: 真 LLM eval fixture ───
@pytest.fixture
async def real_react_agent_factory(monkeypatch):
    """构造真 LLM ReActAgent。

    yields (agent, tool_log)：
      - agent: ReActAgent（真 DeepSeek + 真 fakeredis ConfirmGate + Stub ERP/底层 patches）
      - tool_log: list[tuple[str, dict]] —— 累计所有 LangChain @tool 调用

    设计：
      - 不调真 ERP（hub 测试容器没起 ERP-4）；patch erp_tools.* + draft_tools.* + generate_tools.*
      - 不写真 DB（patch require_permissions + log_tool_call + ContractDraft.filter）
      - ConfirmGate 走真 fakeredis Lua 脚本（plan-then-execute 真链路）
      - tool_log 记录每次 @tool ainvoke 的 (name, args) — 便于断言"tool was called"
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        # 本 fixture 用在 @realllm 测试上,test 函数自己 skip
        # （这里不能 skip,因为是 fixture）— 给个空 yield 让 test 决定
        yield None, []
        return

    from hub.agent.react.agent import ReActAgent
    from hub.agent.react.llm import build_chat_model
    from hub.agent.react.tools import ALL_TOOLS
    from hub.agent.react.tools._confirm_helper import set_confirm_gate
    from hub.agent.react.tools.confirm import WRITE_TOOL_DISPATCH
    from hub.agent.tools.confirm_gate import ConfirmGate

    import fakeredis.aioredis

    tool_log: list[tuple[str, dict]] = []

    # === fakeredis ConfirmGate ===
    redis = fakeredis.aioredis.FakeRedis()
    gate = ConfirmGate(redis)
    set_confirm_gate(gate)

    # === Stub ERP — 同 tests/agent/conftest.py 的 StubToolExecutor 数据 ===
    async def _search_customers(*, query, acting_as_user_id):
        tool_log.append(("search_customers", {"query": query}))
        if "阿里" in query:
            return {"items": [{"id": 10, "name": "阿里"}], "total": 1}
        if "翼蓝" in query:
            return {"items": [{"id": 20, "name": "翼蓝", "address": "广州市天河区"}], "total": 1}
        if "得帆" in query:
            return {"items": [{"id": 11, "name": "广州得帆"}], "total": 1}
        if "百度" in query:
            return {"items": [{"id": 30, "name": "百度"}], "total": 1}
        return {"items": [], "total": 0}

    async def _search_products(*, query, acting_as_user_id):
        tool_log.append(("search_products", {"query": query}))
        _KNOWN = {
            "X1": {"id": 101, "name": "X1", "sku": "SKG-X1", "list_price": 300},
            "H5": {"id": 102, "name": "H5", "sku": "SKG-H5", "list_price": 300},
            "F1": {"id": 103, "name": "F1", "sku": "SKG-F1", "list_price": 500},
            "K5": {"id": 104, "name": "K5", "sku": "SKG-K5", "list_price": 300},
        }
        for key, val in _KNOWN.items():
            if key in query:
                return {"items": [val], "total": 1}
        if "SKG" in query:
            return {"items": list(_KNOWN.values()), "total": len(_KNOWN)}
        return {"items": [], "total": 0}

    async def _check_inventory(*, product_id, acting_as_user_id):
        tool_log.append(("check_inventory", {"product_id": product_id}))
        return {"product_id": product_id, "total_stock": 100, "stocks": []}

    async def _get_customer_balance(*, customer_id, acting_as_user_id):
        tool_log.append(("get_customer_balance", {"customer_id": customer_id}))
        return {"customer_id": customer_id, "balance": 12345.67}

    async def _get_customer_history(*, product_id, customer_id, limit=5, acting_as_user_id):
        tool_log.append(("get_customer_history", {
            "product_id": product_id, "customer_id": customer_id, "limit": limit,
        }))
        return {"items": []}

    async def _get_product_detail(*, product_id, acting_as_user_id):
        tool_log.append(("get_product_detail", {"product_id": product_id}))
        return {"id": product_id, "name": "X1", "list_price": 300}

    async def _search_orders(*, customer_id=0, since_days=30, acting_as_user_id):
        tool_log.append(("search_orders", {"customer_id": customer_id, "since_days": since_days}))
        return {"items": [], "total": 0}

    async def _get_order_detail(*, order_id, acting_as_user_id):
        tool_log.append(("get_order_detail", {"order_id": order_id}))
        return {"order_id": order_id, "status": "approved", "items": []}

    async def _analyze_top_customers(*, period="近一月", top_n=10, acting_as_user_id):
        tool_log.append(("analyze_top_customers", {"period": period, "top_n": top_n}))
        return {"items": [], "data_window": "近一月"}

    monkeypatch.setattr("hub.agent.tools.erp_tools.search_customers", _search_customers)
    monkeypatch.setattr("hub.agent.react.tools.read.erp_tools.search_customers", _search_customers)
    monkeypatch.setattr("hub.agent.tools.erp_tools.search_products", _search_products)
    monkeypatch.setattr("hub.agent.react.tools.read.erp_tools.search_products", _search_products)
    monkeypatch.setattr("hub.agent.tools.erp_tools.check_inventory", _check_inventory)
    monkeypatch.setattr("hub.agent.react.tools.read.erp_tools.check_inventory", _check_inventory)
    monkeypatch.setattr("hub.agent.tools.erp_tools.get_customer_balance", _get_customer_balance)
    monkeypatch.setattr(
        "hub.agent.react.tools.read.erp_tools.get_customer_balance", _get_customer_balance,
    )
    monkeypatch.setattr("hub.agent.tools.erp_tools.get_customer_history", _get_customer_history)
    monkeypatch.setattr(
        "hub.agent.react.tools.read.erp_tools.get_customer_history", _get_customer_history,
    )
    monkeypatch.setattr("hub.agent.tools.erp_tools.get_product_detail", _get_product_detail)
    monkeypatch.setattr(
        "hub.agent.react.tools.read.erp_tools.get_product_detail", _get_product_detail,
    )
    monkeypatch.setattr("hub.agent.tools.erp_tools.search_orders", _search_orders)
    monkeypatch.setattr("hub.agent.react.tools.read.erp_tools.search_orders", _search_orders)
    monkeypatch.setattr("hub.agent.tools.erp_tools.get_order_detail", _get_order_detail)
    monkeypatch.setattr("hub.agent.react.tools.read.erp_tools.get_order_detail", _get_order_detail)
    monkeypatch.setattr(
        "hub.agent.tools.analyze_tools.analyze_top_customers", _analyze_top_customers,
    )
    monkeypatch.setattr(
        "hub.agent.react.tools.read.analyze_tools.analyze_top_customers", _analyze_top_customers,
    )

    # === Stub get_recent_drafts ContractDraft 查询 ===
    async def _query_recent_drafts(conv_id, hub_user_id, limit):
        tool_log.append(("get_recent_drafts", {"limit": limit}))
        # 第一轮没历史；后面轮可能有，但本 stub 简化：永远返一个翼蓝合同
        # （reuse 测试需要看到历史合同）
        return [{
            "id": 100,
            "customer_id": 20,
            "items": [{"product_id": 101, "qty": 10, "price": 300}],
            "extras": {
                "shipping_address": "广州市天河区",
                "shipping_contact": "翼蓝采购",
                "shipping_phone": "13800001111",
            },
            "status": "sent",
            "created_at": "2026-05-04T10:00:00",
        }]

    async def _get_erp_customer_name(customer_id, acting_as_user_id):
        return {10: "阿里", 11: "广州得帆", 20: "翼蓝", 30: "百度"}.get(
            customer_id, f"<id={customer_id}>",
        )

    monkeypatch.setattr(
        "hub.agent.react.tools.read._query_recent_contract_drafts", _query_recent_drafts,
    )
    monkeypatch.setattr(
        "hub.agent.react.tools.read._get_erp_customer_name", _get_erp_customer_name,
    )

    # === Stub _resolve_default_template_id ===
    async def _fake_template():
        return 1

    monkeypatch.setattr(
        "hub.agent.react.tools.write._resolve_default_template_id", _fake_template,
    )

    # === Stub require_permissions + log_tool_call (write/read/_invoke 三处) ===
    monkeypatch.setattr("hub.agent.react.tools._invoke.require_permissions", AsyncMock())
    monkeypatch.setattr("hub.agent.react.tools.write.require_permissions", AsyncMock())
    monkeypatch.setattr("hub.agent.react.tools.read.require_permissions", AsyncMock())

    @asynccontextmanager
    async def _fake_log_tool_call(**kwargs):
        ctx = MagicMock()
        ctx.set_result = lambda r: None
        yield ctx

    monkeypatch.setattr("hub.agent.react.tools._invoke.log_tool_call", _fake_log_tool_call)
    monkeypatch.setattr("hub.agent.react.tools.read.log_tool_call", _fake_log_tool_call)

    # === Stub 写 tool 底层（dispatch 表里的 fn）===
    async def _stub_generate_contract(
        *, customer_id, template_id, items, hub_user_id, conversation_id,
        acting_as_user_id, **kw
    ):
        tool_log.append((
            "dispatch:generate_contract_draft",
            {"customer_id": customer_id, "items": items},
        ))
        return {"draft_id": 999, "file_sent": True}

    async def _stub_generate_quote(
        *, customer_id, items, extras=None, hub_user_id, conversation_id,
        acting_as_user_id, **kw
    ):
        tool_log.append((
            "dispatch:generate_price_quote",
            {"customer_id": customer_id, "items": items},
        ))
        return {"quote_id": 888, "file_sent": True}

    async def _stub_create_voucher(
        *, voucher_data, hub_user_id, conversation_id, acting_as_user_id,
        confirmation_action_id, **kw
    ):
        tool_log.append(("dispatch:create_voucher_draft", {"voucher_data": voucher_data}))
        return {"voucher_id": 777}

    async def _stub_price_adj(
        *, customer_id, product_id, new_price, reason, hub_user_id, conversation_id,
        acting_as_user_id, confirmation_action_id, **kw
    ):
        tool_log.append(("dispatch:create_price_adjustment_request", {"customer_id": customer_id}))
        return {"adjust_id": 666, "status": "pending_approval"}

    async def _stub_stock_adj(
        *, product_id, adjustment_qty, reason, hub_user_id, conversation_id,
        acting_as_user_id, confirmation_action_id, **kw
    ):
        tool_log.append((
            "dispatch:create_stock_adjustment_request", {"product_id": product_id},
        ))
        return {"adjust_id": 555, "status": "pending_approval"}

    monkeypatch.setitem(
        WRITE_TOOL_DISPATCH, "generate_contract_draft",
        ("usecase.generate_contract.use", _stub_generate_contract, False),
    )
    monkeypatch.setitem(
        WRITE_TOOL_DISPATCH, "generate_price_quote",
        ("usecase.generate_quote.use", _stub_generate_quote, False),
    )
    monkeypatch.setitem(
        WRITE_TOOL_DISPATCH, "create_voucher_draft",
        ("usecase.create_voucher.use", _stub_create_voucher, True),
    )
    monkeypatch.setitem(
        WRITE_TOOL_DISPATCH, "create_price_adjustment_request",
        ("usecase.adjust_price.use", _stub_price_adj, True),
    )
    monkeypatch.setitem(
        WRITE_TOOL_DISPATCH, "create_stock_adjustment_request",
        ("usecase.adjust_stock.use", _stub_stock_adj, True),
    )

    # === Patch ALL_TOOLS 的 coroutine 钩 tool_log（捕获 LLM 实际调的 @tool 名）===
    # LangChain StructuredTool.coroutine 是底层异步函数；直接替换可正确拦截 ainvoke。
    # read/write tool 内部通过 invoke_business_tool 调底层,已在上面 hook；
    # 这里再 hook 一层 tool 入口,记录 LLM 直接调到的 @tool 名。
    for tool_obj in ALL_TOOLS:
        original_coroutine = tool_obj.coroutine
        tool_name = tool_obj.name

        async def _wrapped(*args, _name=tool_name, _original=original_coroutine, **kwargs):
            tool_log.append((_name, kwargs))
            return await _original(*args, **kwargs)

        tool_obj.coroutine = _wrapped

    # === 真 LLM ChatModel ===
    chat_model = build_chat_model(
        api_key=api_key,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/beta"),
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
    )

    from langgraph.checkpoint.memory import MemorySaver

    agent = ReActAgent(
        chat_model=chat_model,
        tools=ALL_TOOLS,
        checkpointer=MemorySaver(),  # 跨轮 messages 持久化（同一 conv_id 内）
        recursion_limit=15,
    )

    yield agent, tool_log

    await redis.aclose()
