"""HUB 后台登录 / 登出 / 当前用户路由。"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request, Response
from pydantic import BaseModel

from hub.adapters.downstream.erp4 import ErpPermissionError, ErpSystemError

router = APIRouter(prefix="/hub/v1/admin", tags=["admin-auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(request: Request, response: Response, body: LoginRequest = Body(...)):
    auth = getattr(request.app.state, "session_auth", None)
    if auth is None:
        raise HTTPException(status_code=503, detail="HUB 尚未完成初始化（ERP 下游未配置）")
    try:
        cookie = await auth.login(username=body.username, password=body.password)
    except ErpPermissionError as e:
        raise HTTPException(status_code=401, detail="用户名或密码错误") from e
    except ErpSystemError as e:
        raise HTTPException(status_code=502, detail=f"ERP 通信失败：{e}") from e

    response.set_cookie(
        "hub_session", cookie,
        httponly=True, samesite="strict", max_age=86400,
        secure=False,  # 部署到 HTTPS 时改 True
    )
    return {"success": True}


@router.post("/logout")
async def logout(request: Request, response: Response):
    cookie = request.cookies.get("hub_session")
    auth = getattr(request.app.state, "session_auth", None)
    if cookie and auth is not None:
        await auth.logout(cookie)
    response.delete_cookie("hub_session")
    return {"success": True}


@router.get("/me")
async def me(request: Request):
    """前端用：查当前登录用户 + 权限。"""
    cookie = request.cookies.get("hub_session")
    auth = getattr(request.app.state, "session_auth", None)
    if auth is None:
        raise HTTPException(status_code=503, detail="HUB session 未配置")
    erp_user = await auth.verify_cookie(cookie)
    if erp_user is None:
        raise HTTPException(status_code=401, detail="未登录")

    from hub.auth.admin_perms import resolve_hub_user_from_erp
    hub_user = await resolve_hub_user_from_erp(erp_user_id=erp_user["id"])
    permissions: list[str] = []
    if hub_user:
        from hub.permissions import get_user_permissions
        permissions = list(await get_user_permissions(hub_user.id))

    return {
        "erp_user": erp_user,
        "hub_user_id": hub_user.id if hub_user else None,
        "permissions": permissions,
    }
