import pytest
from unittest.mock import AsyncMock, patch
from hub.agent.llm_client import (
    DeepSeekLLMClient,
    disable_thinking,
    LLMFallbackError,
)


def test_disable_thinking_helper():
    assert disable_thinking() == {"type": "disabled"}


def test_client_uses_beta_endpoint_by_default():
    client = DeepSeekLLMClient(api_key="x", model="deepseek-v4-flash")
    assert "beta" in client.base_url


def test_client_accepts_explicit_base_url():
    client = DeepSeekLLMClient(api_key="x", model="m", base_url="https://override")
    assert client.base_url == "https://override"


@pytest.mark.asyncio
async def test_chat_passes_thinking_disabled_when_requested():
    client = DeepSeekLLMClient(api_key="x", model="m")
    captured = {}

    async def fake_post(*, url, headers, json, timeout):
        captured.update(json)
        from httpx import Response, Request
        return Response(200, request=Request("POST", url), json={
            "choices": [{"message": {"content": '{"intent": "chat'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "prompt_cache_hit_tokens": 8, "prompt_cache_miss_tokens": 2},
        })

    with patch.object(client._http, "post", side_effect=fake_post):
        await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            thinking={"type": "disabled"},
            temperature=0.0,
        )
    assert captured["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_chat_records_cache_hit_rate():
    client = DeepSeekLLMClient(api_key="x", model="m")
    async def fake_post(*, url, headers, json, timeout):
        from httpx import Response, Request
        return Response(200, request=Request("POST", url), json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                      "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20},
        })
    with patch.object(client._http, "post", side_effect=fake_post):
        resp = await client.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.cache_hit_rate == 0.8


@pytest.mark.asyncio
async def test_insufficient_system_resource_triggers_retry():
    client = DeepSeekLLMClient(api_key="x", model="m", max_retries=3,
                                  backoff_seconds=(0.0,))  # 加速测试
    call_count = 0

    async def fake_post(*, url, headers, json, timeout):
        nonlocal call_count
        call_count += 1
        from httpx import Response, Request
        if call_count < 3:
            return Response(200, request=Request("POST", url), json={
                "choices": [{"message": {"content": ""}, "finish_reason": "insufficient_system_resource"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 0,
                          "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10},
            })
        return Response(200, request=Request("POST", url), json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10},
        })

    with patch.object(client._http, "post", side_effect=fake_post):
        resp = await client.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.text == "ok" and call_count == 3


@pytest.mark.asyncio
async def test_fallback_protocol_write_tool_fails_closed():
    from hub.agent.llm_client import ToolClass
    client = DeepSeekLLMClient(api_key="x", model="m")
    async def raise_400(*a, **kw):
        from httpx import Response, Request, HTTPStatusError
        resp = Response(400, request=Request("POST", "u"),
                        json={"error": {"message": "strict schema violation"}})
        raise HTTPStatusError("400", request=resp.request, response=resp)
    with patch.object(client._http, "post", side_effect=raise_400):
        with pytest.raises(LLMFallbackError):
            await client.chat(
                messages=[{"role": "user", "content": "x"}],
                tool_class=ToolClass.WRITE,
                tools=[{"type": "function", "function": {"name": "f", "strict": True,
                                                          "parameters": {"type": "object",
                                                                          "properties": {},
                                                                          "required": [],
                                                                          "additionalProperties": False}}}],
            )
