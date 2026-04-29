from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_login_forwards_to_erp_and_sets_cookie():
    """POST /admin/login → HUB 调 ERP /auth/login → 设置 hub_session cookie。"""
    from hub.auth.erp_session import ErpSessionAuth

    erp_login_resp = {
        "access_token": "erp_jwt_xxx",
        "user": {"id": 42, "username": "admin", "display_name": "管理员",
                 "role": "admin", "permissions": []},
    }
    erp = AsyncMock()
    erp.login = AsyncMock(return_value=erp_login_resp)

    auth = ErpSessionAuth(erp_adapter=erp)
    cookie_value = await auth.login(username="admin", password="x")
    assert cookie_value
    decoded = auth._decode_cookie(cookie_value)
    assert decoded["jwt"] == "erp_jwt_xxx"
    assert decoded["user"]["id"] == 42


@pytest.mark.asyncio
async def test_verify_cookie_calls_erp_me_with_jwt():
    from hub.auth.erp_session import ErpSessionAuth
    erp = AsyncMock()
    erp.get_me = AsyncMock(return_value={"id": 42, "username": "admin",
                                          "permissions": ["admin"]})

    auth = ErpSessionAuth(erp_adapter=erp)
    cookie = auth._encode_cookie({
        "jwt": "tok", "user": {"id": 42, "username": "admin"},
    })
    user = await auth.verify_cookie(cookie)
    assert user["id"] == 42
    erp.get_me.assert_awaited_once_with(jwt="tok")


@pytest.mark.asyncio
async def test_verify_cookie_invalid_returns_none():
    from hub.auth.erp_session import ErpSessionAuth
    auth = ErpSessionAuth(erp_adapter=AsyncMock())
    assert await auth.verify_cookie("garbage") is None


@pytest.mark.asyncio
async def test_verify_cookie_jwt_expired():
    """ERP /me 返回 401 → HUB session 失效。"""
    from hub.adapters.downstream.erp4 import ErpPermissionError
    from hub.auth.erp_session import ErpSessionAuth
    erp = AsyncMock()
    erp.get_me = AsyncMock(side_effect=ErpPermissionError("401"))

    auth = ErpSessionAuth(erp_adapter=erp)
    cookie = auth._encode_cookie({"jwt": "expired", "user": {"id": 1}})
    assert await auth.verify_cookie(cookie) is None


@pytest.mark.asyncio
async def test_verify_cache_avoids_repeated_erp_calls():
    """5 分钟内同 cookie 不应重复调 ERP /auth/me。"""
    from hub.auth.erp_session import ErpSessionAuth
    erp = AsyncMock()
    erp.get_me = AsyncMock(return_value={"id": 1, "permissions": []})
    auth = ErpSessionAuth(erp_adapter=erp, cache_ttl=300)

    cookie = auth._encode_cookie({"jwt": "tok", "user": {"id": 1}})
    await auth.verify_cookie(cookie)
    await auth.verify_cookie(cookie)
    erp.get_me.assert_awaited_once()


@pytest.mark.asyncio
async def test_logout_invalidates_jwt_at_erp_and_clears_cache():
    """logout 必须调 ERP /auth/logout 让 JWT 失效；清本地 cache 后再次 verify
    会重调 ERP，但 ERP 此时返回 401 → verify 返回 None。"""
    from hub.adapters.downstream.erp4 import ErpPermissionError
    from hub.auth.erp_session import ErpSessionAuth

    erp = AsyncMock()
    erp.get_me = AsyncMock(return_value={"id": 1, "permissions": []})
    erp.logout = AsyncMock()
    auth = ErpSessionAuth(erp_adapter=erp, cache_ttl=300)

    cookie = auth._encode_cookie({"jwt": "tok", "user": {"id": 1}})
    user = await auth.verify_cookie(cookie)
    assert user is not None

    await auth.logout(cookie)
    erp.logout.assert_awaited_once_with(jwt="tok")

    erp.get_me = AsyncMock(side_effect=ErpPermissionError("401"))
    user2 = await auth.verify_cookie(cookie)
    assert user2 is None


@pytest.mark.asyncio
async def test_logout_handles_decode_error_gracefully():
    """坏 cookie logout 不抛异常（仍清 cache）。"""
    from hub.auth.erp_session import ErpSessionAuth
    erp = AsyncMock()
    auth = ErpSessionAuth(erp_adapter=erp, cache_ttl=300)
    await auth.logout("garbage")
    erp.logout.assert_not_called()
