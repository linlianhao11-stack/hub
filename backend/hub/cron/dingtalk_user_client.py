"""DingTalk 用户列表客户端（cron 专用）。

用途：每日巡检时拉企业全员 userid，喂给 daily_employee_audit 比对 binding。

接口：
- GET https://oapi.dingtalk.com/gettoken?appkey=&appsecret=    → access_token
- POST /topapi/v2/department/listsub?access_token=             → 子部门列表
- POST /topapi/user/listid?access_token=                       → 部门下所有 userid

用法：
    client = DingTalkUserClient(app_key, app_secret)
    try:
        userids = await client.fetch_active_userids()
    finally:
        await client.aclose()
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable

import httpx

logger = logging.getLogger("hub.cron.dingtalk_user_client")

GET_TOKEN_URL = "https://oapi.dingtalk.com/gettoken"
DEPT_LIST_URL = "https://oapi.dingtalk.com/topapi/v2/department/listsub"
USER_LIST_URL = "https://oapi.dingtalk.com/topapi/user/listid"
ROOT_DEPT_ID = 1


class DingTalkUserClientError(Exception):
    """OpenAPI 返回 errcode != 0 或网络错误。"""


class DingTalkUserClient:
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._cached_token: str | None = None
        self._token_expires_at: float = 0.0

    async def aclose(self):
        await self._client.aclose()

    async def _get_access_token(self) -> str:
        """带 60 秒过期 buffer 的 token 缓存。"""
        now = time.time()
        if self._cached_token and now < self._token_expires_at - 60:
            return self._cached_token
        r = await self._client.get(
            GET_TOKEN_URL,
            params={"appkey": self.app_key, "appsecret": self.app_secret},
        )
        r.raise_for_status()
        body = r.json()
        if body.get("errcode") != 0:
            raise DingTalkUserClientError(f"gettoken 失败: {body}")
        self._cached_token = body["access_token"]
        self._token_expires_at = now + int(body.get("expires_in", 7200))
        return self._cached_token

    async def _list_sub_departments(self, parent_id: int, token: str) -> list[int]:
        r = await self._client.post(
            DEPT_LIST_URL,
            params={"access_token": token},
            json={"dept_id": parent_id},
        )
        r.raise_for_status()
        body = r.json()
        if body.get("errcode") != 0:
            raise DingTalkUserClientError(
                f"listsub dept={parent_id} 失败: {body}",
            )
        return [d["dept_id"] for d in body.get("result", [])]

    async def _list_userids_in_dept(self, dept_id: int, token: str) -> list[str]:
        r = await self._client.post(
            USER_LIST_URL,
            params={"access_token": token},
            json={"dept_id": dept_id},
        )
        r.raise_for_status()
        body = r.json()
        if body.get("errcode") != 0:
            raise DingTalkUserClientError(
                f"listid dept={dept_id} 失败: {body}",
            )
        return body.get("result", {}).get("userid_list", [])

    async def _walk_departments(self, token: str) -> Iterable[int]:
        """BFS 遍历整个组织树，从根部门开始。返回所有 dept_id（含根）。"""
        visited: set[int] = set()
        queue: list[int] = [ROOT_DEPT_ID]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            children = await self._list_sub_departments(current, token)
            queue.extend(children)
        return visited

    async def fetch_active_userids(self) -> set[str]:
        """拉取企业全员现役 userid 集合。"""
        token = await self._get_access_token()
        all_dept_ids = await self._walk_departments(token)
        all_userids: set[str] = set()
        for dept_id in all_dept_ids:
            ids = await self._list_userids_in_dept(dept_id, token)
            all_userids.update(ids)
        logger.info(f"DingTalk 现役 userid 数量: {len(all_userids)}")
        return all_userids
