"""Plan 4 → Plan 6 升级后的 inbound handler 意图路由测试。

原 Plan 4 测试用 chain_parser 模拟意图解析；Plan 6 起改用 chain_agent。
这里保留原测试的业务语义（路由到正确的用例 / 错误翻译），
改用 chain_agent mock + AgentResult 来替代 chain_parser.parse 的返回值。

Plan 4 pending_choice (select_choice) 多轮路径仍走 conversation_state，
该路径独立于 chain_agent，直接数字编号匹配，保持原测试不变。
"""
from unittest.mock import AsyncMock

import pytest

from hub.agent.types import AgentResult
from hub.services.identity_service import IdentityResolution


# ===== 辅助 =====

def _payload(content: str, userid: str = "m1") -> dict:
    return {
        "task_id": "t-intent",
        "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": userid,
            "content": content,
            "conversation_id": "c1",
            "timestamp": 1700000000,
        },
    }


def _identity(hub_user_id: int = 1, erp_user_id: int = 42) -> AsyncMock:
    svc = AsyncMock()
    svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True,
        hub_user_id=hub_user_id, erp_user_id=erp_user_id,
    ))
    return svc


# ===== Case 1：业务查询 → chain_agent 返文本 → send_text =====

@pytest.mark.asyncio
async def test_rule_query_product_routes_to_chain_agent():
    """查商品请求 → chain_agent.run 被调 → 文本结果发给用户。

    Plan 6 中 chain_agent 内部调 ERP tool，不再经由 query_product_usecase；
    此测试只验证 handler 正确把请求交给 chain_agent 并把结果发出去。
    """
    from hub.handlers.dingtalk_inbound import handle_inbound

    agent = AsyncMock()
    agent.run = AsyncMock(return_value=AgentResult.text_result("SKU100 库存 200 件"))
    state = AsyncMock()
    state.load = AsyncMock(return_value=None)
    sender = AsyncMock()

    await handle_inbound(
        _payload("查 SKU100"),
        binding_service=AsyncMock(), identity_service=_identity(),
        sender=sender, chain_agent=agent, conversation_state=state,
    )

    agent.run.assert_awaited_once()
    call_kw = agent.run.call_args.kwargs
    assert call_kw["user_message"] == "查 SKU100"
    assert call_kw["acting_as"] == 42
    sender.send_text.assert_awaited()
    sent = (
        sender.send_text.call_args.kwargs.get("text")
        or sender.send_text.call_args[1].get("text")
    )
    assert "SKU100" in sent


# ===== Case 2：客户历史价查询 → chain_agent 返文本 =====

@pytest.mark.asyncio
async def test_query_customer_history_routes_correctly():
    """查客户历史价 → chain_agent.run 接收请求并返回结果。"""
    from hub.handlers.dingtalk_inbound import handle_inbound

    agent = AsyncMock()
    agent.run = AsyncMock(return_value=AgentResult.text_result("阿里最近成交 ¥88"))
    state = AsyncMock()
    state.load = AsyncMock(return_value=None)

    await handle_inbound(
        _payload("查 SKU100 给阿里"),
        binding_service=AsyncMock(), identity_service=_identity(),
        sender=AsyncMock(), chain_agent=agent, conversation_state=state,
    )

    agent.run.assert_awaited_once()
    kw = agent.run.call_args.kwargs
    assert "SKU100 给阿里" in kw["user_message"] or kw["user_message"] == "查 SKU100 给阿里"


# ===== Case 3：clarification（低置信度）→ 用户收到澄清问题 =====

@pytest.mark.asyncio
async def test_low_confidence_sends_clarification():
    """chain_agent 返 clarification → send_text 把问题发给用户。

    Plan 6 的低置信度处理在 chain_agent 内部（返 AgentResult.clarification），
    handler 直接把 text 发出去；不再保存 conversation_state。
    """
    from hub.handlers.dingtalk_inbound import handle_inbound

    agent = AsyncMock()
    agent.run = AsyncMock(return_value=AgentResult.clarification("你想查哪个商品？"))
    state = AsyncMock()
    state.load = AsyncMock(return_value=None)
    sender = AsyncMock()

    await handle_inbound(
        _payload("嗯帮我看看那个东西"),
        binding_service=AsyncMock(), identity_service=_identity(),
        sender=sender, chain_agent=agent, conversation_state=state,
    )

    sender.send_text.assert_awaited_once()
    sent = (
        sender.send_text.call_args.kwargs.get("text")
        or sender.send_text.call_args[1].get("text")
    )
    assert "你想查哪个商品？" in sent


# ===== Case 4：pending_choice 数字编号回路（Plan 4 遗产，与 chain_agent 无关）=====

@pytest.mark.asyncio
async def test_select_choice_with_pending_state():
    """用户回 "2" 时取上次保存的候选项进入对应 use case。

    pending_choice 路径不经过 chain_agent（直接数字匹配），保持原有行为。
    """
    from hub.handlers.dingtalk_inbound import handle_inbound

    identity_svc = _identity()
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
    state.clear = AsyncMock()
    sender = AsyncMock()
    qp = AsyncMock()
    agent = AsyncMock()

    await handle_inbound(
        _payload("2"),
        binding_service=AsyncMock(), identity_service=identity_svc,
        sender=sender, chain_agent=agent, conversation_state=state,
        query_product_usecase=qp,
        query_customer_history_usecase=AsyncMock(),
        require_permissions=AsyncMock(return_value=None),
    )

    state.clear.assert_awaited_once_with("m1")
    qp.execute_selected.assert_awaited_once()
    qp.execute.assert_not_called()
    args = qp.execute_selected.call_args.kwargs
    assert args["product"]["id"] == 2
    # pending_choice 路径不调 chain_agent
    agent.run.assert_not_called()


# ===== Case 5：BizError（来自 chain_agent）→ 用户收到权限提示 =====

@pytest.mark.asyncio
async def test_permission_error_translates():
    """chain_agent.run 抛 BizError → handler 翻译为中文发给用户。"""
    from hub.error_codes import BizError, BizErrorCode
    from hub.handlers.dingtalk_inbound import handle_inbound

    agent = AsyncMock()
    agent.run = AsyncMock(side_effect=BizError(BizErrorCode.PERM_NO_PRODUCT_QUERY))
    state = AsyncMock()
    state.load = AsyncMock(return_value=None)
    sender = AsyncMock()

    await handle_inbound(
        _payload("查 X"),
        binding_service=AsyncMock(), identity_service=_identity(),
        sender=sender, chain_agent=agent, conversation_state=state,
    )

    sender.send_text.assert_awaited_once()
    sent = (
        sender.send_text.call_args.kwargs.get("text")
        or sender.send_text.call_args[1].get("text")
    )
    # BizError(PERM_NO_PRODUCT_QUERY) → "你没有「商品查询」功能的使用权限，请联系管理员开通"
    assert "权限" in sent
