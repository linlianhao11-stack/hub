"""HUB 后台：消息渠道（钉钉等）配置 API。

提供 4 个 endpoint：
- POST /            创建渠道（app_key/app_secret 加密入库）
- GET  /            列表（不返回明文 secret）
- PUT  /{id}        改 secret/robot_id（rotate）
- POST /{id}/disable 停用渠道

权限码：platform.apikeys.write。
update / disable 完成后会 set lifespan 内的 dingtalk_reload_event，
让 connect_with_reload 后台 task 拿到新配置重启 Stream。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from hub.auth.admin_perms import require_hub_perm
from hub.crypto import encrypt_secret
from hub.models import AuditLog, ChannelApp

router = APIRouter(prefix="/hub/v1/admin/channels", tags=["admin-channels"])


class CreateChannelRequest(BaseModel):
    channel_type: str  # dingtalk
    name: str
    app_key: str
    app_secret: str
    robot_id: str | None = None


@router.post("", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def create_channel(request: Request, body: CreateChannelRequest = Body(...)):
    rec = await ChannelApp.create(
        channel_type=body.channel_type,
        name=body.name,
        encrypted_app_key=encrypt_secret(body.app_key, purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret(body.app_secret, purpose="config_secrets"),
        robot_id=body.robot_id,
        status="active",
    )
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id,
        action="create_channel",
        target_type="channel_app",
        target_id=str(rec.id),
        detail={"channel_type": body.channel_type, "name": body.name},
    )
    # 新增 active channel 后，让 connect_with_reload 重新加载
    _signal_channel_reload(request)
    return {"id": rec.id}


@router.get("", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def list_channels():
    items = await ChannelApp.all().order_by("-created_at")
    return {
        "items": [
            {
                "id": c.id,
                "channel_type": c.channel_type,
                "name": c.name,
                "robot_id": c.robot_id,
                "status": c.status,
                "secret_set": True,
            }
            for c in items
        ],
    }


class UpdateSecretRequest(BaseModel):
    app_key: str | None = None
    app_secret: str | None = None
    robot_id: str | None = None


@router.put("/{ca_id}", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def update_channel(request: Request, ca_id: int, body: UpdateSecretRequest = Body(...)):
    rec = await ChannelApp.filter(id=ca_id).first()
    if rec is None:
        raise HTTPException(404, "渠道不存在")
    if body.app_key is not None:
        rec.encrypted_app_key = encrypt_secret(body.app_key, purpose="config_secrets")
    if body.app_secret is not None:
        rec.encrypted_app_secret = encrypt_secret(body.app_secret, purpose="config_secrets")
    if body.robot_id is not None:
        rec.robot_id = body.robot_id
    await rec.save()
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id,
        action="update_channel",
        target_type="channel_app",
        target_id=str(ca_id),
    )
    # 必须在代码块内调用，让运行中的 Stream 拿到新配置；漏掉就退化回"改完不生效"
    _signal_channel_reload(request)
    return {"success": True}


@router.post("/{ca_id}/disable", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def disable_channel(request: Request, ca_id: int):
    rec = await ChannelApp.filter(id=ca_id).first()
    if rec is None:
        raise HTTPException(404, "渠道不存在")
    rec.status = "disabled"
    await rec.save()
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id,
        action="disable_channel",
        target_type="channel_app",
        target_id=str(ca_id),
    )
    # 通知运行中的 Stream 重连（拿到新配置 / 停止已 disable 的）
    _signal_channel_reload(request)
    return {"success": True}


def _signal_channel_reload(request: Request) -> None:
    """通知 gateway 后台 task 重新加载 ChannelApp 并重启 Stream。

    create_channel / update_channel / disable_channel 完成后调用。Plan 5 在 lifespan 加了
    app.state.dingtalk_reload_event = asyncio.Event()，
    且 connect_dingtalk_stream_when_ready 是循环结构（见 connect_with_reload）。
    若 event 不存在（worker 进程或测试环境）则静默忽略。
    """
    evt = getattr(request.app.state, "dingtalk_reload_event", None)
    if evt is not None:
        evt.set()
