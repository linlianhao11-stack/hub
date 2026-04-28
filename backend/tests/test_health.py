import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def app_client():
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_returns_200(app_client):
    resp = await app_client.get("/hub/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("healthy", "degraded", "unhealthy")
    assert "components" in body
    assert "uptime_seconds" in body
    assert "version" in body


@pytest.mark.asyncio
async def test_health_lists_components(app_client):
    resp = await app_client.get("/hub/v1/health")
    body = resp.json()
    assert "postgres" in body["components"]
    assert "redis" in body["components"]
