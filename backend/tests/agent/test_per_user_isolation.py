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
