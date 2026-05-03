# backend/tests/agent/test_graph_agent.py
import pytest
import json
from unittest.mock import AsyncMock
from hub.agent.graph.agent import GraphAgent


@pytest.mark.asyncio
async def test_graph_agent_accepts_external_checkpointer_for_persistence():
    """Plan 6 v9 staging hotfix：GraphAgent 必须能接受外部 checkpointer 注入，
    让生产用 AsyncPostgresSaver 持久化对话 state、跨 worker 重启 hydrate。
    默认 None 时退回 MemorySaver（仅测试 / 一次性脚本）。
    """
    from langgraph.checkpoint.memory import MemorySaver
    from hub.agent.graph.agent import GraphAgent

    # 注入自定义 saver — 应该被 _build 用上
    custom_saver = MemorySaver()
    agent = GraphAgent(
        llm=AsyncMock(), registry=AsyncMock(), confirm_gate=AsyncMock(),
        session_memory=AsyncMock(), tool_executor=AsyncMock(),
        checkpointer=custom_saver,
    )
    # compiled_graph 内部 checkpointer 应是注入的那个
    inner = agent.compiled_graph.checkpointer
    assert inner is custom_saver, (
        "外部注入的 checkpointer 必须被 GraphAgent 用上（生产路径靠这个跨重启 hydrate）"
    )

    # 不注入 → 默认 MemorySaver
    default_agent = GraphAgent(
        llm=AsyncMock(), registry=AsyncMock(), confirm_gate=AsyncMock(),
        session_memory=AsyncMock(), tool_executor=AsyncMock(),
    )
    assert isinstance(default_agent.compiled_graph.checkpointer, MemorySaver)


@pytest.mark.asyncio
async def test_graph_agent_uses_compound_thread_id():
    """spec §2.1：LangGraph config thread_id 必须 = f'{conv}:{user}'。"""
    captured = {}

    class FakeCompiled:
        async def ainvoke(self, state, *, config):
            captured["config"] = config
            return {"final_response": "ok"}

        async def aget_state(self, config):
            return None

        def get_graph(self):
            class G:
                nodes = []
                edges = []
            return G()

    agent = GraphAgent(
        compiled_graph=FakeCompiled(), llm=AsyncMock(),
        registry=AsyncMock(), confirm_gate=AsyncMock(),
        session_memory=AsyncMock(), tool_executor=AsyncMock(),
    )
    await agent.run(user_message="hi", hub_user_id=42, conversation_id="conv-1")
    assert captured["config"]["configurable"]["thread_id"] == "conv-1:42"


@pytest.mark.asyncio
async def test_graph_agent_build_node_set():
    """P2-H：_build 必须创建完整节点集合。"""
    agent = GraphAgent(
        llm=AsyncMock(), registry=AsyncMock(), confirm_gate=AsyncMock(),
        session_memory=AsyncMock(), tool_executor=AsyncMock(),
    )
    nodes = set(agent.compiled_graph.get_graph().nodes)
    expected = {
        "router", "chat", "query", "contract", "quote", "voucher",
        "adjust_price", "adjust_stock", "confirm",
        "commit_adjust_price", "commit_adjust_stock", "commit_voucher",
    }
    assert expected <= nodes, f"主图缺节点：{expected - nodes}"


@pytest.mark.asyncio
async def test_graph_agent_router_to_subgraph_routing():
    """P2-H：router → 7 个子图 + confirm 的条件边都要存在。"""
    agent = GraphAgent(
        llm=AsyncMock(), registry=AsyncMock(), confirm_gate=AsyncMock(),
        session_memory=AsyncMock(), tool_executor=AsyncMock(),
    )
    edges = agent.compiled_graph.get_graph().edges
    # 至少有从 router 出发到 8 个目标的条件边
    router_targets = {e.target for e in edges if e.source == "router"}
    assert {"chat", "query", "contract", "quote", "voucher",
            "adjust_price", "adjust_stock", "confirm"} <= router_targets


