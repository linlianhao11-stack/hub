# backend/tests/agent/test_node_resolve_customer.py
import pytest
from unittest.mock import AsyncMock
import json
from hub.agent.graph.state import ContractState
from hub.agent.graph.nodes.resolve_customer import resolve_customer_node


@pytest.mark.asyncio
async def test_resolve_customer_unique_match(_llm_mock):
    """unique 命中：直接写 state.customer。"""
    llm = _llm_mock("search_customers", {"query": "阿里"})
    state = ContractState(user_message="给阿里做合同 X1 10 个 300",
                            hub_user_id=1, conversation_id="c1",
                            extracted_hints={"customer_name": "阿里"})
    out = await resolve_customer_node(state, llm=llm,
        tool_executor=AsyncMock(return_value=[{"id": 10, "name": "阿里"}]))
    kw = llm.chat.await_args.kwargs
    assert kw["tool_choice"] == {"type": "function", "function": {"name": "search_customers"}}
    assert out.customer is not None and out.customer.id == 10
    assert out.missing_fields == []  # 没歧义


@pytest.mark.asyncio
async def test_resolve_customer_zero_match(_llm_mock):
    """none：search 返回空，state.customer 留 None，missing_fields 加 'customer'。"""
    llm = _llm_mock("search_customers", {"query": "未知客户"})
    state = ContractState(user_message="给未知客户做合同", hub_user_id=1, conversation_id="c1",
                            extracted_hints={"customer_name": "未知客户"})
    out = await resolve_customer_node(state, llm=llm,
        tool_executor=AsyncMock(return_value=[]))
    assert out.customer is None
    assert "customer" in out.missing_fields  # 让下游 ask_user 问


@pytest.mark.asyncio
async def test_candidate_selection_by_number(_llm_mock):
    """P2-C：上轮 candidate_customers + 本轮"选 2" → 直接消费 candidates[1]。"""
    from hub.agent.graph.state import CustomerInfo
    state = ContractState(user_message="选 2", hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=11, name="阿里云"),
        CustomerInfo(id=12, name="阿里影业"),
    ]
    state.missing_fields = ["customer_choice"]
    out = await resolve_customer_node(state, llm=AsyncMock(),
                                         tool_executor=AsyncMock())
    assert out.customer is not None and out.customer.id == 11
    assert out.candidate_customers == []  # 清空
    assert "customer_choice" not in out.missing_fields


@pytest.mark.asyncio
async def test_candidate_selection_by_id():
    """P2-C：用户回复"id=12" → 精确选 id 12 的候选。"""
    from hub.agent.graph.state import CustomerInfo
    state = ContractState(user_message="id=12", hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=12, name="阿里影业"),
    ]
    state.missing_fields = ["customer_choice"]
    out = await resolve_customer_node(state, llm=AsyncMock(),
                                         tool_executor=AsyncMock())
    assert out.customer is not None and out.customer.id == 12


@pytest.mark.asyncio
async def test_candidate_selection_by_name():
    """P2-C：用户直接说候选里的名字 → 精确选。"""
    from hub.agent.graph.state import CustomerInfo
    state = ContractState(user_message="阿里影业", hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=12, name="阿里影业"),
    ]
    out = await resolve_customer_node(state, llm=AsyncMock(),
                                         tool_executor=AsyncMock())
    assert out.customer is not None and out.customer.id == 12


@pytest.mark.asyncio
async def test_candidate_selection_by_chinese_ordinal():
    """P2-B v1.6：用户回"第二个" 必须命中 candidates[1]，**不能** ValueError。
    （v1.5 的 dict.get(key, int(key) if isdigit else 0) 默认参数提前求值会抛 ValueError）"""
    from hub.agent.graph.state import CustomerInfo
    state = ContractState(user_message="第二个", hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=11, name="阿里云"),
        CustomerInfo(id=12, name="阿里影业"),
    ]
    state.missing_fields = ["customer_choice"]
    out = await resolve_customer_node(state, llm=AsyncMock(),
                                         tool_executor=AsyncMock())
    assert out.customer is not None and out.customer.id == 11
    assert out.candidate_customers == []


@pytest.mark.asyncio
async def test_candidate_selection_unrecognized_keeps_state():
    """P2-C：用户说"嗯..." 不是有效选择 → 保留 candidate，不写 customer，让 ask_user 再列一次。"""
    from hub.agent.graph.state import CustomerInfo
    state = ContractState(user_message="嗯...", hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=12, name="阿里影业"),
    ]
    state.missing_fields = ["customer_choice"]
    out = await resolve_customer_node(state, llm=AsyncMock(),
                                         tool_executor=AsyncMock())
    assert out.customer is None
    assert len(out.candidate_customers) == 2  # 保留候选
    assert "customer_choice" in out.missing_fields  # 让 ask_user 再列


@pytest.mark.asyncio
async def test_resolve_customer_multi_match_does_not_pick_first(_llm_mock):
    """P1-B：multi 命中**绝不**默认取 [0]。
    合同/报价对外文件，错客户比反问严重得多。必须把候选写入 state.candidate_customers
    + missing_fields 加 'customer_choice'，让下游 ask_user 问用户选。"""
    llm = _llm_mock("search_customers", {"query": "阿里"})
    state = ContractState(user_message="给阿里做合同", hub_user_id=1, conversation_id="c1",
                            extracted_hints={"customer_name": "阿里"})
    out = await resolve_customer_node(state, llm=llm,
        tool_executor=AsyncMock(return_value=[
            {"id": 10, "name": "阿里巴巴"},
            {"id": 11, "name": "阿里云"},
            {"id": 12, "name": "阿里影业"},
        ]))
    assert out.customer is None  # 关键：不能默认取 [0]
    assert "customer_choice" in out.missing_fields
    assert len(out.candidate_customers) == 3
    assert {c.id for c in out.candidate_customers} == {10, 11, 12}


@pytest.fixture
def _llm_mock():
    """返回构造 mock LLM 的 helper — 模拟单 tool_call。"""
    import json
    from unittest.mock import AsyncMock
    def _make(tool_name: str, arguments: dict):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=type("R", (), {
            "text": "", "finish_reason": "tool_calls",
            "tool_calls": [{"id": "1", "type": "function",
                "function": {"name": tool_name, "arguments": json.dumps(arguments)}}],
        })())
        return llm
    return _make
