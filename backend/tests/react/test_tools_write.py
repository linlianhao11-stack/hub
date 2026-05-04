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


def _make_fake_pending(action_id: str, subgraph: str, payload: dict):
    """统一构造 PendingAction fake（避免每个 test 重复)。"""
    from datetime import datetime, timezone
    return PendingAction(
        action_id=action_id, conversation_id="test-conv", hub_user_id=1,
        subgraph=subgraph, summary="...", payload=payload,
        created_at=datetime.now(tz=timezone.utc), ttl_seconds=600,
        token=f"tok-{action_id}",
    )


@pytest.mark.asyncio
async def test_create_quote_draft_packs_shipping_into_extras(fake_ctx, monkeypatch):
    """quote 底层 generate_price_quote(customer_id, items, extras=None) 没 shipping_*；
    React tool 把 shipping 塞 extras。"""
    from hub.agent.react.tools.write import create_quote_draft

    gate = AsyncMock()
    gate.create_pending = AsyncMock(side_effect=lambda **kw: _make_fake_pending(
        "act-q1", "quote", kw["payload"]
    ))
    set_confirm_gate(gate)
    monkeypatch.setattr("hub.agent.react.tools.write.require_permissions", AsyncMock())

    result = await create_quote_draft.ainvoke({
        "customer_id": 7,
        "items": [{"product_id": 1, "qty": 5, "price": 280.0}],
        "shipping_address": "北京海淀",
    })
    assert result["status"] == "pending_confirmation"

    payload = gate.create_pending.call_args.kwargs["payload"]
    assert payload["tool_name"] == "generate_price_quote"
    assert payload["args"]["customer_id"] == 7
    assert payload["args"]["items"][0]["product_id"] == 1
    assert "shipping_address" not in payload["args"]
    assert payload["args"]["extras"]["shipping_address"] == "北京海淀"


@pytest.mark.asyncio
async def test_create_voucher_draft_takes_voucher_data_dict(fake_ctx, monkeypatch):
    """voucher 底层 create_voucher_draft(voucher_data: dict, ...) — 不是 order_id/voucher_type。"""
    from hub.agent.react.tools.write import create_voucher_draft

    gate = AsyncMock()
    gate.create_pending = AsyncMock(side_effect=lambda **kw: _make_fake_pending(
        "act-v1", "voucher", kw["payload"]
    ))
    set_confirm_gate(gate)
    monkeypatch.setattr("hub.agent.react.tools.write.require_permissions", AsyncMock())

    result = await create_voucher_draft.ainvoke({
        "voucher_data": {
            "entries": [{"account": "应收账款", "debit": 1000, "credit": 0}],
            "total_amount": 1000,
            "summary": "X 月销售",
        },
        "rule_matched": "sales_template",
    })
    assert result["status"] == "pending_confirmation"

    payload = gate.create_pending.call_args.kwargs["payload"]
    assert payload["tool_name"] == "create_voucher_draft"
    assert payload["args"]["voucher_data"]["total_amount"] == 1000
    assert payload["args"]["rule_matched"] == "sales_template"
    assert "confirmation_action_id" not in payload["args"]


@pytest.mark.asyncio
async def test_request_price_adjustment_returns_pending(fake_ctx, monkeypatch):
    from hub.agent.react.tools.write import request_price_adjustment

    gate = AsyncMock()
    gate.create_pending = AsyncMock(side_effect=lambda **kw: _make_fake_pending(
        "act-p1", "adjust_price", kw["payload"]
    ))
    set_confirm_gate(gate)
    monkeypatch.setattr("hub.agent.react.tools.write.require_permissions", AsyncMock())

    result = await request_price_adjustment.ainvoke({
        "customer_id": 7, "product_id": 1, "new_price": 280.0, "reason": "客户要求",
    })
    assert result["status"] == "pending_confirmation"

    payload = gate.create_pending.call_args.kwargs["payload"]
    assert payload["tool_name"] == "create_price_adjustment_request"
    assert payload["args"]["new_price"] == 280.0
    assert "confirmation_action_id" not in payload["args"]


@pytest.mark.asyncio
async def test_request_stock_adjustment_uses_adjustment_qty(fake_ctx, monkeypatch):
    """stock 底层 create_stock_adjustment_request(adjustment_qty: float, ...) — 不是 delta_qty。"""
    from hub.agent.react.tools.write import request_stock_adjustment

    gate = AsyncMock()
    gate.create_pending = AsyncMock(side_effect=lambda **kw: _make_fake_pending(
        "act-s1", "adjust_stock", kw["payload"]
    ))
    set_confirm_gate(gate)
    monkeypatch.setattr("hub.agent.react.tools.write.require_permissions", AsyncMock())

    result = await request_stock_adjustment.ainvoke({
        "product_id": 1, "adjustment_qty": -5.0, "reason": "盘亏",
    })
    assert result["status"] == "pending_confirmation"

    payload = gate.create_pending.call_args.kwargs["payload"]
    assert payload["tool_name"] == "create_stock_adjustment_request"
    assert payload["args"]["adjustment_qty"] == -5.0
    assert "delta_qty" not in payload["args"]


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


@pytest.mark.asyncio
async def test_voucher_cross_context_idempotency_returns_friendly_error(fake_ctx, monkeypatch):
    """voucher / price / stock 三类用 use_idempotency=True 的 plan tool —
    同 idempotency_key 跨 context 命中时 ConfirmGate 抛 CrossContextIdempotency,
    write tool 必须捕获转成稳定的 error dict,而不是让异常冒出 LangChain tool 链
    （用户看"AI 处理失败"比看"该申请已在其他会话处理中"差很多）。

    场景：用户 A 在 conv-A 提了凭证申请挂着 pending；用户 B 在 conv-B 用同 args
    重发同一份凭证 → idempotency_key 命中 A 的 pending → ConfirmGate fail-closed
    → 本测试验证 wrapper 把异常转成 error dict。
    """
    from hub.agent.react.tools.write import create_voucher_draft
    from hub.agent.tools.confirm_gate import CrossContextIdempotency

    gate = AsyncMock()
    gate.create_pending = AsyncMock(
        side_effect=CrossContextIdempotency("idem_key=react-abc 已被 conv=conv-A user=99 持有"),
    )
    set_confirm_gate(gate)
    monkeypatch.setattr("hub.agent.react.tools.write.require_permissions", AsyncMock())

    result = await create_voucher_draft.ainvoke({
        "voucher_data": {
            "entries": [{"account": "应收", "debit": 1000, "credit": 0}],
            "total_amount": 1000, "summary": "X 月销售",
        },
        "rule_matched": "sales_template",
    })
    # 关键断言：异常被捕获,LLM 看到的是 error dict 不是 raise
    assert "error" in result, f"期望 error dict,实际 {result}"
    err_text = result["error"]
    assert "其他会话" in err_text or "已被" in err_text, (
        f"error 文案应明确跨会话冲突,实际: {err_text!r}"
    )
    assert "status" not in result  # 不能误返 pending_confirmation

