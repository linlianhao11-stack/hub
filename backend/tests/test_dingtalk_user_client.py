"""DingTalkUserClient OpenAPI 行为：access_token 缓存 / 部门遍历 / userid 聚合。"""
from __future__ import annotations

import json

import httpx
import pytest

from hub.cron.dingtalk_user_client import DingTalkUserClient, DingTalkUserClientError


def _make_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fetch_active_userids_walks_dept_tree_and_aggregates():
    """根部门 1 → 子部门 2,3；部门 1 有 u1，部门 2 有 u2,u3，部门 3 有 u3,u4 → 去重后 4 个。"""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/gettoken":
            return httpx.Response(200, json={
                "errcode": 0, "access_token": "tk", "expires_in": 7200,
            })
        if req.url.path == "/topapi/v2/department/listsub":
            body = json.loads(req.content)
            if body["dept_id"] == 1:
                return httpx.Response(200, json={
                    "errcode": 0,
                    "result": [{"dept_id": 2}, {"dept_id": 3}],
                })
            return httpx.Response(200, json={"errcode": 0, "result": []})
        if req.url.path == "/topapi/user/listid":
            body = json.loads(req.content)
            mapping = {1: ["u1"], 2: ["u2", "u3"], 3: ["u3", "u4"]}
            return httpx.Response(200, json={
                "errcode": 0,
                "result": {"userid_list": mapping[body["dept_id"]]},
            })
        return httpx.Response(404)

    client = DingTalkUserClient("ak", "as", transport=_make_transport(handler))
    try:
        ids = await client.fetch_active_userids()
    finally:
        await client.aclose()
    assert ids == {"u1", "u2", "u3", "u4"}


@pytest.mark.asyncio
async def test_get_access_token_caches_within_ttl():
    """同一 client 内连续调用 access_token 只发一次 gettoken 请求。"""
    call_count = [0]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/gettoken":
            call_count[0] += 1
            return httpx.Response(200, json={
                "errcode": 0, "access_token": "tk", "expires_in": 7200,
            })
        return httpx.Response(200, json={"errcode": 0, "result": []})

    client = DingTalkUserClient("ak", "as", transport=_make_transport(handler))
    try:
        t1 = await client._get_access_token()
        t2 = await client._get_access_token()
        assert t1 == t2 == "tk"
        assert call_count[0] == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_gettoken_errcode_nonzero_raises():
    """OpenAPI 返 errcode=40001 → DingTalkUserClientError。"""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "errcode": 40001, "errmsg": "invalid app_key",
        })

    client = DingTalkUserClient("bad", "bad", transport=_make_transport(handler))
    try:
        with pytest.raises(DingTalkUserClientError):
            await client._get_access_token()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_listsub_errcode_nonzero_raises():
    """部门列表权限不足（errcode=60011）→ DingTalkUserClientError。"""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/gettoken":
            return httpx.Response(200, json={
                "errcode": 0, "access_token": "tk", "expires_in": 7200,
            })
        if req.url.path == "/topapi/v2/department/listsub":
            return httpx.Response(200, json={
                "errcode": 60011, "errmsg": "permission denied",
            })
        return httpx.Response(404)

    client = DingTalkUserClient("ak", "as", transport=_make_transport(handler))
    try:
        with pytest.raises(DingTalkUserClientError):
            await client.fetch_active_userids()
    finally:
        await client.aclose()
