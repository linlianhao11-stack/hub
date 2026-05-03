import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

from hub.agent.react.tools.write import create_contract_draft
from hub.agent.react.tools._confirm_helper import set_confirm_gate
from hub.agent.tools.confirm_gate import PendingAction


@pytest.mark.asyncio
async def test_create_contract_draft_returns_pending_with_template_id(fake_ctx, monkeypatch):
    """plan 阶段：(a) 内部查 default template_id (b) 校验 perm
    (c) create_pending 写 Redis (d) 不真正调底层 generate_contract_draft。
    payload args 包含 template_id（dispatch 时传给底层）。"""
    fake_pending = PendingAction(
        action_id="act-1",
        conversation_id="test-conv",
        hub_user_id=1,
        subgraph="contract",
        summary="将给客户...",
        payload={
            "tool_name": "generate_contract_draft",
            "args": {
                "template_id": 1,
                "customer_id": 7,
                "items": [{"product_id": 1, "qty": 10, "price": 300.0}],
                "shipping_address": "北京海淀",
                "shipping_contact": "张三",
                "shipping_phone": "13800001111",
                "payment_terms": "",
                "tax_rate": "",
            },
        },
        created_at=datetime.now(tz=timezone.utc),
        ttl_seconds=600,
        token="tok-1",
    )
    gate = AsyncMock()
    gate.create_pending = AsyncMock(return_value=fake_pending)
    set_confirm_gate(gate)

    async def _fake_resolve():
        return 1
    monkeypatch.setattr(
        "hub.agent.react.tools.write._resolve_default_template_id", _fake_resolve,
    )
    monkeypatch.setattr(
        "hub.agent.react.tools.write.require_permissions", AsyncMock(),
    )
    underlying = AsyncMock()
    monkeypatch.setattr(
        "hub.agent.tools.generate_tools.generate_contract_draft", underlying,
    )

    result = await create_contract_draft.ainvoke({
        "customer_id": 7,
        "items": [{"product_id": 1, "qty": 10, "price": 300.0}],
        "shipping_address": "北京海淀",
        "shipping_contact": "张三",
        "shipping_phone": "13800001111",
    })

    assert result["status"] == "pending_confirmation"
    assert result["action_id"] == "act-1"
    assert "preview" in result
    assert "token" not in result, "token 不应返给 LLM（confirm_action 内部反查）"
    underlying.assert_not_awaited()
    gate.create_pending.assert_awaited_once()

    call_kwargs = gate.create_pending.call_args.kwargs
    assert call_kwargs["subgraph"] == "contract"
    payload = call_kwargs["payload"]
    assert payload["tool_name"] == "generate_contract_draft"
    assert payload["args"]["template_id"] == 1
    assert payload["args"]["customer_id"] == 7
    assert payload["args"]["items"][0]["product_id"] == 1
    assert payload["args"]["shipping_address"] == "北京海淀"


@pytest.mark.asyncio
async def test_create_contract_draft_no_template_returns_error(fake_ctx, monkeypatch):
    """没启用 sales 模板 → tool 返 error 不挂起 LLM,引导 admin 上传模板。"""
    async def _fake_resolve():
        return None
    monkeypatch.setattr(
        "hub.agent.react.tools.write._resolve_default_template_id", _fake_resolve,
    )
    monkeypatch.setattr(
        "hub.agent.react.tools.write.require_permissions", AsyncMock(),
    )

    result = await create_contract_draft.ainvoke({
        "customer_id": 7,
        "items": [{"product_id": 1, "qty": 10, "price": 300.0}],
        "shipping_address": "x", "shipping_contact": "y", "shipping_phone": "z",
    })
    assert "error" in result
    assert "模板" in result["error"]
