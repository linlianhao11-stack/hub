"""Plan 6 Task 6：ChainAgent 测试（12 case）。

测试策略：
- AgentLLMClient.chat 用 AsyncMock 替换，不走真实 LLM
- ToolRegistry.call 用 AsyncMock 替换，避免依赖真实 ERP tool
- ConfirmGate 用真 redis:6380（与 Task 2 fixture 风格一致）
- ConversationLog 用真 PG（conftest.py setup_db 已处理）
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from hub.agent.chain_agent import ChainAgent
from hub.agent.llm_client import AgentLLMClient
from hub.agent.memory.loader import MemoryLoader
from hub.agent.memory.session import SessionMemory
from hub.agent.memory.types import ConversationHistory, Memory
from hub.agent.tools.confirm_gate import ConfirmGate
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools.types import ClaimFailedError, MissingConfirmationError
from hub.agent.types import (
    AgentLLMResponse,
    AgentMaxRoundsError,
    ToolCall,
)
from hub.capabilities.deepseek import LLMParseError, LLMServiceError
from hub.error_codes import BizError

REDIS_URL = "redis://localhost:6380/0"
TEST_CONV_PREFIX = "hub:agent:conv:test-chain-"
PENDING_PREFIX = "hub:agent:pending:"
CONFIRMED_PREFIX = "hub:agent:confirmed:"


# ====== Fixtures ======

@pytest_asyncio.fixture
async def redis_client():
    """真 redis 客户端。"""
    import redis.asyncio as redis_async
    client = redis_async.Redis.from_url(REDIS_URL, decode_responses=True)
    yield client
    # 清理测试期间产生的 key
    async for key in client.scan_iter(f"{TEST_CONV_PREFIX}*"):
        await client.delete(key)
    async for key in client.scan_iter(f"{PENDING_PREFIX}*"):
        await client.delete(key)
    async for key in client.scan_iter(f"{CONFIRMED_PREFIX}*"):
        await client.delete(key)
    await client.aclose()


@pytest_asyncio.fixture
async def redis_client_bytes():
    """bytes 模式 redis（SessionMemory 需要）。"""
    import redis.asyncio as redis_async
    client = redis_async.Redis.from_url(REDIS_URL, decode_responses=False)
    yield client
    keys = await client.keys(f"{TEST_CONV_PREFIX}*".encode())
    if keys:
        await client.delete(*keys)
    await client.aclose()


@pytest.fixture
def confirm_gate(redis_client):
    return ConfirmGate(redis_client)


@pytest.fixture
def session_memory(redis_client_bytes):
    return SessionMemory(redis_client_bytes)


def _empty_memory() -> Memory:
    return Memory(
        session=ConversationHistory(conversation_id="test"),
        user={"facts": [], "preferences": {}},
        customers={},
        products={},
    )


def _make_mock_memory_loader() -> AsyncMock:
    """构造返回空 Memory 的 MemoryLoader mock。"""
    loader = AsyncMock(spec=MemoryLoader)
    loader.load = AsyncMock(return_value=_empty_memory())
    return loader


def _make_mock_registry() -> AsyncMock:
    """构造空 ToolRegistry mock（call 默认成功返 {}）。"""
    registry = AsyncMock(spec=ToolRegistry)
    registry.schema_for_user = AsyncMock(return_value=[])
    registry.call = AsyncMock(return_value={"status": "ok"})
    return registry


def _text_response(text: str) -> AgentLLMResponse:
    return AgentLLMResponse(
        text=text,
        tool_calls=[],
        usage_prompt_tokens=50,
        usage_completion_tokens=20,
        raw_message={"role": "assistant", "content": text},
    )


def _tool_call_response(tool_name: str, args: dict, call_id: str = "call_001") -> AgentLLMResponse:
    return AgentLLMResponse(
        text=None,
        tool_calls=[ToolCall(id=call_id, name=tool_name, args=args)],
        usage_prompt_tokens=80,
        usage_completion_tokens=30,
        raw_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": tool_name, "arguments": "{}"},
            }],
        },
    )


def _conv_id(suffix: str) -> str:
    return f"test-chain-{suffix}"


def _make_agent(*, llm_chat_side_effect=None, llm_chat_return=None,
                registry=None, confirm_gate_fixture=None,
                session_memory_fixture=None, memory_loader=None) -> ChainAgent:
    """构造 ChainAgent，注入各组件 mock。"""
    llm = AsyncMock(spec=AgentLLMClient)
    if llm_chat_side_effect is not None:
        llm.chat = AsyncMock(side_effect=llm_chat_side_effect)
    elif llm_chat_return is not None:
        llm.chat = AsyncMock(return_value=llm_chat_return)
    else:
        llm.chat = AsyncMock(return_value=_text_response("默认回复"))

    reg = registry or _make_mock_registry()
    loader = memory_loader or _make_mock_memory_loader()

    # confirm_gate / session_memory 用真实或 mock
    cg = confirm_gate_fixture or AsyncMock(spec=ConfirmGate)
    if confirm_gate_fixture is None:
        cg.confirm_all_pending = AsyncMock(return_value=[])
        cg.add_pending = AsyncMock(return_value="action-id-001")

    sm = session_memory_fixture or AsyncMock(spec=SessionMemory)
    if session_memory_fixture is None:
        sm.load = AsyncMock(return_value=ConversationHistory(conversation_id="test"))
        sm.append = AsyncMock()

    agent = ChainAgent(
        llm=llm,
        registry=reg,
        confirm_gate=cg,
        session_memory=sm,
        memory_loader=loader,
    )
    return agent


# ====== 测试 ======

@pytest.mark.asyncio
async def test_single_round_text_response():
    """mock LLM 返 text 不调 tool → AgentResult.text_result。"""
    agent = _make_agent(llm_chat_return=_text_response("找到了 3 个客户"))
    result = await agent.run(
        "查询客户列表",
        hub_user_id=1, conversation_id=_conv_id("single-text"),
        acting_as=101,
    )
    assert result.kind == "text"
    assert result.text == "找到了 3 个客户"
    assert result.error is None


@pytest.mark.asyncio
async def test_multi_round_tool_calls():
    """mock LLM 第 1 round 调 search_products，第 2 round 看到 tool result 后返 text。"""
    call_count = 0

    async def chat_side_effect(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # 第 1 round：调 tool
            return _tool_call_response("search_products", {"keyword": "手机"})
        else:
            # 第 2 round：返 text
            return _text_response("找到了 10 款手机商品")

    registry = _make_mock_registry()
    registry.call = AsyncMock(return_value={"items": [{"id": 1, "name": "iPhone"}]})

    agent = _make_agent(llm_chat_side_effect=chat_side_effect, registry=registry)
    result = await agent.run(
        "查手机",
        hub_user_id=1, conversation_id=_conv_id("multi-round"),
        acting_as=101,
    )

    assert result.kind == "text"
    assert result.text == "找到了 10 款手机商品"
    assert call_count == 2  # 确认经过了 2 round
    registry.call.assert_called_once()  # 只调了 1 次 tool


@pytest.mark.asyncio
async def test_max_rounds_exceeded_raises():
    """mock LLM 永远调 tool → 5 round 后 AgentMaxRoundsError 被捕获，返 error_result。"""
    # ChainAgent.run 内部 raise AgentMaxRoundsError 但它不被 reraise，而是写 log 然后在 finally 中返回
    # 实际上 AgentMaxRoundsError 是 raise 出去的，外层需要捕获
    agent = _make_agent(llm_chat_return=_tool_call_response("some_tool", {}))
    registry = _make_mock_registry()
    registry.call = AsyncMock(return_value={"ok": True})
    agent.registry = registry

    with pytest.raises(AgentMaxRoundsError):
        await agent.run(
            "无限循环测试",
            hub_user_id=1, conversation_id=_conv_id("max-rounds"),
            acting_as=101,
        )


@pytest.mark.asyncio
async def test_clarification_response():
    """mock LLM 返含问号的文本 → AgentResult.clarification。"""
    agent = _make_agent(llm_chat_return=_text_response("请问您是指哪个客户？"))
    result = await agent.run(
        "查客户",
        hub_user_id=1, conversation_id=_conv_id("clarification"),
        acting_as=101,
    )
    assert result.kind == "clarification"
    assert "请问" in result.text


@pytest.mark.asyncio
async def test_llm_timeout_returns_error():
    """mock LLM 30s 不返 → AgentResult.error_result（超时不应抛上层）。"""
    async def slow_chat(*args, **kwargs):
        await asyncio.sleep(999)

    llm = AsyncMock(spec=AgentLLMClient)
    llm.chat = slow_chat

    loader = _make_mock_memory_loader()
    cg = AsyncMock(spec=ConfirmGate)
    cg.confirm_all_pending = AsyncMock(return_value=[])
    sm = AsyncMock(spec=SessionMemory)
    sm.load = AsyncMock(return_value=ConversationHistory(conversation_id="test"))
    sm.append = AsyncMock()

    agent = ChainAgent(
        llm=llm,
        registry=_make_mock_registry(),
        confirm_gate=cg,
        session_memory=sm,
        memory_loader=loader,
    )
    # 把 timeout 缩短加速测试
    agent.LLM_TIMEOUT = 0.05

    result = await agent.run(
        "测试超时",
        hub_user_id=1, conversation_id=_conv_id("timeout"),
        acting_as=101,
    )
    assert result.kind == "error"
    assert result.error is not None
    assert "超时" in result.error or "timeout" in result.error.lower()


@pytest.mark.asyncio
async def test_llm_service_error_returns_error():
    """mock LLM 抛 LLMServiceError → AgentResult.error_result。"""
    agent = _make_agent(
        llm_chat_side_effect=LLMServiceError("LLM 503")
    )
    result = await agent.run(
        "测试服务错误",
        hub_user_id=1, conversation_id=_conv_id("service-error"),
        acting_as=101,
    )
    assert result.kind == "error"
    assert "不可用" in result.error or "error" in result.error.lower() or result.error


@pytest.mark.asyncio
async def test_tool_call_missing_confirmation_adds_pending(confirm_gate, session_memory):
    """写 tool 没传 confirmation → ChainAgent 调 add_pending；history 含 next_action=preview。"""
    call_count = 0

    async def chat_side_effect(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _tool_call_response("create_voucher", {"amount": 1000})
        return _text_response("已预览，等您确认")

    registry = _make_mock_registry()
    registry.call = AsyncMock(side_effect=MissingConfirmationError("未确认"))

    conv_id = _conv_id(f"missing-confirm-{uuid.uuid4().hex[:6]}")
    loader = _make_mock_memory_loader()

    agent = ChainAgent(
        llm=AsyncMock(spec=AgentLLMClient, chat=AsyncMock(side_effect=chat_side_effect)),
        registry=registry,
        confirm_gate=confirm_gate,
        session_memory=session_memory,
        memory_loader=loader,
    )

    result = await agent.run(
        "开一张 1000 元的单",
        hub_user_id=1, conversation_id=conv_id,
        acting_as=101,
    )

    # confirm_gate.add_pending 应该被调用（通过真 redis 写入）
    pending = await confirm_gate.list_pending(conv_id, 1)
    assert len(pending) >= 1
    assert pending[0]["tool_name"] == "create_voucher"

    # 结果应该有回复（LLM 看到 preview 后回了文本）
    assert result.kind in ("text", "clarification")


@pytest.mark.asyncio
async def test_tool_call_claim_failed_does_not_add_pending(confirm_gate, session_memory):
    """写 tool 错 token → 不 add_pending；history 含 next_action=re_preview。"""
    call_count = 0

    async def chat_side_effect(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _tool_call_response("create_voucher", {"amount": 500,
                                                           "confirmation_action_id": "old-id",
                                                           "confirmation_token": "bad-token"})
        return _text_response("请重新确认")

    registry = _make_mock_registry()
    registry.call = AsyncMock(side_effect=ClaimFailedError("token 无效"))

    conv_id = _conv_id(f"claim-failed-{uuid.uuid4().hex[:6]}")
    loader = _make_mock_memory_loader()

    agent = ChainAgent(
        llm=AsyncMock(spec=AgentLLMClient, chat=AsyncMock(side_effect=chat_side_effect)),
        registry=registry,
        confirm_gate=confirm_gate,
        session_memory=session_memory,
        memory_loader=loader,
    )

    await agent.run(
        "用旧 token 重试",
        hub_user_id=1, conversation_id=conv_id,
        acting_as=101,
    )

    # ClaimFailed 不应 add_pending（pending 应为空）
    pending = await confirm_gate.list_pending(conv_id, 1)
    assert len(pending) == 0, "ClaimFailedError 不应触发 add_pending"


@pytest.mark.asyncio
async def test_user_just_confirmed_builds_hint(confirm_gate, session_memory):
    """传 user_just_confirmed=True + 已有 pending → confirm_hint 含 action_id+token。"""
    conv_id = _conv_id(f"reconfirm-{uuid.uuid4().hex[:6]}")

    # 预先 add_pending
    await confirm_gate.add_pending(conv_id, 1, "create_voucher", {"amount": 800})

    captured_messages = []

    async def chat_capture(messages, **kwargs):
        captured_messages.extend(messages)
        return _text_response("已重新调用写 tool 成功")

    loader = _make_mock_memory_loader()
    agent = ChainAgent(
        llm=AsyncMock(spec=AgentLLMClient, chat=AsyncMock(side_effect=chat_capture)),
        registry=_make_mock_registry(),
        confirm_gate=confirm_gate,
        session_memory=session_memory,
        memory_loader=loader,
    )

    _result = await agent.run(
        "是",
        hub_user_id=1, conversation_id=conv_id,
        acting_as=101,
        user_just_confirmed=True,
    )

    # confirm_hint 应该以 system 消息注入，含 action_id 和 token
    system_msgs = [m for m in captured_messages if m.get("role") == "system"]
    system_content = " ".join(m.get("content", "") for m in system_msgs)
    assert "confirmation_action_id" in system_content or "action_id" in system_content
    assert "token" in system_content


@pytest.mark.asyncio
async def test_writes_conversation_log_on_success():
    """成功 → ConversationLog 写 final_status=success + rounds_count + tokens_used。"""
    from hub.models.conversation import ConversationLog

    conv_id = _conv_id(f"log-success-{uuid.uuid4().hex[:6]}")
    agent = _make_agent(llm_chat_return=_text_response("查询成功"))

    await agent.run(
        "查询",
        hub_user_id=1, conversation_id=conv_id,
        acting_as=101,
    )

    log = await ConversationLog.filter(conversation_id=conv_id).first()
    assert log is not None
    assert log.final_status == "success"
    assert log.rounds_count >= 1
    assert log.tokens_used >= 0
    assert log.ended_at is not None


@pytest.mark.asyncio
async def test_writes_conversation_log_on_failure():
    """LLM 服务错误 → ConversationLog 写 final_status=failed_system + error_summary。"""
    from hub.models.conversation import ConversationLog

    conv_id = _conv_id(f"log-fail-{uuid.uuid4().hex[:6]}")
    agent = _make_agent(llm_chat_side_effect=LLMServiceError("模拟服务宕机"))

    result = await agent.run(
        "测试失败记录",
        hub_user_id=1, conversation_id=conv_id,
        acting_as=101,
    )
    assert result.kind == "error"

    log = await ConversationLog.filter(conversation_id=conv_id).first()
    assert log is not None
    assert log.final_status == "failed_system"
    assert log.error_summary is not None
    assert len(log.error_summary) > 0
    assert log.ended_at is not None


@pytest.mark.asyncio
async def test_concurrent_calls_use_separate_conversation_ids():
    """asyncio.gather 两个不同 conv_id → 各自独立 history（不串）。"""
    from hub.models.conversation import ConversationLog

    conv_a = _conv_id(f"concurrent-a-{uuid.uuid4().hex[:6]}")
    conv_b = _conv_id(f"concurrent-b-{uuid.uuid4().hex[:6]}")

    responses = {
        conv_a: _text_response("A 的回复"),
        conv_b: _text_response("B 的回复"),
    }

    # 两个独立 agent（模拟并发两个不同 conv）
    agent_a = _make_agent(llm_chat_return=responses[conv_a])
    agent_b = _make_agent(llm_chat_return=responses[conv_b])

    results = await asyncio.gather(
        agent_a.run("A 的请求", hub_user_id=1, conversation_id=conv_a, acting_as=101),
        agent_b.run("B 的请求", hub_user_id=2, conversation_id=conv_b, acting_as=102),
    )

    result_a, result_b = results
    assert result_a.kind == "text"
    assert result_a.text == "A 的回复"
    assert result_b.kind == "text"
    assert result_b.text == "B 的回复"

    # 各自有独立的 ConversationLog
    log_a = await ConversationLog.filter(conversation_id=conv_a).first()
    log_b = await ConversationLog.filter(conversation_id=conv_b).first()
    assert log_a is not None
    assert log_b is not None
    assert log_a.hub_user_id == 1
    assert log_b.hub_user_id == 2
    # 确认两个 log 是不同记录
    assert log_a.id != log_b.id


@pytest.mark.asyncio
async def test_conversation_log_per_user_isolation():
    """**核心安全测试** v8 review #20：群聊场景同 conv_id 不同 hub_user_id
    创建独立 ConversationLog，admin 后台可 per-user 看会话决策链。

    旧行为：单字段 unique 让 B 用户进群聊时复用 A 创建的 log → 串归因。
    新行为：复合 unique (conversation_id, hub_user_id) 让两人各 1 条。
    """
    from hub.models.conversation import ConversationLog

    # 模拟群聊：A 用户 (hub_user_id=10) 和 B 用户 (hub_user_id=20) 共享同一 conv_id
    conv_id = _conv_id(f"groupchat-{uuid.uuid4().hex[:6]}")

    agent_a = _make_agent(llm_chat_return=_text_response("A 的合同已生成"))
    agent_b = _make_agent(llm_chat_return=_text_response("B 的合同已生成"))

    await agent_a.run(
        "给翼蓝做合同",
        hub_user_id=10, conversation_id=conv_id,
        acting_as=101, channel_userid="ding-A",
    )
    await agent_b.run(
        "给得帆做合同",
        hub_user_id=20, conversation_id=conv_id,
        acting_as=102, channel_userid="ding-B",
    )

    # 关键：同 conv_id 应有 2 条独立 log（A 和 B 各 1 条）
    logs = await ConversationLog.filter(conversation_id=conv_id).all()
    assert len(logs) == 2, f"群聊同 conv 应每 user 1 条 log，实际 {len(logs)}"

    by_user = {log.hub_user_id: log for log in logs}
    assert 10 in by_user, "A 用户的 log 应存在"
    assert 20 in by_user, "B 用户的 log 应存在"
    assert by_user[10].id != by_user[20].id, "两条 log 必须是不同记录"
    # channel_userid 各自存自己的（admin 查 task detail 用 channel_userid 过滤就能区分）
    assert by_user[10].channel_userid == "ding-A"
    assert by_user[20].channel_userid == "ding-B"


@pytest.mark.asyncio
async def test_records_tokens_used_correctly():
    """v2 加固（review I-3）：tokens_used 精确等于所有 round usage 累加。"""
    # mock LLM 第 1 round 返 (prompt=100, completion=20)，第 2 round 返 (prompt=50, completion=30)
    # ChainAgent.run 后 ConversationLog.tokens_used == 100+20+50+30 == 200
    from hub.models.conversation import ConversationLog

    call_count = 0

    async def chat_side_effect(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return AgentLLMResponse(
                text=None,
                tool_calls=[ToolCall(id="call_t1", name="search_products", args={})],
                usage_prompt_tokens=100,
                usage_completion_tokens=20,
                raw_message={"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_t1", "type": "function",
                    "function": {"name": "search_products", "arguments": "{}"},
                }]},
            )
        return AgentLLMResponse(
            text="找到了商品",
            tool_calls=[],
            usage_prompt_tokens=50,
            usage_completion_tokens=30,
            raw_message={"role": "assistant", "content": "找到了商品"},
        )

    registry = _make_mock_registry()
    registry.call = AsyncMock(return_value={"items": [{"id": 1}]})
    conv_id = _conv_id(f"tokens-{uuid.uuid4().hex[:6]}")
    agent = _make_agent(llm_chat_side_effect=chat_side_effect, registry=registry)

    await agent.run("查商品", hub_user_id=1, conversation_id=conv_id, acting_as=101)

    log = await ConversationLog.filter(conversation_id=conv_id).first()
    assert log is not None
    assert log.tokens_used == 200, f"expected 200 but got {log.tokens_used}"


@pytest.mark.asyncio
async def test_tool_other_exception_injects_error_to_history_not_raise():
    """v2 加固（review I-3）：tool 抛非确认类异常（如 ERP 5xx）→ 注入 history.error，不上抛。"""
    call_count = 0

    async def chat_side_effect(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _tool_call_response("search_products", {})
        # 第 2 round：LLM 看到 error 后返文本
        return _text_response("抱歉，查询失败，请稍后重试")

    registry = _make_mock_registry()
    registry.call = AsyncMock(side_effect=RuntimeError("ERP 503"))

    agent = _make_agent(llm_chat_side_effect=chat_side_effect, registry=registry)
    result = await agent.run(
        "查商品", hub_user_id=1, conversation_id=_conv_id(f"err-inject-{uuid.uuid4().hex[:6]}"),
        acting_as=101,
    )
    # 不应抛 RuntimeError；应返回文本（LLM 决策后的回复）
    assert result.kind == "text"
    assert result.text is not None


@pytest.mark.asyncio
async def test_llm_invalid_response_returns_error():
    """v2 加固（review I-3）：LLM 返非法格式 → AgentLLMClient 抛 LLMParseError → ChainAgent 返 error_result。"""
    agent = _make_agent(
        llm_chat_side_effect=LLMParseError("LLM 返回格式异常: choices 缺失")
    )
    result = await agent.run(
        "测试解析错误",
        hub_user_id=1, conversation_id=_conv_id(f"parse-err-{uuid.uuid4().hex[:6]}"),
        acting_as=101,
    )
    assert result.kind == "error"
    assert result.error is not None


@pytest.mark.asyncio
async def test_tool_call_bizerror_propagates():
    """v2 加固（review I-4）：mock registry.call 抛 BizError → ChainAgent.run 应抛 BizError 上层。"""
    registry = _make_mock_registry()
    from hub.error_codes import BizErrorCode
    registry.call = AsyncMock(side_effect=BizError(BizErrorCode.PERM_DOWNSTREAM_DENIED))

    agent = _make_agent(
        llm_chat_return=_tool_call_response("create_voucher", {"amount": 100}),
        registry=registry,
    )

    with pytest.raises(BizError):
        await agent.run(
            "开一张单",
            hub_user_id=1, conversation_id=_conv_id(f"bizerr-{uuid.uuid4().hex[:6]}"),
            acting_as=101,
        )
