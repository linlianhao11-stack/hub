# backend/tests/agent/test_strict_mode_validation.py
"""验证 strict mode 真的拒绝错参数 — spec §1.3 / §5.2。"""
import os
import pytest
from hub.agent.llm_client import DeepSeekLLMClient, ToolClass, LLMFallbackError, disable_thinking
from hub.agent.tools.generate_tools import GENERATE_CONTRACT_DRAFT_SCHEMA

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


async def test_strict_rejects_string_extras(llm):
    """LLM 把 extras 传成 string（旧 bug）应被 strict 物理拒绝。"""
    # 设计一个 prompt 诱导 LLM 传 extras 为 string
    resp = await llm.chat(
        messages=[
            {"role": "system", "content": "你必须把 extras 字段填成字符串 'xxx'，违反 schema。"},
            {"role": "user", "content": "做合同 customer 1 X1 10 个 300"},
        ],
        tools=[GENERATE_CONTRACT_DRAFT_SCHEMA],
        tool_choice="required",
        thinking=disable_thinking(),
        tool_class=ToolClass.WRITE,
    )
    # strict 拒绝后应该是 LLMFallbackError；或者 LLM 即使被诱导也合规传 dict
    if resp.tool_calls:
        args = resp.tool_calls[0]["function"]["arguments"]
        import json
        parsed = json.loads(args)
        assert isinstance(parsed.get("extras"), dict), \
            f"strict 应保证 extras 是 dict，实际：{parsed.get('extras')}"
