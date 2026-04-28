"""ERP 反向回调到 HUB 的入口（confirm-final / 钉钉员工事件等）。"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Body, Header, HTTPException, Request
from pydantic import BaseModel

from hub.config import get_settings
from hub.services.binding_service import BindingService

router = APIRouter(prefix="/hub/v1/internal", tags=["internal_callbacks"])


def _verify_erp_secret(x_erp_secret: str | None) -> None:
    """共享密钥校验（ERP → HUB 用）。"""
    if x_erp_secret is None:
        raise HTTPException(status_code=401, detail="缺少 X-ERP-Secret 头")
    expected = get_settings().erp_to_hub_secret
    if not expected:
        raise HTTPException(status_code=503, detail="HUB_ERP_TO_HUB_SECRET 未配置")
    if not secrets.compare_digest(x_erp_secret, expected):
        raise HTTPException(status_code=403, detail="X-ERP-Secret 不匹配")


class ConfirmFinalRequest(BaseModel):
    token_id: int
    erp_user_id: int
    erp_username: str
    erp_display_name: str
    dingtalk_userid: str


@router.post("/binding/confirm-final")
async def confirm_final(
    request: Request,
    payload: ConfirmFinalRequest = Body(...),
    x_erp_secret: str | None = Header(default=None, alias="X-ERP-Secret"),
):
    """ERP 个人中心二次确认成功后调用。

    HTTP 状态码语义（关键，避免 ERP 误标 binding code 为 used）：
    - 200：成功（首次创建 / 复活 / already_consumed）；ERP 可放心 mark used
    - 409 Conflict：业务冲突（dingtalk 已绑别人 / ERP 用户已被占用）；ERP 不应 mark used
    - 401/403：鉴权失败
    - 5xx：HUB 内部错误
    """
    _verify_erp_secret(x_erp_secret)

    svc = BindingService(erp_adapter=None)
    result = await svc.confirm_final(
        token_id=payload.token_id,
        dingtalk_userid=payload.dingtalk_userid,
        erp_user_id=payload.erp_user_id,
        erp_username=payload.erp_username,
        erp_display_name=payload.erp_display_name,
    )

    # 冲突场景：返回 409 让 ERP raise_for_status 抛错，不消费 binding code
    if not result.success and result.note and result.note.startswith("conflict_"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": result.note,
                "message": result.reply_text,
            },
        )

    # 仅"首次成功创建/激活/换绑 ERP"才投递通知；replay / already_active 不重复发
    if result.success and result.note in ("created", "reactivated", "reactivated_with_new_erp"):
        runner = getattr(request.app.state, "task_runner", None)
        if runner:
            from hub import messages as msgs
            await runner.submit("dingtalk_outbound", {
                "channel_userid": payload.dingtalk_userid,
                "type": "text",
                "text": msgs.binding_success(payload.erp_display_name),
            })
            await runner.submit("dingtalk_outbound", {
                "channel_userid": payload.dingtalk_userid,
                "type": "text",
                "text": msgs.privacy_notice(),
            })

    return {
        "success": result.success,
        "hub_user_id": result.hub_user_id,
        "note": result.note,
    }
