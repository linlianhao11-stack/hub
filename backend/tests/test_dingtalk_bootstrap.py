import pytest


@pytest.mark.asyncio
async def test_load_active_dingtalk_app_returns_none_when_empty():
    from hub.runtime import load_active_dingtalk_app
    cfg = await load_active_dingtalk_app()
    assert cfg is None


@pytest.mark.asyncio
async def test_load_active_erp_system_returns_none_when_empty():
    from hub.runtime import load_active_erp_system
    cfg = await load_active_erp_system()
    assert cfg is None


@pytest.mark.asyncio
async def test_load_active_dingtalk_app_decrypts_secrets():
    from hub.crypto import encrypt_secret
    from hub.models import ChannelApp
    from hub.runtime import load_active_dingtalk_app

    await ChannelApp.create(
        channel_type="dingtalk",
        name="test app",
        encrypted_app_key=encrypt_secret("k_plain", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("s_plain", purpose="config_secrets"),
        robot_id="robot_x",
        status="active",
    )
    cfg = await load_active_dingtalk_app()
    assert cfg is not None
    assert cfg.app_key == "k_plain"
    assert cfg.app_secret == "s_plain"
    assert cfg.robot_code == "robot_x"


@pytest.mark.asyncio
async def test_load_active_erp_system_decrypts_apikey():
    from hub.crypto import encrypt_secret
    from hub.models import DownstreamSystem
    from hub.runtime import load_active_erp_system

    await DownstreamSystem.create(
        downstream_type="erp",
        name="erp prod",
        base_url="http://erp:8000",
        encrypted_apikey=encrypt_secret("ak_plain", purpose="config_secrets"),
        apikey_scopes=["system_calls", "act_as_user"],
        status="active",
    )
    cfg = await load_active_erp_system()
    assert cfg is not None
    assert cfg.base_url == "http://erp:8000"
    assert cfg.api_key == "ak_plain"


@pytest.mark.asyncio
async def test_bootstrap_with_no_config_returns_empty_clients():
    from hub.runtime import bootstrap_dingtalk_clients
    clients = await bootstrap_dingtalk_clients(with_stream=False)
    assert clients.erp_adapter is None
    assert clients.binding_service is None
    assert clients.dingtalk_sender is None
    assert clients.dingtalk_stream is None
    await clients.aclose()


@pytest.mark.asyncio
async def test_bootstrap_with_full_config_assembles_all_services():
    from hub.crypto import encrypt_secret
    from hub.models import ChannelApp, DownstreamSystem
    from hub.runtime import bootstrap_dingtalk_clients

    await DownstreamSystem.create(
        downstream_type="erp", name="erp",
        base_url="http://erp:8000",
        encrypted_apikey=encrypt_secret("ak", purpose="config_secrets"),
        status="active",
    )
    await ChannelApp.create(
        channel_type="dingtalk", name="dt",
        encrypted_app_key=encrypt_secret("k", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("s", purpose="config_secrets"),
        robot_id="robot",
        status="active",
    )

    clients = await bootstrap_dingtalk_clients(with_stream=False)
    try:
        assert clients.erp_adapter is not None
        assert clients.erp_cache is not None
        assert clients.identity_service is not None
        assert clients.binding_service is not None
        assert clients.dingtalk_sender is not None
        # with_stream=False → stream 不装
        assert clients.dingtalk_stream is None
    finally:
        await clients.aclose()


@pytest.mark.asyncio
async def test_bootstrap_with_stream_true_assembles_stream():
    from hub.crypto import encrypt_secret
    from hub.models import ChannelApp
    from hub.runtime import bootstrap_dingtalk_clients

    await ChannelApp.create(
        channel_type="dingtalk", name="dt",
        encrypted_app_key=encrypt_secret("k", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("s", purpose="config_secrets"),
        robot_id="robot",
        status="active",
    )

    clients = await bootstrap_dingtalk_clients(with_stream=True)
    try:
        assert clients.dingtalk_stream is not None
    finally:
        await clients.aclose()


@pytest.mark.asyncio
async def test_inactive_apps_are_ignored():
    from hub.crypto import encrypt_secret
    from hub.models import ChannelApp
    from hub.runtime import load_active_dingtalk_app

    await ChannelApp.create(
        channel_type="dingtalk", name="old",
        encrypted_app_key=encrypt_secret("k1", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("s1", purpose="config_secrets"),
        status="inactive",
    )
    cfg = await load_active_dingtalk_app()
    assert cfg is None
