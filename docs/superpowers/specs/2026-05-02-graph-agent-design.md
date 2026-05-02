# Plan 6 v9 — GraphAgent 架构重构 Design Spec（v3）

**日期**：2026-05-02
**作者**：Claude Opus 4.7（与产品 owner 林炼豪共同 brainstorming）
**状态**：Design 阶段（v3 — 完整通读 DeepSeek 14 个章节后重写）
**预估工时**：8 天（约 1.5 周）
**前置 commits**：截至 `af96a93`（v8 staging review patch 收尾）

---

## 0. 背景与动机

Plan 6 v1-v8 在 staging 验收期一直在用 **patch-style prompt engineering** 修 LLM 行为问题。截至 v8，行为准则已堆到 12 条（3a-3l），但用户实测仍频繁出现：

- 反复确认 / 调多余 tool / 不主动推进流程 / 编造 ID

**根本原因**：当前 ChainAgent 是 **single LLM loop + 17 tool 全挂 + 长 system prompt** 架构。在 17 tool + 6 业务流程下 LLM 注意力分散导致行为不一致。

**v2 重要发现**（系统读完 DeepSeek 官方文档后）：
我们之前没有充分利用 DeepSeek 提供的 4 个**架构级**特性：

1. **KV Cache 命中价 ¥0.02 / 未命中 ¥1（差 50 倍）** → 必须设计 prompt 前缀稳定化
2. **Chat Prefix Completion**（独家）→ 强制 LLM 输出指定格式开头
3. **Strict mode**（beta）→ 强制 JSON schema 类型校验，根除"LLM 传错参数"类 bug
4. **1M token context**（不是 128K）→ 根本不需要激进截断

**重构目标**：参考行业标准（Anthropic Routing pattern）+ LangGraph 框架 + **充分利用 DeepSeek 4 个架构特性**，让对话像主流 LLM 一样自然，从代码层根除上面的 bug，月成本控制在 ¥500-1.5K（cache 命中后）。

**真实预期**：
- 重构能根治"流程类"bug 80%（重复确认 / 调多余 tool / 不推进）
- 重构**不能根治** "模型能力类"问题（数字幻觉 / 复杂语义算错）
- 用 deepseek-v4-flash 重构后体感**接近 GPT-4o**，但**达不到 ChatGPT/Claude 体验**
- 想达到主流 LLM 体验需要重构 **+ 模型升级**（v4-pro / Sonnet 4.5）

---

## 1. DeepSeek API 特性深度利用（v2 新增核心章节）

这是 v2 的关键增量。**所有架构决策都围绕这 5 个特性展开**：

### 1.1 KV Cache（成本核心）

**事实**：
- 命中价 ¥0.02/1M tokens vs 未命中 ¥1/1M tokens（**差 50 倍**）
- 命中规则：**完整匹配前缀单元**（部分匹配不算）
- response 字段 `usage.prompt_cache_hit_tokens` + `prompt_cache_miss_tokens` 可监控
- 多轮对话天然有公共前缀

**对架构的强约束 — Prompt 前缀稳定化**：

```
[messages 序列前缀稳定化布局]
   ┌─────────────────────────────────────┐
   │ ✅ system prompt（永不变，最大命中） │  cache 命中 → 几乎免费
   │   - 业务词典                        │
   │   - 同义词                          │
   │   - 子图固定的行为提示              │
   ├─────────────────────────────────────┤
   │ ✅ tools schema（子图固定）         │  cache 命中 → 几乎免费
   ├─────────────────────────────────────┤
   │ ✅ few-shot examples（子图固定）    │  cache 命中 → 几乎免费
   ├─────────────────────────────────────┤
   │ ⚠️ round_state（user 段开头，不进 system）│  跨轮变 → cache miss
   ├─────────────────────────────────────┤
   │ ❌ 历史 messages（每轮变）          │  cache miss
   ├─────────────────────────────────────┤
   │ ❌ 当前 user_message（最易变）      │  cache miss
   └─────────────────────────────────────┘
```

**强约束 → spec 必须遵守**：

- **system prompt 必须完全静态**：不能塞 user_id / timestamp / 动态业务数据
- **round_state 摘要必须放 user 段**（not system 段）— 不破坏 system 前缀
- 每个子图自己的 system prompt 固定 → 同子图多次调用 cache 命中
- **不混用全局 system prompt + 子图 prompt**（前者变了破坏所有缓存）

