# HUB 钉钉机器人 — ReAct Agent 重构设计文档

> 日期：2026-05-03
> 作者：lin + Claude（Plan 6 v9 → v10）
> 状态：待 Codex review

## 1. 背景与问题

### 1.1 当前架构（Plan 6 v9）

LangGraph DAG workflow：
- **入口**：`pre_router` → `router`（LLM 分类 8 个 intent） → 7 个 subgraph（chat / query / contract / quote / voucher / adjust_price / adjust_stock）+ confirm 路径
- **每个业务 subgraph 固定节点链**：`set_origin → extract_context → resolve_customer → resolve_products → parse_items → validate_inputs → ask_user / generate → format_response → cleanup`
- **State**：`AgentState` Pydantic 模型 30+ 字段（customer / items / shipping / candidate_customers / candidate_products / missing_fields / extracted_hints / active_subgraph / draft_id / quote_id / confirmed_* / file_sent / errors / ...）
- **持久化**：AsyncPostgresSaver 跨 worker 重启 hydrate state

### 1.2 实测痛点（2026-05-03 钉钉真实测试一下午发现）

按发现顺序记录的 bug 家族：

| # | 痛点 | 根因类别 |
|---|---|---|
| 1 | DeepSeek base_url main vs beta 错配，全部 LLM 调用 400 | 配置 |
| 2 | MemorySaver 重启即丢上下文 | 持久化 |
| 3 | F1 SKU 子串误命中按摩披肩（DS-125F10A3）| ERP 模糊匹配 |
| 4 | ERP4 返 `{"items":[...]}` dict 而非 list，resolve_customer 报 "string indices" | ERP 接口契约 |
| 5 | `_py_to_json_type` plain dict 落 string fallback | 工具 schema 推断 |
| 6 | `validate_inputs` LLM 自由输出 `customer_address` / `customer_phone`（合同模板占位符）→ 暴露英文 enum 给用户 | LLM 自由发挥未约束 |
| 7 | 合同 payload 漏 `template_id` + 字段名 `contact` vs `shipping_contact` 错 | 子图 deterministic 节点构造 payload 时漏字段 |
| 8 | LangGraph 父子图 schema 边界过滤 Optional 嵌套字段（`customer: CustomerInfo \| None = None` 在子图入口走默认 None）| **架构性 bug** |
| 9 | `missing_fields` 跨轮残留误路由（上轮"还差客户"留下 `missing=['customer']` → 这轮 customer 已有值仍走 ask_user）| **架构性 bug** |
| 10 | 客户切换不重置 state.customer（上轮"阿里巴巴"残留 → 下轮"给翼蓝做合同"用旧客户）| **架构性 bug** |
| 11 | fingerprint 命中 short-circuit 不重发 docx（bot 说"已生成"用户没收到文件）| 业务逻辑 |
| 12 | "同样的内容给得帆也做一份" — bot 不知道"同样"指什么（cleanup 清干净 state，bot 没机制查 ContractDraft 历史）| **架构性 bug** |

### 1.3 根本诊断

修了一下午没修好的根源：**用 state 字段替代了 LLM 的判断力**。每个新跨轮场景（"复用上份" / 客户切换 / fingerprint 重发）都加一条硬编码规则——本质是把 LLM 本应做的语义理解工作硬塞进 deterministic 节点。

业界 2025 共识（基于本次调研，详见 §12）：从 **"我们设计流程让 LLM 走"** 切到 **"我们给 LLM 工具让它自己走"**。Anthropic / OpenAI / LangChain 三家口径一致：消息历史 + 工具调用是新默认；DAG workflow 不再推荐做对话型 agent。

## 2. 设计目标

1. **自然对话**：用户可任意措辞（"同样给得帆" / "上次那份再来一份" / "改一下数量为 8" / "撤销刚才的调价"），bot 自然理解并执行
2. **架构性 bug 永久消失**：删掉 30+ state 字段、子图节点链、cleanup 逻辑、所有跨轮硬编码规则
3. **加新意图成本 < 30 分钟**：增加 1 个新业务能力（如"采购单"）= 加一个 tool + prompt 描述
4. **代码量 -40%**：保留所有底层（DingTalk / ERP / docx 渲染 / ConfirmGate Lua）+ 重写流程层
5. **向后兼容**：DingTalk inbound/outbound handler / 权限 / 审计 / tool_call_log 不动

## 3. 核心架构决策

### 3.1 单一 ReAct agent

**决策**：删 router + 7 个子图 + AgentState 业务字段。换成 1 个 `langgraph.prebuilt.create_react_agent` + 16 tools（10 读 + 5 写 + 1 confirm_action）。

**社区依据**（OpenAI / Anthropic / LangChain 三家口径一致）：
- "Start with one agent, graduate to multi-agent only when you have data showing single agent fails"
- 16 tools 在生产实践安全区（业界经验线 `<15 安全 / 15-20 仍可用 / >20 推 supervisor`，本设计卡在中间区上沿）
- 7 个意图共享同一业务上下文（同用户/订单库/权限），强行 multi-agent 反而切割上下文增加错路由
- DeepSeek 非 SOTA → 架构越简单越好（多 agent 失败面叠加，单 agent 一层故障面）

