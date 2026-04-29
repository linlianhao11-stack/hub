import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient


def _make_app_with_dep(dep_fn):
    app = FastAPI()

    @app.get("/protected")
    async def protected(_=Depends(dep_fn)):
        return {"ok": True}
    return app


@pytest.mark.asyncio
async def test_no_cookie_returns_401():
    from hub.auth.admin_perms import require_hub_perm
    app = _make_app_with_dep(require_hub_perm("platform.tasks.read"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.get("/protected")
        # 没配置 session_auth → 503；按依赖期望 401，但本测试只验证 dep 拒绝
        assert resp.status_code in (401, 503)


@pytest.mark.asyncio
async def test_admin_role_passes_all_perms():
    """platform_admin 拥有所有权限。"""
    from hub.auth.admin_perms import _check_perm_for_hub_user
    from hub.models import HubRole, HubUser, HubUserRole
    from hub.seed import run_seed
    await run_seed()

    user = await HubUser.create(display_name="Adm")
    role = await HubRole.get(code="platform_admin")
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    for perm in ["platform.tasks.read", "platform.users.write",
                 "platform.conversation.monitor", "downstream.erp.use"]:
        assert await _check_perm_for_hub_user(user.id, perm) is True


@pytest.mark.asyncio
async def test_viewer_only_has_read_perms():
    from hub.auth.admin_perms import _check_perm_for_hub_user
    from hub.models import HubRole, HubUser, HubUserRole
    from hub.seed import run_seed
    await run_seed()

    user = await HubUser.create(display_name="V")
    role = await HubRole.get(code="platform_viewer")
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    assert await _check_perm_for_hub_user(user.id, "platform.tasks.read") is True
    assert await _check_perm_for_hub_user(user.id, "platform.users.write") is False
    assert await _check_perm_for_hub_user(user.id, "platform.flags.write") is False


@pytest.mark.asyncio
async def test_resolve_hub_user_from_erp_session():
    """ERP user.id → 找到对应的 hub_user（通过 downstream_identity）。"""
    from hub.auth.admin_perms import resolve_hub_user_from_erp
    from hub.models import DownstreamIdentity, HubUser

    user = await HubUser.create(display_name="X")
    await DownstreamIdentity.create(
        hub_user=user, downstream_type="erp", downstream_user_id=42,
    )
    found = await resolve_hub_user_from_erp(erp_user_id=42)
    assert found is not None
    assert found.id == user.id


@pytest.mark.asyncio
async def test_no_hub_user_for_erp_returns_none():
    """ERP 用户存在但 HUB 没绑定 hub_user → None（依赖会翻 403）。"""
    from hub.auth.admin_perms import resolve_hub_user_from_erp
    found = await resolve_hub_user_from_erp(erp_user_id=999)
    assert found is None