**预期成果**：
- cache 命中率 ≥ 80%（业务词典 + 同义词 + few-shots 永远稳定，占输入大头）
- 月成本从原 ¥3K 降到 ≤ ¥1K
- 省下来的预算**可以升级 v4-pro 模型**或保留作 buffer

### 1.2 Chat Prefix Completion（架构级武器）

**功能**：`messages` 末尾 assistant message 加 `prefix=true` + content，LLM 从 content 续写。

**3 个核心应用**：

#### 应用 A：Router 强制 JSON 输出（替代 JSON mode）

DeepSeek JSON mode 文档明确说"API has probability of returning empty content"。我们改用更稳的 prefix：

```python
messages = [
    {"role": "system", "content": ROUTER_PROMPT},
    {"role": "user", "content": "给阿里写讯飞 x5 合同"},
    {"role": "assistant", "content": '{"intent": "', "prefix": True}
]
# stop=['",'] 截断到 intent 字段
# LLM 必须从 '{"intent": "' 续写 → 物理不可能输出"我觉得是..."
```

#### 应用 B：BOT 输出风格强制

```python
# 防止 LLM 输出"请问要不要继续做合同？"这种重复确认
{"role": "assistant", "content": "已为您生成", "prefix": True}
# LLM 必须从"已为您生成"开始，不能 ask user
```

#### 应用 C：避免冗长开场白

```python
# 防止 LLM 输出 "好的，根据您提供的信息..."
{"role": "assistant", "content": "合同生成成功：", "prefix": True}
```

**约束**：必须用 beta endpoint `https://api.deepseek.com/beta`。

### 1.3 Strict Mode（function calling 强约束）

**功能**：`base_url=.../beta` + tool schema 加 `strict: true`，LLM 输出**严格遵守** JSON schema。

**正好命中之前的 bug**：
- LLM 把 `extras` 传成字符串 → ✅ strict 拒绝（强制 dict）
- LLM 漏传 required 字段 → ✅ strict 拒绝
- LLM 编造未定义字段 → ✅ strict 拒绝（`additionalProperties: false`）

**Schema 改造代价**：所有 17 个 tool 的 schema 必须满足：
- 所有 properties 必须 `required`（即使可选字段也要列出 + 用 `null` union）
- 顶层 `additionalProperties: false`
- 不能用 `minLength` / `maxLength` / `minItems` / `maxItems`（strict 不支持）

**预期成果**：彻底消灭"LLM 传错参数类型"这类 bug（占当前 patch 准则约 30%）。

### 1.4 1M Context Window（不再激进截断）

**事实**：v4-flash 支持 1M token input + 384K output。

**对架构的影响**：
- 旧 ChainAgent 的 `MAX_PROMPT_TOKEN = 32K` 完全不必要
- LangGraph state checkpoint 不用担心爆 token
- round_state + 历史可以保留更全（提升 LLM 理解上下文质量）

**实际工程值**：100K input budget（远少于 1M 但远多于旧 32K），保留余量给输出 + cache 命中波动。

### 1.5 Thinking Mode（战略性使用）+ Reasoner 区分

**两个易混的概念，必须区分**：

| 概念 | 模型 | 支持 function calling | 价格 |
|---|---|---|---|
| **`deepseek-reasoner`**（DeepSeek-R1 独立模型）| 单独 model name | ❌ **不支持** | input ¥1-4, output ¥16/1M（贵 8x）|
| **`v4-flash` + `thinking={"type":"enabled"}`** | v4-flash 子模式 | ⚠️ 文档未明确，需 M0 验证 | 看 v4-flash 价（同档）|

**v3 决策**：
- **不用** `deepseek-reasoner`（不支持 function calling，agent 场景废了）
- **谨慎用** v4-flash thinking 模式：M0 必须先验证「v4-flash + thinking + tools」三者能同时启用
  - 如果**可以同时**：按下表战略性开启
  - 如果**不能同时**：所有需要 tool 的节点都关 thinking；只在不调 tool 的纯文本节点开

**何时用（前提：M0 验证可同时启用 tools）**：

