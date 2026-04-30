"""Plan 6 Task 7：DingTalkSender.send_file 测试（5 case）。"""
import pytest
import httpx
from unittest.mock import AsyncMock


class _MultiResponseHandler:
    """按调用次序返预设 response 列表的 mock handler。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        if not self.responses:
            return httpx.Response(500)
        return self.responses.pop(0)


@pytest.fixture
def sender_factory():
    """工厂：装好 mock transport 的 DingTalkSender。"""
    from hub.adapters.channel.dingtalk_sender import DingTalkSender

    def _make(responses):
        handler = _MultiResponseHandler(responses)
        transport = httpx.MockTransport(handler)
        sender = DingTalkSender(
            app_key="y",
            app_secret="z",
            robot_code="rc",
            transport=transport,
        )
        # mock _get_access_token 直接返，跳过 gettoken 网络调用
        sender._get_access_token = AsyncMock(return_value="fake-token")
        return sender, handler

    return _make


async def test_send_file_uploads_then_batchsend(sender_factory):
    """send_file 先 upload 拿 media_id，再 batchSend 发消息（共 2 次请求）。"""
    sender, handler = sender_factory([
        # 1. upload media
        httpx.Response(200, json={"errcode": 0, "media_id": "m-123"}),
        # 2. batchSend（_send_oto）
        httpx.Response(200, json={"errcode": 0, "task_id": "t-1"}),
    ])
    await sender.send_file(
        dingtalk_userid="U1",
        file_bytes=b"hello-docx-bytes",
        file_name="test.docx",
        file_type="docx",
    )
    assert len(handler.calls) == 2  # upload + send


async def test_upload_5xx_retries_once_then_succeeds(sender_factory):
    """upload 遇 5xx 重试一次后成功，共 3 次请求（5xx + retry + batchSend）。"""
    sender, handler = sender_factory([
        httpx.Response(503),  # 第 1 次：5xx
        httpx.Response(200, json={"errcode": 0, "media_id": "m-456"}),  # 第 2 次：成功
        httpx.Response(200, json={"errcode": 0, "task_id": "t-2"}),  # batchSend
    ])
    await sender.send_file(
        dingtalk_userid="U1",
        file_bytes=b"x",
        file_name="t.docx",
    )
    assert len(handler.calls) == 3  # 5xx + retry + send


async def test_upload_4xx_raises_immediately(sender_factory):
    """upload 遇 4xx 立即抛 DingTalkSendError，不重试。"""
    from hub.adapters.channel.dingtalk_sender import DingTalkSendError

    sender, handler = sender_factory([
        httpx.Response(403, text="forbidden"),
    ])
    with pytest.raises(DingTalkSendError, match="403"):
        await sender.send_file(
            dingtalk_userid="U1",
            file_bytes=b"x",
            file_name="t.docx",
        )
    assert len(handler.calls) == 1  # 不重试


async def test_file_too_large_raises():
    """文件 > 20MB 立即抛 DingTalkSendError，不发起网络请求。"""
    from hub.adapters.channel.dingtalk_sender import DingTalkSender, DingTalkSendError

    sender = DingTalkSender(app_key="x", app_secret="y", robot_code="rc")
    big = b"\x00" * (21 * 1024 * 1024)  # 21MB

    with pytest.raises(DingTalkSendError, match="20MB"):
        await sender.send_file(
            dingtalk_userid="U1",
            file_bytes=big,
            file_name="big.docx",
        )


async def test_batchsend_failure_raises(sender_factory):
    """upload 成功但 batchSend 业务失败 → 抛 DingTalkSendError。"""
    from hub.adapters.channel.dingtalk_sender import DingTalkSendError

    sender, handler = sender_factory([
        httpx.Response(200, json={"errcode": 0, "media_id": "m-789"}),
        httpx.Response(200, json={"errcode": 1, "errmsg": "send failed"}),
    ])
    with pytest.raises(DingTalkSendError):
        await sender.send_file(
            dingtalk_userid="U1",
            file_bytes=b"x",
            file_name="t.docx",
        )
