"""Plan 6 Task 10：inbound handler 接 ChainAgent 路径的测试（≥8 case）。

fixture 风格参考既有 test_dingtalk_inbound_handler.py：
- payload 字典完整（包含 task_id / task_type / payload 三层）
- 使用 AsyncMock 模拟所有外部依赖
- 所有测试均 @pytest.mark.asyncio
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hub.agent.types import AgentResult
from hub.handlers.dingtalk_inbound import RE_CONFIRM, handle_inbound
from hub.services.identity_service import IdentityResolution


# ===== 公共 fixture =====

def _make_payload(content: str, userid: str = "U1", conv_id: str = "dingtalk:U1") -> dict:
    """构造标准 task_data 字典。"""
    return {
        "task_id": "t-test",
        "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": userid,
            "content": content,
            "conversation_id": conv_id,
            "timestamp": 1700000000,
        },
    }


@pytest.fixture
def mock_sender():
    sender = AsyncMock()
    sender.send_text = AsyncMock(return_value=None)
    return sender


@pytest.fixture
def mock_identity():
    svc = AsyncMock()
    svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=10, erp_user_id=42,
    ))
    return svc


@pytest.fixture
def mock_binding():
    svc = AsyncMock()
    svc.initiate_binding = AsyncMock(return_value=MagicMock(reply_text="已发送绑定请求"))
    svc.unbind_self = AsyncMock(return_value=MagicMock(reply_text="已解绑"))
    return svc


@pytest.fixture
def mock_chain_agent():
    agent = AsyncMock()
    agent.run = AsyncMock(return_value=AgentResult.text_result("默认回复"))
    return agent


@pytest.fixture
def mock_conversation_state():
    state = AsyncMock()
    state.load = AsyncMock(return_value=None)
    state.save = AsyncMock()
    state.clear = AsyncMock()
    return state


# ===== Case 1：RE_CONFIRM 识别多种确认词 =====

def test_re_confirm_matches_all_variants():
    """RE_CONFIRM 能识别 6 种标准确认词。"""
    for word in ["是", "确认", "yes", "Y", "OK", "确定"]:
        assert RE_CONFIRM.match(word) is not None, f"应匹配: {word!r}"


def test_re_confirm_does_not_match_non_confirm():
    """RE_CONFIRM 不误匹配后面有其他字的表达。"""
    assert RE_CONFIRM.match("是的") is None, "后面多字，不应匹配"
    assert RE_CONFIRM.match("查 X") is None, "查询指令，不应匹配"
    assert RE_CONFIRM.match("确认一下") is None, "包含额外词，不应匹配"
    assert RE_CONFIRM.match("no") is None, "否定词，不应匹配"


# ===== Case 2：业务路径调 chain_agent.run =====

@pytest.mark.asyncio
async def test_inbound_calls_chain_agent_for_business_path(
    mock_chain_agent, mock_sender, mock_identity, mock_binding, mock_conversation_state,
):
    """已绑定用户发普通消息 → 调 chain_agent.run，不调 chain_parser。"""
    mock_chain_agent.run.return_value = AgentResult.text_result("库存 49")
    payload = _make_payload("查 SKU50139")

    await handle_inbound(
        payload,
        binding_service=mock_binding,
        identity_service=mock_identity,
        sender=mock_sender,
        chain_agent=mock_chain_agent,
        conversation_state=mock_conversation_state,
    )

    mock_chain_agent.run.assert_awaited_once()
    call_kwargs = mock_chain_agent.run.call_args.kwargs
    assert call_kwargs["user_message"] == "查 SKU50139"
    assert call_kwargs["user_just_confirmed"] is False
    mock_sender.send_text.assert_awaited()


# ===== Case 3：user_just_confirmed=True =====

@pytest.mark.asyncio
async def test_inbound_passes_user_just_confirmed_true_for_yes(
    mock_chain_agent, mock_sender, mock_identity, mock_binding, mock_conversation_state,
):
    """用户整条消息是'是' → user_just_confirmed=True 传给 chain_agent。"""
    mock_chain_agent.run.return_value = AgentResult.text_result("已创建凭证")
    payload = _make_payload("是")

    await handle_inbound(
        payload,
        binding_service=mock_binding,
        identity_service=mock_identity,
        sender=mock_sender,
        chain_agent=mock_chain_agent,
        conversation_state=mock_conversation_state,
    )

    call_kwargs = mock_chain_agent.run.call_args.kwargs
    assert call_kwargs["user_just_confirmed"] is True


# ===== Case 4：clarification → send_text =====

@pytest.mark.asyncio
async def test_inbound_clarification_sent_as_text(
    mock_chain_agent, mock_sender, mock_identity, mock_binding, mock_conversation_state,
):
    """AgentResult.clarification → 把 text 发给用户。"""
    mock_chain_agent.run.return_value = AgentResult.clarification("请问查哪个客户？")
    payload = _make_payload("查历史价")

    await handle_inbound(
        payload,
        binding_service=mock_binding,
        identity_service=mock_identity,
        sender=mock_sender,
        chain_agent=mock_chain_agent,
        conversation_state=mock_conversation_state,
    )

    mock_sender.send_text.assert_awaited()
    sent_text = mock_sender.send_text.call_args.kwargs.get("text") or \
                mock_sender.send_text.call_args[1].get("text") or \
                mock_sender.send_text.call_args[0][1]
    assert "请问查哪个客户？" in sent_text


# ===== Case 5：AgentResult.error_result → 友好错误文案 =====

@pytest.mark.asyncio
async def test_inbound_error_result_sends_friendly_message(
    mock_chain_agent, mock_sender, mock_identity, mock_binding, mock_conversation_state,
):
    """AgentResult.error_result → 用户收到 error.error 文本。"""
    mock_chain_agent.run.return_value = AgentResult.error_result("AI 响应超时")
    payload = _make_payload("查 SKU50139")

    await handle_inbound(
        payload,
        binding_service=mock_binding,
        identity_service=mock_identity,
        sender=mock_sender,
        chain_agent=mock_chain_agent,
        conversation_state=mock_conversation_state,
    )

    mock_sender.send_text.assert_awaited()
    sent_text = mock_sender.send_text.call_args.kwargs.get("text") or \
                mock_sender.send_text.call_args[1].get("text")
    assert "AI 响应超时" in sent_text


# ===== Case 6：ChainAgent 未预期异常 → 降级 RuleParser =====

@pytest.mark.asyncio
async def test_inbound_unhandled_exception_falls_back_to_rule_parser(
    mock_chain_agent, mock_sender, mock_identity, mock_binding, mock_conversation_state,
):
    """ChainAgent.run 抛未预期异常 → 调 rule_parser.parse 降级。"""
    mock_chain_agent.run.side_effect = RuntimeError("Redis 崩了")

    mock_rule = AsyncMock()
    rule_intent = MagicMock()
    rule_intent.intent_type = "unknown"
    mock_rule.parse = AsyncMock(return_value=rule_intent)

    payload = _make_payload("查 SKU50139")

    await handle_inbound(
        payload,
        binding_service=mock_binding,
        identity_service=mock_identity,
        sender=mock_sender,
        chain_agent=mock_chain_agent,
        rule_parser=mock_rule,
        conversation_state=mock_conversation_state,
    )

    mock_rule.parse.assert_awaited()
    mock_sender.send_text.assert_awaited()


# ===== Case 7：ChainAgent 异常且无 rule_parser → 友好兜底文案 =====

@pytest.mark.asyncio
async def test_inbound_unhandled_exception_no_rule_parser_sends_error(
    mock_chain_agent, mock_sender, mock_identity, mock_binding, mock_conversation_state,
):
    """ChainAgent 抛错且 rule_parser=None → 友好兜底文案，不崩。"""
    mock_chain_agent.run.side_effect = RuntimeError("未知异常")
    payload = _make_payload("查 SKU50139")

    await handle_inbound(
        payload,
        binding_service=mock_binding,
        identity_service=mock_identity,
        sender=mock_sender,
        chain_agent=mock_chain_agent,
        rule_parser=None,
        conversation_state=mock_conversation_state,
    )

    mock_sender.send_text.assert_awaited()
    sent_text = mock_sender.send_text.call_args.kwargs.get("text") or \
                mock_sender.send_text.call_args[1].get("text")
    assert "AI 处理出了点问题" in sent_text or "请稍后重试" in sent_text


# ===== Case 8：rule 命令不进 ChainAgent =====

@pytest.mark.asyncio
async def test_inbound_help_command_does_not_invoke_agent(
    mock_chain_agent, mock_sender, mock_identity, mock_binding, mock_conversation_state,
):
    """/帮助 命令直接返回帮助文本，不调 chain_agent.run。"""
    payload = _make_payload("帮助")

    await handle_inbound(
        payload,
        binding_service=mock_binding,
        identity_service=mock_identity,
        sender=mock_sender,
        chain_agent=mock_chain_agent,
        conversation_state=mock_conversation_state,
    )

    mock_chain_agent.run.assert_not_called()
    mock_sender.send_text.assert_awaited()


# ===== Case 9：/绑定 命令不进 ChainAgent =====

@pytest.mark.asyncio
async def test_inbound_bind_command_does_not_invoke_agent(
    mock_chain_agent, mock_sender, mock_identity, mock_binding, mock_conversation_state,
):
    """/绑定 命令走 BindingService，不调 chain_agent.run。"""
    payload = _make_payload("/绑定 zhangsan")

    await handle_inbound(
        payload,
        binding_service=mock_binding,
        identity_service=mock_identity,
        sender=mock_sender,
        chain_agent=mock_chain_agent,
        conversation_state=mock_conversation_state,
    )

    mock_binding.initiate_binding.assert_awaited_once()
    mock_chain_agent.run.assert_not_called()


# ===== Case 10：pending_choice（数字编号）不进 ChainAgent =====

@pytest.mark.asyncio
async def test_inbound_pending_choice_digit_does_not_invoke_agent(
    mock_chain_agent, mock_sender, mock_identity, mock_binding,
):
    """state 里有 pending_choice + 用户回数字 '1' → pending_choice 路径，不进 ChainAgent。"""
    state_data = {
        "pending_choice": "yes",
        "intent_type": "query_product",
        "candidates": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
    }
    mock_conv_state = AsyncMock()
    mock_conv_state.load = AsyncMock(return_value=state_data)
    mock_conv_state.clear = AsyncMock()

    mock_query_product = AsyncMock()
    mock_query_product.execute_selected = AsyncMock()

    payload = _make_payload("1")

    await handle_inbound(
        payload,
        binding_service=mock_binding,
        identity_service=mock_identity,
        sender=mock_sender,
        chain_agent=mock_chain_agent,
        conversation_state=mock_conv_state,
        query_product_usecase=mock_query_product,
        require_permissions=AsyncMock(return_value=None),
    )

    mock_chain_agent.run.assert_not_called()


# ===== Case 11：chain_agent=None → 友好占位提示 =====

@pytest.mark.asyncio
async def test_inbound_no_chain_agent_sends_placeholder(
    mock_sender, mock_identity, mock_binding, mock_conversation_state,
):
    """chain_agent=None 时发送占位提示（未配置场景）。"""
    payload = _make_payload("查 SKU50139")

    await handle_inbound(
        payload,
        binding_service=mock_binding,
        identity_service=mock_identity,
        sender=mock_sender,
        chain_agent=None,
        conversation_state=mock_conversation_state,
    )

    mock_sender.send_text.assert_awaited()
    sent_text = mock_sender.send_text.call_args.kwargs.get("text") or \
                mock_sender.send_text.call_args[1].get("text")
    assert "帮助" in sent_text or "没听懂" in sent_text


# ===== Case 12：未绑定用户不进 ChainAgent =====

@pytest.mark.asyncio
async def test_inbound_unbound_user_does_not_invoke_agent(
    mock_chain_agent, mock_sender, mock_binding, mock_conversation_state,
):
    """未绑定用户 → 提示绑定，不调 chain_agent.run。"""
    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=False, erp_active=False,
    ))

    payload = _make_payload("查 SKU50139")

    await handle_inbound(
        payload,
        binding_service=mock_binding,
        identity_service=identity_svc,
        sender=mock_sender,
        chain_agent=mock_chain_agent,
        conversation_state=mock_conversation_state,
    )

    mock_chain_agent.run.assert_not_called()
    mock_sender.send_text.assert_awaited()
