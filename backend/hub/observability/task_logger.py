"""task_log 写入 + task_payload 加密 + LiveStream 推流。

所有钉钉入站消息处理都过这个 context manager：
- 入口创建 TaskLog（status=running）
- 退出时写 finished_at + duration + 加密 TaskPayload（30 天 TTL）
- 同时**发布脱敏事件**到 Redis pubsub（前端 SSE 实时流订阅）

设计原则：
- 业务永远不被可观察性阻塞——payload 加密失败 / live publish 失败仅打 warning
- final_status 由 handler 内部明确设置（success / failed_user）；未设值默认 success
- 异常向上抛由 task_logger 统一打 failed_system_final，并继续重抛让 WorkerRuntime 死信兜底
"""
from __future__ import annotations

import json
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from hub.crypto import encrypt_secret
from hub.models import TaskLog, TaskPayload

logger = logging.getLogger("hub.observability.task_logger")

# PII 脱敏正则（spec §14.4）
_RE_PHONE = re.compile(r"(\d{3})\d{4}(\d{4})")
_RE_BANK = re.compile(r"(\d{4})\d{8}(\d{4})")
_RE_IDCARD = re.compile(r"(\d{6})\d{8}(\w{4})")


def _redact(text: str | None, max_len: int = 100) -> str:
    """对实时流脱敏：截断 + 手机号/身份证号/银行卡号正则脱敏（spec §14.4 PII）。"""
    if not text:
        return ""
    s = text[:max_len]
    s = _RE_PHONE.sub(r"\1****\2", s)
    s = _RE_BANK.sub(r"\1********\2", s)
    s = _RE_IDCARD.sub(r"\1********\2", s)
    return s


@asynccontextmanager
async def log_inbound_task(
    *,
    task_id: str,
    channel_userid: str,
    content: str,
    conversation_id: str,
    live_publisher=None,
    payload_ttl_days: int = 30,
):
    """Context manager：写 TaskLog + 加密 TaskPayload + 推 SSE 实时事件。

    Yields 一个 dict（record），handler 可写入：
        request_text / response / erp_calls / final_status /
        intent_parser / intent_confidence / error_summary
    """
    started = time.monotonic()
    task = await TaskLog.create(
        task_id=task_id,
        task_type="dingtalk_inbound",
        channel_type="dingtalk",
        channel_userid=channel_userid,
        status="running",
    )

    record: dict = {
        "request_text": content,
        "conversation_id": conversation_id,
        "erp_calls": [],
    }
    raised: BaseException | None = None

    try:
        yield record
        status = record.get("final_status", "success")
    except Exception as e:
        status = "failed_system_final"
        record["error_summary"] = str(e)[:500]
        raised = e
    finally:
        # 1. 更新 task_log 元数据
        task.status = status
        task.finished_at = datetime.now(UTC)
        task.duration_ms = int((time.monotonic() - started) * 1000)
        if "error_summary" in record:
            task.error_summary = record["error_summary"]
        if "intent_parser" in record:
            task.intent_parser = record["intent_parser"]
            task.intent_confidence = record.get("intent_confidence")
        try:
            await task.save()
        except Exception:
            logger.exception("task_log 保存失败（不影响业务）")

        # 2. 加密 payload（30 天 TTL）
        try:
            await TaskPayload.create(
                task_log=task,
                encrypted_request=encrypt_secret(
                    record.get("request_text", "") or "",
                    purpose="task_payload",
                ),
                encrypted_erp_calls=encrypt_secret(
                    json.dumps(record.get("erp_calls", []), ensure_ascii=False),
                    purpose="task_payload",
                ),
                encrypted_response=encrypt_secret(
                    record.get("response", "") or "",
                    purpose="task_payload",
                ),
                expires_at=datetime.now(UTC)
                + timedelta(days=payload_ttl_days),
            )
        except Exception:
            logger.exception("task_payload 写入失败（不影响业务）")

        # 3. 发布脱敏事件到 SSE（不阻塞业务；publish 失败仅 warning）
        if live_publisher is not None:
            try:
                await live_publisher.publish({
                    "task_id": task_id,
                    "channel_userid": channel_userid,
                    "status": status,
                    "duration_ms": task.duration_ms,
                    "intent_parser": record.get("intent_parser"),
                    "intent_confidence": record.get("intent_confidence"),
                    "request_preview": _redact(record.get("request_text", "")),
                    "response_preview": _redact(record.get("response", "")),
                    "error_summary": record.get("error_summary"),
                    "timestamp": int(time.time()),
                })
            except Exception:
                logger.warning("LiveStream publish 失败，不影响业务", exc_info=True)

        if raised is not None:
            raise raised