@pytest.mark.asyncio
async def test_candidate_persists_through_checkpoint_and_consumed_next_round():
    """P1-A v1.4 + P2-D v1.5 集成验收：多客户候选 → '选 2' → 2nd round 不重查 search_customers。

    完整两轮 ainvoke：
      第 1 轮："给阿里做合同 X1 10 个 300，地址北京海淀，张三 13800001111"
        - extract_contract_context 抽 customer_name + product_hints + items_raw + shipping
        - resolve_customer 拉 3 候选 → 写 candidate_customers + missing_fields=customer_choice
        - end with ask_user 输出
      第 2 轮："选 2"
        - pre_router 看 checkpoint hydrate 的 candidate_customers + "选 2" → Intent.CONTRACT
        - extract_contract_context 跳过 LLM (_looks_like_pure_selection)
        - resolve_customer 消费 candidates[1] → state.customer = 阿里云
        - parse_items（用 items_raw 本地匹配，不调 LLM）
        - validate / generate / format / cleanup
    """
    from hub.agent.graph.config import build_langgraph_config

    tool_call_log: list[tuple[str, dict]] = []

    async def fake_tool_executor(name: str, args: dict):
        tool_call_log.append((name, args))
        if name == "search_customers":
            return [
                {"id": 10, "name": "阿里巴巴"},
                {"id": 11, "name": "阿里云"},
                {"id": 12, "name": "阿里影业"},
            ]
        if name == "search_products":
            return [{"id": 1, "name": "X1"}]
        if name == "generate_contract_draft":
            return {"draft_id": 999}
        return None

    llm_responses = [
        # 第 1 轮 router → CONTRACT
        type("R", (), {"text": 'contract"', "finish_reason": "stop", "tool_calls": [],
                       "cache_hit_rate": 0.0})(),
        # 第 1 轮 extract_contract_context
        type("R", (), {"text": json.dumps({
            "customer_name": "阿里",
            "product_hints": ["X1"],
            "items_raw": [{"hint": "X1", "qty": 10, "price": 300}],
            "shipping": {"address": "北京海淀", "contact": "张三", "phone": "13800001111"},
        }), "finish_reason": "stop", "tool_calls": [], "cache_hit_rate": 0.0})(),
        # 第 1 轮 resolve_customer → search_customers (multi)
        type("R", (), {"text": "", "finish_reason": "tool_calls",
                       "tool_calls": [{"id": "1", "type": "function",
                          "function": {"name": "search_customers",
                                        "arguments": json.dumps({"query": "阿里"})}}],
                       "cache_hit_rate": 0.0})(),
        # 第 2 轮：跳过 extract_context（pure_selection）
        # 第 2 轮 resolve_products → search_products
        type("R", (), {"text": "", "finish_reason": "tool_calls",
                       "tool_calls": [{"id": "2", "type": "function",
                          "function": {"name": "search_products",
                                        "arguments": json.dumps({"query": "X1"})}}],
                       "cache_hit_rate": 0.5})(),
        # parse_items 用 items_raw 本地匹配 (不调 LLM)
        # validate_inputs (thinking on)
        type("R", (), {"text": json.dumps({"missing_fields": [], "warnings": []}),
                       "finish_reason": "stop", "tool_calls": [], "cache_hit_rate": 0.5})(),
        # generate_contract → generate_contract_draft
        type("R", (), {"text": "", "finish_reason": "tool_calls",
                       "tool_calls": [{"id": "3", "type": "function",
                          "function": {"name": "generate_contract_draft",
                                        "arguments": json.dumps({
                                            "customer_id": 11, "items": [{"product_id": 1, "qty": 10, "price": 300}],
                                            "shipping_address": "北京海淀", "contact": "张三",
                                            "phone": "13800001111", "extras": {},
                                        })}}],
                       "cache_hit_rate": 0.5})(),
        # format_response
        type("R", (), {"text": "OK", "finish_reason": "stop", "tool_calls": [],
                       "cache_hit_rate": 0.5})(),
    ]
    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=llm_responses)

    gate = AsyncMock()
    gate.list_pending_for_context = AsyncMock(return_value=[])

    agent = GraphAgent(
        llm=llm, registry=AsyncMock(), confirm_gate=gate,
        session_memory=AsyncMock(), tool_executor=fake_tool_executor,
    )

    # ===== 第 1 轮 =====
    res1 = await agent.run(
        user_message="给阿里做合同 X1 10 个 300，地址北京海淀，张三 13800001111",
        hub_user_id=1, conversation_id="c1",
    )
    first_round_tool_names = [n for n, _ in tool_call_log]
    assert "search_customers" in first_round_tool_names

    # 验 checkpoint candidate_customers 留下
    config = build_langgraph_config(conversation_id="c1", hub_user_id=1)
    snapshot1 = await agent.compiled_graph.aget_state(config)
    assert len(snapshot1.values["candidate_customers"]) == 3

    # ===== 第 2 轮：选 2 =====
    pre_round2_call_count = len(tool_call_log)
    res2 = await agent.run(
        user_message="选 2",
        hub_user_id=1, conversation_id="c1",
    )
    second_round_tools = [n for n, _ in tool_call_log[pre_round2_call_count:]]
    assert "search_customers" not in second_round_tools, (
        f"第 2 轮不应再调 search_customers，实际调了：{second_round_tools}"
    )
    assert "search_products" in second_round_tools
    assert "generate_contract_draft" in second_round_tools

    # 验 final state（cleanup_after_contract 之后）
    # LangGraph checkpoint 只持久化非默认值字段 — None / [] / {} 等默认值可能不在 keys 里，
    # 需用 .get() 防止 KeyError（等效语义：字段不存在 = 已恢复默认值）。
    # review issue 3：items 也清空 — eval items_count 改从 generate tool args 读。
    snapshot2 = await agent.compiled_graph.aget_state(config)
    assert snapshot2.values.get("draft_id") == 999
    assert snapshot2.values.get("file_sent") is True
    assert snapshot2.values.get("customer") is None
    assert snapshot2.values.get("products", []) == []
    assert snapshot2.values.get("items", []) == []
    assert snapshot2.values.get("candidate_customers", []) == []
    assert snapshot2.values.get("candidate_products", {}) == {}
    assert snapshot2.values.get("extracted_hints", {}) == {}
    assert snapshot2.values.get("active_subgraph") is None