### 3.2 chat 不是意图，是默认行为

LLM 不调任何 tool 时直接回复 = 闲聊。不需要 chat tool / chat subgraph。

### 3.3 读细写粗 tool 粒度

- **读类细粒度**（`search_*` / `get_*` / `list_*` / `check_*`）：让 LLM 自由组合多步推理（"查阿里 → 看 X1 库存 → 报价"）
- **写类粗粒度**（`create_contract_draft(customer_id, items, shipping)` 一次提交）：避免 LLM 中途丢字段

业界依据：Claude Code 同样设计（读用 Read/Glob/Grep 多个细工具，写用 Edit/Write 一次到位）。

### 3.4 plan-then-execute（写操作）

写 tool（如 `create_contract_draft`）被调时**不直接写副作用**，而是在 ConfirmGate
留 pending → 返 preview 给 LLM → LLM 把 preview 自然语言告诉用户 → 用户回"是"后
LLM 调 `confirm_action(action_id)` 真正执行。复用现有 `ConfirmGate` Redis Lua
原子语义（round 1-6 review 调过）。

依据：DeepSeek 长轨迹性能崩塌，写操作不能让 ReAct 自由探索（LangChain benchmark 数据）。

## 4. State Schema

**直接用 LangGraph 内置 `MessagesState`**（只有 `messages: list[BaseMessage]` 一个字段）。
hub 业务相关的 4 个 context 字段（hub_user_id / conversation_id / acting_as /
channel_userid）**不进 LangGraph state**，而是通过 ContextVar `tool_ctx` 在
ReActAgent.run() 入口 set，tool 内部 get。

```python
# react/agent.py 入口示意
from langgraph.prebuilt import create_react_agent

class ReActAgent:
    def __init__(self, ..., chat_model, tools, checkpointer):
        self.compiled_graph = create_react_agent(
            model=chat_model, tools=tools, prompt=SYSTEM_PROMPT,
            checkpointer=checkpointer,
            # 不传 state_schema — 用 LangGraph 内置 MessagesState 即可
        )

    async def run(self, *, user_message, hub_user_id, conversation_id, ...):
        token = tool_ctx.set(ToolContext(
            hub_user_id=hub_user_id, conversation_id=conversation_id,
            acting_as=acting_as, channel_userid=channel_userid,
        ))
        try:
            result = await self.compiled_graph.ainvoke(
                {"messages": [HumanMessage(content=user_message)]},
                config={"configurable": {"thread_id": ...}, "recursion_limit": 15},
            )
            ...
        finally:
            tool_ctx.reset(token)
```

**对比当前**：

| 维度 | v9 GraphAgent | v10 ReAct |
|---|---|---|
| LangGraph state schema | `AgentState` Pydantic 30+ 字段 | LangGraph 内置 `MessagesState` 仅 messages |
| 业务上下文 | state.customer / items / shipping / candidate_* / ... | 全部活在 `messages` 里（tool 调用 + 返值都进 messages） |
| Hub 内部 ctx（hub_user_id 等） | state 字段 | ContextVar `tool_ctx`（tool 内部读，不进 state） |

**关键转变**：
- customer / products / items / shipping / candidate_* / extracted_hints / missing_fields → 都活在 `messages` 里（LLM 看 messages 自己理解 + tool 调用结果也进 messages）
- 缺字段判断 → LLM 看 messages 自己说"还缺 XX，告诉我"
- 跨轮 reference → LLM 看 messages 自然理解（"上次那份" / "同样" 不需硬编码）

**ConfirmGate 仍存在**：但用 Redis 单独管理（已有），不进 LangGraph state。

### 4.1 Thread ID 命名空间

为避免新 React agent 反序列化旧 GraphAgent 留下的 PostgresSaver checkpoint（schema 完全不同 → JSON 反序列化会炸），新 thread_id 加 `react:` 前缀：

```python
# 旧 GraphAgent: thread_id = f"{conversation_id}:{hub_user_id}"
# 新 React agent:
thread_id = f"react:{conversation_id}:{hub_user_id}"
```

旧 GraphAgent 在 PostgresSaver 留下的 checkpoint 数据**不动**（保留作为审计/回滚），但 React agent 完全用新 namespace。生产切换时旧对话上下文会被丢弃（用户感知 = 一次"重启对话"）— 用户已经接受（决策 A：随便改）。

## 5. Tool 集合（16 个：10 读 + 5 写 + 1 confirm）

### 5.1 读类（10 个，细粒度）

按 hub 现有 `erp_tools` / `analyze_tools` 真实签名包装。**不**包装 adapter 上不存在的方法。

