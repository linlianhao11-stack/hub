"""LiveStream Publisher / Subscriber 单元 + 端到端测试。"""
from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio
from fakeredis import aioredis as fakeredis_aio

from hub.observability.live_stream import (
    CHANNEL,
    LiveStreamPublisher,
    LiveStreamSubscriber,
)
from hub.observability.task_logger import log_inbound_task


@pytest_asyncio.fixture
async def fake_redis():
    c = fakeredis_aio.FakeRedis()
    yield c
    await c.aclose()


@pytest.mark.asyncio
async def test_publisher_writes_to_channel(fake_redis):
    """publish 把 JSON 字符串发到 conversation:live。"""
    pub = LiveStreamPublisher(fake_redis)
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(CHANNEL)
    # consume 订阅确认
    await asyncio.sleep(0.01)

    received = []

    async def consume():
        async for msg in pubsub.listen():
            if msg.get("type") == "message":
                received.append(msg["data"])
                break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.01)

    await pub.publish({"task_id": "t1", "status": "success"})
    await asyncio.wait_for(consumer, timeout=2.0)
    await pubsub.unsubscribe(CHANNEL)
    await pubsub.aclose()

    assert len(received) == 1
    payload = received[0]
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    event = json.loads(payload)
    assert event == {"task_id": "t1", "status": "success"}


@pytest.mark.asyncio
async def test_subscriber_yields_decoded_strings(fake_redis):
    """LiveStreamSubscriber.stream 把 bytes 自动解码成 str。"""
    pub = LiveStreamPublisher(fake_redis)
    sub = LiveStreamSubscriber(fake_redis)

    received: list = []

    async def consume():
        async for raw in sub.stream():
            received.append(raw)
            break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    await pub.publish({"hello": "world"})
    await asyncio.wait_for(consumer, timeout=2.0)

    assert len(received) == 1
    assert isinstance(received[0], str)
    assert json.loads(received[0]) == {"hello": "world"}


@pytest.mark.asyncio
async def test_inbound_task_publishes_live_event(fake_redis):
    """端到端：log_inbound_task finally → publish → subscriber 拿到事件。"""
    pub = LiveStreamPublisher(fake_redis)
    sub = LiveStreamSubscriber(fake_redis)

    received: list = []

    async def consume():
        async for raw in sub.stream():
            received.append(raw)
            break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)

    async with log_inbound_task(
        task_id="e2e-1",
        channel_userid="m1",
        content="查 SKU100",
        conversation_id="c1",
        live_publisher=pub,
    ) as record:
        record["intent_parser"] = "rule"
        record["intent_confidence"] = 0.95
        record["response"] = "鼠标 ¥120"
        record["final_status"] = "success"

    await asyncio.wait_for(consumer, timeout=2.0)
    assert len(received) == 1
    event = json.loads(received[0])
    assert event["task_id"] == "e2e-1"
    assert event["channel_userid"] == "m1"
    assert event["status"] == "success"
    assert event["intent_parser"] == "rule"
    assert "鼠标" in event["response_preview"]


@pytest.mark.asyncio
async def test_inbound_task_redacts_phone_number_in_request(fake_redis):
    """request_preview 必须脱敏手机号。"""
    pub = LiveStreamPublisher(fake_redis)
    sub = LiveStreamSubscriber(fake_redis)

    received: list = []

    async def consume():
        async for raw in sub.stream():
            received.append(raw)
            break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)

    async with log_inbound_task(
        task_id="e2e-redact",
        channel_userid="m1",
        content="给客户 13812345678 报价",
        conversation_id="c1",
        live_publisher=pub,
    ) as record:
        record["final_status"] = "success"

    await asyncio.wait_for(consumer, timeout=2.0)
    event = json.loads(received[0])
    assert "13812345678" not in event["request_preview"]
    assert "138****5678" in event["request_preview"]
