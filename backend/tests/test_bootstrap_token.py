import pytest
from datetime import datetime, timezone, timedelta


@pytest.mark.asyncio
async def test_generate_and_verify_token():
    from hub.auth.bootstrap_token import generate_token, verify_token
    plaintext = await generate_token(ttl_seconds=300)
    assert len(plaintext) >= 32
    assert await verify_token(plaintext) is True


@pytest.mark.asyncio
async def test_verify_wrong_token():
    from hub.auth.bootstrap_token import generate_token, verify_token
    await generate_token(ttl_seconds=300)
    assert await verify_token("wrong_token_xxx") is False


@pytest.mark.asyncio
async def test_expired_token():
    from hub.auth.bootstrap_token import generate_token, verify_token
    plaintext = await generate_token(ttl_seconds=-10)  # 已过期
    assert await verify_token(plaintext) is False


@pytest.mark.asyncio
async def test_used_token():
    from hub.auth.bootstrap_token import generate_token, verify_token, mark_used
    plaintext = await generate_token(ttl_seconds=300)
    assert await verify_token(plaintext) is True
    await mark_used(plaintext)
    assert await verify_token(plaintext) is False  # 已使用


@pytest.mark.asyncio
async def test_explicit_token_from_env(monkeypatch):
    """HUB_SETUP_TOKEN 环境变量显式指定时应被采纳。"""
    monkeypatch.setenv("HUB_SETUP_TOKEN", "explicit_token_123456789012345")
    from hub.auth.bootstrap_token import generate_token, verify_token
    # 重新读 settings
    from hub import config
    config._settings = None  # 清缓存
    await generate_token(ttl_seconds=300)
    assert await verify_token("explicit_token_123456789012345") is True
