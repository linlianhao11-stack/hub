"""admin 操作审计路由：普通审计（platform.audit.read）+ meta 审计（system_read）。

普通审计 = AuditLog（创建 ApiKey / 解绑 / 改角色等）
meta 审计 = MetaAuditLog（"谁查看了用户对话"，敏感）
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from hub.auth.admin_perms import require_hub_perm
from hub.models import AuditLog, HubUser, MetaAuditLog

router = APIRouter(prefix="/hub/v1/admin/audit", tags=["admin-audit"])


async def _resolve_actor_names(actor_ids: set[int]) -> dict[int, str]:
    if not actor_ids:
        return {}
    users = await HubUser.filter(id__in=list(actor_ids))
    return {u.id: u.display_name for u in users}


@router.get("", dependencies=[Depends(require_hub_perm("platform.audit.read"))])
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor_id: int | None = None,
    action: str | None = None,
    target_type: str | None = None,
    since_hours: int = Query(168, ge=1, le=8760),
):
    qs = AuditLog.all().order_by("-created_at")
    qs = qs.filter(
        created_at__gte=datetime.now(UTC) - timedelta(hours=since_hours),
    )
    if actor_id:
        qs = qs.filter(who_hub_user_id=actor_id)
    if action:
        qs = qs.filter(action=action)
    if target_type:
        qs = qs.filter(target_type=target_type)

    total = await qs.count()
    items = await qs.offset((page - 1) * page_size).limit(page_size)
    actors = await _resolve_actor_names({it.who_hub_user_id for it in items})
    return {
        "items": [
            {
                "id": it.id,
                "actor_id": it.who_hub_user_id,
                "actor_name": actors.get(it.who_hub_user_id, "?"),
                "action": it.action,
                "target_type": it.target_type,
                "target_id": it.target_id,
                "detail": it.detail,
                "ip": it.ip,
                "created_at": it.created_at,
            }
            for it in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get(
    "/meta",
    dependencies=[Depends(require_hub_perm("platform.audit.system_read"))],
)
async def list_meta_audit(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor_id: int | None = None,
    since_hours: int = Query(168, ge=1, le=8760),
):
    """谁在何时查看了哪些 task 的 payload（"监控监控员"）。"""
    qs = MetaAuditLog.all().order_by("-viewed_at")
    qs = qs.filter(
        viewed_at__gte=datetime.now(UTC) - timedelta(hours=since_hours),
    )
    if actor_id:
        qs = qs.filter(who_hub_user_id=actor_id)

    total = await qs.count()
    items = await qs.offset((page - 1) * page_size).limit(page_size)
    actors = await _resolve_actor_names({it.who_hub_user_id for it in items})
    return {
        "items": [
            {
                "id": it.id,
                "actor_id": it.who_hub_user_id,
                "actor_name": actors.get(it.who_hub_user_id, "?"),
                "viewed_task_id": it.viewed_task_id,
                "viewed_at": it.viewed_at,
                "ip": it.ip,
            }
            for it in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
