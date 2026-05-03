"""Plan 6 v9 Task 8.2 — 30 case 真 LLM eval。

机检主导（hard release gate）+ 人工评分（落 notes/2026-05-02-eval-results.md）。

跑：
  DEEPSEEK_API_KEY=... pytest tests/agent/test_realllm_eval.py -v -m realllm -s

机检 release gate：≥ 28/30 PASS。
人工 release gate：平均分 ≥ 4.0/5（reviewer 在 eval-results.md 填）。
"""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest
import yaml

EVAL_CASES_PATH = Path(__file__).parent / "fixtures" / "eval_30_cases.yaml"


def _load_cases() -> list[dict]:
    return yaml.safe_load(EVAL_CASES_PATH.read_text(encoding="utf-8"))


def _category_of(case: dict) -> str:
    """按 case id 前缀分类（不按 last turn intent — 多轮 case 末轮常是 confirm）。"""
    cid = case["id"]
    for prefix in (
        "chat", "query", "contract", "quote", "voucher",
        "adjust_price", "adjust_stock", "cross_round", "boundary", "isolation",
    ):
        if cid.startswith(prefix):
            return prefix
    return "other"


def test_fixture_schema_30_cases_with_intent_distribution():
    """schema sanity check（无需 LLM）：恰好 30 个 case + 分布合规 + id 唯一 + 字段齐全。"""
    cases = _load_cases()
    assert len(cases) == 30, f"期望 30 个 case，实际 {len(cases)}"

    # id 唯一
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), f"id 重复：{[i for i in ids if ids.count(i) > 1]}"

    # 每 case 必备字段
    for c in cases:
        assert "id" in c, f"缺 id: {c}"
        assert "turns" in c and isinstance(c["turns"], list) and len(c["turns"]) >= 1, \
            f"case {c.get('id')} 缺 turns"
        for t in c["turns"]:
            assert "input" in t, f"case {c['id']} 某 turn 缺 input"
            assert "expected_intent" in t, f"case {c['id']} 某 turn 缺 expected_intent"

    # 类目分布最低值（按 id prefix，不按 last turn intent）
    cat_distribution = Counter(_category_of(c) for c in cases)
    assert cat_distribution.get("chat", 0) >= 3, f"chat 类目应 ≥ 3，实际 {cat_distribution.get('chat', 0)}"
    assert cat_distribution.get("query", 0) >= 5, f"query 类目应 ≥ 5"
    assert cat_distribution.get("contract", 0) >= 4, f"contract 类目应 ≥ 4"
    assert cat_distribution.get("quote", 0) >= 2, f"quote 类目应 ≥ 2"


@pytest.mark.realllm
@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="需要真 API key")
@pytest.mark.parametrize("case_index", range(30))
async def test_eval_case(real_graph_agent_factory, case_index):
    """跑单个 eval case — 复用 acceptance driver 的 SUPPORTED_TURN_FIELDS 机检。"""
    from tests.agent.test_acceptance_scenarios import assert_scenario_turn  # 复用 Task 8.1 driver
    from hub.agent.graph.config import build_langgraph_config

    cases = _load_cases()
    case = cases[case_index]
    case_id = case["id"]
    agent, tool_log, gate = real_graph_agent_factory

    case_conv = f"eval-{case_id}"
    for i, turn in enumerate(case["turns"]):
        tool_log_before = len(tool_log)
        conv = turn.get("conversation_id", case_conv)
        user = turn.get("hub_user_id", 1)
        res = await agent.run(
            user_message=turn["input"], hub_user_id=user, conversation_id=conv,
        )
        config = build_langgraph_config(conversation_id=conv, hub_user_id=user)
        snapshot = await agent.compiled_graph.aget_state(config)
        state_values = snapshot.values if snapshot else {}
        await assert_scenario_turn(
            agent, gate, tool_log, tool_log_before, turn, res, state_values, conv, user,
        )
        intent = state_values.get("intent")
        intent_value = intent.value if hasattr(intent, "value") else intent
        print(f"  case {case_id} turn {i+1}: {turn['input'][:50]} → intent={intent_value}")