| Tool 名 | 签名 | 描述 |
|---|---|---|
| `search_customer` | `(query: str) -> list[dict]` | 按名称/电话搜客户。返 [{id, name, phone, address, ...}] |
| `search_product` | `(query: str) -> list[dict]` | 按名称/SKU/品牌搜商品。返 [{id, name, sku, brand, list_price, ...}] |
| `get_product_detail` | `(product_id: int) -> dict` | 商品详情（含各仓库存明细 + 库龄）|
| `check_inventory` | `(product_id: int) -> dict` | **单产品库存**（不按 brand）。看品牌全库存先 search_product 再批量调本 tool |
| `get_customer_history` | `(product_id: int, customer_id: int, limit: int=5) -> dict` | 客户最近 N 笔某商品成交（含历史价,谈判 / 报价参考）。**product_id 在前** |
| `get_customer_balance` | `(customer_id: int) -> dict` | 客户欠款 / 余额 / 信用额度 |
| `search_orders` | `(customer_id: int=0, since_days: int=30) -> dict` | 搜订单(customer_id=0 不过滤客户) |
| `get_order_detail` | `(order_id: int) -> dict` | 订单详情（含每行商品 / 数量 / 价格）|
| `analyze_top_customers` | `(period: str="近一月", top_n: int=10) -> dict` | 大客户销售排行（period 中文表达 "近一周" / "近一月" 等）|
| `get_recent_drafts` | `(limit: int=5) -> list[dict]` | **关键：当前会话最近合同草稿（仅 contract，YAGNI）**。返 [{draft_id, customer_id, customer_name, items, shipping, payment_terms, tax_rate, created_at}]。LLM 处理"同样/上次/复用"等表达 |

### 5.2 写类（5 个，粗粒度）

| Tool 名 | 签名 | 描述 |
|---|---|---|
| `create_contract_draft` | `(customer_id, items, shipping_address, shipping_contact, shipping_phone, payment_terms?, tax_rate?) -> dict` | 生成销售合同 docx 并发钉钉。items=[{product_id, qty, price}]。template_id 后端自动选 default sales 模板。 |
| `create_quote_draft` | `(customer_id, items, shipping?) -> dict` | 生成报价单 docx 并发钉钉 |
| `create_voucher_draft` | `(voucher_data: dict, rule_matched?: str)` | 生成财务凭证草稿挂会计审批 inbox（voucher_data 含 entries/total_amount/summary，**必须 confirm**）|
| `request_price_adjustment` | `(customer_id, product_id, new_price, reason)` | 提交客户专属价调整（admin 审批，**必须 confirm**）|
| `request_stock_adjustment` | `(product_id, adjustment_qty: float, reason, warehouse_id?: int)` | 提交库存调整（admin 审批，**必须 confirm**；adjustment_qty 正加负减）|

### 5.3 confirm 类（1 个）

| Tool 名 | 签名 | 描述 |
|---|---|---|
| `confirm_action` | `(action_id: str) -> dict` | **用户确认上一条 pending action 后调用**。本 tool 不创建新动作,只是触发上一条写 tool 返回的 pending 真正执行。详见 §6。 |

### 5.4 不在 tool 集合（YAGNI）

- ❌ `chat` / `reply` tool — LLM 默认行为，不需 tool
- ❌ `ask_user` tool — LLM 缺信息时直接生成自然语言询问（让 messages 流自然推进）
- ❌ `summarize_history` / 跨 conversation 记忆 tool — 每个钉钉 conversation 独立，PostgresSaver 已经按 thread_id 隔离

### 5.5 Tool 实现方式

每个 tool 通过 **`invoke_business_tool` helper** 调底层 `erp_tools` / `analyze_tools`
等业务函数（plan Task 2.0 实现）。helper 自动做：
- `require_permissions(perm)` — 权限 fail-closed
- `log_tool_call(...)` — 写 tool_call_log（admin 决策链审计）
- 注入 `acting_as_user_id` 等 ctx kwargs

```python
from langchain_core.tools import tool
from hub.agent.tools import erp_tools
from hub.agent.react.tools._invoke import invoke_business_tool


@tool
async def search_customer(query: str) -> list[dict]:
    """按名称/电话搜客户。返回 [{"id", "name", "phone", "address"}, ...]。"""
    result = await invoke_business_tool(
        tool_name="search_customers",
        perm="usecase.query_customer.use",
        args={"query": query},
        fn=erp_tools.search_customers,
    )
    if isinstance(result, dict):
        return result.get("items", [])
    return result or []
```

**ContextVar 过渡**：当前 worker.py 用 `_tool_ctx: ContextVar` 传 `hub_user_id /
acting_as / conversation_id` 给 tool 内部。新设计抽到 `react/context.py`（命名为
`tool_ctx`,无下划线前缀）,worker.py 改 import 路径但 ContextVar 实例及用法不变。
这样 erp_tools / generate_tools 等底层 tool 函数 0 改动。

## 6. ConfirmGate 集成（写操作 plan-then-execute）

### 6.1 现状

当前 `ConfirmGate` 是子图节点级别（`confirm_node` + `commit_*_node`）。Redis Lua 原子语义已经过 round 1-6 review 调稳。