# ─────────────────────────── pre_router 路由测试（review issue 1） ───────────────────────────


@pytest.mark.asyncio
async def test_pre_router_routes_numeric_to_confirm_when_pendings_exist():
    """review issue 1：pending 存在时，纯数字 / 选 N / 第 N 个 / id=N 都路由到 CONFIRM。

    场景：context 里有两个 pending action，用户回 "1" 期望选第 1 个 pending 执行；
    若 _is_confirm_message 不识别纯数字，会先走 LLM router 误判 chat/unknown。
    """
    from hub.agent.graph.agent import _pre_router_node
    from hub.agent.graph.state import AgentState, Intent
    from hub.agent.tools.confirm_gate import ConfirmGate, PendingAction
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    fake_pendings = [
        PendingAction(
            action_id="adj-aaaa1111", conversation_id="c1", hub_user_id=1,
            subgraph="adjust_price", summary="阿里 X1 → 280",
            payload={"tool_name": "create_price_adjustment_request", "args": {}},
            created_at=datetime.now(tz=timezone.utc),
        ),
        PendingAction(
            action_id="vch-bbbb2222", conversation_id="c1", hub_user_id=1,
            subgraph="voucher", summary="SO-1 出库",
            payload={"tool_name": "create_voucher_draft", "args": {}},
            created_at=datetime.now(tz=timezone.utc),
        ),
    ]

    gate = AsyncMock(spec=ConfirmGate)
    gate.list_pending_for_context = AsyncMock(return_value=fake_pendings)

    selection_inputs = ["1", "选 1", "选1", "第一个", "第1个", "id=adj-aaaa1111"]
    for msg in selection_inputs:
        state = AgentState(user_message=msg, hub_user_id=1, conversation_id="c1")
        out = await _pre_router_node(state, gate=gate)
        assert out.intent == Intent.CONFIRM, (
            f"消息 {msg!r} 在有 pending 时应路由到 CONFIRM，实际 {out.intent!r}"
        )


