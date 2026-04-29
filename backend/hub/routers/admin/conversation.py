"""admin 对话监控路由：SSE 实时流 + 历史搜索 + 历史详情（带解密）。

权限：所有 endpoint 要求 platform.conversation.monitor

设计要点：
- /live：StreamingResponse + Redis pubsub 订阅 conversation:live 频道
- /history：仅查 task_log 元数据，**不解密** payload（避免每次列表都触发 meta_audit）
- /history/{task_id}：解密 payload + 写 MetaAuditLog（与 admin tasks 详情一致）
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from hub.auth.admin_perms import require_hub_perm
from hub.crypto import decrypt_secret
from hub.models import MetaAuditLog, TaskLog, TaskPayload
from hub.observability.live_stream import LiveStreamSubscriber

logger = logging.getLogger("hub.admin.conversation")

router = APIRouter(prefix="/hub/v1/admin/conversation", tags=["admin-conversation"])


def _get_redis(request: Request):
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        runner = getattr(request.app.state, "task_runner", None)
        if runner is not None:
            redis = getattr(runner, "redis", None)
    return redis


@router.get(
    "/live",
    dependencies=[Depends(require_hub_perm("platform.conversation.monitor"))],
)
async def conversation_live(request: Request):
    """SSE 实时对话流。前端用 EventSource API 订阅。"""
    redis = _get_redis(request)
    if redis is None:
        raise HTTPException(503, "Redis 未就绪，无法订阅实时流")

    sub = LiveStreamSubscriber(redis)

    async def event_generator():
        try:
            async for raw in sub.stream():
                yield f"data: {raw}\n\n"
                if await request.is_disconnected():
                    break
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/history",
    dependencies=[Depends(require_hub_perm("platform.conversation.monitor"))],
)
async def conversation_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    channel_userid: str | None = None,
    status: str | None = None,
    since_hours: int | None = Query(24, ge=1, le=8760),
):
    """历史对话搜索（不解密 payload，只展示元数据）。"""
    qs = TaskLog.filter(task_type="dingtalk_inbound").order_by("-created_at")
    if channel_userid:
        qs = qs.filter(channel_userid=channel_userid)
    if status:
        qs = qs.filter(status=status)
    if since_hours:
        qs = qs.filter(
            created_at__gte=datetime.now(UTC) - timedelta(hours=since_hours),
        )
    if keyword:
        # 仅元数据中搜——避免遍历解密
        qs = qs.filter(error_summary__icontains=keyword)

    total = await qs.count()
    items = await qs.offset((page - 1) * page_size).limit(page_size)
    return {
        "items": [
            {
                "task_id": t.task_id,
                "channel_userid": t.channel_userid,
                "status": t.status,
                "intent_parser": t.intent_parser,
                "intent_confidence": t.intent_confidence,
                "duration_ms": t.duration_ms,
                "created_at": t.created_at,
                "error_summary": t.error_summary,
            }
            for t in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get(
    "/history/{task_id}",
    dependencies=[Depends(require_hub_perm("platform.conversation.monitor"))],
)
async def conversation_history_detail(request: Request, task_id: str):
    """历史详情：解密 payload → 触发 MetaAuditLog。"""
    task = await TaskLog.filter(task_id=task_id).first()
    if task is None:
        raise HTTPException(404, "对话不存在")

    payload = await TaskPayload.filter(task_log_id=task.id).first()
    payload_data = None
    if payload and payload.expires_at > datetime.now(UTC):
        try:
            request_text = decrypt_secret(payload.encrypted_request, purpose="task_payload")
            response_text = decrypt_secret(payload.encrypted_response, purpose="task_payload")
            erp_calls_str = (
                decrypt_secret(payload.encrypted_erp_calls, purpose="task_payload")
                if payload.encrypted_erp_calls
                else "[]"
            )
            try:
                erp_calls_parsed = json.loads(erp_calls_str) if erp_calls_str else []
            except json.JSONDecodeError:
                erp_calls_parsed = []
            payload_data = {
                "request_text": request_text,
                "response": response_text,
                "erp_calls": erp_calls_parsed,
            }
        except Exception:
            logger.exception("payload 解密失败 task_id=%s", task_id)
            payload_data = None

        actor = request.state.hub_user
        await MetaAuditLog.create(
            who_hub_user_id=actor.id,
            viewed_task_id=task_id,
            ip=request.client.host if request.client else None,
        )

    return {
        "task_log": {
            "task_id": task.task_id,
            "channel_userid": task.channel_userid,
            "status": task.status,
            "created_at": task.created_at,
            "finished_at": task.finished_at,
            "duration_ms": task.duration_ms,
            "intent_parser": task.intent_parser,
            "intent_confidence": task.intent_confidence,
            "error_summary": task.error_summary,
        },
        "payload": payload_data,
    }
