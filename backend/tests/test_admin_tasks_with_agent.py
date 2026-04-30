"""Plan 6 Task 13：task detail API 加 agent 决策链字段测试。

覆盖：
1. 有关联 conversation_log 时返完整字段
2. tool_calls 按 round_idx + called_at 升序返
3. 没匹配 conversation 时返 null + 空 tool_calls
4. 时间窗口内匹配 >1 个 conversation 时返 null（避免错配）
5. task 与 conversation 时差超 30s 不应匹配
6. 404 — 不存在的 task
7. 既有字段保留（task_log / payload / status 等不被破坏）
8. tool_calls 含 error 字段时正常返

依赖：conftest.py autouse setup_db（每条测试自动清表）。
复用 test_admin_tasks.py 的 admin_client fixture（通过 import）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock

from hub.models import ConversationLog, TaskLog, ToolCallLog


# ─────────────────────────────────────────────────────────────────────────────
# Helper: 复用 admin_client 的构建逻辑（不依赖跨文件 fixture import）
# ─────────────────────────────────────────────────────────────────────────────

async def _setup_admin(erp_user_id: int = 100, role_code: str = "platform_admin"):
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
    transport = ASGITransport(app=app)
    return transport, cookie, user


@pytest_asyncio.fixture
async def admin_client():
    transport, cookie, user = await _setup_admin()
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        yield ac, user


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: 快速建 task / conversation / tool_call
# ─────────────────────────────────────────────────────────────────────────────

async def _create_task(
    task_id: str = "task-agent-001",
    *,
    channel_userid: str = "dingtalk:U1",
    status: str = "success",
    task_type: str = "dingtalk_inbound",
):
    """创建 TaskLog；created_at 由 auto_now_add 自动设置。"""
    return await TaskLog.create(
        task_id=task_id,
        task_type=task_type,
        channel_type="dingtalk",
        channel_userid=channel_userid,
        status=status,
    )


async def _create_conversation(
    conversation_id: str,
    channel_userid: str,
    started_at: datetime,
    *,
    rounds_count: int = 2,
    tokens_used: int = 1500,
    tokens_cost_yuan: float | None = 0.012,
    final_status: str = "success",
    error_summary: str | None = None,
):
    return await ConversationLog.create(
        conversation_id=conversation_id,
        channel_userid=channel_userid,
        started_at=started_at,
        rounds_count=rounds_count,
        tokens_used=tokens_used,
        tokens_cost_yuan=tokens_cost_yuan,
        final_status=final_status,
        error_summary=error_summary,
    )


async def _create_tool_call(
    conversation_id: str,
    round_idx: int,
    tool_name: str,
    *,
    args_json: dict | None = None,
    result_json: dict | None = None,
    duration_ms: int = 100,
    error: str | None = None,
):
    return await ToolCallLog.create(
        conversation_id=conversation_id,
        round_idx=round_idx,
        tool_name=tool_name,
        args_json=args_json or {},
        result_json=result_json or {},
        duration_ms=duration_ms,
        error=error,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Case 1: 有关联 conversation_log 时返完整字段
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_detail_returns_conversation_log(admin_client):
    """有 conversation_log 命中时，返完整字段（rounds_count / tokens_used / cost）。"""
    ac, _ = admin_client
    task = await _create_task("task-agent-c1", channel_userid="dingtalk:U10")
    # started_at 与 task.created_at 同一时刻（在 ±30s 窗口内）
    await _create_conversation(
        "conv-c1", "dingtalk:U10", task.created_at,
        rounds_count=2, tokens_used=1500, tokens_cost_yuan=0.012,
    )

    resp = await ac.get("/hub/v1/admin/tasks/task-agent-c1")
    assert resp.status_code == 200
    data = resp.json()

    assert data["conversation_log"] is not None
    cl = data["conversation_log"]
    assert cl["conversation_id"] == "conv-c1"
    assert cl["rounds_count"] == 2
    assert cl["tokens_used"] == 1500
    assert abs(cl["tokens_cost_yuan"] - 0.012) < 1e-6
    assert cl["final_status"] == "success"


# ─────────────────────────────────────────────────────────────────────────────
# Case 2: tool_calls 按 round_idx 升序返
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_detail_returns_tool_calls_ordered_by_round(admin_client):
    """tool_calls 按 round_idx 升序，字段完整（tool_name / args_json / result_json / duration_ms）。"""
    ac, _ = admin_client
    task = await _create_task("task-agent-c2", channel_userid="dingtalk:U11")
    await _create_conversation("conv-c2", "dingtalk:U11", task.created_at)
    await _create_tool_call(
        "conv-c2", round_idx=0, tool_name="search_products",
        args_json={"query": "SKU100"},
        result_json={"items": [{"id": 1}]},
        duration_ms=200,
    )
    await _create_tool_call(
        "conv-c2", round_idx=1, tool_name="check_inventory",
        args_json={"product_id": 1},
        result_json={"total_stock": 49},
        duration_ms=150,
    )

    resp = await ac.get("/hub/v1/admin/tasks/task-agent-c2")
    data = resp.json()

    assert len(data["tool_calls"]) == 2
    tc0, tc1 = data["tool_calls"]
    assert tc0["round_idx"] == 0
    assert tc0["tool_name"] == "search_products"
    assert tc0["args_json"] == {"query": "SKU100"}
    assert tc0["result_json"] == {"items": [{"id": 1}]}
    assert tc0["duration_ms"] == 200
    assert tc0["error"] is None
    assert tc1["round_idx"] == 1
    assert tc1["tool_name"] == "check_inventory"


# ─────────────────────────────────────────────────────────────────────────────
# Case 3: 没匹配 conversation 时返 null + 空 tool_calls
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_detail_no_conversation_returns_null(admin_client):
    """无关联 conversation（rule 命令直接 return，不进 ChainAgent）→ null + []。"""
    ac, _ = admin_client
    await _create_task("task-no-agent", channel_userid="dingtalk:U20")
    # 不创建任何 ConversationLog

    resp = await ac.get("/hub/v1/admin/tasks/task-no-agent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversation_log"] is None
    assert data["tool_calls"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Case 4: 时间窗口内命中 >1 个 conversation 时返 null（避免错配）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_detail_multiple_conversations_returns_null(admin_client):
    """同一 channel_userid 在时间窗口内有 2 个 conversation → 无法确定 → 返 null。"""
    ac, _ = admin_client
    task = await _create_task("task-ambig", channel_userid="dingtalk:U30")
    # 两个 conversation 都在 ±30s 窗口内
    await _create_conversation("conv-ambig-A", "dingtalk:U30", task.created_at)
    await _create_conversation(
        "conv-ambig-B", "dingtalk:U30",
        task.created_at + timedelta(seconds=5),
    )

    resp = await ac.get("/hub/v1/admin/tasks/task-ambig")
    data = resp.json()
    assert data["conversation_log"] is None
    assert data["tool_calls"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Case 5: task 与 conversation 时差超 30s 不应匹配
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_detail_time_window_30s(admin_client):
    """conversation.started_at 比 task.created_at 早 5 分钟 → 超出窗口 → 不匹配。"""
    ac, _ = admin_client
    task = await _create_task("task-late", channel_userid="dingtalk:U40")
    # started_at 远早于 task.created_at
    await _create_conversation(
        "conv-old", "dingtalk:U40",
        task.created_at - timedelta(minutes=5),
    )

    resp = await ac.get("/hub/v1/admin/tasks/task-late")
    data = resp.json()
    assert data["conversation_log"] is None
    assert data["tool_calls"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Case 6: 404 — 不存在的 task
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_detail_404_nonexistent_task(admin_client):
    """不存在的 task_id → 404。"""
    ac, _ = admin_client
    resp = await ac.get("/hub/v1/admin/tasks/nonexistent-task-xyz")
    assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Case 7: 既有字段保留（不破坏 Plan 5 既有 contract）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_detail_preserves_existing_fields(admin_client):
    """新增字段不能破坏既有 task_log / payload 字段（Plan 5 contract 保留）。"""
    ac, _ = admin_client
    await _create_task(
        "task-preserve",
        channel_userid="dingtalk:U50",
        status="failed_user",
        task_type="dingtalk_inbound",
    )

    resp = await ac.get("/hub/v1/admin/tasks/task-preserve")
    assert resp.status_code == 200
    data = resp.json()

    # 既有 task_log 字段
    assert "task_log" in data
    tl = data["task_log"]
    assert tl["task_id"] == "task-preserve"
    assert tl["status"] == "failed_user"
    assert tl["channel_userid"] == "dingtalk:U50"
    assert "retry_count" in tl

    # 既有 payload 字段（无 payload → None）
    assert "payload" in data

    # 新增字段存在
    assert "conversation_log" in data
    assert "tool_calls" in data


# ─────────────────────────────────────────────────────────────────────────────
# Case 8: tool_call 含 error 字段时正常返（失败工具调用）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_detail_tool_call_with_error(admin_client):
    """失败的 tool_call（error 非 null）能正常出现在 tool_calls 列表中。"""
    ac, _ = admin_client
    task = await _create_task("task-err", channel_userid="dingtalk:U60")
    await _create_conversation("conv-err", "dingtalk:U60", task.created_at, final_status="failed_system")
    await _create_tool_call(
        "conv-err", round_idx=0, tool_name="query_erp",
        args_json={"op": "list_orders"},
        result_json=None,
        duration_ms=5000,
        error="ERP timeout after 5000ms",
    )

    resp = await ac.get("/hub/v1/admin/tasks/task-err")
    data = resp.json()

    assert data["conversation_log"]["final_status"] == "failed_system"
    assert len(data["tool_calls"]) == 1
    tc = data["tool_calls"][0]
    assert tc["tool_name"] == "query_erp"
    assert tc["error"] == "ERP timeout after 5000ms"
    assert tc["duration_ms"] == 5000
