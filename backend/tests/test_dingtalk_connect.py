import asyncio
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_connect_waits_then_starts_when_channel_app_appears():
    """初始无 ChannelApp → 启动 task → 写入 ChannelApp → adapter.start() 被调用。"""
    from hub.crypto import encrypt_secret
    from hub.lifecycle.dingtalk_connect import connect_dingtalk_stream_when_ready
    from hub.models import ChannelApp

    started = {"called": False, "args": None}

    class FakeAdapter:
        def __init__(self, *, app_key, app_secret, robot_id):
            self._on = None
            started["args"] = (app_key, app_secret, robot_id)

        def on_message(self, h):
            self._on = h

        async def start(self):
            started["called"] = True

        async def stop(self):
            pass

    async def on_inbound(msg):
        pass

    task = asyncio.create_task(connect_dingtalk_stream_when_ready(
        on_inbound=on_inbound, adapter_factory=FakeAdapter,
        poll_interval_seconds=0.05,
    ))

    await asyncio.sleep(0.15)
    assert started["called"] is False

    await ChannelApp.create(
        channel_type="dingtalk", name="dt",
        encrypted_app_key=encrypt_secret("fake_key", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("fake_secret", purpose="config_secrets"),
        robot_id="robot_x", status="active",
    )

    adapter = await asyncio.wait_for(task, timeout=2.0)
    assert started["called"] is True
    assert started["args"][0] == "fake_key"
    assert adapter is not None


@pytest.mark.asyncio
async def test_connect_returns_none_when_cancelled():
    """task 被 cancel → 返回 None，不抛异常。"""
    from hub.lifecycle.dingtalk_connect import connect_dingtalk_stream_when_ready

    async def on_inbound(msg):
        pass

    task = asyncio.create_task(connect_dingtalk_stream_when_ready(
        on_inbound=on_inbound,
        adapter_factory=lambda **kw: AsyncMock(),
        poll_interval_seconds=0.5,
    ))
    await asyncio.sleep(0.1)
    task.cancel()
    result = await task
    assert result is None


@pytest.mark.asyncio
async def test_connect_retries_on_adapter_start_failure():
    """adapter.start() 抛错 → 下一轮重试，不让 task 死掉。"""
    from hub.crypto import encrypt_secret
    from hub.lifecycle.dingtalk_connect import connect_dingtalk_stream_when_ready
    from hub.models import ChannelApp

    await ChannelApp.create(
        channel_type="dingtalk", name="dt",
        encrypted_app_key=encrypt_secret("k", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("s", purpose="config_secrets"),
        robot_id="r", status="active",
    )

    call_count = {"n": 0}

    class FlakyAdapter:
        def __init__(self, **kw):
            pass

        def on_message(self, h):
            pass

        async def start(self):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("first start fails")

        async def stop(self):
            pass

    async def on_inbound(msg):
        pass

    task = asyncio.create_task(connect_dingtalk_stream_when_ready(
        on_inbound=on_inbound, adapter_factory=FlakyAdapter,
        poll_interval_seconds=0.05,
    ))
    adapter = await asyncio.wait_for(task, timeout=2.0)
    assert adapter is not None
    assert call_count["n"] == 2
