"""QwenProvider：通义千问 dashscope OpenAI 兼容模式。"""
from __future__ import annotations

from hub.capabilities.deepseek import _OpenAICompatibleProvider


class QwenProvider(_OpenAICompatibleProvider):
    provider_name = "qwen"
