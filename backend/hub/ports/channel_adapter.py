"""ChannelAdapter Protocol：接入端协议适配（钉钉/企微/Web 等）。"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class OutboundMessageType(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"
    ACTIONCARD = "actioncard"


@dataclass
class InboundMessage:
    """统一入站消息（不同渠道转换到同一格式）。"""
    channel_type: str
    channel_userid: str
    conversation_id: str
    content: str
    content_type: str  # text / image / file / button_click
    timestamp: int  # epoch seconds
    raw_payload: dict = field(default_factory=dict)


@dataclass
class OutboundMessage:
    """统一出站消息。"""
    type: OutboundMessageType
    text: str | None = None
    markdown: str | None = None
    actioncard: dict | None = None


class ChannelAdapter(Protocol):
    """渠道接入适配器协议。

    生命周期：start() 建立到渠道的连接 → on_message() 注册回调 → 渠道事件触发回调
    → send_message() 主动 push 回应 → stop() 优雅关闭。
    """
    channel_type: str

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send_message(self, channel_userid: str, message: OutboundMessage) -> None: ...
    def on_message(self, handler: Callable[[InboundMessage], Awaitable[None]]) -> None: ...
