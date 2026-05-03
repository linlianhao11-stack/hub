import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from hub.agent.react.tools._confirm_helper import (
    create_pending_action, set_confirm_gate, _gate,
)
from hub.agent.tools.confirm_gate import PendingAction


@pytest.mark.asyncio
async def test_create_pending_action_returns_pending(fake_ctx, monkeypatch):
    """create_pending_action 应该调 gate.create_pending(subgraph, summary, payload),
    返 PendingAction（含 action_id 和 token）。"""
    fake_pending = PendingAction(
        action_id="act-abc123",
        conversation_id="test-conv",
        hub_user_id=1,
        subgraph="contract",
        summary="预览文案",
        payload={"tool_name": "create_contract_draft", "args": {"customer_id": 7}},
        created_at=datetime.now(tz=timezone.utc),
        ttl_seconds=600,
        token="tok-xyz",
    )
    gate = AsyncMock()
    gate.create_pending = AsyncMock(return_value=fake_pending)
    set_confirm_gate(gate)

    pending = await create_pending_action(
        subgraph="contract",
        summary="预览文案",
        payload={"tool_name": "create_contract_draft", "args": {"customer_id": 7}},
    )
    assert pending.action_id == "act-abc123"
    assert pending.token == "tok-xyz"

    # 校验传给 gate 的 kwargs（按真实 ConfirmGate.create_pending 签名）
    call_kwargs = gate.create_pending.call_args.kwargs
    assert call_kwargs["conversation_id"] == "test-conv"
    assert call_kwargs["hub_user_id"] == 1
    assert call_kwargs["subgraph"] == "contract"
    assert call_kwargs["summary"] == "预览文案"
    assert call_kwargs["payload"]["tool_name"] == "create_contract_draft"


@pytest.mark.asyncio
async def test_gate_not_injected_raises():
    """没调 set_confirm_gate 就用 _gate() 应该 raise。"""
    set_confirm_gate(None)  # reset
    with pytest.raises(RuntimeError, match="ConfirmGate 未注入"):
        _gate()


@pytest.mark.asyncio
async def test_confirm_action_dispatches_to_generate_contract_draft(fake_ctx, monkeypatch):
    """confirm_action 应该 list_pending_for_context → claim() → invoke_business_tool
    → 调底层 generate_contract_draft 真执行。
    payload.tool_name 是**底层函数名**（generate_contract_draft）,不是 React tool 名。
    """
    from datetime import datetime, timezone
    from hub.agent.react.tools.confirm import confirm_action
    from hub.agent.tools.confirm_gate import PendingAction

    fake_pending = PendingAction(
        action_id="act-1",
        conversation_id="test-conv",
        hub_user_id=1,
        subgraph="contract",
        summary="...",
        payload={
            "tool_name": "generate_contract_draft",
            "args": {
                "template_id": 1, "customer_id": 7,
                "items": [{"product_id": 1, "qty": 10, "price": 300}],
                "shipping_address": "x", "shipping_contact": "y", "shipping_phone": "z",
                "payment_terms": "", "tax_rate": "",
            },
        },
        created_at=datetime.now(tz=timezone.utc),
        ttl_seconds=600,
        token="tok-1",
    )

    gate = AsyncMock()
    gate.list_pending_for_context = AsyncMock(return_value=[fake_pending])
    gate.claim = AsyncMock(return_value=True)
    set_confirm_gate(gate)

    underlying = AsyncMock(return_value={"draft_id": 42, "file_sent": True})
    monkeypatch.setattr(
        "hub.agent.tools.generate_tools.generate_contract_draft", underlying,
    )
    monkeypatch.setitem(
        __import__(
            "hub.agent.react.tools.confirm", fromlist=["WRITE_TOOL_DISPATCH"],
        ).WRITE_TOOL_DISPATCH,
        "generate_contract_draft",
        ("usecase.generate_contract.use", underlying, False),
    )
    monkeypatch.setattr(
        "hub.agent.react.tools._invoke.require_permissions", AsyncMock(),
    )

    result = await confirm_action.ainvoke({"action_id": "act-1"})

    assert result["draft_id"] == 42
    assert result["file_sent"] is True
    gate.claim.assert_awaited_once_with(
        action_id="act-1", token="tok-1",
        hub_user_id=1, conversation_id="test-conv",
    )
    underlying.assert_awaited_once()
    fn_kwargs = underlying.call_args.kwargs
    assert fn_kwargs["template_id"] == 1
    assert fn_kwargs["customer_id"] == 7
    assert fn_kwargs["hub_user_id"] == 1
    assert fn_kwargs["conversation_id"] == "test-conv"
    assert fn_kwargs["acting_as_user_id"] == 1
    assert "confirmation_action_id" not in fn_kwargs


