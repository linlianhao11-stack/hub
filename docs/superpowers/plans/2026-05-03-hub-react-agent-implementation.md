# HUB ReAct Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace LangGraph DAG workflow（router + 7 subgraphs + 30+ state fields）with single ReAct agent + 16 tools + plan-then-execute write flow，让钉钉机器人自然处理跨轮 reference / 客户切换 / "复用上份"等任意自然表达。

**Architecture:** `create_react_agent` + LangChain `@tool` 包装现有业务函数 + 写操作经 ConfirmGate plan-then-execute wrapper。MessagesState only，业务上下文活在 message history 里。

**Tech Stack:** LangGraph 0.2.76 / langchain-core 0.3.x / DeepSeek beta endpoint / AsyncPostgresSaver / Redis ConfirmGate / Tortoise ORM。

参考：`docs/superpowers/specs/2026-05-03-hub-react-agent-design.md`

---

## File Structure

### 新建（agent 流程层）

| 路径 | 责任 |
|---|---|
| `backend/hub/agent/react/__init__.py` | 包导出 `ReActAgent` 主类 |
| `backend/hub/agent/react/context.py` | `_tool_ctx: ContextVar` 管理 hub_user_id/acting_as/conversation_id |
| `backend/hub/agent/react/llm.py` | `build_chat_model()` 把 hub `DeepSeekLLMClient` 包成 LangChain `BaseChatModel` |
| `backend/hub/agent/react/tools/__init__.py` | re-export ALL_TOOLS = read + write + confirm |
| `backend/hub/agent/react/tools/_invoke.py` | `invoke_business_tool()` helper：统一 require_permissions + log_tool_call + ctx 注入 |
| `backend/hub/agent/react/tools/read.py` | 10 个 read tool（细粒度）|
| `backend/hub/agent/react/tools/write.py` | 5 个 write tool（plan-then-execute）|
| `backend/hub/agent/react/tools/_confirm_helper.py` | `create_pending_action()` helper（含 canonical idempotency_key 生成）|
| `backend/hub/agent/react/tools/confirm.py` | `confirm_action` tool + `WRITE_TOOL_DISPATCH` 表 |
| `backend/hub/agent/react/prompts.py` | system prompt 文本 |
| `backend/hub/agent/react/agent.py` | `ReActAgent` 主类（封装 `create_react_agent`，对外保持 `.run()` 接口跟 GraphAgent 兼容）|

### 修改

| 路径 | 改动 |
|---|---|
| `backend/worker.py` | 启动构造 `ReActAgent` 替代 `GraphAgent`；删 `tool_registry` / `register_all_tools` / `_tool_ctx` ContextVar / `tool_executor` 闭包 |
| `backend/hub/handlers/dingtalk_inbound.py` | `agent.run()` 接口已兼容，无需改（GraphAgentAdapter 模式保留）|
| `backend/pyproject.toml` | **不需要**改 —— 已含 `langchain-core>=0.3.0,<0.4` + `langgraph>=0.2.0,<0.3`；`langchain` 主包 / `langchain-openai` **均不需要新增**（详见 Task 1.2 / Task 1.3 决策）|

### 删除（最后一个 phase）

| 路径 | 备注 |
|---|---|
| `backend/hub/agent/graph/agent.py` | GraphAgent 主类 |
| `backend/hub/agent/graph/router.py` | LLM router |
| `backend/hub/agent/graph/config.py` | build_langgraph_config |
| `backend/hub/agent/graph/nodes/*.py` | 全部节点（extract / resolve / parse / validate / ask_user / format_response / cleanup / pre_router / confirm）|
| `backend/hub/agent/graph/subgraphs/*.py` | 7 个子图 |
| `backend/hub/agent/graph/state.py` | 业务字段（保留 `Intent` / `CustomerInfo` / `ProductInfo` / `ContractItem` / `ShippingInfo` 留给 tool 内部用）|
| `backend/tests/agent/test_node_*.py` | 节点级测试 |
| `backend/tests/agent/test_subgraph_*.py` | 子图测试 |
| `backend/tests/agent/test_graph_agent.py` | GraphAgent 主类测试 |
| `backend/tests/agent/test_graph_state.py` | 业务字段测试 |
| `backend/tests/agent/test_graph_router_accuracy.py` | router 准确率 |

### 新建测试

| 路径 | 责任 |
|---|---|
| `backend/tests/react/__init__.py` | 包标记 |
| `backend/tests/react/conftest.py` | 共用 fixtures（mock erp_adapter / confirm_gate / sender / llm）|
| `backend/tests/react/test_context.py` | ContextVar 单测 |
| `backend/tests/react/test_llm.py` | DeepSeek → LangChain ChatModel 适配 |
| `backend/tests/react/test_prompts.py` | system prompt 关键约定校验 |
| `backend/tests/react/test_invoke.py` | `invoke_business_tool` helper 单测（权限拒绝 / 审计落库 / ctx 注入）|
| `backend/tests/react/test_tools_read.py` | 10 read tool 单测 |
| `backend/tests/react/test_tools_write.py` | 5 write tool（plan 阶段）单测 |
| `backend/tests/react/test_confirm_wrapper.py` | confirm_action + WRITE_TOOL_DISPATCH |
| `backend/tests/react/test_react_agent.py` | ReActAgent 主类（thread_id / messages / 集成）|
| `backend/tests/react/test_react_agent_e2e.py` | fake LLM + fakeredis ConfirmGate + 真 dispatch 端到端 |
| `backend/tests/react/test_acceptance_scenarios.py` | yaml 场景断言（mock LLM, smoke level）|
| `backend/tests/react/test_realllm_eval.py` | `@pytest.mark.realllm` 真 LLM eval |
| `backend/tests/react/fixtures/scenarios/*.yaml` | 6 个 story + 4 个新增（"同样给 X" / 客户切换 / fingerprint 重发 / 闲聊）|

---

# Phase 1: Foundation（Day 1 上午）

目标：`backend/hub/agent/react/` 包结构 + ContextVar + LangChain LLM 适配。能 import 整个包不报错，但还没逻辑。

## Task 1.1: 包骨架 + ContextVar

**Files:**
- Create: `backend/hub/agent/react/__init__.py`
- Create: `backend/hub/agent/react/context.py`
- Test: `backend/tests/react/__init__.py` + `backend/tests/react/test_context.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/react/test_context.py
import pytest
from hub.agent.react.context import tool_ctx, ToolContext


def test_tool_ctx_default_is_none():
    """未 set 时 .get() 返默认 None — tool 函数据此判断 'react agent 入口必须先 set'。"""
    ctx = tool_ctx.get()
    assert ctx is None


def test_tool_ctx_set_and_get():
    token = tool_ctx.set(ToolContext(
        hub_user_id=42,
        acting_as=None,
        conversation_id="cv-1",
        channel_userid="ding-u-1",
    ))
    try:
        ctx = tool_ctx.get()
        assert ctx["hub_user_id"] == 42
        assert ctx["conversation_id"] == "cv-1"
        assert ctx["channel_userid"] == "ding-u-1"
        assert ctx["acting_as"] is None
    finally:
        tool_ctx.reset(token)
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_context.py -v
# Expected: ImportError 或 ModuleNotFoundError
```

- [ ] **Step 3: 实现**

```python
# backend/hub/agent/react/context.py
"""Tool 调用 context — 由 ReActAgent 在每次入口 set，tool 内部 get。

不通过 LangChain tool args 传 hub_user_id 等内部字段，避免 LLM 看到 / 误改。
跟当前 worker.py `_tool_ctx` 同一个 ContextVar 实例（迁移期 worker 改 import 路径）。
"""
from __future__ import annotations
from contextvars import ContextVar
from typing import TypedDict


class ToolContext(TypedDict):
    hub_user_id: int
    acting_as: int | None
    conversation_id: str
    channel_userid: str


tool_ctx: ContextVar[ToolContext | None] = ContextVar("hub_react_tool_ctx", default=None)
```

```python
# backend/hub/agent/react/__init__.py
"""HUB ReAct Agent — 单 agent + tool calling 替代 LangGraph DAG。"""
from hub.agent.react.context import tool_ctx, ToolContext

__all__ = ["tool_ctx", "ToolContext"]
```

```python
# backend/tests/react/__init__.py
# (empty)
```

- [ ] **Step 4: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_context.py -v
# Expected: 2 passed
```

- [ ] **Step 5: Commit**

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent
git add backend/hub/agent/react/__init__.py backend/hub/agent/react/context.py \
        backend/tests/react/__init__.py backend/tests/react/test_context.py
git commit -m "feat(hub): tool ContextVar + 包骨架（Plan 6 v10 Phase 1.1）"
```

---

## Task 1.2: 依赖核验（无需新增 langchain 主包）

**理由**：本 plan 全部 import 都走 `langchain_core` / `langgraph.prebuilt`
（`from langchain_core.tools import tool` / `from langgraph.prebuilt import create_react_agent`
等），**不需要** langchain 主包（agents / chains / loaders 等高层模块未使用）。
当前 `backend/pyproject.toml` 已含 `langchain-core>=0.3.0,<0.4` + `langgraph>=0.2.0,<0.3`，足够。

**Files:** （无文件改动，仅核验）

- [ ] **Step 1: 核验 langchain-core / langgraph 已就位**

```bash
cd backend && .venv/bin/python -c "from langchain_core.tools import tool; from langchain_core.language_models.chat_models import BaseChatModel; from langgraph.prebuilt import create_react_agent; print('ok')"
# Expected: ok
```

如果失败（pyproject 被改动过），加回 `langchain-core>=0.3.0,<0.4` + `langgraph>=0.2.0,<0.3` 后 `pip install -e .` 再跑。

- [ ] **Step 2: 不 commit（无文件改动）**

---

## Task 1.3: DeepSeek → LangChain ChatModel 适配

**Files:**
- Create: `backend/hub/agent/react/llm.py`
- Test: `backend/tests/react/test_llm.py`

理由：`create_react_agent` 接 LangChain `BaseChatModel`，hub 现有的 `DeepSeekLLMClient` 是自己写的 httpx wrapper。需要适配。LangChain 已有 `langchain-openai` 的 `ChatOpenAI` 能直接连 DeepSeek（OpenAI 兼容协议），但 hub 已有的 prefix completion / strict / thinking 控制要保留。

**决策**：写 **`DeepSeekChatModel(BaseChatModel)` wrapper** 复用现有
`DeepSeekLLMClient.chat()` 的 retry 语义 + chat_log 写入。**不**直接用 ChatOpenAI。

理由（Codex review 升级到必修）：
- `DeepSeekLLMClient` 已经为 staging 实战补过 `{400, 408, 425, 429, 500-504, TransportError}` retry +
  指数退避 + 错误分类 + usage / cache_hit_rate 写 `chat_log` 审计。Plan 6 v9 staging
  已经被 DeepSeek 偶发 400 / 5xx 打中过几次,这套逻辑是真踩坑出来的。
- `ChatOpenAI(max_retries=3)` 默认只覆盖 `{408, 429, 500-504, TransportError}`,**不**重试 400。
- ReAct 切换后如果只用 ChatOpenAI,线上稳定性会**倒退**回踩坑前。
- chat_log（含 cache_hit_rate / token usage）是 admin 决策链 + 计费审计依赖,丢了
  会影响 admin 看 LLM 成本。

包装方案：
- 子类化 `langchain_core.language_models.chat_models.BaseChatModel`
- 实现 `_agenerate(messages, stop, run_manager, **kwargs) -> ChatResult`
  内部转 hub `DeepSeekLLMClient.chat()` 调用（messages / tools / temperature /
  max_tokens 全转过去）
- 实现 `bind_tools(tools, **kwargs)` 把 LangChain Tool 列表转成 hub 的 tool schema dict
- 实现 `_llm_type` 属性返 "deepseek-react"

工作量预估：~80 行代码 + 测试。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/react/test_llm.py
import pytest
from hub.agent.react.llm import build_chat_model


def test_build_chat_model_returns_langchain_chat():
    """build_chat_model 应返回 LangChain BaseChatModel 实例。"""
    from langchain_core.language_models.chat_models import BaseChatModel
    model = build_chat_model(api_key="test", base_url="https://api.deepseek.com/beta",
                              model="deepseek-chat")
    assert isinstance(model, BaseChatModel)


def test_build_chat_model_supports_tool_calls():
    """模型必须支持 bind_tools（react agent 需要）。"""
    from langchain_core.tools import tool
    @tool
    def fake() -> str:
        """fake."""
        return "x"
    model = build_chat_model(api_key="test", base_url="https://api.deepseek.com/beta",
                              model="deepseek-chat")
    bound = model.bind_tools([fake])
    assert bound is not None


def test_build_chat_model_uses_deepseek_wrapper():
    """build_chat_model 必须返 DeepSeekChatModel,不是直接 ChatOpenAI。"""
    from hub.agent.react.llm import DeepSeekChatModel
    model = build_chat_model(
        api_key="test", base_url="https://api.deepseek.com/beta",
        model="deepseek-chat",
    )
    assert isinstance(model, DeepSeekChatModel), (
        f"必须返 DeepSeekChatModel（复用 hub DeepSeekLLMClient retry 语义）,实际 {type(model)}"
    )


@pytest.mark.asyncio
async def test_deepseek_chat_model_retries_on_400(monkeypatch):
    """关键：DeepSeekChatModel 必须复用 DeepSeekLLMClient 的 400 重试语义。

    场景：DeepSeek 偶发 400（schema jitter）— 旧 GraphAgent 路径靠 hub 自己 retry 兜住,
    ReAct 路径必须保留这个能力（用 wrapper 而不是直接 ChatOpenAI）。
    """
    from hub.agent.react.llm import DeepSeekChatModel
    from langchain_core.messages import HumanMessage
    from unittest.mock import AsyncMock

    # mock 底层 DeepSeekLLMClient.chat — 第 1 次抛 400,第 2 次返成功
    # 注：DeepSeekLLMClient.chat 是 keyword-only（`async def chat(self, *, messages, ...)`）,
    # stub 也用 `**kwargs` 接所有 kwargs,避免位置参数 → TypeError。
    # 真 client 返 LLMResponse @dataclass(text, finish_reason, tool_calls, cache_hit_rate, usage, raw)，
    # 见 hub/agent/llm_client.py:220。下面 stub 缺 `raw` 但 _agenerate 没读 raw 所以 OK；
    # 实施时如果改了 wrapper 读 resp.raw 记得给 stub 补上。
    fake_client = AsyncMock()
    call_count = {"n": 0}
    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            from hub.agent.llm_client import LLMServiceError
            raise LLMServiceError("LLM 400")  # DeepSeekLLMClient 自己内部 retry
        # 第 2 次成功（实际 DeepSeekLLMClient 内部已经做完 retry,这里直接返）
        return type("R", (), {
            "text": "ok", "finish_reason": "stop", "tool_calls": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "cache_hit_rate": 0.0,
        })()
    fake_client.chat = chat_with_retry

    model = DeepSeekChatModel(deepseek_client=fake_client)
    # bind_tools 不报错（tool 列表可以空)
    model_bound = model.bind_tools([])
    # 实际 _agenerate 应该靠 DeepSeekLLMClient 的内部 retry 兜住
    # 这里测试 wrapper 调 client.chat 一次（client 自己内部多次重试）
    result = await model_bound.ainvoke([HumanMessage(content="hi")])
    assert result is not None
    # client.chat 被调用（具体次数取决于 DeepSeekLLMClient 内部重试,
    # 这里我们 mock 已经做了 retry — 第 2 次成功）
    assert call_count["n"] >= 1


def test_build_chat_model_configures_retry_and_timeout():
    """build_chat_model 必须把 timeout / max_retries **透传**到底层 DeepSeekLLMClient。

    Codex P2：旧版本 build_chat_model 暴露了 timeout 参数但只传 api_key/base_url/model
    给 DeepSeekLLMClient，运维以为放宽了超时实际走默认值。本断言锁住透传契约。
    """
    from hub.agent.react.llm import build_chat_model
    model = build_chat_model(
        api_key="test", base_url="https://api.deepseek.com/beta",
        model="deepseek-chat", timeout=30, max_retries=7,
    )
    assert model.deepseek_client.timeout_seconds == 30
    assert model.deepseek_client.max_retries == 7
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_llm.py -v
# Expected: ModuleNotFoundError
```

- [ ] **Step 3: 实现 DeepSeekChatModel wrapper**

注：**不需要**安装 / 依赖 `langchain-openai`。`BaseChatModel` / `ChatGeneration` /
`ChatResult` / `convert_to_openai_function` 全在 `langchain-core`，本 wrapper 直接
继承 `BaseChatModel` 自己出 OpenAI ChatCompletion 报文，避免引入额外依赖。

```python
# backend/hub/agent/react/llm.py
"""DeepSeek 适配为 LangChain BaseChatModel。

包装 hub 现有 DeepSeekLLMClient（已经内置 staging 实战补过的 retry 语义,见
backend/hub/agent/llm_client.py）,让 LangGraph create_react_agent 能直接用。

关键：**不**直接用 langchain-openai.ChatOpenAI,因为 ChatOpenAI 默认不重试 400
（DeepSeek 偶发 schema-jitter 400 是 staging 真踩坑出来的）+ 不写 hub chat_log
（admin 决策链审计依赖)。
"""
from __future__ import annotations
import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import ConfigDict, Field

from hub.agent.llm_client import DeepSeekLLMClient


logger = logging.getLogger(__name__)


def _messages_to_openai_format(messages: list[BaseMessage]) -> list[dict]:
    """LangChain BaseMessage → OpenAI ChatCompletion messages 格式。"""
    out = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            out.append({"role": "system", "content": msg.content})
        elif isinstance(msg, HumanMessage):
            out.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            entry: dict = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": __import__("json").dumps(tc["args"]),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            out.append(entry)
        elif isinstance(msg, ToolMessage):
            out.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id,
                "content": msg.content if isinstance(msg.content, str) else __import__("json").dumps(msg.content),
            })
        else:
            logger.warning("Unknown message type: %s", type(msg).__name__)
    return out


def _tools_to_openai_schemas(tools: list[BaseTool]) -> list[dict]:
    """LangChain BaseTool → OpenAI function calling schema dict。

    LangChain Tool.args_schema 是 Pydantic; LangChain 已有 utility 转 OpenAI schema。
    """
    from langchain_core.utils.function_calling import convert_to_openai_function
    return [
        {"type": "function", "function": convert_to_openai_function(tool)}
        for tool in tools
    ]


