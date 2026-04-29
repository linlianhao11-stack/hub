"""清理过期 task_payload（PII 30 天 TTL）。

TaskPayload 写入时 expires_at = now + 30 天（Plan 2 task_logger 设置）。
本 job 删除 expires_at <= now 的记录。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger("hub.cron.task_payload_cleanup")


async def cleanup_expired_task_payloads() -> int:
    """删除 expires_at <= now 的 task_payload，返回删除条数。"""
    from hub.models import TaskPayload
    n = await TaskPayload.filter(
        expires_at__lte=datetime.now(UTC),
    ).delete()
    logger.info(f"清理过期 task_payload: {n} 条")
    return n
