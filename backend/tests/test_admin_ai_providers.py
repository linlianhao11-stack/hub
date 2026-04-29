"""HUB 后台 ai_providers 路由测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


async def _setup_admin(erp_user_id: int = 1, role_code: str = "platform_admin"):
    from hub.auth.erp_session import ErpSessionAuth
    from hub.models import DownstreamIdentity, HubRole, HubUser, HubUserRole
    from hub.seed import run_seed
    from main import app
    await run_seed()

    user = await HubUser.create(display_name=f"u{erp_user_id}")
    await DownstreamIdentity.create(
        hub_user=user, downstream_type="erp", downstream_user_id=erp_user_id,
    )
    role = await HubRole.get(code=role_code)
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    erp = AsyncMock()
    erp.get_me = AsyncMock(return_value={
        "id": erp_user_id, "username": f"u{erp_user_id}", "permissions": [],
    })
    auth = ErpSessionAuth(erp_adapter=erp)
    app.state.session_auth = auth
    cookie = auth._encode_cookie({
        "jwt": "tok", "user": {"id": erp_user_id, "username": f"u{erp_user_id}"},
    })
    transport = ASGITransport(app=app)
    return transport, cookie, user


@pytest_asyncio.fixture
async def admin_client():
    transport, cookie, user = await _setup_admin()
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        yield ac, user


def test_module_imports_without_nameerror():
    """import ai_providers 整个模块不应抛 NameError（防 Field 漏导）。"""
    import importlib
    mod = importlib.import_module("hub.routers.admin.ai_providers")
    assert hasattr(mod, "router")
    assert hasattr(mod, "CreateAIRequest")


@pytest.mark.asyncio
async def test_get_defaults(admin_client):
    """GET /defaults 返预填 base_url + model（deepseek/qwen 各一份）。"""
    ac, _ = admin_client
    r = await ac.get("/hub/v1/admin/ai-providers/defaults")
    assert r.status_code == 200
    body = r.json()
    assert "deepseek" in body
    assert "qwen" in body
    assert body["deepseek"]["base_url"] == "https://api.deepseek.com/v1"
    assert body["deepseek"]["model"] == "deepseek-chat"
    assert body["qwen"]["model"] == "qwen-plus"


@pytest.mark.asyncio
async def test_create_ai_rejects_unsupported_provider_type(admin_client):
    """provider_type=claude → 422，因为 Pydantic pattern 限制 deepseek/qwen。"""
    ac, _ = admin_client
    r = await ac.post(
        "/hub/v1/admin/ai-providers",
        json={"provider_type": "claude", "api_key": "k"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_ai_fills_defaults_for_deepseek(admin_client):
    """缺省 base_url/model 时按 _AI_DEFAULTS 预填。"""
    from hub.models import AIProvider

    ac, _ = admin_client
    r = await ac.post(
        "/hub/v1/admin/ai-providers",
        json={"provider_type": "deepseek", "api_key": "k"},
    )
    assert r.status_code == 200
    rec = await AIProvider.filter(id=r.json()["id"]).first()
    assert rec.base_url == "https://api.deepseek.com/v1"
    assert rec.model == "deepseek-chat"


@pytest.mark.asyncio
async def test_create_ai_disables_others_to_keep_single_active(admin_client):
    """新建 active provider 时，其他同类应被自动 disable，避免 Plan 4 factory 取到不确定项。"""
    from hub.models import AIProvider

    ac, _ = admin_client
    await ac.post(
        "/hub/v1/admin/ai-providers",
        json={"provider_type": "deepseek", "api_key": "k1"},
    )
    await ac.post(
        "/hub/v1/admin/ai-providers",
        json={"provider_type": "qwen", "api_key": "k2"},
    )
    actives = await AIProvider.filter(status="active").all()
    assert len(actives) == 1
    assert actives[0].provider_type == "qwen"


@pytest.mark.asyncio
async def test_test_chat_success(admin_client, monkeypatch):
    """provider chat 成功 → {ok: true}；同时验证 aclose() 被调用避免 client 泄漏。"""
    from hub.crypto import encrypt_secret
    from hub.models import AIProvider

    rec = await AIProvider.create(
        provider_type="deepseek", name="t",
        encrypted_api_key=encrypt_secret("key", purpose="config_secrets"),
        base_url="https://api.deepseek.com/v1", model="deepseek-chat",
        config={}, status="active",
    )

    chat_mock = AsyncMock(return_value={"role": "assistant", "content": "pong"})
    aclose_mock = AsyncMock()

    class _FakeProvider:
        def __init__(self, *, api_key, base_url, model):
            assert api_key == "key"
            assert base_url == "https://api.deepseek.com/v1"
            assert model == "deepseek-chat"
        chat = chat_mock
        aclose = aclose_mock

    monkeypatch.setattr(
        "hub.routers.admin.ai_providers.DeepSeekProvider", _FakeProvider, raising=False,
    )

    ac, _ = admin_client
    r = await ac.post(f"/hub/v1/admin/ai-providers/{rec.id}/test-chat")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    chat_mock.assert_called_once()
    aclose_mock.assert_called_once()


@pytest.mark.asyncio
async def test_test_chat_failure(admin_client, monkeypatch):
    """provider chat 抛错 → {ok: false, error: <消息>}；aclose 仍要在 finally 调用。"""
    from hub.crypto import encrypt_secret
    from hub.models import AIProvider

    rec = await AIProvider.create(
        provider_type="qwen", name="t",
        encrypted_api_key=encrypt_secret("key", purpose="config_secrets"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", model="qwen-plus",
        config={}, status="active",
    )

    aclose_mock = AsyncMock()

    class _FakeProvider:
        def __init__(self, **_):
            pass

        async def chat(self, **_):
            raise RuntimeError("api timeout")

        aclose = aclose_mock

    monkeypatch.setattr(
        "hub.routers.admin.ai_providers.QwenProvider", _FakeProvider, raising=False,
    )

    ac, _ = admin_client
    r = await ac.post(f"/hub/v1/admin/ai-providers/{rec.id}/test-chat")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "api timeout" in body["error"]
    aclose_mock.assert_called_once()


@pytest.mark.asyncio
async def test_set_active_disables_others(admin_client):
    """POST /ai-providers/{id}/set-active：目标设为 active，其他全部 disabled。"""
    from hub.crypto import encrypt_secret
    from hub.models import AIProvider

    a = await AIProvider.create(
        provider_type="deepseek", name="A",
        encrypted_api_key=encrypt_secret("k1", purpose="config_secrets"),
        base_url="x", model="m", config={}, status="active",
    )
    b = await AIProvider.create(
        provider_type="qwen", name="B",
        encrypted_api_key=encrypt_secret("k2", purpose="config_secrets"),
        base_url="x", model="m", config={}, status="active",
    )

    ac, _ = admin_client
    r = await ac.post(f"/hub/v1/admin/ai-providers/{b.id}/set-active")
    assert r.status_code == 200
    a_after = await AIProvider.get(id=a.id)
    b_after = await AIProvider.get(id=b.id)
    assert a_after.status == "disabled"
    assert b_after.status == "active"
    actives = await AIProvider.filter(status="active").count()
    assert actives == 1


@pytest.mark.asyncio
async def test_list_ai_hides_api_key(admin_client):
    """GET /ai-providers：不返 encrypted_api_key 明文字段。"""
    from hub.crypto import encrypt_secret
    from hub.models import AIProvider

    await AIProvider.create(
        provider_type="deepseek", name="t",
        encrypted_api_key=encrypt_secret("secret", purpose="config_secrets"),
        base_url="x", model="m", config={}, status="active",
    )
    ac, _ = admin_client
    r = await ac.get("/hub/v1/admin/ai-providers")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert "encrypted_api_key" not in items[0]
    assert "api_key" not in items[0]
