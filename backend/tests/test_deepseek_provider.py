import json

import httpx
import pytest
from httpx import MockTransport, Response


@pytest.mark.asyncio
async def test_chat_calls_openai_compatible_endpoint():
    from hub.capabilities.deepseek import DeepSeekProvider

    captured = {}

    def handler(req: httpx.Request) -> Response:
        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content)
        captured["auth"] = req.headers.get("authorization")
        return Response(200, json={
            "choices": [{"message": {"content": "回答"}}],
        })

    p = DeepSeekProvider(
        api_key="sk-test", base_url="https://api.deepseek.com/v1",
        model="deepseek-chat", transport=MockTransport(handler),
    )
    out = await p.chat(messages=[{"role": "user", "content": "hi"}])
    assert out == "回答"
    assert "chat/completions" in captured["url"]
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_parse_intent_returns_dict():
    from hub.capabilities.deepseek import DeepSeekProvider
    parsed_json = '{"intent_type":"query_product","fields":{"sku_or_keyword":"SKU100"},"confidence":0.85}'

    def handler(req):
        return Response(200, json={
            "choices": [{"message": {"content": parsed_json}}],
        })
    p = DeepSeekProvider(api_key="k", base_url="http://x", model="m",
                         transport=MockTransport(handler))
    schema = {"intent_type": "string", "fields": "object", "confidence": "float"}
    out = await p.parse_intent("查 SKU100", schema)
    assert out["intent_type"] == "query_product"
    assert out["confidence"] == 0.85


@pytest.mark.asyncio
async def test_parse_intent_handles_invalid_json():
    """LLM 返回非 JSON → 抛 LLMParseError。"""
    from hub.capabilities.deepseek import DeepSeekProvider, LLMParseError

    def handler(req):
        return Response(200, json={
            "choices": [{"message": {"content": "Sorry, I can't parse"}}],
        })
    p = DeepSeekProvider(api_key="k", base_url="http://x", model="m",
                         transport=MockTransport(handler))
    with pytest.raises(LLMParseError):
        await p.parse_intent("xxx", {})


@pytest.mark.asyncio
async def test_5xx_raises():
    from hub.capabilities.deepseek import DeepSeekProvider, LLMServiceError

    def handler(req):
        return Response(503)
    p = DeepSeekProvider(api_key="k", base_url="http://x", model="m",
                         transport=MockTransport(handler))
    with pytest.raises(LLMServiceError):
        await p.chat(messages=[{"role": "user", "content": "hi"}])