@pytest.mark.asyncio
async def test_pre_router_action_id_routes_to_confirm():
    """review issue 1：用户复制 action_id 回复时进 CONFIRM。"""
    from hub.agent.graph.agent import _pre_router_node
    from hub.agent.graph.state import AgentState, Intent
    from hub.agent.tools.confirm_gate import ConfirmGate, PendingAction
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    fake_pendings = [
        PendingAction(
            action_id="adj-aaaa1111aaaa1111aaaa1111aaaa1111", conversation_id="c1",
            hub_user_id=1, subgraph="adjust_price", summary="x", payload={},
            created_at=datetime.now(tz=timezone.utc),
        ),
    ]
    gate = AsyncMock(spec=ConfirmGate)
    gate.list_pending_for_context = AsyncMock(return_value=fake_pendings)

    state = AgentState(
        user_message="adj-aaaa1111aaaa1111aaaa1111aaaa1111",
        hub_user_id=1, conversation_id="c1",
    )
    out = await _pre_router_node(state, gate=gate)
    assert out.intent == Intent.CONFIRM


@pytest.mark.asyncio
async def test_pre_router_candidate_selection_not_hijacked_by_pending():
    """review issue 1：candidate_* 选择不应被 pending 路由抢走。

    场景：context 里没有 pending，但有 candidate_customers + active_subgraph=contract，
    用户回 "1" 应该路由到 CONTRACT（候选选择），不是 CONFIRM。
    """
    from hub.agent.graph.agent import _pre_router_node
    from hub.agent.graph.state import AgentState, Intent, CustomerInfo
    from hub.agent.tools.confirm_gate import ConfirmGate
    from unittest.mock import AsyncMock

    gate = AsyncMock(spec=ConfirmGate)
    gate.list_pending_for_context = AsyncMock(return_value=[])  # 无 pending

    state = AgentState(user_message="1", hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=11, name="阿里云"),
    ]
    state.active_subgraph = "contract"
    out = await _pre_router_node(state, gate=gate)
    assert out.intent == Intent.CONTRACT, (
        f"无 pending + candidate_customers + 活跃 contract 时 '1' 应路由 CONTRACT，"
        f"实际 {out.intent!r}"
    )


@pytest.mark.asyncio
async def test_pre_router_chat_no_pending_no_candidate_falls_to_llm_router():
    """review issue 1：无 pending + 无 candidate 时纯数字应让 LLM router 决定（intent=None）。"""
    from hub.agent.graph.agent import _pre_router_node
    from hub.agent.graph.state import AgentState
    from hub.agent.tools.confirm_gate import ConfirmGate
    from unittest.mock import AsyncMock

    gate = AsyncMock(spec=ConfirmGate)
    gate.list_pending_for_context = AsyncMock(return_value=[])

    state = AgentState(user_message="1", hub_user_id=1, conversation_id="c1")
    out = await _pre_router_node(state, gate=gate)
    assert out.intent is None, f"无 pending + 无 candidate 应交给 LLM router，实际 {out.intent!r}"


# ─────────────────────────── run() 字段重置测试（review issue 4） ───────────────────────────


@pytest.mark.asyncio
async def test_pre_router_pending_plus_candidate_routes_to_subgraph_not_confirm():
    """review round 2 / P1：用户在候选选择流程里回 "选 2"，**即使**有旧 pending 也必须走候选 subgraph，
    不能被 pending 路由抢走（否则单 pending + confirm_node 会直接 claim 写操作 = 误执行）。
    """
    from hub.agent.graph.agent import _pre_router_node
    from hub.agent.graph.state import AgentState, Intent, CustomerInfo
    from hub.agent.tools.confirm_gate import ConfirmGate, PendingAction
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    # 旧 pending 还在（来自前一个调价请求未确认）
    fake_pendings = [
        PendingAction(
            action_id="adj-aaaa1111aaaa1111aaaa1111aaaa1111", conversation_id="c1",
            hub_user_id=1, subgraph="adjust_price", summary="阿里 X1 → 280",
            payload={"tool_name": "create_price_adjustment_request", "args": {}},
            created_at=datetime.now(tz=timezone.utc),
        ),
    ]
    gate = AsyncMock(spec=ConfirmGate)
    gate.list_pending_for_context = AsyncMock(return_value=fake_pendings)

    # 用户当下在合同候选客户的选择流程
    state = AgentState(user_message="选 2", hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=11, name="阿里云"),
    ]
    state.active_subgraph = "contract"

    out = await _pre_router_node(state, gate=gate)
    # 关键断言：必须走 contract（候选选择），不是 CONFIRM
    assert out.intent == Intent.CONTRACT, (
        f"pending + candidate 同时存在时 '选 2' 应进候选 subgraph，"
        f"绝不能进 CONFIRM 误 claim 写操作；实际 {out.intent!r}"
    )


