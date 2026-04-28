import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def app_client():
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_setup_welcome_returns_self_check(app_client):
    resp = await app_client.get("/hub/v1/setup/welcome")
    assert resp.status_code == 200
    body = resp.json()
    assert "checks" in body
    assert "postgres" in body["checks"]
    assert "redis" in body["checks"]
    assert "master_key" in body["checks"]


@pytest.mark.asyncio
async def test_setup_verify_token_rejects_wrong(app_client):
    resp = await app_client.post("/hub/v1/setup/verify-token", json={"token": "wrong"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_setup_verify_token_accepts_valid(app_client):
    from hub.auth.bootstrap_token import generate_token
    plain = await generate_token(ttl_seconds=300)
    resp = await app_client.post("/hub/v1/setup/verify-token", json={"token": plain})
    assert resp.status_code == 200
    body = resp.json()
    assert "session" in body  # 简单 cookie / token 后续步骤用


@pytest.mark.asyncio
async def test_setup_blocked_after_initialized(app_client):
    """system_initialized=true 后所有 /setup/* 路由应返回 404。"""
    from hub.models import SystemConfig
    await SystemConfig.create(key="system_initialized", value=True)
    resp = await app_client.get("/hub/v1/setup/welcome")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_setup_token_one_time_use(app_client):
    """一次性语义：同一 token 第二次 verify 应失败（消费后失效）。"""
    from hub.auth.bootstrap_token import generate_token
    plain = await generate_token(ttl_seconds=300)

    # 第一次 verify 成功
    resp1 = await app_client.post("/hub/v1/setup/verify-token", json={"token": plain})
    assert resp1.status_code == 200
    assert "session" in resp1.json()

    # 第二次 verify 同一 token 应失败
    resp2 = await app_client.post("/hub/v1/setup/verify-token", json={"token": plain})
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_setup_token_concurrent_consume_only_one_wins(app_client):
    """并发场景：两个请求同时 verify 同一 token，只有一个能拿到 session。"""
    import asyncio

    from hub.auth.bootstrap_token import generate_token
    plain = await generate_token(ttl_seconds=300)

    async def attempt():
        return await app_client.post("/hub/v1/setup/verify-token", json={"token": plain})

    r1, r2 = await asyncio.gather(attempt(), attempt())
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 401], f"期望 [200, 401] 但拿到 {statuses}"