| 子图 / 节点 | thinking | 调 tool | 理由 |
|---|---|---|---|
| router | ❌ 关 | 否 | 简单分类，开了浪费 |
| chat | ❌ 关 | 否 | 闲聊不需要推理 |
| query 子图 | ❌ 关 | 是 | 单步查询 |
| **contract.validate_inputs** | ✅ 开 | 否（纯逻辑判断）| 价格合理性 / items 完整性需要推理；不调 tool 所以不受 thinking+tool 兼容性约束 |
| contract.resolve_customer/products | ❌ 关 | 是 | 单步搜索 |
| contract.generate_contract | ❌ 关 | 是 | 直接调 generate tool |
| **adjust_price.preview** | ✅ 开 | 否 | 比较新旧价 / 客户历史需要推理 |
| voucher / adjust_stock 关键节点 | 看 M0 验证 | 是 | 财务判断重要但要调 tool |
| format_response | ❌ 关 | 否 | 模板化输出 |

**多轮对话的 reasoning_content 处理**（关键陷阱）：
- DeepSeek 文档明确：assistant message append 回 messages 时**只 append `content` 字段，不能含 `reasoning_content`**，否则 400
- SessionMemory.append 必须**剥离 reasoning_content**

**成本影响**：thinking 模式 output token 增多，cost +30-50%。仅在 2-3 个纯逻辑判断节点开。

### 1.6 Temperature 矩阵（按 DeepSeek 官方推荐）

DeepSeek 推荐每场景不同 temperature：

| 节点类型 | Temperature | DeepSeek 官方场景 |
|---|---|---|
| router 意图分类 | 0.0 | 数据提取（确定性最重要）|
| query 子图（搜索 / tool） | 0.0 | 数据提取 |
| contract / quote / voucher 节点（tool）| 0.0 | 数据提取（结果一致性）|
| **chat 子图**（闲聊 / 反问澄清）| **1.3** | 通用对话（DeepSeek 推荐） |
| format_response 节点 | 0.7 | 中间值（模板化但需要轻微变化）|

**当前 ChainAgent 全用 0.0**，导致 chat 类回复僵硬不像聊天。新架构按节点配置。

### 1.7 Finish Reason 完整处理

5 种 finish_reason 都要处理：

| finish_reason | 含义 | 处理 |
|---|---|---|
| `stop` | 正常完成 | 取 content |
| `length` | 撞 max_tokens | 截断告警 + 让 LLM 续写或缩短 prompt |
| `tool_calls` | 触发 tool 调用 | 走 tool 执行节点 |
| `content_filter` | 内容审核拒绝 | 友好告知用户 + 记录 |
| **`insufficient_system_resource`** | DeepSeek 系统资源不足 | **当 503 处理**（重试 + 退避）|

llm_client 必须显式处理这 5 种，不要 default fallback。

### 1.8 速率限制 / 超时配置

DeepSeek 是**动态速率**（无固定 RPM/TPM）。规则：

- 高负载时返 429 / 503 / `insufficient_system_resource`
- 长请求自动 keep-alive（不要把空行 / `: keep-alive` 当错误）
- **10 分钟未开始处理才超时**（不是 30s/45s）

**llm_client 必须改**：
- timeout 30/45s → **600s**（10 分钟）
- retry backoff 改成指数退避（高负载时多次快速重试只加剧 429）：1.5s → 5s → 15s → 60s
- `insufficient_system_resource` 加入可重试列表
- keep-alive 字符（空行 / `:`）当作正常等待，不是异常

### 1.9 Tool_choice 强制策略

DeepSeek 支持 4 种 `tool_choice`：

```python
"none"       # 禁止调 tool
"auto"       # LLM 自主决定
"required"   # 必须调某个 tool
{"type": "function", "function": {"name": "search_customers"}}  # 强制调指定 tool
```

**v3 在某些节点用 "specific name" 强制调用**：
- `contract.resolve_customer` → `tool_choice = {"name": "search_customers"}` 强制只调这个
- `contract.resolve_products` → `tool_choice = "required"` 必须调（但允许多次）
- 防止 LLM 跳过解析步骤

### 1.10 OpenAI 兼容性

> 「DeepSeek API interface is compatible with OpenAI」

意味着 **LangGraph / LangChain 直接可用**（基于 OpenAI 协议）。无需写适配层。
LangGraph 用 `ChatOpenAI(base_url=DEEPSEEK_BETA_URL, ...)` 即可。

---

## 2. 架构总览

