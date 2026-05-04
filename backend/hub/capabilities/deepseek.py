"""DeepSeekProvider：OpenAI 兼容 API。"""
from __future__ import annotations

import json

import httpx


class LLMServiceError(Exception):
    """LLM 服务侧异常（5xx / 网络错误）。"""


class LLMParseError(Exception):
    """LLM 返回内容无法解析为期望 schema。"""


class _OpenAICompatibleProvider:
    """OpenAI 兼容 chat completions 客户端基类。"""

    capability_type = "ai"
    provider_name = "base"

    def __init__(
        self, api_key: str, base_url: str, model: str,
        *, timeout: float = 60.0, transport: httpx.BaseTransport | None = None,
    ):
        # timeout=60s: 主对话路径正常 5-15s 完成，60s 对正常调用零影响；
        # MemoryWriter 抽事实场景 prompt 含 schema + 多条 tool_log 会触发慢调用，
        # 30s 不够（实测 ReadTimeout）。这里抬到 60s 兼顾两类调用。
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def aclose(self):
        await self._client.aclose()

    async def chat(self, messages: list[dict], **kwargs) -> str:
        url = f"{self.base_url}/chat/completions"
        body = {"model": self.model, "messages": messages, **kwargs}
        try:
            r = await self._client.post(
                url, json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.RequestError as e:
            raise LLMServiceError(f"网络错误: {e}") from e
        if r.status_code >= 500:
            raise LLMServiceError(f"{self.provider_name} {r.status_code}")
        if r.status_code >= 400:
            raise LLMServiceError(f"{self.provider_name} {r.status_code}: {r.text[:200]}")
        body = r.json()
        return body["choices"][0]["message"]["content"]

    async def parse_intent(self, text: str, schema: dict) -> dict:
        """schema-guided 意图解析：要求 LLM 返回纯 JSON。"""
        sys_msg = (
            "你是一个意图解析器。把用户输入解析成符合给定 schema 的 JSON 对象。"
            "**只返回 JSON，不要包含任何解释或 markdown 标记。**"
            f"\nSchema 字段：{json.dumps(schema, ensure_ascii=False)}"
            "\n如果无法可靠解析，返回 {\"intent_type\":\"unknown\",\"fields\":{},\"confidence\":0.0}。"
        )
        content = await self.chat(messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": text},
        ])
        # 容错：剥离可能的 ```json 代码块标记
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise LLMParseError(f"LLM 返回非 JSON: {content[:200]}") from e


class DeepSeekProvider(_OpenAICompatibleProvider):
    provider_name = "deepseek"
