import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def app_client(monkeypatch):
    monkeypatch.setenv("HUB_ERP_TO_HUB_SECRET", "shared-secret-xyz")
    from hub import config
    config._settings = None  # 清缓存
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_confirm_final_writes_binding_and_dispatches_outbound(app_client):
    """confirm-final 成功 → 写 binding + 投递 dingtalk_outbound 任务。"""
    from hub.seed import run_seed
    await run_seed()

    from main import app
    submitted_tasks = []

    class FakeRunner:
        async def submit(self, task_type, payload):
            submitted_tasks.append((task_type, payload))
            return "fake-task-id"

    app.state.task_runner = FakeRunner()

    payload = {
        "token_id": 1,
        "erp_user_id": 99, "erp_username": "wang",
        "erp_display_name": "王五", "dingtalk_userid": "m99",
    }
    resp = await app_client.post(
        "/hub/v1/internal/binding/confirm-final",
        json=payload,
        headers={"X-ERP-Secret": "shared-secret-xyz"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    from hub.models import ChannelUserBinding
    binding = await ChannelUserBinding.filter(channel_userid="m99").first()
    assert binding is not None

    outbound_tasks = [t for t in submitted_tasks if t[0] == "dingtalk_outbound"]
    assert len(outbound_tasks) >= 1
    payloads = [t[1] for t in outbound_tasks]
    all_text = " ".join(str(p) for p in payloads)
    assert "绑定成功" in all_text or "欢迎" in all_text
    assert "30 天" in all_text or "记录" in all_text


@pytest.mark.asyncio
async def test_confirm_final_conflict_returns_409(app_client):
    """冲突场景应返回 409。"""
    from hub.models import ChannelUserBinding, DownstreamIdentity, HubUser
    from hub.seed import run_seed
    await run_seed()

    user = await HubUser.create(display_name="A")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m_taken", status="active",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=600)

    payload = {
        "token_id": 888,
        "dingtalk_userid": "m_taken",
        "erp_user_id": 999,
        "erp_username": "x", "erp_display_name": "X",
    }
    resp = await app_client.post(
        "/hub/v1/internal/binding/confirm-final",
        json=payload,
        headers={"X-ERP-Secret": "shared-secret-xyz"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert "conflict_" in body.get("detail", {}).get("error", "")


@pytest.mark.asyncio
async def test_confirm_final_rejects_wrong_secret(app_client):
    payload = {"token_id": 1, "erp_user_id": 99, "erp_username": "x",
               "erp_display_name": "X", "dingtalk_userid": "m99"}
    resp = await app_client.post(
        "/hub/v1/internal/binding/confirm-final",
        json=payload,
        headers={"X-ERP-Secret": "wrong"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_confirm_final_token_replay_returns_success_but_no_dup_outbound(app_client):
    """同 token_id 重复 confirm 返回 success 但不重复投递 outbound。"""
    from hub.seed import run_seed
    await run_seed()

    from main import app
    submitted_tasks = []

    class FakeRunner:
        async def submit(self, task_type, payload):
            submitted_tasks.append((task_type, payload))
            return "fake-task-id"

    app.state.task_runner = FakeRunner()

    payload = {"token_id": 42, "erp_user_id": 100, "erp_username": "y",
               "erp_display_name": "Y", "dingtalk_userid": "m100"}
    headers = {"X-ERP-Secret": "shared-secret-xyz"}

    r1 = await app_client.post("/hub/v1/internal/binding/confirm-final", json=payload, headers=headers)
    r2 = await app_client.post("/hub/v1/internal/binding/confirm-final", json=payload, headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200

    from hub.models import ChannelUserBinding
    bindings = await ChannelUserBinding.filter(channel_userid="m100")
    assert len(bindings) == 1

    outbound_count = len([t for t in submitted_tasks if t[0] == "dingtalk_outbound"])
    assert outbound_count <= 2