```
钉钉 inbound message
       ↓
┌─────────────────────────────────────────────────────────┐
│            GraphAgent（基于 LangGraph）                  │
│            base_url = https://api.deepseek.com/beta     │
│                                                         │
│            ┌──────────────┐                             │
│            │   router     │ ← 轻量 LLM + prefix JSON     │
│            │ (no thinking)│   thinking 关，强 cache 命中  │
│            └──────┬───────┘                             │
│                   │                                     │
│   ┌───────────────┼─────────────────────────────────┐   │
│   ↓               ↓                                 ↓   │
│ ┌────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌─────┐ ┌──┐ │
│ │chat│ │query │ │contract│adjust│ │voucher│adjust│ │  │ │
│ │(短)│ │(只读)│ │(strict│price │ │(strict│stock │ │..│ │
│ │    │ │     │ │+think) │+think│ │+think)│+think│ │  │ │
│ └────┘ └──────┘ └──────┘ └──────┘ └──────┘ └─────┘ └──┘ │
│                                                         │
│ 每子图 prompt 静态稳定 → KV cache 命中率 ≥ 80%          │
│ 每子图 tool 数 0-11 个，平均 3-4（vs 旧架构 17 全挂）    │
└─────────────────────────────────────────────────────────┘
       ↓
SessionMemory（Redis，per-user 隔离）
ConversationLog（Postgres，复合 unique）
```

**关键差异 vs 旧 ChainAgent**：

| 维度 | 旧（ChainAgent） | 新（GraphAgent v2）|
|---|---|---|
| 流程控制 | LLM 自己决定 | 代码控制 state machine |
| Tool 数 / 调用 | 17 个全挂 | 平均 3-4 / 子图 |
| Prompt 长度 | system + 12 准则 ≈ 4K token | 子图特定 ≤ 2K |
| **Cache 命中** | **跨轮 system 含动态值，几乎不命中** | **system 完全静态，命中率 ≥ 80%** |
| **JSON 输出可靠性** | 靠 prompt 鼓励 | **prefix 物理强制** |
| **Tool 参数类型** | 靠 prompt 提醒 | **strict mode 强制** |
| 跨轮状态 | 都塞 prompt | Pydantic state + LangGraph checkpoint |

---

## 3. 节点拆分（contract_subgraph 示例）

```
contract_subgraph:
   ┌─────────────────────┐
   │ 1. resolve_customer │ ← LLM + tool: search_customers
   │    thinking: off    │
   └──────┬──────────────┘
          ↓
   ┌──────────────────────┐
   │ 2. resolve_products  │ ← LLM + tool: search_products（多次并行）
   │    thinking: off     │
   └──────┬───────────────┘
          ↓
   ┌──────────────────────┐
   │ 3. validate_inputs   │ ← LLM + thinking on
   │    thinking: on      │   推理价格 / 数量 / items 完整性
   └──────┬───────────────┘
          ↓ (有缺失) → ┌─────────────┐
          ↓            │ 4a. ask_user │ → END
          ↓            └─────────────┘
   ┌──────────────────────┐
   │ 4b. generate_contract│ ← 调 generate_contract_draft（strict tool）
   │    thinking: off     │
   └──────┬───────────────┘
          ↓
   ┌──────────────────────┐
   │ 5. format_response   │ ← LLM + prefix「合同已生成」
   │    thinking: off     │
   └──────────────────────┘
```

**节点设计原则**（v2 强化）：

- **每节点单 LLM 调用 + 单一职责**
- **跨节点 state 用 Pydantic typed**
- **流转条件用代码（不在 prompt 鼓励）**
- **prompt 完全静态**（让 KV cache 命中）
- **关键决策节点开 thinking**，简单节点关 thinking
- **写 tool 用 strict mode**，禁止 LLM 瞎传参数
- **format_response 用 prefix**，强制 BOT 输出风格

---

## 4. State Schema（Pydantic typed）

```python
# state.py
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

class ContractState(BaseModel):
    """contract_subgraph 跨节点共享状态。"""
    user_message: str
    hub_user_id: int
    conversation_id: str
    extracted_hints: dict

    customer: CustomerInfo | None = None
    products: list[ProductInfo] = []
    items: list[ContractItem] = []
    shipping: ShippingInfo = ShippingInfo()

    missing_fields: list[str] = []
    errors: list[str] = []

    final_response: str | None = None
    file_sent: bool = False
    draft_id: int | None = None
```

---

## 5. Tool 重组 + Strict Mode 改造

### 5.1 子图挂载分布

