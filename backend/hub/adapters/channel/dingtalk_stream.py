"""DingTalkStreamAdapter：钉钉 Stream 入站消息接入。

**仅 gateway 进程持有此 adapter**——Stream 是单一长连接，多进程同时连会导致重复收消息。
出站消息走 DingTalkSender（HTTP OpenAPI），无连接冲突，gateway / worker 都可用。

SDK 真实 API（dingtalk-stream PyPI 官方示例）：
- Credential(app_key, app_secret)
- DingTalkStreamClient(credential).register_callback_handler(topic, handler_inst)
- ChatbotHandler.process(callback) → (AckMessage.STATUS_OK, 'OK')
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from hub.ports import InboundMessage

logger = logging.getLogger("hub.adapter.dingtalk_stream")

try:
    from dingtalk_stream import (
        AckMessage,
        ChatbotHandler,
        ChatbotMessage,
        Credential,
        DingTalkStreamClient,
    )
except ImportError:
    DingTalkStreamClient = None
    Credential = None
    ChatbotHandler = object  # 测试环境占位
    ChatbotMessage = None
    AckMessage = type("AckMessage", (), {"STATUS_OK": "OK", "STATUS_SYSTEM_EXCEPTION": "EX"})


InboundCallback = Callable[[InboundMessage], Awaitable[None]] | None


class _HubChatbotHandler(ChatbotHandler):
    """SDK ChatbotHandler 子类：把钉钉 callback 转 InboundMessage 后调用业务回调。"""

    def __init__(self, callback: InboundCallback):
        if hasattr(ChatbotHandler, "__init__") and ChatbotHandler is not object:
            try:
                super().__init__()
            except Exception:
                pass
        self._callback = callback

    async def process(self, callback):
        """钉钉 Stream 回调入口。

        ACK 语义至关重要：如果回调（投递 dingtalk_inbound 任务到 Redis Streams）
        失败仍 ACK，钉钉会认为消息已处理且不会重投——HUB 这边却没收到任务，
        **入站消息被静默丢失**。所以业务回调失败时必须返回 STATUS_SYSTEM_EXCEPTION
        让钉钉重投。

        解析失败（比如 raw_payload 字段缺失）属于消息体本身有问题，重投也无意义；
        这种 ack 掉避免无限重试。
        """
        try:
            data = callback.data if hasattr(callback, "data") else (callback or {})
            ts_ms = data.get("createAt") or 0
            msg = InboundMessage(
                channel_type="dingtalk",
                channel_userid=str(data.get("senderStaffId") or ""),
                conversation_id=str(data.get("conversationId") or ""),
                content=(data.get("text", {}) or {}).get("content", ""),
                content_type="text",
                timestamp=int(ts_ms // 1000),
                raw_payload=data,
            )
        except Exception:
            logger.exception("钉钉入站消息解析异常（消息体本身有问题，ack 掉不重投）")
            return AckMessage.STATUS_OK, "OK"

        if self._callback is None:
            logger.warning("收到钉钉消息但未注册业务回调")
            return AckMessage.STATUS_OK, "OK"

        try:
            await self._callback(msg)
        except Exception:
            # 业务回调失败（如 Redis 短暂不可用 → runner.submit 抛错）：
            # 不 ACK，让钉钉按其重投策略重新派发，避免消息丢失。
            logger.exception("钉钉业务回调失败，返回 SYSTEM_EXCEPTION 让钉钉重投")
            return AckMessage.STATUS_SYSTEM_EXCEPTION, "EX"

        return AckMessage.STATUS_OK, "OK"


class DingTalkStreamAdapter:
    """ChannelAdapter Protocol 实现（仅入站）。"""

    channel_type = "dingtalk"

    def __init__(self, app_key: str, app_secret: str, *, robot_id: str | None = None):
        self.app_key = app_key
        self.app_secret = app_secret
        self.robot_id = robot_id
        self._callback: InboundCallback = None
        self._client = None

    def on_message(self, handler: Callable[[InboundMessage], Awaitable[None]]) -> None:
        self._callback = handler

    async def start(self) -> None:
        if DingTalkStreamClient is None:
            raise RuntimeError("dingtalk_stream SDK 未安装（pip install dingtalk-stream）")
        credential = Credential(self.app_key, self.app_secret)
        self._client = DingTalkStreamClient(credential)
        topic = ChatbotMessage.TOPIC if ChatbotMessage else "/v1.0/im/bot/messages/get"
        self._client.register_callback_handler(topic, _HubChatbotHandler(self._callback))
        logger.info("DingTalkStream 已注册 ChatbotHandler，开始连接钉钉")
        await self._client.start()

    async def stop(self) -> None:
        if self._client and hasattr(self._client, "stop"):
            await self._client.stop()
