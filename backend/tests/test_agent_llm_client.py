"""Plan 6 Task 6：AgentLLMClient 测试（3 case）。

用 httpx.MockTransport 模拟 LLM 响应，不走真实网络。
"""
from __future__ import annotations

import json

import httpx
import pytest
from httpx import MockTransport, Response

from hub.agent.llm_client import AgentLLMClient
from hub.agent.types import AgentLLMResponse, ToolCall
from hub.capabilities.deepseek import LLMServiceError


def _make_client(handler) -> AgentLLMClient:
    return AgentLLMClient(
        api_key="sk-test",
        base_url="https://api.test.com/v1",
        model="test-model",
        transport=MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_chat_parses_tool_calls():
    """MockTransport 返 tool_calls → AgentLLMResponse.tool_calls 长 1，字段正确。"""
    def handler(req: httpx.Request) -> Response:
        return Response(200, json={
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "search_products",
                            "arguments": json.dumps({"keyword": "手机"}),
                        },
                    }],
                },
            }],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 30,
            },
        })

    client = _make_client(handler)
    resp = await client.chat(
        messages=[{"role": "user", "content": "查手机"}],
        tools=[{
            "type": "function",
            "function": {"name": "search_products", "description": "搜索商品", "parameters": {}},
        }],
    )

    assert isinstance(resp, AgentLLMResponse)
    assert resp.is_tool_call is True
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert isinstance(tc, ToolCall)
    assert tc.id == "call_abc123"
    assert tc.name == "search_products"
    assert tc.args == {"keyword": "手机"}
    assert resp.usage_prompt_tokens == 100
    assert resp.usage_completion_tokens == 30
    assert resp.text is None

    await client.aclose()


@pytest.mark.asyncio
async def test_chat_parses_text_response():
    """MockTransport 返 content text → AgentLLMResponse.text 正确，is_tool_call=False。"""
    def handler(req: httpx.Request) -> Response:
        return Response(200, json={
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "这是 LLM 的回复",
                },
            }],
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 10,
            },
        })

    client = _make_client(handler)
    resp = await client.chat(messages=[{"role": "user", "content": "你好"}])

    assert resp.text == "这是 LLM 的回复"
    assert resp.is_tool_call is False
    assert resp.tool_calls == []
    assert resp.usage_prompt_tokens == 50
    assert resp.usage_completion_tokens == 10
    assert resp.raw_message is not None

    await client.aclose()


@pytest.mark.asyncio
async def test_chat_5xx_raises_llm_service_error():
    """LLM 返 5xx → 抛 LLMServiceError，不应返回 AgentLLMResponse。"""
    def handler(req: httpx.Request) -> Response:
        return Response(503, text="Service Unavailable")

    client = _make_client(handler)
    with pytest.raises(LLMServiceError):
        await client.chat(messages=[{"role": "user", "content": "测试"}])

    await client.aclose()