| Subgraph | Tool 数 | 挂载的 tool |
|---|---|---|
| router | 0 | （用 prefix JSON）|
| chat | 0 | （直接 LLM 文本）|
| query | 11 | search_products / search_customers / get_customer_history / check_inventory / search_orders / get_order_detail / get_customer_balance / get_inventory_aging / get_product_detail / analyze_top_customers / analyze_slow_moving_products |
| contract | 4 | search_customers / search_products / get_customer_history / generate_contract_draft |
| quote | 3 | search_customers / search_products / generate_price_quote |
| voucher | 3 | search_orders / get_order_detail / create_voucher_draft |
| adjust_price | 4 | search_customers / search_products / get_product_customer_prices / adjust_price_request |
| adjust_stock | 3 | search_products / check_inventory / adjust_stock_request |

### 5.2 Strict Mode Schema 改造（17 个 tool 全部）

每个 tool 的 schema 都要满足：

```python
{
    "type": "function",
    "function": {
        "name": "...",
        "description": "...（带 ❌ 反例 + ✅ 正例）",
        "strict": True,  # ← 新增
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "integer"},
                "items": {"type": "array", "items": {...}},
                # 即使可选字段也要列出
                "shipping_address": {"type": ["string", "null"]},
            },
            "required": ["customer_id", "items", "shipping_address"],  # 全部 required
            "additionalProperties": False,  # ← 新增
        },
    },
}
```

**改造工时**：17 个 tool × 5 分钟 = 1.5 小时。

---

## 6. Router 设计

### 6.1 Intent 候选

```python
class Intent(str, Enum):
    CHAT = "chat"
    QUERY = "query"
    CONTRACT = "contract"
    QUOTE = "quote"
    VOUCHER = "voucher"
    ADJUST_PRICE = "adjust_price"
    ADJUST_STOCK = "adjust_stock"
    CONFIRM = "confirm"
    UNKNOWN = "unknown"
```

### 6.2 Router 节点（用 Prefix Completion）

```python
async def router_node(state: AgentState) -> AgentState:
    """轻量 LLM 调用做意图分类，prefix 强制 JSON 开头。"""
    messages = [
        {"role": "system", "content": ROUTER_SYSTEM_PROMPT},  # 含 few-shots，约 1K token
        {"role": "user", "content": state.user_message},
        {"role": "assistant", "content": '{"intent": "', "prefix": True},
    ]
    resp = await llm.chat(
        messages=messages,
        stop=['",'],  # 截断到 intent 字段
        max_tokens=20,  # intent 字段最多十几个字符
        # thinking: off
    )
    intent_str = resp.text.split('"')[0]  # 解析 'contract' 等
    state.intent = Intent(intent_str) if intent_str in Intent.__members__ else Intent.UNKNOWN
    return state
```

**关键设计**：
- system prompt 完全静态（含 few-shots）→ KV cache 命中
- prefix 物理保证 JSON 输出
- thinking 关（router 是简单分类）
- max_tokens=20 控制成本

### 6.3 特殊 Intent 处理

- **CONFIRM**（"是" / "确认"）：从 SessionMemory 读上轮 `last_intent.tool` 路由到对应子图的 confirm 节点
- **UNKNOWN** + confidence < 0.7：fallback chat 子图请求澄清

---

## 7. 旧代码处理

| 文件 | 处理 | 原因 |
|---|---|---|
| `chain_agent.py` (505 行) | **删** | 整体替换 |
| `context_builder.py` (363 行) | **删** | state schema + 1M context 替代 budget 截断 |
| `prompt/builder.py` (340 行) | **保留**业务词典 + 同义词；**删** 12 条行为准则 | 业务词典放进每子图 system prompt 顶部（保 cache）|
| `prompt/few_shots.py` | **重构** | 各子图自己的 few-shots（散落 `subgraph_prompts/`）|
| `tools/registry.py` | **保留**核心 + 加 `subgraph_filter` + 加 strict mode 兼容 | |
| `tools/confirm_gate.py` | **保留** | 写门禁仍需要 |
| `tool_logger.py` | **保留** | 需新增 cache 命中率监控字段 |
| `memory/session.py` | **保留**（per-user 已就绪） | |
| `memory/loader.py` | **保留** | |
| `llm_client.py` | **改造**：beta endpoint + strict + prefix 支持 + thinking 参数 | |
| 12 条行为准则（3a-3l）| **全删** | 流程逻辑搬代码层 |

---

## 8. 文件结构（新）