@pytest.mark.asyncio
async def test_pre_router_single_pending_numeric_does_not_auto_claim():
    """review round 2 / P1：单 pending + 用户回 "1" + **无 candidate**，不应自动进 CONFIRM。

    用户可能是想新开一个流程而不是确认 pending。confirm_node 里 1 pending 直接 claim
    写操作 = 误执行风险。建议：仅 pendings >= 2 才把数字当成"选第 N 个 pending"。
    """
    from hub.agent.graph.agent import _pre_router_node
    from hub.agent.graph.state import AgentState
    from hub.agent.tools.confirm_gate import ConfirmGate, PendingAction
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    fake_pendings = [
        PendingAction(
            action_id="adj-aaaa1111aaaa1111aaaa1111aaaa1111", conversation_id="c1",
            hub_user_id=1, subgraph="adjust_price", summary="阿里 X1 → 280",
            payload={"tool_name": "create_price_adjustment_request", "args": {}},
            created_at=datetime.now(tz=timezone.utc),
        ),
    ]
    gate = AsyncMock(spec=ConfirmGate)
    gate.list_pending_for_context = AsyncMock(return_value=fake_pendings)

    state = AgentState(user_message="1", hub_user_id=1, conversation_id="c1")
    out = await _pre_router_node(state, gate=gate)
    assert out.intent is None, (
        f"单 pending + 数字 + 无 candidate 必须交给 LLM router 决定，"
        f"不能自动 CONFIRM 触发 claim；实际 {out.intent!r}"
    )


@pytest.mark.asyncio
async def test_pre_router_multi_pending_numeric_no_candidate_routes_to_confirm():
    """review round 2 / P1：多 pending（≥2）+ 数字 + 无 candidate → CONFIRM（用 confirm_node 按编号匹配 pending）。"""
    from hub.agent.graph.agent import _pre_router_node
    from hub.agent.graph.state import AgentState, Intent
    from hub.agent.tools.confirm_gate import ConfirmGate, PendingAction
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    fake_pendings = [
        PendingAction(
            action_id="adj-aaaa1111aaaa1111aaaa1111aaaa1111", conversation_id="c1",
            hub_user_id=1, subgraph="adjust_price", summary="阿里 X1 → 280",
            payload={"tool_name": "create_price_adjustment_request", "args": {}},
            created_at=datetime.now(tz=timezone.utc),
        ),
        PendingAction(
            action_id="vch-bbbb2222bbbb2222bbbb2222bbbb2222", conversation_id="c1",
            hub_user_id=1, subgraph="voucher", summary="SO-1 出库",
            payload={"tool_name": "create_voucher_draft", "args": {}},
            created_at=datetime.now(tz=timezone.utc),
        ),
    ]
    gate = AsyncMock(spec=ConfirmGate)
    gate.list_pending_for_context = AsyncMock(return_value=fake_pendings)

    state = AgentState(user_message="1", hub_user_id=1, conversation_id="c1")
    out = await _pre_router_node(state, gate=gate)
    assert out.intent == Intent.CONFIRM


