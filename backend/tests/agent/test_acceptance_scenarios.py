# backend/tests/agent/test_acceptance_scenarios.py
import os
import yaml
import pytest
from pathlib import Path
# 实际 agent 入口在 Phase 7 才完成；先用 router + 子图直接拼装

SCENARIOS_DIR = Path(__file__).parent / "fixtures" / "scenarios"

pytestmark = [
    pytest.mark.realllm,
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="需要真 API key"),
]


@pytest.mark.asyncio
async def test_story_1_chat():
    from hub.agent.graph.state import AgentState, Intent
    from hub.agent.graph.router import router_node
    from hub.agent.graph.subgraphs.chat import chat_subgraph
    from hub.agent.llm_client import DeepSeekLLMClient

    scenario = yaml.safe_load((SCENARIOS_DIR / "story1_chat.yaml").read_text(encoding="utf-8"))
    llm = DeepSeekLLMClient(api_key=os.environ["DEEPSEEK_API_KEY"], model="deepseek-v4-flash")
    try:
        for turn in scenario["turns"]:
            state = AgentState(user_message=turn["input"], hub_user_id=1, conversation_id="c1")
            await router_node(state, llm=llm)
            assert state.intent.value == turn["expected_intent"]
            await chat_subgraph(state, llm=llm)
            for forbidden in turn.get("forbid", []):
                assert forbidden not in (state.final_response or ""), \
                    f"chat 回复不应含 '{forbidden}'：{state.final_response}"
    finally:
        await llm.aclose()
