import os
import pytest
from unittest.mock import patch


def test_config_requires_master_key():
    """缺 HUB_MASTER_KEY 必须启动失败并明确报错。"""
    with patch.dict(os.environ, {}, clear=True):
        os.environ["HUB_DATABASE_URL"] = "postgresql://x@y/z"
        os.environ["HUB_REDIS_URL"] = "redis://r:6379/0"
        os.environ.pop("HUB_MASTER_KEY", None)
        from hub.config import Settings
        with pytest.raises(Exception) as exc:
            Settings()
        assert "HUB_MASTER_KEY" in str(exc.value) or "master_key" in str(exc.value).lower()


def test_config_master_key_must_be_64_hex():
    """HUB_MASTER_KEY 必须是 64 位 hex（32 字节）。"""
    from hub.config import Settings
    with patch.dict(os.environ, {
        "HUB_DATABASE_URL": "postgresql://x@y/z",
        "HUB_REDIS_URL": "redis://r:6379/0",
        "HUB_MASTER_KEY": "tooshort",
    }):
        with pytest.raises(ValueError) as exc:
            Settings()
        assert "64" in str(exc.value) or "hex" in str(exc.value).lower()


def test_config_full_load():
    """完整 env 下能正常加载所有字段。"""
    from hub.config import Settings
    with patch.dict(os.environ, {
        "HUB_DATABASE_URL": "postgresql://hub@localhost/hub",
        "HUB_REDIS_URL": "redis://localhost:6379/0",
        "HUB_MASTER_KEY": "a" * 64,
        "HUB_GATEWAY_PORT": "8091",
        "HUB_LOG_LEVEL": "info",
        "HUB_TIMEZONE": "Asia/Shanghai",
    }):
        s = Settings()
        assert s.gateway_port == 8091
        assert s.log_level == "info"
        assert s.timezone == "Asia/Shanghai"
        assert s.master_key_bytes == bytes.fromhex("a" * 64)


def test_config_setup_token_optional():
    """HUB_SETUP_TOKEN 可选；未设置时为 None。"""
    from hub.config import Settings
    with patch.dict(os.environ, {
        "HUB_DATABASE_URL": "postgresql://x@y/z",
        "HUB_REDIS_URL": "redis://r:6379/0",
        "HUB_MASTER_KEY": "a" * 64,
    }, clear=True):
        s = Settings()
        assert s.setup_token is None