### 6.2 新模式：tool wrapper

写 tool 调用流程拆两阶段：

**Phase 1：用户主动发起 → LLM 调写 tool**

详细实现见 plan Task 3.1（`_confirm_helper.create_pending_action`）+ Task 3.2
（write.py 中的 `_format_items_preview` 等 preview 工具）。下面是合同写 tool 的范式
（`use_idempotency` 仅用在 voucher / price / stock 三个写 tool 上 —— 见 §6.3 表
第 3-5 行）：

```python
from hub.agent.react.tools._confirm_helper import create_pending_action
from hub.agent.react.tools.write import _format_items_preview  # 共享 preview helper

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
    ctx = tool_ctx.get()
    # 1. 构造 canonical payload + summary（preview 给用户看的文案）
    payload = {
        "tool_name": "generate_contract_draft",  # 底层业务函数名
        "args": {
            "customer_id": customer_id, "items": items,
            "shipping_address": shipping_address,
            "shipping_contact": shipping_contact,
            "shipping_phone": shipping_phone,
            "payment_terms": payment_terms, "tax_rate": tax_rate,
        },
    }
    summary = (
        f"将给客户 id={customer_id} 生成合同：\n"
        f"  {_format_items_preview(items)}\n"
        f"  收货：{shipping_address} / {shipping_contact} {shipping_phone}"
    )

    # 2. 写 ConfirmGate pending（helper 内部调真 API gate.create_pending,返 PendingAction）
    #    use_idempotency 默认 False；voucher / price / stock 三个写 tool 传 True 复用同 PendingAction
    pending = await create_pending_action(
        subgraph="contract",  # PendingAction 必填字段（用作分类 / audit）
        summary=summary,
        payload=payload,
    )
    # 3. **不真正执行**，返回 preview 给 LLM —— 不返 token，token 由 confirm_action 反查
    return {
        "status": "pending_confirmation",
        "action_id": pending.action_id,
        "preview": summary,
    }
```

LLM 看到 `pending_confirmation` 自然把 preview 说给用户。

**Phase 2：用户回"是"/"确认" → LLM 调 confirm_action tool**

详细实现见 §6.3。简言之：`list_pending_for_context()` 找 PendingAction →
用 `pending.token` 调 `claim()` 原子消费 → 按 `payload["tool_name"]` 在
`WRITE_TOOL_DISPATCH` 找真业务函数 → 调用返结果。

**LLM 看到的契约**：
- 调写 tool → 拿到 `pending_confirmation` + `preview` + `action_id` → 把 preview 自然语言告诉用户
- 用户回"是"等确认词 → LLM 调 `confirm_action(action_id)` → 拿到执行结果（docx 已发）→ 报告用户

action_id 在 messages 里活着（LLM 看得到，下一轮还能引用）。不需要 state 字段。

### 6.3 confirm_action 内部 dispatch

ReAct 用 ConfirmGate 的 **PendingAction 路径**（v9 新 API），**不**用旧 ChainAgent 的
`claim_action` / `mark_confirmed` 协议（confirmed-hash 两步）。

下面伪代码用的是简化形式，**实际实施按 plan Task 3.4** —— dispatch 表 key 用**底层
函数名**（如 `generate_contract_draft` 不是 React tool 名 `create_contract_draft`），
value 用 3-tuple `(perm_code, fn, needs_action_id)`，`needs_action_id=True` 时
（voucher / price / stock）confirm_action 把当前 `action_id` 注入业务函数的
`confirmation_action_id` kwarg 做 DB 唯一约束。流程：

```python
# react/tools/confirm.py
from hub.agent.tools.confirm_gate import CrossContextClaim


WRITE_TOOL_DISPATCH = {
    "create_contract_draft": _execute_create_contract_draft,
    "create_quote_draft": _execute_create_quote_draft,
    "create_voucher_draft": _execute_create_voucher_draft,
    "request_price_adjustment": _execute_request_price_adjustment,
    "request_stock_adjustment": _execute_request_stock_adjustment,
}


@tool
async def confirm_action(action_id: str) -> dict:
    """用户确认上一条 pending action 后调用,触发真正执行。

    流程：
      1. list_pending_for_context() 找到当前 (conv, user) 下 action_id 对应 PendingAction
      2. 用 PendingAction.token 调 gate.claim() 原子消费（HDEL pending）
      3. 按 PendingAction.payload["tool_name"] 在 WRITE_TOOL_DISPATCH 找业务函数
      4. 调业务函数,返结果

    不调用旧 `claim_action` / `mark_confirmed` API（那是 ChainAgent 的两步协议）。
    """
    ctx = tool_ctx.get()
    gate = _gate()

    # 1. 找当前 (conv, user) 下的 PendingAction
    pendings = await gate.list_pending_for_context(
        conversation_id=ctx["conversation_id"],
        hub_user_id=ctx["hub_user_id"],
    )
    pending = next((p for p in pendings if p.action_id == action_id), None)
    if pending is None:
        return {"error": "action_id 不存在或已过期 — 请重新发起请求"}

    # 2. 原子 claim（HDEL pending）— 单次消费,跨 context / token 不匹配 / 过期均抛 CrossContextClaim
    try:
        await gate.claim(
            action_id=action_id,
            token=pending.token,
            hub_user_id=ctx["hub_user_id"],
            conversation_id=ctx["conversation_id"],
        )
    except CrossContextClaim as e:
        return {"error": f"action 失效: {e}"}

    # 3. dispatch 到真业务函数
    payload = pending.payload  # dict {tool_name, args}
    tool_name = payload.get("tool_name")
    args = payload.get("args") or {}
    fn = WRITE_TOOL_DISPATCH.get(tool_name)
    if fn is None:
        return {"error": f"不支持的 tool: {tool_name}"}

    # 4. 执行
    # **失败语义**：claim 已消费（HDEL 不可逆）。业务函数失败时不能 restore_action（那是
    # 旧 confirmed-hash 协议的工具）。靠业务函数自身幂等（generate_contract_draft 已有
    # fingerprint 幂等）+ 用户重发请求触发新 pending 来恢复。
    try:
        return await fn(**args)
    except Exception as e:
        return {"error": f"执行失败: {type(e).__name__}: {e}（请重发请求生成新草稿）"}
```

