"""Plan 6 Task 6：ChainAgent 用的 LLM 客户端封装。

复用 hub.capabilities.deepseek._OpenAICompatibleProvider 的 httpx + 鉴权逻辑，
扩展支持 OpenAI tools=API 的 tool_calls 解析。
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from enum import Enum
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


# ====================================================================
# === DeepSeekLLMClient (Plan 6 v9 GraphAgent — Task 0.4) ===
# ====================================================================
"""DeepSeekLLMClient — beta endpoint + prefix + strict + thinking + cache + 600s + 指数退避 + 5 finish_reason + 按 tool 类型 fallback。

Spec ref：§1.1 / §1.2 / §1.5 / §1.6 / §1.7 / §1.8 / §1.10 / §12.1

注意：保留作为 GraphAgent 的 LLM 适配层，**不**改用 langchain.ChatOpenAI 默认封装。
LangChain 默认不暴露 prefix / strict / thinking / cache usage / finish_reason 语义。
"""

DEEPSEEK_BETA_URL = "https://api.deepseek.com/beta"
DEEPSEEK_MAIN_URL = "https://api.deepseek.com"

# 退避序列 — 高负载下避免快速重试加剧 429 / insufficient_system_resource
DEFAULT_BACKOFF_SECONDS = (1.5, 5, 15, 60)
DEFAULT_TIMEOUT_SECONDS = 600  # DeepSeek 动态速率，10 分钟 keep-alive


class ToolClass(str, Enum):
    """tool 类型分级 — fallback 协议按这个分（spec §12.1）。"""
    READ = "read"      # search_*/get_*/check_*/analyze_* — 幂等查询
    WRITE = "write"    # generate_*/adjust_*/create_*/_request — 有副作用


class LLMFallbackError(Exception):
    """写 tool 路径上 strict / beta 失败时 fail closed（spec §12.1）。"""


# 注意：CrossContextClaim 属于 ConfirmGate 安全边界，**不**在 llm_client 定义。
# 见 Task 0.5：在 hub/agent/tools/confirm_gate.py 唯一定义。


def disable_thinking() -> dict:
    """所有非 thinking 节点必须传这个（DeepSeek thinking 默认 enabled，spec §1.5）。"""
    return {"type": "disabled"}


def enable_thinking() -> dict:
    return {"type": "enabled"}


@dataclass
class LLMResponse:
    text: str
    finish_reason: str
    tool_calls: list[dict]
    cache_hit_rate: float
    usage: dict
    raw: dict


class DeepSeekLLMClient:
    """Plan 6 GraphAgent 用的 LLM client。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-v4-flash",
        base_url: str = DEEPSEEK_BETA_URL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = 4,
        backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._http = httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self):
        await self._http.aclose()

    async def chat(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | dict = "auto",
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        thinking: dict | None = None,
        tool_class: ToolClass | None = None,
        prefix_assistant: str | None = None,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if stop:
            body["stop"] = stop
        body["thinking"] = thinking if thinking is not None else disable_thinking()
        if prefix_assistant is not None:
            body["messages"] = [
                *body["messages"],
                {"role": "assistant", "content": prefix_assistant, "prefix": True},
            ]
        return await self._call_with_retry(body=body, tool_class=tool_class)

    async def _call_with_retry(
        self, *, body: dict, tool_class: ToolClass | None,
    ) -> LLMResponse:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return await self._call_once(body=body)
            except _RetryableError as e:
                last_exc = e
                if attempt + 1 < self.max_retries:
                    wait = self.backoff_seconds[min(attempt, len(self.backoff_seconds) - 1)]
                    logger.warning(
                        "DeepSeek retry attempt=%d wait=%.1fs reason=%s",
                        attempt + 1, wait, e,
                    )
                    await asyncio.sleep(wait)
                    continue
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400 and "strict" in (e.response.text or "").lower():
                    if tool_class == ToolClass.WRITE:
                        logger.error("strict 校验失败 write tool path → fail closed: %s", e.response.text)
                        raise LLMFallbackError(f"strict schema 校验失败：{e.response.text}") from e
                raise
        if last_exc is None:
            raise RuntimeError("retry 循环异常")
        if tool_class == ToolClass.WRITE:
            raise LLMFallbackError(f"达到 max_retries 仍失败：{last_exc}") from last_exc
        raise last_exc

    async def _call_once(self, *, body: dict) -> LLMResponse:
        try:
            resp = await self._http.post(
                url=f"{self.base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
        except httpx.TimeoutException as e:
            raise _RetryableError(f"timeout: {e}") from e
        except httpx.TransportError as e:
            raise _RetryableError(f"transport: {e}") from e
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code in (408, 425, 429) or 500 <= code < 600:
                raise _RetryableError(f"{code}: {e.response.text}") from e
            raise

        data = resp.json()
        choice = data["choices"][0]
        message = choice["message"]
        finish_reason = choice.get("finish_reason", "stop")

        if finish_reason == "insufficient_system_resource":
            raise _RetryableError("insufficient_system_resource")
        if finish_reason == "content_filter":
            logger.warning("content_filter 拦截：messages=%s", body.get("messages"))
        if finish_reason == "length":
            logger.warning("撞 max_tokens — 截断告警，考虑缩短 prompt 或调大 max_tokens")

        usage = data.get("usage", {})
        hit = usage.get("prompt_cache_hit_tokens", 0)
        miss = usage.get("prompt_cache_miss_tokens", 0)
        cache_hit_rate = hit / max(hit + miss, 1)

        return LLMResponse(
            text=message.get("content") or "",
            finish_reason=finish_reason,
            tool_calls=message.get("tool_calls") or [],
            cache_hit_rate=cache_hit_rate,
            usage=usage,
            raw=data,
        )


class _RetryableError(Exception):
    """内部用 — 标记可退避重试的错误。"""
