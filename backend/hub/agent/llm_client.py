"""Plan 6 Task 6：ChainAgent 用的 LLM 客户端封装。

复用 hub.capabilities.deepseek._OpenAICompatibleProvider 的 httpx + 鉴权逻辑，
扩展支持 OpenAI tools=API 的 tool_calls 解析。
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from hub.agent.types import AgentLLMResponse, ToolCall
from hub.capabilities.deepseek import LLMParseError, LLMServiceError

logger = logging.getLogger("hub.agent.llm_client")


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

        try:
            r = await self._client.post(
                url, json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.RequestError as e:
            raise LLMServiceError(f"网络错误: {e}") from e

        if r.status_code >= 500:
            raise LLMServiceError(f"LLM {r.status_code}")
        if r.status_code >= 400:
            raise LLMServiceError(f"LLM {r.status_code}: {r.text[:200]}")

        try:
            return self._parse_response(r.json())
        except (KeyError, ValueError, TypeError) as e:
            raise LLMParseError(f"LLM 返回格式异常: {e}") from e

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
