# GraphAgent Implementation Plan（v1.16）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 hub 钉钉机器人的 ChainAgent 从"单循环 LLM + prompt 准则"重构为基于 LangGraph state machine 的 GraphAgent，让对话像主流 LLM 那样自然，并通过架构（不是 prompt patch）根除流程类 bug 80%。

**Architecture:** LangGraph 7 个 subgraph（chat/query/contract/quote/voucher/adjust_price/adjust_stock）+ 轻量 LLM router（Anthropic Routing pattern）+ DeepSeekLLMClient 适配层（封装 beta endpoint / prefix completion / strict mode / thinking / KV cache / 600s timeout / 指数退避 / 5 种 finish_reason / 按 tool 类型分级 fallback）。所有跨轮状态以 `(conversation_id, hub_user_id)` 复合 key 隔离。

**Tech Stack:**
- Python 3.11 + asyncio
- LangGraph 0.x（state machine）+ LangChain core（仅 Runnable 协议，**不**用 ChatOpenAI）
- DeepSeek API beta endpoint（`https://api.deepseek.com/beta`，model: `deepseek-v4-flash`）
- Pydantic v2（typed state schemas）
- pytest + pytest-asyncio（已有 660+ 单测基础）
- Redis（SessionMemory + ConfirmGate + LangGraph checkpointer）
- Postgres + Tortoise ORM（ConversationLog / ToolCallLog / PendingAction）

**Spec reference:** `docs/superpowers/specs/2026-05-02-graph-agent-design.md` (v3.4, commit `b860d6e`)

**Worktree:** `/Users/lin/Desktop/hub/.worktrees/plan6-agent` on branch `feature/plan6-agent`

---

## File Structure 映射

### 新建文件

```
backend/hub/agent/
  graph/
    __init__.py
    agent.py                  # GraphAgent 顶层入口（编译 graph + run）
    state.py                  # AgentState / ContractState / QuoteState / 等 Pydantic schemas
    router.py                 # router_node（prefix JSON + Intent enum）
    config.py                 # thread_id 工具函数（per-(conv,user) 复合 key）
    subgraphs/
      __init__.py
      chat.py                 # chat_subgraph（temperature=1.3，0 tool）
      query.py                # query_subgraph（11 tool 只读）
      contract.py             # contract_subgraph（5 节点）
      quote.py                # quote_subgraph（3 节点）
      voucher.py              # voucher_subgraph（写 + ConfirmGate）
      adjust_price.py         # adjust_price_subgraph（preview thinking on）
      adjust_stock.py         # adjust_stock_subgraph
    nodes/
      __init__.py
      resolve_customer.py     # 跨子图复用：search_customers
      resolve_products.py     # 跨子图复用：search_products
      validate_inputs.py      # thinking on，价格/数量/items 推理
      ask_user.py             # 缺信息问回
      format_response.py      # prefix 强制开头
      confirm.py              # 多 pending 三分支节点（0 / 1 / >1）

  prompt/
    intent_router.py          # ROUTER_SYSTEM_PROMPT（含 50 case few-shots）
    subgraph_prompts/
      __init__.py
      chat.py
      query.py
      contract.py
      quote.py
      voucher.py
      adjust_price.py
      adjust_stock.py
```

### 改造文件

```
backend/hub/agent/
  llm_client.py               # → DeepSeekLLMClient: beta + prefix + strict + thinking + cache usage + 600s + 指数退避 + 5 finish_reason + 按 tool class fallback
  tools/
    registry.py               # 加 strict mode 支持 + subgraph_filter
    confirm_gate.py           # API 升级到 list_pending_for_context + claim 校验 conversation_id + PendingAction.conversation_id 必填
    erp_tools.py              # 9 个 tool schema 加 strict + sentinel 入口归一化
    generate_tools.py         # 3 个 tool schema 加 strict + sentinel 入口归一化
    draft_tools.py            # 4 个 tool schema 加 strict + sentinel 入口归一化
    analyze_tools.py          # 1 个 tool schema 加 strict + sentinel 入口归一化
  prompt/
    builder.py                # 删 12 条行为准则 (3a-3l)，留业务词典 / 同义词 helper
  memory/
    session.py                # append 加剥离 reasoning_content
  handlers/
    dingtalk_inbound.py       # ChainAgent → GraphAgent 切换
```

### 删除文件

```
backend/hub/agent/
  chain_agent.py              # 505 行，整体替换
  context_builder.py          # 363 行，state schema + 1M context 替代
backend/tests/
  test_chain_agent.py         # 旧测试（部分迁移到 graph/，部分弃用）
```

### 新建测试

```
backend/tests/agent/
  __init__.py
  test_graph_state.py
  test_graph_router.py
  test_graph_router_accuracy.py     # 50 case 准确率
  test_subgraph_chat.py
  test_subgraph_query.py
  test_subgraph_contract.py
  test_subgraph_quote.py
  test_subgraph_voucher.py
  test_subgraph_adjust_price.py
  test_subgraph_adjust_stock.py
  test_node_resolve_customer.py
  test_node_resolve_products.py
  test_node_validate_inputs.py
  test_node_confirm.py              # 多 pending + 跨会话隔离
  test_per_user_isolation.py        # LangGraph thread_id checkpoint
  test_strict_mode_validation.py
  test_prefix_completion.py
  test_cache_hit_rate.py
  test_fallback_protocol.py
  test_finish_reason_handling.py
  test_acceptance_scenarios.py      # 6 个用户故事 yaml + parametrize
backend/tests/integration/
  test_deepseek_compat.py           # M0 真 beta 集成验证（pytest mark：realllm）
```

---

## Phase / Milestone 索引

| Phase | Spec milestone | 内容 | 工时 | Task 数 |
|---|---|---|---|---|
| 0 | M0 | 基建 + DeepSeek 兼容性验证 | 1 d | 8 |
| 1 | M1 | Router + chat | 0.5 d | 5 |
| 2 | M2 | Tool strict 化 + sentinel 归一化 | 0.5 d | 6 |
| 3 | M3 | query 子图 | 1 d | 3 |
| 4 | M4 | contract 子图（最复杂） | 1.5 d | 10 |
| 5 | M5 | 写操作子图（voucher / adjust_price / adjust_stock） | 1.5 d | 8 |
| 6 | M6 | quote 子图 | 0.5 d | 3 |
| 7 | M7 | 接入 + 旧代码删除 | 0.5 d | 5 |
| 8 | M8 | 6 故事 acceptance + 真 LLM eval + cache 命中率统计 | 1 d | 6 |
| **总** | | | **8 d** | **54** |

---

## Phase 0：基建 + DeepSeek 兼容性验证（M0，1 天）

**Goal**：把 LangGraph 装好、把 DeepSeekLLMClient 写完、用真 beta endpoint 验证 spec 的 4 个关键能力（prefix / strict sentinel / strict anyOf-null 实验 / thinking + tools 同时启用 / KV cache usage / `insufficient_system_resource`）。

**Exit criteria**：
1. `pytest tests/integration/test_deepseek_compat.py -v -m realllm` 全过
2. `pytest tests/agent/test_graph_state.py -v` 全过
3. `tests/agent/test_per_user_isolation.py` 同 conv 不同 user 的 LangGraph checkpoint 互不可见

### Task 0.1: 创建 graph/ 目录骨架 + 安装依赖

**Files:**
- Create: `backend/hub/agent/graph/__init__.py`
- Create: `backend/hub/agent/graph/subgraphs/__init__.py`
- Create: `backend/hub/agent/graph/nodes/__init__.py`
- Create: `backend/hub/agent/prompt/subgraph_prompts/__init__.py`
- Modify: `backend/pyproject.toml`（加 langgraph）

- [ ] **Step 1: 加 langgraph 依赖**

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend
# 在 pyproject.toml 的 [project.dependencies] 加：
#   "langgraph>=0.2.0,<0.3",
#   "langchain-core>=0.3.0,<0.4",
```

Edit `backend/pyproject.toml`，找到 `[project]` → `dependencies = [...]`，追加两行后保存。

- [ ] **Step 2: 安装依赖**

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend
uv sync  # 或 pip install -e .
```

Expected: `langgraph` 在 `pip list | grep langgraph` 出现。

- [ ] **Step 3: 创建空 __init__.py 文件**

```bash
mkdir -p backend/hub/agent/graph/subgraphs
mkdir -p backend/hub/agent/graph/nodes
mkdir -p backend/hub/agent/prompt/subgraph_prompts
mkdir -p backend/tests/agent
mkdir -p backend/tests/integration
touch backend/hub/agent/graph/__init__.py
touch backend/hub/agent/graph/subgraphs/__init__.py
touch backend/hub/agent/graph/nodes/__init__.py
touch backend/hub/agent/prompt/subgraph_prompts/__init__.py
touch backend/tests/agent/__init__.py
touch backend/tests/integration/__init__.py
```

- [ ] **Step 4: 验证 import 不爆**

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend
python -c "import langgraph; import langchain_core; from hub.agent import graph; print('OK')"
```

Expected: `OK`，无 ImportError。

- [ ] **Step 5: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/pyproject.toml backend/hub/agent/graph backend/hub/agent/prompt/subgraph_prompts backend/tests/agent backend/tests/integration
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
feat(hub): GraphAgent 骨架目录 + langgraph 依赖（Plan 6 v9 Task 0.1）

加 langgraph + langchain-core 依赖。创建 graph/、graph/subgraphs/、
graph/nodes/、prompt/subgraph_prompts/ 空目录，准备后续 Phase 1-7 实现。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 0.2: 写 Pydantic state schemas

**Files:**
- Create: `backend/hub/agent/graph/state.py`
- Create: `backend/tests/agent/test_graph_state.py`

**Spec ref:** §4 State Schema

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/agent/test_graph_state.py
from decimal import Decimal
import pytest
from hub.agent.graph.state import (
    AgentState, Intent, ContractState, ContractItem,
    CustomerInfo, ProductInfo, ShippingInfo,
)


def test_agent_state_minimal():
    state = AgentState(
        user_message="hi",
        hub_user_id=1,
        conversation_id="c1",
    )
    assert state.intent is None
    assert state.acting_as is None


def test_intent_lowercase_value():
    assert Intent.CHAT.value == "chat"
    assert Intent.CONTRACT.value == "contract"
    # 必须是 lowercase value，否则 router_node 解析会全部落 UNKNOWN
    assert all(i.value == i.value.lower() for i in Intent)


def test_contract_state_with_items():
    state = ContractState(
        user_message="给阿里做合同 X1 10 个 300",
        hub_user_id=1,
        conversation_id="c1",
        extracted_hints={"customer_name": "阿里"},
    )
    state.customer = CustomerInfo(id=10, name="阿里")
    state.items.append(ContractItem(product_id=1, name="X1", qty=10, price=Decimal("300")))
    assert state.customer.name == "阿里"
    assert state.items[0].price == Decimal("300")


def test_shipping_info_all_optional():
    s = ShippingInfo()
    assert s.address is None and s.contact is None and s.phone is None
```

- [ ] **Step 2: 跑测试，应该失败**

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend
pytest tests/agent/test_graph_state.py -v
```

Expected: ImportError on `hub.agent.graph.state`。

- [ ] **Step 3: 实现 state.py**

```python
# backend/hub/agent/graph/state.py
"""GraphAgent state schemas — Pydantic typed，跨节点 / 跨子图共享。"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class Intent(str, Enum):
    """Router 意图分类。value 必须 lowercase — router_node 用 Intent(value) 解析。"""
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


class AgentState(BaseModel):
    """所有子图共享的 state。

    P1-A v1.4 关键：跨轮选择字段（candidate_customers / candidate_products / customer / products / items）
    **必须**在父 AgentState 上声明 —— LangGraph StateGraph 用父 schema 做 checkpoint，
    子图返回的字段如果父 schema 没有 → 不会写入父 checkpoint → 上一轮的 candidate 会丢，
    pre_router 永远 peek 不到，"选 1" 就回不到 contract 子图。

    子图 (ContractState/QuoteState 等) 仍可继承加自己专属字段（如 draft_id），
    但**所有"下一轮可能用得上"的跨轮字段**都在 AgentState 上。
    """
    user_message: str
    hub_user_id: int
    conversation_id: str
    acting_as: int | None = None
    channel_userid: str | None = None

    intent: Intent | None = None
    final_response: str | None = None
    file_sent: bool = False
    errors: list[str] = Field(default_factory=list)

    # confirm 链路（v1.2 P1-A）
    confirmed_subgraph: str | None = None       # e.g. "adjust_price"
    confirmed_action_id: str | None = None      # 完整 32-hex
    confirmed_payload: dict | None = None       # canonical {tool_name, args}

    # P1-A v1.6：active_subgraph 持久化候选来源 — 不能用 intent 判，run() 每轮都把 intent reset 成 None。
    # 当 quote 流程留下 candidate_customers/products 时，下一轮"选 2"必须回 quote，不是兜底 contract。
    active_subgraph: str | None = None  # "contract" / "quote"，写候选时一并写；候选清空时一并清

    # 跨轮选择字段（v1.4 P1-A 提升 / v1.5 P1-C 把 shipping 也加进来）
    # 用户可能在第 1 轮就把地址给齐了，第 2 轮选候选客户/产品时父图 checkpoint
    # 必须保留 shipping，否则 validate_inputs 会重新问地址。
    extracted_hints: dict = Field(default_factory=dict)
    customer: CustomerInfo | None = None
    candidate_customers: list[CustomerInfo] = Field(default_factory=list)
    products: list[ProductInfo] = Field(default_factory=list)
    candidate_products: dict[str, list[ProductInfo]] = Field(default_factory=dict)
    items: list[ContractItem] = Field(default_factory=list)
    shipping: ShippingInfo = Field(default_factory=ShippingInfo)  # ← v1.5 P1-C 提升
    missing_fields: list[str] = Field(default_factory=list)

    # P2-A v1.8：draft_id / quote_id 也提升到 AgentState — 否则跑完合同/报价后
    # 父图 snapshot 拿不到 ID（同 v1.4 candidate_* 教训：父 schema 不含 → checkpoint 不存）。
    # eval driver / 端到端测试都从父图 snapshot 读 draft_id 验合同生成。
    draft_id: int | None = None
    quote_id: int | None = None


class ContractState(AgentState):
    """contract_subgraph state — 全部跨轮字段都在父类，子类无业务专属字段。
    保留这个类是为了类型签名清晰（contract 子图节点接收的是 ContractState 而非裸 AgentState）。"""
    pass


class QuoteState(AgentState):
    """quote_subgraph state — 同上，结构上和 ContractState 等价。"""
    pass


class AdjustPriceState(AgentState):
    """adjust_price_subgraph state."""
    extracted_hints: dict = Field(default_factory=dict)
    customer: CustomerInfo | None = None
    product: ProductInfo | None = None
    old_price: Decimal | None = None
    new_price: Decimal | None = None
    history_prices: list[Decimal] = Field(default_factory=list)
    pending_action_id: str | None = None


class AdjustStockState(AgentState):
    """adjust_stock_subgraph state."""
    extracted_hints: dict = Field(default_factory=dict)
    product: ProductInfo | None = None
    delta_qty: int | None = None
    reason: str | None = None
    pending_action_id: str | None = None


class VoucherState(AgentState):
    """voucher_subgraph state — 出库 / 入库凭证。

    P1-B v1.4：voucher_type 必须从用户消息 / extracted_hints 解析，**不能**硬编码 outbound。
    """
    order_id: int | None = None
    voucher_type: str | None = None  # "outbound" / "inbound"，必填（preview 之前一定要有值）
    voucher_id: int | None = None
    pending_action_id: str | None = None
```

- [ ] **Step 4: 跑测试，应该过**

```bash
pytest tests/agent/test_graph_state.py -v
```

Expected: 4 个测试全 PASS。

- [ ] **Step 5: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/graph/state.py backend/tests/agent/test_graph_state.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
feat(hub): graph state schemas（Plan 6 v9 Task 0.2）

定义 Intent enum + AgentState + 7 个子图 state（Contract/Quote/AdjustPrice/
AdjustStock/Voucher）。Intent value 强制 lowercase 防 router 解析全落 UNKNOWN。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 0.3: 写 thread_id 工具 + per-user 隔离基础

**Files:**
- Create: `backend/hub/agent/graph/config.py`
- Create: `backend/tests/agent/test_per_user_isolation.py`

**Spec ref:** §2.1 Per-User 状态隔离

- [ ] **Step 1: 写失败测试（基础工具函数）**

```python
# backend/tests/agent/test_per_user_isolation.py
import pytest
from hub.agent.graph.config import build_thread_id, parse_thread_id


def test_build_thread_id_basic():
    assert build_thread_id(conversation_id="c1", hub_user_id=42) == "c1:42"


def test_build_thread_id_rejects_empty():
    with pytest.raises(ValueError):
        build_thread_id(conversation_id="", hub_user_id=42)
    with pytest.raises(ValueError):
        build_thread_id(conversation_id="c1", hub_user_id=0)


def test_parse_roundtrip():
    tid = build_thread_id(conversation_id="conv-abc", hub_user_id=7)
    conv, user = parse_thread_id(tid)
    assert conv == "conv-abc" and user == 7


def test_same_conv_different_user_different_thread_id():
    """核心约束：同一 conv，不同 user → 不同 thread_id（LangGraph checkpoint 隔离）。"""
    a = build_thread_id(conversation_id="group-1", hub_user_id=1)
    b = build_thread_id(conversation_id="group-1", hub_user_id=2)
    assert a != b
```

- [ ] **Step 2: 跑测试，应该失败**

```bash
pytest tests/agent/test_per_user_isolation.py -v
```

Expected: ImportError on `hub.agent.graph.config`。

- [ ] **Step 3: 实现 config.py**

```python
# backend/hub/agent/graph/config.py
"""LangGraph config helper — per-(conversation_id, hub_user_id) thread_id 复合 key。

强约束：所有 LangGraph checkpoint / SessionMemory / ConfirmGate 都以 (conv, user) 为边界，
钉钉群聊里不同用户必须互不可见（spec §2.1）。
"""
from __future__ import annotations


def build_thread_id(*, conversation_id: str, hub_user_id: int) -> str:
    """构造 LangGraph checkpoint 用的复合 thread_id。

    格式：f"{conversation_id}:{hub_user_id}"
    """
    if not conversation_id:
        raise ValueError("conversation_id 不能为空")
    if not hub_user_id or hub_user_id <= 0:
        raise ValueError(f"hub_user_id 必须是正整数，不能是 {hub_user_id!r}")
    return f"{conversation_id}:{hub_user_id}"


def parse_thread_id(thread_id: str) -> tuple[str, int]:
    """从 thread_id 反解 (conversation_id, hub_user_id)。"""
    if ":" not in thread_id:
        raise ValueError(f"thread_id 格式错误：{thread_id!r}，应为 'conv:user'")
    conv, user_str = thread_id.rsplit(":", 1)
    return conv, int(user_str)


def build_langgraph_config(
    *,
    conversation_id: str,
    hub_user_id: int,
    extra: dict | None = None,
) -> dict:
    """构造 LangGraph ainvoke 用的 config。永远不要传 None / 漏写 thread_id。"""
    return {
        "configurable": {
            "thread_id": build_thread_id(
                conversation_id=conversation_id, hub_user_id=hub_user_id,
            ),
            **(extra or {}),
        }
    }
```

- [ ] **Step 4: 跑测试**

```bash
pytest tests/agent/test_per_user_isolation.py -v
```

Expected: 4 PASS。

- [ ] **Step 5: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/graph/config.py backend/tests/agent/test_per_user_isolation.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
feat(hub): LangGraph thread_id per-(conv,user) 复合 key（Plan 6 v9 Task 0.3）

build_thread_id / parse_thread_id / build_langgraph_config helper。
强约束：所有 checkpoint 必须以 (conversation_id, hub_user_id) 为边界，
防群聊里不同用户串状态。spec §2.1 兑现。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 0.4: 改造 llm_client 为 DeepSeekLLMClient

**Files:**
- Modify: `backend/hub/agent/llm_client.py`（rewrite，153 → ~400 行）
- Create: `backend/tests/agent/test_deepseek_llm_client.py`

**Spec ref:** §1.1 / §1.2 / §1.5 / §1.6 / §1.7 / §1.8 / §1.10 / §12.1

- [ ] **Step 1: 写失败测试（核心方法签名 + 关键 helper）**

```python
# backend/tests/agent/test_deepseek_llm_client.py
import pytest
from unittest.mock import AsyncMock, patch
from hub.agent.llm_client import (
    DeepSeekLLMClient,
    disable_thinking,
    LLMFallbackError,
)


def test_disable_thinking_helper():
    assert disable_thinking() == {"type": "disabled"}


def test_client_uses_beta_endpoint_by_default():
    client = DeepSeekLLMClient(api_key="x", model="deepseek-v4-flash")
    assert "beta" in client.base_url


def test_client_accepts_explicit_base_url():
    client = DeepSeekLLMClient(api_key="x", model="m", base_url="https://override")
    assert client.base_url == "https://override"


@pytest.mark.asyncio
async def test_chat_passes_thinking_disabled_when_requested():
    """非 thinking 节点必须显式传 thinking={'type':'disabled'}（spec §1.5 默认值陷阱）。"""
    client = DeepSeekLLMClient(api_key="x", model="m")
    captured = {}

    async def fake_post(*, url, headers, json, timeout):
        captured.update(json)
        from httpx import Response, Request
        return Response(200, request=Request("POST", url), json={
            "choices": [{"message": {"content": '{"intent": "chat'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "prompt_cache_hit_tokens": 8, "prompt_cache_miss_tokens": 2},
        })

    with patch.object(client._http, "post", side_effect=fake_post):
        await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            thinking={"type": "disabled"},
            temperature=0.0,
        )
    assert captured["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_chat_records_cache_hit_rate():
    """usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens 必须解析（spec §1.1 监控）。"""
    client = DeepSeekLLMClient(api_key="x", model="m")
    async def fake_post(*, url, headers, json, timeout):
        from httpx import Response, Request
        return Response(200, request=Request("POST", url), json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                      "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20},
        })
    with patch.object(client._http, "post", side_effect=fake_post):
        resp = await client.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.cache_hit_rate == 0.8


@pytest.mark.asyncio
async def test_insufficient_system_resource_triggers_retry():
    """finish_reason='insufficient_system_resource' 当 503 重试（spec §1.7）。"""
    client = DeepSeekLLMClient(api_key="x", model="m", max_retries=3)
    call_count = 0

    async def fake_post(*, url, headers, json, timeout):
        nonlocal call_count
        call_count += 1
        from httpx import Response, Request
        if call_count < 3:
            return Response(200, request=Request("POST", url), json={
                "choices": [{"message": {"content": ""}, "finish_reason": "insufficient_system_resource"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 0,
                          "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10},
            })
        return Response(200, request=Request("POST", url), json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10},
        })

    with patch.object(client._http, "post", side_effect=fake_post):
        resp = await client.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.text == "ok" and call_count == 3


@pytest.mark.asyncio
async def test_fallback_protocol_write_tool_fails_closed():
    """写 tool 类节点不允许 strict 错误降级（spec §12.1）。"""
    from hub.agent.llm_client import ToolClass
    client = DeepSeekLLMClient(api_key="x", model="m")
    async def raise_400(*a, **kw):
        from httpx import Response, Request, HTTPStatusError
        resp = Response(400, request=Request("POST", "u"),
                        json={"error": {"message": "strict schema violation"}})
        raise HTTPStatusError("400", request=resp.request, response=resp)
    with patch.object(client._http, "post", side_effect=raise_400):
        with pytest.raises(LLMFallbackError):
            await client.chat(
                messages=[{"role": "user", "content": "x"}],
                tool_class=ToolClass.WRITE,
                tools=[{"type": "function", "function": {"name": "f", "strict": True,
                                                          "parameters": {"type": "object",
                                                                          "properties": {},
                                                                          "required": [],
                                                                          "additionalProperties": False}}}],
            )
```

- [ ] **Step 2: 跑测试，应该失败**

```bash
pytest tests/agent/test_deepseek_llm_client.py -v
```

Expected: ImportError 或 AttributeError。

- [ ] **Step 3: 实现 DeepSeekLLMClient**

```python
# backend/hub/agent/llm_client.py
"""DeepSeekLLMClient — beta endpoint + prefix + strict + thinking + cache + 600s + 指数退避 + 5 finish_reason + 按 tool 类型 fallback。

Spec ref：§1.1 / §1.2 / §1.5 / §1.6 / §1.7 / §1.8 / §1.10 / §12.1

注意：保留作为 GraphAgent 的 LLM 适配层，**不**改用 langchain.ChatOpenAI 默认封装。
LangChain 默认不暴露 prefix / strict / thinking / cache usage / finish_reason 语义。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger("hub.agent.llm")

DEEPSEEK_BETA_URL = "https://api.deepseek.com/beta"
DEEPSEEK_MAIN_URL = "https://api.deepseek.com"

# 退避序列 — 高负载下避免快速重试加剧 429 / insufficient_system_resource
DEFAULT_BACKOFF_SECONDS = (1.5, 5, 15, 60)
DEFAULT_TIMEOUT_SECONDS = 600  # DeepSeek 动态速率，10 分钟 keep-alive


class ToolClass(str, Enum):
    """tool 类型分级 — fallback 协议按这个分（spec §12.1）。"""
    READ = "read"      # search_*/get_*/check_*/analyze_* — 幂等查询
    WRITE = "write"    # generate_*/adjust_*/create_*/_request — 有副作用


class LLMFallbackError(Exception):
    """写 tool 路径上 strict / beta 失败时 fail closed（spec §12.1）。"""


# 注意：CrossContextClaim 属于 ConfirmGate 安全边界，**不**在 llm_client 定义。
# 见 Task 0.5：在 hub/agent/tools/confirm_gate.py 唯一定义。


def disable_thinking() -> dict:
    """所有非 thinking 节点必须传这个（DeepSeek thinking 默认 enabled，spec §1.5）。"""
    return {"type": "disabled"}


def enable_thinking() -> dict:
    return {"type": "enabled"}


@dataclass
class LLMResponse:
    text: str
    finish_reason: str
    tool_calls: list[dict]
    cache_hit_rate: float
    usage: dict
    raw: dict


class DeepSeekLLMClient:
    """Plan 6 GraphAgent 用的 LLM client。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-v4-flash",
        base_url: str = DEEPSEEK_BETA_URL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = 4,
        backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._http = httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self):
        await self._http.aclose()

    # ----- 主接口 -----

    async def chat(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | dict = "auto",
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        thinking: dict | None = None,
        tool_class: ToolClass | None = None,  # 写 tool 类用 WRITE，读 tool 用 READ；fallback 行为不同
        prefix_assistant: str | None = None,  # 用 prefix completion 强制开头
    ) -> LLMResponse:
        """主聊天调用，含完整 fallback / retry / finish_reason 处理。"""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if stop:
            body["stop"] = stop
        # thinking — 默认 enabled，必须显式传 disabled 才关
        body["thinking"] = thinking if thinking is not None else disable_thinking()
        # prefix completion
        if prefix_assistant is not None:
            body["messages"] = [
                *body["messages"],
                {"role": "assistant", "content": prefix_assistant, "prefix": True},
            ]

        return await self._call_with_retry(body=body, tool_class=tool_class)

    # ----- 内部：retry + finish_reason + fallback -----

    async def _call_with_retry(
        self, *, body: dict, tool_class: ToolClass | None,
    ) -> LLMResponse:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return await self._call_once(body=body)
            except _RetryableError as e:
                last_exc = e
                if attempt + 1 < self.max_retries:
                    wait = self.backoff_seconds[min(attempt, len(self.backoff_seconds) - 1)]
                    logger.warning(
                        "DeepSeek retry attempt=%d wait=%.1fs reason=%s",
                        attempt + 1, wait, e,
                    )
                    await asyncio.sleep(wait)
                    continue
            except httpx.HTTPStatusError as e:
                # strict schema 错误 / 4xx
                if e.response.status_code == 400 and "strict" in (e.response.text or "").lower():
                    if tool_class == ToolClass.WRITE:
                        # 写 tool fail closed
                        logger.error("strict 校验失败 write tool path → fail closed: %s", e.response.text)
                        raise LLMFallbackError(f"strict schema 校验失败：{e.response.text}") from e
                    # 读 tool / 其他场景：让上层降级（这里只 raise，handler 决定）
                raise
        if last_exc is None:
            raise RuntimeError("retry 循环异常")
        # 写 tool fail closed
        if tool_class == ToolClass.WRITE:
            raise LLMFallbackError(f"达到 max_retries 仍失败：{last_exc}") from last_exc
        raise last_exc

    async def _call_once(self, *, body: dict) -> LLMResponse:
        try:
            resp = await self._http.post(
                url=f"{self.base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
        except httpx.TimeoutException as e:
            # 网络/读取超时 — DeepSeek 动态速率 + 600s timeout 下仍可能命中
            raise _RetryableError(f"timeout: {e}") from e
        except httpx.TransportError as e:
            # DNS / TCP / TLS / 连接断开等传输层错误 — 全部当可重试
            raise _RetryableError(f"transport: {e}") from e
        except httpx.HTTPStatusError as e:
            # 可重试状态码：408 Request Timeout / 425 Too Early / 429 Too Many /
            # 全部 5xx（500/502/503/504/...）— staging 实测过 DeepSeek 偶发 5xx 抖动
            code = e.response.status_code
            if code in (408, 425, 429) or 500 <= code < 600:
                raise _RetryableError(f"{code}: {e.response.text}") from e
            raise

        data = resp.json()
        choice = data["choices"][0]
        message = choice["message"]
        finish_reason = choice.get("finish_reason", "stop")

        # 5 种 finish_reason 分支（spec §1.7）
        if finish_reason == "insufficient_system_resource":
            raise _RetryableError("insufficient_system_resource")
        if finish_reason == "content_filter":
            logger.warning("content_filter 拦截：messages=%s", body.get("messages"))
            # 不重试，返回特殊标识让上层友好告知用户
        if finish_reason == "length":
            logger.warning("撞 max_tokens — 截断告警，考虑缩短 prompt 或调大 max_tokens")
        # stop / tool_calls 正常返回

        usage = data.get("usage", {})
        hit = usage.get("prompt_cache_hit_tokens", 0)
        miss = usage.get("prompt_cache_miss_tokens", 0)
        cache_hit_rate = hit / max(hit + miss, 1)

        # **关键陷阱**：assistant message 多轮 append 不能含 reasoning_content（spec §1.5）
        # 这里返回的 text 只取 content，调用方 SessionMemory.append 时也只能用 text
        return LLMResponse(
            text=message.get("content") or "",
            finish_reason=finish_reason,
            tool_calls=message.get("tool_calls") or [],
            cache_hit_rate=cache_hit_rate,
            usage=usage,
            raw=data,
        )


class _RetryableError(Exception):
    """内部用 — 标记可退避重试的错误。"""
```

- [ ] **Step 4: 跑测试**

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend
pytest tests/agent/test_deepseek_llm_client.py -v
```

Expected: 7 个测试全 PASS。

- [ ] **Step 5: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/llm_client.py backend/tests/agent/test_deepseek_llm_client.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
feat(hub): DeepSeekLLMClient v3.4 适配层（Plan 6 v9 Task 0.4）

beta endpoint + prefix completion + strict 透传 + thinking 默认 disabled +
KV cache usage 解析 + 5 种 finish_reason 显式分支 + 600s timeout +
指数退避 1.5/5/15/60s + 按 tool 类型 fallback（写 tool fail closed / 读 tool 降级）

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 0.5: ConfirmGate API 升级到复合 key

**Files:**
- Modify: `backend/hub/agent/tools/confirm_gate.py`
- Modify: `backend/hub/models/` 加 `PendingAction` 字段（如果 PG 表存的话）/ 或纯 Redis 重写
- Create: `backend/tests/agent/test_node_confirm.py`

**Spec ref:** §6.3 ConfirmGate 跨会话隔离

- [ ] **Step 1: 读现有 confirm_gate.py 决定改造路径**

```bash
cat /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend/hub/agent/tools/confirm_gate.py | head -80
```

Expected: 看清当前 API（`get_pending_for_user` / `claim` 签名），决定要 rewrite 还是 incrementally 改。

- [ ] **Step 2: 写失败测试（3 个隔离断言）**

```python
# backend/tests/agent/test_node_confirm.py
import pytest
from datetime import datetime, timedelta
from fakeredis.aioredis import FakeRedis
from hub.agent.tools.confirm_gate import (
    ConfirmGate, PendingAction, CrossContextClaim,
)


@pytest.fixture
async def redis():
    r = FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


@pytest.fixture
def gate(redis):
    return ConfirmGate(redis)


@pytest.mark.asyncio
async def test_pending_action_must_carry_conversation_id(gate):
    p = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_price", summary="阿里 X1 → 280",
        payload={"tool_name": "adjust_price_request",
                 "args": {"customer_id": 10, "product_id": 1, "new_price": 280.0, "reason": ""}},
        action_prefix="adj",
    )
    assert p.conversation_id == "c1"
    # P2-G：action_id 必须是完整 32-hex（含前缀）
    assert p.action_id.startswith("adj-")
    assert len(p.action_id.split("-", 1)[1]) == 32
    # P1-C：payload 必须落库
    assert p.payload["tool_name"] == "adjust_price_request"
    assert p.payload["args"]["customer_id"] == 10


# Test helper：测试用短 action_id 便于断言；production 必须经过自动生成
def _make_test_pending_kwargs(**overrides) -> dict:
    """测试 fixture：补齐 payload 必填字段；overrides 可覆盖 action_id 等。"""
    base = {
        "subgraph": "adjust_price",
        "summary": "test pending",
        "payload": {"tool_name": "adjust_price_request",
                     "args": {"customer_id": 1, "product_id": 1, "new_price": 1.0, "reason": ""}},
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_list_pending_for_context_filters_by_both(gate):
    """同一 user 在两个 conversation 各有 pending — list_for_context 只返回当前 context 的。"""
    await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-1", hub_user_id=1, conversation_id="c1-private", summary="A",
    ))
    await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-2", hub_user_id=1, conversation_id="c2-group", summary="B",
    ))
    in_c1 = await gate.list_pending_for_context(conversation_id="c1-private", hub_user_id=1)
    in_c2 = await gate.list_pending_for_context(conversation_id="c2-group", hub_user_id=1)
    assert {p.action_id for p in in_c1} == {"adj-1"}
    assert {p.action_id for p in in_c2} == {"adj-2"}


@pytest.mark.asyncio
async def test_claim_rejects_cross_conversation(gate):
    """伪造 token 跨会话 claim 必须 raise CrossContextClaim。"""
    p = await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-1", hub_user_id=1, conversation_id="c1", summary="A",
    ))
    with pytest.raises(CrossContextClaim):
        await gate.claim(action_id="adj-1", token=p.token,
                         hub_user_id=1, conversation_id="c2")  # 错的 conv


@pytest.mark.asyncio
async def test_claim_rejects_wrong_user(gate):
    """user A 的 pending 不能被 user B claim。"""
    p = await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-1", hub_user_id=1, conversation_id="c1", summary="A",
    ))
    with pytest.raises(CrossContextClaim):
        await gate.claim(action_id="adj-1", token=p.token,
                         hub_user_id=2, conversation_id="c1")


@pytest.mark.asyncio
async def test_claim_single_use_token(gate):
    """token 单次消费 — 第二次 claim 失败。"""
    p = await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-1", hub_user_id=1, conversation_id="c1", summary="A",
    ))
    ok = await gate.claim(action_id="adj-1", token=p.token, hub_user_id=1, conversation_id="c1")
    assert ok
    with pytest.raises(Exception):  # 已消费
        await gate.claim(action_id="adj-1", token=p.token, hub_user_id=1, conversation_id="c1")


@pytest.mark.asyncio
async def test_list_pending_order_stable_by_created_at(gate):
    """P2-C v1.3：list_pending_for_context 必须按 created_at asc 稳定排序。
    多 pending 时 confirm_node 看到的"1)/2)/3)"和下一轮"1" 必须绑同一 action。"""
    # 显式控制 created_at 时序
    import asyncio
    p_first = await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-first", hub_user_id=1, conversation_id="c1", summary="先创建",
    ))
    await asyncio.sleep(0.01)  # 确保不同 created_at
    p_second = await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-second", hub_user_id=1, conversation_id="c1", summary="后创建",
    ))
    await asyncio.sleep(0.01)
    p_third = await gate.create_pending(**_make_test_pending_kwargs(
        action_id="adj-third", hub_user_id=1, conversation_id="c1", summary="最后创建",
    ))

    # 连续读 5 次顺序必须一致（不靠 Redis scan 偶然顺序）
    orders = []
    for _ in range(5):
        pendings = await gate.list_pending_for_context(conversation_id="c1", hub_user_id=1)
        orders.append([p.action_id for p in pendings])
    # 所有 5 次顺序相同
    assert all(o == orders[0] for o in orders)
    # 顺序与 created_at asc 一致
    assert orders[0] == ["adj-first", "adj-second", "adj-third"]
```

- [ ] **Step 3: 跑测试**

```bash
pytest tests/agent/test_node_confirm.py -v
```

Expected: ImportError on `PendingAction` / `CrossContextClaim` 或类型不匹配。

- [ ] **Step 4: 改造 confirm_gate.py**

参考 spec §6.3 PendingAction 结构 + claim 校验 conversation。Subagent 执行此步时**先读完整现有 confirm_gate.py**，保留 token 单次消费 / TTL / Redis key 设计，做以下改动：

- 新增 `PendingAction` 字段：
  - `conversation_id: str`（必填）— v3.3 复合 key
  - `subgraph: str`（必填）— confirm_node 据此路由（如 "adjust_price" / "voucher"）
  - `summary: str`（必填）— 多 pending 时给用户看的摘要
  - `ttl_seconds: int = 600` — 过期失效
  - **`payload: dict`（必填）— P1-C：canonical 执行参数**（含 `tool_name` + `args`），commit 节点据此执行，不依赖当前 state
  - **`created_at: datetime`（必填）— P2-C v1.3：list_pending_for_context 据此 asc 排序**，
    保证多 pending 时 confirm_node 看到的"1)/2)/3)"和下一轮回复"1"绑定到同一 pending；
    Redis scan 自身顺序不稳定，必须显式排序
  - `action_id: str` — 完整形如 `f"{prefix}-{uuid4().hex}"`（**32-hex** 不是 8-hex），由 ConfirmGate 自动生成

- `list_pending_for_context(*, conversation_id, hub_user_id)` 实现要求：
  - 按 `created_at asc` 排序返回（同时刻 break tie 用 `action_id`）
  - 加测试 `test_list_pending_order_stable_across_calls`：连续调用 100 次顺序一致

- `create_pending` 签名（**调用方不传 action_id，ConfirmGate 内部自动生成**）：

  ```python
  async def create_pending(
      self, *,
      hub_user_id: int,
      conversation_id: str,
      subgraph: str,
      summary: str,
      payload: dict,
      action_prefix: str = "act",        # 业务前缀，如 "adj"/"vch"/"stk"
      ttl_seconds: int = 600,
      idempotency_key: str | None = None,  # v1.2 P2-E：写 tool 用，TTL 内同 key 复用
  ) -> PendingAction:
      """如 idempotency_key 存在且未过期：
        - 同 (conv, user) 命中 → 返回**原 PendingAction**（复用，预期场景）
        - 跨 (conv, user) 命中 → raise CrossContextIdempotency（v1.3 P1-B：不能把
          别人 conv 的 action_id 暴露给当前 user，否则 confirm_node 按 (conv,user)
          查不到 → 显示"没有待办"，但用户以为已经申请了）
      场景：voucher 同订单 12 小时内复用 / adjust_stock 同 (conv,user,product,delta) 5 分钟复用。
      """
      if idempotency_key:
          existing = await self._find_by_idempotency_key(idempotency_key)
          if existing:
              if (existing.conversation_id == conversation_id
                  and existing.hub_user_id == hub_user_id):
                  return existing  # 同 context 复用
              # 跨 context — caller 必须 fail closed 不暴露
              raise CrossContextIdempotency(
                  f"idempotency_key={idempotency_key} 在另一 context 已有 pending "
                  f"(action_id={existing.action_id}); 当前 ({conversation_id}, {hub_user_id}) "
                  f"不能复用"
              )
      action_id = f"{action_prefix}-{uuid4().hex}"  # 完整 32-hex
      # ...
  ```

- **新增异常 `CrossContextIdempotency(Exception)`**：与 `CrossContextClaim` 同位置（`hub.agent.tools.confirm_gate`），由 voucher / adjust_stock 等用了全局幂等 key 的 caller 捕获并 fail closed。

  测试 fixture 里允许传 `action_id` override（仅用于 mock），但生产代码路径必须经过自动生成。

- 新增方法 `list_pending_for_context(*, conversation_id, hub_user_id) -> list[PendingAction]`（替代旧 `get_pending_for_user`，旧方法保留并打 DeprecationWarning）

- **P2-A v1.6 新增方法**（eval driver `_check_pending_state` 依赖）：
  - `get_pending_by_id(action_id: str) -> PendingAction | None`：按 action_id 单查；不存在返 None；过期但 Redis key 还在的也返（让 caller 据 `is_expired` 区分）
  - `is_claimed(action_id: str) -> bool`：claim 后 ConfirmGate 必须留 audit log（Redis key `hub:agent:claimed:{action_id}` TTL 24h）；is_claimed 据此查
  - `PendingAction.is_expired() -> bool`：方法 / property，比较 `created_at + ttl_seconds vs now`

- `claim` 签名加 `conversation_id` 必传，内部校验 `pending.conversation_id == conversation_id`，不一致 `raise CrossContextClaim`

- **新增异常 `CrossContextClaim(Exception)`**：唯一定义在这个文件（`hub.agent.tools.confirm_gate`），llm_client / 测试都从这里 import — 避免模块归属漂移导致 `pytest.raises` 捕不到同一类（参见 Task 0.4 修订）

- Redis key schema 改成 `hub:agent:pending:{conversation_id}:{hub_user_id}:{action_id}`（按 prefix 扫描自然命中复合 key）

- `payload` 落 Redis：`json.dumps` 后存到 hash 字段；`list_pending_for_context` 读取时 `json.loads` 还原

- [ ] **Step 5: 跑测试**

```bash
pytest tests/agent/test_node_confirm.py -v
```

Expected: 5 PASS。同时跑 `pytest tests/test_confirm_gate*.py -v`（如果有旧测试）确保旧用法仍兼容（DeprecationWarning 但不 break）。

- [ ] **Step 6: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/tools/confirm_gate.py backend/tests/agent/test_node_confirm.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
feat(hub): ConfirmGate (conversation_id, hub_user_id) 复合 key（Plan 6 v9 Task 0.5）

PendingAction 加 conversation_id + subgraph + summary + ttl_seconds 字段。
新增 list_pending_for_context(conv, user) 替代 get_pending_for_user。
claim 必须带 conversation_id，跨会话/跨用户 raise CrossContextClaim。
Redis key schema：hub:agent:pending:{conv}:{user}:{action_id}。
spec §6.3 兑现 — 防同一 user 私聊+群聊 pending 串确认。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 0.6: M0 真 beta endpoint 集成验证

**Files:**
- Create: `backend/tests/integration/test_deepseek_compat.py`

**Spec ref:** §1.10 M0 验收 + §1.3 strict sentinel/anyOf 实验

⚠️ 这个 task 需要真 DeepSeek API key（从 hub admin AI provider 配置读）。需要环境变量 `DEEPSEEK_API_KEY` 或 hub PG 里有 active AI provider。

- [ ] **Step 1: 写集成测试**

```python
# backend/tests/integration/test_deepseek_compat.py
"""M0 兼容性验证 — 真 DeepSeek beta endpoint，验证 spec §1 列的关键能力。

跑：pytest tests/integration/test_deepseek_compat.py -v -m realllm
"""
import os
import pytest
from hub.agent.llm_client import DeepSeekLLMClient, ToolClass, disable_thinking, enable_thinking

pytestmark = [
    pytest.mark.realllm,
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="需要真 API key"),
]


@pytest.fixture
async def client():
    c = DeepSeekLLMClient(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        model="deepseek-v4-flash",
    )
    yield c
    await c.aclose()


async def test_prefix_completion_forces_json_opening(client):
    """spec §1.2 应用 A：router 用 prefix 强制 JSON 输出。"""
    resp = await client.chat(
        messages=[
            {"role": "system", "content": "You are a router. Output {\"intent\": \"chat\"|\"query\"|\"contract\"}"},
            {"role": "user", "content": "给阿里做合同"},
        ],
        prefix_assistant='{"intent": "',
        stop=['",'],
        max_tokens=20,
        thinking=disable_thinking(),
    )
    # 模型必须从 prefix 续写，开头 = 我们注入的 prefix（DeepSeek 协议返回 content 不含 prefix 部分）
    # 验证续写出来的是 lowercase value
    assert resp.text.split('"')[0].lower() in {"chat", "query", "contract", "quote", "voucher",
                                                  "adjust_price", "adjust_stock", "confirm", "unknown"}


async def test_strict_with_sentinel_string(client):
    """spec §1.3 v3.4 默认：sentinel 写法 — 可选字段用 type:string + 空串 sentinel。"""
    schema = {
        "type": "function",
        "function": {
            "name": "echo_address",
            "description": "测试 sentinel 写法。无地址传 ''",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "shipping_address": {"type": "string", "description": "无值传 ''"},
                },
                "required": ["name", "shipping_address"],
                "additionalProperties": False,
            },
        },
    }
    resp = await client.chat(
        messages=[{"role": "user", "content": "echo 张三 无地址"}],
        tools=[schema],
        tool_choice="required",
        thinking=disable_thinking(),
    )
    # beta endpoint 应该接受这个 schema 不报错
    assert resp.tool_calls, f"应触发 tool 调用但没有：{resp.text}"


async def test_strict_anyof_null_experiment(client):
    """spec §1.3 M0 实验项 — anyOf+null 是否被 beta 接受。结果决定后续策略。"""
    schema = {
        "type": "function",
        "function": {
            "name": "echo_addr_v2",
            "description": "测试 anyOf null。",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "shipping_address": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["name", "shipping_address"],
                "additionalProperties": False,
            },
        },
    }
    try:
        resp = await client.chat(
            messages=[{"role": "user", "content": "echo 张三 无地址"}],
            tools=[schema],
            tool_choice="required",
            thinking=disable_thinking(),
        )
        # 通过 → 实验成功，spec v3.4 可升级到"anyOf-null 优先"
        print(f"\n[实验通过] anyOf+null 被 beta 接受。tool_calls={resp.tool_calls}")
    except Exception as e:
        # 失败 → 维持 sentinel 默认
        pytest.skip(f"[实验失败] anyOf+null 不被 beta 接受：{e}（spec v3.4 sentinel 默认正确）")


async def test_thinking_disabled_with_tools(client):
    """spec §1.5 + M0：thinking disabled + tools 同时启用必须可工作。"""
    resp = await client.chat(
        messages=[{"role": "user", "content": "搜索客户阿里"}],
        tools=[{
            "type": "function",
            "function": {
                "name": "search_customers",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        }],
        tool_choice="auto",
        thinking=disable_thinking(),
    )
    assert resp.tool_calls or resp.text


async def test_kv_cache_usage_parsed(client):
    """spec §1.1 KV cache 监控 — usage 字段必须解析正确。"""
    static_prompt = "You are a helpful assistant. " * 100  # 制造长前缀
    # 第一次调用 — cache miss
    r1 = await client.chat(
        messages=[
            {"role": "system", "content": static_prompt},
            {"role": "user", "content": "hi"},
        ],
        thinking=disable_thinking(),
    )
    # 第二次同 system → 应有 cache 命中
    r2 = await client.chat(
        messages=[
            {"role": "system", "content": static_prompt},
            {"role": "user", "content": "hello"},
        ],
        thinking=disable_thinking(),
    )
    print(f"\nrun1 cache_hit_rate={r1.cache_hit_rate:.2f}")
    print(f"run2 cache_hit_rate={r2.cache_hit_rate:.2f}")
    assert r2.cache_hit_rate > 0  # 第二次至少有命中


async def test_thinking_enabled_outputs_reasoning(client):
    """spec §1.5 实验：thinking enabled 时模型有 reasoning 输出。"""
    resp = await client.chat(
        messages=[{"role": "user", "content": "9.11 和 9.9 哪个大？请推理"}],
        thinking=enable_thinking(),
        tools=None,
    )
    # 至少 finish_reason 是 stop（不是 insufficient_system_resource）
    assert resp.finish_reason in {"stop", "length"}
    # reasoning_content 在 raw['choices'][0]['message'] 里（如果模型支持）
    msg = resp.raw["choices"][0]["message"]
    print(f"\nthinking enabled — has reasoning_content: {bool(msg.get('reasoning_content'))}")
```

- [ ] **Step 2: 跑测试（需要真 API key）**

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend
DEEPSEEK_API_KEY=$(uv run python -c "import asyncio; from hub.capabilities.factory import load_active_ai_provider; ai = asyncio.run(load_active_ai_provider()); print(ai.api_key); asyncio.run(ai.aclose())") \
pytest tests/integration/test_deepseek_compat.py -v -m realllm -s
```

Expected: 6 个测试 PASS（anyOf-null 那个可能 SKIP 也算正常 — 表明实验失败，sentinel 默认是对的）。

- [ ] **Step 3: 记录验证结果到 spec 附录或 Plan 报告**

如果 `test_strict_anyof_null_experiment` 通过，把结果写到 `docs/superpowers/plans/notes/2026-05-02-m0-deepseek-compat-results.md` 让后续 Phase 2 知道是用 sentinel 还是 anyOf-null。

```markdown
# M0 DeepSeek 兼容性验证结果（2026-05-02）

| 验证项 | 结果 | 影响 |
|---|---|---|
| prefix completion JSON 强制 | ✅ PASS | router 走 prefix 没问题 |
| strict + sentinel ('') | ✅ PASS | spec v3.4 默认方案确认可用 |
| **strict + anyOf+null 实验** | ✅/❌ | 通过则 Phase 2 升级到 anyOf 优先 |
| thinking disabled + tools | ✅ PASS | tool 节点关 thinking 可行 |
| KV cache usage 解析 | ✅ PASS | 监控就绪 |
| thinking enabled reasoning | ✅ PASS | adjust_price.preview / contract.validate 可开 |
```

- [ ] **Step 4: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/tests/integration/test_deepseek_compat.py docs/superpowers/plans/notes/2026-05-02-m0-deepseek-compat-results.md
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
test(hub): M0 DeepSeek beta endpoint 兼容性验证（Plan 6 v9 Task 0.6）

跑 6 个真 LLM 集成测试：prefix completion / strict sentinel /
strict anyOf+null 实验 / thinking disabled + tools / KV cache usage /
thinking enabled。验证结果记到 plans/notes/。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 0.7: SessionMemory 加剥离 reasoning_content

**Files:**
- Modify: `backend/hub/agent/memory/session.py`

**Spec ref:** §1.5 多轮对话陷阱

- [ ] **Step 1: 写测试**

```python
# backend/tests/test_session_memory_strip_reasoning.py
import pytest
from fakeredis.aioredis import FakeRedis
from hub.agent.memory.session import SessionMemory


@pytest.mark.asyncio
async def test_append_strips_reasoning_content():
    """assistant message append 时必须剥离 reasoning_content（DeepSeek 多轮 400 陷阱）。"""
    r = FakeRedis(decode_responses=False)
    sm = SessionMemory(r)
    await sm.append(
        conversation_id="c1", hub_user_id=1,
        message={
            "role": "assistant",
            "content": "答案是 9.11 < 9.9",
            "reasoning_content": "比较小数点后位数...",  # 这个必须被剥离
        },
    )
    msgs = await sm.get_messages(conversation_id="c1", hub_user_id=1)
    assert msgs[-1].get("reasoning_content") is None
    assert msgs[-1]["content"] == "答案是 9.11 < 9.9"
    await r.aclose()
```

- [ ] **Step 2: 跑失败**

```bash
pytest tests/test_session_memory_strip_reasoning.py -v
```

- [ ] **Step 3: 改造 session.py append 方法**

读 `backend/hub/agent/memory/session.py`，找 `append` 方法，加剥离逻辑：

```python
async def append(self, *, conversation_id: str, hub_user_id: int, message: dict) -> None:
    # DeepSeek 多轮陷阱：assistant message append 不能含 reasoning_content，否则 400
    if message.get("role") == "assistant" and "reasoning_content" in message:
        message = {k: v for k, v in message.items() if k != "reasoning_content"}
    # ... 其余逻辑保持
```

- [ ] **Step 4: 跑测试 PASS**

- [ ] **Step 5: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/memory/session.py backend/tests/test_session_memory_strip_reasoning.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
fix(hub): SessionMemory.append 剥离 reasoning_content（Plan 6 v9 Task 0.7）

DeepSeek 多轮陷阱：assistant message append 含 reasoning_content 会 400。
spec §1.5 兑现。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 0.8: Phase 0 集成验证（M0 exit gate）

- [ ] **Step 1: 跑 Phase 0 全部新增 / 改造测试**

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend
pytest tests/agent/ tests/test_session_memory_strip_reasoning.py -v
```

Expected: 全部 PASS。

- [ ] **Step 2: 跑全量旧测试不 regress**

```bash
pytest tests/ -x -q --ignore=tests/integration
```

Expected: 660+ 旧单测仍全 PASS（DeprecationWarning 可接受，但不能有 FAIL/ERROR）。

- [ ] **Step 3: 跑 M0 集成验证（如果有真 API key）**

```bash
pytest tests/integration/test_deepseek_compat.py -v -m realllm -s
```

Expected: 全部 PASS（或 anyOf-null 那个 SKIP）。

- [ ] **Step 4: 标记 M0 完成**

如果以上全过，记录 M0 通过：

```bash
echo "M0 PASSED at $(date -Iseconds)" >> docs/superpowers/plans/notes/2026-05-02-progress.md
```

---

## Phase 1：Router + chat 子图（M1，0.5 天）

**Goal**：实现轻量 LLM router（prefix JSON）+ chat 子图（temperature=1.3，0 tool）。50 case 准确率 ≥ 95%。

**Exit criteria**：
1. `pytest tests/agent/test_graph_router_accuracy.py -v` ≥ 95% PASS
2. 故事 1（闲聊）E2E 跑通

### Task 1.1: 写 ROUTER_SYSTEM_PROMPT + 50 case few-shots

**Files:**
- Create: `backend/hub/agent/prompt/intent_router.py`
- Create: `backend/tests/agent/fixtures/router_50_cases.yaml`

**Spec ref:** §6.1 Intent / §6.2 Router

- [ ] **Step 1: 写 50 case yaml（标注 ground truth）**

```yaml
# backend/tests/agent/fixtures/router_50_cases.yaml
# 每个 case：input -> expected_intent
# 用于 Router 准确率测试，要求 ≥ 95% PASS
- input: "你好"
  intent: chat
- input: "最近怎样"
  intent: chat
- input: "查 SKG 有哪些产品有库存"
  intent: query
- input: "看看阿里上个月订单"
  intent: query
- input: "X1 现在多少钱"
  intent: query
- input: "给阿里做合同 X1 10 个 300"
  intent: contract
- input: "给翼蓝做合同 H5 10 个 300，F1 10 个 500"
  intent: contract
- input: "给阿里报 X1 50 个的价"
  intent: quote
- input: "出库 SO-202404-0001"
  intent: voucher
- input: "把阿里的 X1 价格调到 280"
  intent: adjust_price
- input: "X1 库存调到 100"
  intent: adjust_stock
- input: "确认"
  intent: confirm
- input: "是"
  intent: confirm
- input: "好的"
  intent: confirm
# ... 续 36 case 覆盖每个 intent 至少 5 case + 一些边界
# （subagent 实施时按 intent 类目分桶补到 50 个）
```

⚠️ subagent 实施时务必把 50 case 写满，覆盖每个 intent ≥ 5 个 case + 至少 5 个 UNKNOWN 边界 case（"嗯"、"。。。"、空、emoji 等）。

- [ ] **Step 2: 写 ROUTER_SYSTEM_PROMPT**

```python
# backend/hub/agent/prompt/intent_router.py
"""Router system prompt — 必须完全静态（KV cache 命中关键）。spec §6.2 / §1.1。"""
from __future__ import annotations

ROUTER_SYSTEM_PROMPT = """你是一个意图分类器。读用户消息，输出 JSON：{"intent": "<value>"}。

intent 枚举（必须 lowercase）：
- chat: 闲聊 / 寒暄 / 跟业务无关
- query: 查询信息（产品 / 客户 / 订单 / 库存 / 历史 / 报表）— 只读，不改数据
- contract: 起草销售合同
- quote: 起草报价单
- voucher: 起草出库 / 入库凭证
- adjust_price: 调整客户对某产品的价格（preview，需用户确认）
- adjust_stock: 调整库存数量（preview，需用户确认）
- confirm: 确认上一个待办操作（"是"/"确认"/"好的"等）
- unknown: 上述都不是 / 看不懂

few-shot 示例：
USER: 你好
{"intent": "chat"}

USER: 查 SKG 有哪些产品有库存
{"intent": "query"}

USER: 给阿里做合同 X1 10 个 300，地址北京
{"intent": "contract"}

USER: 给阿里报 X1 50 个的价
{"intent": "quote"}

USER: 把阿里的 X1 价格调到 280
{"intent": "adjust_price"}

USER: X1 库存调到 100
{"intent": "adjust_stock"}

USER: 出库 SO-202404-0001
{"intent": "voucher"}

USER: 确认
{"intent": "confirm"}

USER: 嗯
{"intent": "unknown"}

只输出 JSON，不要任何解释 / 思考 / 寒暄。
"""
```

- [ ] **Step 3: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/prompt/intent_router.py backend/tests/agent/fixtures/router_50_cases.yaml
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
feat(hub): Router system prompt + 50 case fixture（Plan 6 v9 Task 1.1）

ROUTER_SYSTEM_PROMPT 完全静态（含 9 个 intent 定义 + 9 个 few-shot），
让 KV cache 命中。50 case yaml 覆盖每个 intent ≥ 5 个 + UNKNOWN 边界。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.2: 实现 router_node

**Files:**
- Create: `backend/hub/agent/graph/router.py`
- Create: `backend/tests/agent/test_graph_router.py`

**Spec ref:** §6.2

- [ ] **Step 1: 写测试（mock LLM）**

```python
# backend/tests/agent/test_graph_router.py
import pytest
from unittest.mock import AsyncMock
from hub.agent.graph.state import AgentState, Intent
from hub.agent.graph.router import router_node


@pytest.mark.asyncio
async def test_router_lowercase_value_resolved():
    """模型续写 'contract' 应能正确解析（不是落 UNKNOWN）— 核心修复点。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": 'contract"'})())
    state = AgentState(user_message="给阿里做合同", hub_user_id=1, conversation_id="c1")
    out = await router_node(state, llm=llm)
    assert out.intent == Intent.CONTRACT


@pytest.mark.asyncio
async def test_router_unknown_value_falls_back_to_unknown():
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": 'foobar"'})())
    state = AgentState(user_message="???", hub_user_id=1, conversation_id="c1")
    out = await router_node(state, llm=llm)
    assert out.intent == Intent.UNKNOWN


@pytest.mark.asyncio
async def test_router_passes_thinking_disabled():
    """spec §1.5：router 必须显式 thinking={'type':'disabled'}。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": 'chat"'})())
    state = AgentState(user_message="hi", hub_user_id=1, conversation_id="c1")
    await router_node(state, llm=llm)
    kwargs = llm.chat.await_args.kwargs
    assert kwargs["thinking"] == {"type": "disabled"}
    assert kwargs["temperature"] == 0.0
    assert kwargs["prefix_assistant"] == '{"intent": "'
    assert kwargs["stop"] == ['",']
```

- [ ] **Step 2: 跑失败**

- [ ] **Step 3: 实现 router.py**

```python
# backend/hub/agent/graph/router.py
"""Router node — prefix JSON + Intent enum + ValueError 兜底。spec §6.2。"""
from __future__ import annotations

from hub.agent.graph.state import AgentState, Intent
from hub.agent.llm_client import DeepSeekLLMClient, disable_thinking
from hub.agent.prompt.intent_router import ROUTER_SYSTEM_PROMPT


async def router_node(state: AgentState, *, llm: DeepSeekLLMClient) -> AgentState:
    """轻量 LLM 调用做意图分类。"""
    resp = await llm.chat(
        messages=[
            {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": state.user_message},
        ],
        prefix_assistant='{"intent": "',
        stop=['",'],
        max_tokens=20,
        temperature=0.0,
        thinking=disable_thinking(),
    )
    intent_str = resp.text.split('"')[0].strip().lower()
    # 注意：Intent.__members__ 是大写名，不是续写出的小写 value；必须用 Intent(value) 构造 + ValueError 兜底
    try:
        state.intent = Intent(intent_str)
    except ValueError:
        state.intent = Intent.UNKNOWN
    return state
```

- [ ] **Step 4: 跑测试 PASS**

- [ ] **Step 5: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/graph/router.py backend/tests/agent/test_graph_router.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
feat(hub): router_node prefix JSON 意图分类（Plan 6 v9 Task 1.2）

prefix_assistant + stop=['\",'] 强制 lowercase intent value 输出。
try Intent(value) except ValueError 兜底防 __members__ 大写名解析陷阱。
显式 thinking=disabled / temperature=0.0。spec §6.2 + §1.5 兑现。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.3: Router 50 case 准确率测试

**Files:**
- Create: `backend/tests/agent/test_graph_router_accuracy.py`

- [ ] **Step 1: 写参数化测试**

```python
# backend/tests/agent/test_graph_router_accuracy.py
"""Router 50 case 准确率测试 — target ≥ 95%。spec §6.2 / §10.1。

跑：pytest tests/agent/test_graph_router_accuracy.py -v -m realllm
"""
import os
import yaml
import pytest
from pathlib import Path
from hub.agent.graph.router import router_node
from hub.agent.graph.state import AgentState, Intent
from hub.agent.llm_client import DeepSeekLLMClient

CASES_PATH = Path(__file__).parent / "fixtures" / "router_50_cases.yaml"

pytestmark = [
    pytest.mark.realllm,
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="需要真 API key"),
]


@pytest.fixture
async def llm():
    c = DeepSeekLLMClient(api_key=os.environ["DEEPSEEK_API_KEY"], model="deepseek-v4-flash")
    yield c
    await c.aclose()


@pytest.mark.asyncio
async def test_router_accuracy_50_cases(llm):
    cases = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    assert len(cases) >= 50, f"需要 ≥ 50 个 case，当前 {len(cases)}"
    correct = 0
    failures = []
    for c in cases:
        state = AgentState(user_message=c["input"], hub_user_id=1, conversation_id="c1")
        out = await router_node(state, llm=llm)
        if out.intent.value == c["intent"]:
            correct += 1
        else:
            failures.append(f"  '{c['input']}' → 期望 {c['intent']}, 实际 {out.intent.value}")
    accuracy = correct / len(cases)
    if accuracy < 0.95:
        pytest.fail(f"准确率 {accuracy:.2%} < 95%。失败 case：\n" + "\n".join(failures))
    print(f"\nRouter 准确率 {accuracy:.2%} ({correct}/{len(cases)})")
```

- [ ] **Step 2: 跑（需要真 LLM）**

```bash
DEEPSEEK_API_KEY=... pytest tests/agent/test_graph_router_accuracy.py -v -m realllm -s
```

Expected: 准确率 ≥ 95%。如果不达 95%，调 ROUTER_SYSTEM_PROMPT 的 few-shots 直到达标。

- [ ] **Step 3: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/tests/agent/test_graph_router_accuracy.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
test(hub): router 50 case 真 LLM 准确率测试（Plan 6 v9 Task 1.3）

target ≥ 95%；不达标必须调 ROUTER_SYSTEM_PROMPT 重跑。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.4: Chat 子图

**Files:**
- Create: `backend/hub/agent/prompt/subgraph_prompts/chat.py`
- Create: `backend/hub/agent/graph/subgraphs/chat.py`
- Create: `backend/tests/agent/test_subgraph_chat.py`

**Spec ref:** §1.6 temperature 矩阵（chat=1.3）

- [ ] **Step 1: 写 chat prompt**

```python
# backend/hub/agent/prompt/subgraph_prompts/chat.py
"""Chat 子图 system prompt — 完全静态。spec §1.1 KV cache。"""
CHAT_SYSTEM_PROMPT = """你是一个 ERP 业务助手，名字叫"小邦"。当前是闲聊场景：
- 自然回应用户的寒暄 / 闲聊
- 简短亲切，不用敬语堆砌
- **禁止**主动反问"请问您要做什么/查询什么"这类话
- **禁止**在闲聊里夹杂业务术语（订单 / 客户 / 报表）
- 长度通常 1-2 句话

如果用户消息显然不是闲聊（例如包含产品名 / 客户名 / 数字），礼貌引导：
"看起来是业务问题，您可以直接说，比如'查 SKG 库存'或'给阿里做合同'"
"""
```

- [ ] **Step 2: 写测试**

```python
# backend/tests/agent/test_subgraph_chat.py
import pytest
from unittest.mock import AsyncMock
from hub.agent.graph.state import AgentState, Intent
from hub.agent.graph.subgraphs.chat import chat_subgraph


@pytest.mark.asyncio
async def test_chat_subgraph_no_tools_temperature_1_3():
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": "你好呀"})())
    state = AgentState(user_message="你好", hub_user_id=1, conversation_id="c1",
                        intent=Intent.CHAT)
    out = await chat_subgraph(state, llm=llm)
    kwargs = llm.chat.await_args.kwargs
    assert "tools" not in kwargs or kwargs["tools"] is None
    assert kwargs["temperature"] == 1.3
    assert kwargs["thinking"] == {"type": "disabled"}
    assert out.final_response == "你好呀"
```

- [ ] **Step 3: 跑失败**

- [ ] **Step 4: 实现 chat 子图**

```python
# backend/hub/agent/graph/subgraphs/chat.py
"""Chat 子图 — 0 tool，temperature=1.3 让回复自然。spec §1.6。"""
from __future__ import annotations

from hub.agent.graph.state import AgentState
from hub.agent.llm_client import DeepSeekLLMClient, disable_thinking
from hub.agent.prompt.subgraph_prompts.chat import CHAT_SYSTEM_PROMPT


async def chat_subgraph(state: AgentState, *, llm: DeepSeekLLMClient) -> AgentState:
    resp = await llm.chat(
        messages=[
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
            {"role": "user", "content": state.user_message},
        ],
        temperature=1.3,
        thinking=disable_thinking(),
        max_tokens=200,
    )
    state.final_response = resp.text
    return state
```

- [ ] **Step 5: 跑测试 PASS**

- [ ] **Step 6: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/graph/subgraphs/chat.py backend/hub/agent/prompt/subgraph_prompts/chat.py backend/tests/agent/test_subgraph_chat.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
feat(hub): chat 子图（0 tool, temperature=1.3）（Plan 6 v9 Task 1.4）

CHAT_SYSTEM_PROMPT 完全静态（KV cache）。temperature=1.3 让回复自然
（spec §1.6 DeepSeek 官方推荐通用对话）。禁止主动反问业务问题。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.5: Phase 1 集成测试（故事 1）

**Files:**
- Create: `backend/tests/agent/test_acceptance_scenarios.py`（仅故事 1，后续 phase 加）

- [ ] **Step 1: 故事 1 yaml fixture**

```yaml
# backend/tests/agent/fixtures/scenarios/story1_chat.yaml
name: 故事 1：闲聊
turns:
  - input: "你好，最近怎样"
    expected_intent: chat
    tool_caps: {}
    forbid: ["请问", "您要", "查询什么", "做什么"]
```

- [ ] **Step 2: 写故事 1 测试**

```python
# backend/tests/agent/test_acceptance_scenarios.py
import os
import yaml
import pytest
from pathlib import Path
# 实际 agent 入口在 Phase 7 才完成；先用 router + 子图直接拼装

SCENARIOS_DIR = Path(__file__).parent / "fixtures" / "scenarios"

pytestmark = [
    pytest.mark.realllm,
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="需要真 API key"),
]


@pytest.mark.asyncio
async def test_story_1_chat():
    from hub.agent.graph.state import AgentState, Intent
    from hub.agent.graph.router import router_node
    from hub.agent.graph.subgraphs.chat import chat_subgraph
    from hub.agent.llm_client import DeepSeekLLMClient

    scenario = yaml.safe_load((SCENARIOS_DIR / "story1_chat.yaml").read_text(encoding="utf-8"))
    llm = DeepSeekLLMClient(api_key=os.environ["DEEPSEEK_API_KEY"], model="deepseek-v4-flash")
    try:
        for turn in scenario["turns"]:
            state = AgentState(user_message=turn["input"], hub_user_id=1, conversation_id="c1")
            await router_node(state, llm=llm)
            assert state.intent.value == turn["expected_intent"]
            await chat_subgraph(state, llm=llm)
            for forbidden in turn.get("forbid", []):
                assert forbidden not in (state.final_response or ""), \
                    f"chat 回复不应含 '{forbidden}'：{state.final_response}"
    finally:
        await llm.aclose()
```

- [ ] **Step 3: 跑（真 LLM）**

```bash
DEEPSEEK_API_KEY=... pytest tests/agent/test_acceptance_scenarios.py::test_story_1_chat -v -m realllm -s
```

Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/tests/agent/fixtures/scenarios/story1_chat.yaml backend/tests/agent/test_acceptance_scenarios.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
test(hub): 故事 1 闲聊 acceptance（Plan 6 v9 Task 1.5）

router → chat 子图，0 tool 调用，禁止主动反问业务。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2：Tool strict 化 + sentinel 归一化（M2，0.5 天）

**Goal**：17 个 tool 全部加 strict + additionalProperties=false + 全字段 required；写 / 读 tool handler 入口都加 sentinel 归一化。

**Exit criteria**：
1. `pytest tests/agent/test_strict_mode_validation.py -v` 全过
2. M0 实验结果决定 sentinel vs anyOf-null（默认 sentinel）
3. 至少 1 个写 tool + 1 个读 tool 单测覆盖归一化

### Task 2.1: tools/registry.py 加 strict mode + subgraph_filter

**Files:**
- Modify: `backend/hub/agent/tools/registry.py`
- Create: `backend/tests/agent/test_registry_strict.py`

- [ ] **Step 1: 读 registry.py 确定改造点**

```bash
wc -l /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend/hub/agent/tools/registry.py
```

- [ ] **Step 2: 写测试（subgraph_filter + strict 透传）**

```python
# backend/tests/agent/test_registry_strict.py
import pytest
from hub.agent.tools.registry import ToolRegistry


def test_registry_subgraph_filter():
    reg = ToolRegistry()
    reg.register({
        "type": "function",
        "function": {"name": "search_customers", "strict": True,
                     "parameters": {"type": "object", "properties": {},
                                     "required": [], "additionalProperties": False}},
        "_subgraphs": ["query", "contract", "quote"],
    })
    reg.register({
        "type": "function",
        "function": {"name": "generate_contract_draft", "strict": True,
                     "parameters": {"type": "object", "properties": {},
                                     "required": [], "additionalProperties": False}},
        "_subgraphs": ["contract"],
    })
    schemas = reg.schemas_for_subgraph("contract")
    names = {s["function"]["name"] for s in schemas}
    assert names == {"search_customers", "generate_contract_draft"}
    schemas = reg.schemas_for_subgraph("query")
    names = {s["function"]["name"] for s in schemas}
    assert names == {"search_customers"}


def test_registry_rejects_non_strict_schema():
    """所有 schema 必须 strict=True 才能注册（spec §5.2）。"""
    reg = ToolRegistry()
    with pytest.raises(ValueError, match="strict"):
        reg.register({
            "type": "function",
            "function": {"name": "x"},  # 缺 strict
        })
```

- [ ] **Step 3: 跑失败**

- [ ] **Step 4: 改造 registry.py**

加 `_subgraphs` 字段支持 + `schemas_for_subgraph(name) -> list[dict]` 方法 + 注册时校验 `function.strict == True`、`parameters.additionalProperties == False`。原有 `register` / 调用接口保持。

- [ ] **Step 5: 跑测试 PASS**

- [ ] **Step 6: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/tools/registry.py backend/tests/agent/test_registry_strict.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
feat(hub): ToolRegistry strict + subgraph_filter（Plan 6 v9 Task 2.1）

注册时强校验 strict=True / additionalProperties=False。
schemas_for_subgraph(name) 按子图过滤 17 → 0/3/4/11 个 tool。
spec §5.2 兑现。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.2: 17 个 tool schema 改造（写 tool — generate / draft 系）

**Files:**
- Modify: `backend/hub/agent/tools/generate_tools.py`
- Modify: `backend/hub/agent/tools/draft_tools.py`

**Spec ref:** §1.3 / §5.2 / v3.4 sentinel 默认

- [ ] **Step 1: 列出所有写 tool**

```bash
grep -n "def.*draft\|def.*request\|def.*generate" /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend/hub/agent/tools/generate_tools.py /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend/hub/agent/tools/draft_tools.py
```

预期清单（subagent 实施时按实际验证）：
- `generate_contract_draft`
- `generate_price_quote`
- `create_voucher_draft`
- `adjust_price_request`
- `adjust_stock_request`
- 等

- [ ] **Step 2: 写测试（每个写 tool sentinel 归一化）**

```python
# backend/tests/agent/test_write_tool_sentinel.py
import pytest
from hub.agent.tools.generate_tools import generate_contract_draft


@pytest.mark.asyncio
async def test_generate_contract_draft_sentinel_empty_string_to_none(monkeypatch):
    """shipping_address='' 必须归一化成 None（sentinel handler 入口）— spec §1.3 v3.4。"""
    captured = {}
    async def fake_persist(**kw):
        captured.update(kw)
        return type("D", (), {"id": 1})()
    monkeypatch.setattr("hub.agent.tools.generate_tools._persist_contract", fake_persist)

    await generate_contract_draft(
        customer_id=10, items=[{"product_id": 1, "qty": 10, "price": 300}],
        shipping_address="",  # sentinel
        contact="", phone="",
    )
    # 落库的应该是 None，不是 ""
    assert captured["shipping_address"] is None
    assert captured["contact"] is None
    assert captured["phone"] is None
```

- [ ] **Step 3: 跑失败**

- [ ] **Step 4: 改造 generate_tools.py**

每个写 tool 的 schema：
- 顶层 `function.strict = True`
- `parameters.additionalProperties = False`
- 所有 properties 都 required（含可选字段）
- 可选 `string` 字段：`{"type": "string", "description": "可选；如无传 ''"}`
- 可选 `array` 字段：`{"type": "array", "items": {...}, "description": "可选；如无传 []"}`
- handler 入口归一化：`shipping_address = shipping_address or None`

代码骨架（subagent 按实际现有代码改）：

```python
# backend/hub/agent/tools/generate_tools.py
GENERATE_CONTRACT_DRAFT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_contract_draft",
        "description": "生成销售合同草稿。无地址 / 联系人 / 电话时传 ''。extras 是 dict 不是 string。",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "integer"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "integer"},
                            "qty": {"type": "integer"},
                            "price": {"type": "number"},
                        },
                        "required": ["product_id", "qty", "price"],
                        "additionalProperties": False,
                    },
                },
                "shipping_address": {"type": "string", "description": "可选；如无传 ''"},
                "contact": {"type": "string", "description": "可选；如无传 ''"},
                "phone": {"type": "string", "description": "可选；如无传 ''"},
                "extras": {"type": "object", "description": "扩展信息 dict（不是 string）"},
            },
            "required": ["customer_id", "items", "shipping_address", "contact", "phone", "extras"],
            "additionalProperties": False,
        },
    },
    "_subgraphs": ["contract"],
}


async def generate_contract_draft(*, customer_id: int, items: list,
                                    shipping_address: str, contact: str, phone: str,
                                    extras: dict, **_) -> dict:
    # sentinel 归一化（spec §1.3 v3.4）
    shipping_address = shipping_address or None
    contact = contact or None
    phone = phone or None
    extras = extras or {}
    # 之后业务层 / DB 一律 None 表示"未提供"
    draft = await _persist_contract(
        customer_id=customer_id, items=items,
        shipping_address=shipping_address, contact=contact, phone=phone,
        extras=extras,
    )
    return {"draft_id": draft.id}
```

剩余写 tool（quote / voucher / adjust_*）同样模式。**每个**写 tool 都要单测覆盖 sentinel 归一化。

- [ ] **Step 5: 跑测试 PASS**

- [ ] **Step 6: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/tools/generate_tools.py backend/hub/agent/tools/draft_tools.py backend/tests/agent/test_write_tool_sentinel.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
feat(hub): 写 tool strict + sentinel 归一化（Plan 6 v9 Task 2.2）

generate_contract_draft / generate_price_quote / create_voucher_draft /
adjust_price_request / adjust_stock_request 全部加 strict + sentinel。
handler 入口 x = x or None 归一化（spec §1.3 v3.4 + §5.2）。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.3: 17 个 tool schema 改造（读 tool — erp / analyze 系）

**Files:**
- Modify: `backend/hub/agent/tools/erp_tools.py`
- Modify: `backend/hub/agent/tools/analyze_tools.py`

预期读 tool 清单（subagent 验证实际）：search_customers / search_products / search_orders / get_customer_history / get_order_detail / get_customer_balance / get_inventory_aging / get_product_detail / get_product_customer_prices / check_inventory / analyze_top_customers / analyze_slow_moving_products

- [ ] **Step 1: 写读 tool sentinel 归一化测试**

```python
# backend/tests/agent/test_read_tool_sentinel.py
import pytest
from hub.agent.tools.erp_tools import search_orders


@pytest.mark.asyncio
async def test_search_orders_empty_string_filter_normalized(monkeypatch):
    """customer_name='' 不能被传给 ERP 当查询条件 — spec §1.3 v3.4 读 tool 归一化。"""
    captured = {}
    async def fake_query(**kw):
        captured.update(kw)
        return []
    monkeypatch.setattr("hub.agent.tools.erp_tools._erp_query_orders", fake_query)
    await search_orders(customer_name="", start_date="", end_date="")
    assert captured.get("customer_name") is None
    assert captured.get("start_date") is None
    assert captured.get("end_date") is None
```

- [ ] **Step 2: 跑失败**

- [ ] **Step 3: 改造 erp_tools.py / analyze_tools.py**

每个读 tool：
- schema 加 strict + 全 required + additionalProperties: false（同 Task 2.2）
- handler 入口可选过滤参数 `x = x or None`
- ERP 查询层只对 not None 字段加 WHERE

骨架：

```python
# backend/hub/agent/tools/erp_tools.py
SEARCH_ORDERS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_orders",
        "description": "搜索订单。可选过滤项无值传 ''；返回订单列表。",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "可选；如无传 ''"},
                "start_date": {"type": "string", "description": "可选；如无传 ''；格式 YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "可选；如无传 ''"},
                "limit": {"type": "integer", "description": "返回上限"},
            },
            "required": ["customer_name", "start_date", "end_date", "limit"],
            "additionalProperties": False,
        },
    },
    "_subgraphs": ["query", "voucher"],
}


async def search_orders(*, customer_name: str, start_date: str, end_date: str, limit: int) -> list:
    # 读 tool 归一化 — 防 "" 当真实过滤条件查询
    customer_name = customer_name or None
    start_date = start_date or None
    end_date = end_date or None
    return await _erp_query_orders(
        customer_name=customer_name, start_date=start_date,
        end_date=end_date, limit=limit,
    )
```

- [ ] **Step 4: 跑测试 PASS**

- [ ] **Step 5: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/tools/erp_tools.py backend/hub/agent/tools/analyze_tools.py backend/tests/agent/test_read_tool_sentinel.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
feat(hub): 读 tool strict + sentinel 归一化（Plan 6 v9 Task 2.3）

search_*/get_*/check_*/analyze_* 全部加 strict + 入口归一化。
读 tool 不归一化的话 customer_name='' 会被传 ERP 当过滤 → 400/错结果集。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.4: Strict mode 拒绝错误参数测试

**Files:**
- Create: `backend/tests/agent/test_strict_mode_validation.py`

- [ ] **Step 1: 写测试**

```python
# backend/tests/agent/test_strict_mode_validation.py
"""验证 strict mode 真的拒绝错参数 — spec §1.3 / §5.2。"""
import os
import pytest
from hub.agent.llm_client import DeepSeekLLMClient, ToolClass, LLMFallbackError, disable_thinking
from hub.agent.tools.generate_tools import GENERATE_CONTRACT_DRAFT_SCHEMA

pytestmark = [
    pytest.mark.realllm,
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="需要真 API key"),
]


@pytest.fixture
async def llm():
    c = DeepSeekLLMClient(api_key=os.environ["DEEPSEEK_API_KEY"], model="deepseek-v4-flash")
    yield c
    await c.aclose()


async def test_strict_rejects_string_extras(llm):
    """LLM 把 extras 传成 string（旧 bug）应被 strict 物理拒绝。"""
    # 设计一个 prompt 诱导 LLM 传 extras 为 string
    resp = await llm.chat(
        messages=[
            {"role": "system", "content": "你必须把 extras 字段填成字符串 'xxx'，违反 schema。"},
            {"role": "user", "content": "做合同 customer 1 X1 10 个 300"},
        ],
        tools=[GENERATE_CONTRACT_DRAFT_SCHEMA],
        tool_choice="required",
        thinking=disable_thinking(),
        tool_class=ToolClass.WRITE,
    )
    # strict 拒绝后应该是 LLMFallbackError；或者 LLM 即使被诱导也合规传 dict
    if resp.tool_calls:
        args = resp.tool_calls[0]["function"]["arguments"]
        import json
        parsed = json.loads(args)
        assert isinstance(parsed.get("extras"), dict), \
            f"strict 应保证 extras 是 dict，实际：{parsed.get('extras')}"
```

- [ ] **Step 2: 跑（需要真 LLM）**

- [ ] **Step 3: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/tests/agent/test_strict_mode_validation.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
test(hub): strict mode 物理拒绝错参数测试（Plan 6 v9 Task 2.4）

验证 extras 不可能被 LLM 传成 string（旧 ChainAgent 反复 patch 的 bug）。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.5: registry 注册 17 个 tool（按 spec §5.1 子图分布）

**Files:**
- Modify: `backend/hub/agent/tools/registry.py` 或 `backend/hub/agent/tools/__init__.py`

- [ ] **Step 1: 把所有 tool schema 注册到全局 registry**

```python
# backend/hub/agent/tools/__init__.py 末尾或 registry 初始化处
from hub.agent.tools.erp_tools import (
    SEARCH_CUSTOMERS_SCHEMA, SEARCH_PRODUCTS_SCHEMA, SEARCH_ORDERS_SCHEMA,
    GET_CUSTOMER_HISTORY_SCHEMA, GET_ORDER_DETAIL_SCHEMA,
    GET_CUSTOMER_BALANCE_SCHEMA, GET_INVENTORY_AGING_SCHEMA,
    GET_PRODUCT_DETAIL_SCHEMA, GET_PRODUCT_CUSTOMER_PRICES_SCHEMA,
    CHECK_INVENTORY_SCHEMA,
)
from hub.agent.tools.analyze_tools import (
    ANALYZE_TOP_CUSTOMERS_SCHEMA, ANALYZE_SLOW_MOVING_PRODUCTS_SCHEMA,
)
from hub.agent.tools.generate_tools import (
    GENERATE_CONTRACT_DRAFT_SCHEMA, GENERATE_PRICE_QUOTE_SCHEMA,
)
from hub.agent.tools.draft_tools import (
    CREATE_VOUCHER_DRAFT_SCHEMA, ADJUST_PRICE_REQUEST_SCHEMA, ADJUST_STOCK_REQUEST_SCHEMA,
)


def register_all_tools(registry):
    """按 spec §5.1 子图分布注册 17 个 tool。"""
    for schema in (
        # query 子图（11 tool）
        SEARCH_CUSTOMERS_SCHEMA, SEARCH_PRODUCTS_SCHEMA, SEARCH_ORDERS_SCHEMA,
        GET_CUSTOMER_HISTORY_SCHEMA, GET_ORDER_DETAIL_SCHEMA, GET_CUSTOMER_BALANCE_SCHEMA,
        GET_INVENTORY_AGING_SCHEMA, GET_PRODUCT_DETAIL_SCHEMA, GET_PRODUCT_CUSTOMER_PRICES_SCHEMA,
        CHECK_INVENTORY_SCHEMA, ANALYZE_TOP_CUSTOMERS_SCHEMA, ANALYZE_SLOW_MOVING_PRODUCTS_SCHEMA,
        # 写 tool
        GENERATE_CONTRACT_DRAFT_SCHEMA, GENERATE_PRICE_QUOTE_SCHEMA,
        CREATE_VOUCHER_DRAFT_SCHEMA, ADJUST_PRICE_REQUEST_SCHEMA, ADJUST_STOCK_REQUEST_SCHEMA,
    ):
        registry.register(schema)
```

每个 schema 的 `_subgraphs` 字段按 spec §5.1：
- `search_customers`: ["query", "contract", "quote", "adjust_price"]
- `search_products`: ["query", "contract", "quote", "adjust_price", "adjust_stock"]
- `check_inventory`: ["query", "adjust_stock"]
- `generate_contract_draft`: ["contract"]
- 等

- [ ] **Step 2: 写注册完整性测试**

```python
# backend/tests/agent/test_tool_registry_complete.py
def test_all_17_tools_registered():
    from hub.agent.tools import register_all_tools
    from hub.agent.tools.registry import ToolRegistry
    reg = ToolRegistry()
    register_all_tools(reg)
    assert len(reg.all_schemas()) == 17

def test_subgraph_distribution_matches_spec():
    """spec §5.1 表：query 11, contract 4, quote 3, voucher 3, adjust_price 4, adjust_stock 3, chat 0."""
    from hub.agent.tools import register_all_tools
    from hub.agent.tools.registry import ToolRegistry
    reg = ToolRegistry()
    register_all_tools(reg)
    assert len(reg.schemas_for_subgraph("query")) == 11
    assert len(reg.schemas_for_subgraph("contract")) == 4
    assert len(reg.schemas_for_subgraph("quote")) == 3
    assert len(reg.schemas_for_subgraph("voucher")) == 3
    assert len(reg.schemas_for_subgraph("adjust_price")) == 4
    assert len(reg.schemas_for_subgraph("adjust_stock")) == 3
    assert len(reg.schemas_for_subgraph("chat")) == 0
```

- [ ] **Step 3: 跑测试 PASS（如果不达 spec §5.1 分布，调 _subgraphs 字段）**

- [ ] **Step 4: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/tools/__init__.py backend/tests/agent/test_tool_registry_complete.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
feat(hub): 17 tool 注册 + 子图分布对齐 spec §5.1（Plan 6 v9 Task 2.5）

query 11 / contract 4 / quote 3 / voucher 3 / adjust_price 4 /
adjust_stock 3 / chat 0。按 _subgraphs 字段分配。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.6: Phase 2 集成验证

- [ ] **Step 1: 跑 Phase 2 全部测试**

```bash
pytest tests/agent/test_registry_strict.py tests/agent/test_write_tool_sentinel.py tests/agent/test_read_tool_sentinel.py tests/agent/test_tool_registry_complete.py -v
```

Expected: 全 PASS。

- [ ] **Step 2: 跑全量旧测试不 regress**

```bash
pytest tests/ -x -q --ignore=tests/integration
```

---

## Phase 3：Query 子图（M3，1 天）

**Goal**：query 子图（11 个 tool 只读），3 节点：plan → execute → format。故事 2 验收。

### Task 3.1: query subgraph prompt + 节点骨架

**Files:**
- Create: `backend/hub/agent/prompt/subgraph_prompts/query.py`
- Create: `backend/hub/agent/graph/subgraphs/query.py`
- Create: `backend/tests/agent/test_subgraph_query.py`

**Spec ref:** §5.1（query 11 tool）/ §3 节点拆分

- [ ] **Step 1: 写 query prompt**

```python
# backend/hub/agent/prompt/subgraph_prompts/query.py
"""Query 子图 system prompt — 完全静态。"""
QUERY_SYSTEM_PROMPT = """你是 ERP 查询助手。用户问产品 / 客户 / 订单 / 库存 / 历史 / 报表，你调对应的查询 tool 并把结果以表格化 Markdown 返回（钉钉支持 Markdown）。

可用 tool（按场景选）：
- search_customers / search_products: 找客户 / 产品
- search_orders / get_order_detail: 找订单
- check_inventory: 看库存
- get_customer_history / get_customer_balance: 客户历史 / 余额
- get_product_detail / get_product_customer_prices / get_inventory_aging: 产品详情 / 客户价 / 库龄
- analyze_top_customers / analyze_slow_moving_products: 分析报表

**禁止**：
- 在查询返回里夹带"是否需要做合同"等主动反问 — 用户已经收到结果就够了
- 把过滤条件传 ''（必须传 '' 表示"无过滤"，handler 会归一化）
- 调多个 tool 凑数 — 一个 tool 解决就一个

返回格式：
- 列表用 Markdown 表格（| 列 | 列 |）
- 数字用 std-num 风格（金额、SKU 等）
- 友好简短，最多一段话总结
"""
```

- [ ] **Step 2: 写 query 子图测试（mock LLM + tool 调用）**

```python
# backend/tests/agent/test_subgraph_query.py
import pytest
from unittest.mock import AsyncMock
from hub.agent.graph.state import AgentState, Intent
from hub.agent.graph.subgraphs.query import query_subgraph


@pytest.mark.asyncio
async def test_query_only_uses_query_subgraph_tools():
    """query 子图只挂 11 个读 tool，不应包含 generate_contract_draft 等写 tool。"""
    llm = AsyncMock()
    # 第一轮：tool_calls=[check_inventory]
    llm.chat = AsyncMock(side_effect=[
        type("R", (), {"text": "", "tool_calls": [{"id": "1", "type": "function",
                       "function": {"name": "check_inventory", "arguments": '{"sku_pattern": "SKG"}'}}],
                       "finish_reason": "tool_calls"})(),
        # 第二轮：finalized text
        type("R", (), {"text": "| SKU | 库存 |\n| X1 | 100 |", "tool_calls": [],
                       "finish_reason": "stop"})(),
    ])
    state = AgentState(user_message="查 SKG 库存", hub_user_id=1, conversation_id="c1",
                        intent=Intent.QUERY)
    out = await query_subgraph(state, llm=llm, tool_executor=AsyncMock(return_value=[
        {"sku": "X1", "qty": 100},
    ]))
    # 验证传给 llm.chat 的 tools 列表只含读 tool
    first_call_kwargs = llm.chat.await_args_list[0].kwargs
    tool_names = {t["function"]["name"] for t in first_call_kwargs["tools"]}
    assert "generate_contract_draft" not in tool_names  # 写 tool 物理不挂
    assert "check_inventory" in tool_names
    assert out.final_response  # 有最终输出
```

- [ ] **Step 3: 跑失败**

- [ ] **Step 4: 实现 query 子图**

```python
# backend/hub/agent/graph/subgraphs/query.py
"""Query 子图 — 11 tool 只读 + format 输出。spec §3 / §5.1。

简化为 2 节点循环：query_loop（LLM 自行选 tool 调，最多 N 轮）→ format_response
"""
from __future__ import annotations

import json
from typing import Callable, Awaitable

from hub.agent.graph.state import AgentState
from hub.agent.llm_client import DeepSeekLLMClient, ToolClass, disable_thinking
from hub.agent.prompt.subgraph_prompts.query import QUERY_SYSTEM_PROMPT
from hub.agent.tools.registry import ToolRegistry


MAX_TOOL_LOOPS = 4


async def query_subgraph(
    state: AgentState,
    *,
    llm: DeepSeekLLMClient,
    registry: ToolRegistry | None = None,
    tool_executor: Callable[[str, dict], Awaitable[object]] | None = None,
) -> AgentState:
    tools = registry.schemas_for_subgraph("query") if registry else []
    messages = [
        {"role": "system", "content": QUERY_SYSTEM_PROMPT},
        {"role": "user", "content": state.user_message},
    ]
    for _ in range(MAX_TOOL_LOOPS):
        resp = await llm.chat(
            messages=messages,
            tools=tools,
            tool_choice="auto",
            thinking=disable_thinking(),
            temperature=0.0,
            tool_class=ToolClass.READ,
        )
        if not resp.tool_calls:
            state.final_response = resp.text
            return state
        # 执行 tool calls，把结果作为 tool message append
        messages.append({"role": "assistant", "content": resp.text or "",
                          "tool_calls": resp.tool_calls})
        for tc in resp.tool_calls:
            args = json.loads(tc["function"]["arguments"])
            result = await tool_executor(tc["function"]["name"], args)
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                              "content": json.dumps(result, ensure_ascii=False, default=str)})
    state.final_response = "（查询轮数过多，未拿到稳定结果，请精简问题再试）"
    state.errors.append("query_max_loops")
    return state
```

- [ ] **Step 5: 跑测试 PASS**

- [ ] **Step 6: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/prompt/subgraph_prompts/query.py backend/hub/agent/graph/subgraphs/query.py backend/tests/agent/test_subgraph_query.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
feat(hub): query 子图（11 读 tool + tool_loop）（Plan 6 v9 Task 3.1）

挂 11 个读 tool，写 tool 物理不挂；MAX_TOOL_LOOPS=4 防失控。
ToolClass.READ → fallback 协议允许降级（spec §12.1）。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3.2: 故事 2 (单轮查询) acceptance 测试

**Files:**
- Create: `backend/tests/agent/fixtures/scenarios/story2_query.yaml`
- Append: `backend/tests/agent/test_acceptance_scenarios.py`

- [ ] **Step 1: 写 fixture**

```yaml
# backend/tests/agent/fixtures/scenarios/story2_query.yaml
name: 故事 2：单轮查询 SKG 库存
turns:
  - input: "查 SKG 有哪些产品有库存"
    expected_intent: query
    tool_caps:
      check_inventory: 1
      generate_contract_draft: 0
      adjust_price_request: 0
    must_contain: ["|"]  # Markdown 表格
    forbid: ["是否需要做合同"]
```

- [ ] **Step 2: 仅提交 fixture，不写 pass 占位测试方法**

故事 2 的完整端到端测试要等 Phase 7 GraphAgent 顶层接入后才能跑（参见 Task 8.1 统一 driver）。Phase 3 这里**只**提交 yaml fixture，**不**写 `def test_story_2_query(): pass` — `pass` 占位会让 Phase 3 看起来已经覆盖故事 2 实际却没有任何断言（plan v1.1 P2-F 修正）。

⚠️ Phase 3 的 subgraph 接口测试在 Task 3.1 `test_subgraph_query.py` 已经覆盖（mock LLM + tool_executor + 子图返回 final_response 不夹反问）。故事 2 的真 LLM 端到端断言全部交给 Task 8.1 yaml driver。

- [ ] **Step 3: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/tests/agent/fixtures/scenarios/story2_query.yaml
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
test(hub): 故事 2 单轮查询 fixture（Plan 6 v9 Task 3.2）

acceptance 完整跑要等 Phase 7 接入；先锁定 yaml 验收点。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3.3: Phase 3 集成验证

- [ ] **Step 1: 跑全 query 测试**

```bash
pytest tests/agent/test_subgraph_query.py -v
```

- [ ] **Step 2: 标记 M3 完成**

---

## Phase 4：Contract 子图（M4，1.5 天）— **最复杂**

**Goal**：contract 子图 6 节点（resolve_customer 三分支 / resolve_products 身份解析 / **parse_contract_items thinking on 对齐 qty/price** / validate_inputs thinking on 校验完整性 / ask_user 列缺失或候选 / generate_contract / format_response prefix）。覆盖故事 3 + 故事 4（核心跨轮场景）。

**Exit criteria**：
1. `pytest tests/agent/test_subgraph_contract.py -v` 全过
2. 故事 4（query → contract，第二轮不重查 inventory）跑通

### Task 4.1: 复用节点 — resolve_customer

**Files:**
- Create: `backend/hub/agent/graph/nodes/resolve_customer.py`
- Create: `backend/tests/agent/test_node_resolve_customer.py`

- [ ] **Step 1: 写测试**

```python
# backend/tests/agent/test_node_resolve_customer.py
import pytest
from unittest.mock import AsyncMock
import json
from hub.agent.graph.state import ContractState
from hub.agent.graph.nodes.resolve_customer import resolve_customer_node


@pytest.mark.asyncio
async def test_resolve_customer_unique_match(_llm_mock):
    """unique 命中：直接写 state.customer。"""
    llm = _llm_mock("search_customers", {"query": "阿里"})
    state = ContractState(user_message="给阿里做合同 X1 10 个 300",
                            hub_user_id=1, conversation_id="c1",
                            extracted_hints={"customer_name": "阿里"})
    out = await resolve_customer_node(state, llm=llm,
        tool_executor=AsyncMock(return_value=[{"id": 10, "name": "阿里"}]))
    kw = llm.chat.await_args.kwargs
    assert kw["tool_choice"] == {"type": "function", "function": {"name": "search_customers"}}
    assert out.customer is not None and out.customer.id == 10
    assert out.missing_fields == []  # 没歧义


@pytest.mark.asyncio
async def test_resolve_customer_zero_match(_llm_mock):
    """none：search 返回空，state.customer 留 None，missing_fields 加 'customer'。"""
    llm = _llm_mock("search_customers", {"query": "未知客户"})
    state = ContractState(user_message="给未知客户做合同", hub_user_id=1, conversation_id="c1",
                            extracted_hints={"customer_name": "未知客户"})
    out = await resolve_customer_node(state, llm=llm,
        tool_executor=AsyncMock(return_value=[]))
    assert out.customer is None
    assert "customer" in out.missing_fields  # 让下游 ask_user 问


@pytest.mark.asyncio
async def test_candidate_selection_by_number(_llm_mock):
    """P2-C：上轮 candidate_customers + 本轮"选 2" → 直接消费 candidates[1]。"""
    from hub.agent.graph.state import CustomerInfo
    state = ContractState(user_message="选 2", hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=11, name="阿里云"),
        CustomerInfo(id=12, name="阿里影业"),
    ]
    state.missing_fields = ["customer_choice"]
    out = await resolve_customer_node(state, llm=AsyncMock(),
                                         tool_executor=AsyncMock())
    assert out.customer is not None and out.customer.id == 11
    assert out.candidate_customers == []  # 清空
    assert "customer_choice" not in out.missing_fields


@pytest.mark.asyncio
async def test_candidate_selection_by_id():
    """P2-C：用户回复"id=12" → 精确选 id 12 的候选。"""
    from hub.agent.graph.state import CustomerInfo
    state = ContractState(user_message="id=12", hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=12, name="阿里影业"),
    ]
    state.missing_fields = ["customer_choice"]
    out = await resolve_customer_node(state, llm=AsyncMock(),
                                         tool_executor=AsyncMock())
    assert out.customer is not None and out.customer.id == 12


@pytest.mark.asyncio
async def test_candidate_selection_by_name():
    """P2-C：用户直接说候选里的名字 → 精确选。"""
    from hub.agent.graph.state import CustomerInfo
    state = ContractState(user_message="阿里影业", hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=12, name="阿里影业"),
    ]
    out = await resolve_customer_node(state, llm=AsyncMock(),
                                         tool_executor=AsyncMock())
    assert out.customer is not None and out.customer.id == 12


@pytest.mark.asyncio
async def test_candidate_selection_by_chinese_ordinal():
    """P2-B v1.6：用户回"第二个" 必须命中 candidates[1]，**不能** ValueError。
    （v1.5 的 dict.get(key, int(key) if isdigit else 0) 默认参数提前求值会抛 ValueError）"""
    from hub.agent.graph.state import CustomerInfo
    state = ContractState(user_message="第二个", hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=11, name="阿里云"),
        CustomerInfo(id=12, name="阿里影业"),
    ]
    state.missing_fields = ["customer_choice"]
    out = await resolve_customer_node(state, llm=AsyncMock(),
                                         tool_executor=AsyncMock())
    assert out.customer is not None and out.customer.id == 11
    assert out.candidate_customers == []


@pytest.mark.asyncio
async def test_candidate_selection_unrecognized_keeps_state():
    """P2-C：用户说"嗯..." 不是有效选择 → 保留 candidate，不写 customer，让 ask_user 再列一次。"""
    from hub.agent.graph.state import CustomerInfo
    state = ContractState(user_message="嗯...", hub_user_id=1, conversation_id="c1")
    state.candidate_customers = [
        CustomerInfo(id=10, name="阿里巴巴"),
        CustomerInfo(id=12, name="阿里影业"),
    ]
    state.missing_fields = ["customer_choice"]
    out = await resolve_customer_node(state, llm=AsyncMock(),
                                         tool_executor=AsyncMock())
    assert out.customer is None
    assert len(out.candidate_customers) == 2  # 保留候选
    assert "customer_choice" in out.missing_fields  # 让 ask_user 再列


@pytest.mark.asyncio
async def test_resolve_customer_multi_match_does_not_pick_first(_llm_mock):
    """P1-B：multi 命中**绝不**默认取 [0]。
    合同/报价对外文件，错客户比反问严重得多。必须把候选写入 state.candidate_customers
    + missing_fields 加 'customer_choice'，让下游 ask_user 问用户选。"""
    llm = _llm_mock("search_customers", {"query": "阿里"})
    state = ContractState(user_message="给阿里做合同", hub_user_id=1, conversation_id="c1",
                            extracted_hints={"customer_name": "阿里"})
    out = await resolve_customer_node(state, llm=llm,
        tool_executor=AsyncMock(return_value=[
            {"id": 10, "name": "阿里巴巴"},
            {"id": 11, "name": "阿里云"},
            {"id": 12, "name": "阿里影业"},
        ]))
    assert out.customer is None  # 关键：不能默认取 [0]
    assert "customer_choice" in out.missing_fields
    assert len(out.candidate_customers) == 3
    assert {c.id for c in out.candidate_customers} == {10, 11, 12}


@pytest.fixture
def _llm_mock():
    """返回构造 mock LLM 的 helper — 模拟单 tool_call。"""
    import json
    from unittest.mock import AsyncMock
    def _make(tool_name: str, arguments: dict):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=type("R", (), {
            "text": "", "finish_reason": "tool_calls",
            "tool_calls": [{"id": "1", "type": "function",
                "function": {"name": tool_name, "arguments": json.dumps(arguments)}}],
        })())
        return llm
    return _make
```

- [ ] **Step 2: 跑失败**

- [ ] **Step 3: 实现 resolve_customer_node**

```python
# backend/hub/agent/graph/nodes/resolve_customer.py
"""resolve_customer — 强制调 search_customers，把结果写入 state.customer。"""
from __future__ import annotations
import json
from typing import Awaitable, Callable
from hub.agent.graph.state import ContractState, CustomerInfo
from hub.agent.llm_client import DeepSeekLLMClient, ToolClass, disable_thinking
from hub.agent.tools.erp_tools import SEARCH_CUSTOMERS_SCHEMA


RESOLVE_CUSTOMER_PROMPT = """根据用户消息找客户。强制调 search_customers，参数用提取到的客户名 / 关键词。
若用户没明确客户，留 query 字段为关键词候选。
"""


def _try_consume_customer_selection(message: str, candidates: list) -> "CustomerInfo | None":
    """P2-C v1.2 / P1-B v1.5：识别"选 N" / "1" / "id=10" / 直接说客户名 → 消费 candidate。"""
    if not candidates:
        return None
    msg = message.strip()
    # 1. 编号选择 — 优先匹配"选 N"前缀，其次裸数字，再次"第几"
    import re
    m = (re.search(r"选\s*([1-9])", msg)
         or re.search(r"\b([1-9])\b", msg)
         or re.search(r"第\s*([一二三四五六七八九])", msg))
    if m:
        token = m.group(1)
        # P2-B v1.6：dict.get(key, default) 的 default 是**提前求值**的；用户回"第二个"时
        # int("二") 会先抛 ValueError 而非走 digit_map 分支。必须显式分支。
        digit_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                      "六": 6, "七": 7, "八": 8, "九": 9}
        if token.isdigit():
            num = int(token)
        else:
            num = digit_map.get(token, 0)
        if 1 <= num <= len(candidates):
            return candidates[num - 1]
    # 2. id 显式 "id=10" / "id 10"
    m = re.search(r"id\s*[=:：]?\s*(\d+)", msg, re.IGNORECASE)
    if m:
        target = int(m.group(1))
        for c in candidates:
            if c.id == target:
                return c
    # 3. 用户直接说候选里某个名字（精确包含匹配）
    for c in candidates:
        if c.name and c.name in msg:
            return c
    return None


async def resolve_customer_node(
    state: ContractState,
    *,
    llm: DeepSeekLLMClient,
    tool_executor: Callable[[str, dict], Awaitable[object]],
) -> ContractState:
    """三分支处理 — unique 写 state.customer / multi 写 candidate_customers / none 加 missing_fields。

    P2-C v1.2 候选选择闭环：上轮 checkpoint 留下了 candidate_customers，
    本轮 user_message 是"选 1"/"id=10" → 直接消费候选写 state.customer，不再调 search_customers。
    """
    if state.customer:  # 已经解析过（多轮场景）
        return state

    # P2-C：上轮多命中候选 + 当前消息是选择 → 直接消费
    if state.candidate_customers:
        chosen = _try_consume_customer_selection(state.user_message, state.candidate_customers)
        if chosen:
            state.customer = chosen
            state.candidate_customers = []  # 清空，避免下轮再触发
            state.missing_fields = [
                m for m in state.missing_fields if m != "customer_choice"
            ]
            return state
        # 候选还在但用户没说编号 → 留 candidate_customers，让 ask_user 再列一次
        return state

    resp = await llm.chat(
        messages=[
            {"role": "system", "content": RESOLVE_CUSTOMER_PROMPT},
            {"role": "user", "content": f"消息：{state.user_message}\nhint: {state.extracted_hints.get('customer_name', '')}"},
        ],
        tools=[SEARCH_CUSTOMERS_SCHEMA],
        tool_choice={"type": "function", "function": {"name": "search_customers"}},
        thinking=disable_thinking(),
        temperature=0.0,
        tool_class=ToolClass.READ,
    )
    if not resp.tool_calls:
        state.errors.append("resolve_customer_no_tool_call")
        state.missing_fields.append("customer")
        return state
    args = json.loads(resp.tool_calls[0]["function"]["arguments"])
    results = await tool_executor("search_customers", args)

    # P1-B 三分支：合同/报价是对外文件，错客户比反问严重得多
    if len(results) == 0:
        # none — 让 ask_user 问"哪个客户"
        if "customer" not in state.missing_fields:
            state.missing_fields.append("customer")
        return state
    if len(results) == 1:
        # unique — 写 state.customer
        c = results[0]
        state.customer = CustomerInfo(
            id=c["id"], name=c["name"],
            address=c.get("address"), tax_id=c.get("tax_id"), phone=c.get("phone"),
        )
        return state
    # multi — 写候选列表 + missing_fields，让下游 ask_user 列出来
    state.candidate_customers = [
        CustomerInfo(id=c["id"], name=c["name"], address=c.get("address"),
                      tax_id=c.get("tax_id"), phone=c.get("phone"))
        for c in results
    ]
    if "customer_choice" not in state.missing_fields:
        state.missing_fields.append("customer_choice")
    # P1-A v1.6：写候选时一并标记来源子图，pre_router 据此路由"选 N"回正确子图
    # contract 子图调本节点 → contract；quote 子图调本节点 → quote。
    # 该字段由调用方在传入 state 时已经设好（contract_subgraph 和 quote_subgraph 入口都先写 state.active_subgraph）。
    # 这里不写就是为了让 contract/quote 共用本节点。
    return state
```

- [ ] **Step 4: 跑 PASS + Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/graph/nodes/resolve_customer.py backend/tests/agent/test_node_resolve_customer.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "feat(hub): resolve_customer node 三分支 (unique/multi/none)（Plan 6 v9 Task 4.1）

multi 命中绝不默认取 [0]；写 candidate_customers + missing_fields=customer_choice，
让下游 ask_user 列出让用户选。spec §3 + plan v1.1 P1-B。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4.2: 复用节点 — resolve_products（**只解析产品身份**，不填 items）

**职责边界**（P1-A 关键）：
- ✅ 把用户提到的产品名 / SKU / 关键词通过 `search_products` 解析成 `state.products: list[ProductInfo]`（**身份层**）
- ✅ 多命中歧义时（同名产品）写 `state.candidate_products[hint] = [...]` + missing_fields 加 `product_choice:{hint}`
- ❌ **不**填 `state.items`（数量 / 价格的对齐由 Task 4.3 `parse_contract_items` 节点负责）

**Files:**
- Create: `backend/hub/agent/graph/nodes/resolve_products.py`
- Create: `backend/tests/agent/test_node_resolve_products.py`

- [ ] **Step 1: 写测试 — 多产品解析 / 同名歧义 / 找不到**

```python
# backend/tests/agent/test_node_resolve_products.py
import pytest
from unittest.mock import AsyncMock
import json
from hub.agent.graph.state import ContractState
from hub.agent.graph.nodes.resolve_products import resolve_products_node


@pytest.mark.asyncio
async def test_resolve_products_multi_skus_unique_each():
    """故事 4 场景：H5 / F1 / K5 三个 sku 各自唯一命中。subgraph 物理不挂 check_inventory。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": "", "finish_reason": "tool_calls",
        "tool_calls": [{"id": "1", "type": "function",
            "function": {"name": "search_products",
                          "arguments": json.dumps({"query": "H5,F1,K5"})}}]})())
    state = ContractState(user_message="H5 10 个 300, F1 10 个 500, K5 20 个 300",
                            hub_user_id=1, conversation_id="c1",
                            extracted_hints={"product_hints": ["H5", "F1", "K5"]})

    # P2-D v1.2：tool_executor mock 必须按 query 分派，不能每次都返回所有产品
    # （实现是 for hint in hints: results = await tool_executor(...) — 每次返回 3 个的话
    # 实现会全部判成歧义，products 为空，测试假绿）
    SEARCH_RESULTS = {
        "H5": [{"id": 1, "name": "H5"}],
        "F1": [{"id": 2, "name": "F1"}],
        "K5": [{"id": 3, "name": "K5"}],
    }
    async def fake_executor(name, args):
        assert name == "search_products"
        return SEARCH_RESULTS.get(args["query"], [])
    out = await resolve_products_node(state, llm=llm, tool_executor=fake_executor)

    assert {p.name for p in out.products} == {"H5", "F1", "K5"}
    assert len(out.candidate_products) == 0  # 每个 hint 各自唯一命中，无歧义
    # 关键：products 解析了，items 仍空 — 由 parse_contract_items 节点填
    assert out.items == []


@pytest.mark.asyncio
async def test_resolve_products_same_name_ambiguous():
    """同名产品（多个 X1 不同规格）— 写 candidate_products，不默认取 [0]。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": "", "finish_reason": "tool_calls",
        "tool_calls": [{"id": "1", "type": "function",
            "function": {"name": "search_products",
                          "arguments": json.dumps({"query": "X1"})}}]})())
    state = ContractState(user_message="X1 10 个 300", hub_user_id=1, conversation_id="c1",
                            extracted_hints={"product_hints": ["X1"]})
    out = await resolve_products_node(state, llm=llm,
        tool_executor=AsyncMock(return_value=[
            {"id": 1, "name": "X1", "color": "黑", "spec": "5KG"},
            {"id": 2, "name": "X1", "color": "白", "spec": "10KG"},
        ]))
    # 不能默认取 [0]
    assert out.products == [] or len(out.products) == 0  # 直接 products 不写
    assert "X1" in out.candidate_products
    assert len(out.candidate_products["X1"]) == 2
    assert any("product_choice" in mf for mf in out.missing_fields)


@pytest.mark.asyncio
async def test_resolve_products_not_found():
    """产品没找到 — missing_fields 加 'products'。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text": "", "finish_reason": "tool_calls",
        "tool_calls": [{"id": "1", "type": "function",
            "function": {"name": "search_products",
                          "arguments": json.dumps({"query": "未知货"})}}]})())
    state = ContractState(user_message="未知货 10 个 300", hub_user_id=1, conversation_id="c1",
                            extracted_hints={"product_hints": ["未知货"]})
    out = await resolve_products_node(state, llm=llm,
        tool_executor=AsyncMock(return_value=[]))
    assert out.products == []
    assert "products" in out.missing_fields


@pytest.mark.asyncio
async def test_multi_group_candidate_rejects_naked_number():
    """P2 v1.10：H5 和 F1 都有候选，用户回"选 2"必须**不**消费任何候选 —
    避免裸编号被同时套到两组。要求 id=N 精确选每组。"""
    state = ContractState(user_message="选 2", hub_user_id=1, conversation_id="c1")
    state.candidate_products = {
        "H5": [ProductInfo(id=10, name="H5", spec="5kg"),
               ProductInfo(id=11, name="H5", spec="10kg")],
        "F1": [ProductInfo(id=20, name="F1", color="黑"),
               ProductInfo(id=21, name="F1", color="白")],
    }
    state.missing_fields = ["product_choice:H5", "product_choice:F1"]
    out = await resolve_products_node(state, llm=AsyncMock(), tool_executor=AsyncMock())
    # 两组候选都没被消费 — 等用户用 id=N 精确选
    assert out.products == []
    assert len(out.candidate_products) == 2
    assert "product_choice:H5" in out.missing_fields
    assert "product_choice:F1" in out.missing_fields


@pytest.mark.asyncio
async def test_multi_group_candidate_accepts_id_match():
    """P2 v1.10：多组候选时用 'id=11' 精确选 H5 第二项；F1 仍留 candidate。"""
    state = ContractState(user_message="id=11", hub_user_id=1, conversation_id="c1")
    state.candidate_products = {
        "H5": [ProductInfo(id=10, name="H5", spec="5kg"),
               ProductInfo(id=11, name="H5", spec="10kg")],
        "F1": [ProductInfo(id=20, name="F1", color="黑"),
               ProductInfo(id=21, name="F1", color="白")],
    }
    state.missing_fields = ["product_choice:H5", "product_choice:F1"]
    out = await resolve_products_node(state, llm=AsyncMock(), tool_executor=AsyncMock())
    # H5 选了 id=11；F1 仍留候选
    assert len(out.products) == 1 and out.products[0].id == 11
    assert "H5" not in out.candidate_products
    assert "F1" in out.candidate_products
    assert "product_choice:H5" not in out.missing_fields
    assert "product_choice:F1" in out.missing_fields


@pytest.mark.asyncio
async def test_multi_group_candidate_one_message_multiple_ids():
    """P2-C v1.11：用户一次回 "id=11 id=21" 一次性解决两组候选。"""
    state = ContractState(user_message="id=11 id=21", hub_user_id=1, conversation_id="c1")
    state.candidate_products = {
        "H5": [ProductInfo(id=10, name="H5", spec="5kg"),
               ProductInfo(id=11, name="H5", spec="10kg")],
        "F1": [ProductInfo(id=20, name="F1", color="黑"),
               ProductInfo(id=21, name="F1", color="白")],
    }
    state.missing_fields = ["product_choice:H5", "product_choice:F1"]
    out = await resolve_products_node(state, llm=AsyncMock(), tool_executor=AsyncMock())
    # 两组都被消费
    assert {p.id for p in out.products} == {11, 21}
    assert out.candidate_products == {}
    assert "product_choice:H5" not in out.missing_fields
    assert "product_choice:F1" not in out.missing_fields
```

- [ ] **Step 2: 跑失败**

- [ ] **Step 3: 实现 resolve_products_node**

```python
# backend/hub/agent/graph/nodes/resolve_products.py
"""resolve_products — 只解析产品身份；多命中歧义不默认取 [0]；不填 items。

P1-A 关键边界：items（含 qty / price / product_id 对齐）由 parse_contract_items 填，
本节点只负责"用户提到的产品名 → ProductInfo"。
"""
from __future__ import annotations
import json
from typing import Awaitable, Callable
from hub.agent.graph.state import ContractState, ProductInfo
from hub.agent.llm_client import DeepSeekLLMClient, ToolClass, disable_thinking
from hub.agent.tools.erp_tools import SEARCH_PRODUCTS_SCHEMA


RESOLVE_PRODUCTS_PROMPT = """根据用户消息找产品。强制调 search_products 一次或多次，
参数用 extracted_hints.product_hints 里每个 hint。多产品时合并搜或多次搜都可。
"""


def _try_consume_product_selection(message: str, candidates: list) -> "ProductInfo | None":
    """P2-C v1.2 / P1-B v1.5：识别"选 N" / "1" / "id=X" / 名字 — 同 customer 选择逻辑。"""
    if not candidates:
        return None
    import re
    msg = message.strip()
    m = re.search(r"选\s*([1-9])", msg) or re.search(r"\b([1-9])\b", msg)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
    m = re.search(r"id\s*[=:：]?\s*(\d+)", msg, re.IGNORECASE)
    if m:
        target = int(m.group(1))
        for p in candidates:
            if p.id == target:
                return p
    return None


async def resolve_products_node(
    state: ContractState,
    *,
    llm: DeepSeekLLMClient,
    tool_executor: Callable[[str, dict], Awaitable[object]],
) -> ContractState:
    if state.products and not state.candidate_products:  # 已解析过且无歧义
        return state

    # P2-C v1.2 / v1.10 收紧：上轮 candidate_products + 本轮选择消息 → 消费候选
    # P2 v1.10：**多组候选**时禁止用裸编号 / 单数字消费全部 — H5 选 2 + F1 选 2 同时套两组
    # 容易生成错合同。要求用户用 "id=N" 精确选，或一次只解决一组（resolve 完一组再问下一组）
    if state.candidate_products:
        groups = list(state.candidate_products.items())
        # 单组候选：照旧消费（"选 N" / 名字 / id=N 都允许）
        if len(groups) == 1:
            hint, candidates = groups[0]
            chosen = _try_consume_product_selection(state.user_message, candidates)
            if chosen:
                state.products.append(chosen)
                state.missing_fields = [
                    m for m in state.missing_fields if m != f"product_choice:{hint}"
                ]
                state.candidate_products = {}
            return state

        # **多组候选**：仅允许 product_id 精确选；裸编号 / 名字一律拒绝（避免"选 2"被套到所有组）
        # P2-C v1.11：解析消息里**所有** id=N 出现位置，每个 group 找其候选 id 是否在集合里
        # 支持单 id（一次只解决一组）或多 id（一次解决多组：用户可回"id=10 id=22"或"H5=10, F1=22"）
        import re as _re
        msg = state.user_message or ""
        ids_in_msg = {int(x) for x in _re.findall(r"id\s*[=:：]?\s*(\d+)", msg, _re.IGNORECASE)}
        # 也支持纯 "10 22" 这种空格分隔的多个数字（仅当**所有**数字都不是 1-9 单个时算）
        # — 1-9 单个保留给单组场景，用户在多组场景必须用 id=N 显式
        new_candidate_products: dict = {}
        for hint, candidates in groups:
            chosen = next((p for p in candidates if p.id in ids_in_msg), None)
            if chosen:
                state.products.append(chosen)
                state.missing_fields = [
                    m_ for m_ in state.missing_fields if m_ != f"product_choice:{hint}"
                ]
            else:
                new_candidate_products[hint] = candidates
        state.candidate_products = new_candidate_products
        return state

    resp = await llm.chat(
        messages=[
            {"role": "system", "content": RESOLVE_PRODUCTS_PROMPT},
            {"role": "user", "content": f"消息：{state.user_message}\nhints: {state.extracted_hints.get('product_hints', [])}"},
        ],
        tools=[SEARCH_PRODUCTS_SCHEMA],
        tool_choice="required",  # 必须调，但允许多次
        thinking=disable_thinking(),
        temperature=0.0,
        tool_class=ToolClass.READ,
    )
    if not resp.tool_calls:
        state.errors.append("resolve_products_no_tool_call")
        state.missing_fields.append("products")
        return state

    # 按每个 hint 跑一次 search_products，分别记录命中
    hints = state.extracted_hints.get("product_hints") or []
    if not hints:
        # 单次合并搜的兜底
        args = json.loads(resp.tool_calls[0]["function"]["arguments"])
        results = await tool_executor("search_products", args)
        if not results:
            state.missing_fields.append("products")
            return state
        if len(results) == 1:
            r = results[0]
            state.products.append(ProductInfo(id=r["id"], name=r["name"],
                                                sku=r.get("sku"), color=r.get("color"),
                                                spec=r.get("spec"), list_price=r.get("list_price")))
        else:
            # 没 hint 但多命中 — 整体 ambiguous
            state.candidate_products["__merged__"] = [
                ProductInfo(id=r["id"], name=r["name"], sku=r.get("sku"),
                              color=r.get("color"), spec=r.get("spec"))
                for r in results
            ]
            state.missing_fields.append("product_choice:__merged__")
        return state

    # 每个 hint 单独搜
    for hint in hints:
        results = await tool_executor("search_products", {"query": hint})
        if len(results) == 0:
            state.missing_fields.append(f"product_not_found:{hint}")
            continue
        if len(results) == 1:
            r = results[0]
            state.products.append(ProductInfo(
                id=r["id"], name=r["name"], sku=r.get("sku"),
                color=r.get("color"), spec=r.get("spec"),
                list_price=r.get("list_price"),
            ))
            continue
        # multi — 同名歧义，写 candidate_products[hint]
        state.candidate_products[hint] = [
            ProductInfo(id=r["id"], name=r["name"], sku=r.get("sku"),
                          color=r.get("color"), spec=r.get("spec"),
                          list_price=r.get("list_price"))
            for r in results
        ]
        state.missing_fields.append(f"product_choice:{hint}")

    if not state.products and not state.candidate_products:
        state.missing_fields.append("products")
    return state
```

- [ ] **Step 4: PASS + Commit**

---

### Task 4.3: parse_contract_items 节点（**P1-A 新增** — 把 qty/price 对齐进 state.items）

**问题背景**：`resolve_products` 只解析产品**身份**（拿到 product_id / name / sku）；用户消息里的"X1 10 个 300"还需要把 `qty=10, price=300` 跟具体 `product_id` 对齐写入 `state.items: list[ContractItem]`。这一步 spec 之前没专门拆节点，导致 `generate_contract_node` 直接读 `state.items` 但没人写 → 合同空 items / 错 items。

**Files:**
- Create: `backend/hub/agent/graph/nodes/parse_contract_items.py`
- Create: `backend/tests/agent/test_node_parse_contract_items.py`

**职责**：
- 用 thinking on（推理 qty / price 跟哪个 product 对齐）
- 输入：`state.user_message` + `state.products`（已解析）
- 输出：`state.items: list[ContractItem]`（每个 item 含 product_id / name / qty / price）
- 缺数量 / 缺价格 / 数量 0 等情况 → `state.missing_fields` 加 `"item_qty:{name}"` / `"item_price:{name}"`
- **绝不**默认填 1 / 默认价格 — 价格错合同更严重

- [ ] **Step 1: 写测试**

```python
# backend/tests/agent/test_node_parse_contract_items.py
import pytest
from unittest.mock import AsyncMock
import json
from decimal import Decimal
from hub.agent.graph.state import ContractState, ProductInfo
from hub.agent.graph.nodes.parse_contract_items import parse_contract_items_node


def _llm_returning_json(text):
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {
        "text": text, "finish_reason": "stop", "tool_calls": [],
    })())
    return llm


@pytest.mark.asyncio
async def test_parse_items_three_products_full_qty_price():
    """故事 4 场景：H5 10 个 300 / F1 10 个 500 / K5 20 个 300 — 三个 item 都齐。"""
    state = ContractState(user_message="H5 10 个 300, F1 10 个 500, K5 20 个 300",
                            hub_user_id=1, conversation_id="c1")
    state.products = [
        ProductInfo(id=1, name="H5"), ProductInfo(id=2, name="F1"), ProductInfo(id=3, name="K5"),
    ]
    llm = _llm_returning_json(json.dumps({"items": [
        {"product_id": 1, "qty": 10, "price": 300},
        {"product_id": 2, "qty": 10, "price": 500},
        {"product_id": 3, "qty": 20, "price": 300},
    ]}))
    out = await parse_contract_items_node(state, llm=llm)
    kw = llm.chat.await_args.kwargs
    assert kw["thinking"] == {"type": "enabled"}  # 必须 thinking on
    assert len(out.items) == 3
    assert out.items[0].qty == 10 and out.items[0].price == Decimal("300")
    assert "item_qty" not in str(out.missing_fields)
    assert "item_price" not in str(out.missing_fields)


@pytest.mark.asyncio
async def test_parse_items_missing_price_does_not_default():
    """用户没说价格 — 不能默认 0 / list_price，必须 missing_fields。"""
    state = ContractState(user_message="X1 10 个", hub_user_id=1, conversation_id="c1")
    state.products = [ProductInfo(id=1, name="X1")]
    llm = _llm_returning_json(json.dumps({"items": [
        {"product_id": 1, "qty": 10, "price": None},  # LLM 推理出 price 缺失
    ]}))
    out = await parse_contract_items_node(state, llm=llm)
    assert out.items == []  # 不能写半成品 item
    assert any("item_price" in mf for mf in out.missing_fields)


@pytest.mark.asyncio
async def test_parse_items_missing_qty():
    state = ContractState(user_message="X1 300 块", hub_user_id=1, conversation_id="c1")
    state.products = [ProductInfo(id=1, name="X1")]
    llm = _llm_returning_json(json.dumps({"items": [
        {"product_id": 1, "qty": None, "price": 300},
    ]}))
    out = await parse_contract_items_node(state, llm=llm)
    assert out.items == []
    assert any("item_qty" in mf for mf in out.missing_fields)


@pytest.mark.asyncio
async def test_parse_items_skip_when_products_ambiguous():
    """resolve_products 留下 candidate_products 时本节点不应执行（必须等用户先选产品）。"""
    state = ContractState(user_message="X1 10 个 300", hub_user_id=1, conversation_id="c1")
    state.candidate_products["X1"] = [ProductInfo(id=1, name="X1"), ProductInfo(id=2, name="X1")]
    state.missing_fields.append("product_choice:X1")
    llm = AsyncMock()
    llm.chat = AsyncMock()
    out = await parse_contract_items_node(state, llm=llm)
    llm.chat.assert_not_awaited()  # 不调 LLM
    assert out.items == []


@pytest.mark.asyncio
async def test_parse_items_uses_extracted_hints_fast_path_no_llm():
    """v1.8 快路径 + v1.9 P2-B：state.extracted_hints['items_raw'] 已存在时
    本地 hint→product 模糊匹配，**不**调 LLM。"""
    state = ContractState(user_message="选 2", hub_user_id=1, conversation_id="c1")
    state.products = [
        ProductInfo(id=1, name="H5"),
        ProductInfo(id=2, name="F1"),
        ProductInfo(id=3, name="K5"),
    ]
    state.extracted_hints = {
        "items_raw": [
            {"hint": "H5", "qty": 10, "price": 300},
            {"hint": "F1", "qty": 10, "price": 500},
            {"hint": "K5", "qty": 20, "price": 300},
        ],
    }
    llm = AsyncMock()
    llm.chat = AsyncMock()  # 不应被调
    out = await parse_contract_items_node(state, llm=llm)
    llm.chat.assert_not_awaited()  # 快路径不调 LLM
    assert len(out.items) == 3
    assert {(i.product_id, i.qty, int(i.price)) for i in out.items} == {
        (1, 10, 300), (2, 10, 500), (3, 20, 300),
    }


@pytest.mark.asyncio
async def test_parse_items_falls_back_to_llm_when_hint_mismatch():
    """v1.9 P2-B：items_raw hint 在 state.products 里**找不到对应**（如 hint='Z9' 但产品是 H5/F1/K5）
    → 不能本地匹配 → 回退到 LLM thinking on 兜底（确保不会硬错或丢 item）。"""
    state = ContractState(user_message="X1 10 个 300", hub_user_id=1, conversation_id="c1")
    state.products = [ProductInfo(id=1, name="H5"), ProductInfo(id=2, name="F1")]
    state.extracted_hints = {
        "items_raw": [
            {"hint": "Z9", "qty": 10, "price": 300},  # Z9 在 products 里找不到
        ],
    }
    llm = _llm_returning_json(json.dumps({"items": [
        {"product_id": 1, "qty": 10, "price": 300},  # LLM 兜底匹配上了 H5
    ]}))
    out = await parse_contract_items_node(state, llm=llm)
    llm.chat.assert_awaited()  # 必须 fallback 调 LLM
    assert len(out.items) == 1 and out.items[0].product_id == 1


@pytest.mark.asyncio
async def test_parse_items_fallback_uses_items_raw_not_user_message():
    """P1-B v1.10：跨轮场景下 user_message='选 2'（短消息）但 items_raw 还在；
    fallback prompt 必须传 items_raw 给 LLM 而不是当前 user_message，否则 LLM 看到"选 2"无法对齐 qty/price。"""
    state = ContractState(user_message="选 2", hub_user_id=1, conversation_id="c1")  # 跨轮短消息
    state.products = [ProductInfo(id=1, name="H5"), ProductInfo(id=2, name="F1")]
    state.extracted_hints = {
        "items_raw": [
            # hint 故意写不匹配（"M5" 不是 H5），强制 fallback
            {"hint": "M5", "qty": 50, "price": 300},
        ],
    }
    captured = {}
    async def fake_chat(*, messages, **_):
        captured["user_payload"] = json.loads(messages[1]["content"])
        return type("R", (), {"text": json.dumps({"items": [
            {"product_id": 1, "qty": 50, "price": 300},
        ]}), "finish_reason": "stop", "tool_calls": []})()
    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=fake_chat)

    out = await parse_contract_items_node(state, llm=llm)
    # 关键断言：fallback 给 LLM 的内容**不**是 user_message
    assert "user_message" not in captured["user_payload"], \
        f"fallback 不能传 user_message='选 2'，实际：{captured['user_payload']}"
    assert captured["user_payload"]["items_raw"] == [
        {"hint": "M5", "qty": 50, "price": 300},
    ]
    # qty/price 仍能对齐
    assert len(out.items) == 1 and out.items[0].qty == 50 and int(out.items[0].price) == 300
```

- [ ] **Step 2: 跑失败**

- [ ] **Step 3: 实现 parse_contract_items_node**

```python
# backend/hub/agent/graph/nodes/parse_contract_items.py
"""parse_contract_items — 把 user_message 里的 qty/price 跟 state.products 对齐进 state.items。

thinking on（推理对齐关系）；缺 qty/price 必须 missing_fields，**不**默认填值。
"""
from __future__ import annotations
import json
from decimal import Decimal
from hub.agent.graph.state import ContractState, ContractItem
from hub.agent.llm_client import DeepSeekLLMClient, enable_thinking


PARSE_ITEMS_PROMPT = """你是合同 items 对齐器。读用户消息 + 已解析的产品列表，把数量 / 价格跟 product_id 对齐。

输入（v1.10：优先 items_raw，跨轮安全）：
- products: [{id, name, sku?}]
- 二选一：
    - items_raw: [{hint, qty, price}, ...]  // extract_contract_context 已抽出的原始数据
    - user_message: 用户原文                  // 极端兜底，items_raw 也为空时

输出严格 JSON：
{
  "items": [
    {"product_id": <int>, "qty": <int 或 null>, "price": <number 或 null>},
    ...
  ]
}

规则：
- 优先用 items_raw 做 hint → product_id 模糊匹配（hint 包含/被 name/sku 包含）
- qty / price 是 null 就传 null（不要默认 1 / 不要默认 list_price）
- 数量 0 / 负数 / 价格 0 / 负数 — 都按 null 处理（无效值）
- 用户提一个产品对应一个 item；不要补全没提到的产品
- 只输出 JSON，不要解释
"""


async def parse_contract_items_node(state: ContractState, *, llm: DeepSeekLLMClient) -> ContractState:
    # 候选产品未消歧义时不能解析 items（先让用户选产品）
    if state.candidate_products:
        return state
    if not state.products:
        if "products" not in state.missing_fields:
            state.missing_fields.append("products")
        return state

    products_for_prompt = [{"id": p.id, "name": p.name, "sku": p.sku} for p in state.products]

    # v1.8 P1-A+B 优先：从 state.extracted_hints['items_raw'] 拿原始 hint/qty/price，
    # 不用 user_message 重新抽（可能已经是"选 2"等短消息）。fallback 走 user_message。
    items_raw = (state.extracted_hints or {}).get("items_raw")
    if items_raw:
        # 已有原始数据 — 用 LLM 把 hint → product_id 对齐就行
        parsed = {"items": []}
        # name/sku 模糊匹配 hint
        for raw in items_raw:
            hint = (raw.get("hint") or "").lower()
            matched = next(
                (p for p in state.products
                 if hint and (hint in p.name.lower() or (p.sku and hint in p.sku.lower()))),
                None,
            )
            if matched:
                parsed["items"].append({
                    "product_id": matched.id, "qty": raw.get("qty"), "price": raw.get("price"),
                })
        # 如果 hint→product 映射有歧义或没命中，回退到 LLM 兜底（罕见）
        if len(parsed["items"]) != len(items_raw):
            parsed = None
    else:
        parsed = None

    if parsed is None:
        # fallback：LLM thinking on 兜底对齐
        # P1-B v1.10：**不**传 state.user_message — 跨轮场景下它可能是"选 2"等短消息
        # 而不是第一轮的"X1 50 个 300"。优先传 items_raw（已经在 extract_contract_context 阶段抽出来的
        # 原始 hint/qty/price），LLM 只需把 hint → product_id 模糊匹配。
        # items_raw 也为空才回退到 user_message（罕见 — 真用户第一轮就直接给短消息的极端情况）。
        fallback_input = {"products": products_for_prompt}
        if items_raw:
            fallback_input["items_raw"] = items_raw  # 优先 — 跨轮安全
        else:
            fallback_input["user_message"] = state.user_message  # 极端兜底
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": PARSE_ITEMS_PROMPT},
                {"role": "user", "content": json.dumps(fallback_input, ensure_ascii=False)},
            ],
            thinking=enable_thinking(),
            temperature=0.0,
            max_tokens=600,
        )
        try:
            parsed = json.loads(resp.text)
        except json.JSONDecodeError:
            state.errors.append("parse_items_json_decode_failed")
            return state

    name_by_id = {p.id: p.name for p in state.products}
    valid_items: list[ContractItem] = []
    for raw in parsed.get("items", []):
        pid = raw.get("product_id")
        qty = raw.get("qty")
        price = raw.get("price")
        name = name_by_id.get(pid, str(pid))
        # 缺 qty / price 不默认 — 加 missing_fields，不写半成品 item
        if qty is None or qty <= 0:
            state.missing_fields.append(f"item_qty:{name}")
            continue
        if price is None or price <= 0:
            state.missing_fields.append(f"item_price:{name}")
            continue
        if pid not in name_by_id:
            state.errors.append(f"parse_items_unknown_product_id:{pid}")
            continue
        valid_items.append(ContractItem(
            product_id=pid, name=name, qty=int(qty), price=Decimal(str(price)),
        ))
    state.items = valid_items
    return state
```

- [ ] **Step 4: PASS + Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/graph/nodes/parse_contract_items.py backend/tests/agent/test_node_parse_contract_items.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "feat(hub): parse_contract_items node 对齐 qty/price → state.items（Plan 6 v9 Task 4.3）

P1-A：resolve_products 只管产品身份，本节点 thinking on 把 qty/price 对齐。
缺 qty/price 不默认填，必须 missing_fields → ask_user。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4.4: extract_contract_context 节点（**v1.8 P1-A+B 重命名扩展** — 一次抽完原文所有合同信息）

**问题背景**（v1.7 → v1.8）：
- v1.7 P1 新增了 parse_contract_shipping 单独节点，但放在 `parse_contract_items` 之后；多候选 customer/product 时直接走 ask_user 跳过这两节点 → 第一轮原文里的"地址 / 联系人 / 电话 / 数量 / 价格"丢失，第二轮"选 2"看不到原文 → 重新问地址 / items 失效
- 同样 resolve_products 依赖 `state.extracted_hints.product_hints`，但**没有节点写入 product_hints** → 多商品场景兜底走单次合并搜，复杂合同丢商品

**v1.8 修复**：用**一个 extract_contract_context 节点**在子图入口（set_origin 后立即执行）一次抽完原文所有结构化信息写到 state，后续节点都从 state 读：

| 抽取字段 | 写入位置 | 给谁用 |
|---|---|---|
| customer_name | `state.extracted_hints["customer_name"]` | resolve_customer |
| product_hints (list) | `state.extracted_hints["product_hints"]` | resolve_products |
| items_raw (list of {hint, qty, price}) | `state.extracted_hints["items_raw"]` | parse_contract_items |
| shipping address / contact / phone | `state.shipping.*` | generate_contract_node |

这样多候选→ask_user 之前所有信息**已经持久化**；第二轮"选 2" 进同一子图，**短消息跳过抽取**（保留上轮 hints），后续节点用 state 里第一轮抽好的数据继续走。

**Files:**
- Create: `backend/hub/agent/graph/nodes/extract_contract_context.py`
- Create: `backend/tests/agent/test_node_extract_contract_context.py`

**职责**（v1.8 一锅端 / v1.9 收紧跳过规则）：
- LLM 从 `state.user_message` **一次**抽 4 类信息，写到 state（位置见上表）
- **v1.9 跳过规则收紧**（P1-B）：只对**明确选择/确认类**消息跳过 LLM —— `"选 N"` / 纯数字（含中文）/ `"id=N"` / action_id 前缀（adj-/vch-/...）/ 确认词（"是"/"确认"/"好的"）。**短补字段消息**（"北京海淀"/"张三"/电话号）仍跑 LLM，靠 only-write-non-null 不覆盖原值
- 缺值传 null（**不**默认空串、**不**默认 0、**不**根据上下文猜）
- 抽到 null 时**不覆盖** state 已有值（保护跨轮信息）
- thinking off / temperature 0.0
- **位置在 set_origin 后第一个**（resolve_customer 之前）— 多候选 ask_user 之前所有 hints 已落 state
- **contract / quote 子图共用本节点**（P1-A v1.9：同一 LLM prompt 抽出来的 4 类信息对两个流都适用）

- [ ] **Step 1: 写测试**

```python
# backend/tests/agent/test_node_extract_contract_context.py
import pytest
import json
from unittest.mock import AsyncMock
from hub.agent.graph.state import ContractState
from hub.agent.graph.nodes.extract_contract_context import extract_contract_context_node


def _llm_returning_json(text):
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {
        "text": text, "finish_reason": "stop", "tool_calls": [],
    })())
    return llm


@pytest.mark.asyncio
async def test_extract_full_contract_request():
    """v1.8 P1-A+B：第一轮就把 customer_name + product_hints + items_raw + shipping 全抽进 state。"""
    state = ContractState(
        user_message="给阿里做合同 H5 10 个 300，F1 10 个 500，K5 20 个 300，"
                       "地址广州市天河区华穗路406号中景B座，林生，13692977880",
        hub_user_id=1, conversation_id="c1",
    )
    llm = _llm_returning_json(json.dumps({
        "customer_name": "阿里",
        "product_hints": ["H5", "F1", "K5"],
        "items_raw": [
            {"hint": "H5", "qty": 10, "price": 300},
            {"hint": "F1", "qty": 10, "price": 500},
            {"hint": "K5", "qty": 20, "price": 300},
        ],
        "shipping": {
            "address": "广州市天河区华穗路406号中景B座",
            "contact": "林生",
            "phone": "13692977880",
        },
    }))
    out = await extract_contract_context_node(state, llm=llm)
    assert out.extracted_hints["customer_name"] == "阿里"
    assert out.extracted_hints["product_hints"] == ["H5", "F1", "K5"]
    assert len(out.extracted_hints["items_raw"]) == 3
    assert out.extracted_hints["items_raw"][1] == {"hint": "F1", "qty": 10, "price": 500}
    assert out.shipping.address.startswith("广州市天河区")
    assert out.shipping.contact == "林生"
    assert out.shipping.phone == "13692977880"


@pytest.mark.asyncio
async def test_extract_skip_only_on_pure_selection_messages():
    """P1-B v1.9：只对明确选择/确认类消息跳过抽取，保护 hints 不被覆盖。
    "选 2" / "1" / "id=10" / "确认" → 跳过；
    "北京海淀" / "张三" / "13800001111" → 不跳过（用户在补字段）。"""
    base_state = lambda msg: ContractState(user_message=msg, hub_user_id=1, conversation_id="c1")

    SKIP_MESSAGES = [
        "选 2", "选2", "1", "  2 ", "第二个",
        "id=10", "id=12",
        # v1.12 P1-B：多 id 也要算 selection（避免 LLM 把 "id=11 id=21" 误抽成新 hints）
        "id=11 id=21", "id=11, id=21", "id=11、id=21", "id=11,id=21,id=33",
        "确认", "是", "好的",
    ]
    for msg in SKIP_MESSAGES:
        state = base_state(msg)
        state.extracted_hints = {"customer_name": "阿里"}
        llm = AsyncMock()
        llm.chat = AsyncMock()
        await extract_contract_context_node(state, llm=llm)
        llm.chat.assert_not_awaited(), f"消息 {msg!r} 应跳过 LLM 但调了"


@pytest.mark.asyncio
async def test_extract_short_field_supplement_still_runs_llm():
    """P1-B v1.9：用户上轮缺地址，本轮回"北京海淀，张三 13800001111" — **不能**跳过抽取。"""
    state = ContractState(user_message="北京海淀，张三 13800001111",
                           hub_user_id=1, conversation_id="c1")
    state.extracted_hints = {"customer_name": "阿里", "product_hints": ["X1"]}
    # state.shipping 全空（上轮没抽到）
    llm = _llm_returning_json(json.dumps({
        "customer_name": None,
        "product_hints": [],
        "items_raw": [],
        "shipping": {"address": "北京海淀", "contact": "张三", "phone": "13800001111"},
    }))
    out = await extract_contract_context_node(state, llm=llm)
    llm.chat.assert_awaited()  # 必须跑 LLM
    # 抽到的 shipping 写进 state
    assert out.shipping.address == "北京海淀"
    assert out.shipping.contact == "张三"
    assert out.shipping.phone == "13800001111"
    # 上轮 hints 保留（because抽到 customer_name=None 时不覆盖）
    assert out.extracted_hints["customer_name"] == "阿里"


@pytest.mark.asyncio
async def test_extract_skip_when_candidate_products_with_hint_id_reply():
    """P1 v1.13：上轮留了 candidate_products + 本轮"H5 用 id=10，F1 用 id=22"按 ask_user 文案回复 →
    必须**不**调 LLM（否则 LLM 会把 H5/F1 重新抽成新 product_hints / items_raw 覆盖第一轮）。"""
    from hub.agent.graph.state import ProductInfo
    HINT_ID_REPLIES = [
        "H5 用 id=10，F1 用 id=22",   # ask_user 提示原话
        "H5 id=10, F1 id=22",        # 简化
        "H5: id=10  F1: id=22",       # 冒号变体
        "H5=10 F1=22",                # 极简（不含 'id'）— 不命中也行（用户大概率会写 id=N）
    ]
    for msg in HINT_ID_REPLIES[:3]:  # 前 3 个含 id=N，必须命中
        state = ContractState(user_message=msg, hub_user_id=1, conversation_id="c1")
        # 候选 products 里的 id 包含 10 和 22
        state.candidate_products = {
            "H5": [ProductInfo(id=10, name="H5"), ProductInfo(id=11, name="H5")],
            "F1": [ProductInfo(id=22, name="F1"), ProductInfo(id=23, name="F1")],
        }
        # 第一轮已抽到的 items_raw（要保护不被覆盖）
        state.extracted_hints = {
            "items_raw": [
                {"hint": "H5", "qty": 10, "price": 300},
                {"hint": "F1", "qty": 5, "price": 500},
            ],
        }
        llm = AsyncMock()
        llm.chat = AsyncMock()  # 不应被调
        out = await extract_contract_context_node(state, llm=llm)
        llm.chat.assert_not_awaited(), f"消息 {msg!r} 应跳过 LLM"
        # 第一轮 items_raw 仍保留
        assert out.extracted_hints["items_raw"][0]["qty"] == 10
        assert out.extracted_hints["items_raw"][1]["price"] == 500


@pytest.mark.asyncio
async def test_extract_does_not_skip_phone_number_with_no_candidates():
    """P1 v1.13 边界：用户回 "13800001111"（不是候选 id）→ **不**应跳过 LLM。
    电话号刚好是 11 位数字，但没"id="前缀也不是候选 id → safety check OK。"""
    state = ContractState(user_message="13800001111", hub_user_id=1, conversation_id="c1")
    state.extracted_hints = {"customer_name": "阿里"}
    # 没 candidate_products，candidate id reference 不命中
    llm = _llm_returning_json(json.dumps({
        "customer_name": None, "product_hints": [], "items_raw": [],
        "shipping": {"address": None, "contact": None, "phone": "13800001111"},
    }))
    out = await extract_contract_context_node(state, llm=llm)
    llm.chat.assert_awaited()  # 必须跑 LLM 抽 phone
    assert out.shipping.phone == "13800001111"


@pytest.mark.asyncio
async def test_extract_partial_only_customer():
    """用户只说"给阿里做合同 X1" — 抽到 customer_name + product_hints；qty/price/shipping 全 null。"""
    state = ContractState(user_message="给阿里做合同 X1", hub_user_id=1, conversation_id="c1")
    llm = _llm_returning_json(json.dumps({
        "customer_name": "阿里",
        "product_hints": ["X1"],
        "items_raw": [{"hint": "X1", "qty": None, "price": None}],
        "shipping": {"address": None, "contact": None, "phone": None},
    }))
    out = await extract_contract_context_node(state, llm=llm)
    assert out.extracted_hints["customer_name"] == "阿里"
    assert out.extracted_hints["product_hints"] == ["X1"]
    assert out.shipping.address is None
    assert out.shipping.phone is None


@pytest.mark.asyncio
async def test_extract_does_not_overwrite_existing_with_none():
    """v1.8：本轮抽到 None 时**不**覆盖 state 里上轮已有值（保护跨轮信息）。"""
    state = ContractState(
        user_message="顺便把电话也加上 13900002222",
        hub_user_id=1, conversation_id="c1",
    )
    state.shipping.address = "北京海淀"
    state.shipping.contact = "张三"
    llm = _llm_returning_json(json.dumps({
        "customer_name": None,
        "product_hints": [],
        "items_raw": [],
        "shipping": {"address": None, "contact": None, "phone": "13900002222"},
    }))
    out = await extract_contract_context_node(state, llm=llm)
    # address / contact 保留上轮，phone 写新的
    assert out.shipping.address == "北京海淀"
    assert out.shipping.contact == "张三"
    assert out.shipping.phone == "13900002222"
```

- [ ] **Step 2: 跑失败**

- [ ] **Step 3: 实现 extract_contract_context_node**

```python
# backend/hub/agent/graph/nodes/extract_contract_context.py
"""extract_contract_context — 子图入口节点，一次抽完用户原文里所有合同结构化信息。

v1.8 P1-A+B：
- v1.7 把 parse_contract_shipping 放在 parse_items 之后；多候选 → ask_user 时跳过这两节点 → 第一轮信息丢
- resolve_products 依赖 extracted_hints.product_hints 但没人写 → 多商品兜底失败
本节点放 set_origin 后第一个，**任何 ask_user 之前**抽完所有 hints 写 state，跨轮安全。

跨轮规则：
  - state.extracted_hints 已有值 + 本轮消息短（≤ 8 字）→ 跳过 LLM
  - 抽到的字段为 null 时**不**覆盖 state 已有值（保护跨轮信息）
"""
from __future__ import annotations
import json

from hub.agent.graph.state import ContractState
from hub.agent.llm_client import DeepSeekLLMClient, disable_thinking


EXTRACT_CONTEXT_PROMPT = """你是合同请求抽取器。从用户原文一次抽 4 类信息，输出严格 JSON：

{
  "customer_name": <str 或 null>,           // 用户提到的客户名 / 关键词
  "product_hints": [<str>, ...],            // 用户提到的产品名 / 编号列表（如 ["H5", "F1", "K5"]）
  "items_raw": [
    {"hint": <str>, "qty": <int 或 null>, "price": <number 或 null>}, ...
  ],                                          // 每个产品的原始数量 / 价格；用户没明说传 null
  "shipping": {
    "address": <str 或 null>,                // 详细地址；只有"北京"太模糊算 null
    "contact": <str 或 null>,                // 联系人姓名
    "phone": <str 或 null>                   // 11 位电话
  }
}

规则：
- 只抽**当前消息**里明确出现的内容；不要补全 / 不要根据上下文猜
- 数量 / 价格用户没明说就传 null（不要默认 1 / 不要默认 list_price）
- product_hints 顺序与 items_raw 一致
- 只输出 JSON，不要解释
"""


def _looks_like_pure_selection(message: str) -> bool:
    """P1-B v1.9 / v1.12：只有"明确选择/确认类"消息才跳过抽取 — 短消息但是补地址/联系人不能跳过。

    命中：
      - 纯数字 "1" / "2"（含中文""一""二""…"）
      - "选 N" / "第 N 个"
      - "id=N" / "id N"
      - **多 id**："id=11 id=21" / "id=11, id=21" / "id=11、id=21"（v1.12 P1-B 新加）
      - 业务 action_id 前缀（adj-/vch-/stk-/...）
      - 确认词（"是" / "确认" / "好的" / "OK"）
    不命中（仍走 LLM 抽取）：
      - "北京海淀" / "张三" / "13800001111" 等补字段消息
    """
    import re
    msg = message.strip()
    if not msg:
        return False
    if re.fullmatch(r"\s*[1-9一二三四五六七八九]\s*", msg):
        return True
    if re.search(r"^选\s*[1-9]$", msg) or re.search(r"^第\s*[一二三四五六七八九1-9]\s*个?$", msg):
        return True
    # 单 id 或多 id（id=N 重复，可空格 / 逗号 / 顿号 / 中文分隔）
    if re.fullmatch(r"\s*(?:id\s*[=:：]?\s*\d+[\s,，、]*)+\s*", msg, re.IGNORECASE):
        return True
    if re.fullmatch(r"(adj|vch|stk|act|qte|cnt)-[0-9a-f]{8,}", msg, re.IGNORECASE):
        return True
    if msg in {"是", "确认", "好的", "OK", "ok", "yes", "嗯"}:
        return True
    return False


def _looks_like_candidate_id_reference(message: str, candidate_products: dict) -> bool:
    """P1 v1.13：消息里出现至少一个 id=N，且 N 是当前候选里某个 product.id → 算 selection。

    覆盖 ask_user 文案诱导的写法："H5 用 id=10，F1 用 id=22" / "H5: id=10, F1=22" / 等。
    这类消息按 _looks_like_pure_selection 的纯 id 正则不命中（中间有 hint 名 / 中文连接词），
    但只要含至少一个有效候选 id，就可视为选择消息 — extract_context 跳过 LLM 不覆盖第一轮 hints。

    安全：用户回"13800001111"（电话号）没 `id=` 前缀 → 不命中；不会把电话号误当 selection。
    """
    import re
    if not candidate_products:
        return False
    ids_in_msg = {int(x) for x in re.findall(r"id\s*[=:：]?\s*(\d+)", message, re.IGNORECASE)}
    if not ids_in_msg:
        return False
    valid_ids = {p.id for candidates in candidate_products.values() for p in candidates}
    return bool(ids_in_msg & valid_ids)


async def extract_contract_context_node(state: ContractState, *, llm: DeepSeekLLMClient) -> ContractState:
    # P1-B v1.9 / v1.13 跨轮跳过规则：
    # 1. 明确纯选择消息（"选 2"/"是"/单或多 id=N/action_id 等）→ 跳过
    # 2. v1.13 P1：上轮留了 candidate_products + 本轮含至少一个有效候选 id → 也跳过
    #    （覆盖"H5 用 id=10，F1 用 id=22"等带 hint 的选择回复 — 按 ask_user 文案诱导的写法）
    # 3. 其他短消息（"北京海淀"/"张三"）仍走 LLM，靠 only-write-non-null 保护原值
    if _looks_like_pure_selection(state.user_message):
        return state
    if _looks_like_candidate_id_reference(state.user_message, state.candidate_products):
        return state

    resp = await llm.chat(
        messages=[
            {"role": "system", "content": EXTRACT_CONTEXT_PROMPT},
            {"role": "user", "content": state.user_message},
        ],
        thinking=disable_thinking(),
        temperature=0.0,
        max_tokens=600,
    )
    try:
        parsed = json.loads(resp.text)
    except json.JSONDecodeError:
        state.errors.append("extract_context_json_decode_failed")
        return state

    # extracted_hints — 只在抽到非 null/empty 时写，避免覆盖跨轮已有值
    if parsed.get("customer_name"):
        state.extracted_hints["customer_name"] = parsed["customer_name"]
    if parsed.get("product_hints"):
        state.extracted_hints["product_hints"] = parsed["product_hints"]
    if parsed.get("items_raw"):
        state.extracted_hints["items_raw"] = parsed["items_raw"]

    # shipping — 同样的规则，只写抽到的非 null 字段
    shipping = parsed.get("shipping") or {}
    if shipping.get("address"):
        state.shipping.address = shipping["address"]
    if shipping.get("contact"):
        state.shipping.contact = shipping["contact"]
    if shipping.get("phone"):
        state.shipping.phone = shipping["phone"]
    return state
```

- [ ] **Step 4: PASS + Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent add backend/hub/agent/graph/nodes/extract_contract_context.py backend/tests/agent/test_node_extract_contract_context.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "feat(hub): extract_contract_context node（Plan 6 v9 Task 4.4 / plan v1.8 P1-A+B）

子图入口节点，一次抽完用户原文里所有合同结构化信息：
customer_name / product_hints / items_raw / shipping → 全部写进 state。
位置：set_origin 后第一个 — 多候选 ask_user 之前所有 hints 已落 state。
跨轮短消息跳过 LLM 不覆盖；抽到 null 时不覆盖 state 已有值。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```
不默认空串，让 validate_inputs 据 None 判定是否 ask_user。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4.5: validate_inputs node（thinking on）

**Files:**
- Create: `backend/hub/agent/graph/nodes/validate_inputs.py`
- Create: `backend/tests/agent/test_node_validate_inputs.py`

**Spec ref:** §1.5 thinking 战略性使用

- [ ] **Step 1: 写测试**

```python
# backend/tests/agent/test_node_validate_inputs.py
import pytest
from unittest.mock import AsyncMock
from decimal import Decimal
from hub.agent.graph.state import ContractState, ContractItem, CustomerInfo, ProductInfo
from hub.agent.graph.nodes.validate_inputs import validate_inputs_node


@pytest.mark.asyncio
async def test_validate_inputs_thinking_enabled():
    """validate_inputs 必须 thinking enabled — spec §1.5 表。"""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text":
        '{"missing_fields": [], "warnings": []}', "finish_reason": "stop", "tool_calls": []})())
    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=1, name="阿里")
    state.products = [ProductInfo(id=1, name="X1")]
    state.items = [ContractItem(product_id=1, name="X1", qty=10, price=Decimal("300"))]
    state.shipping.address = "北京海淀"
    out = await validate_inputs_node(state, llm=llm)
    kw = llm.chat.await_args.kwargs
    assert kw["thinking"] == {"type": "enabled"}
    assert out.missing_fields == []


@pytest.mark.asyncio
async def test_validate_inputs_detects_missing_address():
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=type("R", (), {"text":
        '{"missing_fields": ["shipping_address"], "warnings": []}', "finish_reason": "stop",
        "tool_calls": []})())
    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=1, name="阿里")
    state.products = [ProductInfo(id=1, name="X1")]
    state.items = [ContractItem(product_id=1, name="X1", qty=10, price=Decimal("300"))]
    # 缺 shipping
    out = await validate_inputs_node(state, llm=llm)
    assert "shipping_address" in out.missing_fields
```

- [ ] **Step 2: 实现 validate_inputs_node**

```python
# backend/hub/agent/graph/nodes/validate_inputs.py
"""validate_inputs — thinking on，推理价格合理性 / items 完整性 / 缺失字段。spec §1.5。"""
from __future__ import annotations
import json
from hub.agent.graph.state import ContractState
from hub.agent.llm_client import DeepSeekLLMClient, enable_thinking


VALIDATE_INPUTS_PROMPT = """你是合同输入校验器。看 state，判断：
1. items 是否完整（每个产品都有 qty / price）
2. 价格是否合理（不为 0 / 不为负数 / 不极端低）
3. 必要字段是否齐全（customer / shipping_address / contact / phone — 部分可选）

输出 JSON：
{
  "missing_fields": ["shipping_address", ...],  // 缺哪些必要字段
  "warnings": ["价格 X1=0.01 异常低", ...]      // 推理出的警告
}

只输出 JSON，不要解释。
"""


async def validate_inputs_node(state: ContractState, *, llm: DeepSeekLLMClient) -> ContractState:
    state_summary = {
        "customer": state.customer.model_dump() if state.customer else None,
        "products": [p.model_dump() for p in state.products],
        "items": [i.model_dump() for i in state.items],
        "shipping": state.shipping.model_dump(),
    }
    resp = await llm.chat(
        messages=[
            {"role": "system", "content": VALIDATE_INPUTS_PROMPT},
            {"role": "user", "content": json.dumps(state_summary, default=str, ensure_ascii=False)},
        ],
        thinking=enable_thinking(),  # ✅ 推理节点开 thinking
        temperature=0.0,
        max_tokens=300,
    )
    try:
        parsed = json.loads(resp.text)
        state.missing_fields = parsed.get("missing_fields", [])
        if parsed.get("warnings"):
            state.errors.extend(parsed["warnings"])
    except json.JSONDecodeError:
        state.errors.append("validate_inputs_json_decode_failed")
    return state
```

- [ ] **Step 3: PASS + Commit**

---

### Task 4.6: ask_user node + format_response (prefix)

**Files:**
- Create: `backend/hub/agent/graph/nodes/ask_user.py`
- Create: `backend/hub/agent/graph/nodes/format_response.py`

- [ ] **Step 1: ask_user — 缺信息时礼貌问回，候选列编号 + id + 名称（P2-C v1.2 修正）**

```python
# backend/hub/agent/graph/nodes/ask_user.py
"""ask_user — 输出缺失字段或候选列表给用户。

P2-C v1.2 关键：candidate_customers / candidate_products 不能只输出 'customer_choice' 这种
内部字段名 — 必须列出候选项的编号 / id / 名称，让用户能精确选回来。
"""
from __future__ import annotations
from hub.agent.graph.state import ContractState

FIELD_LABELS = {
    "shipping_address": "收货地址",
    "contact": "联系人",
    "phone": "电话",
    "customer": "客户",
    "items": "产品明细",
    "products": "产品",
}


async def ask_user_node(state: ContractState) -> ContractState:
    parts: list[str] = []

    # 1. 多候选客户 — 列编号 + id + 名称
    if state.candidate_customers:
        lines = ["请问哪个客户？回复编号或客户 ID："]
        for i, c in enumerate(state.candidate_customers, 1):
            lines.append(f"  {i}) [id={c.id}] {c.name}")
        parts.append("\n".join(lines))

    # 2. 多候选产品 — 按 hint 分组列
    # P2 v1.10：多组候选时提示**必须用 id 精确选**，避免裸编号"选 2"被套到所有组
    multi_groups = len(state.candidate_products) > 1
    for hint, candidates in state.candidate_products.items():
        if multi_groups:
            lines = [f"产品「{hint}」找到多个，请用 id 精确选（如 id={candidates[0].id}）："]
        else:
            lines = [f"产品「{hint}」找到多个，请选一个（回复编号或产品 ID）："]
        for i, p in enumerate(candidates, 1):
            spec = f" {p.spec}" if p.spec else ""
            color = f" {p.color}" if p.color else ""
            lines.append(f"  {i}) [id={p.id}] {p.name}{color}{spec}")
        parts.append("\n".join(lines))
    if multi_groups:
        parts.append("（多个产品都有歧义时，请按 `id=N` 精确选每个，例如：H5 用 id=10，F1 用 id=22）")

    # 3. 一般缺失字段（非 _choice 类）
    plain = [mf for mf in state.missing_fields
              if not mf.startswith("customer_choice") and not mf.startswith("product_choice:")]
    if plain:
        # item_qty:H5 / item_price:F1 用更友好的描述
        labeled = []
        for mf in plain:
            if mf.startswith("item_qty:"):
                labeled.append(f"产品「{mf.split(':', 1)[1]}」的数量")
            elif mf.startswith("item_price:"):
                labeled.append(f"产品「{mf.split(':', 1)[1]}」的单价")
            elif mf.startswith("product_not_found:"):
                labeled.append(f"产品「{mf.split(':', 1)[1]}」找不到，请确认名称")
            else:
                labeled.append(FIELD_LABELS.get(mf, mf))
        parts.append("还差这些：" + "、".join(labeled) + "。")

    state.final_response = "\n\n".join(parts) if parts else "请补充信息后再试。"
    return state
```

- [ ] **Step 2: format_response — prefix 强制开头**

```python
# backend/hub/agent/graph/nodes/format_response.py
"""format_response — prefix 强制 BOT 回执风格。spec §1.2 应用 B/C。"""
from __future__ import annotations
from hub.agent.graph.state import AgentState
from hub.agent.llm_client import DeepSeekLLMClient, disable_thinking

FORMAT_PROMPTS = {
    "contract": "合同已生成：",
    "quote": "报价单已生成：",
    "voucher": "凭证已起草：",
    "adjust_price": "调价已申请，等待审核：",
    "adjust_stock": "库存调整已申请：",
    "confirm_done": "已为您处理：",
}


async def format_response_node(
    state: AgentState,
    *,
    llm: DeepSeekLLMClient,
    template_key: str,
    summary: str,
) -> AgentState:
    """LLM + prefix 强制开头风格。temperature=0.7 让回执有点变化但不啰嗦。"""
    prefix = FORMAT_PROMPTS.get(template_key, "完成：")
    resp = await llm.chat(
        messages=[
            {"role": "system", "content": "你是 ERP 业务回执生成器。把内部 summary 转成给钉钉用户看的简短回执，1-2 行。"},
            {"role": "user", "content": summary},
        ],
        prefix_assistant=prefix,
        thinking=disable_thinking(),
        temperature=0.7,
        max_tokens=200,
    )
    state.final_response = prefix + resp.text
    return state
```

- [ ] **Step 3: 写测试 + Commit**

---

### Task 4.7: contract subgraph 串起来 + prompt

**Files:**
- Create: `backend/hub/agent/prompt/subgraph_prompts/contract.py`
- Create: `backend/hub/agent/graph/subgraphs/contract.py`
- Create: `backend/tests/agent/test_subgraph_contract.py`

- [ ] **Step 1: 写 contract subgraph_prompt**

```python
# backend/hub/agent/prompt/subgraph_prompts/contract.py
CONTRACT_SYSTEM_PROMPT = """你是销售合同起草助手。流程：
1. 找客户（resolve_customer）
2. 找产品（resolve_products，可能多个）
3. 校验输入完整性（validate_inputs）— 用 thinking 推理
4. 信息齐 → 调 generate_contract_draft；不齐 → ask_user

**禁止**：
- 调 check_inventory（合同生成不需要）
- 反问"是否需要做合同"（用户已经说要做了）
- 在合同信息齐时要求二次确认（直接生成）
"""
```

- [ ] **Step 2: 写 contract subgraph 测试**

```python
# backend/tests/agent/test_subgraph_contract.py
import pytest
from hub.agent.graph.state import ContractState


@pytest.mark.asyncio
async def test_contract_subgraph_no_check_inventory_tool():
    """物理保证：contract 子图的 tools 列表不含 check_inventory。"""
    from hub.agent.tools.registry import ToolRegistry
    from hub.agent.tools import register_all_tools
    reg = ToolRegistry()
    register_all_tools(reg)
    schemas = reg.schemas_for_subgraph("contract")
    names = {s["function"]["name"] for s in schemas}
    assert "check_inventory" not in names, f"contract 不应挂 check_inventory：{names}"


@pytest.mark.asyncio
async def test_contract_subgraph_node_set_includes_parse_items():
    """验证 contract 子图节点集合 — 必须含 parse_contract_items（P1-A）。"""
    from hub.agent.graph.subgraphs.contract import build_contract_subgraph
    from unittest.mock import AsyncMock
    compiled = build_contract_subgraph(llm=AsyncMock(), tool_executor=AsyncMock())
    nodes = set(compiled.get_graph().nodes)
    expected = {
        "resolve_customer", "resolve_products", "parse_contract_items",
        "validate_inputs", "ask_user", "generate_contract", "format_response",
    }
    assert expected <= nodes, f"缺节点：{expected - nodes}"


@pytest.mark.asyncio
async def test_generate_contract_keeps_state_for_format_response():
    """P1 v1.11：generate_contract_node 不清状态 — format_response 还要用 state.customer.name / len(state.items)
    写回执。清状态由 cleanup_after_contract_node 在 format_response 之后做。"""
    from hub.agent.graph.subgraphs.contract import generate_contract_node
    from hub.agent.graph.state import ContractState, CustomerInfo, ProductInfo, ContractItem
    from decimal import Decimal
    from unittest.mock import AsyncMock

    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=10, name="阿里")
    state.products = [ProductInfo(id=1, name="X1")]
    state.items = [ContractItem(product_id=1, name="X1", qty=10, price=Decimal("300"))]
    state.shipping.address = "北京海淀"

    async def fake_executor(name, args):
        return {"draft_id": 999}
    out = await generate_contract_node(state, llm=AsyncMock(), tool_executor=fake_executor)

    # generate 阶段：写 draft_id + file_sent；**不**清工作状态（留给 cleanup_after_contract）
    assert out.draft_id == 999
    assert out.file_sent is True
    assert out.customer is not None and out.customer.name == "阿里"  # 保留 → format_response 能用
    assert out.products == [ProductInfo(id=1, name="X1")]
    assert len(out.items) == 1
    assert out.shipping.address == "北京海淀"


@pytest.mark.asyncio
async def test_cleanup_after_contract_clears_complete_working_state():
    """P1 v1.11：cleanup_after_contract_node 把所有跨轮工作字段清空，
    防止下一轮"给百度做合同 Y2..."复用阿里 / 旧产品 / 旧地址。"""
    from hub.agent.graph.subgraphs.contract import cleanup_after_contract_node
    from hub.agent.graph.state import ContractState, CustomerInfo, ProductInfo, ContractItem
    from decimal import Decimal

    state = ContractState(user_message="x", hub_user_id=1, conversation_id="c1")
    state.customer = CustomerInfo(id=10, name="阿里")
    state.products = [ProductInfo(id=1, name="X1")]
    state.items = [ContractItem(product_id=1, name="X1", qty=10, price=Decimal("300"))]
    state.shipping.address = "北京海淀"
    state.shipping.contact = "张三"
    state.extracted_hints = {"customer_name": "阿里", "items_raw": [{"hint": "X1", "qty": 10, "price": 300}]}
    state.active_subgraph = "contract"
    state.missing_fields = []
    state.draft_id = 999
    state.file_sent = True

    out = await cleanup_after_contract_node(state)

    # 全部工作上下文清空
    assert out.customer is None
    assert out.products == []
    assert out.items == []
    assert out.shipping.address is None
    assert out.shipping.contact is None
    assert out.shipping.phone is None
    assert out.extracted_hints == {}
    assert out.candidate_customers == []
    assert out.candidate_products == {}
    assert out.active_subgraph is None
    assert out.missing_fields == []
    # 但业务结果保留
    assert out.draft_id == 999
    assert out.file_sent is True


# 完整 6 节点串行 + 多种分支 mock 测试在 Phase 7 接入后回填到 test_acceptance_scenarios.py
# 参见 Task 4.7（故事 3）/ Task 4.8（故事 4 跨轮）。
```

- [ ] **Step 3: 实现 contract 子图（LangGraph StateGraph，6 节点）**

```python
# backend/hub/agent/graph/subgraphs/contract.py
"""contract 子图 — LangGraph state machine 6 节点。spec §3 + plan v1.1 P1-A。

节点流：
  resolve_customer  → resolve_products  → parse_contract_items
                                              ↓
                                         validate_inputs
                                              ↓
                            (有 missing) → ask_user → END
                            (无 missing) → generate_contract → format_response → END
"""
from __future__ import annotations
from langgraph.graph import StateGraph, START, END

from hub.agent.graph.state import ContractState, ShippingInfo  # v1.10 P1-A
from hub.agent.graph.nodes.resolve_customer import resolve_customer_node
from hub.agent.graph.nodes.resolve_products import resolve_products_node
from hub.agent.graph.nodes.parse_contract_items import parse_contract_items_node
from hub.agent.graph.nodes.extract_contract_context import extract_contract_context_node  # v1.8 P1-A+B
from hub.agent.graph.nodes.validate_inputs import validate_inputs_node
from hub.agent.graph.nodes.ask_user import ask_user_node
from hub.agent.graph.nodes.format_response import format_response_node
from hub.agent.llm_client import DeepSeekLLMClient


async def generate_contract_node(state: ContractState, *, llm, tool_executor) -> ContractState:
    """调 generate_contract_draft — strict + 写 tool fail closed。"""
    payload = {
        "customer_id": state.customer.id,
        "items": [{"product_id": i.product_id, "qty": i.qty, "price": float(i.price)}
                  for i in state.items],
        "shipping_address": state.shipping.address or "",  # sentinel
        "contact": state.shipping.contact or "",
        "phone": state.shipping.phone or "",
        "extras": {},
    }
    result = await tool_executor("generate_contract_draft", payload)
    state.draft_id = result.get("draft_id")
    state.file_sent = True
    # P1 v1.11：cleanup **不**在这里做 — format_response 还要用 state.customer.name / len(state.items)
    # 写回执 summary，提前清会让回执变成 "customer=unknown, items=0"。
    # 改放到独立的 cleanup_after_format 节点，在 format_response 之后跑（见 build_contract_subgraph）。
    return state


async def cleanup_after_contract_node(state: ContractState) -> ContractState:
    """P1 v1.11：合同流程完成后清完整工作上下文。
    放在 format_response 之后 → END 之前，让回执先用 state.customer/items 写好再清。

    必须清（防止下一轮"给百度做合同 Y2..."复用阿里 / 旧产品 / 旧地址）：
      - active_subgraph / candidate_*
      - customer / products / items / shipping / extracted_hints
      - missing_fields
    保留：draft_id / file_sent（业务结果）
    """
    state.active_subgraph = None
    state.candidate_customers = []
    state.candidate_products = {}
    state.customer = None
    state.products = []
    state.items = []
    state.shipping = ShippingInfo()
    state.extracted_hints = {}
    state.missing_fields = []
    return state


def _route_after_resolve_products(state: ContractState) -> str:
    """有候选歧义（同名产品）→ 直接 ask_user 让用户选；否则进 parse_items。"""
    if state.candidate_customers or state.candidate_products:
        return "ask_user"
    return "parse_contract_items"


def _route_after_parse_items(state: ContractState) -> str:
    """items 缺失 → ask_user；齐 → validate_inputs。"""
    if any(mf.startswith("item_") for mf in state.missing_fields):
        return "ask_user"
    return "validate_inputs"


def _route_after_validate(state: ContractState) -> str:
    return "ask_user" if state.missing_fields else "generate_contract"


def build_contract_subgraph(*, llm: DeepSeekLLMClient, tool_executor):
    # P1-B v1.2：LangGraph 节点必须是 async callable —— sync lambda 返回 coroutine
    # LangGraph 不会再 await 一次，会把 coroutine 当 state 返回 → TypeError。
    # 用 async def wrapper 显式 await 内部 async node。

    # P1-A v1.6：子图入口先写 active_subgraph，让 resolve_customer/products 写候选时
    # 候选来源已记录在父图 checkpoint；下一轮"选 N" → pre_router 据此回正确子图。
    async def _set_origin(s: ContractState):
        s.active_subgraph = "contract"
        return s
    async def _extract_context(s):
        # v1.8 P1-A+B：set_origin 后第一个，一次抽完原文 hints 写 state；
        # 多候选 ask_user 之前已落 state，跨轮短消息跳过 LLM。
        return await extract_contract_context_node(s, llm=llm)
    async def _resolve_customer(s):
        return await resolve_customer_node(s, llm=llm, tool_executor=tool_executor)
    async def _resolve_products(s):
        return await resolve_products_node(s, llm=llm, tool_executor=tool_executor)
    async def _parse_items(s):
        # v1.8：parse_contract_items 仍 thinking on 校验对齐，但优先从 state.extracted_hints['items_raw']
        # 拿原始数据（已被 extract_contract_context 写好）；user_message 只作 fallback。
        return await parse_contract_items_node(s, llm=llm)
    async def _validate(s):
        return await validate_inputs_node(s, llm=llm)
    async def _ask_user(s):
        return await ask_user_node(s)
    async def _generate(s):
        return await generate_contract_node(s, llm=llm, tool_executor=tool_executor)
    async def _format(s):
        return await format_response_node(
            s, llm=llm, template_key="contract",
            summary=f"draft_id={s.draft_id}, customer={s.customer.name if s.customer else 'unknown'}, items={len(s.items)}",
        )
    async def _cleanup(s):
        # P1 v1.11：format_response 之后才清工作上下文
        return await cleanup_after_contract_node(s)

    g = StateGraph(ContractState)
    g.add_node("set_origin", _set_origin)
    g.add_node("extract_contract_context", _extract_context)  # v1.8 P1-A+B
    g.add_node("resolve_customer", _resolve_customer)
    g.add_node("resolve_products", _resolve_products)
    g.add_node("parse_contract_items", _parse_items)
    g.add_node("validate_inputs", _validate)
    g.add_node("ask_user", _ask_user)
    g.add_node("generate_contract", _generate)
    g.add_node("format_response", _format)
    g.add_node("cleanup_after_contract", _cleanup)  # v1.11 P1
    g.add_edge(START, "set_origin")
    g.add_edge("set_origin", "extract_contract_context")  # v1.8：抽 hints 在最前
    g.add_edge("extract_contract_context", "resolve_customer")
    # resolve_customer 三分支：candidate 命中后直接 ask_user 不进 resolve_products
    # （hints 已经在 extract_contract_context 阶段写完，ask_user → 下一轮选 N 后续节点能拿到）
    g.add_conditional_edges(
        "resolve_customer",
        lambda s: "ask_user" if s.candidate_customers or "customer" in s.missing_fields else "resolve_products",
        {"ask_user": "ask_user", "resolve_products": "resolve_products"},
    )
    g.add_conditional_edges(
        "resolve_products", _route_after_resolve_products,
        {"ask_user": "ask_user", "parse_contract_items": "parse_contract_items"},
    )
    g.add_conditional_edges(
        "parse_contract_items", _route_after_parse_items,
        {"ask_user": "ask_user", "validate_inputs": "validate_inputs"},
    )
    g.add_conditional_edges(
        "validate_inputs", _route_after_validate,
        {"ask_user": "ask_user", "generate_contract": "generate_contract"},
    )
    g.add_edge("ask_user", END)
    g.add_edge("generate_contract", "format_response")
    # P1 v1.11：format_response → cleanup_after_contract → END
    # format_response 用 state.customer.name / len(state.items) 写回执；cleanup 之后才清状态
    g.add_edge("format_response", "cleanup_after_contract")
    g.add_edge("cleanup_after_contract", END)
    return g.compile()
```

- [ ] **Step 4: 跑 PASS + Commit**

---

### Task 4.8: 故事 3 (单轮合同) acceptance fixture

**Files:**
- Create: `backend/tests/agent/fixtures/scenarios/story3_contract_oneround.yaml`

```yaml
name: 故事 3：单轮合同（信息一次到齐）
turns:
  - input: "给阿里做合同 X1 10 个 300，地址北京海淀，张三 13800000000"
    expected_intent: contract
    tool_caps:
      search_customers: 1
      search_products: 1
      generate_contract_draft: 1
      check_inventory: 0  # 物理不挂，必须 0
    sent_files_min: 1               # 与 30 case yaml + driver SUPPORTED_TURN_FIELDS 一致
    must_contain: ["合同已生成"]    # 替换 response_must_start_with（driver 用 must_contain 机检）
```

- [ ] Commit

---

### Task 4.9: 故事 4 (跨轮合同) acceptance fixture — **核心场景**

**Files:**
- Create: `backend/tests/agent/fixtures/scenarios/story4_query_then_contract.yaml`

```yaml
name: 故事 4：跨轮 query → contract（核心场景）
turns:
  - input: "查 SKG 有哪些产品有库存"
    expected_intent: query
    tool_caps:
      check_inventory: 1
  - input: "给翼蓝做合同 H5 10 个 300，F1 10 个 500，K5 20 个 300，地址广州市天河区华穗路406号中景B座，林生，13692977880"
    expected_intent: contract
    tool_caps:
      check_inventory: 0     # 第二轮**绝对不能**再查 — 物理不挂
      search_customers: 1
      search_products: 1     # 合并搜 H5/F1/K5
      generate_contract_draft: 1
    sent_files_min: 1
    items_count: 3
    must_contain: ["广州", "华穗"]  # shipping_address 内容写入，间接验"必须有 shipping"
```

⚠️ 这个 yaml 要在 Phase 7 完整接入后才能跑端到端真 LLM 测试。Phase 4 这里只锁定 fixture。

- [ ] Commit

---

### Task 4.10: Phase 4 集成验证

- [ ] **Step 1: 跑 Phase 4 全部测试**

```bash
pytest tests/agent/test_subgraph_contract.py tests/agent/test_node_resolve_customer.py tests/agent/test_node_resolve_products.py tests/agent/test_node_validate_inputs.py -v
```

Expected: 全 PASS。

- [ ] **Step 2: 标记 M4 完成**

---

## Phase 5：写操作子图（M5，1.5 天）

**Goal**：voucher / adjust_price / adjust_stock 三个写子图 + 复用 confirm 节点（多 pending 三分支）。覆盖故事 6 + 跨会话隔离。

**Exit criteria**：
1. `pytest tests/agent/test_node_confirm.py tests/agent/test_subgraph_voucher.py tests/agent/test_subgraph_adjust_price.py tests/agent/test_subgraph_adjust_stock.py -v` 全过
2. 跨会话隔离 + 多 pending 不冒认 全过

### Task 5.1: confirm node（多 pending 三分支）

**Files:**
- Create: `backend/hub/agent/graph/nodes/confirm.py`
- Append: `backend/tests/agent/test_node_confirm.py`（已有 ConfirmGate 测试基础）

**Spec ref:** §6.3

- [ ] **Step 1: 写测试 — 三分支行为**

```python
# 追加到 backend/tests/agent/test_node_confirm.py
import pytest
from hub.agent.graph.state import AgentState
from hub.agent.graph.nodes.confirm import confirm_node


@pytest.mark.asyncio
async def test_confirm_node_zero_pending_routes_to_chat(gate):
    state = AgentState(user_message="确认", hub_user_id=1, conversation_id="c1")
    out = await confirm_node(state, gate=gate)
    assert "没有待办" in (out.final_response or "")


@pytest.mark.asyncio
async def test_confirm_node_one_pending_claims(gate):
    p = await gate.create_pending(action_id="adj-1", hub_user_id=1, conversation_id="c1",
                                    subgraph="adjust_price", summary="阿里 X1 → 280")
    state = AgentState(user_message="确认", hub_user_id=1, conversation_id="c1")
    out = await confirm_node(state, gate=gate)
    # claim 成功 → 路由信息写到 state.intent 或专用字段
    assert out.errors == []
    # 后续路由由主 graph 据 pending.subgraph 决定，confirm_node 只负责 claim


@pytest.mark.asyncio
async def test_confirm_node_multi_pending_lists_does_not_claim(gate):
    await gate.create_pending(action_id="adj-1", hub_user_id=1, conversation_id="c1",
                                subgraph="adjust_price", summary="阿里 X1 → 280")
    await gate.create_pending(action_id="vch-1", hub_user_id=1, conversation_id="c1",
                                subgraph="voucher", summary="SO-001 出库")
    state = AgentState(user_message="确认", hub_user_id=1, conversation_id="c1")
    out = await confirm_node(state, gate=gate)
    assert "1)" in out.final_response and "2)" in out.final_response
    # 两个都未 claim
    assert await gate.is_pending("adj-1")
    assert await gate.is_pending("vch-1")
```

- [ ] **Step 2: 实现 confirm_node**

```python
# backend/hub/agent/graph/nodes/confirm.py
"""confirm_node — 0/1/>1 pending 三分支。spec §6.3。"""
from __future__ import annotations
import re
from hub.agent.graph.state import AgentState
from hub.agent.tools.confirm_gate import ConfirmGate


async def confirm_node(state: AgentState, *, gate: ConfirmGate) -> AgentState:
    pendings = await gate.list_pending_for_context(
        conversation_id=state.conversation_id, hub_user_id=state.hub_user_id,
    )
    if not pendings:
        state.final_response = "您要确认什么？本会话没有待办的操作。"
        return state

    # 尝试从 user_message 解析编号 / action_id（>1 时第二轮的精确选择）
    selected = None
    if len(pendings) > 1:
        # 简单 regex 匹配编号 "1" / "2" 或 action_id 子串
        m = re.search(r"\b(\d+)\b", state.user_message)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(pendings):
                selected = pendings[idx]
        if not selected:
            for p in pendings:
                if p.action_id in state.user_message:
                    selected = p
                    break

        if not selected:
            # 列摘要让用户选
            lines = ["您有以下待确认操作，请回复编号或 action_id："]
            for i, p in enumerate(pendings, 1):
                lines.append(f"{i}) [{p.action_id}] {p.summary}")
            state.final_response = "\n".join(lines)
            return state
    else:
        selected = pendings[0]

    # claim
    try:
        await gate.claim(
            action_id=selected.action_id, token=selected.token,
            hub_user_id=state.hub_user_id, conversation_id=state.conversation_id,
        )
    except Exception as e:
        state.final_response = f"该确认已失效或属于他人：{e}"
        state.errors.append(f"confirm_claim_failed:{e}")
        return state

    # 路由信息：把 pending.subgraph 暴露给主 graph 决定下一步
    state.errors = []  # 清掉历史 error
    # P1-A v1.2：写正式 AgentState 字段（Task 0.2 已声明），不用动态 setattr
    state.confirmed_subgraph = selected.subgraph
    state.confirmed_action_id = selected.action_id
    state.confirmed_payload = selected.payload  # ← 这一行 v1.1 漏写，commit 节点取不到 payload
    return state
```

- [ ] **Step 3: 跑 PASS + Commit**

---

### Task 5.2: adjust_price 子图（preview thinking on）

**Files:**
- Create: `backend/hub/agent/prompt/subgraph_prompts/adjust_price.py`
- Create: `backend/hub/agent/graph/subgraphs/adjust_price.py`
- Create: `backend/tests/agent/test_subgraph_adjust_price.py`

**Spec ref:** §1.5（adjust_price.preview thinking on）

- [ ] **Step 1: 写 prompt**

```python
ADJUST_PRICE_SYSTEM_PROMPT = """调价流程：
1. 找客户 + 找产品（resolve_customer / resolve_products）
2. 拉历史成交价（get_product_customer_prices）
3. preview 节点 — thinking on，分析新价 vs 历史价，输出"调价预览"
4. 写 pending action 进 ConfirmGate（不直接落库）
5. 等用户"确认" → confirm 子节点调 adjust_price_request

禁止：直接调 adjust_price_request 而不 preview。
"""
```

- [ ] **Step 2: 实现 preview_node + commit_node + 子图**

**关键设计**：preview 把 **canonical payload 写进 PendingAction**；commit 从 `state.confirmed_payload`（confirm_node 注入的正式 AgentState 字段）拿参数，**不依赖当前 state.customer/product**。这样多 pending / 重启 / checkpoint 恢复时都能找到正确的执行参数。

```python
# backend/hub/agent/graph/subgraphs/adjust_price.py
async def preview_adjust_price_node(state: AdjustPriceState, *, llm, gate, tool_executor):
    # ... 收集旧价 / 新价 / 历史
    resp = await llm.chat(messages=[...], thinking=enable_thinking(), temperature=0.0,
                            max_tokens=400)
    # canonical payload — 把执行 adjust_price_request 需要的所有参数写下来
    canonical_payload = {
        "tool_name": "adjust_price_request",
        "args": {
            "customer_id": state.customer.id,
            "product_id": state.product.id,
            "new_price": float(state.new_price),
            "reason": "",
        },
        # 给用户看的备份摘要 — 多 pending 列表用
        "preview_text": resp.text,
    }
    # action_id 由 ConfirmGate 自动生成（完整 32-hex + "adj-" 前缀，不是 8-hex），
    # 调用方不要自己构造 / 截断。spec §6.3 + 旧 8-hex 碰撞问题。
    pending = await gate.create_pending(
        hub_user_id=state.hub_user_id,
        conversation_id=state.conversation_id,
        subgraph="adjust_price",
        summary=f"调 {state.customer.name} 的 {state.product.name} 价格 {state.old_price}→{state.new_price}",
        payload=canonical_payload,  # ← P1-C：执行参数随 pending 持久化
        action_prefix="adj",
    )
    state.pending_action_id = pending.action_id  # 完整形如 "adj-{32hex}"
    state.final_response = resp.text + f'\n\n回复"确认"执行（action_id: {pending.action_id}）'
    return state


async def commit_adjust_price_node(state: AdjustPriceState, *, tool_executor):
    """confirm_node 已经 claim 过；payload 已注入 state.confirmed_payload（正式字段）。

    **不**从 state.customer / state.product / state.new_price 取 — 那些是当前轮的内容，
    多 pending / 重启 / checkpoint 恢复时可能不再代表用户确认的那次预览。
    """
    if not state.confirmed_payload:
        state.errors.append("commit_adjust_price_no_payload")
        state.final_response = "执行失败：没有找到确认的预览参数"
        return state
    args = state.confirmed_payload["args"]
    result = await tool_executor(state.confirmed_payload["tool_name"], args)
    state.file_sent = False
    state.final_response = "调价已申请，等待审核"
    return state
```

- [ ] **Step 3: 测试 — 含"两个 pending 选第 1 个只执行第 1 个 payload"**

```python
# backend/tests/agent/test_subgraph_adjust_price.py
@pytest.mark.asyncio
async def test_two_pendings_select_first_only_executes_first_payload(gate):
    """P1-C 验收：两个调价 pending，用户选 1，必须只执行第 1 个的 payload，
    不能用 state 的当前值（可能是第 2 个的）。"""
    p1 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1", subgraph="adjust_price",
        summary="阿里 X1 → 280", action_prefix="adj",
        payload={"tool_name": "adjust_price_request",
                 "args": {"customer_id": 10, "product_id": 1, "new_price": 280.0, "reason": ""}},
    )
    p2 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1", subgraph="adjust_price",
        summary="百度 Y1 → 350", action_prefix="adj",
        payload={"tool_name": "adjust_price_request",
                 "args": {"customer_id": 20, "product_id": 5, "new_price": 350.0, "reason": ""}},
    )
    state = AgentState(user_message="1", hub_user_id=1, conversation_id="c1")
    state = await confirm_node(state, gate=gate)
    # P1-A v1.2：confirm_node 必须把 p1 的 payload 写进 state.confirmed_payload（正式字段）
    assert state.confirmed_payload is not None
    assert state.confirmed_payload["args"]["customer_id"] == 10
    assert state.confirmed_payload["args"]["new_price"] == 280.0
    assert state.confirmed_subgraph == "adjust_price"
    assert state.confirmed_action_id == p1.action_id
    # p2 仍 pending
    assert await gate.is_pending(p2.action_id)


@pytest.mark.asyncio
async def test_action_id_is_full_32_hex(gate):
    """P2-G 验收：action_id 必须是完整 32-hex（含前缀）— 不允许 8 位。"""
    p = await gate.create_pending(
        hub_user_id=1, conversation_id="c1", subgraph="adjust_price",
        summary="x", action_prefix="adj", payload={"tool_name": "x", "args": {}},
    )
    assert p.action_id.startswith("adj-")
    hex_part = p.action_id.split("-", 1)[1]
    assert len(hex_part) == 32, f"action_id hex 必须 32 位，实际 {len(hex_part)}: {p.action_id}"
```

跑后 Commit。

---

### Task 5.3: adjust_stock 子图（与 adjust_price 同粒度展开）

**Files:**
- Create: `backend/hub/agent/prompt/subgraph_prompts/adjust_stock.py`
- Create: `backend/hub/agent/graph/subgraphs/adjust_stock.py`
- Create: `backend/tests/agent/test_subgraph_adjust_stock.py`

**职责**：调整某产品库存数量（增 / 减），preview 给当前库存 + 调整后预测；写 pending action；确认后调 `adjust_stock_request` 落申请。

- [ ] **Step 1: 写 prompt**

```python
# backend/hub/agent/prompt/subgraph_prompts/adjust_stock.py
ADJUST_STOCK_SYSTEM_PROMPT = """库存调整流程：
1. 找产品（resolve_products）
2. 拉当前库存（check_inventory）
3. preview — thinking on，对比当前库存 / 调整后；输出"库存调整预览"
4. 写 pending action 进 ConfirmGate（**不**直接落库）
5. 等"确认" → confirm 子节点调 adjust_stock_request

**禁止**：直接调 adjust_stock_request 而不 preview。
"""
```

- [ ] **Step 2: 实现 preview / commit / 子图（参考 5.2 adjust_price 完全同构）**

```python
# backend/hub/agent/graph/subgraphs/adjust_stock.py
async def preview_adjust_stock_node(state: AdjustStockState, *, llm, gate, tool_executor):
    # 收集当前库存 + delta_qty + reason
    resp = await llm.chat(messages=[...], thinking=enable_thinking(),
                            temperature=0.0, max_tokens=400)
    canonical_payload = {
        "tool_name": "adjust_stock_request",
        "args": {
            "product_id": state.product.id,
            "delta_qty": state.delta_qty,
            "reason": state.reason or "",  # sentinel
        },
        "preview_text": resp.text,
    }
    # 幂等键：同 (conv, user, product_id, delta_qty) 5 分钟内只创建 1 个 pending
    idempotency_key = f"stk:{state.conversation_id}:{state.hub_user_id}:{state.product.id}:{state.delta_qty}"
    pending = await gate.create_pending(
        hub_user_id=state.hub_user_id, conversation_id=state.conversation_id,
        subgraph="adjust_stock", action_prefix="stk",
        summary=f"调 {state.product.name} 库存 {'+' if state.delta_qty > 0 else ''}{state.delta_qty}",
        payload=canonical_payload, ttl_seconds=600,
        idempotency_key=idempotency_key,
    )
    state.pending_action_id = pending.action_id
    state.final_response = resp.text + f'\n\n回复"确认"执行（action_id: {pending.action_id}）'
    return state


async def commit_adjust_stock_node(state, *, tool_executor):
    if not state.confirmed_payload:
        state.errors.append("commit_adjust_stock_no_payload")
        state.final_response = "执行失败：没有找到确认的预览参数"
        return state
    args = state.confirmed_payload["args"]
    await tool_executor(state.confirmed_payload["tool_name"], args)
    state.final_response = "库存调整已申请，等待审核"
    return state
```

- [ ] **Step 3: 测试（含幂等 + 重复确认 + 多 pending + 跨会话）**

```python
# backend/tests/agent/test_subgraph_adjust_stock.py
@pytest.mark.asyncio
async def test_idempotency_key_dedup(gate):
    """同 (conv, user, product, delta) 5 分钟内 preview 两次只生成 1 个 pending。"""
    state1 = build_state_with(product_id=1, delta_qty=10, conv="c1", user=1)
    state2 = build_state_with(product_id=1, delta_qty=10, conv="c1", user=1)  # 同输入
    p1 = await preview_adjust_stock_node(state1, llm=mock_llm, gate=gate, tool_executor=mock_executor)
    p2 = await preview_adjust_stock_node(state2, llm=mock_llm, gate=gate, tool_executor=mock_executor)
    assert state1.pending_action_id == state2.pending_action_id  # 幂等命中


@pytest.mark.asyncio
async def test_two_stock_pendings_select_first_only_executes_first(gate):
    """两个库存调整 pending（不同产品），用户选 1 只执行第 1 个 payload。"""
    p1 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_stock", action_prefix="stk",
        summary="X1 库存 +10",
        payload={"tool_name": "adjust_stock_request",
                 "args": {"product_id": 1, "delta_qty": 10, "reason": ""}},
        idempotency_key="stk:c1:1:1:10",
    )
    await asyncio.sleep(0.01)  # 保证 created_at 顺序
    p2 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_stock", action_prefix="stk",
        summary="X2 库存 -5",
        payload={"tool_name": "adjust_stock_request",
                 "args": {"product_id": 2, "delta_qty": -5, "reason": ""}},
        idempotency_key="stk:c1:1:2:-5",
    )
    state = AgentState(user_message="1", hub_user_id=1, conversation_id="c1")
    state = await confirm_node(state, gate=gate)
    assert state.confirmed_payload["args"]["product_id"] == 1
    assert state.confirmed_payload["args"]["delta_qty"] == 10
    assert await gate.is_pending(p2.action_id)  # p2 仍 pending


@pytest.mark.asyncio
async def test_cross_conversation_isolation(gate):
    """同 user 在私聊 c1 起 stock preview，到群聊 c2 回"确认"看不到。"""
    p = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_stock", action_prefix="stk",
        summary="X1 库存 +10",
        payload={"tool_name": "adjust_stock_request",
                 "args": {"product_id": 1, "delta_qty": 10, "reason": ""}},
        idempotency_key="stk:c1:1:1:10",
    )
    state = AgentState(user_message="确认", hub_user_id=1, conversation_id="c2")
    state = await confirm_node(state, gate=gate)
    assert "没有待办" in (state.final_response or "")
    assert await gate.is_pending(p.action_id)


@pytest.mark.asyncio
async def test_repeat_confirm_after_claim_rejected(gate):
    """同一 pending claim 一次后再"确认"必须不能再次执行（token 单次消费）。"""
    p = await gate.create_pending(
        hub_user_id=1, conversation_id="c1",
        subgraph="adjust_stock", action_prefix="stk",
        summary="X1 库存 +10",
        payload={"tool_name": "adjust_stock_request",
                 "args": {"product_id": 1, "delta_qty": 10, "reason": ""}},
        idempotency_key="stk:c1:1:1:10",
    )
    state1 = AgentState(user_message="确认", hub_user_id=1, conversation_id="c1")
    await confirm_node(state1, gate=gate)
    assert state1.confirmed_payload is not None
    # 第二次"确认"
    state2 = AgentState(user_message="确认", hub_user_id=1, conversation_id="c1")
    await confirm_node(state2, gate=gate)
    assert "没有待办" in (state2.final_response or "")
```

- [ ] **Step 4: Commit**

---

### Task 5.4: voucher 子图（与 adjust_price 同粒度展开 — 写操作风险最高）

**Files:**
- Create: `backend/hub/agent/prompt/subgraph_prompts/voucher.py`
- Create: `backend/hub/agent/graph/subgraphs/voucher.py`
- Create: `backend/tests/agent/test_subgraph_voucher.py`

**职责**：根据订单生成出库 / 入库凭证草稿。涉及订单状态校验 + 幂等 + 审批联动。

**关键风险**（v1.2 P2-E 强调）：
- 凭证一旦提交进入审批流，重复提交 = 错账
- 同一订单不同人不同会话不能重复出凭证
- 必须强幂等（同订单 + 凭证类型 12 小时内只允许 1 个 pending）

- [ ] **Step 1: 写 prompt**

```python
# backend/hub/agent/prompt/subgraph_prompts/voucher.py
VOUCHER_SYSTEM_PROMPT = """凭证流程：
1. 找订单（search_orders / get_order_detail）
2. 校验订单状态（已审批 / 未关单 / 未已出过同类型凭证）
3. preview — 列订单明细 + 凭证类型 + 收/发货方
4. 写 pending action 进 ConfirmGate（带强幂等键）
5. 等"确认" → create_voucher_draft

**禁止**：
- 给未审批订单出凭证
- 同订单 12 小时内重复 preview（幂等命中已有 pending 直接复用）
"""
```

- [ ] **Step 2: 实现 preview / commit + 强幂等 + 状态校验**

```python
# backend/hub/agent/graph/subgraphs/voucher.py
VALID_VOUCHER_TYPES = {"outbound", "inbound"}


def _resolve_voucher_type(state: VoucherState) -> str | None:
    """P1-B v1.4：从 state.voucher_type / extracted_hints / user_message 解析凭证类型。
    返回 None 表示无法判定（让 caller 走 ask_user）。"""
    if state.voucher_type in VALID_VOUCHER_TYPES:
        return state.voucher_type
    hint = (state.extracted_hints or {}).get("voucher_type")
    if hint in VALID_VOUCHER_TYPES:
        return hint
    msg = state.user_message or ""
    if "入库" in msg or "收货" in msg:
        return "inbound"
    if "出库" in msg or "发货" in msg:
        return "outbound"
    return None


async def preview_voucher_node(state: VoucherState, *, llm, gate, tool_executor):
    # 0. 解析凭证类型 — P1-B v1.4：必须先确定 inbound / outbound，**不**硬编码
    voucher_type = _resolve_voucher_type(state)
    if voucher_type is None:
        state.missing_fields.append("voucher_type")
        state.final_response = "请问要做出库凭证还是入库凭证？"
        return state
    state.voucher_type = voucher_type  # 写回 state，下游 commit 节点也用这个

    # 1. 拉订单详情
    order = await tool_executor("get_order_detail", {"order_id": state.order_id})
    # 2. 状态校验（fail closed）
    if order["status"] != "approved":
        state.errors.append(f"voucher_order_not_approved:{state.order_id}")
        state.final_response = f"订单 {state.order_id} 未审批，不能出凭证"
        return state
    # 重复检查也按 voucher_type 区分（同订单可能同时有出库 + 入库各 1 张）
    if order.get(f"{voucher_type}_voucher_count", 0) > 0:
        state.errors.append(f"voucher_already_exists:{state.order_id}:{voucher_type}")
        state.final_response = f"订单 {state.order_id} 已有{voucher_type}凭证，不重复出"
        return state

    # 3. preview
    resp = await llm.chat(messages=[...], thinking=enable_thinking(),
                            temperature=0.0, max_tokens=500)
    canonical_payload = {
        "tool_name": "create_voucher_draft",
        "args": {
            "order_id": state.order_id,
            "voucher_type": voucher_type,  # ← 解析出来的真实类型，不再硬编码
            "items": order["items"],       # 锁定预览时的明细，避免后续订单改了
            "remark": "",
        },
        "preview_text": resp.text,
    }
    # 强幂等：同订单 + 凭证类型 12 小时内只允许 1 个 pending（**全局** key，不含 conv/user）
    # 但 v1.3 P1-B：跨 context 幂等命中必须 fail closed
    # v1.4 P1-B：key 必须含 voucher_type — 否则同订单的"出库" pending 会去重掉真"入库"请求
    idempotency_key = f"vch:{state.order_id}:{voucher_type}"
    type_label = "出库" if voucher_type == "outbound" else "入库"
    try:
        pending = await gate.create_pending(
            hub_user_id=state.hub_user_id, conversation_id=state.conversation_id,
            subgraph="voucher", action_prefix="vch",
            summary=f"{type_label}凭证 SO-{state.order_id}",
            payload=canonical_payload,
            ttl_seconds=43200,  # 12 小时
            idempotency_key=idempotency_key,
        )
    except CrossContextIdempotency as e:
        # 别的 (conv, user) 已经为同订单 + 同凭证类型创建了 pending，处于 12 小时窗口内
        # 不能把不可 claim 的 action_id 给当前用户；fail closed
        state.errors.append(f"voucher_pending_in_other_context:{state.order_id}:{voucher_type}")
        state.final_response = (
            f"订单 {state.order_id} 的{type_label}凭证已有申请待确认/处理中，"
            f"请联系发起人或等待其完成。"
        )
        return state
    state.pending_action_id = pending.action_id
    state.final_response = resp.text + f'\n\n回复"确认"提交凭证（action_id: {pending.action_id}）'
    return state


async def commit_voucher_node(state, *, tool_executor):
    if not state.confirmed_payload:
        state.errors.append("commit_voucher_no_payload")
        state.final_response = "执行失败：没有找到确认的预览参数"
        return state
    args = state.confirmed_payload["args"]
    result = await tool_executor(state.confirmed_payload["tool_name"], args)
    state.voucher_id = result.get("voucher_id")
    state.final_response = f"凭证已提交（{state.voucher_id}），等待审批"
    return state
```

- [ ] **Step 3: 测试（写 tool 风险最高，覆盖最完整）**

```python
# backend/tests/agent/test_subgraph_voucher.py
@pytest.mark.asyncio
async def test_voucher_rejects_unapproved_order(gate, mock_llm):
    """未审批订单不允许出凭证（fail closed）。"""
    state = VoucherState(user_message="出库 SO-1", hub_user_id=1, conversation_id="c1",
                          order_id=1)
    async def fake_executor(name, args):
        return {"order_id": 1, "status": "draft"}  # 未审批
    out = await preview_voucher_node(state, llm=mock_llm, gate=gate,
                                        tool_executor=fake_executor)
    assert "未审批" in (out.final_response or "")
    assert state.pending_action_id is None  # 没创建 pending


@pytest.mark.asyncio
async def test_voucher_rejects_already_outbound_voucher(gate, mock_llm):
    """P1-B v1.4 + P2-E v1.5：订单已有 outbound 凭证 → 出库请求拒；inbound 仍可发。"""
    state = VoucherState(user_message="出库 SO-1", hub_user_id=1, conversation_id="c1",
                          order_id=1)
    async def fake_executor(name, args):
        # 实现按 voucher_type 分别检查 outbound_voucher_count / inbound_voucher_count
        return {"order_id": 1, "status": "approved",
                "outbound_voucher_count": 1, "inbound_voucher_count": 0,
                "items": [{"product_id": 1, "qty": 10}]}
    out = await preview_voucher_node(state, llm=mock_llm, gate=gate,
                                        tool_executor=fake_executor)
    assert "已有出库凭证" in (out.final_response or "")
    assert state.pending_action_id is None


@pytest.mark.asyncio
async def test_voucher_rejects_already_inbound_voucher(gate, mock_llm):
    """P2-E v1.5：订单已有 inbound 凭证 → 入库请求拒；outbound 仍可发。"""
    state = VoucherState(user_message="入库 SO-1", hub_user_id=1, conversation_id="c1",
                          order_id=1)
    async def fake_executor(name, args):
        return {"order_id": 1, "status": "approved",
                "outbound_voucher_count": 0, "inbound_voucher_count": 1,
                "items": [{"product_id": 1, "qty": 10}]}
    out = await preview_voucher_node(state, llm=mock_llm, gate=gate,
                                        tool_executor=fake_executor)
    assert "已有入库凭证" in (out.final_response or "")
    assert state.pending_action_id is None


@pytest.mark.asyncio
async def test_voucher_idempotent_same_context_reuses(gate, mock_llm):
    """同 (conv, user) 同订单 12 小时内 preview 两次复用同一 pending。"""
    state1 = VoucherState(user_message="出库 SO-1", hub_user_id=1, conversation_id="c1", order_id=1)
    state2 = VoucherState(user_message="出库 SO-1", hub_user_id=1, conversation_id="c1", order_id=1)
    async def fake_executor(name, args):
        return {"order_id": 1, "status": "approved",
                "outbound_voucher_count": 0, "inbound_voucher_count": 0,
                "items": [{"product_id": 1, "qty": 10}]}
    p1 = await preview_voucher_node(state1, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    p2 = await preview_voucher_node(state2, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    assert state1.pending_action_id == state2.pending_action_id


@pytest.mark.asyncio
async def test_voucher_idempotent_cross_context_fails_closed(gate, mock_llm):
    """P1-B v1.3：A 在私聊创建 SO-1 voucher pending，B 在群聊发起同订单 — B 必须 fail closed
    （回'已有凭证申请待确认/处理中'），**不能**拿到 A 的 action_id（confirm_node 按 B 的 ctx 查不到）。"""
    # A 在 c1-private 创建
    state_a = VoucherState(user_message="出库 SO-1", hub_user_id=1,
                             conversation_id="c1-private", order_id=1)
    async def fake_executor(name, args):
        return {"order_id": 1, "status": "approved",
                "outbound_voucher_count": 0, "inbound_voucher_count": 0,
                "items": [{"product_id": 1, "qty": 10}]}
    out_a = await preview_voucher_node(state_a, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    assert state_a.pending_action_id is not None  # A 拿到 pending

    # B 在群聊发起同订单
    state_b = VoucherState(user_message="出库 SO-1", hub_user_id=2,
                             conversation_id="c2-group", order_id=1)
    out_b = await preview_voucher_node(state_b, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    assert state_b.pending_action_id is None  # B 没拿到 action_id
    assert "已有凭证申请待确认" in (out_b.final_response or "") or "处理中" in (out_b.final_response or "")
    # A 的 pending 仍存活
    assert await gate.is_pending(state_a.pending_action_id)


@pytest.mark.asyncio
async def test_voucher_outbound_explicit(gate, mock_llm):
    """P1-B v1.4：用户说"出库 SO-1" → voucher_type 必须解析为 outbound 写入 payload + key。"""
    state = VoucherState(user_message="出库 SO-1", hub_user_id=1,
                          conversation_id="c1", order_id=1)
    async def fake_executor(name, args):
        return {"order_id": 1, "status": "approved",
                "outbound_voucher_count": 0, "inbound_voucher_count": 0,
                "items": [{"product_id": 1, "qty": 10}]}
    out = await preview_voucher_node(state, llm=mock_llm, gate=gate, tool_executor=fake_executor)
    assert out.voucher_type == "outbound"
    # payload 必须用真实 outbound（subagent 实施时 inspect ConfirmGate 里的 pending）
    pending = await gate.get_pending_by_id(out.pending_action_id)
    assert pending.payload["args"]["voucher_type"] == "outbound"
    assert pending.idempotency_key == "vch:1:outbound"


@pytest.mark.asyncio
async def test_voucher_inbound_does_not_collide_with_outbound(gate, mock_llm):
    """P1-B v1.4：同订单 outbound 和 inbound 应该是 2 个独立 pending（不同 idempotency_key）。
    如果 key 漏掉 voucher_type，inbound 请求会被 outbound 的 pending 去重掉 → 错凭证 / 用户没反馈。"""
    # 先建一个 outbound
    state_out = VoucherState(user_message="出库 SO-1", hub_user_id=1,
                               conversation_id="c1", order_id=1)
    async def fake_executor(name, args):
        return {"order_id": 1, "status": "approved",
                "outbound_voucher_count": 0, "inbound_voucher_count": 0,
                "items": [{"product_id": 1, "qty": 10}]}
    p_out = await preview_voucher_node(state_out, llm=mock_llm, gate=gate,
                                         tool_executor=fake_executor)
    assert state_out.voucher_type == "outbound"

    # 同订单同 user 同 conv 起 inbound — 不应该被 outbound 的 pending 去重掉
    state_in = VoucherState(user_message="入库 SO-1", hub_user_id=1,
                              conversation_id="c1", order_id=1)
    p_in = await preview_voucher_node(state_in, llm=mock_llm, gate=gate,
                                        tool_executor=fake_executor)
    assert state_in.voucher_type == "inbound"
    assert state_in.pending_action_id != state_out.pending_action_id  # 两个独立 pending
    pending_in = await gate.get_pending_by_id(state_in.pending_action_id)
    assert pending_in.payload["args"]["voucher_type"] == "inbound"
    assert pending_in.idempotency_key == "vch:1:inbound"


@pytest.mark.asyncio
async def test_voucher_type_unresolved_asks_user(gate, mock_llm):
    """用户没说出库 / 入库 → ask_user 问，不创建 pending。"""
    state = VoucherState(user_message="给 SO-1 出凭证", hub_user_id=1,
                          conversation_id="c1", order_id=1)
    out = await preview_voucher_node(state, llm=mock_llm, gate=gate,
                                       tool_executor=AsyncMock())
    assert state.pending_action_id is None
    assert "出库" in (out.final_response or "") and "入库" in (out.final_response or "")
    assert "voucher_type" in out.missing_fields


@pytest.mark.asyncio
async def test_voucher_two_pendings_different_orders_select_correctly(gate):
    """两个不同订单的 voucher pending — 用户选 2，只执行第 2 个 payload。"""
    p1 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1", subgraph="voucher", action_prefix="vch",
        summary="SO-1 出库", idempotency_key="vch:1:outbound",
        payload={"tool_name": "create_voucher_draft",
                 "args": {"order_id": 1, "voucher_type": "outbound", "items": [], "remark": ""}},
    )
    p2 = await gate.create_pending(
        hub_user_id=1, conversation_id="c1", subgraph="voucher", action_prefix="vch",
        summary="SO-2 出库", idempotency_key="vch:2:outbound",
        payload={"tool_name": "create_voucher_draft",
                 "args": {"order_id": 2, "voucher_type": "outbound", "items": [], "remark": ""}},
    )
    state = AgentState(user_message="2", hub_user_id=1, conversation_id="c1")
    state = await confirm_node(state, gate=gate)
    assert state.confirmed_payload["args"]["order_id"] == 2
    assert await gate.is_pending(p1.action_id)


@pytest.mark.asyncio
async def test_voucher_cross_conversation_isolation(gate):
    """同 user 在 c1 起 voucher preview，c2 回"确认"看不到。"""
    p = await gate.create_pending(hub_user_id=1, conversation_id="c1",
                                    subgraph="voucher", action_prefix="vch",
                                    summary="SO-1 出库", idempotency_key="vch:1:outbound",
                                    payload={"tool_name": "create_voucher_draft", "args": {}})
    state = AgentState(user_message="确认", hub_user_id=1, conversation_id="c2")
    state = await confirm_node(state, gate=gate)
    assert "没有待办" in (state.final_response or "")
```

ConfirmGate.create_pending 还要加 `idempotency_key: str | None = None` 参数：相同 key 在 ttl 内复用已有 pending（返回原 PendingAction），不创建新的。Task 0.5 改动需补这一项 — subagent 实施 5.4 时回头一并加。

- [ ] **Step 4: Commit**

---

### Task 5.5: 故事 6 acceptance + 跨会话隔离 fixture

**Files:**
- Create: `backend/tests/agent/fixtures/scenarios/story6_adjust_price_confirm.yaml`
- Create: `backend/tests/agent/fixtures/scenarios/story6b_cross_conversation_isolation.yaml`

```yaml
# story6_adjust_price_confirm.yaml
name: 故事 6：调价 + ConfirmGate
turns:
  - input: "把阿里的 X1 价格调到 280"
    expected_intent: adjust_price
    tool_caps:
      search_customers: 1
      search_products: 1
      get_product_customer_prices: 1
      adjust_price_request: 0
    must_contain: ["调价预览", "回复"确认""]
    creates_pending_action: true
  - input: "确认"
    expected_intent: confirm
    tool_caps:
      adjust_price_request: 1
    must_contain: ["调价已申请"]

# story6b_cross_conversation_isolation.yaml
name: 故事 6 变体：跨会话隔离
turns:
  - input: "把阿里的 X1 价格调到 280"
    conversation_id: c1-private
    hub_user_id: 1
    expected_intent: adjust_price
    creates_pending_action: true
  - input: "确认"
    conversation_id: c2-group  # 切到群聊
    hub_user_id: 1
    expected_intent: confirm
    must_contain: ["没有待办"]    # 看不到 c1-private 的 pending
    pending_state: {adj-1: still_pending}  # c1-private 的 pending 在 c2-group 看不到，但 c1 仍存活
```

- [ ] Commit

---

### Task 5.6: 多 pending 行为 fixture + 测试

**Files:**
- Create: `backend/tests/agent/fixtures/scenarios/multi_pending.yaml`
- Append: `backend/tests/agent/test_node_confirm.py`

```yaml
# multi_pending.yaml
name: 多 pending 不自动 claim
setup:
  - create_pending: {action_id: "adj-1", subgraph: "adjust_price", summary: "阿里 X1 → 280"}
  - create_pending: {action_id: "vch-1", subgraph: "voucher", summary: "SO-001 出库"}
turns:
  - input: "确认"
    expected_intent: confirm
    must_contain: ["1)", "2)", "adj-1", "vch-1"]
    pending_state: {adj-1: still_pending, vch-1: still_pending}  # 都没 claim
  - input: "1"  # 选编号 1
    expected_intent: confirm
    tool_caps:
      adjust_price_request: 1
    pending_state: {adj-1: claimed, vch-1: still_pending}
```

- [ ] Commit

---

### Task 5.7: Phase 5 写 tool fallback 协议测试

**Files:**
- Create: `backend/tests/agent/test_fallback_protocol.py`

**Spec ref:** §12.1

- [ ] **Step 1: 写测试**

```python
# backend/tests/agent/test_fallback_protocol.py
"""§12.1 fallback 协议：写 tool fail closed / 读 tool 可降级。"""
import pytest
from unittest.mock import patch
import httpx
from hub.agent.llm_client import DeepSeekLLMClient, ToolClass, LLMFallbackError


@pytest.mark.asyncio
async def test_write_tool_strict_400_fails_closed():
    client = DeepSeekLLMClient(api_key="x", model="m")
    async def fake_400(*a, **kw):
        from httpx import Response, Request, HTTPStatusError
        resp = Response(400, request=Request("POST", "u"),
                          json={"error": "strict schema violation"})
        raise HTTPStatusError("400", request=resp.request, response=resp)
    with patch.object(client._http, "post", side_effect=fake_400):
        with pytest.raises(LLMFallbackError):
            await client.chat(messages=[{"role": "user", "content": "x"}],
                                tool_class=ToolClass.WRITE,
                                tools=[...])


@pytest.mark.asyncio
async def test_read_tool_strict_400_raises_http_status_error_not_fallback():
    """读 tool 路径上 strict 400 必须 raise HTTPStatusError（非 LLMFallbackError），
    让上层 caller 决定降级策略 — 不能 client 内部默默吞掉。"""
    client = DeepSeekLLMClient(api_key="x", model="m")
    async def fake_400(*a, **kw):
        from httpx import Response, Request, HTTPStatusError
        resp = Response(400, request=Request("POST", "u"),
                          json={"error": "strict schema violation"})
        raise HTTPStatusError("400", request=resp.request, response=resp)
    with patch.object(client._http, "post", side_effect=fake_400):
        with pytest.raises(httpx.HTTPStatusError):  # ← 关键：不是 LLMFallbackError
            await client.chat(messages=[{"role": "user", "content": "x"}],
                                tool_class=ToolClass.READ,  # ← 读 tool
                                tools=[...])


@pytest.mark.asyncio
async def test_write_tool_fallback_alarm_metric_written(monkeypatch):
    """写 tool 路径 fallback 必须打 metric — fallback 计数 > 0 时触发 alarm。

    实现要求：DeepSeekLLMClient 在 fail closed 抛 LLMFallbackError 前必须
    通过 hub.metrics.incr('llm.fallback', tags={'tool_class': 'write'}) 打点。
    """
    captured: list[tuple[str, dict]] = []
    def fake_incr(name, **kw):
        captured.append((name, kw))
    monkeypatch.setattr("hub.metrics.incr", fake_incr)

    client = DeepSeekLLMClient(api_key="x", model="m")
    async def fake_400(*a, **kw):
        from httpx import Response, Request, HTTPStatusError
        resp = Response(400, request=Request("POST", "u"),
                          json={"error": "strict schema violation"})
        raise HTTPStatusError("400", request=resp.request, response=resp)
    with patch.object(client._http, "post", side_effect=fake_400):
        with pytest.raises(LLMFallbackError):
            await client.chat(messages=[{"role": "user", "content": "x"}],
                                tool_class=ToolClass.WRITE,
                                tools=[...])
    assert any(name == "llm.fallback" and kw.get("tags", {}).get("tool_class") == "write"
                for name, kw in captured), f"未打 metric：{captured}"
```

- [ ] Commit

---

### Task 5.8: Phase 5 集成验证

- [ ] **Step 1: 跑全部 Phase 5 测试**

```bash
pytest tests/agent/test_node_confirm.py tests/agent/test_subgraph_adjust_price.py tests/agent/test_subgraph_adjust_stock.py tests/agent/test_subgraph_voucher.py tests/agent/test_fallback_protocol.py -v
```

Expected: 全 PASS。

- [ ] **Step 2: 标记 M5 完成**

---

## Phase 6：Quote 子图（M6，0.5 天）

**Goal**：quote 子图比 contract 简单（无 shipping，3 节点）。覆盖故事 5。

### Task 6.1: quote prompt + subgraph（**v1.7 P2-B 完整骨架**）

**Files:**
- Create: `backend/hub/agent/prompt/subgraph_prompts/quote.py`
- Create: `backend/hub/agent/graph/subgraphs/quote.py`
- Create: `backend/tests/agent/test_subgraph_quote.py`

**结构**：set_origin → resolve_customer → resolve_products → parse_contract_items → generate_quote → format_response（无 shipping，比 contract 少一节点）

**复用**：4.1 resolve_customer / 4.2 resolve_products / 4.3 parse_contract_items（quote 也要 qty/price 对齐）

- [ ] **Step 1: 写 prompt**

```python
# backend/hub/agent/prompt/subgraph_prompts/quote.py
QUOTE_SYSTEM_PROMPT = """你是销售报价单生成助手。流程：
1. 找客户（resolve_customer）
2. 找产品（resolve_products，可多个）
3. 对齐 qty/price（parse_contract_items）
4. 调 generate_price_quote 生成报价单 PDF

**禁止**：
- 调 check_inventory（报价不需要）
- 报价缺数量 / 价格时默认填值（必须 ask_user）
- 报价生成时再次反问"是否确认"（直接生成）
"""
```

- [ ] **Step 2: 写测试 — 含"报价多候选 → 选 2 → 仍走 quote"**

```python
# backend/tests/agent/test_subgraph_quote.py
import pytest
from unittest.mock import AsyncMock
from hub.agent.graph.state import QuoteState, AgentState, Intent, CustomerInfo


@pytest.mark.asyncio
async def test_quote_subgraph_no_check_inventory_tool():
    from hub.agent.tools.registry import ToolRegistry
    from hub.agent.tools import register_all_tools
    reg = ToolRegistry()
    register_all_tools(reg)
    schemas = reg.schemas_for_subgraph("quote")
    names = {s["function"]["name"] for s in schemas}
    assert "check_inventory" not in names
    assert "generate_contract_draft" not in names  # 物理隔离 — 报价不应能生成合同
    assert "generate_price_quote" in names


@pytest.mark.asyncio
async def test_quote_subgraph_set_origin_active_subgraph():
    """v1.6 P1-A：报价子图入口必须写 state.active_subgraph='quote'，让 pre_router
    下一轮看到 candidate 时回 quote 而不是兜底 contract。"""
    from hub.agent.graph.subgraphs.quote import build_quote_subgraph
    compiled = build_quote_subgraph(llm=AsyncMock(), tool_executor=AsyncMock())
    nodes = set(compiled.get_graph().nodes)
    assert "set_origin" in nodes
    assert "generate_quote" in nodes


@pytest.mark.asyncio
async def test_quote_multi_customer_select_2_stays_in_quote_route():
    """v1.7 P2-B 集成验收：报价多候选 → 下一轮"选 2" → pre_router 看到
    state.active_subgraph='quote' → 路由到 quote（不是 contract）。"""
    from hub.agent.graph.agent import GraphAgent
    from hub.agent.graph.config import build_langgraph_config
    import json

    tool_call_log: list[tuple[str, dict]] = []
    async def fake_executor(name, args):
        tool_call_log.append((name, args))
        if name == "search_customers":
            return [
                {"id": 10, "name": "阿里巴巴"},
                {"id": 11, "name": "阿里云"},
            ]
        if name == "search_products":
            return [{"id": 1, "name": "X1"}]
        if name == "generate_price_quote":
            return {"quote_id": 888}
        return None

    # P2-A v1.9 + P1-A v1.9：quote 子图也加了 extract_contract_context；第 2 轮"选 2"跳过 LLM
    llm_responses = [
        # 第 1 轮 router → QUOTE
        type("R", (), {"text": 'quote"', "finish_reason": "stop", "tool_calls": [],
                       "cache_hit_rate": 0.0})(),
        # 第 1 轮 extract_contract_context → 抽 customer/product/items_raw
        type("R", (), {"text": json.dumps({
            "customer_name": "阿里",
            "product_hints": ["X1"],
            "items_raw": [{"hint": "X1", "qty": 50, "price": 300}],
            "shipping": {"address": None, "contact": None, "phone": None},  # 报价无 shipping
        }), "finish_reason": "stop", "tool_calls": [], "cache_hit_rate": 0.0})(),
        # 第 1 轮 resolve_customer → search_customers（命中 2 个 → ask_user）
        type("R", (), {"text": "", "finish_reason": "tool_calls",
                       "tool_calls": [{"id": "1", "type": "function",
                          "function": {"name": "search_customers",
                                        "arguments": json.dumps({"query": "阿里"})}}],
                       "cache_hit_rate": 0.0})(),
        # 第 2 轮"选 2"：
        # - pre_router 看到 candidate_customers + active_subgraph=quote → Intent.QUOTE
        # - extract_contract_context 看到 _looks_like_pure_selection("选 2") → 跳过 LLM
        # - resolve_customer 消费候选 → 不调 LLM
        # 第 2 轮 resolve_products → search_products
        type("R", (), {"text": "", "finish_reason": "tool_calls",
                       "tool_calls": [{"id": "2", "type": "function",
                          "function": {"name": "search_products",
                                        "arguments": json.dumps({"query": "X1"})}}],
                       "cache_hit_rate": 0.5})(),
        # parse_contract_items：v1.8 从 items_raw 本地匹配（不消耗 LLM）
        # generate_quote
        type("R", (), {"text": "", "finish_reason": "tool_calls",
                       "tool_calls": [{"id": "3", "type": "function",
                          "function": {"name": "generate_price_quote",
                                        "arguments": json.dumps({"customer_id": 11,
                                            "items": [{"product_id": 1, "qty": 50, "price": 300}]})}}],
                       "cache_hit_rate": 0.5})(),
        # format_response
        type("R", (), {"text": "OK", "finish_reason": "stop", "tool_calls": [],
                       "cache_hit_rate": 0.5})(),
    ]
    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=llm_responses)
    gate = AsyncMock()
    gate.list_pending_for_context = AsyncMock(return_value=[])

    agent = GraphAgent(llm=llm, registry=AsyncMock(), confirm_gate=gate,
                        session_memory=AsyncMock(), tool_executor=fake_executor)

    # 第 1 轮：起报价（多候选）
    await agent.run(user_message="给阿里报 X1 50 个的价 300",
                     hub_user_id=1, conversation_id="quote-c1")
    config = build_langgraph_config(conversation_id="quote-c1", hub_user_id=1)
    snap1 = await agent.compiled_graph.aget_state(config)
    assert snap1.values["active_subgraph"] == "quote"
    assert len(snap1.values["candidate_customers"]) == 2

    # 第 2 轮："选 2" — 必须进 quote 子图（不是 contract）
    pre_count = len(tool_call_log)
    await agent.run(user_message="选 2", hub_user_id=1, conversation_id="quote-c1")
    second_round_tools = [n for n, _ in tool_call_log[pre_count:]]
    # 关键断言：调了 generate_price_quote（quote 路径），**没**调 generate_contract_draft（contract 路径）
    assert "generate_price_quote" in second_round_tools
    assert "generate_contract_draft" not in second_round_tools

    # v1.11 P1：报价生成 → format_response → cleanup_after_quote → END
    snap2 = await agent.compiled_graph.aget_state(config)
    # 业务结果保留
    assert snap2.values["quote_id"] == 888
    assert snap2.values["file_sent"] is True
    # 工作上下文已清空（cleanup_after_quote 之后）
    assert snap2.values["customer"] is None
    assert snap2.values["products"] == []
    assert snap2.values["items"] == []
    assert snap2.values["candidate_customers"] == []
    assert snap2.values["candidate_products"] == {}
    assert snap2.values["extracted_hints"] == {}
    assert snap2.values["active_subgraph"] is None
```

- [ ] **Step 3: 实现 quote 子图（含 set_origin 写 quote / generate_quote 清候选）**

```python
# backend/hub/agent/graph/subgraphs/quote.py
"""quote 子图 — 4 节点（set_origin/resolve_customer/resolve_products/parse_items/generate_quote）。

v1.6 P1-A + v1.7 P2-B：必须有 set_origin 节点写 active_subgraph="quote"，
generate_quote 成功后清候选 + active_subgraph，与 contract 子图同模式。
"""
from __future__ import annotations
from langgraph.graph import StateGraph, START, END

from hub.agent.graph.state import QuoteState, ShippingInfo  # v1.10 P1-A
from hub.agent.graph.nodes.extract_contract_context import extract_contract_context_node  # v1.9 P1-A
from hub.agent.graph.nodes.resolve_customer import resolve_customer_node
from hub.agent.graph.nodes.resolve_products import resolve_products_node
from hub.agent.graph.nodes.parse_contract_items import parse_contract_items_node
from hub.agent.graph.nodes.ask_user import ask_user_node
from hub.agent.graph.nodes.format_response import format_response_node
from hub.agent.llm_client import DeepSeekLLMClient


async def generate_quote_node(state: QuoteState, *, llm, tool_executor) -> QuoteState:
    """调 generate_price_quote — strict + 写 tool fail closed。

    cleanup 在 cleanup_after_quote_node 做，不在这里 — 让 format_response 先用
    state.customer.name / len(state.items) 写回执。
    """
    payload = {
        "customer_id": state.customer.id,
        "items": [{"product_id": i.product_id, "qty": i.qty, "price": float(i.price)}
                  for i in state.items],
    }
    result = await tool_executor("generate_price_quote", payload)
    state.quote_id = result.get("quote_id")
    state.file_sent = True
    return state


async def cleanup_after_quote_node(state: QuoteState) -> QuoteState:
    """P1 v1.11：报价流程完成后清完整工作上下文，放在 format_response 后。"""
    state.active_subgraph = None
    state.candidate_customers = []
    state.candidate_products = {}
    state.customer = None
    state.products = []
    state.items = []
    state.shipping = ShippingInfo()
    state.extracted_hints = {}
    state.missing_fields = []
    return state


def _route_after_resolve_products(state: QuoteState) -> str:
    if state.candidate_customers or state.candidate_products:
        return "ask_user"
    return "parse_contract_items"


def _route_after_parse_items(state: QuoteState) -> str:
    if any(mf.startswith("item_") for mf in state.missing_fields):
        return "ask_user"
    return "generate_quote"


def build_quote_subgraph(*, llm: DeepSeekLLMClient, tool_executor):
    async def _set_origin(s: QuoteState):
        s.active_subgraph = "quote"
        return s
    async def _extract_context(s):
        # v1.9 P1-A：quote 也复用 extract_contract_context — 同一节点抽 customer/product/items_raw/shipping
        # 报价用不到 shipping 但写到 state 也无害。位置在 set_origin 后第一个，保证多候选 ask_user 之前
        # 第一轮的 product_hints / items_raw 已落 state，第二轮"选 2"后 parse_items 能读到。
        return await extract_contract_context_node(s, llm=llm)
    async def _resolve_customer(s):
        return await resolve_customer_node(s, llm=llm, tool_executor=tool_executor)
    async def _resolve_products(s):
        return await resolve_products_node(s, llm=llm, tool_executor=tool_executor)
    async def _parse_items(s):
        return await parse_contract_items_node(s, llm=llm)
    async def _ask_user(s):
        return await ask_user_node(s)
    async def _generate(s):
        return await generate_quote_node(s, llm=llm, tool_executor=tool_executor)
    async def _format(s):
        return await format_response_node(
            s, llm=llm, template_key="quote",
            summary=f"quote_id={s.quote_id}, customer={s.customer.name if s.customer else 'unknown'}, items={len(s.items)}",
        )
    async def _cleanup(s):
        return await cleanup_after_quote_node(s)  # v1.11 P1

    g = StateGraph(QuoteState)
    g.add_node("set_origin", _set_origin)
    g.add_node("extract_contract_context", _extract_context)  # v1.9 P1-A
    g.add_node("resolve_customer", _resolve_customer)
    g.add_node("resolve_products", _resolve_products)
    g.add_node("parse_contract_items", _parse_items)
    g.add_node("ask_user", _ask_user)
    g.add_node("generate_quote", _generate)
    g.add_node("format_response", _format)
    g.add_node("cleanup_after_quote", _cleanup)  # v1.11 P1
    g.add_edge(START, "set_origin")
    g.add_edge("set_origin", "extract_contract_context")  # v1.9 P1-A
    g.add_edge("extract_contract_context", "resolve_customer")
    g.add_conditional_edges(
        "resolve_customer",
        lambda s: "ask_user" if s.candidate_customers or "customer" in s.missing_fields else "resolve_products",
        {"ask_user": "ask_user", "resolve_products": "resolve_products"},
    )
    g.add_conditional_edges(
        "resolve_products", _route_after_resolve_products,
        {"ask_user": "ask_user", "parse_contract_items": "parse_contract_items"},
    )
    g.add_conditional_edges(
        "parse_contract_items", _route_after_parse_items,
        {"ask_user": "ask_user", "generate_quote": "generate_quote"},
    )
    g.add_edge("ask_user", END)
    g.add_edge("generate_quote", "format_response")
    # P1 v1.11：format_response → cleanup_after_quote → END
    g.add_edge("format_response", "cleanup_after_quote")
    g.add_edge("cleanup_after_quote", END)
    return g.compile()
```

- [ ] **Step 4: PASS + Commit**

---

### Task 6.2: 故事 5 fixture

```yaml
# backend/tests/agent/fixtures/scenarios/story5_quote.yaml
name: 故事 5：报价
turns:
  - input: "给阿里报 X1 50 个的价"
    expected_intent: quote
    tool_caps:
      search_customers: 1
      search_products: 1
      generate_price_quote: 1
      check_inventory: 0
    sent_files_min: 1
    must_contain: ["报价单已生成"]
```

- [ ] Commit

---

### Task 6.3: Phase 6 验证

- [ ] **Step 1: 跑 quote 测试**

---

## Phase 7：接入 + 旧代码删除（M7，0.5 天）

**Goal**：GraphAgent 顶层 + dingtalk_inbound 切换 + 删 chain_agent.py / context_builder.py / 12 条行为准则。

### Task 7.1: GraphAgent 顶层入口

**Files:**
- Create: `backend/hub/agent/graph/agent.py`
- Create: `backend/tests/agent/test_graph_agent.py`

**Spec ref:** §2.1 thread_id 复合 key

- [ ] **Step 1: 写测试**

```python
# backend/tests/agent/test_graph_agent.py
import pytest
from unittest.mock import AsyncMock
from hub.agent.graph.agent import GraphAgent


@pytest.mark.asyncio
async def test_graph_agent_uses_compound_thread_id():
    """spec §2.1：LangGraph config thread_id 必须 = f'{conv}:{user}'。"""
    captured = {}
    fake_compiled = AsyncMock()
    async def fake_invoke(state, *, config):
        captured["config"] = config
        return {"final_response": "ok"}
    fake_compiled.ainvoke = fake_invoke

    agent = GraphAgent(compiled_graph=fake_compiled, llm=AsyncMock(),
                        registry=AsyncMock(), confirm_gate=AsyncMock(),
                        session_memory=AsyncMock())
    await agent.run(user_message="hi", hub_user_id=42, conversation_id="conv-1")
    assert captured["config"]["configurable"]["thread_id"] == "conv-1:42"


@pytest.mark.asyncio
async def test_graph_agent_build_node_set():
    """P2-H：_build 必须创建完整节点集合，主图不能漏挂任何子图。"""
    from hub.agent.graph.agent import GraphAgent
    from unittest.mock import AsyncMock
    agent = GraphAgent(
        llm=AsyncMock(), registry=AsyncMock(), confirm_gate=AsyncMock(),
        session_memory=AsyncMock(), tool_executor=AsyncMock(),
    )
    nodes = set(agent.compiled_graph.get_graph().nodes)
    expected = {
        "router", "chat", "query", "contract", "quote", "voucher",
        "adjust_price", "adjust_stock", "confirm",
        "commit_adjust_price", "commit_adjust_stock", "commit_voucher",
    }
    assert expected <= nodes, f"主图缺节点：{expected - nodes}"


@pytest.mark.asyncio
async def test_graph_agent_router_to_subgraph_routing():
    """P2-H：router → 7 个子图 + confirm 的条件边都要存在。"""
    from hub.agent.graph.agent import GraphAgent
    from unittest.mock import AsyncMock
    agent = GraphAgent(
        llm=AsyncMock(), registry=AsyncMock(), confirm_gate=AsyncMock(),
        session_memory=AsyncMock(), tool_executor=AsyncMock(),
    )
    edges = agent.compiled_graph.get_graph().edges
    # 至少有从 router 出发到 8 个目标的条件边
    router_targets = {e.target for e in edges if e.source == "router"}
    assert {"chat", "query", "contract", "quote", "voucher",
            "adjust_price", "adjust_stock", "confirm"} <= router_targets


@pytest.mark.asyncio
async def test_candidate_persists_through_checkpoint_and_consumed_next_round():
    """P1-A v1.4 + P2-D v1.5 集成验收：多客户候选 → 下一轮"选 2" → resolve_customer 真的消费。

    完整两轮 ainvoke：
      第 1 轮："给阿里做合同 X1 10 个 300，地址北京海淀，张三 13800001111"
        - resolve_customer 拉 3 候选 → 写 candidate_customers + missing_fields=customer_choice
        - end with ask_user 输出
      第 2 轮："选 2"
        - pre_router 看 checkpoint hydrate 的 candidate_customers + "选 2" → Intent.CONTRACT
        - resolve_customer 消费 candidates[1] → state.customer = 阿里云
        - parse_items / validate / generate 一路跑
        - 生成合同
    """
    from hub.agent.graph.agent import GraphAgent
    from hub.agent.graph.config import build_langgraph_config
    from hub.agent.graph.state import CustomerInfo, Intent
    from unittest.mock import AsyncMock
    import json

    tool_call_log: list[tuple[str, dict]] = []

    async def fake_tool_executor(name: str, args: dict):
        tool_call_log.append((name, args))
        if name == "search_customers":
            # 第 1 轮多命中 — 第 2 轮**不应再调**（直接消费候选）
            return [
                {"id": 10, "name": "阿里巴巴"},
                {"id": 11, "name": "阿里云"},
                {"id": 12, "name": "阿里影业"},
            ]
        if name == "search_products":
            return [{"id": 1, "name": "X1"}]
        if name == "generate_contract_draft":
            return {"draft_id": 999}
        return None

    # mock LLM — 按调用次序返回不同 tool_call
    # P2-A v1.9：必须为 extract_contract_context 也预留响应（contract 子图入口已加这个节点）。
    # 第 2 轮"选 2"是 _looks_like_pure_selection 命中 → extract_context 跳过 LLM 不消耗响应。
    llm_responses = [
        # 第 1 轮 router → CONTRACT
        type("R", (), {"text": 'contract"', "finish_reason": "stop", "tool_calls": [],
                       "cache_hit_rate": 0.0})(),
        # 第 1 轮 extract_contract_context → 抽 customer_name + product_hints + items_raw + shipping
        type("R", (), {"text": json.dumps({
            "customer_name": "阿里",
            "product_hints": ["X1"],
            "items_raw": [{"hint": "X1", "qty": 10, "price": 300}],
            "shipping": {"address": "北京海淀", "contact": "张三", "phone": "13800001111"},
        }), "finish_reason": "stop", "tool_calls": [], "cache_hit_rate": 0.0})(),
        # 第 1 轮 resolve_customer → search_customers（多命中 → ask_user，第 1 轮到此 END）
        type("R", (), {"text": "", "finish_reason": "tool_calls",
                       "tool_calls": [{"id": "1", "type": "function",
                          "function": {"name": "search_customers",
                                        "arguments": json.dumps({"query": "阿里"})}}],
                       "cache_hit_rate": 0.0})(),
        # 第 2 轮"选 2"：
        # - pre_router 命中 candidate_customers + active_subgraph=contract → Intent.CONTRACT
        # - extract_contract_context 看到 _looks_like_pure_selection("选 2") → 跳过 LLM **不消耗响应**
        # - resolve_customer 入口消费候选 → 不调 LLM
        # 第 2 轮 resolve_products → search_products
        type("R", (), {"text": "", "finish_reason": "tool_calls",
                       "tool_calls": [{"id": "2", "type": "function",
                          "function": {"name": "search_products",
                                        "arguments": json.dumps({"query": "X1"})}}],
                       "cache_hit_rate": 0.5})(),
        # 第 2 轮 parse_contract_items：v1.8 优先用 state.extracted_hints['items_raw'] 本地匹配
        # → **不消耗 LLM 响应**（hint X1 唯一命中产品 X1，本地映射成功）
        # 第 2 轮 validate_inputs（thinking on）
        type("R", (), {"text": json.dumps({"missing_fields": [], "warnings": []}),
                       "finish_reason": "stop", "tool_calls": [], "cache_hit_rate": 0.5})(),
        # 第 2 轮 generate_contract → generate_contract_draft
        type("R", (), {"text": "", "finish_reason": "tool_calls",
                       "tool_calls": [{"id": "3", "type": "function",
                          "function": {"name": "generate_contract_draft",
                                        "arguments": json.dumps({
                                            "customer_id": 11, "items": [{"product_id": 1, "qty": 10, "price": 300}],
                                            "shipping_address": "北京海淀", "contact": "张三",
                                            "phone": "13800001111", "extras": {},
                                        })}}],
                       "cache_hit_rate": 0.5})(),
        # 第 2 轮 format_response
        type("R", (), {"text": "OK", "finish_reason": "stop", "tool_calls": [],
                       "cache_hit_rate": 0.5})(),
    ]
    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=llm_responses)

    # ConfirmGate mock：list_pending_for_context → 空（这个 case 没有 pending）
    gate = AsyncMock()
    gate.list_pending_for_context = AsyncMock(return_value=[])

    agent = GraphAgent(
        llm=llm, registry=AsyncMock(), confirm_gate=gate,
        session_memory=AsyncMock(), tool_executor=fake_tool_executor,
    )

    # ===== 第 1 轮 =====
    res1 = await agent.run(
        user_message="给阿里做合同 X1 10 个 300，地址北京海淀，张三 13800001111",
        hub_user_id=1, conversation_id="c1",
    )
    # 第 1 轮 search_customers 调过一次
    first_round_tool_names = [n for n, _ in tool_call_log]
    assert "search_customers" in first_round_tool_names

    # 验 checkpoint 真的保留了 candidate_customers
    config = build_langgraph_config(conversation_id="c1", hub_user_id=1)
    snapshot1 = await agent.compiled_graph.aget_state(config)
    assert len(snapshot1.values["candidate_customers"]) == 3
    assert snapshot1.values["candidate_customers"][1]["id"] == 11  # 阿里云

    # ===== 第 2 轮：选 2 =====
    pre_round2_call_count = len(tool_call_log)
    res2 = await agent.run(
        user_message="选 2",
        hub_user_id=1, conversation_id="c1",
    )
    # 第 2 轮 search_customers **绝对不能**再调（直接消费候选）
    second_round_tools = [n for n, _ in tool_call_log[pre_round2_call_count:]]
    assert "search_customers" not in second_round_tools, (
        f"第 2 轮不应再调 search_customers，实际调了：{second_round_tools}")
    # 第 2 轮调了 search_products + generate_contract_draft
    assert "search_products" in second_round_tools
    assert "generate_contract_draft" in second_round_tools

    # 验 final state（v1.11 P1：合同生成 → format_response → cleanup_after_contract → END）
    snapshot2 = await agent.compiled_graph.aget_state(config)
    # 业务结果保留
    assert snapshot2.values["draft_id"] == 999
    assert snapshot2.values["file_sent"] is True
    # 工作上下文已清空（cleanup_after_contract 之后）— 避免下一轮"给百度做合同"复用阿里
    assert snapshot2.values["customer"] is None
    assert snapshot2.values["products"] == []
    assert snapshot2.values["items"] == []
    assert snapshot2.values["candidate_customers"] == []
    assert snapshot2.values["candidate_products"] == {}
    assert snapshot2.values["extracted_hints"] == {}
    assert snapshot2.values["active_subgraph"] is None
    # shipping 也清空（用空 ShippingInfo，address/contact/phone 都是 None）
    assert snapshot2.values["shipping"]["address"] is None
    assert snapshot2.values["shipping"]["contact"] is None
```

- [ ] **Step 2: 实现 GraphAgent**

```python
# backend/hub/agent/graph/agent.py
"""GraphAgent 顶层入口 — 编译完整 graph + run 接口（兼容 ChainAgent.run 签名）。"""
from __future__ import annotations

import logging  # P2-B v1.8：pre_router fallback 路径用 logger.warning，必须 import
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END

from hub.agent.graph.config import build_langgraph_config
from hub.agent.graph.state import AgentState, Intent
from hub.agent.graph.router import router_node
from hub.agent.graph.subgraphs.chat import chat_subgraph
from hub.agent.graph.subgraphs.query import query_subgraph
from hub.agent.graph.subgraphs.contract import build_contract_subgraph
from hub.agent.graph.subgraphs.quote import build_quote_subgraph
from hub.agent.graph.subgraphs.voucher import build_voucher_subgraph
from hub.agent.graph.subgraphs.adjust_price import build_adjust_price_subgraph
from hub.agent.graph.subgraphs.adjust_stock import build_adjust_stock_subgraph
from hub.agent.graph.nodes.confirm import confirm_node


logger = logging.getLogger(__name__)  # P2-B v1.8


@dataclass
class AgentResult:
    text: str | None
    error: str | None
    kind: str  # "text" / "file" / "error"


class GraphAgent:
    """ChainAgent 的替代品 — 兼容 .run() 签名。"""

    def __init__(self, *, llm, registry, confirm_gate, session_memory,
                  tool_executor, compiled_graph=None):
        self.llm = llm
        self.registry = registry
        self.confirm_gate = confirm_gate
        self.session_memory = session_memory
        self.tool_executor = tool_executor
        self.compiled_graph = compiled_graph or self._build()

    async def _peek_last_subgraph_state(self, *, conversation_id: str, hub_user_id: int) -> dict | None:
        """P1-A v1.3 helper：从 LangGraph checkpointer 读上轮 (conv, user) 的 state，
        用于 pre_router 检查是否有 candidate_customers / candidate_products / 上一业务 intent。

        实现：
          config = build_langgraph_config(conversation_id=conv, hub_user_id=user)
          tup = await self.compiled_graph.aget_state(config)
          return tup.values if tup else None
        """
        from hub.agent.graph.config import build_langgraph_config
        try:
            config = build_langgraph_config(
                conversation_id=conversation_id, hub_user_id=hub_user_id,
            )
            tup = await self.compiled_graph.aget_state(config)
            return tup.values if tup else None
        except Exception:
            return None

    def _build(self):
        """主图组装：router → 7 子图分发 → confirm 后用 selected.subgraph 二次路由。

        节点：
          - router_node               : Intent 分类
          - chat / query              : 直接调（已是 callable，不是 compiled subgraph）
          - contract / quote / voucher / adjust_price / adjust_stock : compiled subgraph
          - confirm_node              : 0/1/>1 三分支；>1 不 claim 直接 END
          - confirm_dispatch_node     : 1 个 pending claim 成功后，把 payload 注入 state
                                          再路由到 pending.subgraph 的 commit 子节点

        路由：
          - START → router
          - router → 据 state.intent 进对应子图（CONFIRM 进 confirm_node；UNKNOWN 进 chat）
          - 子图执行完 → END（已写 final_response）
          - confirm_node 1 命中 → confirm_dispatch
          - confirm_dispatch → 据 selected.subgraph 进对应 commit 子节点（adjust_price.commit 等）
        """
        from langgraph.checkpoint.memory import MemorySaver
        from hub.agent.graph.state import AgentState

        contract_sub = build_contract_subgraph(llm=self.llm, tool_executor=self.tool_executor)
        quote_sub = build_quote_subgraph(llm=self.llm, tool_executor=self.tool_executor)
        voucher_sub = build_voucher_subgraph(
            llm=self.llm, tool_executor=self.tool_executor, gate=self.confirm_gate,
        )
        adjust_price_sub = build_adjust_price_subgraph(
            llm=self.llm, tool_executor=self.tool_executor, gate=self.confirm_gate,
        )
        adjust_stock_sub = build_adjust_stock_subgraph(
            llm=self.llm, tool_executor=self.tool_executor, gate=self.confirm_gate,
        )

        async def _confirm_dispatch_node(state: AgentState) -> AgentState:
            """confirm_node 已 claim，payload 在 state.confirmed_payload（正式字段）。
            这里是占位节点 — 真正路由由 _route_after_confirm 条件边据 state.confirmed_subgraph 决定。"""
            return state

        # P1-A v1.3：context-aware pre_router — 在调真 LLM router 前先看 (conv, user) 上下文
        # 解决"用户第二轮回 '1' 被 router 判成 unknown/chat 进不到对应子图"。
        async def _pre_router(state: AgentState) -> AgentState:
            """预路由：根据 (conv, user) 的 pending action / candidate state 直接决定 intent，
            走得通就跳过 LLM router；走不通就清空 intent 让 LLM router 决定。"""
            import re
            msg = state.user_message.strip()
            looks_like_selection = bool(
                re.search(r"^\s*[1-9]\s*$", msg)             # "1" / " 2 "
                or re.search(r"选\s*[1-9]", msg)              # "选 2" / "选2" — P1-B v1.5
                or re.search(r"\bid\s*[=:：]?\s*\d+", msg, re.IGNORECASE)  # "id=10"
                or msg in {"是", "确认", "好的", "OK", "ok", "yes"}
                or re.search(r"^第\s*[一二三四五六七八九]", msg)
            )

            # 1. 当前 (conv, user) 有 pending action？→ Intent.CONFIRM
            try:
                pendings = await self.confirm_gate.list_pending_for_context(
                    conversation_id=state.conversation_id, hub_user_id=state.hub_user_id,
                )
            except Exception:
                pendings = []
            # P2-C v1.4：识别 action_id 前缀（adj-/vch-/stk-/act-）也走 CONFIRM
            looks_like_action_id = bool(
                re.search(r"\b(adj|vch|stk|act|qte|cnt)-[0-9a-f]{8,}", state.user_message, re.IGNORECASE)
            ) or any(p.action_id and p.action_id in state.user_message for p in pendings)
            if pendings and (looks_like_selection or looks_like_action_id):
                state.intent = Intent.CONFIRM
                return state

            # 2. P1-A v1.4 + v1.6：candidate_* 已提升到 AgentState，跨轮自动 hydrate；
            # 候选来源用 **state.active_subgraph**（resolve_customer/products 写候选时一并写），
            # 不要用 state.intent — run() 每轮 reset intent=None，pre_router 阶段还看不到。
            if looks_like_selection and (state.candidate_customers or state.candidate_products):
                origin = state.active_subgraph
                if origin == "contract":
                    state.intent = Intent.CONTRACT
                    return state
                if origin == "quote":
                    state.intent = Intent.QUOTE
                    return state
                # active_subgraph 未设（旧 checkpoint / bug）— 仍兜底 contract，但记日志
                logger.warning(
                    "candidate_* 存在但 active_subgraph 未设，conv=%s user=%s 兜底 contract",
                    state.conversation_id, state.hub_user_id,
                )
                state.intent = Intent.CONTRACT
                return state

            # 3. 没命中 pre_router 条件 → 让 LLM router 决定
            state.intent = None  # 显式清空，下游 _route_after_pre_router 路由到 router
            return state

        def _route_after_pre_router(state: AgentState) -> str:
            """pre_router 命中 → 跳过 LLM router 直接进对应分发；否则进 LLM router。"""
            if state.intent is None:
                return "router"
            return "after_router_dispatch"  # 与 router 出口共用同一个分发逻辑

        def _route_after_router(state: AgentState) -> str:
            i = state.intent
            return {
                Intent.CHAT: "chat",
                Intent.QUERY: "query",
                Intent.CONTRACT: "contract",
                Intent.QUOTE: "quote",
                Intent.VOUCHER: "voucher",
                Intent.ADJUST_PRICE: "adjust_price",
                Intent.ADJUST_STOCK: "adjust_stock",
                Intent.CONFIRM: "confirm",
                Intent.UNKNOWN: "chat",  # fallback chat 子图请求澄清
            }.get(i, "chat")

        def _route_after_confirm(state: AgentState) -> str:
            """confirm_node 后：0/>1 pending 已写 final_response → END；
            1 个 pending claim 成功 → 按 state.confirmed_subgraph 路由到对应 commit。"""
            if not state.confirmed_subgraph:
                return "END"
            return f"commit_{state.confirmed_subgraph}"  # commit_adjust_price / commit_voucher / commit_adjust_stock

        # P1-B v1.2：所有节点必须是 async callable，不能用 sync lambda 返回 coroutine
        async def _router(s):
            return await router_node(s, llm=self.llm)
        async def _chat(s):
            return await chat_subgraph(s, llm=self.llm)
        async def _query(s):
            return await query_subgraph(
                s, llm=self.llm, registry=self.registry, tool_executor=self.tool_executor,
            )
        async def _confirm(s):
            return await confirm_node(s, gate=self.confirm_gate)

        g = StateGraph(AgentState)
        g.add_node("pre_router", _pre_router)  # P1-A v1.3
        g.add_node("router", _router)
        g.add_node("chat", _chat)
        g.add_node("query", _query)
        # 子图 (compiled) 本身已是 Runnable，可以直接 add_node
        g.add_node("contract", contract_sub)
        g.add_node("quote", quote_sub)
        g.add_node("voucher", voucher_sub)
        g.add_node("adjust_price", adjust_price_sub)
        g.add_node("adjust_stock", adjust_stock_sub)
        g.add_node("confirm", _confirm)
        # commit 子节点 — 在各写 subgraph 里实现并返回 async callable
        g.add_node("commit_adjust_price", adjust_price_sub.get_commit_node())
        g.add_node("commit_adjust_stock", adjust_stock_sub.get_commit_node())
        g.add_node("commit_voucher", voucher_sub.get_commit_node())

        # P1-A v1.3：先 pre_router，命中 context 直接分发；否则进 LLM router
        g.add_edge(START, "pre_router")
        g.add_conditional_edges("pre_router", _route_after_pre_router, {
            "router": "router",  # pre_router 没命中，让 LLM 判
            "after_router_dispatch": "after_router_dispatch",  # 命中，state.intent 已设
        })
        # after_router_dispatch 是个空节点，复用 _route_after_router 把 intent 派到子图
        async def _passthrough(s):
            return s
        g.add_node("after_router_dispatch", _passthrough)
        g.add_conditional_edges("after_router_dispatch", _route_after_router, {
            "chat": "chat", "query": "query", "contract": "contract",
            "quote": "quote", "voucher": "voucher",
            "adjust_price": "adjust_price", "adjust_stock": "adjust_stock",
            "confirm": "confirm",
        })
        g.add_conditional_edges("router", _route_after_router, {
            "chat": "chat", "query": "query", "contract": "contract",
            "quote": "quote", "voucher": "voucher",
            "adjust_price": "adjust_price", "adjust_stock": "adjust_stock",
            "confirm": "confirm",
        })
        g.add_conditional_edges("confirm", _route_after_confirm, {
            "END": END,
            "commit_adjust_price": "commit_adjust_price",
            "commit_adjust_stock": "commit_adjust_stock",
            "commit_voucher": "commit_voucher",
        })
        for terminal in ("chat", "query", "contract", "quote", "voucher",
                          "adjust_price", "adjust_stock",
                          "commit_adjust_price", "commit_adjust_stock", "commit_voucher"):
            g.add_edge(terminal, END)

        # checkpointer：MemorySaver 是进程内；生产可换 RedisSaver
        # spec §2.1：thread_id 必须是 (conv, user) 复合 key — 由 build_langgraph_config 注入
        return g.compile(checkpointer=MemorySaver())

    async def run(self, *, user_message: str, hub_user_id: int,
                  conversation_id: str, acting_as: int | None = None,
                  channel_userid: str | None = None) -> AgentResult:
        config = build_langgraph_config(
            conversation_id=conversation_id, hub_user_id=hub_user_id,
        )
        # P1-A v1.5：**只**传本轮新输入字段 — 不能 model_dump 整个 AgentState 默认值，
        # 否则 candidate_customers=[] / candidate_products={} / items=[] / missing_fields=[]
        # 等空默认值会覆盖 checkpoint 里的旧值（LangGraph 默认 reducer 是 overwrite/merge），
        # pre_router 永远 peek 不到候选。
        # 同时清掉上轮的 final_response / errors / confirmed_* / intent —— 这些是"上轮输出"
        # 不应让本轮看见。
        update_payload = {
            "user_message": user_message,
            "hub_user_id": hub_user_id,
            "conversation_id": conversation_id,
            "acting_as": acting_as,
            "channel_userid": channel_userid,
            # 显式 reset 上轮输出字段，避免污染本轮判定
            "intent": None,
            "final_response": None,
            "file_sent": False,
            "errors": [],
            "confirmed_subgraph": None,
            "confirmed_action_id": None,
            "confirmed_payload": None,
        }
        # **不**写：candidate_customers / candidate_products / customer / products / items /
        # missing_fields / extracted_hints / shipping —— LangGraph checkpoint hydrate 它们
        out = await self.compiled_graph.ainvoke(update_payload, config=config)
        return AgentResult(
            text=out.get("final_response"),
            error="; ".join(out.get("errors", [])) or None,
            kind="file" if out.get("file_sent") else "text",
        )
```

**关键 LangGraph 语义注意**：默认 StateGraph schema 字段的 reducer 是"覆盖"（除非用 `Annotated[list, operator.add]` 改成累加）。所以 ainvoke 只传 partial dict，未传字段会从 checkpoint 保留；**显式传** `[] / {}` 会覆盖。这正是 v1.5 P1-A 的关键 — 上面 `update_payload` 严格只列要 reset 的字段。

- [ ] **Step 3: 跑测试 + Commit**

---

### Task 7.2: dingtalk_inbound 切换 ChainAgent → GraphAgent

**Files:**
- Modify: `backend/hub/handlers/dingtalk_inbound.py`

- [ ] **Step 1: 找现有 ChainAgent 注入位置**

```bash
grep -n "ChainAgent" /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend/hub/handlers/dingtalk_inbound.py
```

- [ ] **Step 2: 切换 import + 构造**

```python
# from hub.agent.chain_agent import ChainAgent  # ← 删
from hub.agent.graph.agent import GraphAgent  # ← 加
# 注入处把 ChainAgent(...) 改成 GraphAgent(...)；其他 .run() 签名不变
```

- [ ] **Step 3: 跑现有 dingtalk_inbound 单测确保兼容**

```bash
pytest tests/test_dingtalk_inbound*.py -v
```

⚠️ 这一步可能暴露 ChainAgent 调用方很多细节差异（TaskRunner / ChannelMessageRouter 等）。subagent 实施时可能要做适配 / 或临时保留两套并 feature flag。

- [ ] **Step 4: Commit**

---

### Task 7.3: 删除旧代码

**Files:**
- Delete: `backend/hub/agent/chain_agent.py`
- Delete: `backend/hub/agent/context_builder.py`
- Delete: `backend/tests/test_chain_agent.py`（如果完全废弃）
- Modify: `backend/hub/agent/prompt/builder.py`（删 12 条行为准则 3a-3l）

- [ ] **Step 1: 确认无引用**

```bash
grep -rn "from hub.agent.chain_agent" /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend/
grep -rn "from hub.agent.context_builder" /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend/
```

Expected: 没有引用（除了 `__init__.py` 导出，可以一并删）。

- [ ] **Step 2: 删文件 + 改 builder.py**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent rm backend/hub/agent/chain_agent.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent rm backend/hub/agent/context_builder.py
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent rm backend/tests/test_chain_agent.py
```

改 `prompt/builder.py`：保留业务词典 / 同义词 helper（如 `get_business_dict()` / `expand_synonyms()`），删所有"3a-3l"行为准则 / system_prompt 拼接（这部分逻辑都搬到 subgraph_prompts/ 了）。

- [ ] **Step 3: 跑全量测试不 regress**

```bash
pytest tests/ -x -q --ignore=tests/integration
```

- [ ] **Step 4: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
refactor(hub): 删 ChainAgent / context_builder / 12 条行为准则（Plan 6 v9 Task 7.3）

GraphAgent 完全替代。prompt/builder.py 留业务词典 helper，
原 system_prompt 拼接逻辑全部迁移到 graph/subgraph_prompts/。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7.4: 旧测试迁移 / 弃用

**Files:**
- Modify: `backend/tests/test_*.py` 里依赖 ChainAgent 的部分

- [ ] **Step 1: 列出所有 ChainAgent 相关测试**

```bash
grep -l "ChainAgent\|chain_agent" /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend/tests/*.py
```

- [ ] **Step 2: 评估迁移 / 删除**

每个文件：
- 如果是测 prompt 准则 (3a-3l) → 删（行为搬代码）
- 如果是测 ChainAgent.run() 接口 → 改成测 GraphAgent.run()
- 如果是 e2e 实景测 → 改用真 GraphAgent + e2e fixture

- [ ] **Step 3: Commit**

---

### Task 7.5: Phase 7 集成验证

- [ ] **Step 1: 跑全量测试**

```bash
pytest tests/ -x -q
```

Expected: 全 PASS（含 Phase 0-7 新增）。

- [ ] **Step 2: 真启动一次 dingtalk_inbound 看 GraphAgent 能跑通**

如果有 staging 环境，部署到 staging 跑一个真 webhook。

- [ ] **Step 3: 标记 M7 完成**

---

## Phase 8：6 故事 acceptance + 真 LLM eval + cache 命中率（M8，1 天）

**Goal**：6 故事全跑通真 LLM、30 case eval、cache 命中率统计、per-user 隔离 / 多 pending 全套验收。

**Exit criteria**（spec §13）：
1. 6 故事每个 1-2 round 完成
2. Router 准确率 ≥ 95%
3. **Cache 命中率 ≥ 80%**（月平均）
4. 真 LLM eval 30 case 满意度 ≥ 80%
5. 单测覆盖 ≥ 95% PASS
6. p50 < 5s, p99 < 15s

### Task 8.1: 6 故事真 LLM 端到端测试

**Files:**
- Modify: `backend/tests/agent/test_acceptance_scenarios.py`

加一个统一 driver：

```python
@pytest.mark.realllm
@pytest.mark.parametrize("scenario_yaml", [
    "story1_chat.yaml", "story2_query.yaml", "story3_contract_oneround.yaml",
    "story4_query_then_contract.yaml", "story5_quote.yaml",
    "story6_adjust_price_confirm.yaml", "story6b_cross_conversation_isolation.yaml",
    "multi_pending.yaml",
])
@pytest.mark.asyncio
async def test_acceptance_scenario(scenario_yaml):
    scenario = yaml.safe_load((SCENARIOS_DIR / scenario_yaml).read_text(encoding="utf-8"))
    agent = await build_real_graph_agent()  # subagent 实施时写 helper
    try:
        for turn in scenario["turns"]:
            res = await agent.run(
                user_message=turn["input"],
                hub_user_id=turn.get("hub_user_id", 1),
                conversation_id=turn.get("conversation_id", "test-conv"),
            )
            # 断言 expected_intent / tool_caps / must_contain / forbid / sent_files_min /
            # items_count / creates_pending_action / pending_state（见 SUPPORTED_TURN_FIELDS）
            assert_scenario_turn(res, turn)
    finally:
        await agent.aclose()
```

- [ ] **Step 1-2: 写 driver + 跑全 6 故事 + Commit**

---

### Task 8.2: 30 case eval（**机检断言主导**，人工分落产物文件）

**Files:**
- Create: `backend/tests/agent/fixtures/eval_30_cases.yaml`
- Create: `backend/tests/agent/test_realllm_eval.py`
- Create: `docs/superpowers/plans/notes/2026-05-02-eval-results.md`（产物，**必须**落盘签字）

**Spec ref:** §13 真 LLM eval / plan v1.1 P2-I

**关键反模式**（plan v1.1 修正）：v1 写"人工打分或半自动"+ 24/30 PASS 当 release gate — 这导致 pytest 容易只跑流程不真正判定满意度。**v1.1 改成机检主导**：

- 每个 case 在 yaml 里**必须**写**机检断言**（intent / tool_caps 上限 / 必须含字段 / 禁止词），机检不通过 → 直接 FAIL（不是"低分"）
- 主观维度（语气自然 / 友好度）保留**人工打分 1-5**，但**必须**落 `notes/2026-05-02-eval-results.md` 文件（含每个 case 评分 + reviewer 签名 + 时间戳）。pytest 单纯输出到 stdout 不算
- release gate：**机检 ≥ 28/30 PASS**（≥93%）+ **人工平均分 ≥ 4.0/5**（24/30 ≥ 80%）

- [ ] **Step 1: 写 30 case eval YAML（v1.2 P2-F：完整列出 30 case，不留"续 26 case"）**

```yaml
# backend/tests/agent/fixtures/eval_30_cases.yaml
# 30 个 gold-set case，每 case 必须含机检字段：
# - id / turns(input/expected_intent/tool_caps/must_contain/forbid) / rubric
# tool_caps 双语义（v1.3 P2-D）：
#   - int N         → exact，count 必须正好 == N（写"必须调 1 次"用 1，"绝不调"用 0）
#   - {min:A,max:B} → range，A <= count <= B（如 search_products: {min:1, max:2}）
# 分布：chat 4 / query 6 / contract 6 / quote 3 / voucher 3 / adjust_price 3 /
#       adjust_stock 2 / cross-round 1 / boundary 1 + isolation 1 = 30 总计
# 每个 intent ≥ 3 case，跨轮 / 边界各 ≥ 1

# ===== chat (4) =====
- id: chat-01
  turns:
    - input: "你好"
      expected_intent: chat
      tool_caps: {}
      forbid: ["请问您要", "查询什么"]
  rubric: ["natural_tone", "brevity"]

- id: chat-02
  turns:
    - input: "最近怎样"
      expected_intent: chat
      tool_caps: {}
      forbid: ["业务", "订单"]
  rubric: ["natural_tone"]

- id: chat-03
  turns:
    - input: "辛苦了"
      expected_intent: chat
      tool_caps: {}
  rubric: ["natural_tone"]

- id: chat-04-edge-emoji
  turns:
    - input: "👍"
      expected_intent: chat
      tool_caps: {}
  rubric: ["natural_tone"]

# ===== query (6) =====
- id: query-01-inventory
  turns:
    - input: "查 SKG 有哪些产品有库存"
      expected_intent: query
      tool_caps: {check_inventory: 1, generate_contract_draft: 0, adjust_price_request: 0}
      must_contain: ["|"]
      forbid: ["是否需要做合同"]
  rubric: ["table_quality"]

- id: query-02-orders
  turns:
    - input: "看看阿里上个月订单"
      expected_intent: query
      tool_caps: {search_orders: 1, generate_contract_draft: 0}
      must_contain: ["|"]
  rubric: ["table_quality"]

- id: query-03-balance
  turns:
    - input: "阿里现在欠多少"
      expected_intent: query
      tool_caps: {get_customer_balance: 1, search_customers: 1}
  rubric: ["brevity"]

- id: query-04-product-detail
  turns:
    - input: "X1 现在多少钱"
      expected_intent: query
      tool_caps: {search_products: 1, get_product_detail: 1}
  rubric: ["brevity"]

- id: query-05-aging
  turns:
    - input: "看下哪些库存超 6 个月没动"
      expected_intent: query
      tool_caps: {get_inventory_aging: 1, analyze_slow_moving_products: 1}
      must_contain: ["|"]
  rubric: ["table_quality"]

- id: query-06-top-customers
  turns:
    - input: "这个季度前 10 客户"
      expected_intent: query
      tool_caps: {analyze_top_customers: 1}
      must_contain: ["|"]
  rubric: ["table_quality"]

# ===== contract (6) =====
- id: contract-01-full-info
  turns:
    - input: "给阿里做合同 X1 10 个 300，地址北京海淀，张三 13800000000"
      expected_intent: contract
      tool_caps:
        search_customers: 1
        search_products: 1
        generate_contract_draft: 1
        check_inventory: 0
      must_contain: ["合同已生成"]
      sent_files_min: 1
  rubric: ["receipt_clarity"]

- id: contract-02-multi-items
  turns:
    - input: "给百度做合同 H5 10 个 500，K5 20 个 300，地址上海，李四 13900001111"
      expected_intent: contract
      tool_caps:
        search_customers: 1
        search_products: {min: 1, max: 2}  # 合并搜或分别搜都可
        generate_contract_draft: 1
        check_inventory: 0
      sent_files_min: 1
      items_count: 2
  rubric: ["receipt_clarity"]

- id: contract-03-missing-address
  turns:
    - input: "给阿里做合同 X1 10 个 300"
      expected_intent: contract
      tool_caps: {search_customers: 1, search_products: 1, generate_contract_draft: 0}
      must_contain: ["地址"]  # ask_user 提示
      sent_files_min: 0
  rubric: ["ask_clarity"]

- id: contract-04-cross-round-query-then-contract
  turns:
    - input: "查 SKG 库存"
      expected_intent: query
      tool_caps: {check_inventory: 1}
    - input: "给翼蓝做合同 H5 10 个 300，F1 10 个 500，K5 20 个 300，地址广州市天河区华穗路406号中景B座，林生，13692977880"
      expected_intent: contract
      tool_caps:
        check_inventory: 0   # 第二轮物理不挂
        search_customers: 1
        generate_contract_draft: 1
      sent_files_min: 1
      items_count: 3
  rubric: ["no_redundant_query", "natural_tone"]

- id: contract-05-multi-customer-ambiguity
  # P2-E v1.4：第一轮信息齐（含地址/联系人/电话），只测客户多命中歧义；
  # 第二轮"选 1"消费候选 + 直接生成合同（不再缺其他字段）
  turns:
    - input: "给阿里做合同 X1 10 个 300，地址北京海淀，张三 13800001111"
      expected_intent: contract
      tool_caps: {search_customers: 1, generate_contract_draft: 0}  # 客户歧义未消，不生成
      must_contain: ["1)", "2)", "id="]  # 多候选客户列编号
    - input: "1"  # 用户选第 1 个
      expected_intent: contract
      tool_caps: {generate_contract_draft: 1, search_customers: 0}  # 直接消费候选，不再搜
      sent_files_min: 1
      items_count: 1
  rubric: ["candidate_selection_clarity"]

- id: contract-06-missing-price
  turns:
    - input: "给阿里做合同 X1 10 个"
      expected_intent: contract
      tool_caps: {generate_contract_draft: 0}
      must_contain: ["单价"]  # 缺价格不默认
  rubric: ["ask_clarity"]

# ===== quote (3) =====
- id: quote-01
  turns:
    - input: "给阿里报 X1 50 个的价"
      expected_intent: quote
      tool_caps: {search_customers: 1, search_products: 1, generate_price_quote: 1}
      must_contain: ["报价单已生成"]
      sent_files_min: 1
  rubric: ["receipt_clarity"]

- id: quote-02-list-price
  turns:
    - input: "Y3 100 个给百度报价"
      expected_intent: quote
      tool_caps: {generate_price_quote: 1}
      sent_files_min: 1
  rubric: ["receipt_clarity"]

- id: quote-03-missing-qty
  turns:
    - input: "给阿里报 X1 的价"
      expected_intent: quote
      tool_caps: {generate_price_quote: 0}
      must_contain: ["数量"]
  rubric: ["ask_clarity"]

# ===== voucher (3) =====
- id: voucher-01
  turns:
    - input: "出库 SO-202404-0001"
      expected_intent: voucher
      tool_caps: {search_orders: 1, get_order_detail: 1, create_voucher_draft: 0}
      must_contain: ["确认"]
    - input: "确认"
      expected_intent: confirm
      tool_caps: {create_voucher_draft: 1}
      must_contain: ["凭证已提交"]
  rubric: ["receipt_clarity"]

- id: voucher-02-already-exists
  turns:
    - input: "出库 SO-EXISTING"  # 假定已有凭证的订单
      expected_intent: voucher
      tool_caps: {get_order_detail: 1, create_voucher_draft: 0}
      must_contain: ["已有凭证"]
  rubric: ["error_clarity"]

- id: voucher-03-not-approved
  turns:
    - input: "出库 SO-DRAFT"  # 假定未审批订单
      expected_intent: voucher
      tool_caps: {create_voucher_draft: 0}
      must_contain: ["未审批"]
  rubric: ["error_clarity"]

# ===== adjust_price (3) =====
- id: adjust-price-01
  turns:
    - input: "把阿里的 X1 价格调到 280"
      expected_intent: adjust_price
      tool_caps:
        search_customers: 1
        search_products: 1
        get_product_customer_prices: 1
        adjust_price_request: 0  # 必须 preview 不直接落
      must_contain: ["调价预览", "确认"]
    - input: "确认"
      expected_intent: confirm
      tool_caps: {adjust_price_request: 1}
      must_contain: ["调价已申请"]
  rubric: ["preview_clarity"]

- id: adjust-price-02-cross-conv-isolation
  turns:
    - input: "把百度的 Y2 价格调到 350"
      conversation_id: c1-private
      expected_intent: adjust_price
      tool_caps: {adjust_price_request: 0}
    - input: "确认"
      conversation_id: c2-group  # 切到群聊
      expected_intent: confirm
      tool_caps: {adjust_price_request: 0}  # 跨会话不可见
      must_contain: ["没有待办"]
  rubric: ["isolation_clarity"]

- id: adjust-price-03-multi-pending-select
  turns:
    - input: "把阿里的 X1 价格调到 280"
      expected_intent: adjust_price
    - input: "把百度的 Y2 价格调到 350"
      expected_intent: adjust_price
    - input: "确认"
      expected_intent: confirm
      tool_caps: {adjust_price_request: 0}  # 多 pending 不自动 claim
      must_contain: ["1)", "2)"]
    - input: "1"
      expected_intent: confirm
      tool_caps: {adjust_price_request: 1}
  rubric: ["multi_pending_clarity"]

# ===== adjust_stock (2) =====
- id: adjust-stock-01
  turns:
    - input: "X1 库存调到 100"
      expected_intent: adjust_stock
      tool_caps: {adjust_stock_request: 0}
      must_contain: ["确认"]
    - input: "确认"
      expected_intent: confirm
      tool_caps: {adjust_stock_request: 1}
  rubric: ["preview_clarity"]

- id: adjust-stock-02-idempotent
  turns:
    - input: "X1 库存 +50"
      expected_intent: adjust_stock
    - input: "X1 库存 +50"  # 同输入再来一次
      expected_intent: adjust_stock
      # 幂等命中：复用上一个 pending（同 action_id），不创建新的
  rubric: ["idempotency"]

# ===== cross-round + boundary (2) =====
- id: cross-01-query-then-quote
  turns:
    - input: "查 X1 价格"
      expected_intent: query
      tool_caps: {get_product_detail: 1}
    - input: "给阿里报 X1 50 个的价"
      expected_intent: quote
      tool_caps: {generate_price_quote: 1}
      sent_files_min: 1
  rubric: ["no_redundant_query"]

- id: boundary-01-empty-then-greeting
  turns:
    - input: "。。。"
      expected_intent: unknown
      tool_caps: {}
      forbid: ["合同", "报价"]
    - input: "你好"
      expected_intent: chat
      tool_caps: {}
  rubric: ["graceful_unknown"]

# ===== isolation (1) — P2-E v1.3 补第 30 条 =====
- id: isolation-01-group-per-user-context
  # 同群聊里 user A 起合同到一半（candidate 客户待选），user B 在同群里起另一个查询
  # 必须：A 的 candidate_customers / 业务上下文不被 B 污染；B 的查询不影响 A
  turns:
    - input: "给阿里做合同 X1 10 个 300"
      conversation_id: group-1
      hub_user_id: 1
      expected_intent: contract
      tool_caps:
        search_customers: 1
        generate_contract_draft: 0   # 多客户候选还没选，不能生成
      must_contain: ["1)", "2)", "id="]   # 候选列编号
    - input: "查 SKG 库存"
      conversation_id: group-1
      hub_user_id: 2     # 同群另一个 user
      expected_intent: query
      tool_caps:
        check_inventory: 1
        search_customers: 0   # 不应碰 user 1 的合同流程
        generate_contract_draft: 0
    - input: "1"        # user 1 回到合同流程，选第 1 个候选客户
      conversation_id: group-1
      hub_user_id: 1
      expected_intent: contract       # pre_router 据 user 1 的 candidate_customers 沿用 contract
      tool_caps:
        generate_contract_draft: 0   # 还缺地址
      must_contain: ["地址"]
  rubric: ["per_user_isolation", "candidate_persistence"]
```

⚠️ 这个 30 case 是 **gold set**：必须在 Step 2 driver 之前完成；Step 2 driver 要先断言 `len(cases) == 30` + 每个 intent ≥ N。

- [ ] **Step 2: 写 eval driver — 含 schema 断言（先验 fixture 完整性，再跑机检）**

```python
# backend/tests/agent/test_realllm_eval.py
"""30 case 真 LLM eval — 机检断言主导，人工分落产物文件。"""
import os
import yaml
import json
import pytest
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from hub.agent.graph.state import Intent
from hub.agent.tools.confirm_gate import ConfirmGate

CASES_PATH = Path(__file__).parent / "fixtures" / "eval_30_cases.yaml"
RESULTS_PATH = (Path(__file__).parent.parent.parent.parent
                / "docs/superpowers/plans/notes/2026-05-02-eval-results.md")

pytestmark = [
    pytest.mark.realllm,
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="需要真 API key"),
]


# P2-F v1.2：fixture schema 断言独立测试 — 不需要真 LLM，跑 unit test 阶段先卡住
# 这个测试不带 realllm mark，所以跑 `pytest tests/agent/test_realllm_eval.py::test_fixture_schema -v` 就能跑
def test_fixture_schema_30_cases_with_intent_distribution():
    """先卡住 fixture：30 个 case，每个 intent 分布达标，机检字段齐全。"""
    import yaml as _y
    cases = _y.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    assert isinstance(cases, list) and len(cases) == 30, f"需要恰好 30 case，当前 {len(cases)}"

    # 每个 case 必填字段
    for c in cases:
        assert "id" in c and "turns" in c
        for turn in c["turns"]:
            assert "input" in turn and "expected_intent" in turn
            assert "tool_caps" in turn  # 显式空 {} 也算

    # intent 分布最低线
    intents = [turn["expected_intent"] for c in cases for turn in c["turns"]]
    from collections import Counter
    counts = Counter(intents)
    assert counts["chat"] >= 4
    assert counts["query"] >= 6
    assert counts["contract"] >= 5
    assert counts["quote"] >= 3
    assert counts["voucher"] >= 3
    assert counts["adjust_price"] >= 3
    assert counts["adjust_stock"] >= 2

    # case id 唯一
    ids = [c["id"] for c in cases]
    assert len(set(ids)) == len(ids), f"case id 重复：{ids}"

    # P2-D v1.4：所有 yaml 里出现的机检字段都必须被 driver 校验
    SUPPORTED_TURN_FIELDS = {
        "input", "conversation_id", "hub_user_id",
        "expected_intent", "tool_caps", "must_contain", "forbid",
        "sent_files_min", "items_count", "creates_pending_action", "pending_state",
    }
    for c in cases:
        for turn in c["turns"]:
            unknown = set(turn.keys()) - SUPPORTED_TURN_FIELDS
            assert not unknown, (
                f"case {c['id']} turn 含未知字段 {unknown}; "
                f"必须先在 driver 加机检逻辑再加 yaml 字段"
            )


# v1.14 P2 / v1.15 P3 / v1.16 P2：直接引用本模块顶部已定义的真 helper —
# 不**复制**逻辑（v1.14 旧版本错误），也**不** import 自己（v1.15 错误的 hub.agent.tests... 路径
# 不存在 → ModuleNotFoundError）。
# FILE_GENERATING_TOOLS / _is_successful_tool_call 在本文件顶部模块级定义，
# 同模块内直接可见，不需要 import 语句。


def _make_tool_record(*, name, success=True, result_payload=None, error_msg=None):
    """构造测试用 tool_logger record。默认成功；显式传 success=False 或 error_msg 才算失败。"""
    if not success:
        return {
            "name": name, "args": {},
            "result": None, "error": error_msg or "Mock failure",
            "duration_ms": 5.0,
        }
    return {
        "name": name, "args": {},
        "result": result_payload or {"ok": True},
        "error": None, "duration_ms": 5.0,
    }


def test_is_successful_tool_call_helper():
    """直接测真 helper（不复制逻辑）。"""
    assert _is_successful_tool_call(_make_tool_record(name="x", success=True)) is True
    assert _is_successful_tool_call(_make_tool_record(name="x", success=False)) is False
    # error=None 但 result=None — 不算成功
    assert _is_successful_tool_call({
        "name": "x", "args": {}, "result": None, "error": None,
    }) is False


def test_file_generating_tools_set_matches_implementation():
    """v1.15 P3 防回退：FILE_GENERATING_TOOLS 必须含三个生成 tool。"""
    assert "generate_contract_draft" in FILE_GENERATING_TOOLS
    assert "generate_price_quote" in FILE_GENERATING_TOOLS
    assert "create_voucher_draft" in FILE_GENERATING_TOOLS
    # 不能含 search_*、get_*、check_*、analyze_* 之类（query 用）
    for n in ("search_customers", "search_products", "check_inventory", "get_customer_history"):
        assert n not in FILE_GENERATING_TOOLS


def test_sent_files_excludes_failed_generation_calls():
    """v1.14 P2 / v1.15 P3：sent_files 过滤逻辑用真 helper 测。"""
    records = [
        _make_tool_record(name="generate_contract_draft", success=False, error_msg="PermissionError"),
        _make_tool_record(name="generate_price_quote", result_payload={"quote_id": 888}),
        # error=None 但 result=None — 也不算
        {"name": "create_voucher_draft", "args": {}, "result": None, "error": None, "duration_ms": 5.0},
    ]
    sent_files = [
        r["name"] for r in records
        if r["name"] in FILE_GENERATING_TOOLS and _is_successful_tool_call(r)
    ]
    assert sent_files == ["generate_price_quote"]


# v1.15 P2：items_count 同样必须过滤失败调用
def test_items_count_excludes_failed_generation_calls():
    """v1.15 P2：items 取自最后一次**成功的** generate_*_draft 调用 args；
    失败调用即使有 args["items"] 也不能让 items_count 通过。"""
    records = [
        # 第一次成功（items=[H5,F1]），第二次失败（args 含错误 items=[X1,X2,X3,X4]）
        _make_tool_record(
            name="generate_contract_draft",
            result_payload={"draft_id": 1},
        ) | {"args": {"items": [{"product_id": 1, "qty": 10, "price": 300},
                                 {"product_id": 2, "qty": 5, "price": 500}]}},
        _make_tool_record(
            name="generate_contract_draft", success=False, error_msg="DB error",
        ) | {"args": {"items": [{"product_id": 9, "qty": 1, "price": 1},
                                 {"product_id": 10, "qty": 2, "price": 2},
                                 {"product_id": 11, "qty": 3, "price": 3},
                                 {"product_id": 12, "qty": 4, "price": 4}]}},
    ]
    # 复用 driver 里的逻辑（reverse + 成功过滤）
    items_from_generate = []
    for r in reversed(records):
        if r["name"] in FILE_GENERATING_TOOLS and _is_successful_tool_call(r):
            items_from_generate = r["args"].get("items") or []
            break
    # 只取到第一次成功的 2 items，不取失败的 4 items
    assert len(items_from_generate) == 2
    assert items_from_generate[0]["product_id"] == 1


# P2-F v1.5：eval driver 用的富结果对象 — GraphAgent.run 的 AgentResult 只有 text/error/kind，
# 不够 driver 验 sent_files_min / items_count / creates_pending_action / pending_state。
# _run_turn_with_metrics 必须返回 EvalTurnResult，从 LangGraph state snapshot + tool logger 填。
#
# ⚠️ ToolLogger 接口契约（v1.14 P2）— 实施时必须保证：
# `tool_logger.records: list[dict]` 每条记录 schema 固定为：
#   {
#     "name": str,            # tool 名（generate_contract_draft / search_customers / ...）
#     "args": dict,           # 调用参数（已脱敏）
#     "result": dict | None,  # 成功时是 tool 返回值；失败时为 None
#     "error": str | None,    # 异常 repr；成功时为 None
#     "duration_ms": float,
#     "called_at": datetime,
#   }
# ToolLogger 用 try/finally 模式记录 — 失败也写一条。driver 的 sent_files / items_count
# 等机检判定**必须**用 `error is None and result is not None` 过滤成功调用。
# subagent 实施 ToolLogger 时若现有实现不含 error/result 字段，必须扩展到这个契约。

# v1.15 P3：模块级常量 + helper（替代之前嵌套在 _run_turn_with_metrics 里的版本）。
# 让单测能直接 `from ... import _is_successful_tool_call, FILE_GENERATING_TOOLS` 测真实实现，
# 而不是在测试里复制逻辑。
FILE_GENERATING_TOOLS = frozenset({
    "generate_contract_draft",
    "generate_price_quote",
    "create_voucher_draft",
})


def _is_successful_tool_call(record: dict) -> bool:
    """判定 tool 调用成功的统一规则（与 sent_files / items_count / pending_state 都共用）。

    成功 = error is None **且** result 非空（result 为 None 视为没有真实业务输出，不算）。
    ToolLogger.records 字段格式见上面契约说明。
    """
    return record.get("error") is None and record.get("result") is not None


@dataclass
class EvalTurnResult:
    text: str | None
    intent: Intent | None              # checkpoint snapshot.values["intent"]
    sent_files: list[str]              # v1.13 P2：本轮 generate_*_draft / create_voucher_draft tool 调用名列表
                                          #         （每次成功调用 = 1 个 sent_file；不依赖 send_file tool 调用记录）
    items: list                        # v1.12 P1-A：本轮 generate_*_draft 调用的 args["items"]
                                          #         （不从 state.values["items"] 取 — cleanup 后是空）
    pending_action_id: str | None      # snapshot.values 或 ConfirmGate 当前 (conv,user) 的 latest pending
    error: str | None
    kind: str                          # "text" / "file" / "error"


# P2-A v1.6：eval YAML 里 pending_state: {action_id: "claimed" / "still_pending" / "expired"} 的
# 实际状态查询 — 必须在 driver 里实现完整，不能让 release gate 等手工补洞
async def _check_pending_state(gate: ConfirmGate, action_id: str) -> str:
    """查 ConfirmGate 里某 action_id 的状态。

    返回值（与 yaml 约定一致）：
      - "still_pending"：尚未消费且未过期
      - "claimed"：已被 claim 消费（token 用过）
      - "expired"：超过 ttl_seconds，自动失效
      - "missing"：从来不存在 / Redis key 早被清
    """
    pending = await gate.get_pending_by_id(action_id)  # 返 PendingAction | None
    if pending is None:
        # 区分 claimed vs missing：claim 后 ConfirmGate 应在另一 key 留 audit log
        if await gate.is_claimed(action_id):
            return "claimed"
        return "missing"
    if pending.is_expired():
        return "expired"
    return "still_pending"


async def _run_turn_with_metrics(
    turn: dict, *, case_id: str, agent, gate, tool_logger,
) -> tuple[EvalTurnResult, list[str]]:
    """跑一轮 + 收集 metrics。返回 (EvalTurnResult, tool_call_names)。

    实现：
      1. 重置 tool_logger（只统计本轮新增 tool 调用）
      2. 从 turn 取 conversation_id / hub_user_id；conversation_id 默认 f"eval-{case_id}"
         （同 case 多 turn 共享 case 级 conversation；不同 case 用不同 id 避免污染）
      3. await agent.run(...)
      4. 从 compiled_graph.aget_state(config) 取 snapshot.values
      5. 从 tool_logger 取本轮 tool 调用名列表 + 本轮 send_file 列表
      6. 从 ConfirmGate 取本轮 (conv, user) 的 pending action_id（如有）
      7. 组装 EvalTurnResult
    """
    from hub.agent.graph.config import build_langgraph_config
    # P1-B v1.6：默认 conversation_id 必须按 case_id 隔离 — 否则 30 case 共用同一 LangGraph
    # checkpoint，前 case 的 customer/products/pending 会污染后 case，eval 结果不可用
    default_conv = f"eval-{case_id}"
    conv = turn.get("conversation_id", default_conv)
    user = turn.get("hub_user_id", 1)
    pre_count = tool_logger.count
    res = await agent.run(
        user_message=turn["input"],
        hub_user_id=user, conversation_id=conv,
    )
    # 本轮 tool 调用
    new_tool_calls = tool_logger.records[pre_count:]
    tool_names = [r["name"] for r in new_tool_calls]

    # P2 v1.13：sent_files 不依赖 send_file tool；改成检查本轮"会发文件"的 tool 调用。
    # P2 v1.14 + v1.15：必须过滤**成功**调用（ToolLogger try/finally 模式，失败也写记录）。
    # P3 v1.15：FILE_GENERATING_TOOLS 和 _is_successful_tool_call 都提到模块级，
    # 让单测能直接 import 真实定义、不复制逻辑。
    sent_files = [
        r["name"] for r in new_tool_calls
        if r["name"] in FILE_GENERATING_TOOLS and _is_successful_tool_call(r)
    ]

    # P1-A v1.12 + P2 v1.15：items 从最后一次**成功的** generate_*_draft / generate_price_quote
    # 调用的 args["items"] 取。失败调用不算（args 可能是错的）。
    items_from_generate: list = []
    for r in reversed(new_tool_calls):
        if (r["name"] in FILE_GENERATING_TOOLS
                and _is_successful_tool_call(r)):
            items_from_generate = r["args"].get("items") or []
            break

    # state snapshot
    config = build_langgraph_config(conversation_id=conv, hub_user_id=user)
    snap = await agent.compiled_graph.aget_state(config)
    values = snap.values if snap else {}
    # ConfirmGate 当前 pending（取最新一个）
    pendings = await gate.list_pending_for_context(
        conversation_id=conv, hub_user_id=user,
    )
    pending_action_id = pendings[-1].action_id if pendings else None

    eval_res = EvalTurnResult(
        text=res.text, intent=values.get("intent"), sent_files=sent_files,
        items=items_from_generate,  # v1.12 P1-A：从 tool args 取，不被 cleanup 清空
        pending_action_id=pending_action_id,
        error=res.error, kind=res.kind,
    )
    return eval_res, tool_names


@pytest.mark.asyncio
async def test_eval_30_cases_mechanical_assertions(real_agent, real_gate, tool_logger):
    """机检主导：每 case 跑完比对 intent / tool_caps / must_contain / forbid /
    sent_files_min / items_count / creates_pending_action / pending_state。
    机检不通过 → FAIL（不是低分）。release gate ≥ 28/30 PASS。"""
    cases = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    assert len(cases) == 30, f"需要恰好 30 case，当前 {len(cases)}"  # P2-F：恰好 30，不是 ≥

    results: list[dict] = []
    for case in cases:
        case_result = {"id": case["id"], "turns": [], "passed": True, "failures": []}
        for turn in case["turns"]:
            res, tool_calls = await _run_turn_with_metrics(
                turn, case_id=case["id"],  # P1-B v1.6：同 case 多 turn 共享 conv，跨 case 隔离
                agent=real_agent, gate=real_gate, tool_logger=tool_logger,
            )
            # 机检 1: intent
            if turn.get("expected_intent") and res.intent.value != turn["expected_intent"]:
                case_result["passed"] = False
                case_result["failures"].append(
                    f"intent: 期望 {turn['expected_intent']}, 实际 {res.intent.value}")
            # 机检 2: tool_caps — P2-D v1.3 双语义
            #   int N        → exact: count must equal N
            #   {min:A, max:B} → range: A <= count <= B
            for tool, spec in (turn.get("tool_caps") or {}).items():
                count = sum(1 for tc in tool_calls if tc == tool)
                if isinstance(spec, int):
                    # exact
                    if count != spec:
                        case_result["passed"] = False
                        case_result["failures"].append(
                            f"tool {tool}: 期望 exact={spec}，实际 {count}")
                elif isinstance(spec, dict):
                    lo = spec.get("min", 0)
                    hi = spec.get("max", float("inf"))
                    if count < lo:
                        case_result["passed"] = False
                        case_result["failures"].append(
                            f"tool {tool}: 调 {count} 次低于 min={lo}")
                    if count > hi:
                        case_result["passed"] = False
                        case_result["failures"].append(
                            f"tool {tool}: 调 {count} 次超 max={hi}")
                else:
                    case_result["passed"] = False
                    case_result["failures"].append(
                        f"tool {tool}: tool_caps spec 必须是 int 或 {{min,max}} dict，实际 {type(spec).__name__}")
            # 机检 3: must_contain
            for needed in turn.get("must_contain", []):
                if needed not in (res.text or ""):
                    case_result["passed"] = False
                    case_result["failures"].append(f"必须含 '{needed}'，实际未找到")
            # 机检 4: forbid
            for forbidden in turn.get("forbid", []):
                if forbidden in (res.text or ""):
                    case_result["passed"] = False
                    case_result["failures"].append(f"禁止含 '{forbidden}'，实际出现")

            # 机检 5: sent_files_min — P2-D v1.4
            if "sent_files_min" in turn:
                actual = len(getattr(res, "sent_files", []) or [])
                if actual < turn["sent_files_min"]:
                    case_result["passed"] = False
                    case_result["failures"].append(
                        f"sent_files: {actual} < min {turn['sent_files_min']}")

            # 机检 6: items_count — exact 等于（合同 / 报价 items 数）
            if "items_count" in turn:
                actual = len(getattr(res, "items", []) or [])
                if actual != turn["items_count"]:
                    case_result["passed"] = False
                    case_result["failures"].append(
                        f"items_count: 期望 {turn['items_count']}，实际 {actual}")

            # 机检 7: creates_pending_action — bool（preview 必须 / 不准创建 pending）
            if "creates_pending_action" in turn:
                created = bool(getattr(res, "pending_action_id", None))
                expected = turn["creates_pending_action"]
                if created != expected:
                    case_result["passed"] = False
                    case_result["failures"].append(
                        f"creates_pending_action: 期望 {expected}，实际 {created}")

            # 机检 8: pending_state — dict 描述每个 action_id 期望的 ConfirmGate 状态
            #   {action_id: "claimed" | "still_pending" | "expired" | "missing"}
            # v1.7 P2-A：必须传 real_gate（_check_pending_state 签名是 (gate, action_id)）
            if "pending_state" in turn:
                for aid, expected_state in (turn["pending_state"] or {}).items():
                    actual_state = await _check_pending_state(real_gate, aid)
                    if actual_state != expected_state:
                        case_result["passed"] = False
                        case_result["failures"].append(
                            f"pending_state[{aid}]: 期望 {expected_state}，实际 {actual_state}")
        results.append(case_result)

    passed = sum(1 for r in results if r["passed"])
    # 机检 release gate
    pass_rate = passed / len(results)
    # 写产物文件供人工评分（即使机检通过也要落）
    _write_eval_results_markdown(results, mechanical_pass_rate=pass_rate)

    assert pass_rate >= 28 / 30, (
        f"机检 PASS={passed}/{len(results)} = {pass_rate:.2%} < 93% 阈值。"
        f"详见 {RESULTS_PATH}"
    )


def _write_eval_results_markdown(results: list[dict], *, mechanical_pass_rate: float):
    """落产物：每 case 机检结果 + 人工分模板（reviewer 填）。"""
    lines = [
        f"# Plan 6 v9 GraphAgent — 30 case eval 结果",
        f"",
        f"**跑测时间**：{datetime.utcnow().isoformat()}Z",
        f"**机检 PASS**：{sum(1 for r in results if r['passed'])}/{len(results)} = {mechanical_pass_rate:.2%}",
        f"**机检 release gate**：≥ 28/30（93%）— **{'PASS' if mechanical_pass_rate >= 28/30 else 'FAIL'}**",
        f"",
        f"## 机检结果（自动）",
        f"",
        f"| Case | 机检 | 失败原因 |",
        f"|---|---|---|",
    ]
    for r in results:
        flag = "✅" if r["passed"] else "❌"
        reason = "; ".join(r["failures"]) if r["failures"] else "—"
        lines.append(f"| {r['id']} | {flag} | {reason} |")
    lines.extend([
        f"",
        f"## 人工评分（reviewer 填，必填项）",
        f"",
        f"| Case | natural_tone (1-5) | brevity (1-5) | 备注 |",
        f"|---|---|---|---|",
    ])
    for r in results:
        lines.append(f"| {r['id']} | __ | __ | |")
    lines.extend([
        f"",
        f"**人工平均分**：__/5（reviewer 算完填）",
        f"**人工 release gate**：≥ 4.0/5",
        f"",
        f"**Reviewer**：__（签名）",
        f"**Reviewer 时间**：__（签字时刻）",
        f"",
        f"⚠️ 这份文件**必须**有 reviewer 签名才能视为 Plan 6 v9 release gate 通过。",
    ])
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")
```

- [ ] **Step 3: 跑测试**

```bash
DEEPSEEK_API_KEY=... pytest tests/agent/test_realllm_eval.py -v -m realllm -s
```

Expected：
- 机检 ≥ 28/30 PASS
- `docs/superpowers/plans/notes/2026-05-02-eval-results.md` 落盘
- reviewer 在该文件填人工分（4 维度），算平均 ≥ 4.0/5 才视为整体 PASS

---

### Task 8.3: Cache 命中率统计

**Files:**
- Create: `backend/tests/agent/test_cache_hit_rate.py`
- Modify: `backend/hub/models/conversation.py`（如果 ToolCallLog 加 `cache_hit_rate` 字段）

**Spec ref:** §1.1 / §9.3

- [ ] **Step 1: 模型加字段**

```python
# backend/hub/models/conversation.py
class ToolCallLog(Model):
    # ... existing fields
    cache_hit_rate = fields.FloatField(null=True, description="DeepSeek KV cache 命中率")
```

加 migration。

- [ ] **Step 2: llm_client 调用后写入**

```python
# 在 DeepSeekLLMClient.chat 返回后，调用方（节点 / 子图）写入 ToolCallLog
await ToolCallLog.create(
    conversation_id=..., hub_user_id=..., tool_name=...,
    cache_hit_rate=resp.cache_hit_rate,  # ← 新加
)
```

- [ ] **Step 3: 写测试 — 30 case 后查命中率**

```python
# backend/tests/agent/test_cache_hit_rate.py
@pytest.mark.asyncio
@pytest.mark.realllm
async def test_cache_hit_rate_above_80_percent_after_30_runs():
    """跑 30 个不同 case 后，月平均 cache 命中率 ≥ 80% — spec §1.1 / §13。"""
    agent = await build_real_graph_agent()
    cases = load_eval_30_cases()
    for c in cases:
        await agent.run(user_message=c["input"], hub_user_id=1, conversation_id=c["conv"])

    avg = await ToolCallLog.filter(
        called_at__gte=...
    ).aggregate(Avg("cache_hit_rate"))
    assert avg["cache_hit_rate__avg"] >= 0.80, \
        f"cache 命中率 {avg['cache_hit_rate__avg']:.2%} < 80%"
```

- [ ] **Step 4: 跑 + 调整（如果不达 80% 调 prompt 静态化）+ Commit**

---

### Task 8.4: per-user 隔离 + 多 pending 真 LLM 验收

**Files:**
- Modify: `backend/tests/agent/test_per_user_isolation.py`（加端到端版本）

跑同 conv 不同 user 的真 LLM session：

```python
@pytest.mark.realllm
@pytest.mark.asyncio
async def test_per_user_isolation_real_llm():
    agent = await build_real_graph_agent()
    conv = "group-realllm-test"
    # A 起合同到一半
    a1 = await agent.run(user_message="给阿里做合同", hub_user_id=1, conversation_id=conv)
    # B 在同一群里起百度合同
    b1 = await agent.run(user_message="给百度做合同", hub_user_id=2, conversation_id=conv)
    # A 续：X1 10 个 300（应继续阿里，不是百度）
    a2 = await agent.run(user_message="X1 10 个 300，地址北京", hub_user_id=1, conversation_id=conv)
    assert "阿里" in (a2.text or "")
    assert "百度" not in (a2.text or "")
```

- [ ] Commit

---

### Task 8.5: 性能 / 成本统计

跑 30 case 记录 p50 / p99 延迟、总 token / 成本估算。

- [ ] **Step 1: 写脚本**

```python
# scripts/benchmark_graph_agent.py
import asyncio
import time
from hub.agent.graph.agent import GraphAgent
# ...

async def main():
    agent = await build_real_graph_agent()
    cases = load_eval_30_cases()
    latencies = []
    total_input_tokens = 0
    total_output_tokens = 0
    cache_hits = 0
    cache_total = 0
    for c in cases:
        t0 = time.monotonic()
        res = await agent.run(...)
        latencies.append(time.monotonic() - t0)
        # 拉这次的 ToolCallLog 累计 token / cache
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[int(len(latencies) * 0.99)]
    print(f"p50={p50:.2f}s p99={p99:.2f}s")
    print(f"input tokens: {total_input_tokens} / output: {total_output_tokens}")
    cache_rate = cache_hits / max(cache_total, 1)
    # cost = miss_tokens * 1 + hit_tokens * 0.02 (元 / 1M tokens)
    cost = ((cache_total - cache_hits) * 1 + cache_hits * 0.02) / 1_000_000
    print(f"cache_hit_rate={cache_rate:.2%} estimated_cost={cost:.4f} 元")
```

- [ ] **Step 2: 跑 + 写报告**

```bash
python scripts/benchmark_graph_agent.py > docs/superpowers/plans/notes/2026-05-02-benchmark.md
```

- [ ] **Step 3: 检查 spec §13 全部指标**

| 指标 | 目标 | 实际 |
|---|---|---|
| Router 准确率 | ≥ 95% | __ |
| Cache 命中率 | ≥ 80% | __ |
| p50 延迟 | < 5s | __ |
| p99 延迟 | < 15s | __ |
| 月成本（外推）| ≤ ¥1K | __ |
| 6 故事完成率 | 100% | __ |
| 单测覆盖 | ≥ 95% | __ |

- [ ] **Step 4: Commit**

```bash
git -C /Users/lin/Desktop/hub/.worktrees/plan6-agent commit -m "$(cat <<'EOF'
chore(hub): Plan 6 v9 GraphAgent 8 天重构完成 — M8 全指标达标

附件 docs/superpowers/plans/notes/2026-05-02-benchmark.md。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8.6: 最终 staging 验收 / 上线 checklist

- [ ] **Step 1: 在 staging 环境部署**
- [ ] **Step 2: 真钉钉群跑 6 故事录屏**
- [ ] **Step 3: 至少观察 1 周不出"反复确认 / 调多余 tool / 群聊串状态"等流程类 bug**
- [ ] **Step 4: 通过后 merge feature/plan6-agent → main**

---

## 验收 Cheat Sheet（M8 必须全过）

```bash
# 全量单测
pytest tests/ -q

# 真 LLM 验收（需 DEEPSEEK_API_KEY）
DEEPSEEK_API_KEY=... pytest tests/integration tests/agent -v -m realllm

# Phase-by-phase 验证（执行时按顺序）
pytest tests/agent/test_graph_state.py -v                       # M0
pytest tests/agent/test_per_user_isolation.py -v                # M0
pytest tests/integration/test_deepseek_compat.py -v -m realllm  # M0
pytest tests/agent/test_graph_router_accuracy.py -v -m realllm  # M1 (≥95%)
pytest tests/agent/test_subgraph_*.py -v                        # M3-M6
pytest tests/agent/test_node_*.py -v                            # M0/M4/M5
pytest tests/agent/test_acceptance_scenarios.py -v -m realllm   # M8
pytest tests/agent/test_cache_hit_rate.py -v -m realllm         # M8 (≥80%)
pytest tests/agent/test_realllm_eval.py -v -m realllm           # M8 机检 ≥28/30
# M8 之后人工填 docs/superpowers/plans/notes/2026-05-02-eval-results.md
```

---

## 版本变化

### v1 → v1.1（5/2 凌晨）— 用户 review 后逐条修复

9 条 review 全部接受：3 条 P1（直接产生错合同 / 错写操作 / 状态丢失）+ 6 条 P2。

| 编号 | 问题 | v1 | v1.1 |
|---|---|---|---|
| **P1-A** | resolve_products 只解析产品身份，没人填 state.items（qty/price），合同会空 items / 错 items | resolve_products 直查产品，generate_contract_node 直接读 state.items | **新增 Task 4.3 parse_contract_items 节点**（thinking on 推理 qty/price 跟 product_id 对齐）；resolve_products 职责边界明确（只负责身份）；contract subgraph 改成 6 节点带条件边路由；补多商品 / 缺价格 / 缺数量 / 同名歧义测试 |
| **P1-B** | resolve_customer 多命中默认取 [0]，合同会发给错客户 | `c = results[0]; state.customer = ...` | **三分支**：unique → 写 state.customer；none → missing_fields=customer；multi → 写 candidate_customers + missing_fields=customer_choice，**不**默认；ContractState/QuoteState 加 candidate_customers / candidate_products；三种命中场景测试 |
| **P1-C** | PendingAction 没存 payload，commit 节点从当前 state 读参数；多 pending / 重启 / checkpoint 恢复时会取错参数 | `await tool_executor("adjust_price_request", {"customer_id": state.customer.id, ...})` | PendingAction 加必填 `payload: dict`（含 `tool_name` + canonical `args`）；preview 节点写 payload 进 ConfirmGate；confirm_node claim 后**注入 state._confirmed_payload**；commit 节点**只**从 payload 读参数；补"两个 pending 选第 1 个只执行第 1 个 payload"测试 |
| **P2-D** | 重试范围只 429/503，漏 408/425/其他 5xx/TransportError/TimeoutException | `if e.response.status_code in (429, 503): raise _RetryableError(...)` | retryable = {408,425,429} ∪ 5xx；显式捕 `httpx.TransportError`（DNS/TCP/TLS）；`TimeoutException` 仍重试 |
| **P2-E** | CrossContextClaim 在 llm_client 和 confirm_gate 各定义一次 → `pytest.raises` 捕不到同一类 | Task 0.4 在 llm_client.py 定义 + Task 0.5 import from confirm_gate | llm_client.py 删除该类（加注释指引）；CrossContextClaim **唯一定义**在 hub.agent.tools.confirm_gate；test 全部 import from confirm_gate |
| **P2-F** | 4 处 `pass` 占位测试让 phase 看起来已覆盖实际无断言 | 故事 2 / contract full flow / read fallback / write metric 4 处 pass | 故事 2 改为只提交 fixture（端到端测试归 Task 8.1 driver）；contract full flow 改为节点集合断言 + Phase 7 接入回填注释；read fallback 写完整测试（断言 raise HTTPStatusError）；write metric 写完整测试（mock hub.metrics.incr） |
| **P2-G** | action_id 用 `uuid4().hex[:8]`（8-hex 长期碰撞风险），Plan 6 之前已修过又退回 | `action_id=f"adj-{uuid4().hex[:8]}"` | ConfirmGate.create_pending **内部生成完整 32-hex**（`f"{action_prefix}-{uuid4().hex}"`）；调用方不传 action_id 不截断；测试 fixture 短 ID 仅限 mock；加 `test_action_id_is_full_32_hex` 验收 |
| **P2-H** | GraphAgent._build 只留 `...` 占位，Phase 7 主图组装关键路径无骨架 | `def _build(self): ... # subagent 实施时按 spec §2 架构图组装` | 完整可执行 StateGraph 骨架（router → 7 子图 + confirm 三分支 + confirm 后用 selected.subgraph 二次路由到 commit_*）；MemorySaver checkpointer；补 `test_graph_agent_build_node_set` + `test_router_to_subgraph_routing` 单测 |
| **P2-I** | 30 case eval 用"人工打分或半自动" + 24/30 PASS 当 release gate，pytest 容易跑流程但不真判定 | "eval 不要求精确 match...主观满意度 1-5（人工或半自动）" | YAML 每 case 必填机检字段（intent / tool_caps / must_contain / forbid）；机检 ≥ 28/30 PASS 是 hard gate；人工分写到 `docs/.../notes/2026-05-02-eval-results.md` 产物文件（含 reviewer 签名 + 时间戳）；纯 stdout 输出不算 release gate |

**Phase 4 Task 数**：8 → 9（新增 Task 4.3 parse_contract_items，原 4.3-4.8 顺延 4.4-4.9）。
**总 Task 数**：54 → 55。
**总工时**：仍 8 天（parse_items 节点开发+测试摊在 M4 1.5 天里）。

### v1.1 → v1.2（5/2 凌晨晚段）— 第二轮 review 修补实施 bug

6 条 review 全部接受：2 条 P1（confirm_node 漏写 payload + LangGraph 节点 async 误用，会让 GraphAgent 完全跑不起来）+ 4 条 P2。

| 编号 | 问题 | v1.1 | v1.2 |
|---|---|---|---|
| **P1-A** | confirm_node claim 成功后只写了 `_confirmed_subgraph` / `_confirmed_action_id` 没写 payload；commit 节点 `getattr(state, "_confirmed_payload", None)` 永远是 None → 走 no_payload 分支 | `setattr(state, "_confirmed_subgraph", ...)` 动态属性 | AgentState 加 3 个**正式 Pydantic 字段** `confirmed_subgraph` / `confirmed_action_id` / `confirmed_payload`；confirm_node 直接 `state.confirmed_payload = selected.payload`；commit 节点直接 `state.confirmed_payload`；测试断言这 3 个字段都被写 |
| **P1-B** | LangGraph 节点用 `lambda s: async_node(s, ...)` 这种 sync lambda 返回 coroutine，LangGraph 不会再 await → TypeError 把 coroutine 当 state 返回 | `g.add_node("resolve_customer", lambda s: resolve_customer_node(s, llm=llm, ...))` | 改成显式 `async def _resolve_customer(s): return await resolve_customer_node(...)` 然后 `g.add_node('resolve_customer', _resolve_customer)`；contract subgraph + GraphAgent _build 全改 |
| **P2-C** | resolve_customer/resolve_products 已写 candidate 不默认 [0]，但 ask_user 只输出"customer_choice"内部字段，没有列候选项 + id + 名称；也没有节点能解析"选 1"消费 candidate → 卡死无法继续 | ask_user `state.final_response = f"还差这些信息：customer_choice"` | ask_user 重写：列编号 / id / 名称（"1) [id=10] 阿里巴巴"），分别处理 candidate_customers / candidate_products / 普通缺失字段；resolve_customer/resolve_products 节点入口加 `_try_consume_*_selection`：识别"选 1"/"id=10"/直接说名字 → 消费候选写 state.customer/products，清空 candidate；4 个测试覆盖 by_number / by_id / by_name / unrecognized 四种 |
| **P2-D** | resolve_products 测试 mock `tool_executor` 每次都返回 [H5,F1,K5]，但实现按 hint 单独调用 → 每次拿到 3 个判成歧义 → out.products 全空，TDD 自身跑不过 | `tool_executor=AsyncMock(return_value=[{H5},{F1},{K5}])` | 测试改 `async def fake_executor(name, args): return SEARCH_RESULTS[args["query"]]`，按 query 字典分派；每个 hint 各拿到唯一对应产品；断言 `len(candidate_products) == 0` 确认无歧义 |
| **P2-E** | adjust_price 详细展开但 voucher / adjust_stock 只写"结构同 5.2"。voucher 涉及幂等 + 审批状态，比调价风险更高 | `- [ ] Step 1-3: 类似 5.2 + Commit` | adjust_stock 完整展开（preview payload / commit / 幂等键 idempotency_key 5 分钟去重 / 多 pending 测试 / 跨会话隔离测试 / 重复确认拒绝测试）；voucher 完整展开（订单状态 fail closed / 已有凭证拒绝 / 12 小时强幂等 key / 多 pending 选择 / 跨会话）；ConfirmGate.create_pending 加 `idempotency_key` 参数（同 key TTL 内复用原 PendingAction 不创建新的） |
| **P2-F** | eval YAML 仍只示例 4 case + 注释"续 26 case"，release gate 依赖完整 30 case gold set；执行时容易偷懒只补最小数量 | YAML 4 case + "subagent 实施时 case 总数 ≥ 30" | YAML **完整列出 30 case**（chat 4 / query 6 / contract 6 / quote 3 / voucher 3 / adjust_price 3 / adjust_stock 2 / cross-round 1 / boundary 1 + adjust_price 2 个隔离/多 pending case，刚好 30）；新增 unit test `test_fixture_schema_30_cases_with_intent_distribution`（无需真 LLM）先卡住：恰好 30 个 + 每 intent 最低分布 + id 唯一 + 字段齐全 |

### v1.2 → v1.3（5/2 凌晨深夜）— 第三轮 review 修补关键路径

6 条 review 全部接受：2 条 P1（生产环境直接卡死或拿到不可 claim 的 action_id）+ 4 条 P2。

| 编号 | 问题 | v1.2 | v1.3 |
|---|---|---|---|
| **P1-A** | resolve_customer / confirm_node 已加候选选择，但顶层先走 LLM router；用户第二轮只回"1"会被 router 判成 unknown/chat → 选择逻辑根本进不去 | `START → router (LLM) → 子图` | **新增 pre_router 节点**：`START → pre_router → (router 或直接分发)`；pre_router 检查 (conv, user) 是否有 pending action（消息匹配确认/编号 → Intent.CONFIRM）或 checkpoint 是否有 candidate_customers/products（沿用上一业务 intent 如 contract/quote）；命中跳过 LLM router；新增 `GraphAgent._peek_last_subgraph_state` helper 通过 `compiled_graph.aget_state` 读上轮 state |
| **P1-B** | voucher 全局幂等 key 不含 conv/user（防同订单重复出凭证），但 `create_pending` "相同 key 返回原 pending" 会让 A 私聊创建的 pending 被 B 群聊拿到 action_id；B `confirm_node` 按 (conv,user) 查不到 → 显示"没有待办"但用户以为已经申请了 | `idempotency_key=f"vch:{order_id}:outbound"` 直接复用 | ConfirmGate.create_pending 同 (conv, user) 命中复用 / 跨 (conv, user) 命中**raise CrossContextIdempotency**；voucher preview 节点捕获后 fail closed 回"该订单已有凭证申请待确认/处理中"，**不**暴露 action_id；新增 `test_voucher_idempotent_cross_context_fails_closed` 测试 |
| **P2-C** | confirm_node 用 `pendings` 列表顺序生成 1)/2)/3)；list_pending_for_context 没要求按 created_at 排序，Redis scan 顺序不稳定 → "1)" 看到的和下一轮"1"绑不到同一 action | `pendings = await gate.list_pending_for_context(...)` 直接索引 | PendingAction 加 `created_at: datetime` 必填字段；list_pending_for_context **必须**按 created_at asc 排序（同时刻用 action_id break tie）；新增 `test_list_pending_order_stable_by_created_at`（连续 5 次顺序一致）|
| **P2-D** | eval driver 只检查 `count > cap`，写 `create_voucher_draft: 1` 实际 0 次也通过；YAML 已写 `search_products: {min:1, max:2}` dict，driver 拿 int 比 dict 直接 TypeError | `if count > cap: fail` | tool_caps 双语义：int 表 exact（count 必须正好等于）/ dict `{min, max}` 表 range（min ≤ count ≤ max）；driver 显式 isinstance 分支 + 校验上下限；YAML 头部注释更新约定 |
| **P2-E** | 我在 v1.2 写"恰好 30"，但实际只列了 29 个 case id（chat-01 到 boundary-01）；test_fixture_schema_30_cases 会直接失败 | 29 case，断言 `len == 30` | 补第 30 条 `isolation-01-group-per-user-context`：同群聊 user A 起合同 + user B 起查询 + A 选 1 — 验证 per-user 隔离 + candidate 跨轮持久化（也验收 P1-A pre_router）|
| **P2-F** | 代码骨架 f-string 含未转义中文双引号 `f"...回复"确认"..."` Python 语法错误；adjust_stock / voucher 都有 | `f"\n\n回复"确认"执行..."` | 全改单引号外层 `f'\n\n回复"确认"执行...'`，骨架可直接复制不炸 |

### v1.3 → v1.4（5/2 凌晨深夜后段）— 第四轮 review 修补集成路径

6 条 review 全部接受：2 条 P1（候选上下文丢失会让 pre_router 永远不命中 / 入库凭证错类型）+ 4 条 P2。

| 编号 | 问题 | v1.3 | v1.4 |
|---|---|---|---|
| **P1-A** | pre_router 依赖 checkpointer 里的 candidate_customers/candidate_products 路由"1"回 contract/quote，但**父图 StateGraph 用 AgentState**，AgentState 没声明这些子图字段 → 子图返回的候选**不会进父图 checkpoint** → pre_router peek 永远拿不到 | candidate_* 在 ContractState/QuoteState 上；pre_router 用 `_peek_last_subgraph_state` peek | **跨轮选择字段全部提升到 AgentState**：`extracted_hints` / `customer` / `candidate_customers` / `products` / `candidate_products` / `items` / `missing_fields`（ContractState/QuoteState 只留 shipping/draft_id/quote_id 等专属字段）；pre_router 直接读 state（LangGraph hydrate 已带这些字段，不再 peek）；新增集成测试 `test_candidate_persists_through_checkpoint_and_consumed_next_round` 验"多客户候选→下一轮选 1→真消费"端到端跑通 |
| **P1-B** | Task 5.4 voucher 说出库/入库凭证，但 preview payload + idempotency_key 都硬编码 `"outbound"`；用户发起入库时会去重到出库语义，会创建错类型凭证 | `"voucher_type": "outbound"` / `f"vch:{order_id}:outbound"` | VoucherState 加 `voucher_type` 字段（必填）；新增 `_resolve_voucher_type` 从消息 / extracted_hints 解析（"入库/收货"→inbound / "出库/发货"→outbound / 其他→ask_user）；payload + idempotency_key 都写解析后的真实 voucher_type；订单 voucher_count 检查也按 type 区分（同订单可同时有 inbound + outbound 各 1 张）；4 个新测试覆盖 outbound explicit / inbound 不被 outbound 去重 / 类型未解析 ask_user / 跨 context fail closed |
| **P2-C** | confirm_node 多 pending 文案允许用户回复 action_id，但 pre_router 的 looks_like_selection 只识别纯数字 / id=数字 / 确认词，不识别 `adj-/vch-/stk-` action_id 前缀；用户复制 action_id 仍走 LLM router 落 chat/unknown | `looks_like_selection = ...只匹配数字...` | 加 `looks_like_action_id`：正则匹配通用前缀 `(adj\|vch\|stk\|act\|qte\|cnt)-[0-9a-f]{8,}` + 当前 pending 列表里的实际 action_id 子串；`pendings and (looks_like_selection or looks_like_action_id)` 都走 Intent.CONFIRM |
| **P2-D** | 30 case YAML 有 `sent_files_min` / `items_count` / `pending_state` / `creates_pending_action` 关键字段，但 driver 只验 intent / tool_caps / must_contain / forbid → 合同没发文件 / items 数不对 / pending 没创建也通过 | 4 个机检维度 | driver 加 4 个新机检：sent_files_min（≥）/ items_count（exact）/ creates_pending_action（bool）/ pending_state（dict {action_id: 状态}）；schema 测试加反向断言 `SUPPORTED_TURN_FIELDS` — yaml 里出现未知字段会失败，强制"先在 driver 加机检再加 yaml 字段" |
| **P2-E** | contract-05 第一轮缺地址/联系人/电话，第二轮只选客户却期望 generate_contract_draft=1 / sent_files_min=1，与"缺地址 ask_user"规则冲突 | 第一轮无地址/联系人/电话 | 第一轮补齐地址 + 张三 13800001111，让这个 case 专测客户多命中歧义；第二轮"1"消费候选 + 直接生成（tool_caps 显式 search_customers=0 验候选不重搜）+ items_count=1 |
| **P2-F** | adjust_stock 测试 `gate.create_pending(...payload=... ...)` 是 Python 语法错误 | `create_pending(...payload={...}...)` 三处 | 三处 create_pending 全补完整参数（hub_user_id / conversation_id / subgraph / action_prefix / summary / payload / idempotency_key），可直接复制运行 |

### v1.4 → v1.5（5/2 早晨）— 第五轮 review 修补运行时正确性

6 条 review 全部接受：3 条 P1（v1.4 把候选放父 state 但 run() 把它清空 / "选 N" 不识别 / shipping 还在子 state 跨轮丢）+ 3 条 P2。

| 编号 | 问题 | v1.4 | v1.5 |
|---|---|---|---|
| **P1-A** | run() 把完整 AgentState `model_dump()` 传给 ainvoke。下一轮"选 1"时 candidate_customers=[] / candidate_products={} / items=[] / missing_fields=[] 等空默认值会**覆盖** checkpoint 里的旧值 → pre_router 永远 peek 不到 | `out = await ainvoke(initial.model_dump(), config=config)` | run() 显式构造 minimal `update_payload`：只列要 reset 的字段（intent / final_response / file_sent / errors / confirmed_*）+ 本轮新输入；**不**写 candidate_* / customer / products / items / missing_fields / shipping —— LangGraph checkpoint 自动 hydrate；加 LangGraph "未传字段保留 / 显式传覆盖"语义说明 |
| **P1-B** | pre_router 的 `looks_like_selection` 只匹配纯数字 / id=数字 / 确认词 / "第 N"，不识别"选 2" / "选2"；同样 `_try_consume_*_selection` 优先匹配裸数字 → "选 2" 进 LLM router 落 chat | `re.search(r"^\s*[1-9]\s*$", msg)` | 三处都加 `re.search(r"选\s*[1-9]", msg)`：pre_router looks_like_selection / _try_consume_customer_selection / _try_consume_product_selection；端到端测试改用"选 2"而不是"1" |
| **P1-C** | v1.4 提升了 candidate_* / customer / products / items 但 **shipping 还在 ContractState**。用户先给完整地址后遇到候选 → 下一轮选候选时父图 checkpoint 保留了 products/items 但**丢了 shipping** → validate_inputs 又问地址 | `class ContractState: shipping: ShippingInfo` | shipping 也提升到 AgentState；ContractState 只剩 draft_id；端到端测试 `test_candidate_persists_through_checkpoint_and_consumed_next_round` 加断言 `snapshot2.values["shipping"]["address"] == "北京海淀"` |
| **P2-D** | 候选持久化测试 `test_candidate_persists_through_checkpoint_and_consumed_next_round` 是注释骨架，不会捕获 P1-A 的 checkpoint 覆盖 bug | 函数体只有"# 详细 mock 构造由 subagent 落地" | 写成完整可执行测试：构造 fake_tool_executor + 7 个有序 LLM mock response + 跑两轮 ainvoke + 4 个关键断言（第 2 轮 search_customers 不调 / customer.id == 阿里云 / candidate_customers 清空 / draft_id 写入 / shipping 跨轮保留） |
| **P2-E** | preview_voucher_node 改成按 voucher_type 检查 outbound_voucher_count / inbound_voucher_count，但 `test_voucher_rejects_already_voucher` 的 mock 仍返回 `voucher_count: 1`（按当前实现拿不到，会继续创建 pending）；同时缺 inbound 已存在测试 | `mock 返 voucher_count: 1` 单测 | 拆成两个测试：`test_voucher_rejects_already_outbound_voucher` mock outbound_voucher_count=1 / `test_voucher_rejects_already_inbound_voucher` mock inbound_voucher_count=1；同时 cross-context 测试 mock 也修成 outbound_voucher_count=0 / inbound_voucher_count=0 |
| **P2-F** | driver 新增 `sent_files_min` / `items_count` / `creates_pending_action` / `pending_state` 机检读 `res.sent_files` / `res.items` / `res.pending_action_id` / `res.intent`，但 GraphAgent.run 返 AgentResult 只有 text/error/kind → AttributeError | `res, tool_calls = await _run_turn_with_metrics(turn)` | **新增 `EvalTurnResult` dataclass**（text/intent/sent_files/items/pending_action_id/error/kind）；`_run_turn_with_metrics` 完整实现：从 compiled_graph.aget_state(config) 取 snapshot.values 填 intent/items / 从 tool_logger 取本轮 tool 调用 + send_file 列表 / 从 ConfirmGate.list_pending_for_context 取最新 pending；driver fixture 加 real_agent / real_gate / tool_logger |

### v1.5 → v1.6（5/2 早晨晚段）— 第六轮 review 修补 quote 路径 + eval 隔离

4 条 review 全部接受：2 条 P1（候选来源 intent 丢失会让 quote 流程"选 N"误进 contract / 30 case 共用 conversation 互相污染）+ 2 条 P2。

| 编号 | 问题 | v1.5 | v1.6 |
|---|---|---|---|
| **P1-A** | run() 显式把 `intent` reset 为 None；pre_router 又依赖 state.intent 判候选回 contract 还是 quote → quote 流程留下候选时下一轮"选 2"会兜底走 contract | `last_intent = state.intent; if last_intent in {Intent.CONTRACT, Intent.QUOTE}: ...` | **AgentState 加 `active_subgraph: str \| None`** 持久化候选来源；contract / quote 子图入口加 `set_origin` 节点（写 state.active_subgraph = "contract" / "quote"）；pre_router 用 `state.active_subgraph` 路由（None 时打 warning 兜底 contract）；contract/quote 生成成功后清 candidate_* + active_subgraph |
| **P1-B** | _run_turn_with_metrics 给所有未指定 conversation_id 的 turn 用 `eval-default` → 30 case 共享同一 LangGraph checkpoint，前一 case 的 customer/products/pending/items 污染后 case | `conversation_id=turn.get("conversation_id", "eval-default")` | _run_turn_with_metrics 签名加 `case_id: str`；默认 `conversation_id = f"eval-{case_id}"`；同 case 多 turn 共享；不同 case 隔离；driver 调用处 `case_id=case["id"]` 传入 |
| **P2-A** | eval driver 用 @dataclass / Intent / ConfirmGate 但 import 不全；调用未定义的 `_check_pending_state` → collection 期 NameError | `import os, yaml, json, pytest, datetime, Path` | imports 加 `Counter / dataclass / Intent / ConfirmGate`；新增 **`_check_pending_state(gate, action_id) -> str`** 完整实现（still_pending / claimed / expired / missing 四态）；Task 0.5 ConfirmGate 改造说明加 `get_pending_by_id` / `is_claimed` / `PendingAction.is_expired` 三个 API |
| **P2-B** | `digit_map.get(m.group(1), int(m.group(1)) if isdigit else 0)` 默认参数提前求值 — 用户回"第二个"时 `int("二")` 先抛 ValueError | `dict.get(key, default 提前求值)` | 显式 if/else：`if token.isdigit(): num = int(token); else: num = digit_map.get(token, 0)`；新增 `test_candidate_selection_by_chinese_ordinal` 验"第二个" 命中 candidates[1] |

### v1.6 → v1.7（5/2 上午）— 第七轮 review 收合同核心链路缺口 + quote 闭环

3 条 review 全部接受：1 条 P1（合同 shipping 没解析，会空地址生成）+ 2 条 P2。

| 编号 | 问题 | v1.6 | v1.7 |
|---|---|---|---|
| **P1** | shipping 已提升到 AgentState 但**没有节点把 user_message 解析进 state.shipping** — generate_contract_node 只取 state.shipping 字段 → 用户给"地址北京海淀，张三 138..."合同也会空地址生成，或 validate_inputs 反复问 | shipping 字段在父 state 但无写入路径 | **新增 Task 4.4 parse_contract_shipping 节点**（thinking off 结构化抽取 address/contact/phone；只写抽到字段不默认空串；跨轮短消息如"选 2"不调 LLM 不覆盖上轮抽到的）；contract subgraph 编排加节点（parse_items → parse_shipping → validate_inputs）；后续 4.5-4.9 顺延 4.6-4.10；新增 4 测试（full info / missing address / cross-round 跳过 / only phone）；Phase 4 task 数 9 → 10，总数 55 → 56 |
| **P2-A** | eval driver 调用 `_check_pending_state(aid)` 漏传 gate（函数签名是 (gate, action_id)）→ pending_state case 直接 TypeError | `actual_state = await _check_pending_state(aid)` | `actual_state = await _check_pending_state(real_gate, aid)` |
| **P2-B** | Task 6.1 quote 子图仍只写"类似 contract"概要，无可执行骨架 — v1.6 的 active_subgraph 修复依赖 quote 也实现 set_origin / cleanup | "Step 1-3: 类似 contract 但更简单 + Commit" 一行 | quote 完整展开：QUOTE_SYSTEM_PROMPT / build_quote_subgraph 含 6 节点（set_origin/resolve_customer/resolve_products/parse_items/generate_quote/format）/ generate_quote_node 调 generate_price_quote 后清候选 + active_subgraph；3 个测试（不挂 generate_contract_draft / set_origin 节点存在 / 报价多候选→选 2 仍走 quote 端到端） |

### v1.7 → v1.8（5/2 中午）— 第八轮 review 收"多候选丢信息"根因

4 条 review 全部接受：2 条 P1（同根因 — 多候选 ask_user 之前没把原文 hints 落 state）+ 2 条 P2。

| 编号 | 问题 | v1.7 | v1.8 |
|---|---|---|---|
| **P1-A** | contract 多候选（customer 或 product）时直接走 ask_user，parse_contract_shipping / parse_contract_items 都不执行；第二轮"选 2"时 user_message="选 2" → 后续节点看不到第一轮的地址 / 联系人 / qty / price → 反复问 / 丢信息 | parse_shipping 和 parse_items 都在 ask_user 之后或被它跳过 | **新增 extract_contract_context 节点放 set_origin 后第一个**（任何 ask_user 之前）；一次 LLM 抽完 customer_name + product_hints + items_raw + shipping 全写 state；跨轮短消息（≤ 8 字）跳过 LLM 不覆盖；抽到 null 时不覆盖 state 已有值；parse_contract_shipping 弃用（功能并入）；parse_contract_items 改成优先从 `state.extracted_hints['items_raw']` 拿原始 hint→qty→price，本地 hint→product 名/SKU 模糊匹配，匹配失败回退 LLM；4 个新测试覆盖 |
| **P1-B** | resolve_products 多商品依赖 `state.extracted_hints.product_hints` 但**没有节点写入** → 多商品场景兜底走单次合并搜，复杂合同丢商品 | hints 字段空 → 走 fallback 单次合并 | extract_contract_context 节点同时写 product_hints；resolve_products 拿到完整 hints list，每个 hint 各自 search → 各自唯一/歧义判定 |
| **P2-A** | draft_id 仍只在 ContractState；同 v1.4 candidate 教训 — 父图 AgentState 不含字段 → checkpoint 不存 → 集成测试 / eval driver 从父图 snapshot 读不到 ID | `class ContractState: draft_id: int \| None` | draft_id + quote_id 都提升到 AgentState；ContractState / QuoteState 变成空业务子类（保留只为类型签名清晰）；端到端测试断言 `snapshot.values["draft_id"]` 能读到 |
| **P2-B** | GraphAgent _build 的 _pre_router 兜底分支调 `logger.warning` 但 import 区没 import logging → 一旦旧 checkpoint / bug 触发 fallback → NameError | `import dataclass / typing` 但缺 `logging` | 顶部加 `import logging` + 类外 `logger = logging.getLogger(__name__)` |

**Phase 4 task 数**：10 不变（Task 4.4 内部从 parse_shipping 升级成 extract_contract_context，节点数没增）；总 task 数 56 不变。

### v1.8 → v1.9（5/2 中午晚段）— 第九轮 review 修 quote 平等 + 短消息边界

4 条 review 全部接受：2 条 P1（quote 流没复用 v1.8 修复 / 短消息无差别跳过会吞补字段）+ 2 条 P2。

| 编号 | 问题 | v1.8 | v1.9 |
|---|---|---|---|
| **P1-A** | v1.8 只在 contract 流前移了 extract_contract_context；quote 流仍是 resolve_customer→resolve_products→parse_items；报价多命中走 ask_user 后第二轮"选 2" → parse_contract_items 看到的 user_message 是"选 2"不是"X1 50 个 300" → 真 LLM 重新问数量/价格 | 只 contract 子图加了节点 | quote 子图也加 extract_contract_context（同一节点函数复用，报价用不到 shipping 但写到 state 也无害）；`build_quote_subgraph` 加 set_origin → extract_contract_context → resolve_customer 路径 |
| **P1-B** | 跳过规则 `state 已有 hints + 本轮 ≤ 8 字 → 跳过抽取`，能保护"选 2"但**也吞掉**用户上轮缺地址后回"北京海淀"/"张三"等短补字段消息 → validate_inputs 仍认为缺 | `len(state.user_message.strip()) <= 8` 一刀切跳过 | 抽出 `_looks_like_pure_selection(message)` helper：只对纯数字/"选 N"/"id=N"/action_id 前缀/确认词跳过；其他短消息（"北京海淀"/"张三"/电话号）仍走 LLM；only-write-non-null 保护原值不被覆盖；2 个新测试（10 个 selection 消息 / 1 个补字段消息）|
| **P2-A** | 集成测试 `test_candidate_persists_through_checkpoint_and_consumed_next_round` 的 `llm_responses` 序列还按 v1.7 编排（router → search_customers → ...）；v1.8 加了 extract_contract_context 后第 1 轮 LLM 调用次序 → router → extract_context → search_customers → ... 序列错位会导致 extract_context 消费空 tool_call 响应 + json decode 失败 | router 后直接 search_customers 响应 | 第 1 轮 router 响应后插入 extract_context 的 JSON 响应；同样修补 `test_quote_multi_customer_select_2_stays_in_quote_route` 的序列；注释说明第 2 轮"选 2"被 _looks_like_pure_selection 命中 → extract_context 跳过不消耗响应 |
| **P2-B** | v1.8 的 parse_contract_items 优先消费 `state.extracted_hints['items_raw']` 本地匹配 hint→product，但旧测试只覆盖 LLM fallback 路径 — 快路径无单测覆盖 | 3 个测试都进 LLM 兜底 | 加 2 个新测试：`test_parse_items_uses_extracted_hints_fast_path_no_llm`（items_raw 已存在 + 产品身份能本地匹配 → llm.chat 不被调用 / 3 items 全部生成）/ `test_parse_items_falls_back_to_llm_when_hint_mismatch`（hint=Z9 在 products 里找不到 → 必须 fallback LLM）|

### v1.9 → v1.10（5/2 下午）— 第十轮 review 收"复用旧上下文 / 跨轮 fallback / 多组候选"

3 条 review 全部接受：2 条 P1（生成后只清候选会复用旧客户/商品 + items_raw fallback 仍传 user_message）+ 1 条 P2。

| 编号 | 问题 | v1.9 | v1.10 |
|---|---|---|---|
| **P1-A** | 合同/报价生成成功后只清 candidate_* 和 active_subgraph，但 customer / products / items / shipping / extracted_hints 仍留 checkpoint；下一轮"给百度做合同 Y2..." → resolve_customer 看 state.customer 已存在直接 return → 复用阿里出错合同 | `state.candidate_customers = []; state.candidate_products = {}; state.active_subgraph = None` | generate_contract_node + generate_quote_node 都清**完整工作上下文**：customer / products / items / shipping (重置成空 ShippingInfo) / extracted_hints / candidate_* / missing_fields / active_subgraph；保留 draft_id / quote_id / file_sent（业务结果）；新增 `test_generate_contract_clears_complete_working_state` 测试覆盖每个字段 |
| **P1-B** | items_raw 本地匹配失败后 fallback LLM 时仍传 `state.user_message` — 跨轮场景 user_message="选 2"，hint 不完全匹配产品名时 LLM 看到"选 2"无法对齐 qty/price → 丢数据 | `{"user_message": state.user_message, "products": ...}` | fallback prompt 优先传 `items_raw + products`（items_raw 已是 extract_contract_context 抽好的原始 hint/qty/price，跨轮安全）；items_raw 为空才 fallback 到 user_message（极端兜底）；PARSE_ITEMS_PROMPT 同步更新输入字段说明；新增测试 `test_parse_items_fallback_uses_items_raw_not_user_message`（user_message='选 2' + hint 不匹配 → fallback 必须不传 user_message + 仍能对齐 qty 50 / price 300）|
| **P2** | 多组候选 candidate_products 按 hint 分组（如 {H5: [...], F1: [...]}），但消费时**对每个 hint 都用同一条 user_message 调 _try_consume_product_selection** — 用户回"选 2"会把 H5 和 F1 都选第二项 → 错合同 | `for hint in groups: chosen = _try_consume(user_message, candidates)` 每组独立但用同一消息 | resolve_products 加分支：单组候选照旧（"选 N" / 名字 / id 都接受）；**多组候选**仅接受 `id=N` 精确匹配，裸编号 / 名字一律拒；ask_user 输出多组时提示"请用 id=N 精确选每个，例如：H5 用 id=10，F1 用 id=22"；2 个新测试（多组候选 "选 2" 不消费 / 多组候选 "id=11" 精确选 H5 第二项 + F1 留候选）|

### v1.10 → v1.11（5/2 下午中段）— 第十一轮 review 修 cleanup 时序 + 测试断言一致性

4 条 review 全部接受：1 条 P1（cleanup 提前导致 format_response 看到空 state）+ 3 条 P2。

| 编号 | 问题 | v1.10 | v1.11 |
|---|---|---|---|
| **P1** | generate_contract_node 生成成功后立刻清 customer/products/items/shipping/extracted_hints；子图下一步 format_response 用 `s.customer.name` 和 `len(s.items)` 写 summary → 回执变成 "customer=unknown, items=0"；quote 同问题 | cleanup 在 generate_*_node 内部 | 拆出独立 `cleanup_after_contract_node` / `cleanup_after_quote_node`；子图编排改成 `generate → format_response → cleanup_after_* → END`；contract / quote subgraph 都加 cleanup 节点；测试拆成两个：`test_generate_contract_keeps_state_for_format_response`（generate 不清状态）+ `test_cleanup_after_contract_clears_complete_working_state`（cleanup 才清）|
| **P2-A** | 合同集成测试 `test_candidate_persists_*` 仍按 v1.7 断言 `snapshot2["customer"]["id"] == 11` / shipping 保留 — v1.10 cleanup 之后这些应该是 None / 空 → 测试和实现冲突 | 7 行旧断言 | 改成断言：`draft_id == 999` + `file_sent is True` 保留；`customer is None` / `products == []` / `items == []` / `candidate_* == []`/`{}` / `extracted_hints == {}` / `active_subgraph is None` / `shipping.address/contact is None` |
| **P2-B** | 报价集成测试 `test_quote_multi_customer_select_2` 仍断言 `snap2["customer"]["id"] == 11` | 旧断言 | 改成 `quote_id == 888` + `file_sent is True` 保留 / `customer is None` / 全部跨轮工作字段清空 |
| **P2-C** | ask_user 文案提示"H5 用 id=10，F1 用 id=22"（暗示一次选多个），但 resolve_products 多组分支只 `re.search` 取**第一个** id 套到所有组 → 用户按提示一次回两个 id 时只消费第一项 | `m = re.search(...; target_id = int(m.group(1))` 取一个 id 套全部 | 改 `re.findall` 解析消息里**所有** `id=N` 出现位置 → `ids_in_msg: set[int]`；每个 group 找其候选 id 是否在集合里；支持单 id（一次解决一组）或多 id（一次解决多组）；新增测试 `test_multi_group_candidate_one_message_multiple_ids`（"id=11 id=21" 同时消费 H5+F1）|

### v1.11 → v1.12（5/2 下午晚段）— 第十二轮 review 收 cleanup 副作用

2 条 P1 都接受：cleanup 后状态读取 / 多 id selection 识别。

| 编号 | 问题 | v1.11 | v1.12 |
|---|---|---|---|
| **P1-A** | v1.11 cleanup_after_*_node 把 state.items 清空，但 eval driver `_run_turn_with_metrics` 仍从 `state.values["items"]` 填 EvalTurnResult.items → contract-02 / 04 / 05 等 items_count case 永远读到 0 → release gate 假失败 | `items=values.get("items", [])` | items 改从 `tool_logger` 取本轮 `generate_contract_draft` / `generate_price_quote` 调用的 `args["items"]`（最后一次 generate 调用）；不被 cleanup 影响；EvalTurnResult.items 注释更新 |
| **P1-B** | v1.11 支持 "id=11 id=21" 多 id 消费，但 `_looks_like_pure_selection` 只匹配**单个** `re.fullmatch(r"id\s*[=:：]?\s*\d+")`；多 id 消息 → 不跳 LLM → extract_contract_context 真 LLM 可能把 "id=11 id=21" 误抽成新 product_hints/items_raw 覆盖第一轮内容 | `re.fullmatch(r"id\s*[=:：]?\s*\d+", msg)` 单 id | 改成 `re.fullmatch(r"\s*(?:id\s*[=:：]?\s*\d+[\s,，、]*)+\s*", msg, re.I)` 支持多 id（空格 / 逗号 / 顿号 / 中文逗号分隔均可）；SKIP_MESSAGES 测试集追加 4 个多 id case（"id=11 id=21" / "id=11, id=21" / "id=11、id=21" / "id=11,id=21,id=33"） |

### v1.12 → v1.13（5/2 傍晚）— 第十三轮 review 收 hint+id 写法 + sent_files 数据源

2 条接受：1 条 P1（按 ask_user 文案诱导写法仍触发 LLM 抽取）+ 1 条 P2（sent_files 取数源不存在）。

| 编号 | 问题 | v1.12 | v1.13 |
|---|---|---|---|
| **P1** | ask_user 文案明确提示用户回 "H5 用 id=10，F1 用 id=22"；但 `_looks_like_pure_selection` 只匹配**纯** id=N 重复，不识别 hint+id 混合写法 → 进 extract_contract_context → LLM 可能把 H5/F1 重新抽成新 product_hints/items_raw 覆盖第一轮 qty/price | 单/多 id 都得是 fullmatch 纯 id | 新增 `_looks_like_candidate_id_reference(message, candidate_products)` helper：消息里出现至少一个 id=N 且 N 是某个候选 product.id → 算 selection；extract_contract_context_node 入口除 `_looks_like_pure_selection` 外还跑这个；安全 — 用户回电话号"13800001111"无 `id=` 前缀不命中；新增 2 测试（"H5 用 id=10，F1 用 id=22" 等 3 个写法跳 LLM / 电话号无候选时仍跑 LLM） |
| **P2** | eval driver 用 `tool_logger.records` 中 `name == "send_file"` 算 sent_files；但子图里只有 `generate_contract_draft`/`generate_price_quote` 调用，**没有节点写 send_file 记录** → 所有 sent_files_min: 1 case 假失败 | 取 `r["name"] == "send_file"` 记录 | 改成检查本轮调用了多少次"会发文件"的 tool（`generate_contract_draft` / `generate_price_quote` / `create_voucher_draft`）；每次成功调用计 1；EvalTurnResult.sent_files 字段注释更新；不再依赖 send_file tool 记录存在 |

### v1.13 → v1.14（5/2 晚上）— 第十四轮 review 收 sent_files 成功过滤

1 条 P2：sent_files 仍会把失败 generation 计入。

| 编号 | 问题 | v1.13 | v1.14 |
|---|---|---|---|
| **P2** | v1.13 driver 按 tool name 过滤但**没**过滤成功 — ToolLogger 是 try/finally 模式，tool 抛错也写一条带 error 的记录 → 失败 generation 仍可能让 sent_files_min 通过 | `[r["name"] for r in records if r["name"] in FILE_GENERATING_TOOLS]` | 加 `_is_successful_tool_call(r)` helper：`r.get("error") is None and r.get("result") is not None`；driver 过滤通过 + 成功；EvalTurnResult 上方加 ToolLogger 字段契约说明（name / args / result / error / duration_ms / called_at）；新增 unit test `test_sent_files_excludes_failed_generation_calls`：3 种记录（generate_contract_draft 抛错 / generate_price_quote 成功 / create_voucher_draft null result）→ sent_files 只含 generate_price_quote |

### v1.14 → v1.15（5/2 深夜）— 第十五轮 review 收 items_count 失败过滤 + 测试设计

2 条接受：1 条 P2（items 同样疏忽）+ 1 条 P3（测试复制逻辑）。

| 编号 | 问题 | v1.14 | v1.15 |
|---|---|---|---|
| **P2** | v1.14 把 sent_files 改成过滤成功，但 items_from_generate 仍从最后一次 generate 调用读 args 不检查成功 → 失败调用的 items 也被读 → items_count 假绿或读到错参数 | `for r in reversed: if r["name"] in (...): items = r["args"]["items"]; break` | items 也复用 `_is_successful_tool_call(r)` 过滤；只取最后**成功的** generate 调用；新增 `test_items_count_excludes_failed_generation_calls`（成功 2 items + 失败 4 items → 取到 2 items 不是 4） |
| **P3** | v1.14 测试在测试函数内**重新定义** `_is_successful` 和 `FILE_GENERATING_TOOLS`，driver 里写错也假绿 | `def _is_successful(r): ...; FILE_GENERATING_TOOLS = {...}; sent_files = [...]` 全在测试函数里 | `FILE_GENERATING_TOOLS` 和 `_is_successful_tool_call` 提到模块级（`@dataclass class EvalTurnResult` 之上）；测试改 `from hub.agent.tests.test_realllm_eval import FILE_GENERATING_TOOLS, _is_successful_tool_call` 真 import；新增 2 测试：`test_is_successful_tool_call_helper`（直接测 helper）+ `test_file_generating_tools_set_matches_implementation`（防 set 漂移：必含 3 个生成 tool / 不含 search_/get_/check_ 等查询 tool） |

### v1.15 → v1.16（5/2 深夜末段）— 第十六轮 review 收 import 路径错误

1 条 P2：测试 import 用了**不存在**的包路径。

| 编号 | 问题 | v1.15 | v1.16 |
|---|---|---|---|
| **P2** | v1.15 P3 修复时引入新 bug：`from hub.agent.tests.test_realllm_eval import ...`。但实际文件路径是 `backend/tests/agent/test_realllm_eval.py`（项目里没有 `hub.agent.tests` 包）→ pytest collection 直接 ModuleNotFoundError。helper 本就在**同一文件**模块级定义，根本不需要 import | `from hub.agent.tests.test_realllm_eval import FILE_GENERATING_TOOLS, _is_successful_tool_call` | 删除该 import — 同模块内顶部已定义的 `FILE_GENERATING_TOOLS` / `_is_successful_tool_call` 直接可见，测试函数直接引用即可；注释标明历史误区（不复制 / 不 import 自己） |


