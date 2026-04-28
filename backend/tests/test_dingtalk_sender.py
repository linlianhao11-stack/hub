import httpx
import pytest
from httpx import MockTransport, Response


@pytest.mark.asyncio
async def test_sender_acquires_access_token():
    """access_token 通过 AppKey/AppSecret 调钉钉 OpenAPI 取得。"""
    from hub.adapters.channel.dingtalk_sender import DingTalkSender

    captured = []
    def handler(req: httpx.Request) -> Response:
        captured.append(req.url.path)
        if "gettoken" in str(req.url):
            return Response(200, json={"errcode": 0, "access_token": "tk_xyz", "expires_in": 7200})
        return Response(200, json={"errcode": 0})

    sender = DingTalkSender(
        app_key="k", app_secret="s", robot_code="rc",
        transport=MockTransport(handler),
    )
    token = await sender._get_access_token()
    assert token == "tk_xyz"
    assert any("gettoken" in p for p in captured)


@pytest.mark.asyncio
async def test_send_text_to_user():
    """send_text 调钉钉机器人发消息 OpenAPI。"""
    from hub.adapters.channel.dingtalk_sender import DingTalkSender

    captured_payloads = []
    def handler(req: httpx.Request) -> Response:
        if "gettoken" in str(req.url):
            return Response(200, json={"errcode": 0, "access_token": "tk", "expires_in": 7200})
        captured_payloads.append({"path": req.url.path, "body": req.content})
        return Response(200, json={"processQueryKey": "abc"})

    sender = DingTalkSender(
        app_key="k", app_secret="s", robot_code="rc",
        transport=MockTransport(handler),
    )
    await sender.send_text(dingtalk_userid="u1", text="hi 你好")
    assert any(
        "hi 你好" in p["body"].decode("utf-8") for p in captured_payloads
    )


@pytest.mark.asyncio
async def test_send_text_caches_token():
    """同一 sender 多次 send 应只取一次 token。"""
    from hub.adapters.channel.dingtalk_sender import DingTalkSender

    token_calls = 0
    def handler(req: httpx.Request) -> Response:
        nonlocal token_calls
        if "gettoken" in str(req.url):
            token_calls += 1
            return Response(200, json={"errcode": 0, "access_token": "tk", "expires_in": 7200})
        return Response(200, json={})

    sender = DingTalkSender(
        app_key="k", app_secret="s", robot_code="rc",
        transport=MockTransport(handler),
    )
    await sender.send_text("u1", "msg1")
    await sender.send_text("u1", "msg2")
    assert token_calls == 1