class DeepSeekChatModel(BaseChatModel):
    """DeepSeekLLMClient 包装成 LangChain BaseChatModel。

    复用 hub 现有 DeepSeekLLMClient 的:
    - retry 语义（{400, 408, 425, 429, 500-504, TransportError} + 指数退避）
    - chat_log 写入（cache_hit_rate / token usage）
    - 错误分类

    仅实现 ReAct 用得上的 _agenerate + bind_tools 接口。
    """

    # Pydantic v2 配置（langchain-core 0.3.x 已用 Pydantic 2;`class Config` 无效）
    model_config = ConfigDict(arbitrary_types_allowed=True)  # DeepSeekLLMClient 不是 Pydantic

    deepseek_client: DeepSeekLLMClient
    # 可变默认值用 Field(default_factory=...),避免 Pydantic v2 的 mutable default 校验报错
    bound_tools: list[dict] = Field(default_factory=list)  # OpenAI schema dict 列表（bind_tools 后填）
    temperature: float = 0.0
    max_tokens: int = 4096

    @property
    def _llm_type(self) -> str:
        return "deepseek-react"

    def bind_tools(self, tools: list[BaseTool], **kwargs) -> "DeepSeekChatModel":
        """转成 OpenAI schema list,存进新实例的 bound_tools。
        LangGraph create_react_agent 内部调本方法把 ALL_TOOLS bind 上去。
        """
        new = self.__class__(
            deepseek_client=self.deepseek_client,
            bound_tools=_tools_to_openai_schemas(tools),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return new

    async def _agenerate(
        self, messages: list[BaseMessage], stop=None,
        run_manager=None, **kwargs,
    ) -> ChatResult:
        """LangChain async generation 入口 — 转 hub DeepSeekLLMClient.chat()。"""
        oai_messages = _messages_to_openai_format(messages)
        resp = await self.deepseek_client.chat(
            messages=oai_messages,
            tools=self.bound_tools or None,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        # resp.text / resp.tool_calls / resp.finish_reason
        ai_msg_kwargs: dict = {"content": resp.text or ""}
        if resp.tool_calls:
            import json
            ai_msg_kwargs["tool_calls"] = [
                {
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "args": json.loads(tc["function"]["arguments"]),
                }
                for tc in resp.tool_calls
            ]
        ai_msg = AIMessage(**ai_msg_kwargs)
        return ChatResult(generations=[ChatGeneration(message=ai_msg)])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """同步入口 — ReAct 全异步,本方法理论上不会被调,raise NotImplementedError。"""
        raise NotImplementedError(
            "DeepSeekChatModel 仅支持 async（ReAct agent 全 async）。"
            "请用 model.ainvoke() 而非 invoke()。"
        )


def build_chat_model(
    *,
    api_key: str,
    base_url: str,
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    timeout: int = 60,
    max_retries: int = 4,
) -> BaseChatModel:
    """构造 DeepSeekChatModel（包装 hub DeepSeekLLMClient）。

    LangGraph create_react_agent 拿这个 model 后会调 .bind_tools(ALL_TOOLS) +
    .ainvoke() 驱动 ReAct 循环。底层每次 LLM call 都走 DeepSeekLLMClient,
    自动获得 hub 历史踩坑出来的 retry / 错误分类 / chat_log 审计语义。

    timeout / max_retries 必须**透传**到底层 client —— 否则配置看起来生效但实际走
    DeepSeekLLMClient 默认值（很容易让运维误以为已经放宽超时 / 加大重试次数）。
    """
    client = DeepSeekLLMClient(
        api_key=api_key, base_url=base_url, model=model,
        timeout_seconds=timeout, max_retries=max_retries,
    )
    return DeepSeekChatModel(
        deepseek_client=client,
        temperature=temperature,
        max_tokens=max_tokens,
    )
```

- [ ] **Step 4: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_llm.py -v
# Expected: 5 passed
#   test_build_chat_model_returns_langchain_chat
#   test_build_chat_model_supports_tool_calls
#   test_build_chat_model_uses_deepseek_wrapper
#   test_deepseek_chat_model_retries_on_400
#   test_build_chat_model_configures_retry_and_timeout
```

- [ ] **Step 5: Commit**

```bash
git add backend/hub/agent/react/llm.py backend/tests/react/test_llm.py
git commit -m "feat(hub): DeepSeek → LangChain ChatModel 适配"
```

---

# Phase 2: Read Tools（Day 1 下午 - Day 2 上午）

目标：10 个 read tool 全部实现 + 单测。每个 tool **包装现有 erp_tools / analyze_tools 函数**（业务底层 0 改动）+ 1 个新 tool `get_recent_drafts`（专治"复用上份"，仅 contract）。

**关键设计**：所有 read tool（以及 write tool 的 confirm dispatch）都通过 `invoke_business_tool`
helper 调底层函数。helper 自动做 `require_permissions` + `log_tool_call` + 注入
ContextVar（`hub_user_id` / `acting_as_user_id` / `conversation_id`）。这样 ReAct
tool 不会绕过现有权限审计门禁（GraphAgent 时代靠 ToolRegistry.call 自动做这事;
ReAct 不走 ToolRegistry 必须显式补）。

## Task 2.0: invoke_business_tool helper（权限 + 审计 + ctx 注入）

**Files:**
- Create: `backend/hub/agent/react/tools/__init__.py` （空文件,Python 包标记）
- Create: `backend/hub/agent/react/tools/_invoke.py`
- Create: `backend/tests/react/conftest.py` （**整个 react test 套件共享的 fake_ctx fixture**）
- Test: `backend/tests/react/test_invoke.py`

- [ ] **Step 1: 创建 tools 包标记 + 共享 conftest（其它 task 都依赖）**

```bash
touch backend/hub/agent/react/tools/__init__.py
```

```python
# backend/tests/react/conftest.py
"""React 测试共享 fixture。**必须在 Task 2.0 创建** —— Task 2.0 / 2.1 / 2.2 / ...
所有 react 单测都用 fake_ctx,如果只在 Task 2.1 创建,Task 2.0 的 test_invoke.py
跑时会 ERROR fixture 'fake_ctx' not found（不是 FAIL）,后续 task 链式 broken。
"""
import pytest
from hub.agent.react.context import tool_ctx, ToolContext


@pytest.fixture
def fake_ctx():
    """提供一个 set 好的 ToolContext，测试结束自动 reset。

    fake hub_user_id=1, conversation_id="test-conv", acting_as=None。
    测试断言权限调用 / 审计 log / fn kwargs 时按这个值算。
    """
    token = tool_ctx.set(ToolContext(
        hub_user_id=1,
        acting_as=None,
        conversation_id="test-conv",
        channel_userid="ding-test",
    ))
    yield {"hub_user_id": 1, "conversation_id": "test-conv"}
    tool_ctx.reset(token)
```

- [ ] **Step 2: 写失败测试**

```python
# backend/tests/react/test_invoke.py
import pytest
from unittest.mock import AsyncMock, patch
from hub.agent.react.tools._invoke import invoke_business_tool


@pytest.mark.asyncio
async def test_invoke_business_tool_runs_perm_check_and_calls_fn(fake_ctx):
    """invoke_business_tool 必须：(a) require_permissions (b) 调 fn (c) 注入 ctx kwargs。"""
    fake_fn = AsyncMock(return_value={"items": [], "total": 0})

    with patch(
        "hub.agent.react.tools._invoke.require_permissions",
        new=AsyncMock(),
    ) as mock_perm:
        result = await invoke_business_tool(
            tool_name="search_customers",
            perm="usecase.query_customer.use",
            args={"query": "翼蓝"},
            fn=fake_fn,
        )
    assert result == {"items": [], "total": 0}
    mock_perm.assert_awaited_once_with(1, ["usecase.query_customer.use"])
    fake_fn.assert_awaited_once_with(query="翼蓝", acting_as_user_id=1)


@pytest.mark.asyncio
async def test_invoke_business_tool_perm_denied_raises(fake_ctx):
    """权限校验 fail → require_permissions 抛 BizError → invoke 透传抛（fail-closed）。

    注：hub 的 `require_permissions` 抛 `BizError(BizErrorCode.PERM_NO_*)`，**没有**单独的
    PermissionDenied class（hub.permissions:36 实际抛的是 BizError）。
    """
    from hub.error_codes import BizError, BizErrorCode

    fake_fn = AsyncMock()
    with patch(
        "hub.agent.react.tools._invoke.require_permissions",
        new=AsyncMock(side_effect=BizError(BizErrorCode.PERM_NO_PRODUCT_QUERY)),
    ):
        with pytest.raises(BizError):
            await invoke_business_tool(
                tool_name="search_customers", perm="usecase.x.use",
                args={"query": "x"}, fn=fake_fn,
            )
    fake_fn.assert_not_awaited()  # 权限挂掉不应该调 fn


@pytest.mark.asyncio
async def test_invoke_business_tool_writes_audit_log(fake_ctx):
    """log_tool_call context manager 必须包住 fn 调用,异常照常上抛。"""
    fake_fn = AsyncMock(return_value={"x": 1})
    with (
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
        patch("hub.agent.react.tools._invoke.log_tool_call") as mock_log,
    ):
        # 让 log_tool_call 返一个真 async context manager
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def fake_log(**kw):
            ctx = AsyncMock()
            ctx.set_result = lambda r: None
            yield ctx
        mock_log.side_effect = fake_log
        result = await invoke_business_tool(
            tool_name="search_customers", perm="usecase.x.use",
            args={"query": "x"}, fn=fake_fn,
        )
    assert result == {"x": 1}
    mock_log.assert_called_once()
    log_kwargs = mock_log.call_args.kwargs
    assert log_kwargs["tool_name"] == "search_customers"
    assert log_kwargs["hub_user_id"] == 1
    assert log_kwargs["conversation_id"] == "test-conv"
```

- [ ] **Step 3: 实现**

```python
# backend/hub/agent/react/tools/_invoke.py
"""ReAct tool 调用底层业务函数的统一包装。

ReAct @tool 函数都通过本 helper 调 erp_tools / analyze_tools / generate_tools /
draft_tools 真业务函数。统一做：
  1. require_permissions(perm) — 权限 fail-closed（无权限直接抛 `BizError(BizErrorCode.PERM_NO_*)`，dingtalk_inbound 已有 `except BizError → 翻译中文给用户`）
  2. log_tool_call — 写 tool_call_log（admin 决策链审计）
  3. 注入 ctx kwargs — acting_as_user_id 来自 ContextVar tool_ctx

不通过 ToolRegistry.call 是因为：
  - ReAct write tool 的 confirm 流程跟 ToolRegistry 内置的两步协议不兼容
  - ToolRegistry.call 会再做 strict schema 校验,跟 LangChain @tool 自带 schema 重复
"""
from __future__ import annotations
from typing import Any, Awaitable, Callable

from hub.agent.react.context import tool_ctx
from hub.observability.tool_logger import log_tool_call
from hub.permissions import require_permissions


async def invoke_business_tool(
    *,
    tool_name: str,
    perm: str,
    args: dict,
    fn: Callable[..., Awaitable[Any]],
    extra_ctx_kwargs: dict | None = None,
) -> Any:
    """统一调底层业务函数。

    Args:
        tool_name: 写 tool_call_log 用的名字（按底层函数名,如 "search_customers"）。
        perm: 权限 code（如 "usecase.query_customer.use"）。
        args: LLM 传的业务 args（不含 hub_user_id 等 ctx 字段）。
        fn: 底层业务函数（如 erp_tools.search_customers）。函数必须接
            `acting_as_user_id` kwarg；其它 ctx kwargs 通过 extra_ctx_kwargs 加。
        extra_ctx_kwargs: write 类底层需要的额外 ctx（如 hub_user_id /
            conversation_id / confirmation_action_id）。read 类一般为 None。

    Returns:
        fn 的返值。
    """
    c = tool_ctx.get()
    if c is None:
        raise RuntimeError("tool_ctx 未 set — react agent 入口必须先 set 才能调 tool")

    # 1. 权限 fail-closed
    await require_permissions(c["hub_user_id"], [perm])

    # 2. 审计 log + 调 fn
    async with log_tool_call(
        conversation_id=c["conversation_id"],
        hub_user_id=c["hub_user_id"],
        round_idx=0,
        tool_name=tool_name,
        args=args,
    ) as log_ctx:
        kwargs = {
            **args,
            "acting_as_user_id": c.get("acting_as") or c["hub_user_id"],
        }
        if extra_ctx_kwargs:
            kwargs.update(extra_ctx_kwargs)
        result = await fn(**kwargs)
        log_ctx.set_result(result)
        return result
```

- [ ] **Step 4: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_invoke.py -v
# Expected: 3 passed
#   test_invoke_business_tool_runs_perm_check_and_calls_fn
#   test_invoke_business_tool_perm_denied_raises
#   test_invoke_business_tool_writes_audit_log
```

- [ ] **Step 5: Commit**

```bash
git add backend/hub/agent/react/tools/__init__.py backend/hub/agent/react/tools/_invoke.py \
        backend/tests/react/conftest.py backend/tests/react/test_invoke.py
git commit -m "feat(hub): invoke_business_tool helper + 共享 fake_ctx fixture"
```

---

**真实底层签名**（已读 `backend/hub/agent/tools/erp_tools.py` + `analyze_tools.py` 确认）：

| 底层函数 | 签名 | 备注 |
|---|---|---|
| `erp_tools.search_customers(query, *, acting_as_user_id)` | 返 `{items, total}` |  |
| `erp_tools.search_products(query, *, acting_as_user_id)` | 返 `{items, total}` |  |
| `erp_tools.get_product_detail(product_id, *, acting_as_user_id)` | 返 dict 含库存 | 包装 adapter.get_product |
| `erp_tools.check_inventory(product_id, *, acting_as_user_id)` | 返 `{product_id, total_stock, stocks}` | **单产品库存**（不按 brand）|
| `erp_tools.get_customer_history(product_id, customer_id, *, limit=5, acting_as_user_id)` | 返 dict | **product_id 在前** |
| `erp_tools.get_customer_balance(customer_id, *, acting_as_user_id)` | 返 dict |  |
| `erp_tools.search_orders(customer_id=None, since_days=30, *, acting_as_user_id)` | 返 dict |  |
| `erp_tools.get_order_detail(order_id, *, acting_as_user_id)` | 返 dict |  |
| `erp_tools.get_inventory_aging(threshold_days=90, *, acting_as_user_id)` | 返 dict |  |
| `analyze_tools.analyze_top_customers(period="近一月", top_n=10, *, acting_as_user_id)` | 返 dict |  |

`erp_tools` **没有** `get_customer_detail` 函数（只有 adapter.get_customer）。React 不暴露
单独的 `get_customer_detail` tool — 客户档案信息已在 `search_customer` 返值里，
不够再用 `get_customer_balance` 拿余额。

**Permission 映射**（按 `backend/hub/seed.py` 真实 perm code 同步,**已读 seed.py 确认**）：

| React tool | 底层 fn | perm |
|---|---|---|
| search_customer | erp_tools.search_customers | `usecase.query_customer.use` |
| search_product | erp_tools.search_products | `usecase.query_product.use` |
| get_product_detail | erp_tools.get_product_detail | `usecase.query_product.use` |
| check_inventory | erp_tools.check_inventory | `usecase.query_inventory.use` |
| get_customer_history | erp_tools.get_customer_history | `usecase.query_customer_history.use` |
| get_customer_balance | erp_tools.get_customer_balance | `usecase.query_customer_balance.use` |
| search_orders | erp_tools.search_orders | `usecase.query_orders.use` |
| get_order_detail | erp_tools.get_order_detail | `usecase.query_orders.use` |
| analyze_top_customers | analyze_tools.analyze_top_customers | `usecase.analyze.use` （**注意**: seed 只有 `analyze.use` 不是 `analyze_top_customers.use`） |
| get_recent_drafts | (本 task 自实现,仅 contract) | `usecase.query_recent_drafts.use` （**新加**,Task 2.4 同时给 seed.py / migration 加） |

**写 tool perm**（同样按真实 seed code）：

| React tool | 底层 fn | perm |
|---|---|---|
| create_contract_draft | generate_tools.generate_contract_draft | `usecase.generate_contract.use` |
| create_quote_draft | generate_tools.generate_price_quote | `usecase.generate_quote.use` |
| create_voucher_draft | draft_tools.create_voucher_draft | `usecase.create_voucher.use` （**不是** `create_voucher_draft.use`） |
| request_price_adjustment | draft_tools.create_price_adjustment_request | `usecase.adjust_price.use` （**不是** `create_price_adjustment.use`） |
| request_stock_adjustment | draft_tools.create_stock_adjustment_request | `usecase.adjust_stock.use` （**不是** `create_stock_adjustment.use`） |

## Task 2.1: read tools 第一批（search_customer / search_product）

**Files:**
- Modify: `backend/hub/agent/react/tools/__init__.py` （Task 2.0 已建空文件,本 task 加 ALL_TOOLS）
- Create: `backend/hub/agent/react/tools/read.py`
- Test: `backend/tests/react/test_tools_read.py`

注：`backend/tests/react/conftest.py` + `fake_ctx` fixture **已在 Task 2.0 Step 1 创建**,
本 task 直接用，**不要重复创建**。read tools 自己 monkeypatch `erp_tools.*` 真实函数
（如 `monkeypatch.setattr("hub.agent.react.tools.read.erp_tools.search_customers", fake_fn)`）—
跟 read.py 实际 import 路径一致,patch 生效。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/react/test_tools_read.py
import pytest
from unittest.mock import AsyncMock, patch
from hub.agent.react.tools.read import search_customer, search_product


@pytest.mark.asyncio
async def test_search_customer_calls_erp_tools_via_invoke(fake_ctx):
    """search_customer 必须通过 invoke_business_tool 调 erp_tools.search_customers
    （不是直接 adapter）— 拿到权限校验 + 审计 log 自动两件套。"""
    fake_search = AsyncMock(return_value={
        "items": [{"id": 7, "name": "翼蓝", "phone": "138..."}], "total": 1,
    })
    with (
        patch("hub.agent.react.tools.read.erp_tools.search_customers", new=fake_search),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()) as perm,
    ):
        result = await search_customer.ainvoke({"query": "翼蓝"})
    assert result == [{"id": 7, "name": "翼蓝", "phone": "138..."}]
    # 权限被校验
    perm.assert_awaited_once_with(1, ["usecase.query_customer.use"])
    # 底层函数收到正确 kwargs
    fake_search.assert_awaited_once_with(query="翼蓝", acting_as_user_id=1)


@pytest.mark.asyncio
async def test_search_product_unwraps_items(fake_ctx):
    """ERP 返 {items, total} dict → tool 解包成 list 给 LLM。"""
    fake_search = AsyncMock(return_value={
        "items": [{"id": 1, "name": "X1", "sku": "MAT01"}], "total": 1,
    })
    with (
        patch("hub.agent.react.tools.read.erp_tools.search_products", new=fake_search),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await search_product.ainvoke({"query": "X1"})
    assert isinstance(result, list)
    assert result[0]["id"] == 1
```

- [ ] **Step 3: 跑测试确认 fail**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_tools_read.py -v
# Expected: ImportError on hub.agent.react.tools.read
```

- [ ] **Step 4: 实现**

```python
# backend/hub/agent/react/tools/read.py
"""Read tools — 包装现有 erp_tools / analyze_tools 函数,让 LLM 调。

所有 tool 通过 invoke_business_tool helper 调底层（自动 require_permissions +
log_tool_call + 注入 acting_as_user_id）。
"""
from __future__ import annotations
from langchain_core.tools import tool

from hub.agent.tools import erp_tools, analyze_tools
from hub.agent.react.tools._invoke import invoke_business_tool


@tool
async def search_customer(query: str) -> list[dict]:
    """按名称/电话搜客户。返回客户列表 [{id, name, phone, address, ...}]。
    用户提到客户时（"翼蓝" / "广州得帆" / "13800..."）调本 tool 搜。
    """
    result = await invoke_business_tool(
        tool_name="search_customers",
        perm="usecase.query_customer.use",
        args={"query": query},
        fn=erp_tools.search_customers,
    )
    if isinstance(result, dict):
        return result.get("items", [])
    return result or []


@tool
async def search_product(query: str) -> list[dict]:
    """按名称/SKU/品牌搜商品。返回 [{id, name, sku, brand, list_price, ...}]。
    用户提到商品时（"X1" / "F1 系列" / "MAT0130104"）调本 tool 搜。
    """
    result = await invoke_business_tool(
        tool_name="search_products",
        perm="usecase.query_product.use",
        args={"query": query},
        fn=erp_tools.search_products,
    )
    if isinstance(result, dict):
        return result.get("items", [])
    return result or []
```

```python
# backend/hub/agent/react/tools/__init__.py
"""所有 ReAct agent tool 集中导出。"""
from hub.agent.react.tools.read import (
    search_customer, search_product,
)

ALL_TOOLS = [
    search_customer, search_product,
    # 后续 task 追加
]
```

- [ ] **Step 5: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_tools_read.py -v
# Expected: 2 passed
```

- [ ] **Step 6: Commit**

```bash
git add backend/hub/agent/react/tools/__init__.py backend/hub/agent/react/tools/read.py \
        backend/tests/react/conftest.py backend/tests/react/test_tools_read.py
git commit -m "feat(hub): read tools 第 1 批 — search_customer / search_product"
```

---

## Task 2.2: read tools 第二批（get_product_detail / check_inventory / get_customer_history）

**Files:**
- Modify: `backend/hub/agent/react/tools/read.py`
- Modify: `backend/hub/agent/react/tools/__init__.py`
- Modify: `backend/tests/react/test_tools_read.py`

注意 **真实底层签名**：
- `check_inventory(product_id, *, acting_as_user_id)` — **单产品库存**（不是按 brand 列表）。需要按品牌看库存的，让 LLM 先 search_product(brand) 再逐个 check_inventory。
- `get_customer_history(product_id, customer_id, *, limit=5, acting_as_user_id)` — **product_id 在前**。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/react/test_tools_read.py 追加
@pytest.mark.asyncio
async def test_get_product_detail(fake_ctx):
    from hub.agent.react.tools.read import get_product_detail
    fake_fn = AsyncMock(return_value={
        "id": 1, "name": "X1", "total_stock": 100, "stocks": [{"warehouse": "总仓", "qty": 100}],
    })
    with (
        patch("hub.agent.react.tools.read.erp_tools.get_product_detail", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await get_product_detail.ainvoke({"product_id": 1})
    assert result["stocks"][0]["qty"] == 100
    fake_fn.assert_awaited_once_with(product_id=1, acting_as_user_id=1)


@pytest.mark.asyncio
async def test_check_inventory_single_product(fake_ctx):
    """check_inventory 是单产品库存（不是按 brand 列表）。"""
    from hub.agent.react.tools.read import check_inventory
    fake_fn = AsyncMock(return_value={
        "product_id": 1, "total_stock": 100, "stocks": [{"warehouse": "总仓", "qty": 100}],
    })
    with (
        patch("hub.agent.react.tools.read.erp_tools.check_inventory", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await check_inventory.ainvoke({"product_id": 1})
    assert result["total_stock"] == 100
    fake_fn.assert_awaited_once_with(product_id=1, acting_as_user_id=1)


@pytest.mark.asyncio
async def test_get_customer_history(fake_ctx):
    """get_customer_history 参数顺序 product_id 在前。"""
    from hub.agent.react.tools.read import get_customer_history
    fake_fn = AsyncMock(return_value={
        "items": [{"order_id": 100, "qty": 10, "price": 300, "date": "2026-04-01"}],
    })
    with (
        patch("hub.agent.react.tools.read.erp_tools.get_customer_history", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await get_customer_history.ainvoke(
            {"product_id": 1, "customer_id": 7, "limit": 5},
        )
    fake_fn.assert_awaited_once_with(
        product_id=1, customer_id=7, limit=5, acting_as_user_id=1,
    )
    assert result["items"][0]["price"] == 300
```

- [ ] **Step 2: 实现**

```python
# backend/hub/agent/react/tools/read.py 追加
@tool
async def get_product_detail(product_id: int) -> dict:
    """获取商品详情（含各仓库存明细 + 库龄）。需要展示完整商品规格时调。"""
    return await invoke_business_tool(
        tool_name="get_product_detail",
        perm="usecase.query_product.use",
        args={"product_id": product_id},
        fn=erp_tools.get_product_detail,
    )


@tool
async def check_inventory(product_id: int) -> dict:
    """单个商品库存查询(返 {product_id, total_stock, stocks: [...]})。
    需要看某品牌全库存的,先用 search_product(brand) 拿 product_id 列表,再逐个 check_inventory。
    """
    return await invoke_business_tool(
        tool_name="check_inventory",
        perm="usecase.query_inventory.use",
        args={"product_id": product_id},
        fn=erp_tools.check_inventory,
    )


@tool
async def get_customer_history(
    product_id: int, customer_id: int, limit: int = 5,
) -> dict:
    """客户最近 N 笔某商品成交（含数量 / 价格 / 日期,用于报价 / 谈判参考）。
    用户问"上次买这个什么价" / "翼蓝最近 X1 成交怎样" 等历史成交问题时调。
    """
    return await invoke_business_tool(
        tool_name="get_customer_history",
        perm="usecase.query_customer_history.use",
        args={"product_id": product_id, "customer_id": customer_id, "limit": limit},
        fn=erp_tools.get_customer_history,
    )
```

```python
# backend/hub/agent/react/tools/__init__.py 同步更新
from hub.agent.react.tools.read import (
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
)

ALL_TOOLS = [
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
]
```

- [ ] **Step 3: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_tools_read.py -v
# Expected: 5 passed
```

- [ ] **Step 4: Commit**

```bash
git add backend/hub/agent/react/tools/read.py backend/hub/agent/react/tools/__init__.py \
        backend/tests/react/test_tools_read.py
git commit -m "feat(hub): read tools 第 2 批 — product_detail / check_inventory / customer_history"
```

---

## Task 2.3: read tools 第三批（get_customer_balance / search_orders / get_order_detail / analyze_top_customers）

**Files:**
- Modify: `backend/hub/agent/react/tools/read.py`
- Modify: `backend/hub/agent/react/tools/__init__.py`
- Modify: `backend/tests/react/test_tools_read.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/react/test_tools_read.py 追加
@pytest.mark.asyncio
async def test_get_customer_balance(fake_ctx):
    from hub.agent.react.tools.read import get_customer_balance
    fake_fn = AsyncMock(return_value={"balance": 1000.50, "credit_limit": 50000})
    with (
        patch("hub.agent.react.tools.read.erp_tools.get_customer_balance", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await get_customer_balance.ainvoke({"customer_id": 7})
    assert result["balance"] == 1000.50


@pytest.mark.asyncio
async def test_search_orders(fake_ctx):
    from hub.agent.react.tools.read import search_orders
    fake_fn = AsyncMock(return_value={"items": [{"id": 100}], "total": 1})
    with (
        patch("hub.agent.react.tools.read.erp_tools.search_orders", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await search_orders.ainvoke({"customer_id": 7, "since_days": 30})
    fake_fn.assert_awaited_once_with(customer_id=7, since_days=30, acting_as_user_id=1)


@pytest.mark.asyncio
async def test_get_order_detail(fake_ctx):
    from hub.agent.react.tools.read import get_order_detail
    fake_fn = AsyncMock(return_value={"id": 100, "customer_id": 7, "items": []})
    with (
        patch("hub.agent.react.tools.read.erp_tools.get_order_detail", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await get_order_detail.ainvoke({"order_id": 100})
    assert result["id"] == 100


@pytest.mark.asyncio
async def test_analyze_top_customers(fake_ctx):
    from hub.agent.react.tools.read import analyze_top_customers
    fake_fn = AsyncMock(return_value={
        "items": [{"customer_id": 7, "total": 50000}],
        "data_window": "近一月,3 单",
    })
    with (
        patch("hub.agent.react.tools.read.analyze_tools.analyze_top_customers", new=fake_fn),
        patch("hub.agent.react.tools._invoke.require_permissions", new=AsyncMock()),
    ):
        result = await analyze_top_customers.ainvoke({"period": "近一月", "top_n": 10})
    fake_fn.assert_awaited_once_with(period="近一月", top_n=10, acting_as_user_id=1)
    assert result["items"][0]["customer_id"] == 7
```

- [ ] **Step 2: 实现**

```python
# backend/hub/agent/react/tools/read.py 追加
@tool
async def get_customer_balance(customer_id: int) -> dict:
    """客户欠款 / 余额 / 信用额度 / rebate 余额。
    用户问"还欠多少" / "信用够吗" / "余额怎样"时调。
    """
    return await invoke_business_tool(
        tool_name="get_customer_balance",
        perm="usecase.query_customer_balance.use",
        args={"customer_id": customer_id},
        fn=erp_tools.get_customer_balance,
    )


@tool
async def search_orders(customer_id: int = 0, since_days: int = 30) -> dict:
    """搜订单（按客户 + 最近 N 天）。customer_id=0 表示不过滤,看全部用户的订单。
    用户问"最近订单怎样" / "翼蓝最近买啥"时调。
    """
    return await invoke_business_tool(
        tool_name="search_orders",
        perm="usecase.query_orders.use",
        args={"customer_id": customer_id, "since_days": since_days},
        fn=erp_tools.search_orders,
    )


@tool
async def get_order_detail(order_id: int) -> dict:
    """订单详情（含每行商品 / 数量 / 价格）。"""
    return await invoke_business_tool(
        tool_name="get_order_detail",
        perm="usecase.query_orders.use",
        args={"order_id": order_id},
        fn=erp_tools.get_order_detail,
    )


@tool
async def analyze_top_customers(period: str = "近一月", top_n: int = 10) -> dict:
    """近 N 天客户销售排行。period 取 "近一周" / "近一月" / "近一季" / "近一年" 等中文表达。
    用户问"哪些大客户" / "本月销售排行"调。返 {items: [...], data_window: ...}。
    """
    return await invoke_business_tool(
        tool_name="analyze_top_customers",
        perm="usecase.analyze.use",
        args={"period": period, "top_n": top_n},
        fn=analyze_tools.analyze_top_customers,
    )
```

```python
# backend/hub/agent/react/tools/read.py 顶部 import 同步加：
from hub.agent.tools import erp_tools, analyze_tools
```

```python
# backend/hub/agent/react/tools/__init__.py 更新
from hub.agent.react.tools.read import (
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
    get_customer_balance, search_orders, get_order_detail, analyze_top_customers,
)

ALL_TOOLS = [
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
    get_customer_balance, search_orders, get_order_detail, analyze_top_customers,
]  # 9 read tools so far,Task 2.4 加 get_recent_drafts 凑 10
```

- [ ] **Step 3: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_tools_read.py -v
# Expected: 9 passed
```

- [ ] **Step 4: Commit**

```bash
git add backend/hub/agent/react/tools/read.py backend/hub/agent/react/tools/__init__.py \
        backend/tests/react/test_tools_read.py
git commit -m "feat(hub): read tools 第 3 批 — balance / orders / order_detail / top_customers"
```

---

## Task 2.4: get_recent_drafts（解决"复用上份"的关键 tool，仅 contract）

**Files:**
- Modify: `backend/hub/agent/react/tools/read.py`
- Modify: `backend/hub/agent/react/tools/__init__.py`
- Modify: `backend/tests/react/test_tools_read.py`

这是新写的 tool（非现有 erp_tools 包装），查 hub 自身 `ContractDraft` 数据库。

**范围收窄到 contract-only**（YAGNI）：
- spec §5.1 之前列了 contract / quote / voucher 三类
- 实际 hub 数据模型：`ContractDraft` 表只存 contract（template_type=sales）；
  报价单也存在同表但 template_type=quote；voucher / 调价 / 调库存有各自独立的 draft 表
- 真正用户痛点是"上次合同同样给 X" — 报价单复用 / 凭证复用 是非高频场景（YAGNI）
- 实现先收窄到 contract,未来真有需求再加 quote/voucher 类型

- [ ] **Step 1: 写失败测试**

注意 Tortoise ORM `.filter().order_by(...).limit(...)` 是 QuerySet 链式构造，
最后 `await qs` 才返 list。直接 mock `.limit` 返 AsyncMock(return_value=[...])
不对。最简单的办法：把 DB 查询抽成 `_query_recent_contract_drafts(...)` helper,
测试 mock helper 而不是 ORM。

```python
# backend/tests/react/test_tools_read.py 追加
@pytest.mark.asyncio
async def test_get_recent_drafts(fake_ctx, monkeypatch):
    """contract 类型,调 _query_recent_contract_drafts helper 拿草稿,反向填到 result。"""
    from hub.agent.react.tools.read import get_recent_drafts
    from unittest.mock import AsyncMock

    fake_drafts = [{
        "id": 20,
        "customer_id": 7,
        "requester_hub_user_id": 1,  # helper 的 ContractDraft.filter() 已按 user 隔离;
                                       # fixture 也带上让断言能验"返的是当前 user 的 draft"
        "conversation_id": "test-conv",
        "items": [{"product_id": 1, "qty": 10, "price": 300}],
        "extras": {
            "shipping_address": "北京海淀", "shipping_contact": "张三",
            "shipping_phone": "138...",
            "payment_terms": "30 天", "tax_rate": "13%",
        },
        "status": "sent",
        "created_at": "2026-05-03T10:00:00",
    }]
    # mock helper（避开 Tortoise ORM 链式 mock 复杂度）
    async def _fake_query(conv_id, hub_user_id, limit):
        # 防御性断言：实施者不能误删 helper 的 conversation_id / hub_user_id 过滤
        assert conv_id == "test-conv"
        assert hub_user_id == 1
        return fake_drafts
    monkeypatch.setattr(
        "hub.agent.react.tools.read._query_recent_contract_drafts", _fake_query,
    )
    monkeypatch.setattr(
        "hub.agent.react.tools.read._get_erp_customer_name",
        AsyncMock(return_value="翼蓝"),
    )
    # mock 权限校验 — 关键：实现里 read.py 模块级 `from hub.permissions import require_permissions`,
    # patch 目标必须是 read 模块上的引用,**不是** _invoke（get_recent_drafts 不走 invoke_business_tool,
    # 自己直接调 require_permissions + log_tool_call,因为它查 hub 本地表不是 ERP）。
    monkeypatch.setattr(
        "hub.agent.react.tools.read.require_permissions", AsyncMock(),
    )
    # mock 审计 log（隔离 DB 写入,跟 invoke_business_tool 对齐）
    from contextlib import asynccontextmanager

    class _FakeLogCtx:
        def set_result(self, _r): ...

    @asynccontextmanager
    async def _fake_log_tool_call(**kwargs):
        yield _FakeLogCtx()

    monkeypatch.setattr(
        "hub.agent.react.tools.read.log_tool_call", _fake_log_tool_call,
    )

    result = await get_recent_drafts.ainvoke({"limit": 5})

    assert len(result) == 1
    assert result[0]["draft_id"] == 20
    assert result[0]["customer_name"] == "翼蓝"
    assert result[0]["items"][0]["product_id"] == 1
    assert result[0]["shipping"]["address"] == "北京海淀"
    assert result[0]["payment_terms"] == "30 天"
```

- [ ] **Step 2: 实现**

```python
# backend/hub/agent/react/tools/read.py 追加
# 模块级 import — 让测试 monkeypatch 能命中 read.* 路径（见 test 里 patch 路径解释）。
from hub.agent.react.context import tool_ctx
from hub.agent.tools.erp_tools import current_erp_adapter
from hub.models.contract import ContractDraft
from hub.permissions import require_permissions
from hub.observability.tool_logger import log_tool_call


async def _query_recent_contract_drafts(
    conversation_id: str, hub_user_id: int, limit: int,
) -> list[dict]:
    """查 ContractDraft sent 的最近 limit 条 — 抽成 helper 便于 mock。

    返 list[dict]（每个 dict 是 draft 字段的浅拷贝）,而非 Tortoise Model 实例,
    让上层不依赖 ORM 接口。
    """
    drafts = await (
        ContractDraft.filter(
            conversation_id=conversation_id,
            requester_hub_user_id=hub_user_id,
            status="sent",  # 只列已发出的（未确认的不算"上次合同"）
        )
        .order_by("-created_at")
        .limit(limit)
    )
    return [
        {
            "id": d.id,
            "customer_id": d.customer_id,
            "items": d.items or [],
            "extras": d.extras or {},
            "status": d.status,
            "created_at": str(d.created_at),
        }
        for d in drafts
    ]


async def _get_erp_customer_name(customer_id: int, acting_as_user_id: int) -> str:
    """从 ERP 拿客户 name（drafts 表只存 id）。"""
    adapter = current_erp_adapter()
    try:
        detail = await adapter.get_customer(
            customer_id=customer_id, acting_as_user_id=acting_as_user_id,
        )
        return detail.get("name", f"<id={customer_id}>")
    except Exception:
        return f"<id={customer_id}>"


@tool
async def get_recent_drafts(limit: int = 5) -> list[dict]:
    """**关键 tool：当前会话最近的合同草稿(contract only),让 LLM 处理"同样/上次/复用"等表达。**

    返回最近 limit 条 contract 草稿（按 created_at desc 排序),每条含 customer_name /
    items / shipping / payment_terms / tax_rate / created_at。

    使用时机：用户消息提到"和上份一样" / "前面那份给 X 也来一份" / "复制上次合同"
    等表达,先调本 tool 拿 items 和 shipping,再调 search_customer 找新客户,然后
    调 create_contract_draft 提交。

    返 [] 表示当前会话没有合同历史 — LLM 应回复用户"没找到上次记录,请明确说明
    客户/商品/数量/价格"。

    范围限定为 contract（YAGNI）。报价单 / 凭证 / 调价 / 调库存 的"复用"暂不支持。
    """
    c = tool_ctx.get()
    if c is None:
        raise RuntimeError("tool_ctx 未 set")

    # 1. 权限 fail-closed
    await require_permissions(c["hub_user_id"], ["usecase.query_recent_drafts.use"])

    # 2. log_tool_call context manager 包裹（统一审计,跟 invoke_business_tool 对齐）
    async with log_tool_call(
        conversation_id=c["conversation_id"],
        hub_user_id=c["hub_user_id"],
        round_idx=0,
        tool_name="get_recent_drafts",
        args={"limit": limit},
    ) as log_ctx:
        raw = await _query_recent_contract_drafts(
            conversation_id=c["conversation_id"],
            hub_user_id=c["hub_user_id"],
            limit=limit,
        )

        acting_as = c.get("acting_as") or c["hub_user_id"]
        out: list[dict] = []
        for d in raw:
            cust_name = await _get_erp_customer_name(d["customer_id"], acting_as)
            ext = d.get("extras") or {}
            out.append({
                "draft_id": d["id"],
                "customer_id": d["customer_id"],
                "customer_name": cust_name,
                "items": d.get("items") or [],
                "shipping": {
                    "address": ext.get("shipping_address") or "",
                    "contact": ext.get("shipping_contact") or "",
                    "phone": ext.get("shipping_phone") or "",
                },
                "payment_terms": ext.get("payment_terms") or "",
                "tax_rate": ext.get("tax_rate") or "",
                "created_at": d.get("created_at") or "",
            })
        log_ctx.set_result(out)
        return out
```

**注**：`get_recent_drafts` 不调 ERP（查 hub 自己的 ContractDraft 表）。但**仍要走
统一审计** — 用 `log_tool_call` context manager 包裹 _query_recent_contract_drafts
调用（跟 invoke_business_tool 内部审计语义对齐）。

`usecase.query_recent_drafts.use` 是新加的 perm。hub 启动会调 `hub.seed.run_seed()`
（`backend/main.py:44`）做幂等 seed,因此**只改 seed.py 就够,不用单独写 SQL migration**。

具体步骤：

1. **编辑 `backend/hub/seed.py`** —— 找到 `PERMISSIONS.extend([...])` 区域（约 line 56-87,
   按 commit 时实际 grep 定位）,在最后追加 6-tuple `(code, domain, resource, action, label, description)`：
   ```python
   PERMISSIONS.extend([
       # ...（已有的 13 条）
       ("usecase.query_recent_drafts.use", "usecase", "query_recent_drafts", "use",
        "复用上份合同查询",
        "允许 ReAct agent 查最近合同草稿(用于'同样给 X 也来一份'类自然表达)"),
   ])
   ```

2. **绑定到 bot_user_basic / bot_user_sales 角色** —— 找到 `ROLES["bot_user_basic"]["permissions"].extend(...)`（约 line 145）和 `ROLES["bot_user_sales"]["permissions"].extend(...)`（约 line 153）,各自加新 code:
   ```python
   ROLES["bot_user_basic"]["permissions"].extend([
       # ... 已有
       "usecase.query_recent_drafts.use",
   ])
   ROLES["bot_user_sales"]["permissions"].extend([
       # ... 已有
       "usecase.query_recent_drafts.use",
   ])
   ```
   （`platform_admin` 通常用 `["*"]` 全权限,无需手动加。）

3. **本地验证 seed 写入**：
   ```bash
   cd /Users/lin/Desktop/hub/.worktrees/plan6-agent
   COMPOSE_PROJECT_NAME=hub docker compose up -d hub-app
   docker exec -it hub-hub-app-1 python -c "from hub.seed import run_seed; import asyncio; asyncio.run(run_seed())"
   docker exec -it hub-hub-postgres-1 psql -U hub -d hub -c \
     "SELECT code FROM hub_permissions WHERE code='usecase.query_recent_drafts.use';"
   # Expected: 1 row
   ```

4. **不需要 aerich migration** —— ContractDraft 表 schema 没变,只新增了一条种子数据。
   `aerich migrate` 只在 model 字段/类型变更时才需要。本 task 跑过 step 3 后,pytest
   测试本身用 mock 跳过 DB,启动后 admin UI 能在「角色 / 权限」看到这条新 perm。

```python
# backend/hub/agent/react/tools/__init__.py 更新
from hub.agent.react.tools.read import (
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
    get_customer_balance, search_orders, get_order_detail, analyze_top_customers,
    get_recent_drafts,
)

ALL_TOOLS = [
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
    get_customer_balance, search_orders, get_order_detail, analyze_top_customers,
    get_recent_drafts,
]
```

- [ ] **Step 3: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_tools_read.py -v
# Expected: 10 passed
#   Task 2.1-2.3 累计 9 个 + 本 task 新加 1 个：test_get_recent_drafts
```

- [ ] **Step 4: Commit**

```bash
git add backend/hub/agent/react/tools/read.py backend/hub/agent/react/tools/__init__.py \
        backend/tests/react/test_tools_read.py
git commit -m "feat(hub): get_recent_drafts — 解决'复用上份'的关键 tool"
```

---

# Phase 3: Write Tools + ConfirmGate Wrapper（Day 2 下午 - Day 3 上午）

目标：5 个 write tool（plan-then-execute 模式）+ confirm_action + WRITE_TOOL_DISPATCH。每个 write tool 有两态：
1. **plan 阶段**（LLM 调写 tool）：写 ConfirmGate pending → 返 `{status: "pending_confirmation", action_id, preview}`
2. **execute 阶段**（LLM 调 confirm_action(action_id)）：list_pending_for_context → claim() 原子消费 → dispatch payload 到真正业务函数

## Task 3.1: ConfirmGate 集成基础（write tool 共用 helper）

**Files:**
- Create: `backend/hub/agent/react/tools/_confirm_helper.py`
- Test: `backend/tests/react/test_confirm_wrapper.py`

**真实 ConfirmGate API**（已读 `backend/hub/agent/tools/confirm_gate.py` 确认）：

- `create_pending(*, hub_user_id, conversation_id, subgraph, summary, payload, ...)`
  → 返 `PendingAction` dataclass（含 `.action_id`, `.token`, `.payload`, `.subgraph`,
  `.summary`, `.created_at`, `.ttl_seconds`）。**subgraph + summary 必填**。
- `list_pending_for_context(*, conversation_id, hub_user_id) -> list[PendingAction]`
- `claim(*, action_id, token, hub_user_id, conversation_id) -> bool` — 原子 HDEL 消费,
  跨 context / token 不匹配 / 过期 → raise `CrossContextClaim`。
- ⚠️ **不要用** `claim_action` / `restore_action`,那是旧 ChainAgent 两步协议（confirm_node + commit_node）。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/react/test_confirm_wrapper.py
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
```

- [ ] **Step 2: 实现**

```python
# backend/hub/agent/react/tools/_confirm_helper.py
"""写 tool 共用 ConfirmGate helper（PendingAction API,v9 路径）。

write tool plan 阶段流程:
1. 业务参数校验
2. 构造 canonical payload {tool_name, args}
3. 调 gate.create_pending(subgraph, summary, payload) → PendingAction
4. 返 {status: "pending_confirmation", action_id, preview}（**不返 token**,token 在
   confirm_action 内部从 list_pending_for_context 反查）

execute 阶段（confirm_action tool 内调用）见 confirm.py：
1. list_pending_for_context 找当前 (conv, user) 的 PendingAction
2. 用 PendingAction.token 调 gate.claim() 原子消费
3. dispatch 到 WRITE_TOOL_DISPATCH 真正业务函数

⚠️ 不用旧 claim_action / restore_action / mark_confirmed —— 那是 ChainAgent 两步协议。
"""
from __future__ import annotations
from typing import Any

from hub.agent.react.context import tool_ctx
from hub.agent.tools.confirm_gate import ConfirmGate, PendingAction


_CONFIRM_GATE: ConfirmGate | None = None


def set_confirm_gate(gate: ConfirmGate | None) -> None:
    """worker.py 启动时调,注入 ConfirmGate 单例。测试也用它注入 fake gate / 真 fakeredis gate。"""
    global _CONFIRM_GATE
    _CONFIRM_GATE = gate


def _gate() -> ConfirmGate:
    if _CONFIRM_GATE is None:
        raise RuntimeError(
            "ConfirmGate 未注入 — 应在 worker startup 调 set_confirm_gate(gate)"
        )
    return _CONFIRM_GATE


def _canonical_idempotency_key(payload: dict) -> str:
    """对同一 (conv, user) 重复发同样 args 的写请求,生成稳定 idempotency_key。

    payload = {"tool_name": str, "args": dict}。args 内部 dict / list 用 sort_keys
    canonicalize,所以 LLM 哪怕字段顺序换、空格变化也能命中复用。

    ConfirmGate.create_pending 内部把 idempotency_key 跟 (conv, user) 一起
    HSET → 命中复用,跨 context 命中 fail-closed (CrossContextIdempotency)。

    场景：用户连续两次发"做凭证..." → React tool 第二次 create_pending 时
    idempotency_key 命中第一次 → 复用同一 PendingAction → 同一 action_id →
    confirm_action 一次成功后第二次 claim 拒（pending HDEL）。
    """
    import hashlib
    import json
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, default=str,
    )
    return f"react-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]}"


async def create_pending_action(
    *, subgraph: str, summary: str, payload: dict,
    use_idempotency: bool = False,
) -> PendingAction:
    """写 ConfirmGate pending,返 PendingAction（含 action_id + token）。

    Args:
        subgraph: 业务分类（"contract" / "quote" / "voucher" /
            "adjust_price" / "adjust_stock"）— ConfirmGate.create_pending 必填字段。
        summary: 给用户看的预览文案。
        payload: {"tool_name": str, "args": dict} canonical 格式。confirm_action
            从 PendingAction.payload 解出来 dispatch。
        use_idempotency: 是否给 ConfirmGate 传 canonical idempotency_key。
            voucher / price / stock 三个写 tool **必须传 True**（底层 DB 唯一约束
            靠 confirmation_action_id,但同一用户同一 args 发 N 次会创 N 个不同
            action_id,导致约束挡不住）。contract / quote 已有 fingerprint 兜底
            可不传（True 也无妨,只是多一层防护）。
    """
    c = tool_ctx.get()
    if c is None:
        raise RuntimeError("tool_ctx 未 set — react agent 入口必须先 set 才能调 tool")
    gate = _gate()
    kwargs = dict(
        hub_user_id=c["hub_user_id"],
        conversation_id=c["conversation_id"],
        subgraph=subgraph,
        summary=summary,
        payload=payload,
    )
    if use_idempotency:
        kwargs["idempotency_key"] = _canonical_idempotency_key(payload)
    return await gate.create_pending(**kwargs)
```

- [ ] **Step 3: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_confirm_wrapper.py -v
# Expected: 2 passed
```

- [ ] **Step 4: Commit**

```bash
git add backend/hub/agent/react/tools/_confirm_helper.py backend/tests/react/test_confirm_wrapper.py
git commit -m "feat(hub): ConfirmGate helper（用 PendingAction API,v9 路径）"
```

---

**真实底层签名**（已读 `generate_tools.py` / `draft_tools.py` 确认）：

| 底层 fn | 必填 args | 必填 kwargs |
|---|---|---|
| `generate_contract_draft` | `template_id, customer_id, items` | `hub_user_id, conversation_id, acting_as_user_id` |
| `generate_price_quote` | `customer_id, items` | `hub_user_id, conversation_id, acting_as_user_id`（**没 shipping_*；放 extras**） |
| `create_voucher_draft` | `voucher_data: dict` | `hub_user_id, conversation_id, acting_as_user_id, confirmation_action_id`（**dict 形态,不是 order_id**） |
| `create_price_adjustment_request` | `customer_id, product_id, new_price` | + `confirmation_action_id` |
| `create_stock_adjustment_request` | `product_id, adjustment_qty: float` | + `confirmation_action_id`（**adjustment_qty 不是 delta_qty**） |

**confirmation_action_id**：voucher / price / stock 三个写 tool 底层必填,作为
DB 幂等 key。**plan 阶段还没生成 action_id**（create_pending 之后才有）—
所以 plan payload 里**不含** confirmation_action_id；confirm_action dispatch 时把
当前 action_id 作为 confirmation_action_id 传给底层。

**template_id**：`generate_contract_draft` 必填。用户不该感知,React tool plan 阶段
内部查 hub.contract_template 表拿默认 sales 模板的 id（参考 commit `8a8643e` 已有的
`_resolve_default_template_id` 实现）。

## Task 3.2: write tool 第一个 — create_contract_draft（plan 阶段）

**Files:**
- Create: `backend/hub/agent/react/tools/write.py`
- Modify: `backend/hub/agent/react/tools/__init__.py`
- Test: `backend/tests/react/test_tools_write.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/react/test_tools_write.py
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
            "tool_name": "generate_contract_draft",  # ← 注意：payload tool_name = 底层函数名
            "args": {
                "template_id": 1,           # ← 内部解析,LLM 看不到
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

    # mock _resolve_default_template_id 返 1
    async def _fake_resolve():
        return 1
    monkeypatch.setattr(
        "hub.agent.react.tools.write._resolve_default_template_id", _fake_resolve,
    )
    # mock 权限校验
    monkeypatch.setattr(
        "hub.agent.react.tools.write.require_permissions", AsyncMock(),
    )
    # mock 底层 generate（plan 阶段不该调）
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
    underlying.assert_not_awaited()  # plan 阶段不真执行
    gate.create_pending.assert_awaited_once()

    # 校验 ConfirmGate 收到的 kwargs（按真实 create_pending 签名）
    call_kwargs = gate.create_pending.call_args.kwargs
    assert call_kwargs["subgraph"] == "contract"
    payload = call_kwargs["payload"]
    # payload tool_name 是**底层函数名**（dispatch 时按它查 WRITE_TOOL_DISPATCH）
    assert payload["tool_name"] == "generate_contract_draft"
    # payload args 字段名严格按底层签名（template_id 内部填,customer_id/items/shipping_*/...）
    assert payload["args"]["template_id"] == 1
    assert payload["args"]["customer_id"] == 7
    assert payload["args"]["items"][0]["product_id"] == 1
    assert payload["args"]["shipping_address"] == "北京海淀"


@pytest.mark.asyncio
async def test_create_contract_draft_no_template_returns_error(fake_ctx, monkeypatch):
    """没启用 sales 模板 → tool 返 error 不挂起 LLM,引导 admin 上传模板。"""
    async def _fake_resolve():
        return None  # 模拟 hub.contract_template 表无 active sales
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
```

- [ ] **Step 2: 实现**

```python
# backend/hub/agent/react/tools/write.py
"""Write tools — plan-then-execute 模式。

plan 阶段（LLM 调写 tool）：
  1. 校验权限（require_permissions, fail-closed）
  2. 业务参数校验
  3. 内部解析需要的字段（如 contract 的 template_id 从 hub.contract_template 表查）
  4. 构造 canonical payload {tool_name=底层函数名, args=底层签名严格对齐}
  5. create_pending → 拿 PendingAction (含 action_id + token)
  6. 返 {status: "pending_confirmation", action_id, preview}（不返 token）

execute 阶段（LLM 调 confirm_action(action_id)）：
  → 见 confirm.py — list_pending_for_context 反查 PendingAction + claim() 消费 +
    按 payload.tool_name 在 WRITE_TOOL_DISPATCH 找底层函数 + 调用（dispatch 时把
    当前 action_id 作为 confirmation_action_id 传给 voucher / price / stock 三个底层）

关键约定：payload.tool_name = **底层函数名**（如 "generate_contract_draft"）,
不是 React tool 名（"create_contract_draft"）。这样 dispatch 直接按底层函数名查表。
"""
from __future__ import annotations
from langchain_core.tools import tool

from hub.agent.react.context import tool_ctx
from hub.agent.react.tools._confirm_helper import create_pending_action
from hub.permissions import require_permissions


async def _resolve_default_template_id() -> int | None:
    """选默认销售合同模板：第一条 is_active=True + template_type='sales'。
    复用 commit `8a8643e` 已实现的逻辑（contract subgraph 的同名 helper）。
    """
    from hub.models.contract import ContractTemplate
    tpl = (
        await ContractTemplate.filter(is_active=True, template_type="sales")
        .order_by("id").first()
    )
    return tpl.id if tpl else None


def _format_items_preview(items: list[dict]) -> str:
    """把 items 列表渲染成简短预览文本。"""
    if not items:
        return "(无)"
    parts = []
    for i, it in enumerate(items, 1):
        parts.append(
            f"{i}. 商品 id={it.get('product_id')} × {it.get('qty')} 件 @ {it.get('price')}"
        )
    return "\n  ".join(parts)


@tool
async def create_contract_draft(
    customer_id: int,
    items: list[dict],
    shipping_address: str,
    shipping_contact: str,
    shipping_phone: str,
    payment_terms: str = "",
    tax_rate: str = "",
) -> dict:
    """**plan 阶段**: 提交销售合同生成请求。本 tool **不直接生成 docx**,
    而是把请求落到 ConfirmGate pending,返 preview 给用户看,等用户确认后由
    confirm_action tool 真正执行 + 渲染 + 发钉钉。

    参数：
      customer_id: ERP 客户 ID（必须先 search_customer 拿到真实 id）
      items: [{"product_id": int, "qty": int, "price": number}, ...]
      shipping_address: 收货地址
      shipping_contact: 收货联系人
      shipping_phone: 收货电话
      payment_terms: 付款方式（默认空,admin 后台审批时补）
      tax_rate: 税率字符串（默认空）

    返 {status, action_id, preview} 三件套。
    """
    c = tool_ctx.get()
    if c is None:
        return {"error": "tool_ctx 未 set"}

    # 1. 权限 fail-closed
    await require_permissions(c["hub_user_id"], ["usecase.generate_contract.use"])

    # 2. 参数校验
    if not items:
        return {"error": "items 不能为空,合同至少要有一项商品"}
    if not customer_id:
        return {"error": "customer_id 必须传"}

    # 3. 内部查默认 template_id（不暴露给 LLM）
    template_id = await _resolve_default_template_id()
    if template_id is None:
        return {"error": "未启用销售合同模板,请联系管理员到 admin 后台上传"}

    # 4. payload args 严格按底层 generate_contract_draft 签名构造
    payload = {
        "tool_name": "generate_contract_draft",  # ← 底层函数名
        "args": {
            "template_id": template_id,
            "customer_id": customer_id,
            "items": items,
            "shipping_address": shipping_address,
            "shipping_contact": shipping_contact,
            "shipping_phone": shipping_phone,
            "payment_terms": payment_terms,
            "tax_rate": tax_rate,
        },
    }

    summary = (
        f"将给客户 id={customer_id} 生成销售合同：\n"
        f"  {_format_items_preview(items)}\n"
        f"  收货：{shipping_address} / {shipping_contact} / {shipping_phone}"
    )
    pending = await create_pending_action(
        subgraph="contract", summary=summary, payload=payload,
    )
    return {
        "status": "pending_confirmation",
        "action_id": pending.action_id,
        "preview": summary,
    }
```

```python
# backend/hub/agent/react/tools/__init__.py 更新
from hub.agent.react.tools.read import (
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
    get_customer_balance, search_orders, get_order_detail, analyze_top_customers,
    get_recent_drafts,
)
from hub.agent.react.tools.write import create_contract_draft

ALL_TOOLS = [
    # read
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
    get_customer_balance, search_orders, get_order_detail, analyze_top_customers,
    get_recent_drafts,
    # write
    create_contract_draft,
]
```

- [ ] **Step 3: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_tools_write.py -v
# Expected: 2 passed
#   test_create_contract_draft_returns_pending_with_template_id
#   test_create_contract_draft_no_template_returns_error
```

- [ ] **Step 4: Commit**

```bash
git add backend/hub/agent/react/tools/write.py backend/hub/agent/react/tools/__init__.py \
        backend/tests/react/test_tools_write.py
git commit -m "feat(hub): write tool 1 — create_contract_draft plan 阶段"
```

---

## Task 3.3: 剩 4 个 write tool — quote / voucher / adjust_price / adjust_stock

**Files:**
- Modify: `backend/hub/agent/react/tools/write.py`
- Modify: `backend/hub/agent/react/tools/__init__.py`
- Modify: `backend/tests/react/test_tools_write.py`

- [ ] **Step 1: 写失败测试（4 个）**

```python
# backend/tests/react/test_tools_write.py 追加
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
    # tool_name 是底层函数名
    assert payload["tool_name"] == "generate_price_quote"
    # args 严格按底层签名（customer_id / items / extras）— 没顶层 shipping_*
    assert payload["args"]["customer_id"] == 7
    assert payload["args"]["items"][0]["product_id"] == 1
    assert "shipping_address" not in payload["args"]
    # shipping 字段塞 extras
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
    assert payload["tool_name"] == "create_voucher_draft"  # 底层函数名
    assert payload["args"]["voucher_data"]["total_amount"] == 1000
    assert payload["args"]["rule_matched"] == "sales_template"
    # confirmation_action_id 不在 plan args 里（dispatch 时注入）
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
    assert payload["args"]["adjustment_qty"] == -5.0  # 字段名严格按底层
    assert "delta_qty" not in payload["args"]
```

- [ ] **Step 2: 实现**

```python
# backend/hub/agent/react/tools/write.py 追加
@tool
async def create_quote_draft(
    customer_id: int,
    items: list[dict],
    shipping_address: str = "",
    shipping_contact: str = "",
    shipping_phone: str = "",
) -> dict:
    """**plan 阶段**: 提交报价单生成请求。返 pending_confirmation。
    shipping 字段对报价单可选（报价单不一定有收货地址）。
    """
    c = tool_ctx.get()
    if c is None:
        return {"error": "tool_ctx 未 set"}
    await require_permissions(c["hub_user_id"], ["usecase.generate_quote.use"])

    if not items:
        return {"error": "items 不能为空"}
    if not customer_id:
        return {"error": "customer_id 必须传"}

    # 底层 generate_price_quote(customer_id, items, extras=None) 没 shipping_* 字段。
    # 把 shipping 塞 extras（合同模板渲染时从 extras 拿）。
    extras: dict = {}
    if shipping_address:
        extras["shipping_address"] = shipping_address
    if shipping_contact:
        extras["shipping_contact"] = shipping_contact
    if shipping_phone:
        extras["shipping_phone"] = shipping_phone

    payload = {
        "tool_name": "generate_price_quote",  # 底层函数名
        "args": {
            "customer_id": customer_id,
            "items": items,
            "extras": extras,
        },
    }
    summary = (
        f"将给客户 id={customer_id} 生成报价单：\n"
        f"  {_format_items_preview(items)}"
    )
    pending = await create_pending_action(
        subgraph="quote", summary=summary, payload=payload,
    )
    return {"status": "pending_confirmation", "action_id": pending.action_id, "preview": summary}


@tool
async def create_voucher_draft(
    voucher_data: dict, rule_matched: str = "",
) -> dict:
    """**plan 阶段**: 提交财务凭证草稿（挂会计审批 inbox）。

    voucher_data 必须含字段：
      - entries: list[{account, debit, credit, ...}] 借贷分录
      - total_amount: number 凭证总额
      - summary: str 凭证摘要

    rule_matched 可选,凭证模板名（如 "sales_template"）。

    LLM 看用户消息（如"做一个销售凭证 1000 元"）后,自己构造 voucher_data dict。
    本 tool **不直接写 ERP**,只写 hub VoucherDraft 表挂 admin 审批。
    """
    c = tool_ctx.get()
    if c is None:
        return {"error": "tool_ctx 未 set"}
    await require_permissions(c["hub_user_id"], ["usecase.create_voucher.use"])

    if not isinstance(voucher_data, dict) or not voucher_data:
        return {"error": "voucher_data 必须是非空 dict（含 entries / total_amount / summary）"}

    payload = {
        "tool_name": "create_voucher_draft",  # 底层函数名
        "args": {
            "voucher_data": voucher_data,
            "rule_matched": rule_matched or None,
        },
    }
    total = voucher_data.get("total_amount", "?")
    desc = voucher_data.get("summary", "<无摘要>")
    summary = f"将提交财务凭证草稿:总额 {total},摘要：{desc}"
    pending = await create_pending_action(
        subgraph="voucher", summary=summary, payload=payload,
        use_idempotency=True,  # voucher 底层 DB 唯一约束靠 confirmation_action_id;
                              # 同 args 重复必须复用同一 PendingAction（同 action_id）。
    )
    return {"status": "pending_confirmation", "action_id": pending.action_id, "preview": summary}


@tool
async def request_price_adjustment(
    customer_id: int, product_id: int, new_price: float, reason: str,
) -> dict:
    """**plan 阶段**: 提交客户专属价调整请求。新价 + 原因。需 admin 后台审批。"""
    c = tool_ctx.get()
    if c is None:
        return {"error": "tool_ctx 未 set"}
    await require_permissions(c["hub_user_id"], ["usecase.adjust_price.use"])

    if not customer_id or not product_id:
        return {"error": "customer_id 和 product_id 必须传"}
    if new_price <= 0:
        return {"error": "new_price 必须 > 0"}
    if not reason or len(reason) < 2:
        return {"error": "reason 必须填写（≥2 字）"}

    payload = {
        "tool_name": "create_price_adjustment_request",  # 底层函数名
        "args": {
            "customer_id": customer_id,
            "product_id": product_id,
            "new_price": new_price,
            "reason": reason,
        },
    }
    summary = (
        f"将提交客户 id={customer_id} 商品 id={product_id} 调价至 {new_price}。\n"
        f"理由：{reason}\n等 admin 审批后生效。"
    )
    pending = await create_pending_action(
        subgraph="adjust_price", summary=summary, payload=payload,
        use_idempotency=True,  # 同 (customer, product, price) 重复请求必须复用 PendingAction
    )
    return {"status": "pending_confirmation", "action_id": pending.action_id, "preview": summary}


@tool
async def request_stock_adjustment(
    product_id: int, adjustment_qty: float, reason: str,
    warehouse_id: int = 0,
) -> dict:
    """**plan 阶段**: 提交库存调整请求。adjustment_qty 正数加,负数减。
    warehouse_id=0 表示不指定仓库。需 admin 审批。
    """
    c = tool_ctx.get()
    if c is None:
        return {"error": "tool_ctx 未 set"}
    await require_permissions(c["hub_user_id"], ["usecase.adjust_stock.use"])

    if not product_id:
        return {"error": "product_id 必须传"}
    if adjustment_qty == 0:
        return {"error": "adjustment_qty 不能为 0"}
    if not reason or len(reason) < 2:
        return {"error": "reason 必须填写（≥2 字）"}

    args = {
        "product_id": product_id,
        "adjustment_qty": adjustment_qty,  # 字段名严格按底层(不是 delta_qty)
        "reason": reason,
    }
    if warehouse_id:
        args["warehouse_id"] = warehouse_id

    payload = {
        "tool_name": "create_stock_adjustment_request",  # 底层函数名
        "args": args,
    }
    sign = "+" if adjustment_qty > 0 else ""
    summary = (
        f"将提交商品 id={product_id} 库存调整 {sign}{adjustment_qty}。\n"
        f"理由：{reason}\n等 admin 审批后生效。"
    )
    pending = await create_pending_action(
        subgraph="adjust_stock", summary=summary, payload=payload,
        use_idempotency=True,  # 同 (product, qty) 重复请求必须复用 PendingAction
    )
    return {"status": "pending_confirmation", "action_id": pending.action_id, "preview": summary}
```

```python
# backend/hub/agent/react/tools/__init__.py 更新
from hub.agent.react.tools.write import (
    create_contract_draft, create_quote_draft, create_voucher_draft,
    request_price_adjustment, request_stock_adjustment,
)

ALL_TOOLS = [
    # read
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
    get_customer_balance, search_orders, get_order_detail, analyze_top_customers,
    get_recent_drafts,
    # write (plan 阶段)
    create_contract_draft, create_quote_draft, create_voucher_draft,
    request_price_adjustment, request_stock_adjustment,
]
```

- [ ] **Step 3: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_tools_write.py -v
# Expected: 6 passed
#   Task 3.2 旧的 2 个：test_create_contract_draft_returns_pending_with_template_id,
#                       test_create_contract_draft_no_template_returns_error
#   Task 3.3 新加 4 个：test_create_quote_draft_packs_shipping_into_extras,
#                       test_create_voucher_draft_takes_voucher_data_dict,
#                       test_request_price_adjustment_returns_pending,
#                       test_request_stock_adjustment_uses_adjustment_qty
```

- [ ] **Step 4: Commit**

```bash
git add backend/hub/agent/react/tools/write.py backend/hub/agent/react/tools/__init__.py \
        backend/tests/react/test_tools_write.py
git commit -m "feat(hub): write tools 4-5 — quote/voucher/price_adj/stock_adj plan 阶段"
```

---

## Task 3.4: confirm_action + WRITE_TOOL_DISPATCH

**Files:**
- Create: `backend/hub/agent/react/tools/confirm.py`
- Modify: `backend/hub/agent/react/tools/__init__.py`
- Modify: `backend/tests/react/test_confirm_wrapper.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/react/test_confirm_wrapper.py 追加
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
            "tool_name": "generate_contract_draft",  # 底层函数名
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
    # WRITE_TOOL_DISPATCH 在 import 时已绑定 generate_tools.generate_contract_draft 引用，
    # 直接 patch generate_tools 模块上的属性不够 — 需要 patch dispatch 表
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
    # 校验 claim 用 PendingAction.token 调
    gate.claim.assert_awaited_once_with(
        action_id="act-1", token="tok-1",
        hub_user_id=1, conversation_id="test-conv",
    )
    # 校验底层被调,且收到正确 kwargs（template_id 来自 plan 阶段填的;ctx 自动注入）
    underlying.assert_awaited_once()
    fn_kwargs = underlying.call_args.kwargs
    assert fn_kwargs["template_id"] == 1
    assert fn_kwargs["customer_id"] == 7
    assert fn_kwargs["hub_user_id"] == 1
    assert fn_kwargs["conversation_id"] == "test-conv"
    assert fn_kwargs["acting_as_user_id"] == 1
    # contract 底层不需要 confirmation_action_id（needs_action_id=False）
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
    # 关键：voucher 底层拿到了 confirmation_action_id = 当前 action_id
    assert fn_kwargs["confirmation_action_id"] == "act-vch-1"
    # voucher_data dict 直接透传
    assert fn_kwargs["voucher_data"]["total_amount"] == 1000


@pytest.mark.asyncio
async def test_confirm_action_pending_not_found(fake_ctx):
    """list_pending_for_context 没找到 action_id → 返 error 不抛。"""
    from hub.agent.react.tools.confirm import confirm_action

    gate = AsyncMock()
    gate.list_pending_for_context = AsyncMock(return_value=[])  # 空列表
    set_confirm_gate(gate)

    result = await confirm_action.ainvoke({"action_id": "act-bad"})
    assert "error" in result
    assert "不存在" in result["error"] or "过期" in result["error"]


@pytest.mark.asyncio
async def test_confirm_action_claim_raises_cross_context(fake_ctx):
    """claim() 抛 CrossContextClaim（token 失效/过期/跨 context）→ 返 error 不抛。"""
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
```

- [ ] **Step 2: 实现**

```python
# backend/hub/agent/react/tools/confirm.py
"""confirm_action tool — 用户确认 pending action 后真正执行业务。

PendingAction API 工作流（v9 路径）：
  1. list_pending_for_context() 找当前 (conv, user) 下 action_id 对应 PendingAction
  2. 用 PendingAction.token 调 gate.claim() 原子消费（HDEL pending）
  3. 按 PendingAction.payload["tool_name"] 在 WRITE_TOOL_DISPATCH 找业务函数 dispatch
  4. 调用底层函数（通过 invoke_business_tool 拿权限校验 + 审计 log）

⚠️ **不**用旧 ChainAgent claim_action / mark_confirmed / restore_action 协议。
失败语义：claim 已消费（HDEL 不可逆）→ 不能 restore。靠业务函数自身幂等
（generate_contract_draft 已有 fingerprint 幂等; voucher/price/stock 用
confirmation_action_id 做 DB 唯一约束）+ 用户重发请求触发新 pending 来恢复。

**confirmation_action_id 注入**：voucher / price / stock 三个底层函数的 kwargs
必填 confirmation_action_id 作为 DB 幂等 key。dispatch 时把当前 action_id 注入。
contract / quote 的底层不需要这个 kwarg（用 fingerprint 做幂等）。
"""
from __future__ import annotations
from typing import Any, Awaitable, Callable

from langchain_core.tools import tool

from hub.agent.react.context import tool_ctx
from hub.agent.react.tools._confirm_helper import _gate
from hub.agent.react.tools._invoke import invoke_business_tool
from hub.agent.tools import generate_tools, draft_tools
from hub.agent.tools.confirm_gate import CrossContextClaim


# Dispatch 表：payload.tool_name = 底层函数名 → (perm, fn, needs_action_id)
# needs_action_id=True 表示底层 fn 必填 confirmation_action_id kwarg（voucher/price/stock）
# False 表示不需要（contract/quote）。
WRITE_TOOL_DISPATCH: dict[str, tuple[str, Callable[..., Awaitable[Any]], bool]] = {
    "generate_contract_draft": (
        "usecase.generate_contract.use",
        generate_tools.generate_contract_draft,
        False,
    ),
    "generate_price_quote": (
        "usecase.generate_quote.use",
        generate_tools.generate_price_quote,
        False,
    ),
    "create_voucher_draft": (
        "usecase.create_voucher.use",
        draft_tools.create_voucher_draft,
        True,
    ),
    "create_price_adjustment_request": (
        "usecase.adjust_price.use",
        draft_tools.create_price_adjustment_request,
        True,
    ),
    "create_stock_adjustment_request": (
        "usecase.adjust_stock.use",
        draft_tools.create_stock_adjustment_request,
        True,
    ),
}


@tool
async def confirm_action(action_id: str) -> dict:
    """**用户确认上一条 pending action 后调本 tool 真正执行。**

    使用时机：上一轮某个写 tool 返回了 {status: "pending_confirmation", action_id, preview},
    把 preview 自然语言告诉用户后,用户回"是" / "确认" / "好的" 等确认词 → LLM 调本 tool,
    传 action_id 真正触发执行。

    成功返业务结果（如 {draft_id, file_sent}）；
    失败返 {error: "..."} (action_id 失效 / 不在 dispatch 表 / 业务执行异常等)。
    """
    c = tool_ctx.get()
    if c is None:
        return {"error": "tool_ctx 未 set"}

    gate = _gate()

    # 1. 找当前 (conv, user) 下的 PendingAction
    pendings = await gate.list_pending_for_context(
        conversation_id=c["conversation_id"],
        hub_user_id=c["hub_user_id"],
    )
    pending = next((p for p in pendings if p.action_id == action_id), None)
    if pending is None:
        return {"error": f"action_id {action_id} 不存在或已过期 — 请重新发起请求"}

    # 2. 原子 claim（HDEL pending，单次消费）
    try:
        await gate.claim(
            action_id=action_id,
            token=pending.token,
            hub_user_id=c["hub_user_id"],
            conversation_id=c["conversation_id"],
        )
    except CrossContextClaim as e:
        return {"error": f"action 失效: {e}"}

    # 3. dispatch
    payload = pending.payload  # dict {tool_name, args}
    tool_name = payload.get("tool_name") or ""
    args: dict = dict(payload.get("args") or {})
    entry = WRITE_TOOL_DISPATCH.get(tool_name)
    if entry is None:
        return {"error": f"不支持的 tool_name: {tool_name}"}
    perm, fn, needs_action_id = entry

    # 4. 执行 — 通过 invoke_business_tool 走权限 + 审计 + 注入 ctx kwargs。
    # voucher/price/stock 底层需要 confirmation_action_id（DB 唯一约束），用当前 action_id。
    extra_ctx = {
        "hub_user_id": c["hub_user_id"],
        "conversation_id": c["conversation_id"],
    }
    if needs_action_id:
        extra_ctx["confirmation_action_id"] = action_id

    # 失败语义：claim 已消费（不可 restore）。业务函数自身幂等保护
    # （contract/quote 靠 fingerprint;voucher/price/stock 靠 confirmation_action_id 唯一约束）
    try:
        return await invoke_business_tool(
            tool_name=tool_name,
            perm=perm,
            args=args,
            fn=fn,
            extra_ctx_kwargs=extra_ctx,
        )
    except Exception as e:
        return {
            "error": f"执行失败: {type(e).__name__}: {e}（请重发请求生成新草稿）"
        }
```

```python
# backend/hub/agent/react/tools/__init__.py 更新
from hub.agent.react.tools.confirm import confirm_action

ALL_TOOLS = [
    # read
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
    get_customer_balance, search_orders, get_order_detail, analyze_top_customers,
    get_recent_drafts,
    # write (plan 阶段)
    create_contract_draft, create_quote_draft, create_voucher_draft,
    request_price_adjustment, request_stock_adjustment,
    # confirm
    confirm_action,
]
# 共 16 个
```

- [ ] **Step 3: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_confirm_wrapper.py -v
# Expected: 7 passed
#   Task 3.1 旧的 2 个：test_create_pending_action_returns_pending, test_gate_not_injected_raises
#   Task 3.4 新加 5 个：test_confirm_action_dispatches_to_generate_contract_draft,
#       test_confirm_action_voucher_passes_confirmation_action_id,
#       test_confirm_action_pending_not_found,
#       test_confirm_action_claim_raises_cross_context,
#       test_confirm_action_unknown_tool_name
```

- [ ] **Step 4: Commit**

```bash
git add backend/hub/agent/react/tools/confirm.py backend/hub/agent/react/tools/__init__.py \
        backend/tests/react/test_confirm_wrapper.py
git commit -m "feat(hub): confirm_action + WRITE_TOOL_DISPATCH（5 写 tool 真执行入口）"
```

---

## Task 3.5: 端到端 fakeredis 集成测试（plan-then-execute 全链路）

**Files:**
- Test: `backend/tests/react/test_confirm_wrapper.py`（追加端到端 case）

理由：前面 Task 3.1-3.4 都用 mock gate。但真 ConfirmGate Lua 脚本的原子语义
（claim 单次消费 / 跨 context 拒 / 过期清理 / 重复 claim 抛 CrossContextClaim）
是核心安全机制,必须用真 fakeredis 端到端跑过 plan-then-execute 全链路才放心。

Codex review 建议：写 tool 创建 pending → confirm_action(action_id) claim → dispatch
payload → 重复 confirm 被拒。本 task 实现这条端到端测试。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/react/test_confirm_wrapper.py 追加
@pytest.mark.asyncio
async def test_e2e_plan_then_execute_with_real_fakeredis(fake_ctx, monkeypatch):
    """端到端：fakeredis 真 ConfirmGate + 真 create_pending_action + 真 confirm_action
    走通完整 plan-then-execute 链路。

    步骤：
      1. write tool 调 create_pending_action → 拿 PendingAction（action_id + token）
      2. confirm_action(action_id) 调 list_pending_for_context 找 pending →
         claim() 原子消费 → dispatch 业务函数（mock 返成功）
      3. 重复 confirm_action(action_id) 应该拒（pending 已被 claim 掉）
    """
    import fakeredis.aioredis
    from hub.agent.tools.confirm_gate import ConfirmGate
    from hub.agent.react.tools.write import create_contract_draft
    from hub.agent.react.tools.confirm import confirm_action, WRITE_TOOL_DISPATCH

    # 真 fakeredis ConfirmGate
    redis = fakeredis.aioredis.FakeRedis()
    gate = ConfirmGate(redis)
    set_confirm_gate(gate)

    # mock 底层 generate_contract_draft（不真渲染 docx）
    # **关键**：confirm.py 模块 import 时已经把 generate_tools.generate_contract_draft
    # 引用绑死进 WRITE_TOOL_DISPATCH 元组,monkeypatch 子模块属性**不会**生效。
    # 必须直接 setitem 替换 dispatch 表里的元组。
    underlying = AsyncMock(return_value={"draft_id": 100, "file_sent": True})
    monkeypatch.setitem(
        WRITE_TOOL_DISPATCH,
        "generate_contract_draft",
        ("usecase.generate_contract.use", underlying, False),
    )

    # Phase 1: plan 阶段
    plan_result = await create_contract_draft.ainvoke({
        "customer_id": 7,
        "items": [{"product_id": 1, "qty": 10, "price": 300.0}],
        "shipping_address": "北京海淀",
        "shipping_contact": "张三",
        "shipping_phone": "13800001111",
    })
    assert plan_result["status"] == "pending_confirmation"
    action_id = plan_result["action_id"]
    underlying.assert_not_awaited()  # 还没真执行

    # Phase 2: confirm 阶段
    exec_result = await confirm_action.ainvoke({"action_id": action_id})
    assert exec_result["draft_id"] == 100
    assert exec_result["file_sent"] is True
    underlying.assert_awaited_once()
    # 验证 dispatch 的参数跟 plan 阶段传的一致
    call_kwargs = underlying.call_args.kwargs
    assert call_kwargs["customer_id"] == 7
    assert call_kwargs["shipping_address"] == "北京海淀"
    # ctx 字段也注入了
    assert call_kwargs["hub_user_id"] == 1
    assert call_kwargs["conversation_id"] == "test-conv"

    # Phase 3: 重复 confirm 必须被拒（pending 已 HDEL）
    duplicate_result = await confirm_action.ainvoke({"action_id": action_id})
    assert "error" in duplicate_result
    assert "不存在" in duplicate_result["error"] or "过期" in duplicate_result["error"]
    underlying.assert_awaited_once()  # 仍然只调了 1 次（第二次 claim 失败,没 dispatch）


@pytest.mark.asyncio
async def test_e2e_voucher_idempotency_reuses_same_pending(fake_ctx, monkeypatch):
    """voucher 写 tool 同一 user 同 args 连续两次调 → 复用同一 PendingAction
    （同 action_id）。否则确认两次会创两条 voucher 记录（confirmation_action_id 不同）。"""
    import fakeredis.aioredis
    from hub.agent.tools.confirm_gate import ConfirmGate
    from hub.agent.react.tools.write import create_voucher_draft

    redis = fakeredis.aioredis.FakeRedis()
    gate = ConfirmGate(redis)
    set_confirm_gate(gate)
    monkeypatch.setattr("hub.agent.react.tools.write.require_permissions", AsyncMock())

    args = {
        "voucher_data": {
            "entries": [{"account": "应收", "debit": 1000, "credit": 0}],
            "total_amount": 1000, "summary": "X 月销售",
        },
        "rule_matched": "sales_template",
    }

    # 第 1 次
    r1 = await create_voucher_draft.ainvoke(args)
    aid1 = r1["action_id"]

    # 第 2 次 — 同 args（用户重复发请求）→ 必须复用 aid1
    r2 = await create_voucher_draft.ainvoke(args)
    aid2 = r2["action_id"]

    assert aid1 == aid2, (
        f"voucher 同 args 重复必须复用同一 PendingAction;实际 aid1={aid1} aid2={aid2}\n"
        f"否则用户连续两次确认会创建两条不同 voucher 草稿"
    )

    # 验证 fakeredis 只有 1 条 pending entry
    pendings = await gate.list_pending_for_context(
        conversation_id="test-conv", hub_user_id=1,
    )
    assert len(pendings) == 1


@pytest.mark.asyncio
async def test_e2e_cross_context_claim_blocked(monkeypatch):
    """另一个 user 不能 confirm 别人的 pending（ConfirmGate 跨 context 隔离）。"""
    import fakeredis.aioredis
    from hub.agent.tools.confirm_gate import ConfirmGate
    from hub.agent.react.tools.write import create_contract_draft
    from hub.agent.react.tools.confirm import confirm_action
    from hub.agent.react.context import tool_ctx, ToolContext

    redis = fakeredis.aioredis.FakeRedis()
    gate = ConfirmGate(redis)
    set_confirm_gate(gate)

    underlying = AsyncMock(return_value={"draft_id": 200, "file_sent": True})
    monkeypatch.setattr(
        "hub.agent.tools.generate_tools.generate_contract_draft", underlying,
    )

    # User 1 创建 pending
    token1 = tool_ctx.set(ToolContext(
        hub_user_id=1, acting_as=None,
        conversation_id="conv-A", channel_userid="ding-1",
    ))
    try:
        plan = await create_contract_draft.ainvoke({
            "customer_id": 7,
            "items": [{"product_id": 1, "qty": 1, "price": 100.0}],
            "shipping_address": "X", "shipping_contact": "Y", "shipping_phone": "Z",
        })
        action_id = plan["action_id"]
    finally:
        tool_ctx.reset(token1)

    # User 2 尝试 confirm User 1 的 action — 应被拒
    token2 = tool_ctx.set(ToolContext(
        hub_user_id=2, acting_as=None,  # 不同 user
        conversation_id="conv-A", channel_userid="ding-2",
    ))
    try:
        result = await confirm_action.ainvoke({"action_id": action_id})
        assert "error" in result
        underlying.assert_not_awaited()  # 必须没真执行
    finally:
        tool_ctx.reset(token2)
```

- [ ] **Step 2: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_confirm_wrapper.py -v
# Expected: 10 passed
#   Task 3.1 + 3.4 累计 7 个 + 本 task 新加 3 个 e2e:
#     test_e2e_plan_then_execute_with_real_fakeredis
#     test_e2e_voucher_idempotency_reuses_same_pending
#     test_e2e_cross_context_claim_blocked
```

如果 fakeredis 没装,先 `pip install fakeredis`（hub backend 的 dev deps 已经有 fakeredis>=2.20，pyproject.toml 里）。

- [ ] **Step 3: Commit**

```bash
git add backend/tests/react/test_confirm_wrapper.py
git commit -m "test(hub): 端到端 fakeredis 验证 plan-then-execute + 跨 context 隔离"
```

---

# Phase 4: System Prompt + Agent + Worker（Day 3 下午）

目标：完成 ReActAgent 主类 + system prompt + worker.py 改造。能 docker compose up 后跑钉钉机器人。

## Task 4.1: System Prompt

**Files:**
- Create: `backend/hub/agent/react/prompts.py`
- Test: `backend/tests/react/test_prompts.py`

- [ ] **Step 1: 写测试**

```python
# backend/tests/react/test_prompts.py
from hub.agent.react.prompts import SYSTEM_PROMPT


def test_system_prompt_mentions_key_concepts():
    """system prompt 必须告诉 LLM 关键约定。"""
    assert "ERP" in SYSTEM_PROMPT or "erp" in SYSTEM_PROMPT.lower()
    # 写操作 plan-then-execute
    assert "confirm_action" in SYSTEM_PROMPT
    assert "pending_confirmation" in SYSTEM_PROMPT
    # 复用上份合同
    assert "get_recent_drafts" in SYSTEM_PROMPT
    # 钉钉文案约定
    assert "钉钉" in SYSTEM_PROMPT
    # 中文回复
    assert "中文" in SYSTEM_PROMPT


def test_system_prompt_does_not_teach_nonexistent_tool_args():
    """关键回归断言：prompt 不能教 LLM 传不存在的参数。

    `get_recent_drafts` 真实签名只有 `limit`（详见 read.py）。早期版本 prompt 写过
    `get_recent_drafts(draft_type="contract", limit=5)`,LLM 真按 prompt 传 draft_type
    会导致 tool schema 报错,直接打死"同样/上次/复用"核心场景。本断言锁住该回归。
    """
    assert "draft_type" not in SYSTEM_PROMPT, (
        "system prompt 不应该出现 `draft_type` —— get_recent_drafts 没这个参数"
    )


def test_system_prompt_size_reasonable():
    """token 控制 — DeepSeek 中文 tokenizer 约 1 char ≈ 1 token。
    控制在 6000 char 以内（约 6K token,DeepSeek prompt cache 友好）。
    """
    assert len(SYSTEM_PROMPT) < 6000, (
        f"SYSTEM_PROMPT 太长（{len(SYSTEM_PROMPT)} chars）— 精简或拆分"
    )
```

- [ ] **Step 2: 实现**

```python
# backend/hub/agent/react/prompts.py
"""ReAct agent system prompt。

设计原则：
  1. 简短（< 2K token,DeepSeek prompt cache 友好）
  2. 关键 tool 用法显式提（confirm_action / get_recent_drafts）
  3. 中文大白话约定（不暴露英文 enum）
  4. 写操作 plan-then-execute 强约束
"""

SYSTEM_PROMPT = """你是 HUB 钉钉机器人，企业 ERP 业务助手。用户是销售/财务/管理员。

# 核心规则
- 用**中文大白话**回复,不要 markdown 标题/表格（钉钉渲染差）。
- 看不懂用户意思就直接问,不要乱猜。
- 任何业务数据（客户名、商品 SKU、价格、库存）都**必须先调 tool 查 ERP**,不要凭印象编。

# 工具集（16 个）

## 读类工具（直接调,不需用户确认）
- `search_customer(query)` — 按名/电话搜客户
- `search_product(query)` — 按名/SKU/品牌搜商品
- `get_product_detail(product_id)` — 商品详情含库存
- `check_inventory(product_id)` — 单产品库存（看品牌库存先 search_product 再批量 check）
- `get_customer_history(product_id, customer_id, limit?)` — 客户最近 N 笔某商品成交（含历史价）
- `get_customer_balance(customer_id)` — 客户余额/欠款/信用额度
- `search_orders(customer_id?, since_days?)` — 搜订单（customer_id=0 看全部）
- `get_order_detail(order_id)` — 订单详情
- `analyze_top_customers(period?, top_n?)` — 大客户销售排行
- `get_recent_drafts(limit?)` — **当前会话最近的合同草稿**（仅 contract,解决"同样/上次/复用"）

## 写类工具（plan-then-execute 模式）
- `create_contract_draft(...)` / `create_quote_draft(...)` / `create_voucher_draft(...)` /
  `request_price_adjustment(...)` / `request_stock_adjustment(...)`

调写工具会返 `{status: "pending_confirmation", action_id, preview}`。
**不要假装已经成功** — 你必须把 preview 自然语言告诉用户,等用户回"是/确认/好的"等
确认词后,**再调 `confirm_action(action_id)`** 才真正执行（生成 docx / 提交审批等）。

## confirm 工具
- `confirm_action(action_id)` — 用户确认后调本工具触发真正执行。返业务结果（如 draft_id）。

# 跨轮 reference（关键）

用户说"同样" / "一样" / "上一份" / "前面那个" / "和翼蓝那份一样" 等任意表达 →
**先调 `get_recent_drafts(limit=5)`** 看上次发了什么,然后据此构造新请求。
不要硬猜,工具拿到的数据才算真。

注：`get_recent_drafts` **只有** `limit` 一个参数（仅返合同草稿,本身就是 contract-only),
不要传 `draft_type` 之类的额外参数,会导致 tool schema 报错。

# 缺信息怎么办

不要假装信息齐全。直接告诉用户"还缺 XX,告诉我"。例：
  用户："做合同 X1 10 个"
  你：（无 tool 调用）"好的,给哪个客户?X1 单价多少?收货地址、联系人、电话?"

下一轮用户补字段后,你看完整 message 历史再决定调 tool。

# 风格
- 简短,务实,不啰嗦
- 不暴露英文字段名（如 customer_address / shipping_phone 等）— 用中文说"客户地址" / "收货电话"
- 出错时说人话:"找不到这个客户,确认下名字" 不要说 "404 / not_found"
"""
```

- [ ] **Step 3: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_prompts.py -v
# Expected: 3 passed
#   test_system_prompt_mentions_key_concepts
#   test_system_prompt_does_not_teach_nonexistent_tool_args
#   test_system_prompt_size_reasonable
```

- [ ] **Step 4: Commit**

```bash
git add backend/hub/agent/react/prompts.py backend/tests/react/test_prompts.py
git commit -m "feat(hub): system prompt（< 2K token,关键工具用法显式）"
```

---

## Task 4.2: ReActAgent 主类

**Files:**
- Create: `backend/hub/agent/react/agent.py`
- Test: `backend/tests/react/test_react_agent.py`

- [ ] **Step 1: 写测试**

```python
# backend/tests/react/test_react_agent.py
import pytest
from unittest.mock import AsyncMock
from hub.agent.react.agent import ReActAgent


def test_agent_thread_id_uses_react_namespace():
    """thread_id 必须 = f'react:{conv}:{user}' (避开旧 GraphAgent checkpoint)。"""
    agent = ReActAgent(
        chat_model=AsyncMock(),
        tools=[],
        checkpointer=None,
    )
    config = agent._build_config(conversation_id="cv-1", hub_user_id=42)
    assert config["configurable"]["thread_id"] == "react:cv-1:42"


@pytest.mark.asyncio
async def test_agent_run_returns_friendly_msg_on_recursion_limit():
    """recursion_limit 触发（GraphRecursionError）→ 返友好文本,不抛。"""
    from langgraph.errors import GraphRecursionError
    from hub.agent.react.context import tool_ctx

    fake_compiled = AsyncMock()
    fake_compiled.ainvoke = AsyncMock(side_effect=GraphRecursionError("limit hit"))

    agent = ReActAgent(chat_model=AsyncMock(), tools=[], checkpointer=None)
    agent.compiled_graph = fake_compiled

    reply = await agent.run(
        user_message="x", hub_user_id=1, conversation_id="cv-r",
        acting_as=None, channel_userid="ding-r",
    )
    assert reply is not None
    assert "超限" in reply or "限" in reply
    # ContextVar 仍然 reset
    assert tool_ctx.get() is None


@pytest.mark.asyncio
async def test_agent_run_sets_tool_ctx_and_invokes():
    """run() 应该 set ContextVar + 调 compiled_graph.ainvoke + 提取 last assistant message。"""
    from langchain_core.messages import HumanMessage, AIMessage
    from hub.agent.react.context import tool_ctx

    fake_compiled = AsyncMock()
    fake_compiled.ainvoke = AsyncMock(return_value={
        "messages": [HumanMessage(content="hi"), AIMessage(content="在的~")],
    })

    agent = ReActAgent(
        chat_model=AsyncMock(),
        tools=[],
        checkpointer=None,
    )
    agent.compiled_graph = fake_compiled  # 注入 mock

    # 调用前 ContextVar 是空
    assert tool_ctx.get() is None

    reply = await agent.run(
        user_message="hi",
        hub_user_id=1,
        conversation_id="cv-1",
        acting_as=None,
        channel_userid="ding-1",
    )
    assert reply == "在的~"
    fake_compiled.ainvoke.assert_awaited_once()
    # 调用后 ContextVar 必须 reset 回 None（不污染下一个测试）
    assert tool_ctx.get() is None
```

- [ ] **Step 2: 实现**

```python
# backend/hub/agent/react/agent.py
"""ReActAgent — HUB v10 主 agent 类。

封装 langgraph.prebuilt.create_react_agent,对外保持 .run() 接口跟 GraphAgent 兼容,
让现有 DingTalk inbound handler / GraphAgentAdapter 不动。
"""
from __future__ import annotations
import logging
from typing import Any, Awaitable, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError

from hub.agent.react.context import tool_ctx, ToolContext
from hub.agent.react.prompts import SYSTEM_PROMPT


logger = logging.getLogger(__name__)


class ReActAgent:
    """ReAct agent 主类。

    对外接口跟 GraphAgent 兼容（worker.py + dingtalk_inbound 不动）。

    用法：
        agent = ReActAgent(chat_model=..., tools=ALL_TOOLS, checkpointer=...)
        reply = await agent.run(
            user_message="...",
            hub_user_id=1,
            conversation_id="cv-1",
            acting_as=None,
            channel_userid="ding-u",
        )
    """

    def __init__(
        self,
        *,
        chat_model: BaseChatModel,
        tools: list[BaseTool],
        checkpointer: BaseCheckpointSaver | None,
        recursion_limit: int = 15,
    ):
        self.chat_model = chat_model
        self.tools = tools
        self.checkpointer = checkpointer
        self.recursion_limit = recursion_limit

        self.compiled_graph = create_react_agent(
            model=chat_model,
            tools=tools,
            prompt=SYSTEM_PROMPT,
            checkpointer=checkpointer,
        )

    def _build_config(self, *, conversation_id: str, hub_user_id: int) -> dict:
        """thread_id = f'react:{conv}:{user}' — 跟旧 GraphAgent checkpoint 隔离 namespace。"""
        return {
            "configurable": {
                "thread_id": f"react:{conversation_id}:{hub_user_id}",
            },
            "recursion_limit": self.recursion_limit,
        }

    async def run(
        self,
        *,
        user_message: str,
        hub_user_id: int,
        conversation_id: str,
        acting_as: int | None = None,
        channel_userid: str = "",
    ) -> str | None:
        """跑一轮对话,返 LLM 最终自然语言回复。

        流程:
          1. set ContextVar tool_ctx（hub_user_id / acting_as / conv / channel）
          2. ainvoke compiled_graph 传入 messages 增量（HumanMessage(user_message)）
          3. 拿最后一条 AIMessage.content 当 reply
          4. reset ContextVar
        """
        config = self._build_config(
            conversation_id=conversation_id, hub_user_id=hub_user_id,
        )
        ctx: ToolContext = {
            "hub_user_id": hub_user_id,
            "acting_as": acting_as,
            "conversation_id": conversation_id,
            "channel_userid": channel_userid,
        }
        token = tool_ctx.set(ctx)
        try:
            result = await self.compiled_graph.ainvoke(
                {"messages": [HumanMessage(content=user_message)]},
                config=config,
            )
            messages = result.get("messages", [])
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    return msg.content
            return None
        except GraphRecursionError:
            # recursion_limit 触发（DeepSeek 死循环 / LLM 反复调相同 tool 等)→ 友好返
            logger.warning(
                "ReActAgent recursion_limit 触发 conv=%s user=%s msg=%r",
                conversation_id, hub_user_id, user_message[:200],
            )
            return "推理步骤超限,请简化请求或联系管理员。"
        except Exception:
            logger.exception(
                "ReActAgent 抛异常 conv=%s user=%s msg=%r",
                conversation_id, hub_user_id, user_message[:200],
            )
            raise
        finally:
            tool_ctx.reset(token)
```

- [ ] **Step 3: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_react_agent.py -v
# Expected: 3 passed
#   test_agent_thread_id_uses_react_namespace
#   test_agent_run_returns_friendly_msg_on_recursion_limit
#   test_agent_run_sets_tool_ctx_and_invokes
```

- [ ] **Step 4: Commit**

```bash
git add backend/hub/agent/react/agent.py backend/tests/react/test_react_agent.py
git commit -m "feat(hub): ReActAgent 主类（thread_id namespace + ContextVar set + run 接口）"
```

---

## Task 4.3: worker.py 改造启动 ReActAgent

**Files:**
- Modify: `backend/worker.py`

- [ ] **Step 1: 读 worker.py 现有 GraphAgent 构造段**

```bash
grep -nE "GraphAgent|graph_agent_inner|register_all_tools" backend/worker.py | head -20
```

- [ ] **Step 2: 找 worker.py 现有 GraphAgent 构造段位置 + 实际变量名**

```bash
# 找到 ConfirmGate 实例的变量名
grep -nE "ConfirmGate\(\)|confirm_gate\s*=" backend/worker.py | head -3

# 找到 GraphAgent 构造段
grep -nE "GraphAgent\(|graph_agent_inner" backend/worker.py | head -5

# 找到 checkpointer 实际变量名（worker.py:176 现有名为 _checkpointer）
grep -nE "checkpointer\s*=|AsyncPostgresSaver" backend/worker.py | head -5
```

记录：
- ConfirmGate 实例变量名（应该是 `confirm_gate`，但要 verify）
- checkpointer 实例变量名（worker.py:176 实际叫 `_checkpointer`,Plan 6 v9 命名）
- `agent_base_url` / `ai_provider` 等的实际定义位置

- [ ] **Step 3: 改 worker.py main() 关键段**

把 `GraphAgent(...)` 替换成 `ReActAgent(...)`，并注入 `set_confirm_gate`。**用变量名 + 上下文匹配，不依赖行号**。

**同时必须删除以下 v9 残留**（否则 worker 启动会冗余 register / 内存泄漏 / dead code）：

| 残留 | 位置（worker.py 现状大致行号）| 删除原因 |
|---|---|---|
| `tool_registry = ToolRegistry(...)` | ~108 | ReAct 不走 ToolRegistry,所有 tool 通过 LangChain `@tool` 装饰 + `invoke_business_tool` helper |
| `erp_tools.register_all(tool_registry)` | ~113 | 同上 |
| `analyze_tools.register_all(tool_registry)` | ~114 | 同上 |
| `generate_tools.register_all(tool_registry)` | ~116 | 同上 |
| `draft_tools.register_all(tool_registry)` | ~118 | 同上 |
| `_tool_ctx: ContextVar` 顶层定义 | ~147 | 旧 GraphAgent tool_executor 闭包用,ReAct 用 `hub.agent.react.context.tool_ctx` |
| `async def tool_executor(name, args)` 闭包 | ~151 | GraphAgent → ToolRegistry.call 入口,ReAct 不需要 |
| `import contextvars` | imports 段 | 上面 `_tool_ctx` 删后无用 |
| `from hub.agent.tools.tool_registry import ToolRegistry` | imports 段 | 上面 `tool_registry` 删后无用 |

```python
# backend/worker.py 修改：

# 1. 在 imports 段加（顶部 import 区）
from hub.agent.react.agent import ReActAgent
from hub.agent.react.llm import build_chat_model
from hub.agent.react.tools import ALL_TOOLS
from hub.agent.react.tools._confirm_helper import set_confirm_gate

# （删 imports）
# - import contextvars
# - from hub.agent.tools.tool_registry import ToolRegistry

# 2. 找到 ConfirmGate 构造完成的位置（变量名假设 confirm_gate），紧随其后插入：
set_confirm_gate(confirm_gate)

# 3. 删除现有 `tool_registry = ToolRegistry(...)` 整段 + 所有 register_all 调用
#    + `_tool_ctx: ContextVar` + `tool_executor` 闭包定义。

# 4. 找到现有 `graph_agent_inner = GraphAgent(...)` 段（多行），整段替换为：
chat_model = build_chat_model(
    api_key=ai_provider.api_key if ai_provider else "",
    base_url=agent_base_url,
    model=ai_provider.model if ai_provider else "deepseek-v4-flash",
    temperature=0.0,
    max_tokens=4096,
)
graph_agent_inner = ReActAgent(
    chat_model=chat_model,
    tools=ALL_TOOLS,
    checkpointer=_checkpointer,
    recursion_limit=15,
)
```

如果 ConfirmGate 实例变量不叫 `confirm_gate`，按实际名字改。

- [ ] **Step 4: 改 GraphAgentAdapter 内部调试日志（删旧 graph/config 调用）**

worker.py 现有 `class GraphAgentAdapter` 内部 `[GA-IN]` / `[GA-PRE-STATE]` /
`[GA-OUT]` 调试日志用了 `from hub.agent.graph.config import build_langgraph_config`
+ `compiled_graph.aget_state(cfg)` 来 peek 旧 GraphAgent state（看 customer /
candidate / shipping 等业务字段）。

ReAct 时代：
- graph/config.py 在 Task 5.4 删除（adapter 不能再 import）
- 没有"业务 state 字段"可 peek（messages 才是状态,看不出语义）
- ReAct thread_id namespace 不一样（`react:` 前缀）

**修法**：simplify GraphAgentAdapter,只保留 `[GA-IN]` 入口 log + `[GA-OUT]` 出口
log（自然语言 reply 截断 200 字）。删除 `[GA-PRE-STATE]` 整段以及任何 `aget_state` /
`build_langgraph_config` 调用。

```python
# backend/worker.py — GraphAgentAdapter 改造（关键差异）：
# - 删除 import: from hub.agent.graph.config import build_langgraph_config
# - 删除 [GA-PRE-STATE] 整段（约 worker.py:213-247）
# - 删除 [GA-OUT] 段里的 aget_state(cfg) 调用,只保留 reply 截断 log
# - **删除 _tool_ctx.set/reset 调用** — 旧 GraphAgent 靠 _tool_ctx 给 tool_executor
#   闭包传 ctx,ReActAgent.run() 内部自己 set hub.agent.react.context.tool_ctx,
#   adapter 不再需要管 ctx。

class GraphAgentAdapter:
    """v10 简化版：只透传 user_message → ReActAgent.run() → text reply。

    v10 ReAct 时代 state 是 messages,无业务字段可 peek（LangSmith trace 看 message 流）。
    旧版 _tool_ctx 闭包 + aget_state state peek 全删。
    """
    async def run(
        self, user_message: str, *, hub_user_id: int, conversation_id: str,
        acting_as: int | None = None, channel_userid: str | None = None,
    ) -> AgentResult:
        cid_short = (conversation_id or "")[-12:]
        logger.info(
            "[GA-IN] cid=...%s user=%d msg=%r",
            cid_short, hub_user_id, user_message[:200],
        )
        result = await graph_agent_inner.run(
            user_message=user_message,
            hub_user_id=hub_user_id,
            conversation_id=conversation_id,
            acting_as=acting_as,
            channel_userid=channel_userid or "",
        )

        logger.info(
            "[GA-OUT] cid=...%s reply=%r",
            cid_short, (result or "")[:200],
        )

        if result is None:
            return AgentResult.text_result("（无回复）")
        return AgentResult.text_result(result)
```

GraphAgentAdapter 类名保留（dingtalk_inbound handler 期望同接口）, 但内部去掉所有
GraphAgent state peek 逻辑 + `_tool_ctx` 闭包逻辑。

- [ ] **Step 5: 启动前断言 ALL_TOOLS 齐 16 + docker 启动验证**

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent

# 启动前 sanity check —— Phase 1-3 累加 16 个 tool（10 read + 5 write + 1 confirm），
# 任何 task `__init__.py` rewrite 漏 import 在这里炸出来,不要等 LLM 试调时才发现
cd backend && .venv/bin/python -c "from hub.agent.react.tools import ALL_TOOLS; assert len(ALL_TOOLS) == 16, f'ALL_TOOLS 数量错: {len(ALL_TOOLS)} != 16'; print(f'OK: {len(ALL_TOOLS)} tools')"
# Expected: OK: 16 tools
cd ..

COMPOSE_PROJECT_NAME=hub docker compose build hub-worker
COMPOSE_PROJECT_NAME=hub docker compose up -d --force-recreate hub-worker

# 等启动完成
until docker logs hub-hub-worker-1 2>&1 | grep -qE "Worker.*启动"; do sleep 2; done
docker logs hub-hub-worker-1 2>&1 | head -10

# Expected: 启动成功无 error，LangGraph + AsyncPostgresSaver 仍 ready
```

- [ ] **Step 6: Commit**

```bash
git add backend/worker.py
git commit -m "feat(hub): worker 启动 ReActAgent 替代 GraphAgent"
```

---

# Phase 5: Acceptance Tests + Cleanup（Day 4-5）

目标：写真 LLM 端到端 acceptance 测试；钉钉手测主线场景；删旧代码。

## Task 5.1: Scenario YAML fixtures（仅校验 yaml 合法 + schema 一致）

**Files:**
- Create: `backend/tests/react/test_acceptance_scenarios.py`
- Create: `backend/tests/react/fixtures/scenarios/single_turn_contract.yaml`
- Create: `backend/tests/react/fixtures/scenarios/reuse_previous_contract.yaml`

⚠️ **本 task 只是 fixture 合法性校验**（yaml parse + 必填字段都在），**不**驱动 ReAct
agent 跑。真正的"agent 调用链可验证"由 Task 5.1.5（fake chat model 端到端）+
Task 5.3（@realllm 真 LLM eval）覆盖。

Codex review 提醒：本 task 之前命名 "acceptance" 误导,会让人以为已覆盖 orchestration
正确性 — 实际上只解析 yaml 不驱动 agent,即使写 tool/confirm_action/tool binding 全坏
也能 pass。重命名 + scope 清楚。

- [ ] **Step 1: yaml fixture**

```yaml
# backend/tests/react/fixtures/scenarios/reuse_previous_contract.yaml
name: 复用上份合同（同样给 X 也来一份）
turns:
  # T1：先做翼蓝合同 — 期望调到 search_customer + search_product + create_contract_draft
  - input: "给翼蓝做合同 X1 10 个 300，地址北京海淀，张三 13800001111"
    mock_tool_calls_must_include: [search_customer, search_product, create_contract_draft]
    final_message_contains_any: ["preview", "确认", "请回"]

  # T2：用户回"是" — 期望调 confirm_action
  - input: "是"
    mock_tool_calls_must_include: [confirm_action]
    final_message_contains_any: ["已生成", "draft_id"]

  # T3：复用 — 同样给得帆
  - input: "同样的内容给得帆也做一份"
    mock_tool_calls_must_include: [get_recent_drafts, search_customer, create_contract_draft]
    final_message_contains_any: ["preview", "确认", "请回"]
```

注：用 `mock_tool_calls_must_include`（list of tool name）而非具体 args 断言 —
yaml 写不死 runtime action_id / fingerprint。具体 args 校验放代码层（mock 检查
call_args.kwargs）。

- [ ] **Step 2: test_acceptance_scenarios.py**

```python
# backend/tests/react/test_acceptance_scenarios.py
"""ReAct agent yaml 场景测试 — mock LLM 版本（deterministic 路径）。

真 LLM eval 在 test_realllm_eval.py（@pytest.mark.realllm）。
"""
import pytest
from unittest.mock import AsyncMock
from pathlib import Path
import yaml

SCENARIOS_DIR = Path(__file__).parent / "fixtures" / "scenarios"


@pytest.mark.parametrize("yaml_file", sorted(p.name for p in SCENARIOS_DIR.glob("*.yaml")))
@pytest.mark.asyncio
async def test_react_scenario(yaml_file, monkeypatch):
    """每个 yaml fixture 跑一遍。

    断言：
      - 每个 turn 的 mock_tool_calls_must_include 都被调到
      - 最终 message 包含 final_message_contains 字符串
    """
    scenario = yaml.safe_load((SCENARIOS_DIR / yaml_file).read_text(encoding="utf-8"))

    # mock chat_model + create_react_agent 内部
    # ... 详细 mock 见 conftest（保留 LangGraph compiled_graph 的真实路径,
    # 只 mock chat_model.bind_tools.invoke）...
    # 由于篇幅,本 task 只走 SMOKE：能 import + scenario 能 parse
    assert scenario.get("name")
    assert scenario.get("turns")
```

由于真完整 mock LangGraph + tool 调用链复杂，本 task **smoke level**——确保 yaml 能 parse + test 不抛。完整 mock 留给真 LLM eval（Task 5.3）。

- [ ] **Step 3: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_acceptance_scenarios.py -v
# Expected: smoke 全 pass（每个 yaml 1 个 test）
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/react/test_acceptance_scenarios.py \
        backend/tests/react/fixtures/scenarios/*.yaml
git commit -m "test(hub): yaml fixture 合法性校验（不驱动 agent）"
```

---

## Task 5.1.5: Fake chat model 真驱动 ReAct agent 端到端测试

**Files:**
- Create: `backend/tests/react/test_react_agent_e2e.py`

理由：Task 5.1 只 parse yaml,Task 5.2 钉钉手测,Task 5.3 真 LLM eval（需 API key）。
这中间缺一层：**deterministic mock chat model 驱动 ReAct agent 跑完整 tool 链**,
不依赖外部 LLM,但能验证：
- LangGraph create_react_agent + ALL_TOOLS 能正确 bind + invoke
- LLM 输出 tool_calls 后,LangGraph 真调到对应 @tool 函数
- 写 tool 返 pending_confirmation 后,LLM 第二次 invoke 能调 confirm_action
- ContextVar 在两轮 ainvoke 之间正确传递

LangChain 测试推荐用法：`langchain_core.language_models.fake_chat_models.FakeChatModel`
或自己写一个返 `AIMessage(tool_calls=[...])` 的 fake — 让 LangGraph 真跑 ToolNode。

- [ ] **Step 1: 写失败测试（两轮：preview → confirm）**

**关键约束**（根据 Codex review）：必须覆盖完整 plan-then-execute 链路:
- T1 用户提合同请求 → LLM 调 search_customer → create_contract_draft → 返 preview
- T2 用户回"是" → LLM 调 confirm_action(action_id) → list_pending → claim → dispatch
  → 真调底层 generate_contract_draft

底层 `generate_contract_draft` 必须**只被调用一次**（confirm 阶段）, plan 阶段不该调。
重复 confirm 必须被拒（pending HDEL 单次）。

```python
# backend/tests/react/test_react_agent_e2e.py
"""ReAct agent 端到端测试 — fake chat model 驱动真 LangGraph + 真 ALL_TOOLS + 真 fakeredis ConfirmGate。

按 messages 长度决定 LLM 下一步,跑完整两轮 plan-then-execute。

**关键约束**（Codex review 升级）：fake chat model 必须是真 BaseChatModel 子类,
能跟 LangGraph create_react_agent 完整对接,**禁止降级为直接调 tool.ainvoke** —
否则 LangGraph ToolNode + AIMessage tool_calls 编排路径没被覆盖,confirm_action
ReAct 编排的正确性也没被验证。
"""
import pytest
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
    （我们的 fake 不消费 tool schemas,所以不传 tools 给底层是正确的。）

    实测：跑过 `m.bind_tools([fake_tool])` 直接 NotImplementedError,本子类后跑通。
    """
    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self  # noop;create_react_agent 之后用 self.ainvoke 推进 scripted iterator


def _make_scripted_chat(messages: list[BaseMessage]):
    """LangGraph 兼容的 fake chat model：scripted AIMessage iterator + bind_tools no-op。

    每次 LangGraph 内部 model.ainvoke 从 iterator 推进一条 AIMessage（含可选 tool_calls）,
    驱动 ToolNode → @tool 调用 → ToolMessage → 下一轮 model.ainvoke 完整链路。

    iter(list) 持有原 list 引用,可以在 T1 完成后用 `messages[i] = AIMessage(...)`
    修改尚未 yield 的 entry（plan 步骤要求 T2 前回填真 action_id）。
    """
    return _ToolBindingFake(messages=iter(messages))


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
    # erp_tools.search_customers 内部用 module-level current_erp_adapter() 拿 adapter,
    # patch 模块属性即可生效。
    monkeypatch.setattr("hub.agent.tools.erp_tools.current_erp_adapter", lambda: erp)
    # 但 read.py 里 `from hub.agent.tools.erp_tools import current_erp_adapter` 把引用
    # 拷到了 read 模块 namespace。`_get_erp_customer_name` 用的是 read.current_erp_adapter,
    # patch 源模块**不影响**已 import 的引用。e2e 走 `get_recent_drafts` 时会走到这条路径。
    # 必须**两个路径都 patch**。
    monkeypatch.setattr("hub.agent.react.tools.read.current_erp_adapter", lambda: erp)

    # 真 fakeredis ConfirmGate（关键：让 plan-then-execute 走真 Lua 原子语义）
    redis = fakeredis.aioredis.FakeRedis()
    gate = ConfirmGate(redis)
    set_confirm_gate(gate)

    # mock 底层 generate_contract_draft（不真渲染 docx）
    # 关键：confirm.py 模块 import 时已经把 generate_tools.generate_contract_draft
    # 引用绑进 WRITE_TOOL_DISPATCH 元组,monkeypatch 子模块属性**不会**改 dispatch 表
    # 已绑的引用 → confirm_action 仍走旧函数。必须直接 setitem 替换 dispatch 表里的元组。
    underlying = AsyncMock(return_value={"draft_id": 99, "file_sent": True})
    monkeypatch.setitem(
        WRITE_TOOL_DISPATCH,
        "generate_contract_draft",
        ("usecase.generate_contract.use", underlying, False),
    )
    # mock _resolve_default_template_id
    async def _fake_template():
        return 1
    monkeypatch.setattr(
        "hub.agent.react.tools.write._resolve_default_template_id", _fake_template,
    )
    # mock 权限校验全过
    monkeypatch.setattr("hub.agent.react.tools.write.require_permissions", AsyncMock())
    monkeypatch.setattr("hub.agent.react.tools._invoke.require_permissions", AsyncMock())

    # === T1 scripted: search_customer → create_contract_draft → preview ===
    # 注：scripted 列表是**两轮 ainvoke** 的 LLM 调用累计。
    # T1 ainvoke 跑 3 步（search → create_contract → preview）;T2 ainvoke 跑 2 步（confirm → final）
    scripted = [
        # --- T1 LLM 调用 1: search_customer ---
        AIMessage(content="", tool_calls=[{
            "id": "c1", "name": "search_customer", "args": {"query": "阿里"},
        }]),
        # --- T1 LLM 调用 2: create_contract_draft ---
        AIMessage(content="", tool_calls=[{
            "id": "c2", "name": "create_contract_draft", "args": {
                "customer_id": 7,
                "items": [{"product_id": 1, "qty": 10, "price": 300.0}],
                "shipping_address": "北京海淀",
                "shipping_contact": "张三",
                "shipping_phone": "13800001111",
            },
        }]),
        # --- T1 LLM 调用 3: preview 给用户（自然语言）---
        AIMessage(content="将给阿里生成合同：X1×10@300。请回'是'确认。"),

        # --- T2 LLM 调用 1: 看到用户"是" + 上轮 messages 含 action_id → 调 confirm_action ---
        # 注意 action_id 是 runtime 生成的,scripted 这里写"占位",
        # 实施时改成从 T1 messages 反查 action_id（看 ToolMessage 内容里的 action_id）。
        # 简化：让 fake LLM 看 messages 自动 extract action_id（见 _ScriptedChatModelV2 实现）
        # 或：把 ainvoke 拆成函数模式（不写死 scripted）
        AIMessage(content="", tool_calls=[{
            "id": "c3", "name": "confirm_action", "args": {"action_id": "<filled-at-runtime>"},
        }]),
        # --- T2 LLM 调用 2: 业务结果给用户 ---
        AIMessage(content="合同已生成,draft_id=99,文件已发送。"),
    ]

    # 注：上面 scripted T2 的 confirm_action.args.action_id = "<filled-at-runtime>"
    # 是占位 — 实施 test 时,T2 ainvoke 前需要从 T1 messages 里 extract 出真 action_id
    # 重写 scripted[3].tool_calls[0]["args"]["action_id"] = real_action_id。
    # 见 step 2 实现里的 helper.

    chat = _make_scripted_chat(scripted)

    # MemorySaver 让两轮间 messages 持久化（必须有 checkpointer 跨轮）
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
    # T1 阶段：底层 generate_contract_draft **不该被调**
    underlying.assert_not_awaited()

    # 从 fakeredis 反查真 action_id（plan 阶段创建的 PendingAction）
    pendings = await gate.list_pending_for_context(
        conversation_id="cv-e2e", hub_user_id=1,
    )
    assert len(pendings) == 1
    real_action_id = pendings[0].action_id
    # 把 T2 scripted 的 action_id 占位填上真值
    scripted[3].tool_calls[0]["args"]["action_id"] = real_action_id

    # === T2: confirm ===
    reply2 = await agent.run(
        user_message="是",
        hub_user_id=1, conversation_id="cv-e2e",
        acting_as=None, channel_userid="ding-e2e",
    )
    assert reply2 is not None
    assert "已生成" in reply2 or "draft_id" in reply2

    # 关键断言：底层执行**且仅执行一次**（不是 0,不是 2）
    underlying.assert_awaited_once()
    fn_kwargs = underlying.call_args.kwargs
    assert fn_kwargs["customer_id"] == 7
    assert fn_kwargs["template_id"] == 1
    assert fn_kwargs["hub_user_id"] == 1

    # pending 已被消费（claim HDEL）— 重复 confirm 拿不到
    pendings_after = await gate.list_pending_for_context(
        conversation_id="cv-e2e", hub_user_id=1,
    )
    assert len(pendings_after) == 0, "claim 后 pending 必须 HDEL（不可双发）"
```

**实现约束**（**禁止降级**）：

✅ 必须用 LangChain 真 BaseChatModel 子类（`_ToolBindingFake` 继承自 `GenericFakeChatModel`,后者继承 `BaseChatModel`）。
✅ 必须走真 LangGraph `create_react_agent` + 真 `ALL_TOOLS` 的编排路径。
✅ 必须断言 ToolNode 真把 AIMessage.tool_calls 调到对应 @tool。
❌ 禁止降级为顺序调 `tool.ainvoke()` 绕开 LangGraph — 那样 confirm_action ReAct 编排
   路径没被验证,本 task 失去意义。

**关于 bind_tools 兼容性**：`GenericFakeChatModel` 自身的 `bind_tools` 抛 `NotImplementedError`
（实测:`m.bind_tools([fake_tool])` 直接 NotImplementedError → `create_react_agent` 编译期挂）。
`_ToolBindingFake` 子类把 `bind_tools` 覆盖成 no-op 返 self —— 这是已知 LangGraph + GenericFakeChatModel
集成的标准 fake pattern。如果未来 langchain-core 版本升级 fake_chat_models 自带 bind_tools,
`_ToolBindingFake` 子类直接 drop 掉即可。

- [ ] **Step 2: 跑测试确认 pass**

```bash
cd backend && .venv/bin/python -m pytest tests/react/test_react_agent_e2e.py -v
# Expected: 1 passed
```

如果 `langchain_core.language_models.fake_chat_models.FakeChatModel` 在当前
langchain-core 版本里 path 不一样,改为 `from langchain_core.language_models.fake import FakeListChatModel` 或自己写一个最简的 `BaseChatModel` 子类（只实现 `_agenerate`）。

- [ ] **Step 3: Commit**

```bash
git add backend/tests/react/test_react_agent_e2e.py
git commit -m "test(hub): fake chat model 端到端驱动 ReAct + ALL_TOOLS（验证 orchestration）"
```

---

## Task 5.2: 钉钉手测 4 个核心场景

无新代码，**手测脚本 + 截图**记录到 `docs/superpowers/staging/2026-05-03-react-acceptance-manual.md`。

- [ ] **Step 1: 创建 staging 文档**

```bash
# 重要：cwd 必须是 worktree root（docs/ 在 worktree 顶层,不是 backend/ 下）
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent
mkdir -p docs/superpowers/staging
```

```markdown
# 2026-05-03 React Agent 钉钉手测记录

## 场景 1：单轮做合同
输入：`给阿里做合同 X1 10 个 300，地址北京海淀，张三 13800001111`
期望：bot 输出 preview → 用户回"是" → bot 发出 docx
实测：[ ] 通过 / [ ] 不通过（worker logs：）

## 场景 2：复用上份（同样给 X）
[场景 1 跑通后]
输入：`同样的内容给得帆也做一份`
期望：bot 调 get_recent_drafts → search_customer("得帆") → preview → 用户确认 → docx 发到钉钉
实测：[ ] 通过 / [ ] 不通过

## 场景 3：闲聊
输入：`在吗`
期望：bot 不调任何 tool,直接回"在的~"
实测：[ ] 通过 / [ ] 不通过

## 场景 4：缺字段
输入：`做合同 X1 10 个`
期望：bot 不调 create_contract_draft（信息不齐）,直接回询问"哪个客户？地址？联系人？电话？"
实测：[ ] 通过 / [ ] 不通过
```

- [ ] **Step 2: 用户钉钉发四条消息测**（人工 step）

记录每个场景：
- worker 日志（`docker logs hub-hub-worker-1 --tail 100`）
- bot 实际回复内容
- 是否收到 docx

- [ ] **Step 3: Commit 测试报告**

```bash
git add docs/superpowers/staging/2026-05-03-react-acceptance-manual.md
git commit -m "docs(staging): React agent 钉钉手测 4 场景结果"
```

---

## Task 5.3: 真 LLM eval（@pytest.mark.realllm，**新写 ReAct fixture**）

**Files:**
- Create: `backend/tests/react/test_realllm_eval.py`
- Create: `backend/tests/react/fixtures/scenarios/realllm/*.yaml`

**不复用** 旧 GraphAgent 的 yaml（`tests/agent/fixtures/scenarios/`）。
理由：旧 fixture 期望"单轮 generate_contract_draft 调 1 次 + sent_files >= 1",
但 ReAct 设计是 plan-then-execute 两轮（preview turn 调 `create_contract_draft`
返 pending；confirm turn 调 `confirm_action` 后才真 send_file）。直接复用会
要么失败,要么强迫 ReAct 退回旧语义。

**新 fixture schema**（按 ReAct 流程设计）：

```yaml
# 每个 turn 一个新断言 schema
turns:
  - input: <用户消息>
    expected_tool_calls_at_least:    # 这一轮必须调到的 tool（≥1 次）
      - search_customer
      - create_contract_draft
    expected_tool_calls_zero:        # 这一轮一定不能调（防 LLM 越权 / 误调）
      - confirm_action               # preview turn 不该调 confirm
    final_message_contains_any:      # 最终回复必须含其中一个
      - preview
      - 确认
      - 是
```

- [ ] **Step 1: 写 6 个 ReAct fixture（覆盖钉钉实测全部场景）**

```yaml
# backend/tests/react/fixtures/scenarios/realllm/story1_chat.yaml
name: 闲聊场景（不调任何 tool）
turns:
  - input: "在吗"
    expected_tool_calls_zero:
      - search_customer
      - search_product
      - create_contract_draft
      - create_quote_draft
      - confirm_action
    final_message_contains_any: ["在", "你好", "需要"]


# backend/tests/react/fixtures/scenarios/realllm/story2_query.yaml
name: 单轮查询库存
turns:
  - input: "查 SKG 有哪些产品"
    expected_tool_calls_at_least:
      - search_product
    final_message_contains_any: ["SKG", "F1", "H5", "K5", "X1"]


# backend/tests/react/fixtures/scenarios/realllm/story3_contract_one_round.yaml
name: 单轮合同（信息一次到齐）— 两步：preview + confirm
turns:
  # T1: 用户提交完整请求 → bot 调 search_customer/product + create_contract_draft 拿 pending
  - input: "给阿里做合同 X1 10 个 300，地址北京海淀，张三 13800001111"
    expected_tool_calls_at_least:
      - search_customer
      - create_contract_draft
    expected_tool_calls_zero:
      - confirm_action  # preview turn 不该 confirm
    final_message_contains_any: ["preview", "确认", "回'是'", "请回"]

  # T2: 用户回"是"→ bot 调 confirm_action 真生成 docx
  - input: "是"
    expected_tool_calls_at_least:
      - confirm_action
    final_message_contains_any: ["draft_id", "已生成", "已发送"]


# backend/tests/react/fixtures/scenarios/realllm/story4_reuse_previous.yaml
name: 复用上份合同（"同样给 X 也来一份"）— 关键场景,是 ReAct 设计核心
turns:
  # T1: 先做翼蓝合同
  - input: "给翼蓝做合同 X1 10 个 300，地址北京海淀，张三 13800001111"
    expected_tool_calls_at_least: [search_customer, create_contract_draft]
  # T2: confirm
  - input: "是"
    expected_tool_calls_at_least: [confirm_action]
  # T3: 复用 — 给得帆同样内容
  - input: "同样的内容给得帆也做一份"
    expected_tool_calls_at_least:
      - get_recent_drafts        # ← 关键 tool
      - search_customer          # 找得帆 id
      - create_contract_draft    # 提交新 pending
  # T4: confirm 第二份
  - input: "是"
    expected_tool_calls_at_least: [confirm_action]


# backend/tests/react/fixtures/scenarios/realllm/story5_missing_fields.yaml
name: 缺字段 — bot 不调 tool 直接自然语言询问
turns:
  - input: "做合同 X1 10 个"
    expected_tool_calls_zero:
      - create_contract_draft     # 信息不全不该提交 pending
    final_message_contains_any: ["客户", "地址", "联系人", "电话", "请告诉"]
  - input: "翼蓝,300 块,北京海淀,张三,13800001111"
    expected_tool_calls_at_least:
      - search_customer
      - create_contract_draft


# backend/tests/react/fixtures/scenarios/realllm/story6_customer_switch.yaml
name: 中途切换客户 — 别用旧 customer 生成新合同
turns:
  # T1: 翼蓝合同 preview
  - input: "给翼蓝做合同 X1 10 个 300, 北京海淀, 张三, 13800001111"
    expected_tool_calls_at_least: [search_customer, create_contract_draft]
  # T2: 用户改主意,切换得帆（别按上轮翼蓝继续,要重新 search_customer）
  - input: "算了,改成得帆"
    expected_tool_calls_at_least:
      - search_customer           # 必须重新搜得帆
      - create_contract_draft     # 用新 customer_id 生成新 pending
```

- [ ] **Step 2: 实现 test 驱动**

```python
# backend/tests/react/test_realllm_eval.py
"""ReAct agent 真 LLM eval（按 ReAct plan-then-execute 流程设计的新 fixture）。

跑：DEEPSEEK_API_KEY=xxx pytest -m realllm tests/react/test_realllm_eval.py
"""
import os
import pytest
import yaml
from pathlib import Path

REACT_SCENARIOS = (
    Path(__file__).parent / "fixtures" / "scenarios" / "realllm"
)


@pytest.fixture(scope="session", autouse=True)
def _enforce_release_gate_or_skip():
    """release gate 模式下没 DEEPSEEK_API_KEY 必须 fail（不能 skip 当绿）。

    用法:
      - dev 模式（默认）: 没 API key 跳过 case（绿 + skipped）
      - release gate 模式: 设 HUB_REACT_RELEASE_GATE=1,没 key 直接 fail
        （pytest 退出非 0,CI 不会误把 skipped 当通过）
    """
    is_release_gate = os.environ.get("HUB_REACT_RELEASE_GATE") == "1"
    has_key = bool(os.environ.get("DEEPSEEK_API_KEY"))
    if is_release_gate and not has_key:
        pytest.fail(
            "release gate 模式 (HUB_REACT_RELEASE_GATE=1) 必须设 DEEPSEEK_API_KEY,"
            "不允许 skipped 当绿"
        )


def _scenario_files() -> list[str]:
    return sorted(p.name for p in REACT_SCENARIOS.glob("story*.yaml"))


@pytest.mark.realllm
def test_eval_has_minimum_scenario_count():
    """release gate 边界：fixture 数量必须 >= 6（钉钉实测覆盖度下限）。
    防 fixture 被无意删除导致 release gate 通过案例数过少。

    **必须**带 `@pytest.mark.realllm` mark — release gate 命令是
    `pytest -m realllm tests/react/test_realllm_eval.py`,没 mark 会被 deselect
    （fixture 只剩 1-5 条都 pass 时 release gate 仍可能误绿）。
    """
    files = _scenario_files()
    assert len(files) >= 6, (
        f"ReAct 真 LLM eval fixture 数量不足: {len(files)} < 6\n"
        f"现有: {files}\n钉钉实测覆盖度下限是 6 个 story（chat / query / contract /"
        f" reuse / missing / switch）"
    )


@pytest.mark.realllm
@pytest.mark.parametrize("yaml_file", _scenario_files())
@pytest.mark.asyncio
async def test_realllm_react_scenario(yaml_file, real_react_agent_factory):
    """每个 turn 检查：
      - expected_tool_calls_at_least: tool 被调 >=1 次
      - expected_tool_calls_zero: tool 必须 0 次
      - final_message_contains_any: 最终自然语言回复含其一
    """
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip(
            "无 DEEPSEEK_API_KEY (dev 模式) — release gate 设 "
            "HUB_REACT_RELEASE_GATE=1 严格化（缺 key 时 fail 不 skip）"
        )

    scenario = yaml.safe_load(
        (REACT_SCENARIOS / yaml_file).read_text(encoding="utf-8"),
    )
    agent, tool_log = real_react_agent_factory

    case_id = yaml_file.replace(".yaml", "")
    conv_id = f"react-eval-{case_id}"
    user_id = 1

    for i, turn in enumerate(scenario["turns"], 1):
        # 记录本轮 tool call 起始位置（之后 slice）
        before = len(tool_log)
        reply = await agent.run(
            user_message=turn["input"],
            hub_user_id=user_id, conversation_id=conv_id,
            acting_as=None, channel_userid="test",
        )
        turn_tool_calls = [n for n, _ in tool_log[before:]]

        for must_have in turn.get("expected_tool_calls_at_least", []):
            assert must_have in turn_tool_calls, (
                f"{yaml_file} turn {i}: 期望调到 {must_have},实际本轮 tools={turn_tool_calls}, "
                f"reply={reply!r}"
            )
        for must_zero in turn.get("expected_tool_calls_zero", []):
            assert must_zero not in turn_tool_calls, (
                f"{yaml_file} turn {i}: 不应调 {must_zero},实际本轮 tools={turn_tool_calls}"
            )
        if turn.get("final_message_contains_any"):
            assert any(s in (reply or "") for s in turn["final_message_contains_any"]), (
                f"{yaml_file} turn {i}: reply 不含任一关键词 {turn['final_message_contains_any']}, "
                f"实际 reply={reply!r}"
            )
```

**release gate 用法**:
```bash
# dev 模式（缺 key 跳过 case）
DEEPSEEK_API_KEY=$KEY pytest -m realllm tests/react/test_realllm_eval.py

# release gate 模式（缺 key 直接 fail; 任何 skipped 也 fail）
HUB_REACT_RELEASE_GATE=1 DEEPSEEK_API_KEY=$KEY pytest -m realllm tests/react/test_realllm_eval.py
```

CI / staging 上必须用 release gate 模式跑,确保 6 个 fixture 全部真跑（不被
"skipped" 蒙混过关）。Plan 6 v9 staging checklist 同样要求 0 skipped。

**关键**：在 `backend/tests/react/conftest.py` 加 `pytest_sessionfinish` hook,
release gate 模式下任何 skipped case → exit code 非 0：

注：用 `pytest_sessionfinish` **不是** `pytest_terminal_summary`。后者是只读的 reporting hook,
session.exitstatus 此时已经 commit 给 main loop（改了不生效）。`pytest_sessionfinish(session, exitstatus)`
拿到 session 还能改 `session.exitstatus`（pytest 7.x 实测有效）+ 用 `pytest_terminal_summary`
只做展示（红色横幅 + 列出 skipped case）。

```python
# backend/tests/react/conftest.py 追加
import os


def _release_gate_active() -> bool:
    return os.environ.get("HUB_REACT_RELEASE_GATE") == "1"


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """release gate 模式下展示横幅 + 列出 skipped。**不修改 exit code（这里改不动）**。"""
    if not _release_gate_active():
        return
    skipped = terminalreporter.stats.get("skipped", [])
    if not skipped:
        return
    terminalreporter.write_sep("=", "RELEASE GATE FAILED", red=True, bold=True)
    terminalreporter.write_line(
        f"❌ HUB_REACT_RELEASE_GATE=1 下不允许 skipped (本次 {len(skipped)} 个 skipped):",
    )
    for sk in skipped[:10]:
        terminalreporter.write_line(f"  - {sk.nodeid}: {sk.longreprtext[:200]}")


def pytest_sessionfinish(session, exitstatus):
    """release gate 模式下: skipped > 0 → 强制 session.exitstatus 非 0。

    `pytest_sessionfinish` 是 pytest 改 exit code 的官方入口（终端尚未 commit）。
    """
    if not _release_gate_active():
        return
    # session.testsfailed = 失败数；想看 skipped 数得从 reporter 拿
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    skipped = reporter.stats.get("skipped", [])
    if skipped and session.exitstatus == 0:
        session.exitstatus = 1  # pytest.ExitCode.TESTS_FAILED
```

- [ ] **Step 2: 加 fixture（real_react_agent_factory）到 conftest**

参考 `backend/tests/agent/conftest.py` 中现有的 `real_graph_agent_factory`（搜 `def real_graph_agent_factory` 找到完整实现）。复制并按 react 改造：

1. 把 `GraphAgent(...)` 换成 `ReActAgent(chat_model=..., tools=ALL_TOOLS, checkpointer=...)`
2. 把 `register_all_tools(tool_registry)` 删掉，改成 `set_confirm_gate(confirm_gate)`
3. 用 `tool_log` MagicMock 钩在 each tool 的 fn 头部记录调用（同 graph 时代做法）

```python
# backend/tests/react/conftest.py 追加（具体代码按 tests/agent/conftest.py 改造）
@pytest.fixture
async def real_react_agent_factory(monkeypatch):
    """真 LLM ReActAgent + 真 ConfirmGate（fakeredis）+ 真 ERP4 adapter。
    返 (agent, tool_log)。tool_log 是 list[(tool_name, args)] 累计所有 tool 调用。

    实施时参考 backend/tests/agent/conftest.py:real_graph_agent_factory 改造。
    """
    from hub.agent.react.agent import ReActAgent
    from hub.agent.react.llm import build_chat_model
    from hub.agent.react.tools import ALL_TOOLS
    from hub.agent.react.tools._confirm_helper import set_confirm_gate
    # ... 其它 imports（fakeredis / ConfirmGate / Erp4Adapter 等）按 tests/agent/conftest.py
    # 复用：
    #   - DeepSeek API key 检查 + skip
    #   - fakeredis 起 ConfirmGate + set_confirm_gate(gate)
    #   - 真 Erp4Adapter（用本地 ERP-4 docker 服务）
    #   - tool_log 钩子（patch 每个 tool fn 记录调用）
    #
    # ReActAgent 构造：
    chat_model = build_chat_model(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com/beta",
        model="deepseek-v4-flash",  # 或测试用便宜模型
    )
    agent = ReActAgent(
        chat_model=chat_model,
        tools=ALL_TOOLS,
        checkpointer=None,  # eval 不持久化（每个 case 独立 conv_id 隔离）
    )
    yield agent, tool_log
    # cleanup ...
```

- [ ] **Step 3: 跑（如有 key）**

```bash
DEEPSEEK_API_KEY=$KEY .venv/bin/python -m pytest tests/react/test_realllm_eval.py -v -m realllm
# Expected: 6 个 story 全 pass(前提：真 fixture 完整)
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/react/test_realllm_eval.py backend/tests/react/conftest.py
git commit -m "test(hub): 真 LLM eval（@realllm）— 复用 6 story yaml,放宽断言"
```

---

## Task 5.4: 删旧代码（GraphAgent + 子图 + 节点 + 旧测试）

钉钉手测 4 场景 + 真 LLM eval 全部通过后才执行。

**Files:**
- Delete: `backend/hub/agent/graph/agent.py`
- Delete: `backend/hub/agent/graph/router.py`
- Delete: `backend/hub/agent/graph/config.py`
- Delete: `backend/hub/agent/graph/nodes/` 整目录
- Delete: `backend/hub/agent/graph/subgraphs/` 整目录
- Modify: `backend/hub/agent/graph/state.py` 只保留 Intent / CustomerInfo / ProductInfo / ContractItem / ShippingInfo（**显式删 `AgentState` Pydantic 类整个定义** + 7 个子图 State alias）
- Delete: `backend/tests/agent/conftest.py` （**关键**：顶层 `from hub.agent.graph.agent import GraphAgent`,
  graph/agent.py 删后任何 tests/agent/* 都 collect ImportError）
- Delete: `backend/tests/agent/test_node_*.py` / `test_subgraph_*.py` / `test_graph_*.py`
- Delete: `backend/tests/agent/test_per_user_isolation.py`（GraphAgent state per-user 测试）
- Delete: `backend/tests/agent/test_acceptance_scenarios.py` / `test_realllm_eval.py`（v9 旧版本,Phase 5 在 tests/react/ 重写）

**保留**（不依赖 graph 包）:
- `tests/agent/test_deepseek_llm_client.py` / `test_cache_hit_rate.py` / `test_fallback_protocol.py`
- `tests/agent/test_registry_strict.py` / `test_tool_registry_complete.py` / `test_strict_mode_validation.py` / `test_read_tool_sentinel.py` / `test_write_tool_sentinel.py`（测 ToolRegistry / 工具 sentinel,跟 GraphAgent 无关）

- [ ] **Step 1: 删文件**

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent

# 删 graph 流程层
rm backend/hub/agent/graph/agent.py
rm backend/hub/agent/graph/router.py
rm backend/hub/agent/graph/config.py
rm -rf backend/hub/agent/graph/nodes/
rm -rf backend/hub/agent/graph/subgraphs/

# 删测试
rm backend/tests/agent/conftest.py            # 顶层 import GraphAgent,必须删
rm backend/tests/agent/test_node_*.py
rm backend/tests/agent/test_subgraph_*.py
rm backend/tests/agent/test_graph_*.py
rm backend/tests/agent/test_per_user_isolation.py
rm backend/tests/agent/test_acceptance_scenarios.py
rm backend/tests/agent/test_realllm_eval.py

# 验证保留下来的 tests/agent/ 测试不依赖被删的 conftest.py / GraphAgent
.venv/bin/python -m pytest backend/tests/agent/ --co -q 2>&1 | tail -10
# Expected: 仅 test_deepseek_llm_client / test_cache_hit_rate / test_fallback_protocol /
#           test_registry_strict / test_tool_registry_complete / test_strict_mode_validation /
#           test_read_tool_sentinel / test_write_tool_sentinel 被收集,无 ImportError
```

- [ ] **Step 2: 精简 state.py**

```python
# backend/hub/agent/graph/state.py 改成只留数据类（tool 内部仍用 CustomerInfo 等）。
# **删除内容**（v9 残留）：
#   - AgentState 主 Pydantic 类（30+ 业务字段）
#   - ContractState / QuoteState / VoucherState / AdjustPriceState / AdjustStockState /
#     ChatState / QueryState 等 7 个子图 alias 类
#   - 任何 model_validator / field_validator / 业务字段（resolved / candidates / extracted_hints 等）
"""HUB 业务数据类（v10：仅 react tools 内部用,无 LangGraph state schema）。"""
from __future__ import annotations
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel


class Intent(str, Enum):
    """[v9 残留,本 v10 不再用 LLM router；保留 enum 给 audit / log 用]"""
    CHAT = "chat"
    QUERY = "query"
    CONTRACT = "contract"
    QUOTE = "quote"
    VOUCHER = "voucher"
    ADJUST_PRICE = "adjust_price"
    ADJUST_STOCK = "adjust_stock"
    CONFIRM = "confirm"
    UNKNOWN = "unknown"


class CustomerInfo(BaseModel):
    id: int
    name: str
    address: str | None = None
    tax_id: str | None = None
    phone: str | None = None


class ProductInfo(BaseModel):
    id: int
    name: str
    sku: str | None = None
    color: str | None = None
    spec: str | None = None
    list_price: Decimal | None = None


class ContractItem(BaseModel):
    product_id: int
    name: str
    qty: int
    price: Decimal


class ShippingInfo(BaseModel):
    address: str | None = None
    contact: str | None = None
    phone: str | None = None
```

- [ ] **Step 3: import 检查**

```bash
cd backend && .venv/bin/python -c "from hub.agent.react.agent import ReActAgent; from hub.agent.react.tools import ALL_TOOLS; print(len(ALL_TOOLS))"
# Expected: 16

# 确保没有 import 漏掉
# 注：tests/agent/test_realllm_eval.py 已在 Step 1 rm 掉,不需要 --ignore
.venv/bin/python -m pytest tests/ -q -m "not realllm" --co 2>&1 | tail -5
# Expected: collection 完成无 ImportError
```

- [ ] **Step 4: 跑全量测试**

```bash
cd backend && .venv/bin/python -m pytest tests/ -q -m "not realllm"
# Expected: 全 pass(数量比 v9 少很多,因为删了节点/子图测试)
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(hub): v10 删 GraphAgent + 7 子图 + 节点链 + 旧测试

# 删除清单
- backend/hub/agent/graph/agent.py（GraphAgent 主类）
- backend/hub/agent/graph/router.py
- backend/hub/agent/graph/config.py
- backend/hub/agent/graph/nodes/* (10 个节点)
- backend/hub/agent/graph/subgraphs/* (7 个子图)
- backend/tests/agent/test_node_*.py / test_subgraph_*.py / test_graph_*.py

# 精简
- backend/hub/agent/graph/state.py — 只保留 5 个数据类（CustomerInfo 等）

# 业务底层全部保留（erp_tools / generate_tools / draft_tools / confirm_gate / ...）
# DingTalk handler / 权限 / 审计 / tool_call_log 全部不动
"
```

---

## Task 5.5: 端到端 docker 部署 + 钉钉真测

- [ ] **Step 1: 部署**

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent
COMPOSE_PROJECT_NAME=hub docker compose build hub-worker
COMPOSE_PROJECT_NAME=hub docker compose up -d --force-recreate hub-worker

until docker logs hub-hub-worker-1 2>&1 | grep -qE "Worker.*启动"; do sleep 2; done
docker logs hub-hub-worker-1 2>&1 | head -10
```

- [ ] **Step 2: 钉钉发 Task 5.2 的 4 场景**

更新 staging 文档实测结果。每个场景记录：
- bot 回复
- worker 日志（`grep "tool_call_log" / Traceback / Error`）
- 是否收到 docx（场景 1、2 必须收到）

- [ ] **Step 3: 验收**

成功标准（spec §14）：
- ✅ 4 个核心场景全部通过
- ✅ 全量测试 pass
- ✅ 代码量减少（用 `cloc backend/hub` 对比 v9）
- ✅ 加新意图测试：手工临时加 `create_purchase_order` tool（5 行代码 + prompt 一行描述），看是否 30 分钟内能跑通

- [ ] **Step 4: 总结 commit**

```bash
git add docs/superpowers/staging/2026-05-03-react-acceptance-manual.md
git commit -m "docs(staging): React agent v10 端到端验收完成"
```

---

# 验收 Checklist

实施完成时勾选：

- [ ] Phase 1 完成（context / 依赖核验 / chat_model）
- [ ] Phase 2 完成（10 read tools，含 get_recent_drafts）
- [ ] Phase 3 完成（5 write tools + confirm_action + WRITE_TOOL_DISPATCH）
- [ ] Phase 4 完成（system prompt + ReActAgent + worker.py 改造）
- [ ] Phase 5 完成（acceptance / 手测 / 真 LLM eval / 删旧代码）
- [ ] 钉钉 4 场景全通过（单轮做合同 / 复用上份 / 闲聊 / 缺字段）
- [ ] 全量测试 pass
- [ ] 代码量 -40%（v9 删除的子图/节点/state/测试,vs 新增的 react/* + tests/react/*）
- [ ] 加新意图 < 30 分钟（手工添加一个新 tool 验证）