@pytest.mark.asyncio
async def test_pre_router_action_id_with_candidate_routes_to_confirm_not_subgraph():
    """review round 3 / P1：action_id 是单点引用（指向具体 pending），即使存在
    candidate_* 也不能被候选选择路由抢走，必须进 CONFIRM。

    旧实现：_is_selection_message 复用 _looks_like_pure_selection，把 action_id 也
    当作 selection；候选分支优先 → 用户复制 action_id 时被路由回 contract/quote
    子图，永远进不了 CONFIRM。
    """
    from hub.agent.graph.agent import _pre_router_node
    from hub.agent.graph.state import AgentState, Intent, CustomerInfo
    from hub.agent.tools.confirm_gate import ConfirmGate, PendingAction
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    aid = "adj-aaaa1111aaaa1111aaaa1111aaaa1111"
    fake_pendings = [
        PendingAction(
            action_id=aid, conversation_id="c1", hub_user_id=1,
            subgraph="adjust_price", summary="阿里 X1 → 280",
            payload={"tool_name": "create_price_adjustment_request", "args": {}},
            created_at=datetime.now(tz=timezone.utc),
        ),
    ]
    gate = AsyncMock(spec=ConfirmGate)
    gate.list_pending_for_context = AsyncMock(return_value=fake_pendings)

    state = AgentState(user_message=aid, hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=11, name="阿里云"),
    ]
    state.active_subgraph = "contract"

    out = await _pre_router_node(state, gate=gate)
    assert out.intent == Intent.CONFIRM, (
        f"action_id 复制粘贴 + pending 命中时必须进 CONFIRM，"
        f"即使 candidate_* 还有也不能被候选选择路由抢；实际 {out.intent!r}"
    )


@pytest.mark.asyncio
async def test_pre_router_confirm_word_with_single_pending_still_routes_confirm():
    """review round 2：确认词路径不变 — 单 pending + "确认" 仍进 CONFIRM（用户明确表达确认意图）。"""
    from hub.agent.graph.agent import _pre_router_node
    from hub.agent.graph.state import AgentState, Intent
    from hub.agent.tools.confirm_gate import ConfirmGate, PendingAction
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    fake_pendings = [
        PendingAction(
            action_id="adj-aaaa1111aaaa1111aaaa1111aaaa1111", conversation_id="c1",
            hub_user_id=1, subgraph="adjust_price", summary="x", payload={},
            created_at=datetime.now(tz=timezone.utc),
        ),
    ]
    gate = AsyncMock(spec=ConfirmGate)
    gate.list_pending_for_context = AsyncMock(return_value=fake_pendings)

    for word in ("确认", "是", "好的", "OK", "yes"):
        state = AgentState(user_message=word, hub_user_id=1, conversation_id="c1")
        out = await _pre_router_node(state, gate=gate)
        assert out.intent == Intent.CONFIRM, (
            f"确认词 {word!r} + 任意 pending 数应进 CONFIRM，实际 {out.intent!r}"
        )


@pytest.mark.asyncio
async def test_run_resets_per_turn_output_fields():
    """review issue 4：每轮 update_payload 必须重置上轮输出字段，
    避免 file_sent / confirmed_* 跨轮 hydrate 污染本轮判定。
    """
    from hub.agent.graph.agent import GraphAgent

    captured_payloads: list[dict] = []

    class CapturingCompiled:
        async def ainvoke(self, payload, *, config):
            captured_payloads.append(payload)
            return {"final_response": "ok"}

        async def aget_state(self, config):
            return None

        def get_graph(self):
            class G:
                nodes = []
                edges = []
            return G()

    agent = GraphAgent(
        compiled_graph=CapturingCompiled(), llm=AsyncMock(),
        registry=AsyncMock(), confirm_gate=AsyncMock(),
        session_memory=AsyncMock(), tool_executor=AsyncMock(),
    )
    await agent.run(user_message="hi", hub_user_id=1, conversation_id="c1")
    assert len(captured_payloads) == 1
    p = captured_payloads[0]
    # 每轮必须显式 reset 这些上轮输出字段（避免 LangGraph checkpoint hydrate 污染）
    assert p.get("file_sent") is False, "file_sent 必须每轮 reset 为 False"
    assert p.get("confirmed_subgraph") is None, "confirmed_subgraph 必须每轮 reset 为 None"
    assert p.get("confirmed_action_id") is None, "confirmed_action_id 必须每轮 reset 为 None"
    assert p.get("confirmed_payload") is None, "confirmed_payload 必须每轮 reset 为 None"
    # 现有的也应保留
    assert p.get("intent") is None
    assert p.get("final_response") is None
    assert p.get("errors") == []
