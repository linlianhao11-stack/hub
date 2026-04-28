import httpx
import pytest
from httpx import MockTransport, Response


@pytest.mark.asyncio
async def test_qwen_uses_dashscope_compatible_endpoint():
    from hub.capabilities.qwen import QwenProvider

    captured = {}

    def handler(req: httpx.Request) -> Response:
        captured["url"] = str(req.url)
        captured["body"] = req.content
        return Response(200, json={
            "choices": [{"message": {"content": "ok"}}],
        })

    p = QwenProvider(
        api_key="sk-q", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus", transport=MockTransport(handler),
    )
    out = await p.chat(messages=[{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert "dashscope" in captured["url"]


@pytest.mark.asyncio
async def test_qwen_capability_type_correct():
    from hub.capabilities.qwen import QwenProvider
    p = QwenProvider(api_key="x", base_url="x", model="x")
    assert p.capability_type == "ai"
    assert p.provider_name == "qwen"


@pytest.mark.asyncio
async def test_qwen_parse_intent_returns_dict():
    from hub.capabilities.qwen import QwenProvider

    def handler(req):
        return Response(200, json={
            "choices": [{"message": {"content": '{"intent_type":"x","fields":{},"confidence":0.5}'}}],
        })
    p = QwenProvider(api_key="k", base_url="http://x", model="m",
                     transport=MockTransport(handler))
    out = await p.parse_intent("test", {})
    assert out["intent_type"] == "x"
