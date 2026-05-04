# backend/tests/agent/test_registry_strict.py
"""Plan 6 §5.2：ToolRegistry strict 校验 + subgraph_filter 测试（Task 2.1）。"""
from __future__ import annotations

import pytest
from hub.agent.tools.registry import ToolRegistry


def test_registry_subgraph_filter():
    """spec §5.2：register 时按 _subgraphs 字段过滤。"""
    reg = ToolRegistry()
    reg.register({
        "type": "function",
        "function": {"name": "search_customers", "strict": True,
                     "parameters": {"type": "object", "properties": {},
                                    "required": [], "additionalProperties": False}},
        "_subgraphs": ["query", "contract", "quote"],
    })
    reg.register({
        "type": "function",
        "function": {"name": "generate_contract_draft", "strict": True,
                     "parameters": {"type": "object", "properties": {},
                                    "required": [], "additionalProperties": False}},
        "_subgraphs": ["contract"],
    })
    schemas = reg.schemas_for_subgraph("contract")
    names = {s["function"]["name"] for s in schemas}
    assert names == {"search_customers", "generate_contract_draft"}
    schemas = reg.schemas_for_subgraph("query")
    names = {s["function"]["name"] for s in schemas}
    assert names == {"search_customers"}


def test_registry_rejects_non_strict_schema_when_enforce():
    """spec §5.2：enforce_strict=True 时所有 schema 必须 strict=True 才能注册。

    注：默认 enforce_strict=False（向后兼容 ChainAgent / 现有测试）；
    Phase 2.5 register_all_tools 用 enforce_strict=True 强约束。
    """
    reg = ToolRegistry()
    with pytest.raises(ValueError, match="strict"):
        reg.register({
            "type": "function",
            "function": {"name": "x"},  # 缺 strict
        }, enforce_strict=True)


def test_registry_register_backward_compat():
    """ChainAgent 旧 callers 用 register(schema) 不传 enforce_strict 必须仍然工作。"""
    reg = ToolRegistry()
    # 不带 strict / additionalProperties 也能注册
    reg.register({
        "type": "function",
        "function": {"name": "legacy_tool"},
    })
    assert reg.get("legacy_tool") is not None


def test_registry_schemas_for_subgraph_missing():
    """没有 _subgraphs 字段的 schema 不会被任何 subgraph 拿到。"""
    reg = ToolRegistry()
    reg.register({
        "type": "function",
        "function": {"name": "no_subgraph_tag", "strict": True,
                     "parameters": {"type": "object", "properties": {},
                                    "required": [], "additionalProperties": False}},
        # 没 _subgraphs
    })
    assert reg.schemas_for_subgraph("query") == []