@pytest.mark.asyncio
async def test_confirm_action_voucher_passes_confirmation_action_id(fake_ctx, monkeypatch):
    """voucher 底层 create_voucher_draft 必填 confirmation_action_id —
    dispatch 时用当前 action_id 注入。"""
    from datetime import datetime, timezone
    from hub.agent.react.tools.confirm import confirm_action
    from hub.agent.tools.confirm_gate import PendingAction

    fake_pending = PendingAction(
        action_id="act-vch-1",
        conversation_id="test-conv",
        hub_user_id=1,
        subgraph="voucher",
        summary="...",
        payload={
            "tool_name": "create_voucher_draft",
            "args": {
                "voucher_data": {"entries": [], "total_amount": 1000, "summary": "x"},
                "rule_matched": "sales_template",
            },
        },
        created_at=datetime.now(tz=timezone.utc), ttl_seconds=600, token="tok-vch-1",
    )

    gate = AsyncMock()
    gate.list_pending_for_context = AsyncMock(return_value=[fake_pending])
    gate.claim = AsyncMock(return_value=True)
    set_confirm_gate(gate)

    underlying = AsyncMock(return_value={"draft_id": 99, "status": "pending"})
    monkeypatch.setitem(
        __import__(
            "hub.agent.react.tools.confirm", fromlist=["WRITE_TOOL_DISPATCH"],
        ).WRITE_TOOL_DISPATCH,
        "create_voucher_draft",
        ("usecase.create_voucher.use", underlying, True),
    )
    monkeypatch.setattr(
        "hub.agent.react.tools._invoke.require_permissions", AsyncMock(),
    )

    result = await confirm_action.ainvoke({"action_id": "act-vch-1"})
    assert result["draft_id"] == 99

    underlying.assert_awaited_once()
    fn_kwargs = underlying.call_args.kwargs
    assert fn_kwargs["confirmation_action_id"] == "act-vch-1"
    assert fn_kwargs["voucher_data"]["total_amount"] == 1000


@pytest.mark.asyncio
async def test_confirm_action_pending_not_found(fake_ctx):
    """list_pending_for_context 没找到 action_id → 返 error 不抛。"""
    from hub.agent.react.tools.confirm import confirm_action

    gate = AsyncMock()
    gate.list_pending_for_context = AsyncMock(return_value=[])
    set_confirm_gate(gate)

    result = await confirm_action.ainvoke({"action_id": "act-bad"})
    assert "error" in result
    assert "不存在" in result["error"] or "过期" in result["error"]


@pytest.mark.asyncio
async def test_confirm_action_claim_raises_cross_context(fake_ctx):
    """claim() 抛 CrossContextClaim → 返 error 不抛。"""
    from datetime import datetime, timezone
    from hub.agent.react.tools.confirm import confirm_action
    from hub.agent.tools.confirm_gate import PendingAction, CrossContextClaim

    fake_pending = PendingAction(
        action_id="act-2", conversation_id="test-conv", hub_user_id=1,
        subgraph="contract", summary="...",
        payload={"tool_name": "create_contract_draft", "args": {}},
        created_at=datetime.now(tz=timezone.utc), ttl_seconds=600, token="tok-2",
    )
    gate = AsyncMock()
    gate.list_pending_for_context = AsyncMock(return_value=[fake_pending])
    gate.claim = AsyncMock(side_effect=CrossContextClaim("已过期"))
    set_confirm_gate(gate)

    result = await confirm_action.ainvoke({"action_id": "act-2"})
    assert "error" in result
    assert "失效" in result["error"] or "已过期" in result["error"]


