"""HUB 后台：下游系统（ERP/CRM 等）配置 API。

提供 5 个 endpoint：
- POST /            创建下游系统（ApiKey 加密入库）
- GET  /            列表（不返回明文 ApiKey，只返 apikey_set 提示）
- PUT  /{id}/apikey 改 ApiKey（rotate） / 改 scopes
- POST /{id}/test-connection  测试连接（仅支持 erp 类型）
- POST /{id}/disable          吊销/停用

权限码：platform.apikeys.write。
所有写操作都写 audit_log。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from hub.auth.admin_perms import require_hub_perm
from hub.crypto import decrypt_secret, encrypt_secret
from hub.models import AuditLog, DownstreamSystem

router = APIRouter(prefix="/hub/v1/admin/downstreams", tags=["admin-downstreams"])


class CreateDownstreamRequest(BaseModel):
    downstream_type: str  # erp / crm / ...
    name: str
    base_url: str
    api_key: str  # 明文，加密存储
    apikey_scopes: list[str]


@router.post("", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def create_downstream(request: Request, body: CreateDownstreamRequest = Body(...)):
    encrypted = encrypt_secret(body.api_key, purpose="config_secrets")
    ds = await DownstreamSystem.create(
        downstream_type=body.downstream_type,
        name=body.name,
        base_url=body.base_url,
        encrypted_apikey=encrypted,
        apikey_scopes=body.apikey_scopes,
        status="active",
    )
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id,
        action="create_downstream",
        target_type="downstream_system",
        target_id=str(ds.id),
        detail={"downstream_type": body.downstream_type, "name": body.name},
    )
    return {"id": ds.id, "name": ds.name}


@router.get("", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def list_downstreams():
    items = await DownstreamSystem.all().order_by("-created_at")
    return {
        "items": [
            {
                "id": d.id,
                "downstream_type": d.downstream_type,
                "name": d.name,
                "base_url": d.base_url,
                "apikey_scopes": d.apikey_scopes,
                "status": d.status,
                # 不返回 encrypted_apikey 明文，仅返回长度提示
                "apikey_set": True,
            }
            for d in items
        ],
    }


class UpdateApiKeyRequest(BaseModel):
    api_key: str
    apikey_scopes: list[str] | None = None


@router.put(
    "/{ds_id}/apikey",
    dependencies=[Depends(require_hub_perm("platform.apikeys.write"))],
)
async def update_apikey(request: Request, ds_id: int, body: UpdateApiKeyRequest = Body(...)):
    ds = await DownstreamSystem.filter(id=ds_id).first()
    if ds is None:
        raise HTTPException(404, "下游系统不存在")
    ds.encrypted_apikey = encrypt_secret(body.api_key, purpose="config_secrets")
    if body.apikey_scopes is not None:
        ds.apikey_scopes = body.apikey_scopes
    await ds.save()

    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id,
        action="update_downstream_apikey",
        target_type="downstream_system",
        target_id=str(ds_id),
        detail={},
    )
    return {"success": True}


@router.post(
    "/{ds_id}/test-connection",
    dependencies=[Depends(require_hub_perm("platform.apikeys.write"))],
)
async def test_connection(ds_id: int):
    ds = await DownstreamSystem.filter(id=ds_id).first()
    if ds is None:
        raise HTTPException(404, "下游系统不存在")

    if ds.downstream_type != "erp":
        raise HTTPException(400, f"暂不支持测试 {ds.downstream_type} 类型连接")

    from hub.adapters.downstream.erp4 import Erp4Adapter
    api_key = decrypt_secret(ds.encrypted_apikey, purpose="config_secrets")
    adapter = Erp4Adapter(base_url=ds.base_url, api_key=api_key)
    try:
        ok = await adapter.health_check()
    finally:
        await adapter.aclose()
    return {"ok": ok}


@router.post(
    "/{ds_id}/disable",
    dependencies=[Depends(require_hub_perm("platform.apikeys.write"))],
)
async def disable_downstream(request: Request, ds_id: int):
    ds = await DownstreamSystem.filter(id=ds_id).first()
    if ds is None:
        raise HTTPException(404, "下游系统不存在")
    ds.status = "disabled"
    await ds.save()
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id,
        action="disable_downstream",
        target_type="downstream_system",
        target_id=str(ds_id),
    )
    return {"success": True}