```
backend/hub/agent/
  chain_agent.py            ← 删
  context_builder.py        ← 删

  graph/                    ← 新
    __init__.py
    agent.py                ← GraphAgent 顶层入口
    state.py                ← Pydantic state schemas
    router.py               ← intent classifier
    subgraphs/
      chat.py / query.py / contract.py / quote.py
      voucher.py / adjust_price.py / adjust_stock.py
    nodes/                  ← 跨子图复用
      resolve_customer.py / resolve_products.py
      validate_inputs.py / ask_user.py / format_response.py

  prompt/
    builder.py              ← 留业务词典/同义词
    intent_router.py        ← router 专用 prompt
    subgraph_prompts/       ← 每子图 prompt（静态 / cache 命中关键）
      chat.py / query.py / contract.py / ...

  tools/
    registry.py             ← 加 strict mode + subgraph_filter
    erp_tools.py / generate_tools.py / ... ← 17 tool schema 改 strict

  memory/                   ← 保留
  llm_client.py             ← 加 prefix / strict / thinking / beta endpoint
```

---

## 9. Prompt 前缀稳定化设计（v2 关键章节）

### 9.1 子图 prompt 模板

每个子图的 system prompt **必须**满足：

```
[完全静态部分 — KV cache 命中目标]
   1. 子图角色描述（"你是合同生成助手"）
   2. 业务词典（粘贴 DEFAULT_DICT）
   3. 同义词
   4. Few-shot examples
   5. Tool 使用约束

[user 段动态部分 — 不要塞 system]
   - round_state 摘要
   - 当前用户消息
```

**正反对照**：

```python
# ❌ 反例：cache 永远不命中
system = f"今天是 {date.today()}, 用户 ID 是 {hub_user_id}, 上轮 state 是 {round_state}..."

# ✅ 正例：cache 高命中
system = STATIC_CONTRACT_SUBGRAPH_PROMPT  # 永不变
messages = [
    {"role": "system", "content": system},
    {"role": "user", "content": f"[上轮状态]\n{round_state}\n\n[当前消息]\n{user_msg}"},
]
```

### 9.2 子图 prompt 长度 budget

| 子图 | prompt 长度 | cache 命中价 |
|---|---|---|
| router | ≤ 1K token | ¥0.00002/调用 |
| chat | ≤ 500 token | ¥0.00001/调用 |
| query | ≤ 2K token（11 个 tool description）| ¥0.00004/调用 |
| contract | ≤ 1.5K token | ¥0.00003/调用 |
| voucher / adjust_price / adjust_stock | ≤ 1.5K token 各 | ¥0.00003/调用 |

**预期**：单次主 LLM 调用 cache 命中部分**几乎免费**，未命中部分（user_msg + tool_results）≤ 5K token = ¥0.005/调用。

### 9.3 Cache 命中率监控

每次 LLM 调用后记录：
```python
hit_rate = usage.prompt_cache_hit_tokens / (
    usage.prompt_cache_hit_tokens + usage.prompt_cache_miss_tokens
)
```
存到 `tool_call_log` 加新字段 `cache_hit_rate`，admin dashboard 显示**月平均命中率**。target ≥ 80%。

---

## 10. 测试策略

| 层 | 测试 |
|---|---|
| 单节点 | 每节点函数单测（mock LLM + state） |
| 子图集成 | 完整跑子图（stub LLM 模拟正常 / 错误 / 边界）|
| Router 准确率 | 50+ case 标注 case，target ≥ 95% |
| **Cache 命中率** | 跑 30 case 后统计实际命中率，**target ≥ 80%** |
| **Strict mode 验证** | mock LLM 输出错误类型 → 断言 strict 拒绝 |
| **Prefix 功能** | mock router 输出 → 验证 JSON parse 成功率 100% |
| 端到端真 LLM | 6 用户故事 + 30 case eval，满意度 ≥ 80% |
| 旧测试迁移 | 660+ 单测大部分迁移，少量重写 |

---

## 11. Timeline 与 Milestone

