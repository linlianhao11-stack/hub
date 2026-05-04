"""HUB 配置（基础部署 secret + 运行时常量）。

业务 secret（钉钉 AppSecret / ERP ApiKey / AI Key 等）**不**在这里——
它们走 Web UI + 数据库加密存储（见 hub.crypto + hub.models.config）。
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        env_prefix="HUB_",
        case_sensitive=False,
        extra="ignore",
    )

    # --- 部署级 secret（必填）---
    database_url: str = Field(..., description="HUB Postgres 连接字符串")
    redis_url: str = Field(..., description="HUB Redis 连接字符串")
    master_key: str = Field(..., description="32 字节 hex（64 字符）AES-GCM 主密钥")

    # --- 运行时常量 ---
    gateway_port: int = Field(default=8091)
    log_level: str = Field(default="info")
    timezone: str = Field(default="Asia/Shanghai")

    # --- 一次性初始化（启动时由 hub 自动生成或运维显式指定）---
    setup_token: str | None = Field(default=None)
    setup_token_ttl_seconds: int = Field(default=1800)

    # --- TTL 配置 ---
    task_payload_ttl_days: int = Field(default=30)
    task_log_ttl_days: int = Field(default=365)

    # --- 紧急运维 ApiKey ---
    admin_key: str | None = Field(default=None, description="紧急 admin API Key（运维专用）")

    # --- ERP → HUB 反向回调共享密钥 ---
    erp_to_hub_secret: str | None = Field(
        default=None, description="ERP 调 HUB confirm-final 时 X-ERP-Secret 头共享密钥",
    )

    @field_validator("master_key")
    @classmethod
    def validate_master_key(cls, v: str) -> str:
        if len(v) != 64:
            raise ValueError("HUB_MASTER_KEY 必须为 64 位 hex 字符（32 字节）")
        try:
            bytes.fromhex(v)
        except ValueError:
            raise ValueError("HUB_MASTER_KEY 不是合法 hex")
        return v

    @property
    def master_key_bytes(self) -> bytes:
        return bytes.fromhex(self.master_key)


# 模块级单例（懒初始化）
_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局 Settings 单例。每次启动只读一次环境变量。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