dispatch 表显式声明所有可 confirm 的写 tool。新增写 tool 时：
1. 加 LangChain `@tool` 函数（生成 `pending_confirmation`，通过 `_confirm_helper.create_pending_action(subgraph=..., summary=..., payload=..., use_idempotency=...)` 写 pending）
2. 加 `_execute_*` 内部函数（实际执行业务）
3. 在 `WRITE_TOOL_DISPATCH` 注册映射

**5 个写 tool 对应的 subgraph + use_idempotency 配置**：

| Tool | subgraph | use_idempotency | 备注 |
|---|---|---|---|
| create_contract_draft | "contract" | False | 底层 generate_contract_draft 已有 fingerprint 幂等 |
| create_quote_draft | "quote" | False | 底层 generate_price_quote 已有 fingerprint 幂等 |
| create_voucher_draft | "voucher" | True | 同 args 必须复用 PendingAction（DB 唯一约束靠 confirmation_action_id） |
| request_price_adjustment | "adjust_price" | True | 同 (customer, product, price) 重发必须复用 PendingAction |
| request_stock_adjustment | "adjust_stock" | True | 同 (product, qty) 重发必须复用 PendingAction |

`use_idempotency=True` 时 helper 用 `_canonical_idempotency_key(payload)` 给
ConfirmGate 传 idempotency_key，第二次同 args 调用直接复用第一次的 PendingAction
（同 action_id），不会重复占库存额度 / 不会撞 DB 唯一约束。

### 6.4 ConfirmGate 安全机制保留

- Redis Lua 原子 claim（防双发）
- token 防伪（防跨 action 复用）
- 幂等 dedupe（同 args 同 fingerprint 不重发 — 注意 fingerprint **不再** short-circuit
  跳过 send_file，commit `0e6b71d` 已修；幂等只是 DB 复用同一 draft.id 防爆审计行）
- TTL EXPIRE NX+GT（pending 不缩短 TTL）

全部不动，只换调用入口。

## 7. 数据流（典型场景 trace）

### 7.1 场景：单轮做合同

```
User: "给翼蓝做合同 X1 10 个 300，地址北京海淀，张三 13800001111"
  ↓ DingTalk Handler → ReAct agent.invoke({messages: [user_msg], hub_user_id, ...})
  ↓ LLM 看 messages，决定调 search_customer("翼蓝")
  ↓ tool 返回 [{id:7, name:"北京翼蓝科技..."}]
  ↓ LLM 决定调 search_product("X1")
  ↓ tool 返回 [{id:1, name:"X1", list_price:...}]
  ↓ LLM 决定调 create_contract_draft(customer_id=7, items=[{1,10,300}], shipping=...)
  ↓ tool 返回 {status:"pending_confirmation", action_id:"...", preview:"将给翼蓝..."}
  ↓ LLM 输出："将给翼蓝生成合同：X1×10 单价 300，收货北京海淀。确认请回'是'。"
  ↓ DingTalk 发出
User: "是"
  ↓ Handler → agent.invoke (messages 含上轮历史 + "是")
  ↓ LLM 看到 messages 里 action_id，调 confirm_action(action_id)
  ↓ tool 调真 generate_contract_draft → 渲染 docx → 发钉钉
  ↓ tool 返回 {draft_id:42, file_sent:True}
  ↓ LLM 输出："合同已生成并发送，draft_id=42。"
```

### 7.2 场景："同样给得帆做一份"（之前最痛 case）