@pytest.mark.asyncio
async def test_confirm_action_unknown_tool_name(fake_ctx):
    """PendingAction.payload.tool_name 不在 dispatch 表 → 返 error 不抛。"""
    from datetime import datetime, timezone
    from hub.agent.react.tools.confirm import confirm_action
    from hub.agent.tools.confirm_gate import PendingAction

    fake_pending = PendingAction(
        action_id="act-3", conversation_id="test-conv", hub_user_id=1,
        subgraph="???", summary="...",
        payload={"tool_name": "unknown_tool", "args": {}},
        created_at=datetime.now(tz=timezone.utc), ttl_seconds=600, token="tok-3",
    )
    gate = AsyncMock()
    gate.list_pending_for_context = AsyncMock(return_value=[fake_pending])
    gate.claim = AsyncMock(return_value=True)
    set_confirm_gate(gate)

    result = await confirm_action.ainvoke({"action_id": "act-3"})
    assert "error" in result
    assert "unknown_tool" in result["error"]


@pytest.mark.asyncio
async def test_e2e_plan_then_execute_with_real_fakeredis(fake_ctx, monkeypatch):
    """端到端：fakeredis 真 ConfirmGate + 真 create_pending_action + 真 confirm_action
    走通完整 plan-then-execute 链路。

    步骤：
      1. write tool 调 create_pending_action → 拿 PendingAction（action_id + token）
      2. confirm_action(action_id) 调 list_pending_for_context 找 pending →
         claim() 原子消费 → dispatch 业务函数（mock 返成功）
      3. 重复 confirm_action(action_id) 应该拒（pending 已被 claim 掉）
    """
    import fakeredis.aioredis
    from hub.agent.tools.confirm_gate import ConfirmGate
    from hub.agent.react.tools.write import create_contract_draft
    from hub.agent.react.tools.confirm import confirm_action, WRITE_TOOL_DISPATCH

    # 真 fakeredis ConfirmGate
    redis = fakeredis.aioredis.FakeRedis()
    gate = ConfirmGate(redis)
    set_confirm_gate(gate)

    # mock 底层 generate_contract_draft（不真渲染 docx）
    # **关键**：confirm.py 模块 import 时已经把 generate_tools.generate_contract_draft
    # 引用绑死进 WRITE_TOOL_DISPATCH 元组,monkeypatch 子模块属性**不会**生效。
    # 必须直接 setitem 替换 dispatch 表里的元组。
    underlying = AsyncMock(return_value={"draft_id": 100, "file_sent": True})
    monkeypatch.setitem(
        WRITE_TOOL_DISPATCH,
        "generate_contract_draft",
        ("usecase.generate_contract.use", underlying, False),
    )

    # mock _resolve_default_template_id 返 1（plan 阶段需要,本测试 fakeredis 不是真 hub.contract_template DB）
    async def _fake_resolve():
        return 1
    monkeypatch.setattr(
        "hub.agent.react.tools.write._resolve_default_template_id", _fake_resolve,
    )
    # mock require_permissions（plan 阶段 + dispatch 阶段都用）
    monkeypatch.setattr(
        "hub.agent.react.tools.write.require_permissions", AsyncMock(),
    )
    monkeypatch.setattr(
        "hub.agent.react.tools._invoke.require_permissions", AsyncMock(),
    )

    # Phase 1: plan 阶段
    plan_result = await create_contract_draft.ainvoke({
        "customer_id": 7,
        "items": [{"product_id": 1, "qty": 10, "price": 300.0}],
        "shipping_address": "北京海淀",
        "shipping_contact": "张三",
        "shipping_phone": "13800001111",
    })
    assert plan_result["status"] == "pending_confirmation"
    action_id = plan_result["action_id"]
    underlying.assert_not_awaited()  # 还没真执行

    # Phase 2: confirm 阶段
    exec_result = await confirm_action.ainvoke({"action_id": action_id})
    assert exec_result["draft_id"] == 100
    assert exec_result["file_sent"] is True
    underlying.assert_awaited_once()
    # 验证 dispatch 的参数跟 plan 阶段传的一致
    call_kwargs = underlying.call_args.kwargs
    assert call_kwargs["customer_id"] == 7
    assert call_kwargs["shipping_address"] == "北京海淀"
    # ctx 字段也注入了
    assert call_kwargs["hub_user_id"] == 1
    assert call_kwargs["conversation_id"] == "test-conv"

    # Phase 3: 重复 confirm 必须被拒（pending 已 HDEL）
    duplicate_result = await confirm_action.ainvoke({"action_id": action_id})
    assert "error" in duplicate_result
    assert "不存在" in duplicate_result["error"] or "过期" in duplicate_result["error"]
    underlying.assert_awaited_once()  # 仍然只调了 1 次（第二次 claim 失败,没 dispatch）


