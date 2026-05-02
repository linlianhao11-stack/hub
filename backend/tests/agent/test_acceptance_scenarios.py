# backend/tests/agent/test_acceptance_scenarios.py
"""Plan 6 v9 Task 8.1 — 6 故事真 LLM 端到端 acceptance 驱动。

Fixture yaml 位于 backend/tests/agent/fixtures/scenarios/。
运行：
    uv run pytest tests/agent/test_acceptance_scenarios.py -v -m realllm -s

SUPPORTED_TURN_FIELDS（8 个机检维度）：
  - expected_intent        : state.intent.value 精确匹配
  - tool_caps              : {tool_name: N} 或 {tool_name: {min: A, max: B}}
  - must_contain           : 回复文本必须包含的子串列表
  - forbid                 : 回复文本不得包含的子串列表
  - sent_files_min         : file_sent 布尔 → ≥1 视为 1
  - items_count            : state.items 精确长度
  - creates_pending_action : 本轮是否在 gate 中新建了 pending
  - pending_state          : {action_id: "still_pending"|"claimed"|"expired"|"missing"}
"""
from __future__ import annotations

import os
import yaml
import pytest
from collections import Counter
from pathlib import Path

SCENARIOS_DIR = Path(__file__).parent / "fixtures" / "scenarios"

pytestmark = [
    pytest.mark.realllm,
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="需要真 API key"),
]


# ──────────────────────────────────────────────────────────────────────────────
# pending 状态辅助
# ──────────────────────────────────────────────────────────────────────────────

async def _check_pending_state(gate, action_id: str) -> str:
    """返回 'still_pending' / 'claimed' / 'expired' / 'missing'。"""
    if await gate.is_claimed(action_id):
        return "claimed"
    p = await gate.get_pending_by_id(action_id)
    if p is None:
        return "missing"
    if p.is_expired():
        return "expired"
    return "still_pending"


# ──────────────────────────────────────────────────────────────────────────────
# tool_caps 检查
# ──────────────────────────────────────────────────────────────────────────────

def _check_tool_caps(tool_log_subset: list, caps_spec: dict) -> list[str]:
    """
    tool_caps 规格检查：
      - int N       → 精确 count == N
      - {min, max}  → count in [min, max]
    """
    counts = Counter(name for name, _ in tool_log_subset)
    errors = []
    for tool_name, spec in caps_spec.items():
        actual = counts.get(tool_name, 0)
        if isinstance(spec, int):
            if actual != spec:
                errors.append(f"{tool_name}: 期望恰好 {spec} 次，实际 {actual} 次")
        elif isinstance(spec, dict):
            mn = spec.get("min", 0)
            mx = spec.get("max", 999)
            if not (mn <= actual <= mx):
                errors.append(f"{tool_name}: 期望 [{mn},{mx}] 次，实际 {actual} 次")
    return errors


# ──────────────────────────────────────────────────────────────────────────────
# 主 turn 断言
# ──────────────────────────────────────────────────────────────────────────────

async def assert_scenario_turn(
    agent,
    gate,
    tool_log: list,
    tool_log_before: int,
    turn: dict,
    response_text: str | None,
    state_values: dict,
    conv_id: str,
    hub_user_id: int,
) -> None:
    """对单个 turn 跑所有 SUPPORTED_TURN_FIELDS 断言。

    失败时收集所有错误一起报告（便于调试真 LLM 行为差异）。
    """
    errors: list[str] = []
    text = response_text or ""

    # 1. expected_intent
    if "expected_intent" in turn:
        raw = state_values.get("intent")
        actual_value = raw.value if hasattr(raw, "value") else str(raw)
        if actual_value != turn["expected_intent"]:
            errors.append(
                f"intent: 期望 {turn['expected_intent']!r}，实际 {actual_value!r}"
            )

    # 2. tool_caps
    if "tool_caps" in turn:
        tool_log_subset = tool_log[tool_log_before:]
        errors.extend(_check_tool_caps(tool_log_subset, turn["tool_caps"]))

    # 3. must_contain
    if "must_contain" in turn:
        for needle in turn["must_contain"]:
            if needle not in text:
                errors.append(
                    f"must_contain {needle!r} 不在回复中；回复前 120 字：{text[:120]!r}"
                )

    # 4. forbid
    if "forbid" in turn:
        for needle in turn["forbid"]:
            if needle in text:
                errors.append(
                    f"forbid {needle!r} 命中；回复前 120 字：{text[:120]!r}"
                )

    # 5. sent_files_min
    if "sent_files_min" in turn:
        sent = 1 if state_values.get("file_sent") else 0
        if sent < turn["sent_files_min"]:
            errors.append(
                f"sent_files: 期望 ≥{turn['sent_files_min']}，实际 {sent}"
            )

    # 6. items_count
    if "items_count" in turn:
        items = state_values.get("items") or []
        if len(items) != turn["items_count"]:
            errors.append(
                f"items_count: 期望 {turn['items_count']}，实际 {len(items)}"
            )

    # 7. creates_pending_action
    # AgentState 没有 pending_action_id — 从 gate.list_pending_for_context 检查
    if "creates_pending_action" in turn:
        pending_list = await gate.list_pending_for_context(
            conversation_id=conv_id, hub_user_id=hub_user_id
        )
        has_pending = len(pending_list) > 0
        if has_pending != turn["creates_pending_action"]:
            errors.append(
                f"creates_pending_action: 期望 {turn['creates_pending_action']}，"
                f"实际 {has_pending}（gate 中有 {len(pending_list)} 条 pending）"
            )

    # 8. pending_state
    if "pending_state" in turn:
        for action_id, expected_state in turn["pending_state"].items():
            actual = await _check_pending_state(gate, action_id)
            if actual != expected_state:
                errors.append(
                    f"pending_state[{action_id!r}]: 期望 {expected_state!r}，实际 {actual!r}"
                )

    assert not errors, (
        "Turn assertion failures:\n  " + "\n  ".join(errors)
    )


