"""ReAct agent 端到端测试 — fake chat model 驱动真 LangGraph + 真 ALL_TOOLS + 真 fakeredis ConfirmGate。

按 messages 长度决定 LLM 下一步,跑完整两轮 plan-then-execute。

**关键约束**（Codex review 升级）：fake chat model 必须是真 BaseChatModel 子类,
能跟 LangGraph create_react_agent 完整对接,**禁止降级为直接调 tool.ainvoke** —
否则 LangGraph ToolNode + AIMessage tool_calls 编排路径没被覆盖,confirm_action
ReAct 编排的正确性也没被验证。
"""
import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from datetime import datetime, timezone

import fakeredis.aioredis
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langgraph.checkpoint.memory import MemorySaver

from hub.agent.react.agent import ReActAgent
from hub.agent.react.tools import ALL_TOOLS
from hub.agent.react.tools._confirm_helper import set_confirm_gate
from hub.agent.react.tools.confirm import WRITE_TOOL_DISPATCH
from hub.agent.tools.confirm_gate import ConfirmGate


class _ToolBindingFake(GenericFakeChatModel):
    """`GenericFakeChatModel` 自身**没**实现 `bind_tools`（继承 `BaseChatModel.bind_tools`
    抛 `NotImplementedError`）。`langgraph.prebuilt.create_react_agent` 编译期会调
    `model.bind_tools(tools)` —— 不覆盖会直接 NotImplementedError 挂在 agent 构造期。

    覆盖成 no-op 返 self：scripted iterator 仍然驱动 ainvoke,但 LangGraph 编译能过。
    """
    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self


def _make_scripted_chat(messages: list[BaseMessage]):
    """LangGraph 兼容的 fake chat model：scripted AIMessage iterator + bind_tools no-op。

    iter(list) 持有原 list 引用,可以在 T1 完成后用 `messages[i].tool_calls[...]['args']['action_id'] = ...`
    修改尚未 yield 的 entry（plan 步骤要求 T2 前回填真 action_id）。
    """
    return _ToolBindingFake(messages=iter(messages))


@asynccontextmanager
async def _fake_log_ctx(**kw):
    """替代 log_tool_call 的 no-op context manager（避免 ToolCallLog.create DB 写入）。"""
    ctx = AsyncMock()
    ctx.set_result = lambda r: None
    yield ctx


@pytest.mark.asyncio
async def test_react_agent_full_plan_then_execute_loop(monkeypatch):
    """端到端两轮：T1 preview → T2 confirm。

    步骤：
      T1: user "做合同..." → LLM 调 search_customer + create_contract_draft → preview
      T2: user "是" → LLM 调 confirm_action(action_id) → claim → dispatch → 底层执行
    """
    # mock ERP adapter
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={
        "items": [{"id": 7, "name": "阿里"}], "total": 1,
    })
    monkeypatch.setattr("hub.agent.tools.erp_tools.current_erp_adapter", lambda: erp)
    monkeypatch.setattr("hub.agent.react.tools.read.current_erp_adapter", lambda: erp)

    # patch log_tool_call — invoke_business_tool 调链必须绕过 ToolCallLog DB 写入
    monkeypatch.setattr(
        "hub.agent.react.tools._invoke.log_tool_call",
        _fake_log_ctx,
    )

    # 真 fakeredis ConfirmGate
    redis = fakeredis.aioredis.FakeRedis()
    gate = ConfirmGate(redis)
    set_confirm_gate(gate)

    # mock 底层 generate_contract_draft（不真渲染 docx）
    underlying = AsyncMock(return_value={"draft_id": 99, "file_sent": True})
    monkeypatch.setitem(
        WRITE_TOOL_DISPATCH,
        "generate_contract_draft",
        ("usecase.generate_contract.use", underlying, False),
    )
    async def _fake_template():
        return 1
    monkeypatch.setattr(
        "hub.agent.react.tools.write._resolve_default_template_id", _fake_template,
    )
    # mock 权限校验全过
    monkeypatch.setattr("hub.agent.react.tools.write.require_permissions", AsyncMock())
    monkeypatch.setattr("hub.agent.react.tools._invoke.require_permissions", AsyncMock())

    # === scripted: T1 (search → create_contract → preview), T2 (confirm → final) ===
    scripted = [
        AIMessage(content="", tool_calls=[{
            "id": "c1", "name": "search_customer", "args": {"query": "阿里"},
        }]),
        AIMessage(content="", tool_calls=[{
            "id": "c2", "name": "create_contract_draft", "args": {
                "customer_id": 7,
                "items": [{"product_id": 1, "qty": 10, "price": 300.0}],
                "shipping_address": "北京海淀",
                "shipping_contact": "张三",
                "shipping_phone": "13800001111",
            },
        }]),
        AIMessage(content="将给阿里生成合同：X1×10@300。请回'是'确认。"),

        # T2 LLM 调用 1 — action_id 占位,T1 后回填
        AIMessage(content="", tool_calls=[{
            "id": "c3", "name": "confirm_action", "args": {"action_id": "<filled-at-runtime>"},
        }]),
        AIMessage(content="合同已生成,draft_id=99,文件已发送。"),
    ]

    chat = _make_scripted_chat(scripted)

    agent = ReActAgent(
        chat_model=chat, tools=ALL_TOOLS,
        checkpointer=MemorySaver(), recursion_limit=10,
    )

    # === T1: preview ===
    reply1 = await agent.run(
        user_message="给阿里做合同 X1 10 个 300 北京海淀 张三 13800001111",
        hub_user_id=1, conversation_id="cv-e2e",
        acting_as=None, channel_userid="ding-e2e",
    )
    assert reply1 is not None
    assert "确认" in reply1 or "请回" in reply1
    underlying.assert_not_awaited()  # T1 阶段：底层不该被调

    # 从 fakeredis 反查真 action_id（plan 阶段创建的 PendingAction）
    pendings = await gate.list_pending_for_context(
        conversation_id="cv-e2e", hub_user_id=1,
    )
    assert len(pendings) == 1
    real_action_id = pendings[0].action_id
    # 把 T2 scripted 的 action_id 占位填上真值（iter 还没 yield 到 index 3,可改）
    scripted[3].tool_calls[0]["args"]["action_id"] = real_action_id

    # === T2: confirm ===
    reply2 = await agent.run(
        user_message="是",
        hub_user_id=1, conversation_id="cv-e2e",
        acting_as=None, channel_userid="ding-e2e",
    )
    assert reply2 is not None
    assert "已生成" in reply2 or "draft_id" in reply2

    # 关键断言：底层执行**且仅执行一次**
    underlying.assert_awaited_once()
    fn_kwargs = underlying.call_args.kwargs
    assert fn_kwargs["customer_id"] == 7
    assert fn_kwargs["template_id"] == 1
    assert fn_kwargs["hub_user_id"] == 1

    # pending 已被消费（claim HDEL）
    pendings_after = await gate.list_pending_for_context(
        conversation_id="cv-e2e", hub_user_id=1,
    )
    assert len(pendings_after) == 0, "claim 后 pending 必须 HDEL（不可双发）"