| Milestone | 内容 | 工时 |
|---|---|---|
| **M0: 基建 + DeepSeek 兼容性验证** | LangGraph 接入；llm_client 改造（beta endpoint / prefix / strict / thinking / 600s timeout / 指数退避 / 5 种 finish_reason / temperature 矩阵）；**关键验证：v4-flash 同时启用 thinking + tools 是否可行**（如不可行调整 1.5 节计划）；state schemas | 1 天 |
| **M1: Router + chat** | router 节点（prefix JSON）+ chat 子图最简；50 case router 准确率测试 | 0.5 天 |
| **M2: Tool strict 化** | 17 个 tool schema 加 strict + additionalProperties: false + 全字段 required | 0.5 天 |
| **M3: query 子图** | 11 个 ERP/analyze 读 tool 重组；3 节点；prompt 稳定化 | 1 天 |
| **M4: contract 子图** | 5 节点（含 thinking 在 validate）；最复杂 | 1.5 天 |
| **M5: 写操作子图** | voucher / adjust_price / adjust_stock 三子图 + ConfirmGate 集成 | 1.5 天 |
| **M6: quote 子图** | 与合同结构相似但更简单 | 0.5 天 |
| **M7: 接入 + 旧代码删除** | dingtalk_inbound 切换；删 ChainAgent / context_builder / 12 条准则；旧测试迁移 | 0.5 天 |
| **M8: 测试 + 真 LLM eval** | 单测全过；6 故事 staging + 30 eval；cache 命中率 ≥ 80% | 1 天 |
| **总** | | **8 天** |

---

## 12. 风险点 + 缓解

| 风险 | 缓解 |
|---|---|
| LangGraph 学习曲线 | M0 先做最简 chat 子图练手 |
| Router 分类不准 | M1 准确率必须 ≥ 95% 才进 M3 |
| **Cache 命中率不达预期** | M3 后跑 5 case 测命中率，不达 80% 立即调 prompt 结构 |
| **Strict mode 偶发拒绝合法输入** | strict 错误时 fallback 走非 strict + 加日志告警 |
| **Beta endpoint 不稳定** | llm_client 加重试 + 切回 main endpoint 的 fallback |
| State schema 频繁变更 | Pydantic 容错 + version 字段 |
| 真 LLM eval 不达 80% | M4 后用 5 case 跑 eval 看趋势 |
| Plan 6 staging 暂停 | 接受 1.5 周；重构完一次性回归 |
| **JSON mode 偶发空响应** | router 用 prefix completion 替代 JSON mode（更稳） |
| **v4-flash + thinking + tools 三者不兼容** | M0 必验；不兼容则关闭所有需要 tool 的节点的 thinking（影响 1.5 节计划但不破坏架构）|
| **reasoning_content 入历史导致 400** | SessionMemory.append 显式剥离 reasoning_content 字段；加单测验证 |
| **高负载下 429 / insufficient_system_resource 风暴** | 指数退避 + 长 timeout + keep-alive 识别（v3 加固）|
| **chat 子图 temperature 0.0 太僵硬** | 按节点配置：chat=1.3 / tool=0.0 / format=0.7 |

---

## 13. Success Criteria

| 维度 | 目标 | 验证方式 |
|---|---|---|
| 自然对话 | 6 故事每个 1-2 round 完成 | staging 跑 6 故事录屏 |
| 意图准确率 | router ≥ 95% | 50 case 自动测 |
| **Cache 命中率** | **≥ 80%（月平均）** | tool_call_log 统计 |
| 不再 patch bug | 1 周内不出现"反复确认/调多余 tool" | staging 主观评价 |
| 延迟 | p50 < 5s, p99 < 15s | 真 LLM eval 期间统计 |
| **月成本（cache 命中 ≥ 80%）** | **≤ ¥1K** | 1 周外推 |
| 月成本（最坏，cache < 50%）| ≤ ¥3K | 退化兜底 |
| 真 LLM eval | 30 case ≥ 80% 满意度 | pytest -m eval |
| 测试覆盖 | 单测 ≥ 95% pass | pytest -q |

---

## 14. 不在本次重构范围内（YAGNI）

- ❌ LangSmith 接入（observability，将来做）
- ❌ Streaming 输出（钉钉不要求）
- ❌ Multi-agent 协作（单 agent + 子图够用）
- ❌ 工具结果向量化检索（数据量不够）
- ❌ 改任何 tool 业务逻辑（只重组挂载方式 + 加 strict schema）
- ❌ 改模型（用 v4-flash；模型升级单独评估）
- ❌ 改 ConfirmGate / 写门禁（v6/v7 已稳定）
- ❌ Reasoning model（DeepSeek-R1 不必要）

---

## 15. 重构后下一步建议（不属于本 spec）

1. 用真 v4-flash 跑 staging 6 故事 + 30 eval case
2. **如果 cache 命中率 ≥ 80% 且体感够好** → 维持 v4-flash，月成本 ≤ ¥1K
3. **如果想更自然** → 升级 v4-pro（同家更强，月成本 +200%）
4. **如果要"跟 Claude 聊天一样"** → Claude Sonnet 4.5（月成本 +500%）
5. **加上下文硬盘缓存监控 dashboard**：admin 后台显示每天 cache 命中率
6. **加 router 准确率持续监控**：基于真实流量更新 50+ case