```
[T1 已完成翼蓝合同]
User: "同样的内容给得帆也做一份"
  ↓ agent.invoke (messages 含 T1 完整历史)
  ↓ LLM 看 messages — 看到上轮 create_contract_draft 调用 + 结果
  ↓ LLM 决定调 get_recent_drafts(limit=1)
  ↓ tool 返回 [{customer:翼蓝, items:[{X1,10,300}], shipping:北京海淀,...}]
  ↓ LLM 决定调 search_customer("得帆")
  ↓ tool 返回 [{id:11, name:"广州市得帆..."}]
  ↓ LLM 决定调 create_contract_draft(customer_id=11, items=<上份>, shipping=<上份>)
  ↓ pending_confirmation → preview → 用户确认 → 执行
```

**零硬编码规则**。"前面那份 / 上次那个 / 翼蓝那份给得帆 / 复制上次" 任何措辞都自然处理。

### 7.3 场景：闲聊

```
User: "在吗"
  ↓ agent.invoke
  ↓ LLM 不调任何 tool，直接生成回复："在的，有什么可以帮你？"
```

### 7.4 场景：缺字段

```
User: "做合同 X1 10 个"
  ↓ LLM 看消息，发现没说客户、没价格、没地址
  ↓ LLM 直接回复："好的，给哪个客户做合同？X1 单价多少？收货地址、联系人、电话？"
（注意：不调 tool，因为没足够信息可执行；不需要 ask_user tool；
  不需要 missing_fields state 字段）
User: "翼蓝，单价 300，地址北京海淀张三 138..."
  ↓ LLM 看完整 messages（含上轮"做合同 X1 10 个"+ 本轮补字段）
  ↓ 调 search_customer("翼蓝") + create_contract_draft(...)
```

## 8. 错误处理 + 失败模式缓解

基于业界已知 ReAct 失败模式（详见 §12 调研）：

| 失败模式 | 缓解 | 实现 |
|---|---|---|
| 工具名幻觉（DeepSeek V3 81.5% function calling） | tool 名/描述写死"动词+对象"模板；strict schema 校验 | LangChain `@tool` 自动生成 schema；errors 走 strict 验证 |
| 死循环重试 | `recursion_limit=15` hard limit | LangGraph 0.2.x：`recursion_limit` 不是 `create_react_agent()` 的 kwarg, 而是在 `compiled_graph.ainvoke({...}, config={"recursion_limit": 15, ...})` 里传 |
| 长轨迹性能崩塌 | 写操作 plan-then-execute（必经 confirm 中转）| §6 ConfirmGate wrapper |
| token 爆炸 | tool 描述精简 1-2 行；闲聊不进 tool 集合；DeepSeek prompt cache | system prompt 总长 < 2K tokens |
| ERP 调用错误 | tool 内部捕获 + 返友好 error dict（不 raise） | 沿用现有 erp_tools 错误处理 |
| ConfirmGate token 失效 | tool 返 `{"error": "action 已失效，请重新发起"}` | 沿用现有 Lua 脚本 |

**recursion_limit 触发**：返回 `{"error": "推理步骤超限，请简化请求或联系管理员"}`，不让 agent 卡死。

## 9. 测试策略

### 9.1 必须保留的测试

- `test_tool_registry.py`（44 case，业务底层不变）
- `test_generate_tools.py`（22 case，docx 渲染 / ConfirmGate 入口）
- `test_inbound_handler_with_agent.py`（DingTalk handler 接口）
- ERP4 adapter / 权限 / 审计相关单测

### 9.2 替换的测试

删除：
- `tests/agent/test_node_*.py`（节点级测试）
- `tests/agent/test_subgraph_*.py`（7 个子图测试）
- `tests/agent/test_graph_agent.py`（GraphAgent 主类测试）
- `tests/agent/test_graph_router_accuracy.py`
- `tests/agent/test_graph_state.py`

新增：
- `tests/react/test_react_agent.py` — agent 主入口
- `tests/react/test_tools.py` — 16 个 tool 单测（mock erp_adapter + confirm_gate）
- `tests/react/test_confirm_wrapper.py` — write tool plan-then-execute 流
- `tests/react/test_acceptance_scenarios.py` — yaml 场景测试（保留 6 个 story 但断言放宽：检查最终 tool 调用 + 业务结果，不检查节点路径）

### 9.3 加 acceptance 场景

钉钉实测痛点全部加入 yaml fixture：
- "同样给得帆做一份" → 期望调 `get_recent_drafts` + `search_customer` + `create_contract_draft`
- 客户切换："给翼蓝再做一份" → 期望新客户 customer_id
- fingerprint 重发 → 期望 `send_file` 被调用
- "在吗" → 期望不调任何 tool
- 缺字段 → 期望 LLM 自然语言询问（不卡死）

### 9.4 真 LLM eval（@pytest.mark.realllm）

保留现有 6 个 story yaml，但断言改成：
- ✅ 最终调对了写 tool（如 `create_contract_draft`）
- ✅ tool args 正确（customer_id / items / shipping）
- ✅ ConfirmGate pending 有创建
- ❌ 不再断言节点路径（`pre_router → router → contract` 这种）
- ❌ 不再断言 state 字段值（`state.customer.name == 'xxx'`）

