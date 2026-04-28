"""DingTalkSender：钉钉机器人**主动 push** 消息（HTTP OpenAPI，不依赖 Stream 连接）。

为什么单独拆出：
- DingTalkStreamAdapter 是 Stream 长连接，gateway 持有；worker 不能重复连
- 主动 push 是无状态 HTTP 调用（OpenAPI），gateway / worker 都能调，无连接冲突
- 流程：调 https://oapi.dingtalk.com/gettoken 取 access_token（缓存 ~2h）→
  调 /v1.0/robot/oToMessages/batchSend 用 robotCode + access_token 发消息

注意：access_token 应在多实例间共享缓存（Redis），本 plan 实现简化为进程内缓存；
Plan 5 在多 worker 部署时升级到 Redis 共享。
"""
from __future__ import annotations

import json
import logging
import time

import httpx

logger = logging.getLogger("hub.adapter.dingtalk_sender")


GET_TOKEN_URL = "https://oapi.dingtalk.com/gettoken"
SEND_OTO_URL = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"


class DingTalkSendError(Exception):
    pass


class DingTalkSender:
    """钉钉机器人主动 push 消息（HTTP OpenAPI）。"""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        robot_code: str,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.robot_code = robot_code
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._cached_token: str | None = None
        self._token_expires_at: float = 0.0

    async def aclose(self):
        await self._client.aclose()

    async def _get_access_token(self) -> str:
        now = time.time()
        if self._cached_token and now < self._token_expires_at - 60:  # 提前 1 分钟刷
            return self._cached_token
        r = await self._client.get(
            GET_TOKEN_URL, params={"appkey": self.app_key, "appsecret": self.app_secret},
        )
        r.raise_for_status()
        body = r.json()
        if body.get("errcode") != 0:
            raise DingTalkSendError(f"gettoken 失败: {body}")
        self._cached_token = body["access_token"]
        self._token_expires_at = now + int(body.get("expires_in", 7200))
        return self._cached_token

    async def send_text(self, dingtalk_userid: str, text: str) -> None:
        await self._send_oto(
            user_ids=[dingtalk_userid],
            msg_key="sampleText",
            msg_param={"content": text},
        )

    async def send_markdown(self, dingtalk_userid: str, title: str, markdown: str) -> None:
        await self._send_oto(
            user_ids=[dingtalk_userid],
            msg_key="sampleMarkdown",
            msg_param={"title": title, "text": markdown},
        )

    async def send_action_card(self, dingtalk_userid: str, actioncard: dict) -> None:
        await self._send_oto(
            user_ids=[dingtalk_userid],
            msg_key="sampleActionCard",
            msg_param=actioncard,
        )

    async def _send_oto(self, user_ids: list[str], msg_key: str, msg_param: dict) -> None:
        token = await self._get_access_token()
        body = {
            "robotCode": self.robot_code,
            "userIds": user_ids,
            "msgKey": msg_key,
            "msgParam": json.dumps(msg_param, ensure_ascii=False),
        }
        r = await self._client.post(
            SEND_OTO_URL,
            headers={"x-acs-dingtalk-access-token": token},
            json=body,
        )
        if r.status_code >= 400:
            raise DingTalkSendError(f"send oto 失败 {r.status_code}: {r.text[:200]}")
