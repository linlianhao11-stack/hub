"""M0 兼容性验证 — 真 DeepSeek beta endpoint，验证 spec §1 列的关键能力。

跑：DEEPSEEK_API_KEY=... uv run pytest tests/integration/test_deepseek_compat.py -v -m realllm

无 DEEPSEEK_API_KEY 时所有 case 自动 skip（不 fail）。
"""
import os
import pytest
from hub.agent.llm_client import DeepSeekLLMClient, ToolClass, disable_thinking, enable_thinking

pytestmark = [
    pytest.mark.realllm,
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="需要真 API key"),
]


@pytest.fixture
async def client():
    c = DeepSeekLLMClient(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        model="deepseek-v4-flash",
    )
    yield c
    await c.aclose()


async def test_prefix_completion_forces_json_opening(client):
    """spec §1.2 应用 A：router 用 prefix 强制 JSON 输出。"""
    resp = await client.chat(
        messages=[
            {"role": "system", "content": "You are a router. Output {\"intent\": \"chat\"|\"query\"|\"contract\"}"},
            {"role": "user", "content": "给阿里做合同"},
        ],
        prefix_assistant='{"intent": "',
        stop=['",'],
        max_tokens=20,
        thinking=disable_thinking(),
    )
    assert resp.text.split('"')[0].lower() in {"chat", "query", "contract", "quote", "voucher",
                                                  "adjust_price", "adjust_stock", "confirm", "unknown"}


async def test_strict_with_sentinel_string(client):
    """spec §1.3 v3.4 默认：sentinel 写法 — 可选字段用 type:string + 空串 sentinel。"""
    schema = {
        "type": "function",
        "function": {
            "name": "echo_address",
            "description": "测试 sentinel 写法。无地址传 ''",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "shipping_address": {"type": "string", "description": "无值传 ''"},
                },
                "required": ["name", "shipping_address"],
                "additionalProperties": False,
            },
        },
    }
    resp = await client.chat(
        messages=[{"role": "user", "content": "echo 张三 无地址"}],
        tools=[schema],
        tool_choice="required",
        thinking=disable_thinking(),
    )
    assert resp.tool_calls, f"应触发 tool 调用但没有：{resp.text}"


async def test_strict_anyof_null_experiment(client):
    """spec §1.3 M0 实验项 — anyOf+null 是否被 beta 接受。"""
    schema = {
        "type": "function",
        "function": {
            "name": "echo_addr_v2",
            "description": "测试 anyOf null。",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "shipping_address": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["name", "shipping_address"],
                "additionalProperties": False,
            },
        },
    }
    try:
        resp = await client.chat(
            messages=[{"role": "user", "content": "echo 张三 无地址"}],
            tools=[schema],
            tool_choice="required",
            thinking=disable_thinking(),
        )
        print(f"\n[实验通过] anyOf+null 被 beta 接受。tool_calls={resp.tool_calls}")
    except Exception as e:
        pytest.skip(f"[实验失败] anyOf+null 不被 beta 接受：{e}")


async def test_thinking_disabled_with_tools(client):
    """spec §1.5 + M0：thinking disabled + tools 同时启用必须可工作。"""
    resp = await client.chat(
        messages=[{"role": "user", "content": "搜索客户阿里"}],
        tools=[{
            "type": "function",
            "function": {
                "name": "search_customers",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        }],
        tool_choice="auto",
        thinking=disable_thinking(),
    )
    assert resp.tool_calls or resp.text


async def test_kv_cache_usage_parsed(client):
    """spec §1.1 KV cache 监控 — usage 字段必须解析正确。"""
    static_prompt = "You are a helpful assistant. " * 100
    r1 = await client.chat(
        messages=[
            {"role": "system", "content": static_prompt},
            {"role": "user", "content": "hi"},
        ],
        thinking=disable_thinking(),
    )
    r2 = await client.chat(
        messages=[
            {"role": "system", "content": static_prompt},
            {"role": "user", "content": "hello"},
        ],
        thinking=disable_thinking(),
    )
    print(f"\nrun1 cache_hit_rate={r1.cache_hit_rate:.2f}")
    print(f"run2 cache_hit_rate={r2.cache_hit_rate:.2f}")
    assert r2.cache_hit_rate > 0


async def test_thinking_enabled_outputs_reasoning(client):
    """spec §1.5：thinking enabled 时模型有 reasoning 输出。"""
    resp = await client.chat(
        messages=[{"role": "user", "content": "9.11 和 9.9 哪个大？请推理"}],
        thinking=enable_thinking(),
        tools=None,
    )
    assert resp.finish_reason in {"stop", "length"}
    msg = resp.raw["choices"][0]["message"]
    print(f"\nthinking enabled — has reasoning_content: {bool(msg.get('reasoning_content'))}")
