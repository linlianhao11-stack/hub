"""HUB 后台用户/角色/分配/关联/权限说明 5 个 API。"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from hub.auth.admin_perms import require_hub_perm
from hub.models import (
    AuditLog,
    ChannelUserBinding,
    DownstreamIdentity,
    HubPermission,
    HubRole,
    HubUser,
    HubUserRole,
)

router = APIRouter(prefix="/hub/v1/admin", tags=["admin-users"])


# ===== HubUser 列表 =====
@router.get(
    "/hub-users",
    dependencies=[Depends(require_hub_perm("platform.users.write"))],
)
async def list_hub_users(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
):
    qs = HubUser.all().order_by("-created_at")
    if keyword:
        qs = qs.filter(display_name__icontains=keyword)
    total = await qs.count()
    items = await qs.offset((page - 1) * page_size).limit(page_size)
    return {
        "items": [
            {
                "id": u.id, "display_name": u.display_name, "status": u.status,
                "created_at": u.created_at,
            }
            for u in items
        ],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get(
    "/hub-users/{user_id}",
    dependencies=[Depends(require_hub_perm("platform.users.write"))],
)
async def get_hub_user_detail(user_id: int):
    user = await HubUser.filter(id=user_id).first()
    if user is None:
        raise HTTPException(404, "HUB 用户不存在")
    bindings = await ChannelUserBinding.filter(hub_user_id=user_id)
    identities = await DownstreamIdentity.filter(hub_user_id=user_id)
    user_roles = await HubUserRole.filter(hub_user_id=user_id)
    role_ids = [ur.role_id for ur in user_roles]
    roles = await HubRole.filter(id__in=role_ids) if role_ids else []
    return {
        "id": user.id, "display_name": user.display_name, "status": user.status,
        "channel_bindings": [
            {
                "channel_type": b.channel_type, "channel_userid": b.channel_userid,
                "status": b.status, "bound_at": b.bound_at, "revoked_at": b.revoked_at,
                "revoked_reason": b.revoked_reason,
            }
            for b in bindings
        ],
        "downstream_identities": [
            {"downstream_type": d.downstream_type, "downstream_user_id": d.downstream_user_id}
            for d in identities
        ],
        "roles": [{"id": r.id, "code": r.code, "name": r.name} for r in roles],
    }


# ===== HubRole 列表（只读，C 阶段不支持自定义编辑） =====
@router.get(
    "/hub-roles",
    dependencies=[Depends(require_hub_perm("platform.users.write"))],
)
async def list_hub_roles():
    roles = await HubRole.all().prefetch_related("permissions")
    items = []
    for r in roles:
        perms = [p async for p in r.permissions]
        items.append({
            "id": r.id, "code": r.code, "name": r.name,
            "description": r.description, "is_builtin": r.is_builtin,
            "permissions": [{"code": p.code, "name": p.name} for p in perms],
        })
    return {"items": items}


# ===== HubPermission 列表（"功能权限说明"页面） =====
@router.get(
    "/hub-permissions",
    dependencies=[Depends(require_hub_perm("platform.users.write"))],
)
async def list_hub_permissions():
    perms = await HubPermission.all().order_by("resource", "sub_resource", "action")
    return {
        "items": [
            {
                "code": p.code, "resource": p.resource, "sub_resource": p.sub_resource,
                "action": p.action, "name": p.name, "description": p.description,
            }
            for p in perms
        ],
    }


# ===== 用户角色分配 =====
class AssignRolesRequest(BaseModel):
    role_ids: list[int]


@router.put(
    "/hub-users/{user_id}/roles",
    dependencies=[Depends(require_hub_perm("platform.users.write"))],
)
async def assign_user_roles(
    request: Request, user_id: int, body: AssignRolesRequest = Body(...),
):
    user = await HubUser.filter(id=user_id).first()
    if user is None:
        raise HTTPException(404, "HUB 用户不存在")
    valid_roles = await HubRole.filter(id__in=body.role_ids)
    if len(valid_roles) != len(set(body.role_ids)):
        raise HTTPException(400, "包含无效角色 ID")

    await HubUserRole.filter(hub_user_id=user_id).delete()
    actor = request.state.hub_user
    for role in valid_roles:
        await HubUserRole.create(
            hub_user_id=user_id, role_id=role.id, assigned_by_hub_user_id=actor.id,
        )

    await AuditLog.create(
        who_hub_user_id=actor.id, action="assign_roles",
        target_type="hub_user", target_id=str(user_id),
        detail={"role_ids": body.role_ids},
    )
    return {"success": True}


# ===== 账号关联 =====
class UpdateDownstreamIdentityRequest(BaseModel):
    downstream_type: str
    downstream_user_id: int


@router.put(
    "/hub-users/{user_id}/downstream-identity",
    dependencies=[Depends(require_hub_perm("platform.users.write"))],
)
async def update_downstream_identity(
    request: Request, user_id: int, body: UpdateDownstreamIdentityRequest = Body(...),
):
    user = await HubUser.filter(id=user_id).first()
    if user is None:
        raise HTTPException(404, "HUB 用户不存在")

    di = await DownstreamIdentity.filter(
        hub_user_id=user_id, downstream_type=body.downstream_type,
    ).first()
    if di:
        di.downstream_user_id = body.downstream_user_id
        await di.save()
    else:
        await DownstreamIdentity.create(
            hub_user_id=user_id, downstream_type=body.downstream_type,
            downstream_user_id=body.downstream_user_id,
        )

    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id, action="update_downstream_identity",
        target_type="hub_user", target_id=str(user_id),
        detail={
            "downstream_type": body.downstream_type,
            "downstream_user_id": body.downstream_user_id,
        },
    )
    return {"success": True}


# ===== 强制解绑（admin 后台） =====
@router.post(
    "/hub-users/{user_id}/force-unbind",
    dependencies=[Depends(require_hub_perm("platform.users.write"))],
)
async def force_unbind(request: Request, user_id: int, channel_type: str = Query(...)):
    binding = await ChannelUserBinding.filter(
        hub_user_id=user_id, channel_type=channel_type, status="active",
    ).first()
    if binding is None:
        raise HTTPException(404, "无活跃绑定")
    binding.status = "revoked"
    binding.revoked_at = datetime.now(UTC)
    binding.revoked_reason = "admin_force"
    await binding.save()

    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id, action="force_unbind",
        target_type="hub_user", target_id=str(user_id),
        detail={"channel_type": channel_type},
    )

    runner = getattr(request.app.state, "task_runner", None)
    if runner:
        await runner.submit("dingtalk_outbound", {
            "channel_userid": binding.channel_userid,
            "type": "text",
            "text": "你的 HUB 绑定已被管理员解除。如需重新绑定请发送 /绑定 你的ERP用户名。",
        })
    return {"success": True}
