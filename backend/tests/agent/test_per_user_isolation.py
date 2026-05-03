import os
import pytest
from hub.agent.graph.config import build_thread_id, parse_thread_id


def test_build_thread_id_basic():
    assert build_thread_id(conversation_id="c1", hub_user_id=42) == "c1:42"


def test_build_thread_id_rejects_empty():
    with pytest.raises(ValueError):
        build_thread_id(conversation_id="", hub_user_id=42)
    with pytest.raises(ValueError):
        build_thread_id(conversation_id="c1", hub_user_id=0)


def test_parse_roundtrip():
    tid = build_thread_id(conversation_id="conv-abc", hub_user_id=7)
    conv, user = parse_thread_id(tid)
    assert conv == "conv-abc" and user == 7


def test_same_conv_different_user_different_thread_id():
    """核心约束：同一 conv，不同 user → 不同 thread_id（LangGraph checkpoint 隔离）。"""
    a = build_thread_id(conversation_id="group-1", hub_user_id=1)
    b = build_thread_id(conversation_id="group-1", hub_user_id=2)
    assert a != b


@pytest.mark.realllm
@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="需要真 API key")
async def test_per_user_isolation_real_llm(real_graph_agent_factory):
    """同 conv 不同 user：A 起阿里合同 + B 起百度合同 + A 续 → 必须延续阿里 不混入百度。

    Plan 6 v9 §2.1：thread_id = (conv, user) 复合 key 严格隔离。
    spec §13 真 LLM 验收：per-user state machine 不串。
    """
    agent, tool_log, gate = real_graph_agent_factory
    conv = "group-realllm-isolation"

    # A 起合同（不给完整信息 → 落 ask_user）
    a1 = await agent.run(
        user_message="给阿里做合同 X1 10 个 300",
        hub_user_id=1, conversation_id=conv,
    )
    a1_text = a1 or ""
    print(f"\nA first turn response: {a1_text[:200]}")

    # B 在同一群里起百度合同
    b1 = await agent.run(
        user_message="给百度做合同 Y1 5 个 200",
        hub_user_id=2, conversation_id=conv,
    )
    b1_text = b1 or ""
    print(f"B first turn response: {b1_text[:200]}")

    # A 续：补地址 — 必须仍是阿里上下文，**不能**是百度
    a2 = await agent.run(
        user_message="地址北京海淀，张三 13800001111",
        hub_user_id=1, conversation_id=conv,
    )
    a2_text = a2 or ""
    print(f"A second turn response: {a2_text[:200]}")

    # 关键断言：A 的第二轮回复不应包含百度（B 的客户）
    assert "百度" not in a2_text, f"A 第二轮串入百度上下文：{a2_text[:300]}"
