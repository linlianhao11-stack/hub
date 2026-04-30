"""admin tasks 路由：列表 + 详情（详情解密 payload 触发 meta_audit_log）。

权限：
- 列表：platform.tasks.read（看元数据）
- 详情（带 payload）：platform.conversation.monitor（更敏感，需要单独权限）
- 详情触发 MetaAuditLog（"谁看了谁的 task"）

Plan 6 Task 13 升级：task 详情末尾附加 conversation_log + tool_calls（agent 决策链）。
关联策略：channel_userid + task.created_at ±30s 时间窗口模糊匹配 ConversationLog；
命中 0 / >1 时返 null（避免错配），不影响既有字段。
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from hub.auth.admin_perms import require_hub_perm
from hub.crypto import decrypt_secret
from hub.models import MetaAuditLog, TaskLog, TaskPayload
from hub.models.conversation import ConversationLog, ToolCallLog

logger = logging.getLogger("hub.admin.tasks")

router = APIRouter(prefix="/hub/v1/admin/tasks", tags=["admin-tasks"])


@router.get("", dependencies=[Depends(require_hub_perm("platform.tasks.read"))])
async def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: str | None = None,
    task_type: str | None = None,
    status: str | None = None,
    since_hours: int | None = Query(None, ge=1, le=8760),
):
    qs = TaskLog.all().order_by("-created_at")
    if user_id:
        qs = qs.filter(channel_userid=user_id)
    if task_type:
        qs = qs.filter(task_type=task_type)
    if status:
        qs = qs.filter(status=status)
    if since_hours:
        qs = qs.filter(
            created_at__gte=datetime.now(UTC) - timedelta(hours=since_hours),
        )

    total = await qs.count()
    items = await qs.offset((page - 1) * page_size).limit(page_size)
    return {
        "items": [
            {
                "task_id": t.task_id,
                "task_type": t.task_type,
                "channel_userid": t.channel_userid,
                "status": t.status,
                "created_at": t.created_at,
                "finished_at": t.finished_at,
                "duration_ms": t.duration_ms,
                "intent_parser": t.intent_parser,
                "intent_confidence": t.intent_confidence,
                "error_summary": t.error_summary,
            }
            for t in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get(
    "/{task_id}",
    dependencies=[Depends(require_hub_perm("platform.conversation.monitor"))],
)
async def get_task_detail(request: Request, task_id: str):
    """获取 task 详情（含 payload）→ 触发 meta_audit_log。

    payload 30 天 TTL：超期直接返 None 不解密；进入详情才写 meta 审计。

    已知限制（Plan 6 Task 13 第一版）：
    多轮对话只能匹配首轮 task；后续轮次因 ConversationLog.started_at 锚定首轮，
    落出 ±30s 窗口 → 关联失败。长期修复方向：ConversationLog 加 turn_idx 字段，
    或在 TaskLog 直接加 conversation_id 字段精确关联。
    """
    task = await TaskLog.filter(task_id=task_id).first()
    if task is None:
        raise HTTPException(404, "任务不存在")

    payload = await TaskPayload.filter(task_log_id=task.id).first()
    payload_data = None
    erp_calls_parsed: list = []
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
            logger.exception("task_payload 解密失败 task_id=%s", task_id)
            payload_data = None

        # 只有进入详情页（解密 payload）才触发 meta_audit
        actor = request.state.hub_user
        await MetaAuditLog.create(
            who_hub_user_id=actor.id,
            viewed_task_id=task_id,
            ip=request.client.host if request.client else None,
        )

    # === Plan 6 Task 13：关联 agent 决策链（conversation_log + tool_calls）===
    # 策略：用 channel_userid + task.created_at ±30s 时间窗口模糊匹配 ConversationLog。
    # 命中 0 / >1 时均返 null，避免错配（task 不一定走 agent 路径）。
    conversation_log_dict = None
    tool_calls_list: list[dict] = []

    if task.channel_userid and task.created_at:
        time_window_start = task.created_at - timedelta(seconds=30)
        time_window_end = task.created_at + timedelta(seconds=30)
        candidates = await ConversationLog.filter(
            channel_userid=task.channel_userid,
            started_at__gte=time_window_start,
            started_at__lte=time_window_end,
        ).all()

        if len(candidates) == 1:
            conv = candidates[0]
            tokens_cost_yuan = (
                float(conv.tokens_cost_yuan) if conv.tokens_cost_yuan is not None else None
            )
            conversation_log_dict = {
                "conversation_id": conv.conversation_id,
                "rounds_count": conv.rounds_count,
                "tokens_used": conv.tokens_used,
                "tokens_cost_yuan": tokens_cost_yuan,
                "final_status": conv.final_status,
                "error_summary": conv.error_summary,
                "started_at": conv.started_at.isoformat() if conv.started_at else None,
                "ended_at": conv.ended_at.isoformat() if conv.ended_at else None,
            }
            # 拉对应 tool_calls，按 round_idx + called_at 升序
            tool_logs = await ToolCallLog.filter(
                conversation_id=conv.conversation_id,
            ).order_by("round_idx", "called_at").all()
            for log in tool_logs:
                tool_calls_list.append({
                    "id": log.id,
                    "round_idx": log.round_idx,
                    "tool_name": log.tool_name,
                    "args_json": log.args_json,
                    "result_json": log.result_json,
                    "duration_ms": log.duration_ms,
                    "error": log.error,
                    "called_at": log.called_at.isoformat() if log.called_at else None,
                })

    return {
        "task_log": {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "channel_userid": task.channel_userid,
            "status": task.status,
            "created_at": task.created_at,
            "finished_at": task.finished_at,
            "duration_ms": task.duration_ms,
            "intent_parser": task.intent_parser,
            "intent_confidence": task.intent_confidence,
            "error_summary": task.error_summary,
            "retry_count": task.retry_count,
        },
        "payload": payload_data,
        "conversation_log": conversation_log_dict,
        "tool_calls": tool_calls_list,
    }
