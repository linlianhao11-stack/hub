"""dingtalk_outbound task handler：用 DingTalkSender 主动 push 消息到钉钉。

业务侧（confirm-final / inbound handler / 告警等）投递此 task；
worker 消费 → 调 DingTalkSender HTTP OpenAPI → 完成 push。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("hub.handler.dingtalk_outbound")


async def handle_outbound(task_data: dict, *, sender) -> None:
    payload = task_data.get("payload", {})
    userid = payload.get("channel_userid")
    msg_type = payload.get("type", "text")

    if not userid:
        logger.error(f"dingtalk_outbound 缺 channel_userid: {payload}")
        return

    if msg_type == "text":
        await sender.send_text(dingtalk_userid=userid, text=payload.get("text", ""))
    elif msg_type == "markdown":
        await sender.send_markdown(
            dingtalk_userid=userid,
            title=payload.get("title", ""),
            markdown=payload.get("markdown", ""),
        )
    elif msg_type == "actioncard":
        await sender.send_action_card(
            dingtalk_userid=userid, actioncard=payload.get("actioncard", {}),
        )
    else:
        raise ValueError(f"未知 outbound type: {msg_type}")
