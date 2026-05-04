"""ReAct agent 真 LLM eval（按 ReAct plan-then-execute 流程设计的新 fixture）。

跑：DEEPSEEK_API_KEY=xxx pytest -m realllm tests/react/test_realllm_eval.py
"""
import os
import pytest
import yaml
from pathlib import Path

REACT_SCENARIOS = (
    Path(__file__).parent / "fixtures" / "scenarios" / "realllm"
)


@pytest.fixture(scope="session", autouse=True)
def _enforce_release_gate_or_skip():
    """release gate 模式下没 DEEPSEEK_API_KEY 必须 fail（不能 skip 当绿）。"""
    is_release_gate = os.environ.get("HUB_REACT_RELEASE_GATE") == "1"
    has_key = bool(os.environ.get("DEEPSEEK_API_KEY"))
    if is_release_gate and not has_key:
        pytest.fail(
            "release gate 模式 (HUB_REACT_RELEASE_GATE=1) 必须设 DEEPSEEK_API_KEY,"
            "不允许 skipped 当绿"
        )


def _scenario_files() -> list[str]:
    return sorted(p.name for p in REACT_SCENARIOS.glob("story*.yaml"))


@pytest.mark.realllm
def test_eval_has_minimum_scenario_count():
    """release gate 边界：fixture 数量必须 >= 6（钉钉实测覆盖度下限）。"""
    files = _scenario_files()
    assert len(files) >= 6, (
        f"ReAct 真 LLM eval fixture 数量不足: {len(files)} < 6\n"
        f"现有: {files}\n钉钉实测覆盖度下限是 6 个 story（chat / query / contract /"
        f" reuse / missing / switch）"
    )


@pytest.mark.realllm
@pytest.mark.parametrize("yaml_file", _scenario_files())
@pytest.mark.asyncio
async def test_realllm_react_scenario(yaml_file, real_react_agent_factory):
    """每个 turn 检查：
      - expected_tool_calls_at_least: tool 被调 >=1 次
      - expected_tool_calls_zero: tool 必须 0 次
      - final_message_contains_any: 最终自然语言回复含其一
    """
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip(
            "无 DEEPSEEK_API_KEY (dev 模式) — release gate 设 "
            "HUB_REACT_RELEASE_GATE=1 严格化（缺 key 时 fail 不 skip）"
        )

    scenario = yaml.safe_load(
        (REACT_SCENARIOS / yaml_file).read_text(encoding="utf-8"),
    )
    agent, tool_log = real_react_agent_factory

    case_id = yaml_file.replace(".yaml", "")
    conv_id = f"react-eval-{case_id}"
    user_id = 1

    for i, turn in enumerate(scenario["turns"], 1):
        before = len(tool_log)
        reply = await agent.run(
            user_message=turn["input"],
            hub_user_id=user_id, conversation_id=conv_id,
            acting_as=None, channel_userid="test",
        )
        turn_tool_calls = [n for n, _ in tool_log[before:]]

        for must_have in turn.get("expected_tool_calls_at_least", []):
            assert must_have in turn_tool_calls, (
                f"{yaml_file} turn {i}: 期望调到 {must_have},实际本轮 tools={turn_tool_calls}, "
                f"reply={reply!r}"
            )
        for must_zero in turn.get("expected_tool_calls_zero", []):
            assert must_zero not in turn_tool_calls, (
                f"{yaml_file} turn {i}: 不应调 {must_zero},实际本轮 tools={turn_tool_calls}"
            )
        if turn.get("final_message_contains_any"):
            assert any(s in (reply or "") for s in turn["final_message_contains_any"]), (
                f"{yaml_file} turn {i}: reply 不含任一关键词 {turn['final_message_contains_any']}, "
                f"实际 reply={reply!r}"
            )
