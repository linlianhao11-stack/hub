"""ReAct agent yaml 场景测试 — mock LLM 版本（deterministic 路径）。

真 LLM eval 在 test_realllm_eval.py（@pytest.mark.realllm）。
本 task **smoke level**——确保 yaml 能 parse + test 不抛。
完整 mock 留给真 LLM eval（Task 5.3）+ fake chat model e2e（Task 5.1.5）。
"""
import pytest
from pathlib import Path
import yaml

SCENARIOS_DIR = Path(__file__).parent / "fixtures" / "scenarios"


@pytest.mark.parametrize("yaml_file", sorted(p.name for p in SCENARIOS_DIR.glob("*.yaml")))
@pytest.mark.asyncio
async def test_react_scenario(yaml_file):
    """每个 yaml fixture 校验合法 + 必填 schema 字段都在。

    断言：
      - name 字段存在
      - turns 字段存在且非空
      - 每个 turn 至少有 input
    """
    scenario = yaml.safe_load((SCENARIOS_DIR / yaml_file).read_text(encoding="utf-8"))

    assert scenario.get("name"), f"{yaml_file} 缺 name 字段"
    turns = scenario.get("turns")
    assert turns, f"{yaml_file} 缺 turns 字段或为空"
    for i, turn in enumerate(turns):
        assert turn.get("input"), f"{yaml_file} turn[{i}] 缺 input"