## 10. 保留 vs 删除清单（文件级）

### 10.1 保留（不动）

```
backend/hub/agent/llm_client.py                # DeepSeek 客户端（旧 GraphAgent 用,react 不再用但 import 保留）
backend/hub/agent/tools/erp_tools.py           # ERP 业务函数实现
backend/hub/agent/tools/generate_tools.py      # 合同/报价 docx 渲染 + send 钉钉
backend/hub/agent/tools/draft_tools.py         # 调价/调库存/凭证 草稿
backend/hub/agent/tools/analyze_tools.py       # top customers 分析
backend/hub/agent/tools/confirm_gate.py        # Redis Lua 原子语义
backend/hub/agent/tools/registry.py            # 仍保留（被 erp_tools 等内部用）
backend/hub/agent/tools/types.py               # ToolType / 异常类
backend/hub/agent/document/contract.py         # docx 渲染引擎
backend/hub/agent/prompt/                       # 现有 prompt 文件保留供参考（react 用新 prompt）
backend/hub/handlers/dingtalk_inbound.py       # 入口 handler（react agent .run() 接口兼容）
backend/worker.py                              # 启动入口（改：构造 ReActAgent 替 GraphAgent）
backend/hub/integrations/dingtalk/             # 钉钉 SDK 封装
backend/hub/erp4/                              # ERP4 adapter
backend/hub/auth/ + permissions/               # 权限
backend/hub/observability/                     # 审计 / log
backend/hub/models/                            # DB schema
backend/migrations/                            # DB migration
```

### 10.2 精简（保留文件，删除业务字段）

```
backend/hub/agent/graph/state.py               # 删 AgentState 业务字段（30+ 个），
                                                # 保留 Intent / CustomerInfo / ProductInfo /
                                                # ContractItem / ShippingInfo（5 个 BaseModel）
                                                # 给 react tools 内部 type 用
```

### 10.3 删除

```
backend/hub/agent/graph/agent.py               # GraphAgent 主类
backend/hub/agent/graph/router.py              # router_node
backend/hub/agent/graph/config.py              # build_langgraph_config
backend/hub/agent/graph/nodes/*.py             # 所有节点（extract / resolve / parse / validate /
                                                # ask_user / format_response / cleanup / pre_router / confirm）
backend/hub/agent/graph/subgraphs/*.py         # 7 个子图

backend/tests/agent/test_node_*.py             # 节点级测试
backend/tests/agent/test_subgraph_*.py         # 子图测试
backend/tests/agent/test_graph_agent.py        # GraphAgent 主类测试
backend/tests/agent/test_graph_state.py
backend/tests/agent/test_graph_router_accuracy.py
backend/tests/agent/test_acceptance_scenarios.py  # 重写为 react 版（fixture yaml 保留）
backend/tests/agent/test_realllm_eval.py       # 重写为 react 版
```

### 10.4 新建

```
backend/hub/agent/react/__init__.py            # 包入口
backend/hub/agent/react/context.py             # ContextVar tool_ctx 管理
backend/hub/agent/react/llm.py                 # DeepSeek → LangChain ChatModel 适配
backend/hub/agent/react/agent.py               # ReActAgent 主类（封装 create_react_agent）
backend/hub/agent/react/prompts.py             # system prompt
backend/hub/agent/react/tools/__init__.py      # re-export ALL_TOOLS
backend/hub/agent/react/tools/_invoke.py       # invoke_business_tool helper（统一 require_permissions + log_tool_call + ctx 注入）
backend/hub/agent/react/tools/read.py          # 10 个 read tool
backend/hub/agent/react/tools/write.py         # 5 个 write tool（plan 阶段）
backend/hub/agent/react/tools/_confirm_helper.py  # ConfirmGate 注入 + create_pending_action（含 idempotency_key）
backend/hub/agent/react/tools/confirm.py       # confirm_action + WRITE_TOOL_DISPATCH

backend/tests/react/__init__.py
backend/tests/react/conftest.py                 # 共用 fixtures（mock erp_adapter / gate / sender / ReAct fixtures）
backend/tests/react/test_context.py
backend/tests/react/test_llm.py
backend/tests/react/test_prompts.py
backend/tests/react/test_invoke.py             # invoke_business_tool helper 单测
backend/tests/react/test_tools_read.py
backend/tests/react/test_tools_write.py
backend/tests/react/test_confirm_wrapper.py
backend/tests/react/test_react_agent.py
backend/tests/react/test_react_agent_e2e.py    # fake LLM + fakeredis ConfirmGate 端到端
backend/tests/react/test_acceptance_scenarios.py
backend/tests/react/test_realllm_eval.py
backend/tests/react/fixtures/scenarios/*.yaml  # 复用现有 6 story + 4 新增场景
```

## 11. 不在 scope（YAGNI）