---

## 16. 决策记录（brainstorming + DeepSeek 文档调研后确定）

1. **重构策略**：A 一次性重写（staging 还没合并 main）
2. **State machine 框架**：B LangGraph
3. **意图前置**：B 轻量 LLM router（Anthropic Routing pattern）
4. **DeepSeek 利用**：beta endpoint + strict + prefix + thinking 战略性使用 + KV cache 前缀稳定
5. **JSON mode 不用**：用 prefix completion 替代（更稳）
6. **预期管理**：重构能根治"流程类"bug 80%，模型本身能力是天花板（不超 GPT-4o）

---

## 附录 A：参考资料

- DeepSeek 文档（v2 主要参考）：
  - [Multi-round Chat](https://api-docs.deepseek.com/zh-cn/guides/multi_round_chat)
  - [Function Calling](https://api-docs.deepseek.com/zh-cn/guides/function_calling)
  - [Chat Prefix Completion](https://api-docs.deepseek.com/zh-cn/guides/chat_prefix_completion)
  - [JSON Mode](https://api-docs.deepseek.com/zh-cn/guides/json_mode)
  - [KV Cache](https://api-docs.deepseek.com/zh-cn/guides/kv_cache)
  - [Pricing](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)
  - [Token Usage](https://api-docs.deepseek.com/zh-cn/quick_start/token_usage)
  - [Error Codes](https://api-docs.deepseek.com/zh-cn/quick_start/error_codes)
  - [API Spec](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion)
- Anthropic [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)
- LangGraph 文档：<https://langchain-ai.github.io/langgraph/>
- 项目内文档：
  - `docs/superpowers/plans/2026-04-29-hub-agent-implementation.md`（Plan 6 原始）
  - `docs/superpowers/plans/notes/2026-05-01-plan6-final-report.md`（v8 staging 总结）

---

## 附录 B：版本变化

### v1 → v2（5/2 中午）— 对齐 DeepSeek 部分文档

| v1 | v2 |
|---|---|
| MAX_PROMPT_TOKEN 32K | 100K（1M context 不需要激进截断）|
| 月成本预估 ¥3K | ≤ ¥1K（cache 命中后）|
| 没明确 prompt 前缀稳定 | 新章节专门讲 |
| Router 用 JSON mode | Router 用 prefix completion（更稳）|
| Tool schema 沿用 OpenAI 旧格式 | 全改 strict mode（17 tool）|
| 没用 thinking mode | 4-5 个关键节点开 thinking |
| 没用 prefix completion | 3 处应用 |
| 没监控 cache 命中率 | 加 success criteria + tool_call_log 字段 |
| Beta endpoint 没提 | 必须用 |

### v2 → v3（5/2 晚）— 完整通读 DeepSeek 14 个章节

| v2 | v3 |
|---|---|
| thinking 默认与 tool 兼容 | **明确分离 deepseek-reasoner（不支持 tool）vs v4-flash thinking 模式（M0 验证兼容性）**|
| 没提 reasoning_content 多轮陷阱 | **明确：SessionMemory append 时必须剥离 reasoning_content，否则 400** |
| 全节点 temperature=0.0 | **按节点配置：tool 类 0.0 / chat 类 1.3 / format 0.7（DeepSeek 官方推荐）**|
| finish_reason 简单处理 | **5 种全显式处理：含 insufficient_system_resource 当 503 重试**|
| timeout 30s/45s | **600s（10 分钟，DeepSeek 动态速率 + keep-alive 机制）**|
| retry backoff 1.5s 固定 | **指数退避：1.5 → 5 → 15 → 60s，避免高负载下加剧 429**|
| 没用 tool_choice | **关键节点用 specific name 强制调用**（resolve_customer 等）|
| 没明确 LangGraph 适配 | **OpenAI 兼容声明 → LangChain/LangGraph 直接可用，无需适配层**|

DeepSeek 文档已通读章节：
- ✅ Multi-round Chat / Function Calling / Chat Prefix Completion / JSON Mode / KV Cache
- ✅ Pricing / Token Usage / Error Codes / Rate Limit / FAQ
- ✅ Create Chat Completion API / Reasoning Model / FIM / Parameter Settings
- ✅ News (R1 release)
