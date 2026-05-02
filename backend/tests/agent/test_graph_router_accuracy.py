# backend/tests/agent/test_graph_router_accuracy.py
"""Router 50 case 准确率测试 — target ≥ 95%。spec §6.2 / §10.1。

跑：pytest tests/agent/test_graph_router_accuracy.py -v -m realllm
"""
import os
import yaml
import pytest
from pathlib import Path
from hub.agent.graph.router import router_node
from hub.agent.graph.state import AgentState, Intent
from hub.agent.llm_client import DeepSeekLLMClient

CASES_PATH = Path(__file__).parent / "fixtures" / "router_50_cases.yaml"

pytestmark = [
    pytest.mark.realllm,
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="需要真 API key"),
]


@pytest.fixture
async def llm():
    c = DeepSeekLLMClient(api_key=os.environ["DEEPSEEK_API_KEY"], model="deepseek-v4-flash")
    yield c
    await c.aclose()


@pytest.mark.asyncio
async def test_router_accuracy_50_cases(llm):
    cases = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    assert len(cases) >= 50, f"需要 ≥ 50 个 case，当前 {len(cases)}"
    correct = 0
    failures = []
    for c in cases:
        state = AgentState(user_message=c["input"], hub_user_id=1, conversation_id="c1")
        out = await router_node(state, llm=llm)
        if out.intent.value == c["intent"]:
            correct += 1
        else:
            failures.append(f"  '{c['input']}' → 期望 {c['intent']}, 实际 {out.intent.value}")
    accuracy = correct / len(cases)
    if accuracy < 0.95:
        pytest.fail(f"准确率 {accuracy:.2%} < 95%。失败 case：\n" + "\n".join(failures))
    print(f"\nRouter 准确率 {accuracy:.2%} ({correct}/{len(cases)})")
