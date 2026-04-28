"""钉钉入站消息 task handler。

职责（Plan 3 范围）：
- 解析命令：/绑定 <user> / /解绑 / 帮助
- 编排 BindingService
- 非绑定/解绑命令前必须过 IdentityService（识别 + 检查 ERP 启用状态）
- ERP 用户禁用 → 拒绝并提示
- 未绑定 → 提示先绑定

依赖外部注入（避免直连 Stream）：
- binding_service: BindingService
- identity_service: IdentityService
- sender: DingTalkSender（HTTP OpenAPI，不依赖 Stream 连接）

不在 Plan 3 范围（Plan 4）：自然语言意图解析 / 商品查询 / 历史价等具体业务用例。
"""
from __future__ import annotations

import logging
import re

from hub import messages

logger = logging.getLogger("hub.handler.dingtalk_inbound")


RE_BIND = re.compile(r"^/?绑定\s+(\S+)\s*$")
RE_UNBIND = re.compile(r"^/?解绑\s*$")
RE_HELP = re.compile(r"^/?(help|帮助|\?|菜单)\s*$", re.IGNORECASE)


async def handle_inbound(
    task_data: dict, *,
    binding_service,
    identity_service,
    sender,
) -> None:
    """处理一条钉钉入站消息任务。

    task_data 结构：
        {
            task_id, task_type="dingtalk_inbound",
            payload: { channel_userid, content, conversation_id, timestamp }
        }
    """
    payload = task_data.get("payload", {})
    channel_userid = payload.get("channel_userid", "")
    content = (payload.get("content") or "").strip()

    # 1. 命令路由（绑定/解绑命令不需要先解析身份）
    m_bind = RE_BIND.match(content)
    if m_bind:
        erp_username = m_bind.group(1)
        result = await binding_service.initiate_binding(
            dingtalk_userid=channel_userid, erp_username=erp_username,
        )
        await _send_text(sender, channel_userid, result.reply_text)
        return

    if RE_UNBIND.match(content):
        result = await binding_service.unbind_self(dingtalk_userid=channel_userid)
        await _send_text(sender, channel_userid, result.reply_text)
        return

    if RE_HELP.match(content):
        cmds = [
            "/绑定 你的ERP用户名 — 绑定 ERP 账号",
            "/解绑 — 解绑当前账号",
            "查 SKU100 — 查商品（Plan 4 启用）",
            "查 SKU100 给阿里 — 查客户历史价（Plan 4 启用）",
        ]
        await _send_text(sender, channel_userid, messages.help_message(cmds))
        return

    # 2. 非绑定命令必须过 IdentityService（解析身份 + 检查 ERP 启用状态）
    resolution = await identity_service.resolve(dingtalk_userid=channel_userid)

    if not resolution.found:
        await _send_text(
            sender, channel_userid,
            "请先发送「/绑定 你的ERP用户名」完成绑定。\n发送「帮助」查看更多说明。",
        )
        return

    if not resolution.erp_active:
        await _send_text(
            sender, channel_userid,
            "你的 ERP 账号已停用，机器人无法继续为你服务。请联系管理员核实。",
        )
        return

    # 3. 已绑定 + 启用，但本 plan 无业务用例（Plan 4 接手）
    await _send_text(
        sender, channel_userid,
        "我没听懂，请发送「帮助」查看可用功能。\n业务功能（查商品 / 查报价）将在后续上线。",
    )


async def _send_text(sender, userid: str, text: str) -> None:
    """发送失败让异常上抛，由 WorkerRuntime 转入死信流，不静默 ACK。

    早期版本捕获并吞异常 → 钉钉短暂故障时用户收不到回复，
    任务也不会重试或进死信，问题被掩盖。
    """
    await sender.send_text(dingtalk_userid=userid, text=text)
