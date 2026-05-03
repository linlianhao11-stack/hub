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
