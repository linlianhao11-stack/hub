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
