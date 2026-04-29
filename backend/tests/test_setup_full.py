"""初始化向导步骤 2-6 完整业务测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def setup_client():
    """已通过 verify-token 拿到 session 的 client。"""
    from main import app
    if not hasattr(app.state, "active_setup_sessions"):
        app.state.active_setup_sessions = {}
    app.state.active_setup_sessions["test-session"] = True
    # 步骤 3 测试需要 session_auth；其它测试不依赖时清空
    app.state.session_auth = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac

    # 清理避免影响后续测试（system_initialized 由 setup_db 自动 truncate）
    app.state.active_setup_sessions.pop("test-session", None)


@pytest.mark.asyncio
async def test_no_session_returns_401(setup_client):
    """缺少 X-Setup-Session 头 → 401。"""
    resp = await setup_client.post(
        "/hub/v1/setup/connect-erp",
        json={
            "name": "X",
            "base_url": "http://x",
            "api_key": "12345678",
            "apikey_scopes": ["s"],
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_session_returns_401(setup_client):
    """X-Setup-Session 头不在 active_setup_sessions → 401。"""
    resp = await setup_client.post(
        "/hub/v1/setup/connect-erp",
        json={
            "name": "X",
            "base_url": "http://x",
            "api_key": "12345678",
            "apikey_scopes": ["s"],
        },
        headers={"X-Setup-Session": "wrong-session"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_connect_erp_persists_and_refreshes_session_auth(setup_client):
    """步骤 2：health 通过 → 写库 + 立即刷新 session_auth。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter
    from hub.models import DownstreamSystem
    from main import app

    with patch.object(
        Erp4Adapter, "health_check", new_callable=AsyncMock, return_value=True,
    ):
        resp = await setup_client.post(
            "/hub/v1/setup/connect-erp",
            json={
                "name": "ERP生产",
                "base_url": "http://erp:8090",
                "api_key": "abcd1234",
                "apikey_scopes": ["act_as_user", "system_calls"],
            },
            headers={"X-Setup-Session": "test-session"},
        )
    assert resp.status_code == 200
    ds = await DownstreamSystem.filter(
        downstream_type="erp", name="ERP生产",
    ).first()
    assert ds is not None
    # session_auth 已立即可用
    assert app.state.session_auth is not None


@pytest.mark.asyncio
async def test_connect_erp_idempotent_updates_existing(setup_client):
    """步骤 2 幂等：同 (downstream_type, name) 第二次写更新而不重复创建。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter
    from hub.models import DownstreamSystem

    with patch.object(
        Erp4Adapter, "health_check", new_callable=AsyncMock, return_value=True,
    ):
        r1 = await setup_client.post(
            "/hub/v1/setup/connect-erp",
            json={
                "name": "ERP",
                "base_url": "http://a",
                "api_key": "abcdefgh",
                "apikey_scopes": ["s"],
            },
            headers={"X-Setup-Session": "test-session"},
        )
        r2 = await setup_client.post(
            "/hub/v1/setup/connect-erp",
            json={
                "name": "ERP",
                "base_url": "http://b",
                "api_key": "abcdefgh",
                "apikey_scopes": ["s"],
            },
            headers={"X-Setup-Session": "test-session"},
        )
    assert r1.status_code == 200 and r2.status_code == 200
    rows = await DownstreamSystem.filter(
        downstream_type="erp", name="ERP",
    ).all()
    assert len(rows) == 1
    assert rows[0].base_url == "http://b"  # 后写覆盖


@pytest.mark.asyncio
async def test_connect_erp_health_fail_returns_400_or_502(setup_client):
    """步骤 2 health_check 返 False → 400；网络异常 → 502。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter
    with patch.object(
        Erp4Adapter, "health_check", new_callable=AsyncMock, return_value=False,
    ):
        resp = await setup_client.post(
            "/hub/v1/setup/connect-erp",
            json={
                "name": "X",
                "base_url": "http://x",
                "api_key": "abcdefgh",
                "apikey_scopes": ["s"],
            },
            headers={"X-Setup-Session": "test-session"},
        )
    assert resp.status_code in (400, 502)


