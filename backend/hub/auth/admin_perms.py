"""HUB 后台路由权限校验（require_hub_perm 依赖）。

链路：cookie → ERP user → HUB hub_user（via DownstreamIdentity）→
hub_user_role → hub_role_permission → 校验。
"""
from __future__ import annotations

import logging

from fastapi import Cookie, HTTPException, Request

from hub.models import DownstreamIdentity, HubUser
from hub.permissions import has_permission

logger = logging.getLogger("hub.auth.admin_perms")


async def resolve_hub_user_from_erp(erp_user_id: int) -> HubUser | None:
    di = await DownstreamIdentity.filter(
        downstream_type="erp", downstream_user_id=erp_user_id,
    ).first()
    if di is None:
        return None
    return await HubUser.filter(id=di.hub_user_id).first()


async def _check_perm_for_hub_user(hub_user_id: int, perm_code: str) -> bool:
    return await has_permission(hub_user_id, perm_code)


def require_hub_perm(perm_code: str):
    """FastAPI 依赖：要求当前 cookie 用户拥有指定 HUB 权限。"""
    async def _dep(request: Request, hub_session: str | None = Cookie(default=None)):
        auth = getattr(request.app.state, "session_auth", None)
        if auth is None:
            raise HTTPException(status_code=503, detail="HUB session 未配置")

        erp_user = await auth.verify_cookie(hub_session)
        if erp_user is None:
            raise HTTPException(status_code=401, detail="请先登录")

        hub_user = await resolve_hub_user_from_erp(erp_user_id=erp_user["id"])
        if hub_user is None:
            raise HTTPException(
                status_code=403,
                detail="你的 ERP 账号未关联 HUB 用户。如需访问后台请联系管理员。",
            )

        if not await _check_perm_for_hub_user(hub_user.id, perm_code):
            raise HTTPException(status_code=403, detail=f"缺少权限：{perm_code}")

        request.state.hub_user = hub_user
        request.state.erp_user = erp_user
        return hub_user

    return _dep
