"""Plan 6 v9 Task 5.7：写 tool fail closed / 读 tool 可降级。spec §12.1。

写 tool path：
- strict 400 → LLMFallbackError + metric
- max_retries 耗尽 → LLMFallbackError + metric

读 tool path：
- strict 400 → 原样 raise HTTPStatusError（让上层 caller 决定降级）
- 不在 client 内部默默吞掉
"""
from __future__ import annotations
import pytest
from unittest.mock import patch
import httpx

from hub.agent.llm_client import DeepSeekLLMClient, ToolClass, LLMFallbackError


def _build_400_strict_error():
    """构造一个 strict-violation 的 HTTPStatusError（response.text 含 'strict'）。"""
    request = httpx.Request("POST", "https://api.deepseek.com/beta/v1/chat/completions")
    response = httpx.Response(
        400, request=request,
        content=b'{"error": {"message": "strict schema validation failed: foo"}}',
    )
    return httpx.HTTPStatusError("400", request=request, response=response)


@pytest.mark.asyncio
async def test_write_tool_strict_400_fails_closed():
    """写 tool path strict 400 → LLMFallbackError（不能默默继续 / 重试）。"""
    client = DeepSeekLLMClient(api_key="x", model="deepseek-v4-flash")

    async def fake_post(*a, **kw):
        raise _build_400_strict_error()

    with patch.object(client._http, "post", side_effect=fake_post):
        with pytest.raises(LLMFallbackError):
            await client.chat(
                messages=[{"role": "user", "content": "x"}],
                tool_class=ToolClass.WRITE,
                tools=[{"type": "function", "function": {"name": "x", "strict": True,
                        "parameters": {"type": "object", "properties": {},
                                       "required": [], "additionalProperties": False}}}],
            )

    await client.aclose()


@pytest.mark.asyncio
async def test_read_tool_strict_400_raises_http_status_error_not_fallback():
    """读 tool path strict 400 → 原样 raise HTTPStatusError（让上层降级），
    **不**封装成 LLMFallbackError。spec §12.1。"""
    client = DeepSeekLLMClient(api_key="x", model="deepseek-v4-flash")

    async def fake_post(*a, **kw):
        raise _build_400_strict_error()

    with patch.object(client._http, "post", side_effect=fake_post):
        with pytest.raises(httpx.HTTPStatusError):  # 不是 LLMFallbackError
            await client.chat(
                messages=[{"role": "user", "content": "x"}],
                tool_class=ToolClass.READ,
                tools=[{"type": "function", "function": {"name": "x", "strict": True,
                        "parameters": {"type": "object", "properties": {},
                                       "required": [], "additionalProperties": False}}}],
            )

    await client.aclose()


@pytest.mark.asyncio
async def test_write_tool_fallback_alarm_metric_written(monkeypatch):
    """写 tool path fallback 必须打 hub.metrics.incr('llm.fallback', tags={'tool_class':'write'})
    便于触发告警（fallback 计数 > 0 是上线必查的健康指标）。"""
    captured: list[tuple[str, dict]] = []

    def fake_incr(name, **kw):
        captured.append((name, kw))

    monkeypatch.setattr("hub.metrics.incr", fake_incr)

    client = DeepSeekLLMClient(api_key="x", model="deepseek-v4-flash")

    async def fake_post(*a, **kw):
        raise _build_400_strict_error()

    with patch.object(client._http, "post", side_effect=fake_post):
        with pytest.raises(LLMFallbackError):
            await client.chat(
                messages=[{"role": "user", "content": "x"}],
                tool_class=ToolClass.WRITE,
                tools=[{"type": "function", "function": {"name": "x", "strict": True,
                        "parameters": {"type": "object", "properties": {},
                                       "required": [], "additionalProperties": False}}}],
            )

    assert any(
        name == "llm.fallback"
        and kw.get("tags", {}).get("tool_class") == "write"
        for name, kw in captured
    ), f"未打 metric：{captured}"

    await client.aclose()
