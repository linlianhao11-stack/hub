import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_admin_key_valid(monkeypatch):
    monkeypatch.setenv("HUB_ADMIN_KEY", "test_admin_key_xyz")
    from hub import config
    config._settings = None
    from hub.auth.admin_key import require_admin_key

    app = FastAPI()

    @app.get("/admin/test")
    async def endpoint(_=Depends(require_admin_key)):
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/admin/test", headers={"X-HUB-Admin-Key": "test_admin_key_xyz"})
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_key_missing(monkeypatch):
    monkeypatch.setenv("HUB_ADMIN_KEY", "test_admin_key_xyz")
    from hub import config
    config._settings = None
    from hub.auth.admin_key import require_admin_key

    app = FastAPI()
    @app.get("/admin/test")
    async def endpoint(_=Depends(require_admin_key)):
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/admin/test")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_key_wrong(monkeypatch):
    monkeypatch.setenv("HUB_ADMIN_KEY", "test_admin_key_xyz")
    from hub import config
    config._settings = None
    from hub.auth.admin_key import require_admin_key

    app = FastAPI()
    @app.get("/admin/test")
    async def endpoint(_=Depends(require_admin_key)):
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/admin/test", headers={"X-HUB-Admin-Key": "wrong"})
        assert resp.status_code == 403
