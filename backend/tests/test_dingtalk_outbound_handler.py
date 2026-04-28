from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_outbound_text_calls_sender():
    from hub.handlers.dingtalk_outbound import handle_outbound

    sender = AsyncMock()
    payload = {
        "task_id": "t1", "task_type": "dingtalk_outbound",
        "payload": {"channel_userid": "u1", "type": "text", "text": "hi"},
    }
    await handle_outbound(payload, sender=sender)

    sender.send_text.assert_awaited_once_with(dingtalk_userid="u1", text="hi")


@pytest.mark.asyncio
async def test_outbound_markdown_calls_sender():
    from hub.handlers.dingtalk_outbound import handle_outbound

    sender = AsyncMock()
    payload = {
        "task_id": "t2", "task_type": "dingtalk_outbound",
        "payload": {
            "channel_userid": "u1", "type": "markdown",
            "title": "T", "markdown": "# x",
        },
    }
    await handle_outbound(payload, sender=sender)

    sender.send_markdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_outbound_unknown_type_raises():
    from hub.handlers.dingtalk_outbound import handle_outbound

    sender = AsyncMock()
    payload = {
        "task_id": "t3", "task_type": "dingtalk_outbound",
        "payload": {"channel_userid": "u1", "type": "weird"},
    }
    with pytest.raises(ValueError):
        await handle_outbound(payload, sender=sender)