- ❌ 跨 conversation 记忆（不同钉钉群/私聊不互通）
- ❌ Message compaction / 长对话摘要（钉钉对话短，不会爆 token）
- ❌ Multi-agent supervisor（16 tools 在 15-20 仍可用区间内，单 agent 足够）
- ❌ Letta / Mem0 / LangMem 长期记忆（用户偏好 / 常用客户 — 真有需求再上）
- ❌ LangSmith trace / observability platform（tool_call_log 已有）
- ❌ 切换 LLM provider（先继续 DeepSeek，效果差再说）
- ❌ Streaming 响应（钉钉机器人本来就异步，不需要）
- ❌ 重写 ConfirmGate Lua 脚本（已经过 round 1-6 review 调稳）
- ❌ 重写 ERP4 adapter / DingTalk SDK
- ❌ 写"复用上份"的硬编码关键词检测（让 LLM + `get_recent_drafts` tool 自然处理）

## 12. 调研依据

业界 2025 主流做法（详见会话调研记录）：

**核心 pattern**：
- Tool-Loop / 薄 harness（Claude Code、Claude Agent SDK）
- Just-in-Time Context（Anthropic 官方推荐：prompt 不预塞数据，LLM 用 tool 按需 fetch）
- Layered Memory（Letta / Mem0 / LangMem，跨 session 长期记忆——本设计不上）

**官方立场**（一致）：
- Anthropic："start with one agent first"
- OpenAI："maximize a single agent's capabilities first"
- LangChain："不再推荐纯 DAG 做对话型 agent，新默认是 create_react_agent + interrupt + Store"

**关键参考**：
- Anthropic [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- Anthropic [Building agents with the Claude Agent SDK](https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk)
- LangGraph [Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- LangGraph [Memory overview](https://docs.langchain.com/oss/python/langgraph/memory)
- LangChain [Multi-Agent Orchestration: Supervisor vs Swarm](https://dev.to/focused_dot_io/multi-agent-orchestration-in-langgraph-supervisor-vs-swarm-tradeoffs-and-architecture-1b7e)
- LangChain [Benchmarking Multi-Agent Architectures](https://blog.langchain.com/benchmarking-multi-agent-architectures/)
- LangChain [LangGraph ReAct Production Lessons 2025](https://latenode.com/blog/ai-frameworks-technical-infrastructure/langchain-setup-tools-agents-memory/langchain-react-agent-complete-implementation-guide-working-examples-2025)
- OpenAI [Agents SDK Orchestration and handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration)
- DeepSeek [V3 Function Calling Evaluation #1108](https://github.com/deepseek-ai/DeepSeek-V3/issues/1108)（V3 函数调用 81.5% — 决定 ReAct max_iter / strict schema 设计）

**单 agent vs multi-agent 阈值**：tool 数 < 15 直接选 / 15-20 仍可单 agent / > 20 推 supervisor。7 个意图共享同一业务上下文（同用户/订单/权限）→ 不应该 multi-agent；DeepSeek 非 SOTA → 简单架构最稳。本设计 16 tools（10 读 + 5 写 + 1 confirm）在中间区上沿，单 agent 正解。

## 13. 已知风险 + 缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| LLM 调错 tool / 不调 tool | 中 | tool 描述精简 + 写好 system prompt + acceptance evals 跑回归 |
| DeepSeek 长轨迹退化 | 中 | recursion_limit=15 + 写操作 plan-then-execute |
| token 消耗增加 | 低 | DeepSeek prompt cache + tool 描述 1-2 行 + 闲聊不进 tool |
| 旧 PostgresSaver checkpoint 反序列化失败（schema 变了）| 低 | 新 thread_id 命名空间（`react:` 前缀），跟旧 GraphAgent 隔离 |
| ConfirmGate 集成 bug | 低 | 复用现有 Lua 脚本 + plan-then-execute 单测覆盖 |
| ConfirmGate Redis 不可用导致 ReAct 第一轮抛非 BizError | 低 | 现有 `dingtalk_inbound` `except Exception → fallback RuleParser`（chat-only 降级路径）保留；ReAct 异常自动走老降级路径 |
| `tool_call_log.round_idx` 退化为 0 | 低 | v9 GraphAgent 每轮 LLM call 递增 round_idx 给 admin 决策链排序；ReAct 一轮 ainvoke 内部多次 LLM call 共享同 round_idx=0（messages 已含完整顺序）。admin UI 改用 `created_at` 排序即可，不算 bug |
| 用户体验过渡期不稳 | 中 | 4-5 天内开发完毕,过渡期旧 GraphAgent 在 git 历史可回滚（不主动保留）|

## 14. 成功标准

实施完成时：

- ✅ 钉钉机器人能自然处理 §7 全部 4 个场景（单轮做合同 / "同样给 X" / 闲聊 / 缺字段询问）
- ✅ 全量 tests 通过（含新加 acceptance 场景）
- ✅ 代码量减少 40% 以上（删 graph/* + nodes/* + subgraphs/* 大头）
- ✅ 加新意图 < 30 分钟（手工测："新增'采购单'功能"，加一个 tool + prompt 描述即可）
- ✅ 真实钉钉测试一下午无新出 bug
