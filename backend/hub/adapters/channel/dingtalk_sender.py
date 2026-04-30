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
        # 检查钉钉业务错误码
        resp_body = r.json() if r.content else {}
        if resp_body.get("errcode", 0) not in (0, None):
            raise DingTalkSendError(f"send oto 业务失败: {resp_body}")

    async def send_file(
        self,
        *,
        dingtalk_userid: str,
        file_bytes: bytes,
        file_name: str,
        file_type: str = "docx",
    ) -> None:
        """发文件给单个用户（合同 docx / Excel / PDF 等）。

        两步：
        1. _upload_media 上传拿 media_id
        2. _send_oto 用 sampleFile msg_key 发给用户

        Args:
            dingtalk_userid: 钉钉用户 ID
            file_bytes: 文件字节（< 20MB）
            file_name: 文件名（含扩展名，如 "合同.docx"）
            file_type: 文件类型标识（"docx" / "xlsx" / "pdf" 等）

        Raises:
            DingTalkSendError: 文件超 20MB / 上传失败 / 发送失败
        """
        media_id = await self._upload_media(file_bytes, file_name, file_type)
        await self._send_oto(
            user_ids=[dingtalk_userid],
            msg_key="sampleFile",
            msg_param={"mediaId": media_id, "fileName": file_name, "fileType": file_type},
        )

    async def _upload_media(
        self,
        file_bytes: bytes,
        file_name: str,
        file_type: str,
        *,
        max_retry: int = 1,
    ) -> str:
        """调钉钉 media/upload 接口拿 media_id。

        - 文件 > 20MB 立即抛 DingTalkSendError（不上传）
        - 5xx 重试最多 max_retry 次
        - 4xx 立即抛（不重试）

        Returns:
            media_id 字符串
        """
        if len(file_bytes) > 20 * 1024 * 1024:
            raise DingTalkSendError("文件超过钉钉 20MB 上限")

        token = await self._get_access_token()
        url = "https://oapi.dingtalk.com/media/upload"

        last_err: Exception | None = None
        for attempt in range(max_retry + 1):
            try:
                files = {
                    "media": (file_name, file_bytes, f"application/{file_type}"),
                }
                r = await self._client.post(
                    url,
                    params={"access_token": token, "type": "file"},
                    files=files,
                )
                if r.status_code == 200:
                    body = r.json()
                    if body.get("errcode") == 0:
                        return body["media_id"]
                    raise DingTalkSendError(f"上传失败: {body}")
                if r.status_code >= 500 and attempt < max_retry:
                    # 5xx：记录错误，继续重试
                    last_err = DingTalkSendError(f"upload {r.status_code}")
                    continue
                # 4xx 或超出重试次数的 5xx：立即抛
                raise DingTalkSendError(f"上传 {r.status_code}: {r.text[:200]}")
            except DingTalkSendError:
                raise
            except httpx.RequestError as e:
                last_err = DingTalkSendError(f"网络错误: {e}")
                if attempt < max_retry:
                    continue
                raise last_err from e

        # 兜底（实际不应到达）
        raise last_err or DingTalkSendError("upload exhausted")
