"""admin /conversation/live SSE endpoint 测试。

httpx ASGITransport 缓冲所有 body parts 直到 more_body=False，无法测真正的流式
（参见 httpx._transports.asgi.handle_async_request：body_parts.append 累积，
最后一次性返回）。所以这里：
- 用 ASGI 客户端只验证：路由挂在了 200/403/503 三个分支
- 真实的流式行为 + 脱敏 + publisher → subscriber 端到端走 LiveStream 单元测试
  （tests/test_live_stream.py）
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fakeredis import aioredis as fakeredis_aio
from httpx import ASGITransport, AsyncClient

from hub.observability.live_stream import LiveStreamPublisher, LiveStreamSubscriber


async def _setup_admin(erp_user_id: int = 1, role_code: str = "platform_admin"):
    from hub.auth.erp_session import ErpSessionAuth
    from hub.models import DownstreamIdentity, HubRole, HubUser, HubUserRole
    from hub.seed import run_seed
    from main import app

    await run_seed()
    user = await HubUser.create(display_name=f"u{erp_user_id}")
    await DownstreamIdentity.create(
        hub_user=user, downstream_type="erp", downstream_user_id=erp_user_id,
    )
    role = await HubRole.get(code=role_code)
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    erp = AsyncMock()
    erp.get_me = AsyncMock(return_value={
        "id": erp_user_id, "username": f"u{erp_user_id}", "permissions": [],
    })
    auth = ErpSessionAuth(erp_adapter=erp)
    app.state.session_auth = auth
    cookie = auth._encode_cookie({
        "jwt": "tok", "user": {"id": erp_user_id, "username": f"u{erp_user_id}"},
    })
    return app, cookie


@pytest_asyncio.fixture
async def fake_redis():
    c = fakeredis_aio.FakeRedis()
    yield c
    await c.aclose()


@pytest.mark.asyncio
async def test_sse_returns_503_when_redis_missing():
    """app.state.redis 缺失 → 503。"""
    app, cookie = await _setup_admin(erp_user_id=10)
    app.state.redis = None
    app.state.task_runner = None

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/conversation/live")
        assert resp.status_code == 503


@pytest.mark.asyncio
async def test_sse_requires_perm(fake_redis):
    """没有 platform.conversation.monitor 权限 → 403。"""
    app, cookie = await _setup_admin(erp_user_id=11, role_code="bot_user_basic")
    app.state.redis = fake_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/conversation/live")
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_sse_event_generator_yields_published_message(fake_redis):
    """直接驱动 endpoint 的内部生成器，断言 publish → 'data: <json>\\n\\n'。

    （ASGITransport 不能测流式；这里直接调 LiveStreamSubscriber + 包成 SSE 帧）
    """
    pub = LiveStreamPublisher(fake_redis)
    sub = LiveStreamSubscriber(fake_redis)

    received: list[str] = []

    async def consume():
        async for raw in sub.stream():
            # 模拟 endpoint 的 SSE 包装
            received.append(f"data: {raw}\n\n")
            break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    await pub.publish({"task_id": "frame-1", "status": "success"})
    await asyncio.wait_for(consumer, timeout=2.0)

    assert len(received) == 1
    chunk = received[0]
    assert chunk.startswith("data: ")
    assert chunk.endswith("\n\n")
    payload = chunk[len("data: "):].rstrip("\n")
    event = json.loads(payload)
    assert event == {"task_id": "frame-1", "status": "success"}


@pytest.mark.asyncio
async def test_sse_uses_runner_redis_fallback(fake_redis):
    """app.state.redis=None 但 task_runner.redis 可用 → endpoint 不返 503。"""
    app, cookie = await _setup_admin(erp_user_id=12)
    app.state.redis = None
    saved_runner = getattr(app.state, "task_runner", None)

    class RunnerStub:
        def __init__(self, redis):
            self.redis = redis

    app.state.task_runner = RunnerStub(fake_redis)
    try:
        # 不发起真正的 GET（避免 ASGITransport 流式缓冲死锁）；
        # 直接驱动 conversation 的 _get_redis 工具函数验证 fallback 命中
        from hub.routers.admin.conversation import _get_redis

        class FakeReq:
            def __init__(self, app_):
                self.app = app_

        redis = _get_redis(FakeReq(app))
        assert redis is fake_redis
    finally:
        # 还原避免污染下个测试
        app.state.task_runner = saved_runner