@pytest.mark.asyncio
async def test_e2e_voucher_idempotency_reuses_same_pending(fake_ctx, monkeypatch):
    """voucher 写 tool 同一 user 同 args 连续两次调 → 复用同一 PendingAction
    （同 action_id）。否则确认两次会创两条 voucher 记录（confirmation_action_id 不同）。"""
    import fakeredis.aioredis
    from hub.agent.tools.confirm_gate import ConfirmGate
    from hub.agent.react.tools.write import create_voucher_draft

    redis = fakeredis.aioredis.FakeRedis()
    gate = ConfirmGate(redis)
    set_confirm_gate(gate)
    monkeypatch.setattr("hub.agent.react.tools.write.require_permissions", AsyncMock())

    args = {
        "voucher_data": {
            "entries": [{"account": "应收", "debit": 1000, "credit": 0}],
            "total_amount": 1000, "summary": "X 月销售",
        },
        "rule_matched": "sales_template",
    }

    # 第 1 次
    r1 = await create_voucher_draft.ainvoke(args)
    aid1 = r1["action_id"]

    # 第 2 次 — 同 args（用户重复发请求）→ 必须复用 aid1
    r2 = await create_voucher_draft.ainvoke(args)
    aid2 = r2["action_id"]

    assert aid1 == aid2, (
        f"voucher 同 args 重复必须复用同一 PendingAction;实际 aid1={aid1} aid2={aid2}\n"
        f"否则用户连续两次确认会创建两条不同 voucher 草稿"
    )

    # 验证 fakeredis 只有 1 条 pending entry
    pendings = await gate.list_pending_for_context(
        conversation_id="test-conv", hub_user_id=1,
    )
    assert len(pendings) == 1


@pytest.mark.asyncio
async def test_e2e_cross_context_claim_blocked(monkeypatch):
    """另一个 user 不能 confirm 别人的 pending（ConfirmGate 跨 context 隔离）。"""
    import fakeredis.aioredis
    from hub.agent.tools.confirm_gate import ConfirmGate
    from hub.agent.react.tools.write import create_contract_draft
    from hub.agent.react.tools.confirm import confirm_action
    from hub.agent.react.context import tool_ctx, ToolContext

    redis = fakeredis.aioredis.FakeRedis()
    gate = ConfirmGate(redis)
    set_confirm_gate(gate)

    underlying = AsyncMock(return_value={"draft_id": 200, "file_sent": True})
    monkeypatch.setattr(
        "hub.agent.tools.generate_tools.generate_contract_draft", underlying,
    )
    async def _fake_resolve():
        return 1
    monkeypatch.setattr(
        "hub.agent.react.tools.write._resolve_default_template_id", _fake_resolve,
    )
    monkeypatch.setattr(
        "hub.agent.react.tools.write.require_permissions", AsyncMock(),
    )

    # User 1 创建 pending
    token1 = tool_ctx.set(ToolContext(
        hub_user_id=1, acting_as=None,
        conversation_id="conv-A", channel_userid="ding-1",
    ))
    try:
        plan = await create_contract_draft.ainvoke({
            "customer_id": 7,
            "items": [{"product_id": 1, "qty": 1, "price": 100.0}],
            "shipping_address": "X", "shipping_contact": "Y", "shipping_phone": "Z",
        })
        action_id = plan["action_id"]
    finally:
        tool_ctx.reset(token1)

    # User 2 尝试 confirm User 1 的 action — 应被拒
    token2 = tool_ctx.set(ToolContext(
        hub_user_id=2, acting_as=None,
        conversation_id="conv-A", channel_userid="ding-2",
    ))
    try:
        result = await confirm_action.ainvoke({"action_id": action_id})
        assert "error" in result
        underlying.assert_not_awaited()
    finally:
        tool_ctx.reset(token2)
