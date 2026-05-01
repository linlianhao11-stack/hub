"""Plan 6 Task 6：ChainAgent 用的 LLM 客户端封装。

复用 hub.capabilities.deepseek._OpenAICompatibleProvider 的 httpx + 鉴权逻辑，
扩展支持 OpenAI tools=API 的 tool_calls 解析。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from hub.agent.types import AgentLLMResponse, ToolCall
from hub.capabilities.deepseek import LLMParseError, LLMServiceError

logger = logging.getLogger("hub.agent.llm_client")

# v8 staging review：DeepSeek 偶发 400 Bad Request（实测同请求重发就成功），
# 加 1 次重试覆盖该抽风。429 限流 / 5xx 服务端错也走重试（标准做法）。
# 401/403 不重试（auth 错误重试无意义）。
_RETRYABLE_STATUS = {400, 408, 425, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 2
_RETRY_BACKOFF_SECONDS = 1.5


class AgentLLMClient:
    """LLM 客户端 wrapper，专为 ChainAgent 设计。

    依赖：注入 OpenAI 兼容的 httpx async client + api_key + base_url + model。
    职责：
    - 调 chat completions API 时传 tools= 参数
    - 解析返回值成 AgentLLMResponse（含 tool_calls / usage）
    """

    def __init__(self, *, api_key: str, base_url: str, model: str,
                 timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def aclose(self):
        await self._client.aclose()

    async def chat(self, messages: list[dict], *,
                   tools: list[dict] | None = None,
                   temperature: float = 0.0,
                   **kwargs) -> AgentLLMResponse:
        """调 OpenAI 兼容 chat completions API，返结构化响应。

        Args:
            messages: list of {role, content, ...}
            tools: list of {type:"function", function:{name, description, parameters}}（OpenAI 格式）
            temperature: 默认 0.0（spec §3.5）
            **kwargs: 透传额外参数（max_tokens / top_p 等）
        """
        url = f"{self.base_url}/chat/completions"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                r = await self._client.post(
                    url, json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            except httpx.RequestError as e:
                # 网络错误（连接超时 / DNS / TLS 等）→ retryable
                last_error = LLMServiceError(f"网络错误: {e}")
                if attempt + 1 < _MAX_ATTEMPTS:
                    logger.warning(
                        "LLM 网络错误，将在 %.1fs 后重试 (attempt %d/%d): %s",
                        _RETRY_BACKOFF_SECONDS, attempt + 1, _MAX_ATTEMPTS, e,
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise last_error from e

            if r.status_code in _RETRYABLE_STATUS:
                # 偶发性服务端错（含 DeepSeek 偶发 400 / 限流 / 5xx）→ retry
                # **不要**把 r.text 写进 last_error 默认 message——可能含敏感信息；
                # 只记 status + 截断 body 到 logger（warn 级别）
                body_preview = r.text[:300] if r.text else ""
                last_error = LLMServiceError(f"LLM {r.status_code}")
                if attempt + 1 < _MAX_ATTEMPTS:
                    logger.warning(
                        "LLM %d，将在 %.1fs 后重试 (attempt %d/%d) body=%r",
                        r.status_code, _RETRY_BACKOFF_SECONDS,
                        attempt + 1, _MAX_ATTEMPTS, body_preview,
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                # 重试用尽：抛错（仍不暴露 body 给上游）
                logger.error(
                    "LLM %d 重试用尽 body=%r", r.status_code, body_preview,
                )
                raise last_error

            if r.status_code >= 400:
                # 401/403/404 等定性错误：不重试，立即抛
                raise LLMServiceError(f"LLM {r.status_code}: {r.text[:200]}")

            try:
                return self._parse_response(r.json())
            except (KeyError, ValueError, TypeError) as e:
                raise LLMParseError(f"LLM 返回格式异常: {e}") from e

        # 防御兜底（理论上 for 循环里都会 return / raise，到这里说明逻辑漏洞）
        raise last_error or LLMServiceError("LLM 重试逻辑异常")

    @staticmethod
    def _parse_response(resp: dict) -> AgentLLMResponse:
        """从 OpenAI chat completions 响应解析成 AgentLLMResponse。"""
        choices = resp.get("choices") or []
        if not choices:
            raise LLMParseError("响应无 choices")
        msg = choices[0].get("message", {})

        text = msg.get("content")  # 可能为 None（纯 tool_calls 时）
        tool_calls_raw = msg.get("tool_calls") or []
        tool_calls: list[ToolCall] = []
        for tc in tool_calls_raw:
            tc_id = tc.get("id", "")
            fn = tc.get("function") or {}
            fn_name = fn.get("name", "")
            args_raw = fn.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                # LLM 返非法 JSON 视作单字段
                args = {"_raw": args_raw}
            if fn_name:
                tool_calls.append(ToolCall(id=tc_id, name=fn_name, args=args))

        usage = resp.get("usage") or {}
        return AgentLLMResponse(
            text=text,
            tool_calls=tool_calls,
            usage_prompt_tokens=int(usage.get("prompt_tokens") or 0),
            usage_completion_tokens=int(usage.get("completion_tokens") or 0),
            raw_message=msg,
        )
