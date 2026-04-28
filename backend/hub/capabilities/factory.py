"""按 ai_provider 表的 provider_type 构造对应 Provider 实例。"""
from __future__ import annotations

from hub.capabilities.deepseek import DeepSeekProvider
from hub.capabilities.qwen import QwenProvider
from hub.crypto import decrypt_secret
from hub.models import AIProvider

_PROVIDERS = {
    "deepseek": DeepSeekProvider,
    "qwen": QwenProvider,
}


async def load_active_ai_provider():
    """从 ai_provider 表查 status=active 的第一条配置，构造 Provider 实例。"""
    record = await AIProvider.filter(status="active").first()
    if record is None:
        return None
    cls = _PROVIDERS.get(record.provider_type)
    if cls is None:
        raise ValueError(f"未知 AI provider_type: {record.provider_type}")
    api_key = decrypt_secret(record.encrypted_api_key, purpose="config_secrets")
    return cls(api_key=api_key, base_url=record.base_url, model=record.model)
