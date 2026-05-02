# backend/tests/agent/test_graph_agent.py
import pytest
import json
from unittest.mock import AsyncMock
from hub.agent.graph.agent import GraphAgent


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
    # items 不清空 — parse_contract_items 每次重新生成，e2e 测试可验证 items_count。
    snapshot2 = await agent.compiled_graph.aget_state(config)
    assert snapshot2.values.get("draft_id") == 999
    assert snapshot2.values.get("file_sent") is True
    assert snapshot2.values.get("customer") is None
    assert snapshot2.values.get("products", []) == []
    assert snapshot2.values.get("candidate_customers", []) == []
    assert snapshot2.values.get("candidate_products", {}) == {}
    assert snapshot2.values.get("extracted_hints", {}) == {}
    assert snapshot2.values.get("active_subgraph") is None
