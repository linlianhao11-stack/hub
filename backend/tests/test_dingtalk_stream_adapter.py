from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hub.ports import InboundMessage


@pytest.mark.asyncio
async def test_chatbot_handler_routes_inbound_to_callback():
    """SDK ChatbotHandler.process() 触发 → adapter 转 InboundMessage → 业务回调。"""
    from hub.adapters.channel.dingtalk_stream import _HubChatbotHandler

    received: list[InboundMessage] = []
    async def callback(msg: InboundMessage):
        received.append(msg)

    handler = _HubChatbotHandler(callback)

    # 模拟 SDK 传入的 callback 数据（dingtalk-stream 的 callback.data 结构）
    fake_callback = MagicMock()
    fake_callback.data = {
        "senderStaffId": "manager4521",
        "conversationId": "cid-1",
        "text": {"content": "查 SKU100"},
        "createAt": 1700000000000,
    }
    # SDK process 返回 (AckMessage.STATUS_OK, 'OK')
    result = await handler.process(fake_callback)

    assert len(received) == 1
    msg = received[0]
    assert msg.channel_type == "dingtalk"
    assert msg.channel_userid == "manager4521"
    assert msg.conversation_id == "cid-1"
    assert msg.content == "查 SKU100"
    assert msg.timestamp == 1700000000

    # 返回 SDK 期望的 ack 形态
    status, body = result
    assert body == "OK"


@pytest.mark.asyncio
async def test_start_registers_handler_with_sdk():
    """start() 通过 SDK Credential + register_callback_handler 注册 ChatbotHandler。"""
    from hub.adapters.channel.dingtalk_stream import DingTalkStreamAdapter

    with patch("hub.adapters.channel.dingtalk_stream.DingTalkStreamClient") as mock_client_cls, \
         patch("hub.adapters.channel.dingtalk_stream.Credential") as mock_cred_cls:
        mock_inst = mock_client_cls.return_value
        mock_inst.start = AsyncMock()
        mock_inst.register_callback_handler = MagicMock()

        adapter = DingTalkStreamAdapter(app_key="k", app_secret="s")
        async def cb(msg):
            pass
        adapter.on_message(cb)
        await adapter.start()

        mock_cred_cls.assert_called_once_with("k", "s")
        mock_inst.register_callback_handler.assert_called_once()
        mock_inst.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_handler_no_callback_warns_but_acks():
    """没注册业务回调时，handler 仍返回正常 ack（避免 SDK 重投）。"""
    from hub.adapters.channel.dingtalk_stream import _HubChatbotHandler
    handler = _HubChatbotHandler(callback=None)
    fake_callback = MagicMock()
    fake_callback.data = {"senderStaffId": "x", "text": {"content": "y"}}
    result = await handler.process(fake_callback)
    assert result[1] == "OK"


@pytest.mark.asyncio
async def test_handler_callback_failure_returns_system_exception():
    """业务回调抛错（如 runner.submit 失败）→ 返回 STATUS_SYSTEM_EXCEPTION 让钉钉重投。

    回归 P1：避免 Redis 短暂失败时静默 ACK 导致入站消息丢失。
    """
    from hub.adapters.channel.dingtalk_stream import AckMessage, _HubChatbotHandler

    async def failing_callback(msg):
        raise RuntimeError("redis down")

    handler = _HubChatbotHandler(failing_callback)
    fake_callback = MagicMock()
    fake_callback.data = {"senderStaffId": "u", "text": {"content": "hi"}, "createAt": 1700000000000}

    status, body = await handler.process(fake_callback)
    assert status == AckMessage.STATUS_SYSTEM_EXCEPTION
    assert body == "EX"


@pytest.mark.asyncio
async def test_handler_parse_failure_acks_to_avoid_infinite_retry():
    """消息体解析异常（数据本身坏）→ ACK 掉避免无限重投。"""
    from hub.adapters.channel.dingtalk_stream import _HubChatbotHandler

    received = []

    async def cb(msg):
        received.append(msg)

    handler = _HubChatbotHandler(cb)

    bad_callback = MagicMock()
    # data 直接抛异常的对象：模拟解析阶段坏数据
    type(bad_callback).data = property(lambda self: (_ for _ in ()).throw(ValueError("bad data")))

    status, body = await handler.process(bad_callback)
    assert body == "OK"
    assert received == []  # 业务回调没被调用
