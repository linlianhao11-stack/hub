"""ReActAgent ↔ Memory 接通的单元测试（v11）。

覆盖三件事:
1. memory_loader 注入后,_dynamic_prompt 被调用并把 memory section 拼进 SystemMessage
2. ReActAgent.run 结束后调 MemoryWriter.extract_and_write(从 ToolCallLog 拉日志)
3. invoke_business_tool 调用后把 customer_id / product_id 写入 SessionMemory entity_refs

这组测试不依赖真 LLM,用 AsyncMock + fakeredis。
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from hub.agent.memory.loader import MemoryLoader
from hub.agent.memory.types import Memory
from hub.agent.memory.writer import MemoryWriter
from hub.agent.react.agent import ReActAgent
from hub.agent.react.context import tool_ctx, ToolContext


@pytest.mark.asyncio
async def test_dynamic_prompt_injects_memory_section_when_loader_provided():
    """memory_loader 注入 + tool_ctx 已 set → _dynamic_prompt 把 memory 渲染进 SystemMessage。"""
    fake_loader = AsyncMock(spec=MemoryLoader)
    fake_loader.load = AsyncMock(return_value=Memory(
        session=None,
        user={"facts": [
            {"fact": "用户偏好按历史价下单", "kind": "reference"},
        ], "preferences": {}},
        customers={},
        products={},
    ))

    agent = ReActAgent(
        chat_model=AsyncMock(),
        tools=[],
        checkpointer=None,
        memory_loader=fake_loader,
    )

    # 模拟 run() 已经 set 好 ContextVar 的状态
    token = tool_ctx.set(ToolContext(
        hub_user_id=42,
        acting_as=None,
        conversation_id="cv-1",
        channel_userid="ding-1",
    ))
    try:
        result_msgs = await agent._dynamic_prompt({"messages": [HumanMessage(content="hi")]})
    finally:
        tool_ctx.reset(token)

    assert len(result_msgs) == 2
    assert isinstance(result_msgs[0], SystemMessage)
    sys_text = result_msgs[0].content
    # memory section 必须出现在 SystemMessage 中
    assert "[当前用户偏好]" in sys_text
    assert "用户偏好按历史价下单" in sys_text
    # SYSTEM_PROMPT 头部内容也保留
    assert "HUB" in sys_text
    fake_loader.load.assert_awaited_once_with(hub_user_id=42, conversation_id="cv-1")


@pytest.mark.asyncio
async def test_dynamic_prompt_falls_back_when_loader_raises():
    """memory_loader.load 抛异常 → 回落静态 SYSTEM_PROMPT,不阻塞业务。"""
    fake_loader = AsyncMock(spec=MemoryLoader)
    fake_loader.load = AsyncMock(side_effect=RuntimeError("redis down"))

    agent = ReActAgent(
        chat_model=AsyncMock(),
        tools=[],
        checkpointer=None,
        memory_loader=fake_loader,
    )

    token = tool_ctx.set(ToolContext(
        hub_user_id=1,
        acting_as=None,
        conversation_id="cv-fail",
        channel_userid="ding-x",
    ))
    try:
        msgs = await agent._dynamic_prompt({"messages": [HumanMessage(content="x")]})
    finally:
        tool_ctx.reset(token)

    # 没崩,SystemMessage 是基础 SYSTEM_PROMPT
    assert isinstance(msgs[0], SystemMessage)
    assert "HUB" in msgs[0].content
    # 没拼 memory section
    assert "[当前用户偏好]" not in msgs[0].content


@pytest.mark.asyncio
async def test_dynamic_prompt_no_loader_uses_system_prompt_only():
    """memory_loader=None → 直接静态 SYSTEM_PROMPT,不调 loader。"""
    # 不传 memory_loader,prompt 应该是静态字符串路径
    agent = ReActAgent(
        chat_model=AsyncMock(), tools=[], checkpointer=None,
    )
    # 没 loader 注入时 _dynamic_prompt 不会被 langgraph 用到,
    # langgraph 直接用 SYSTEM_PROMPT 字符串。验证 attribute:
    assert agent._memory_loader is None


@pytest.mark.asyncio
async def test_run_triggers_memory_writer_after_invoke():
    """ReActAgent.run 完成后 fire-and-forget 调 MemoryWriter.extract_and_write,
    传 LangGraph state messages(v12 改造:不再查 ToolCallLog)。"""
    msgs = [
        HumanMessage(content="查阿里订单"),
        AIMessage(content="阿里近一个月订单 3 笔"),
    ]
    fake_compiled = AsyncMock()
    fake_compiled.ainvoke = AsyncMock(return_value={"messages": msgs})

    fake_writer = MagicMock(spec=MemoryWriter)
    fake_writer.extract_and_write = AsyncMock(return_value={
        "new_user_facts": 0, "new_customer_facts": 0,
        "new_product_facts": 0, "dedup_skipped": 0,
        "input_chars": 100, "duration_ms": 50,
    })
    fake_ai_provider = AsyncMock()

    agent = ReActAgent(
        chat_model=AsyncMock(),
        tools=[],
        checkpointer=None,
        memory_writer=fake_writer,
        ai_provider=fake_ai_provider,
    )
    agent.compiled_graph = fake_compiled

    # patch log_conversation 防止真写 DB
    with patch("hub.agent.react.agent.log_conversation", AsyncMock()):
        reply = await agent.run(
            user_message="查阿里订单",
            hub_user_id=1,
            conversation_id="cv-w1",
            acting_as=None,
            channel_userid="ding-w1",
        )
        assert reply == "阿里近一个月订单 3 笔"
        # fire-and-forget 任务需要让出事件循环让它跑完
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    fake_writer.extract_and_write.assert_awaited_once()
    call_kwargs = fake_writer.extract_and_write.await_args.kwargs
    assert call_kwargs["conversation_id"] == "cv-w1"
    assert call_kwargs["hub_user_id"] == 1
    assert call_kwargs["rounds_count"] == 1   # 1 条 AIMessage
    assert call_kwargs["ai_provider"] is fake_ai_provider
    # v12: 直接传 messages,不再查 ToolCallLog
    assert call_kwargs["messages"] == msgs


@pytest.mark.asyncio
async def test_run_skips_memory_writer_when_not_provided():
    """memory_writer=None → 不抽取,业务正常返回。"""
    fake_compiled = AsyncMock()
    fake_compiled.ainvoke = AsyncMock(return_value={
        "messages": [AIMessage(content="ok")],
    })

    agent = ReActAgent(
        chat_model=AsyncMock(),
        tools=[],
        checkpointer=None,
        memory_writer=None,    # 显式不传
        ai_provider=None,
    )
    agent.compiled_graph = fake_compiled

    reply = await agent.run(
        user_message="x", hub_user_id=1, conversation_id="cv-z",
        acting_as=None, channel_userid="ding-z",
    )
    assert reply == "ok"
    # 没崩、没抛、没意外行为


@pytest.mark.asyncio
async def test_invoke_business_tool_writes_entity_refs_to_session():
    """invoke_business_tool 成功调底层后,从 result 提取 entity 写入 SessionMemory。"""
    from hub.agent.react.tools import _invoke
    from hub.agent.memory.session import SessionMemory
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis()
    session = SessionMemory(redis=redis)

    # 注入 SessionMemory
    _invoke.set_session_memory(session)
    try:
        token = tool_ctx.set(ToolContext(
            hub_user_id=7, acting_as=None,
            conversation_id="cv-e1", channel_userid="ding-e",
        ))
        try:
            # mock 权限和 log
            with patch.object(_invoke, "require_permissions", AsyncMock()), \
                 patch.object(_invoke, "log_tool_call") as mock_logger:
                from contextlib import asynccontextmanager

                @asynccontextmanager
                async def _fake_log(**kw):
                    ctx = MagicMock()
                    ctx.set_result = lambda r: None
                    yield ctx

                mock_logger.side_effect = _fake_log

                async def _fake_fn(*, query, acting_as_user_id):
                    return {
                        "items": [
                            {"customer_id": 100, "name": "阿里"},
                            {"customer_id": 101, "name": "得帆"},
                        ],
                        "total": 2,
                    }

                result = await _invoke.invoke_business_tool(
                    tool_name="search_customers",
                    perm="usecase.query_customer.use",
                    args={"query": "阿里"},
                    fn=_fake_fn,
                )
                assert result["total"] == 2
        finally:
            tool_ctx.reset(token)

        # 验证 SessionMemory 收到了 entity refs
        refs = await session.get_entity_refs("cv-e1", 7)
        assert refs.customer_ids == {100, 101}
        assert refs.product_ids == set()
    finally:
        _invoke.set_session_memory(None)  # 清理全局 state
        await redis.aclose()


@pytest.mark.asyncio
async def test_invoke_business_tool_skips_session_when_not_set():
    """没注入 SessionMemory 时,invoke_business_tool 仍正常返回。"""
    from hub.agent.react.tools import _invoke

    _invoke.set_session_memory(None)

    token = tool_ctx.set(ToolContext(
        hub_user_id=1, acting_as=None,
        conversation_id="cv-no-sess", channel_userid="ding-x",
    ))
    try:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_log(**kw):
            ctx = MagicMock()
            ctx.set_result = lambda r: None
            yield ctx

        with patch.object(_invoke, "require_permissions", AsyncMock()), \
             patch.object(_invoke, "log_tool_call", side_effect=_fake_log):
            async def _fn(*, x, acting_as_user_id):
                return {"product_id": 200, "x": x}
            result = await _invoke.invoke_business_tool(
                tool_name="get_product_detail",
                perm="usecase.query_product.use",
                args={"x": 1},
                fn=_fn,
            )
            assert result == {"product_id": 200, "x": 1}
    finally:
        tool_ctx.reset(token)
