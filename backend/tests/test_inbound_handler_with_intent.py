from unittest.mock import AsyncMock

import pytest

from hub.ports import ParsedIntent
from hub.services.identity_service import IdentityResolution


@pytest.mark.asyncio
async def test_rule_query_product_routes_to_query_product_usecase():
    from hub.handlers.dingtalk_inbound import handle_inbound

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=1, erp_user_id=42,
    ))
    chain = AsyncMock()
    chain.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_product",
        fields={"sku_or_keyword": "SKU100", "customer_keyword": None},
        confidence=0.95, parser="rule",
    ))
    query_product = AsyncMock()
    query_customer = AsyncMock()
    state = AsyncMock()
    state.load = AsyncMock(return_value=None)

    binding_svc = AsyncMock()
    sender = AsyncMock()

    payload = {
        "task_id": "t1", "task_type": "dingtalk_inbound",
        "payload": {"channel_userid": "m1", "content": "查 SKU100",
                    "conversation_id": "c1", "timestamp": 1700000000},
    }
    await handle_inbound(
        payload,
        binding_service=binding_svc, identity_service=identity_svc,
        sender=sender,
        chain_parser=chain, conversation_state=state,
        query_product_usecase=query_product,
        query_customer_history_usecase=query_customer,
        require_permissions=AsyncMock(return_value=None),
    )
    query_product.execute.assert_awaited_once()
    args = query_product.execute.call_args.kwargs
    assert args["sku_or_keyword"] == "SKU100"
    assert args["acting_as"] == 42


@pytest.mark.asyncio
async def test_query_customer_history_routes_correctly():
    from hub.handlers.dingtalk_inbound import handle_inbound

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=1, erp_user_id=42,
    ))
    chain = AsyncMock()
    chain.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_customer_history",
        fields={"sku_or_keyword": "SKU100", "customer_keyword": "阿里"},
        confidence=0.9, parser="rule",
    ))
    query_product = AsyncMock()
    query_customer = AsyncMock()
    state = AsyncMock()
    state.load = AsyncMock(return_value=None)

    payload = {
        "task_id": "t2", "task_type": "dingtalk_inbound",
        "payload": {"channel_userid": "m1", "content": "查 SKU100 给阿里",
                    "conversation_id": "c1", "timestamp": 1700000000},
    }
    await handle_inbound(
        payload,
        binding_service=AsyncMock(), identity_service=identity_svc, sender=AsyncMock(),
        chain_parser=chain, conversation_state=state,
        query_product_usecase=query_product,
        query_customer_history_usecase=query_customer,
        require_permissions=AsyncMock(return_value=None),
    )
    query_customer.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_low_confidence_sends_confirm_card():
    from hub.handlers.dingtalk_inbound import handle_inbound

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=1, erp_user_id=42,
    ))
    chain = AsyncMock()
    chain.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_product",
        fields={"sku_or_keyword": "X"},
        confidence=0.5, parser="llm", notes="low_confidence",
    ))
    state = AsyncMock()
    state.load = AsyncMock(return_value=None)
    sender = AsyncMock()

    payload = {
        "task_id": "t3", "task_type": "dingtalk_inbound",
        "payload": {"channel_userid": "m1", "content": "嗯帮我看看那个东西",
                    "conversation_id": "c1", "timestamp": 1700000000},
    }
    await handle_inbound(
        payload, binding_service=AsyncMock(), identity_service=identity_svc,
        sender=sender, chain_parser=chain, conversation_state=state,
        query_product_usecase=AsyncMock(),
        query_customer_history_usecase=AsyncMock(),
        require_permissions=AsyncMock(return_value=None),
    )
    sender.send_text.assert_awaited_once()
    state.save.assert_awaited_once()
    saved = state.save.call_args.args[1]
    assert saved.get("pending_confirm") == "yes"


@pytest.mark.asyncio
async def test_select_choice_with_pending_state():
    """用户回 "2" 时取上次保存的候选项进入对应 use case。"""
    from hub.handlers.dingtalk_inbound import handle_inbound

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=1, erp_user_id=42,
    ))
    chain = AsyncMock()
    chain.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="select_choice", fields={"choice": 2},
        confidence=0.95, parser="rule",
    ))
    state = AsyncMock()
    state.load = AsyncMock(return_value={
        "intent_type": "query_product",
        "resource": "商品",
        "candidates": [
            {"id": 1, "label": "A", "retail_price": "100"},
            {"id": 2, "label": "B", "retail_price": "200"},
        ],
        "pending_choice": "yes",
    })
    sender = AsyncMock()
    qp = AsyncMock()

    payload = {
        "task_id": "t4", "task_type": "dingtalk_inbound",
        "payload": {"channel_userid": "m1", "content": "2",
                    "conversation_id": "c1", "timestamp": 1700000000},
    }
    await handle_inbound(
        payload, binding_service=AsyncMock(), identity_service=identity_svc,
        sender=sender, chain_parser=chain, conversation_state=state,
        query_product_usecase=qp,
        query_customer_history_usecase=AsyncMock(),
        require_permissions=AsyncMock(return_value=None),
    )
    state.clear.assert_awaited_once_with("m1")
    qp.execute_selected.assert_awaited_once()
    qp.execute.assert_not_called()
    args = qp.execute_selected.call_args.kwargs
    assert args["product"]["id"] == 2


@pytest.mark.asyncio
async def test_permission_error_translates():
    from hub.error_codes import BizError, BizErrorCode
    from hub.handlers.dingtalk_inbound import handle_inbound

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=1, erp_user_id=42,
    ))
    chain = AsyncMock()
    chain.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_product", fields={"sku_or_keyword": "X"},
        confidence=0.95, parser="rule",
    ))

    require_permissions = AsyncMock(side_effect=BizError(BizErrorCode.PERM_NO_PRODUCT_QUERY))
    state = AsyncMock()
    state.load = AsyncMock(return_value=None)
    sender = AsyncMock()

    payload = {
        "task_id": "t5", "task_type": "dingtalk_inbound",
        "payload": {"channel_userid": "m1", "content": "查 X",
                    "conversation_id": "c1", "timestamp": 1700000000},
    }
    await handle_inbound(
        payload, binding_service=AsyncMock(), identity_service=identity_svc,
        sender=sender, chain_parser=chain, conversation_state=state,
        query_product_usecase=AsyncMock(),
        query_customer_history_usecase=AsyncMock(),
        require_permissions=require_permissions,
    )
    sender.send_text.assert_awaited_once()
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "权限" in sent