# ──────────────────────────────────────────────────────────────────────────────
# parametrized driver — 6 故事
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scenario_yaml", [
    "story1_chat.yaml",
    "story2_query.yaml",
    "story3_contract_oneround.yaml",
    "story4_query_then_contract.yaml",
    "story5_quote.yaml",
    "story6_adjust_price_confirm.yaml",
    # story6b 跨会话需要两个独立 conversation_id，driver 已支持 per-turn conversation_id，
    # 但 pending_state 中的 {adj-1: still_pending} 依赖 setup 预置 — 留后续 multi_pending 一起支持
    # "story6b_cross_conversation_isolation.yaml",
    # multi_pending 需要 setup 预置 pending — 驱动暂不支持，留后续任务
    # "multi_pending.yaml",
])
@pytest.mark.realllm
@pytest.mark.asyncio
async def test_acceptance_scenario(real_graph_agent_factory, scenario_yaml):
    """6 故事真 LLM 端到端 — Plan 6 v9 Phase 8 Task 8.1。

    每个 scenario 对应一个 yaml fixture，驱动 GraphAgent 跑完所有 turns，
    逐 turn 检查 8 个机检维度（见模块 docstring）。
    """
    from hub.agent.graph.config import build_langgraph_config

    agent, tool_log, gate = real_graph_agent_factory
    scenario = yaml.safe_load(
        (SCENARIOS_DIR / scenario_yaml).read_text(encoding="utf-8")
    )

    # 每 case 用独立 conversation_id，避免跨 case 状态污染
    case_id = scenario_yaml.replace(".yaml", "")
    default_conv = f"e2e-{case_id}"
    default_user = 1

    print(f"\n\n{'='*60}")
    print(f"[{scenario_yaml}] {scenario.get('name', '')}")
    print(f"{'='*60}")

    for i, turn in enumerate(scenario["turns"]):
        tool_log_before = len(tool_log)
        conv = turn.get("conversation_id", default_conv)
        user = turn.get("hub_user_id", default_user)

        print(f"\n  -- Turn {i + 1} [{conv}] --")
        print(f"  input: {turn['input']}")

        # 调 agent.run
        response_text = await agent.run(
            user_message=turn["input"],
            hub_user_id=user,
            conversation_id=conv,
        )
        print(f"  response: {(response_text or '')[:200]!r}")

        # 读 LangGraph checkpoint snapshot
        config = build_langgraph_config(
            conversation_id=conv, hub_user_id=user
        )
        snapshot = await agent.compiled_graph.aget_state(config)
        state_values: dict = snapshot.values if snapshot else {}

        intent_raw = state_values.get("intent")
        intent_str = intent_raw.value if hasattr(intent_raw, "value") else str(intent_raw)
        tool_log_slice = tool_log[tool_log_before:]
        tool_counts = Counter(name for name, _ in tool_log_slice)
        print(f"  intent: {intent_str}  tools: {dict(tool_counts)}")

        # 断言
        await assert_scenario_turn(
            agent=agent,
            gate=gate,
            tool_log=tool_log,
            tool_log_before=tool_log_before,
            turn=turn,
            response_text=response_text,
            state_values=state_values,
            conv_id=conv,
            hub_user_id=user,
        )
        print(f"  [PASS]")

    print(f"\n[{scenario_yaml}] ALL TURNS PASSED")


# ──────────────────────────────────────────────────────────────────────────────
# 保留原有 story_1 单测（兼容 Phase 1 baseline）
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_story_1_chat():
    """保留 Phase 1 baseline：用 router + chat_subgraph 直接测，不经 GraphAgent。"""
    from hub.agent.graph.state import AgentState, Intent
    from hub.agent.graph.router import router_node
    from hub.agent.graph.subgraphs.chat import chat_subgraph
    from hub.agent.llm_client import DeepSeekLLMClient

    scenario = yaml.safe_load(
        (SCENARIOS_DIR / "story1_chat.yaml").read_text(encoding="utf-8")
    )
    llm = DeepSeekLLMClient(api_key=os.environ["DEEPSEEK_API_KEY"], model="deepseek-v4-flash")
    try:
        for turn in scenario["turns"]:
            state = AgentState(user_message=turn["input"], hub_user_id=1, conversation_id="c1")
            await router_node(state, llm=llm)
            assert state.intent.value == turn["expected_intent"]
            await chat_subgraph(state, llm=llm)
            for forbidden in turn.get("forbid", []):
                assert forbidden not in (state.final_response or ""), (
                    f"chat 回复不应含 {forbidden!r}：{state.final_response}"
                )
    finally:
        await llm.aclose()
