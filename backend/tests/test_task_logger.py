"""task_logger context manager 行为测试。"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from hub.crypto import decrypt_secret
from hub.models import TaskLog, TaskPayload
from hub.observability.task_logger import _redact, log_inbound_task


@pytest.mark.asyncio
async def test_success_path_writes_status_and_payload():
    """正常退出 → status=success + payload 加密入库。"""
    async with log_inbound_task(
        task_id="t-success",
        channel_userid="m1",
        content="查 SKU100",
        conversation_id="c1",
    ) as record:
        record["response"] = "鼠标 ¥120"
        record["intent_parser"] = "rule"
        record["intent_confidence"] = 0.95
        record["final_status"] = "success"

    task = await TaskLog.get(task_id="t-success")
    assert task.status == "success"
    assert task.intent_parser == "rule"
    assert task.intent_confidence == 0.95
    assert task.duration_ms is not None
    assert task.finished_at is not None

    payload = await TaskPayload.get(task_log_id=task.id)
    assert decrypt_secret(payload.encrypted_request, purpose="task_payload") == "查 SKU100"
    assert decrypt_secret(payload.encrypted_response, purpose="task_payload") == "鼠标 ¥120"
    erp_calls = json.loads(decrypt_secret(payload.encrypted_erp_calls, purpose="task_payload"))
    assert erp_calls == []


@pytest.mark.asyncio
async def test_failed_user_status_persisted():
    """用户错误（如未绑定）→ failed_user。"""
    async with log_inbound_task(
        task_id="t-failed-user",
        channel_userid="m2",
        content="查 SKU",
        conversation_id="c1",
    ) as record:
        record["response"] = "请先绑定"
        record["final_status"] = "failed_user"

    task = await TaskLog.get(task_id="t-failed-user")
    assert task.status == "failed_user"


@pytest.mark.asyncio
async def test_exception_marks_failed_system_final_and_reraises():
    """handler 抛异常 → task_log status=failed_system_final + error_summary，并重抛。"""
    with pytest.raises(RuntimeError, match="boom"):
        async with log_inbound_task(
            task_id="t-bang",
            channel_userid="m3",
            content="random",
            conversation_id="c1",
        ):
            raise RuntimeError("boom")

    task = await TaskLog.get(task_id="t-bang")
    assert task.status == "failed_system_final"
    assert "boom" in (task.error_summary or "")


@pytest.mark.asyncio
async def test_payload_ttl_30_days():
    """payload 默认 30 天 TTL，expires_at 大约等于现在 + 30 天。"""
    async with log_inbound_task(
        task_id="t-ttl",
        channel_userid="m4",
        content="x",
        conversation_id="c1",
    ) as record:
        record["final_status"] = "success"

    task = await TaskLog.get(task_id="t-ttl")
    payload = await TaskPayload.get(task_log_id=task.id)
    delta = payload.expires_at - datetime.now(UTC)
    # 允许 +/- 1 天浮动
    assert 29 <= delta.days <= 30


@pytest.mark.asyncio
async def test_redact_phone_number():
    assert _redact("13812345678") == "138****5678"
    assert _redact("给 13812345678 发货") == "给 138****5678 发货"


@pytest.mark.asyncio
async def test_redact_truncates_long_text():
    """实时流脱敏 + 长度截断到 100 字符。"""
    long_text = "a" * 200
    assert len(_redact(long_text)) == 100


@pytest.mark.asyncio
async def test_live_publisher_called_on_success():
    """注入 publisher → finally 阶段 publish 一次（脱敏后的 event）。"""
    captured: list = []

    class FakePublisher:
        async def publish(self, event):
            captured.append(event)

    async with log_inbound_task(
        task_id="t-pub",
        channel_userid="m5",
        content="给 13812345678 报价",
        conversation_id="c1",
        live_publisher=FakePublisher(),
    ) as record:
        record["response"] = "ok"
        record["final_status"] = "success"

    assert len(captured) == 1
    event = captured[0]
    assert event["task_id"] == "t-pub"
    assert event["status"] == "success"
    # 手机号脱敏
    assert "13812345678" not in event["request_preview"]
    assert "138****5678" in event["request_preview"]


@pytest.mark.asyncio
async def test_live_publisher_failure_does_not_break_business():
    """publisher 抛异常时不冒泡（业务永远优先）。"""

    class BrokenPublisher:
        async def publish(self, event):
            raise RuntimeError("redis is down")

    async with log_inbound_task(
        task_id="t-pub-fail",
        channel_userid="m6",
        content="x",
        conversation_id="c1",
        live_publisher=BrokenPublisher(),
    ) as record:
        record["final_status"] = "success"

    # task_log 仍然成功写入
    task = await TaskLog.get(task_id="t-pub-fail")
    assert task.status == "success"
