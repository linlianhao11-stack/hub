"""cron job 函数：构造依赖 + 调用业务 + 错误重试。

每个 job 都要：
1. 处理"配置缺失"场景（无 ChannelApp / disabled）→ 跳过 + WARN 日志
2. 处理 OpenAPI 调用失败 → 重试 1 次 → 仍失败则 ERROR 日志（不抛异常，避免炸 scheduler）
3. 关闭 httpx client（finally aclose）
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from hub.cron.dingtalk_user_client import DingTalkUserClient, DingTalkUserClientError
from hub.cron.dingtalk_user_sync import daily_employee_audit
from hub.cron.task_payload_cleanup import cleanup_expired_task_payloads
from hub.crypto import decrypt_secret
from hub.models import ChannelApp

logger = logging.getLogger("hub.cron.jobs")


async def _load_active_dingtalk_app() -> ChannelApp | None:
    return await ChannelApp.filter(
        channel_type="dingtalk", status="active",
    ).first()


async def run_daily_audit() -> dict | None:
    """每日凌晨：拉钉钉企业现役员工 → 标记离职用户 binding 为 revoked。

    返回 daily_employee_audit 的统计字典，无可用配置则返回 None。
    OpenAPI 失败重试一次；2 次都失败也只记 ERROR 日志，不抛异常。
    """
    app = await _load_active_dingtalk_app()
    if app is None:
        logger.warning("daily_audit 跳过：没有 active 状态的 dingtalk ChannelApp")
        return None

    try:
        app_key = decrypt_secret(
            app.encrypted_app_key, purpose="config_secrets",
        )
        app_secret = decrypt_secret(
            app.encrypted_app_secret, purpose="config_secrets",
        )
    except Exception:
        logger.exception("daily_audit 跳过：ChannelApp 解密失败")
        return None

    client = DingTalkUserClient(app_key=app_key, app_secret=app_secret)
    last_err: Exception | None = None
    try:
        for attempt in (1, 2):
            try:
                stats = await daily_employee_audit(client)
                logger.info(f"daily_audit 完成: {stats}")
                return stats
            except (DingTalkUserClientError, httpx.HTTPError) as e:
                last_err = e
                logger.warning(f"daily_audit 第 {attempt} 次失败: {e}")
                if attempt < 2:
                    await asyncio.sleep(5)
        logger.error(f"daily_audit 重试 2 次仍失败: {last_err}")
        return None
    finally:
        await client.aclose()


async def run_payload_cleanup() -> int:
    """每日凌晨：删除过期 task_payload。"""
    try:
        n = await cleanup_expired_task_payloads()
        return n
    except Exception:
        logger.exception("payload cleanup 失败")
        return 0