@pytest.mark.asyncio
async def test_create_admin_creates_hub_user_and_role(setup_client):
    """步骤 3 端到端：测试 ERP login → 创建 hub_user + downstream_identity + 绑 platform_admin。"""
    from hub.auth.erp_session import ErpSessionAuth
    from hub.models import DownstreamIdentity, HubRole, HubUserRole
    from hub.seed import run_seed
    from main import app
    await run_seed()

    erp = AsyncMock()
    erp.login = AsyncMock(return_value={
        "access_token": "tok",
        "user": {"id": 42, "username": "admin", "display_name": "管理员"},
    })
    app.state.session_auth = ErpSessionAuth(erp_adapter=erp)

    resp = await setup_client.post(
        "/hub/v1/setup/create-admin",
        json={"erp_username": "admin", "erp_password": "x"},
        headers={"X-Setup-Session": "test-session"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["erp_user_id"] == 42

    # 校验副作用
    di = await DownstreamIdentity.filter(
        downstream_type="erp", downstream_user_id=42,
    ).first()
    assert di is not None
    role = await HubRole.get(code="platform_admin")
    ur = await HubUserRole.filter(
        hub_user_id=di.hub_user_id, role_id=role.id,
    ).first()
    assert ur is not None


@pytest.mark.asyncio
async def test_create_admin_without_session_auth_returns_400(setup_client):
    """步骤 3 在步骤 2 之前调 → 提示先完成步骤 2。"""
    from main import app
    app.state.session_auth = None
    resp = await setup_client.post(
        "/hub/v1/setup/create-admin",
        json={"erp_username": "x", "erp_password": "y"},
        headers={"X-Setup-Session": "test-session"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_admin_idempotent_just_appends_role(setup_client):
    """步骤 3 幂等：同 erp_user_id 第二次调用不会重复创建 hub_user。"""
    from hub.auth.erp_session import ErpSessionAuth
    from hub.models import DownstreamIdentity, HubUser
    from hub.seed import run_seed
    from main import app
    await run_seed()

    erp = AsyncMock()
    erp.login = AsyncMock(return_value={
        "access_token": "t",
        "user": {"id": 100, "username": "u", "display_name": "U"},
    })
    app.state.session_auth = ErpSessionAuth(erp_adapter=erp)

    headers = {"X-Setup-Session": "test-session"}
    payload = {"erp_username": "u", "erp_password": "p"}
    r1 = await setup_client.post(
        "/hub/v1/setup/create-admin", json=payload, headers=headers,
    )
    r2 = await setup_client.post(
        "/hub/v1/setup/create-admin", json=payload, headers=headers,
    )
    assert r1.status_code == 200 and r2.status_code == 200
    di_count = await DownstreamIdentity.filter(
        downstream_type="erp", downstream_user_id=100,
    ).count()
    user_count = await HubUser.all().count()
    assert di_count == 1
    assert user_count == 1


@pytest.mark.asyncio
async def test_connect_dingtalk_idempotent(setup_client):
    """步骤 4 幂等：同 (channel_type, name) 第二次更新不重复创建。"""
    from hub.models import ChannelApp
    payload = {
        "name": "钉钉",
        "app_key": "k",
        "app_secret": "s",
        "robot_id": "r",
    }
    headers = {"X-Setup-Session": "test-session"}
    r1 = await setup_client.post(
        "/hub/v1/setup/connect-dingtalk", json=payload, headers=headers,
    )
    r2 = await setup_client.post(
        "/hub/v1/setup/connect-dingtalk", json=payload, headers=headers,
    )
    assert r1.status_code == 200 and r2.status_code == 200
    apps = await ChannelApp.filter(
        channel_type="dingtalk", name="钉钉",
    ).all()
    assert len(apps) == 1


@pytest.mark.asyncio
async def test_connect_dingtalk_sets_reload_event(setup_client):
    """步骤 4 写完 ChannelApp 后必须 set 钉钉 reload event 让 gateway 重连。"""
    import asyncio

    from main import app
    app.state.dingtalk_reload_event = asyncio.Event()
    assert not app.state.dingtalk_reload_event.is_set()

    resp = await setup_client.post(
        "/hub/v1/setup/connect-dingtalk",
        json={"name": "X", "app_key": "k", "app_secret": "s"},
        headers={"X-Setup-Session": "test-session"},
    )
    assert resp.status_code == 200
    assert app.state.dingtalk_reload_event.is_set()


@pytest.mark.asyncio
async def test_connect_ai_uses_default_for_known_provider(setup_client):
    """步骤 5 deepseek 未传 base_url/model → 用默认值。"""
    from hub.models import AIProvider
    from hub.routers.setup_full import DeepSeekProvider
    with patch.object(
        DeepSeekProvider, "chat", new_callable=AsyncMock, return_value="ok",
    ):
        resp = await setup_client.post(
            "/hub/v1/setup/connect-ai",
            json={"provider_type": "deepseek", "api_key": "sk-x"},
            headers={"X-Setup-Session": "test-session"},
        )
    assert resp.status_code == 200
    rec = await AIProvider.filter(provider_type="deepseek").first()
    assert rec is not None
    assert "deepseek.com" in rec.base_url
    assert rec.model == "deepseek-chat"
    assert rec.status == "active"


@pytest.mark.asyncio
async def test_connect_ai_qwen_default(setup_client):
    """步骤 5 qwen 未传 → 用 dashscope 默认。"""
    from hub.models import AIProvider
    from hub.routers.setup_full import QwenProvider
    with patch.object(
        QwenProvider, "chat", new_callable=AsyncMock, return_value="ok",
    ):
        resp = await setup_client.post(
            "/hub/v1/setup/connect-ai",
            json={"provider_type": "qwen", "api_key": "sk-q"},
            headers={"X-Setup-Session": "test-session"},
        )
    assert resp.status_code == 200
    rec = await AIProvider.filter(provider_type="qwen").first()
    assert rec is not None
    assert "dashscope.aliyuncs.com" in rec.base_url
    assert rec.model == "qwen-plus"


@pytest.mark.asyncio
async def test_connect_ai_chat_failure_returns_502(setup_client):
    """步骤 5 chat 抛错 → 502。"""
    from hub.routers.setup_full import DeepSeekProvider
    with patch.object(
        DeepSeekProvider, "chat", new_callable=AsyncMock,
        side_effect=Exception("boom"),
    ):
        resp = await setup_client.post(
            "/hub/v1/setup/connect-ai",
            json={"provider_type": "deepseek", "api_key": "sk-x"},
            headers={"X-Setup-Session": "test-session"},
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_complete_blocks_until_required_steps_done(setup_client):
    """步骤 6 必须在 ERP + admin + 钉钉 都完成后才能调。"""
    headers = {"X-Setup-Session": "test-session"}
    resp = await setup_client.post("/hub/v1/setup/complete", headers=headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_complete_writes_system_initialized_and_blocks_setup(setup_client):
    """步骤 6 完成后写入 SystemConfig + 后续 /setup/* 全部 404。"""
    from hub.models import (
        ChannelApp,
        DownstreamIdentity,
        DownstreamSystem,
        HubUser,
        SystemConfig,
    )
    # 预置三个前置
    await DownstreamSystem.create(
        downstream_type="erp",
        name="X",
        base_url="http://x",
        encrypted_apikey=b"\0" * 32,
        apikey_scopes=["x"],
        status="active",
    )
    user = await HubUser.create(display_name="A")
    await DownstreamIdentity.create(
        hub_user=user, downstream_type="erp", downstream_user_id=1,
    )
    await ChannelApp.create(
        channel_type="dingtalk",
        name="D",
        encrypted_app_key=b"\0" * 32,
        encrypted_app_secret=b"\0" * 32,
        status="active",
    )

    headers = {"X-Setup-Session": "test-session"}
    resp = await setup_client.post("/hub/v1/setup/complete", headers=headers)
    assert resp.status_code == 200

    cfg = await SystemConfig.filter(key="system_initialized").first()
    assert cfg is not None and cfg.value is True

    # 完成后再调 connect-erp 应返回 404
    r2 = await setup_client.post(
        "/hub/v1/setup/connect-erp",
        json={
            "name": "Y",
            "base_url": "http://y",
            "api_key": "abcdefgh",
            "apikey_scopes": ["s"],
        },
        headers=headers,
    )
    assert r2.status_code == 404
