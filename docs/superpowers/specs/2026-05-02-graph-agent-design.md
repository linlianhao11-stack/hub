# Plan 6 v9 — GraphAgent 架构重构 Design Spec（v3.4）

**日期**：2026-05-02
**作者**：Claude Opus 4.7（与产品 owner 林炼豪共同 brainstorming）
**状态**：Design 阶段（v3.4 — 第四轮 review：sentinel 范围扩到读 tool + 故事 6 同步复合 key）
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
- 所有 properties 必须 `required`（即使可选字段也要列出）
- 顶层 `additionalProperties: false`
- 不能用 `minLength` / `maxLength` / `minItems` / `maxItems`（strict 不支持）

**可选字段表达**（**关键**：DeepSeek strict 与 OpenAI 不一致）：

DeepSeek strict 文档列的支持类型只有 `object/string/number/integer/boolean/array/enum/anyOf`，**没有 `null` 也不支持 `type` 数组**。文档列了 `anyOf` 不等于 `anyOf` 分支里允许 `{"type": "null"}` —— 这是一个**未被文档明确背书**的组合，如果 17 个 tool 默认都按这个写，可能在 beta schema 校验阶段整体 400。

**v3.2 默认策略反转 — 业务 sentinel 设为默认实施方案**：

```python
# ✅ 写法 B（默认）：业务 sentinel — 不依赖 null 支持，DeepSeek 文档明确支持的纯 string 类型
"shipping_address": {
    "type": "string",
    "description": "可选；如无地址传空字符串 ''",
}
# 业务层在 tool handler 入口把 "" 视为缺省

# ⚠️ 写法 A（M0 实验项）：anyOf + null
"shipping_address": {"anyOf": [{"type": "string"}, {"type": "null"}]}
# DeepSeek 文档列了 anyOf 但没明确举例 null 分支；M0 必须真实 beta 跑一次 schema 校验 +
# 用各种缺省调用确认 LLM 真的会传 null 而不是省略字段

# ⚠️ 写法 C（极端方案）：按"有/无"拆分 tool
# generate_contract_draft_with_address / _no_address 两份 schema
# 17 → 25+ 个 tool，仅在 A/B 都失败时考虑
```

**v3.2 决策**：
- **默认**用写法 B（业务 sentinel）批量改造 17 个 tool — 文档明确支持的安全写法
- **M0 实验项**：单独挑 1 个 tool（如 `generate_contract_draft.shipping_address`）用写法 A 在 beta 上跑，确认接受 + LLM 实际能正确传 null
  - 实验通过 → spec 升级到"写法 A 优先 / 写法 B 兜底"，写一份升级 plan
  - 实验失败 → 维持写法 B 不动
- 写法 C 仅在 B 也不行时考虑（不预期发生）

**对业务层的要求**（采用写法 B 的代价）：

- **所有用了 sentinel 的 tool handler**（无论读 / 写 tool）**入口**都必须把 sentinel 显式转回 `None`，再交给业务层 — 读 tool 也会有可选过滤条件（`search_orders` 的日期 / 客户、`analyze_*` 的 period / filter 等），如果 sentinel 不归一化就传给 ERP，会被当成真实过滤条件 → 400 或返回错误结果集。

  写 tool 示例（更严格 — 归一化后还要保证 `None` 不能被当成"清空字段"落库）：

  ```python
  async def generate_contract_draft(*, customer_id, items, shipping_address, ...):
      # 入口归一化 — string sentinel "" → None
      shipping_address = shipping_address or None
      # array sentinel [] → None
      items_extra = items_extra or None
      # 之后业务层 / DB 层一律用 None 表示"未提供"
      ...
  ```

  读 tool 示例（同样必须归一化 — 否则 `""` 会被当真实条件查询）：

  ```python
  async def search_orders(*, customer_name, start_date, end_date, ...):
      # 入口归一化 — 不归一化的话 customer_name="" 会被传给 ERP 作"客户名为空字符串"过滤
      customer_name = customer_name or None
      start_date = start_date or None
      end_date = end_date or None
      # 业务层只对 not None 的字段加 WHERE 子句
      ...
  ```

- 在每个 tool 的 schema description 里**显式写**给 LLM 看的传值约定（如"无值传空字符串 ''"），让模型生成时知道 sentinel 是什么

- **写 tool vs 读 tool 的 sentinel 处理对比**：

  | 维度 | 读 tool | 写 tool |
  |---|---|---|
  | 入口归一化 | ✅ 必须 | ✅ 必须 |
  | `None` 含义 | "不加这条过滤条件" | "字段未提供" |
  | 错误后果 | 查询条件错误 / 400 / 空结果集 | 落库脏数据 / 错误业务执行 |
  | 单测要求 | 至少 1 个读 tool（如 `search_orders`）覆盖 sentinel 归一化 | 每个写 tool 都覆盖 |

- **不同类型字段的 sentinel 规范**：

  | 字段类型 | sentinel | 入口归一化 | 备注 |
  |---|---|---|---|
  | `string` | `""` | `x = x or None` | 最常用 |
  | `array` | `[]` | `x = x or None` | 空数组语义同"未提供" |
  | `object` | `{}` | `x = x or None` | 同上 |
  | `integer` / `number` | **避免用 0** | — | 0 可能是合法值（如数量 / 价格的"调到 0"），不能当 sentinel |
  | 真正可选的数字字段 | 无 sentinel 可用 | — | **必须拆 tool**（写法 C），或在 schema 里改成 `enum`/`string` 编码 |
  | `boolean` | **避免用 false** | — | false 通常是合法语义；改成必填 + 默认值由调用方约定 |

  原则：**sentinel 必须不在业务合法取值集合内**，否则就要拆 tool。

**禁用** OpenAI 风格 `type: ["string", "null"]`（即使本地 mock 通过也会在 beta 上失败）。

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

**默认值陷阱**：DeepSeek Chat API 的 `thinking` 字段**默认是 enabled**（不是 disabled）。

- **强约束**：所有非 thinking 节点的 `llm.chat(...)` 调用**必须显式传** `thinking={"type": "disabled"}`，否则会默认开 thinking → token 多 30-50% / 延迟翻倍 / 与 tool 的兼容性看 M0 验证
- llm_client 加 helper：`disable_thinking()` 返回 `{"type": "disabled"}`，节点显式调用避免漏传
- 单测：每个非 thinking 节点的调用 mock 检查 `thinking={"type":"disabled"}` 出现在请求里
- **本 spec 后文所有节点示例代码都按这条约束写**

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

**v3 在某些节点用 "specific name" 强制调用**（必须用完整 ChatCompletionNamedToolChoice 结构，不能简写）：

```python
# ✅ 完整结构（OpenAI / DeepSeek 都支持）
tool_choice = {"type": "function", "function": {"name": "search_customers"}}

# ❌ 简写（DeepSeek/OpenAI 兼容接口会校验失败或退回 auto）
tool_choice = {"name": "search_customers"}
```

具体节点：
- `contract.resolve_customer` → `tool_choice = {"type": "function", "function": {"name": "search_customers"}}` 强制只调这个
- `contract.resolve_products` → `tool_choice = "required"` 必须调（但允许多次）
- 防止 LLM 跳过解析步骤

### 1.10 OpenAI 兼容性的边界（必须保留 LLM client 适配层）

> 「DeepSeek API interface is compatible with OpenAI」

OpenAI 协议兼容**不等于** `langchain.ChatOpenAI` 默认封装可以直接吃下我们所有需求。本 spec 同时依赖以下字段，LangChain 默认 wrapper 不一定都暴露 / 透传 / 记录：

| 我们依赖 | LangChain `ChatOpenAI` 默认 |
|---|---|
| `prefix=true` (assistant message 续写) | 不暴露 — DeepSeek 独家字段 |
| tool schema `strict: true` + `additionalProperties: false` | 部分暴露但不验证 |
| `thinking={"type": "disabled"}` 显式传 | 不暴露 — DeepSeek 独家 |
| `finish_reason == "insufficient_system_resource"` 识别 | 默认归到 generic error，丢失语义 |
| `usage.prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` | 不解析 — KV cache 监控失明 |
| `user_id` 字段（DeepSeek 用于反滥用 / 限流） | 可传但不一定标准化 |
| keep-alive 字符（空行 / `:`）流式识别 | 默认按 SSE 解析，可能误判 |
| 600s timeout + 1.5/5/15/60s 指数退避 | 默认 60s，需要 override |

**v3.1 决策**：

- **保留** `hub/agent/llm_client.py` 作为 GraphAgent 的 LLM 适配层（不是直接拿 `ChatOpenAI`）
- llm_client 内部**可以**用 OpenAI Python SDK（接口兼容），但**对外暴露的** API 是 `DeepSeekLLMClient`，封装：
  - prefix completion 参数构造
  - strict tool schema 校验前置
  - thinking 模式开关 + 默认 disabled
  - 5 种 finish_reason 显式分支（含 `insufficient_system_resource`）
  - usage cache 字段解析 → 写 `tool_call_log.cache_hit_rate`
  - 指数退避 + 600s timeout + keep-alive 识别
  - 按 tool 类型分级的 fallback（参见 12.1）
- LangGraph 节点调用 `DeepSeekLLMClient`（如果需要 `Runnable` 接口，包一层 `RunnableLambda`），**不**直接 `ChatOpenAI(base_url=...)`
- 现有 `llm_client.py` 153 行可以演化升级，不是从零写

**M0 验收**：DeepSeekLLMClient 提供以下能力的真实 beta 集成测试：
1. prefix completion 强制 JSON 输出
2. strict tool schema 业务 sentinel 写法（默认）接受 + 错误类型被拒
3. **附加实验**：strict + `anyOf: [{type: string}, {type: null}]` 单 tool 试跑（通过则升级，失败维持 sentinel）
4. thinking disabled + tools 同时启用
5. 命中 KV cache 后 usage 字段解析正确
6. 模拟 `insufficient_system_resource` → 触发指数退避

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

### 2.1 Per-User 状态隔离（硬约束）

**问题**：Plan 6 v8 review #16-#19 已经把 SessionMemory / ConversationLog / ToolCallLog / ConfirmGate 全部收敛到 per-user 边界。但 GraphAgent 引入 LangGraph **checkpointer** 后会新增一份"跨节点 / 跨轮 state"持久化（保存 `ContractState` / `AdjustPriceState` / pending action 元数据等）。如果 checkpointer 沿用常见默认 `thread_id = conversation_id`，**钉钉群聊里不同用户会共享 ContractState / pending state**，把刚修掉的群聊串状态问题又原样引回来。

**硬约束 — 所有"跨轮状态"组件必须以 `(conversation_id, hub_user_id)` 为复合 key**：

| 组件 | 旧 key（v8 review 前）| 新 key（v3.2 起强制）|
|---|---|---|
| LangGraph checkpointer | `thread_id = conversation_id` ❌ | **`thread_id = f"{conversation_id}:{hub_user_id}"`** ✅ |
| SessionMemory（Redis）| 已 per-user（v8 #16）| 保持 |
| ConversationLog（PG）| 已加复合 unique（v8 #17）| 保持 |
| ToolCallLog（PG）| 已 per-user（v8 #18）| 保持 |
| ConfirmGate pending map | 已 per-user（v8 #19）| 保持 |

**实现位置**：

```python
# graph/agent.py
async def run(self, *, user_message, hub_user_id, conversation_id, **_):
    config = {
        "configurable": {
            "thread_id": f"{conversation_id}:{hub_user_id}",
            # 不要漏写 thread_id 让 LangGraph 默认用 None — 会全局共享
        }
    }
    return await self.compiled_graph.ainvoke(
        {"user_message": user_message, "hub_user_id": hub_user_id, ...},
        config=config,
    )
```

**禁止**：
- `thread_id = conversation_id`（群聊串状态）
- `thread_id = hub_user_id`（不同会话串上下文）
- 任何 checkpoint key 漏写 `hub_user_id`

**验收测试**（M0 必加）：

```python
# tests/agent/test_per_user_isolation.py
async def test_same_conv_different_user_checkpoint_isolated():
    """同一 conversation_id，不同 hub_user_id 的 ContractState 必须互不可见。"""
    conv = "群聊-test"
    # 用户 A 起草合同到一半（resolve_customer 完成，待 resolve_products）
    state_a = await agent.run(user_message="给阿里做合同", hub_user_id=1, conversation_id=conv)
    # 用户 B 在同一群里另起一个合同
    state_b = await agent.run(user_message="给百度做合同", hub_user_id=2, conversation_id=conv)
    # 用户 A 接着补充
    state_a2 = await agent.run(user_message="X1 10 个 300", hub_user_id=1, conversation_id=conv)
    # A 的 ContractState 仍是阿里，不能被 B 的百度覆盖
    assert state_a2.customer.name == "阿里"
```

把这个测试加到 §10.1 测试层级表，列入 M0 / M4 验收门禁。

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

每个 tool 的 schema 都要满足（注意可选字段写法见 1.3 节）：

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
                # 即使可选字段也要列出 + required
                # v3.2 默认：业务 sentinel（DeepSeek 文档明确支持的纯 string 类型）
                # 不用 anyOf+null（文档没明确举例，未经 beta 验证有 400 风险，见 §1.3）
                "shipping_address": {
                    "type": "string",
                    "description": "可选；如无地址传空字符串 ''",
                },
            },
            "required": ["customer_id", "items", "shipping_address"],  # 全部 required
            "additionalProperties": False,  # ← 新增
        },
    },
}
```

**Tool handler 入口处理 sentinel**：

```python
async def generate_contract_draft(*, customer_id, items, shipping_address, ...):
    # explicit 处理空串 sentinel — 避免 "" 被当成 valid address 落库
    shipping_address = shipping_address or None
    ...
```

**改造工时**：17 个 tool × 5 分钟 schema + 业务层 sentinel 处理 5 分钟 = 约 3 小时；M0 单独挑 1 个 tool 跑 anyOf-null 实验，通过则升级方案。

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
        thinking={"type": "disabled"},  # 必须显式传，DeepSeek 默认 enabled
        temperature=0.0,
    )
    intent_str = resp.text.split('"')[0].strip().lower()  # 解析 'contract' 等
    # 注意：Intent.__members__ 是 {'CHAT', 'QUERY', ...} 大写枚举名，
    # 不是模型续写出的 lowercase value。必须用 Intent(value) 构造 + ValueError 兜底，
    # 否则所有合法 intent 都会落 UNKNOWN。
    try:
        state.intent = Intent(intent_str)
    except ValueError:
        state.intent = Intent.UNKNOWN
    return state
```

**关键设计**：
- system prompt 完全静态（含 few-shots）→ KV cache 命中
- prefix 物理保证 JSON 输出
- thinking 关（router 是简单分类）
- max_tokens=20 控制成本

### 6.3 特殊 Intent 处理

- **CONFIRM**（"是" / "确认"）：**绝不**从 SessionMemory 的 `last_intent.tool` 推断要确认什么。Plan 6 v6/v7 staging review 已经把写门禁收敛到 **per-user pending action map + confirmation_action_id + token claim** 协议（参见 `tools/confirm_gate.py`），不能回退。

  **API 形态**（关键 — 必须以 `(conversation_id, hub_user_id)` 复合 key 查询，与 §2.1 边界一致）：

  ```python
  # ❌ 旧设想 1：返回单个 pending —— 用户有多个 pending 时会确认错的那个
  pending = await ConfirmGate.get_pending_for_user(hub_user_id)

  # ❌ 旧设想 2（v3.2 早期）：只按 user 查 —— 同一 user 在私聊+群聊都有 pending 时
  # 任一会话回"确认"都会列出/确认另一会话的 pending action（跨会话串确认）
  pendings = await ConfirmGate.list_pending_for_user(hub_user_id)

  # ✅ v3.3：必须复合 key — conversation 作用域 + user 拥有
  pendings: list[PendingAction] = await ConfirmGate.list_pending_for_context(
      conversation_id=conversation_id,
      hub_user_id=hub_user_id,
  )
  ```

  **confirm_node 三分支行为**（基于 `len(pendings)`）：

  | 场景 | 行为 |
  |---|---|
  | **0 个 pending** | 路由到 chat 子图："您要确认什么？本会话没有待办的操作" |
  | **1 个 pending** | claim → claim 成功路由到 `pending.subgraph` 的 confirm 子节点；claim 失败提示"该确认已失效或属于他人" |
  | **>1 个 pending**（**关键**：禁止默认取最新 / 任意一个）| **不**自动 claim，回复列表让用户选：「您有 N 个待确认操作：1) 给阿里调价 X1 → 280；2) 给百度合同 PDF；请回复编号或 action_id」。下一轮 router → confirm 时 `user_message` 含编号或 action_id → `confirm_node` 据此精确选 pending → 走单 pending 分支 |

  **pending 的元数据结构**（ConfirmGate 写时必带，confirm_node 读时用）：

  ```python
  class PendingAction(BaseModel):
      action_id: str            # 唯一 id
      hub_user_id: int          # 拥有者
      conversation_id: str      # 作用域会话 — 必须随写入；list_pending_for_context 据此过滤
      subgraph: str             # "adjust_price" / "voucher" / ...，confirm_node 据此路由
      summary: str              # "给阿里调价 X1 价格 300 → 280"，>1 时给用户看
      created_at: datetime
      ttl_seconds: int = 600    # 过期失效，避免长期堆积
  ```

  **claim 也要校验 conversation_id**（防止 A 把私聊的 token 复制到群聊确认）：

  ```python
  await ConfirmGate.claim(
      action_id=...,
      token=...,
      hub_user_id=hub_user_id,
      conversation_id=conversation_id,  # 必传 — claim 时校验 pending.conversation_id 一致
  )
  # claim 内部：if pending.conversation_id != conversation_id: raise CrossContextClaim
  ```

  **关键不变量**：
  - **per-user + per-conversation 复合隔离**：A 在私聊的 pending 不会被 A 在群聊的"是"确认（与 §2.1 LangGraph thread_id 边界对齐）
  - 群聊里 A 的 pending 不会被 B 的"是"确认（per-user）
  - **action_id 显式**：不靠"上一个意图是什么"猜
  - **token 单次消费**：避免重发 / 双触
  - **多 pending 不冒认**：宁可让用户多说一句也不替他选

  **验收测试**（`test_confirm_multi_pending.py`）：

  ```python
  async def test_two_pendings_in_same_context_does_not_auto_claim():
      """同一 (conv, user) 有 2 个待确认时，"确认"必须列摘要让用户选，不自动 claim。"""
      gate.create_pending(user=1, conv="c1", action_id="adj-1",
                          subgraph="adjust_price", summary="阿里 X1 → 280")
      gate.create_pending(user=1, conv="c1", action_id="vch-1",
                          subgraph="voucher", summary="SO-001 出库")
      res = await agent.run(user_message="确认", hub_user_id=1, conversation_id="c1")
      assert "1)" in res.text and "2)" in res.text
      assert gate.is_pending("adj-1") and gate.is_pending("vch-1")

  async def test_pending_in_other_conversation_invisible():
      """同一 user 在私聊 c1 有 pending，群聊 c2 回"确认"看不到 / 确认不到。"""
      gate.create_pending(user=1, conv="c1-private", action_id="adj-1",
                          subgraph="adjust_price", summary="阿里 X1 → 280")
      # 同 user 在另一会话 c2-group 回"确认"
      res = await agent.run(user_message="确认", hub_user_id=1, conversation_id="c2-group")
      # 不应看到 c1-private 的 pending；adj-1 仍然 pending
      assert "没有待办" in res.text
      assert gate.is_pending("adj-1")

  async def test_cross_context_claim_rejected():
      """伪造 token 跨会话 claim 必须被拒。"""
      pending = gate.create_pending(user=1, conv="c1", action_id="adj-1", ...)
      with pytest.raises(CrossContextClaim):
          await gate.claim(action_id="adj-1", token=pending.token,
                           hub_user_id=1, conversation_id="c2")  # 错的 conv
  ```

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

### 10.1 测试层级

| 层 | 测试 |
|---|---|
| 单节点 | 每节点函数单测（mock LLM + state） |
| 子图集成 | 完整跑子图（stub LLM 模拟正常 / 错误 / 边界）|
| Router 准确率 | 50+ case 标注 case，target ≥ 95% |
| **Cache 命中率** | 跑 30 case 后统计实际命中率，**target ≥ 80%** |
| **Strict mode 验证** | mock LLM 输出错误类型 → 断言 strict 拒绝 |
| **Prefix 功能** | mock router 输出 → 验证 JSON parse 成功率 100% |
| **Per-user 隔离** | 同一 `conversation_id` 不同 `hub_user_id` 的 LangGraph checkpoint / SessionMemory / ConfirmGate pending **不可见**（参见 §2.1）；M0 + M4 + M5 都要跑这套断言 |
| **多 pending 安全** | confirm_node 在 0 / 1 / >1 个 pending 下行为正确（参见 §6.3）；多 pending 时**不**自动 claim，必须列摘要让用户选 |
| 端到端真 LLM | 见 10.2 的 6 个 Acceptance Scenarios + 30 case eval，满意度 ≥ 80% |
| 旧测试迁移 | 660+ 单测大部分迁移，少量重写 |

### 10.2 Acceptance Scenarios（M8 验收基准）

以下 6 个用户故事是 GraphAgent 重构的最终验收基准。每个故事必须用真 LLM（v4-flash + beta endpoint）跑通；现有 `/tmp/e2e_*.py` 类的 e2e 脚本可以直接迁移成 pytest fixture。

每个故事都用同样的字段格式描述：**输入序列 → 预期 router 路由 → 预期 tool 调用上限 → 预期最终输出**。

#### 故事 1：闲聊（chat 子图）

- **输入**：「你好，最近怎样」
- **预期路由**：`router → chat`
- **Tool 调用**：0 个 tool 调用（chat 子图根本不挂 tool）
- **最终输出**：自然中文寒暄回复，**禁止**出现"请问您要做什么/查询什么"这类反问。
- **验收点**：temperature=1.3 让 chat 类回复自然，不僵硬。

#### 故事 2：单轮查询（query 子图）

- **输入**：「查 SKG 有哪些产品有库存」
- **预期路由**：`router → query`
- **Tool 调用**：`check_inventory` × 1（其他 query 子图工具 0 次）
- **最终输出**：表格化产品列表（字段 ≥ SKU/库存/单价），不夹杂"是否需要做合同"这种主动反问。

#### 故事 3：单轮合同（contract 子图，信息一次到齐）

- **输入**：「给阿里做合同 X1 10 个 300，地址北京海淀，张三 13800000000」
- **预期路由**：`router → contract`
- **Tool 调用**：`search_customers` × 1 + `search_products` × 1 + `generate_contract_draft` × 1（最多 `get_customer_history` 1 次预热价格判断），**禁止 check_inventory**（contract 子图不挂这个 tool，物理拒绝）。
- **最终输出**：合同 PDF 发到钉钉 + 中文一句话回执（前缀强制以"合同已生成"开头）。
- **验收点**：strict mode 保证 `extras` 是 dict 不是 string；prefix 保证开场白不啰嗦。

#### 故事 4：跨轮合同（query → contract，**核心场景**）

- **输入序列**：
  1. 「查 SKG 有哪些产品有库存」
  2. 「给翼蓝做合同 H5 10 个 300，F1 10 个 500，K5 20 个 300，地址广州市天河区华穗路406号中景B座，林生，13692977880」
- **预期路由**：第 1 轮 `router → query`，第 2 轮 `router → contract`
- **Tool 调用累计**：`check_inventory` ≤ 1 次（**只发生在第 1 轮**）；第 2 轮：`search_customers` × 1 + `search_products` × 1（合并搜 H5/F1/K5）+ `generate_contract_draft` × 1。
- **最终输出**：第 2 轮合同 PDF 一轮发出，含 3 个 items + shipping_address。
- **验收点**：**这是当前 ChainAgent 反复 patch 仍脆弱的场景**。GraphAgent 通过架构保证：
  1. router 看到"做合同"必走 contract 子图
  2. contract 子图 tool list 不含 check_inventory → 物理不可能再查库存
  3. validate_inputs 节点 thinking 模式判断 items 已齐，直接进 generate
- **现有 e2e 参考**：`/tmp/e2e_inv_then_contract.py`（断言：`check_inventory ≤ 1` / `generate_contract_draft == 1` / drafts == 1 / sent_files ≥ 1）。

#### 故事 5：报价（quote 子图）

- **输入**：「给阿里报 X1 50 个的价」
- **预期路由**：`router → quote`
- **Tool 调用**：`search_customers` × 1 + `search_products` × 1 + `generate_price_quote` × 1
- **最终输出**：报价单 PDF + 一句话回执。
- **验收点**：与故事 3 同构但更简单（不要求 shipping）。

#### 故事 6：调价 + ConfirmGate（adjust_price 子图）

- **输入序列**：
  1. 「把阿里的 X1 价格调到 280」
  2. （BOT 给预览 + 询问是否确认）→ 「确认」
- **预期路由**：第 1 轮 `router → adjust_price`（preview 节点写一个 `PendingAction(conversation_id, hub_user_id, ...)` 进 ConfirmGate），第 2 轮 `router → confirm` → `confirm_node` 用 **`(conversation_id, hub_user_id)` 复合 key** 调 `ConfirmGate.list_pending_for_context(...)` 取 pending action + claim token（claim 时同样带 conversation_id 做跨会话校验），路由到 `adjust_price` 子图的 confirm 子节点（**不**走 last_intent 推断，参见 §6.3）。
- **Tool 调用**：第 1 轮：`search_customers` × 1 + `search_products` × 1 + `get_product_customer_prices` × 1（**不**调 adjust_price_request，先 preview）。第 2 轮：`adjust_price_request` × 1。
- **最终输出**：第 1 轮 BOT 给"调价预览"消息（旧价 → 新价 + 客户历史成交价对比，由 thinking 模式产出），第 2 轮 BOT 回执"调价已申请，等待审核"。
- **验收点**：
  - ConfirmGate 在 GraphAgent 下仍然工作；preview 节点的 thinking 模式真的体现在输出（看到价格分析推理）
  - **跨会话隔离**：补一个变体 case — 同一 user 在 `c1-private` 起 preview 后到 `c2-group` 回"确认"，必须**看不到**该 pending（"本会话没有待办"），且 `c1-private` 的 pending 仍存活；回到 `c1-private` 才能 claim 成功（参见 §6.3 `test_pending_in_other_conversation_invisible`）

### 10.3 Acceptance scenario 自动化结构

```python
# tests/agent/test_acceptance_scenarios.py
@pytest.mark.eval_realllm
@pytest.mark.parametrize("scenario", load_scenarios("docs/scenarios/*.yaml"))
async def test_acceptance(scenario):
    agent = build_graph_agent(...)
    for turn in scenario.turns:
        res = await agent.run(user_message=turn.input, ...)
        assert turn.expected_intent == res.intent
        for tool, max_calls in turn.tool_caps.items():
            assert count_tool_calls(res, tool) <= max_calls
        if turn.expected_files:
            assert sender.sent_files == turn.expected_files
```

每个故事一份 yaml，便于持续维护与新增场景。

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
| **Strict mode 偶发拒绝合法输入** | 见 12.1 fallback 协议 — 写 tool **绝不**降级到非 strict |
| **Beta endpoint 不稳定** | 见 12.1 fallback 协议 — 失去 prefix/strict 时**写操作 fail closed**，不切 main endpoint 静默继续 |
| State schema 频繁变更 | Pydantic 容错 + version 字段 |
| 真 LLM eval 不达 80% | M4 后用 5 case 跑 eval 看趋势 |
| Plan 6 staging 暂停 | 接受 1.5 周；重构完一次性回归 |
| **JSON mode 偶发空响应** | router 用 prefix completion 替代 JSON mode（更稳） |
| **v4-flash + thinking + tools 三者不兼容** | M0 必验；不兼容则关闭所有需要 tool 的节点的 thinking（影响 1.5 节计划但不破坏架构）|
| **reasoning_content 入历史导致 400** | SessionMemory.append 显式剥离 reasoning_content 字段；加单测验证 |
| **高负载下 429 / insufficient_system_resource 风暴** | 指数退避 + 长 timeout + keep-alive 识别（v3 加固）|
| **chat 子图 temperature 0.0 太僵硬** | 按节点配置：chat=1.3 / tool=0.0 / format=0.7 |

### 12.1 Fallback 协议（关键安全约束）

**核心原则**：strict / prefix / beta endpoint 是本架构的**安全基础**，不是性能优化。失去这些能力**等于回到 ChainAgent 的脆弱状态**，不能静默 fallback。

按 tool 类型分级：

| Tool 类型 | 例子 | strict schema 错误 | beta endpoint 不可用 |
|---|---|---|---|
| **写 tool**（产生副作用 / DB 写） | `generate_contract_draft` / `adjust_price_request` / `adjust_stock_request` / `create_voucher_draft` / `generate_price_quote` | **fail closed**：直接报"系统配置异常，请稍后重试"，**绝不**降级到非 strict 重试 | **fail closed**：返回错误给用户 + alert 运维，**绝不**切 main endpoint 让 prefix/strict 失效后继续 |
| **读 tool**（只查询，幂等） | `search_customers` / `search_products` / `check_inventory` / `search_orders` / `get_*` / `analyze_*` | 可白名单降级到非 strict（schema 加日志后用宽松 schema 重试一次）| 可切 main endpoint 兜底，但需打 metric `endpoint_fallback=true` |
| **router/chat**（无 tool） | router_node / chat_subgraph | beta 不可用 → 切 main endpoint 兜底（失去 prefix 的 router 用 JSON mode + 重试 3 次降级）| 同左 |

**实现要求**：
- `llm_client` 必须按 tool 类型路由（不能全局 fallback 开关）
- 每次 fallback 必须记录 metric `llm.fallback{tool=,reason=}` + WARN 日志
- 写 tool 的 fail closed 错误**必须**返回给钉钉用户（不是静默忽略 + 不返回）
- 加 alarm：`llm.fallback{tool_class="write"}` 出现 1 次就告警（应永远 0）

**验收单测**：
- mock strict 校验 400 → 写 tool 路径必须抛异常 + 不重试
- mock beta endpoint 503 → 写 tool 路径必须返回错误响应 + alert
- mock 同样错误在读 tool 路径 → 必须降级 + 返回正常结果 + 打 metric

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

### v3 → v3.1（5/2 夜）— 用户 review 反馈逐条修复

7 条 review 意见全部接受，按 P1 → P2 修复：

| 编号 | 问题 | v3 | v3.1 |
|---|---|---|---|
| **P1-1** | Router intent 解析永远落 UNKNOWN | `intent_str in Intent.__members__`（大写枚举名 vs 小写 value 永不命中）| `try Intent(intent_str) except ValueError: UNKNOWN` + 注释解释陷阱 |
| **P1-2** | strict null union 在 DeepSeek 上会 400 | `"shipping_address": {"type": ["string", "null"]}`（OpenAI 风格）| 必须 `anyOf: [{type: string}, {type: null}]`（DeepSeek 文档列的支持构造）；M0 真实 beta 验证；fallback 业务 sentinel |
| **P1-3** | confirm 路由回退到 last_intent.tool 推断（重新引入多 pending / 群聊串确认）| 从 SessionMemory 读 last_intent.tool 路由 | 改成 ConfirmGate per-user pending action map + token claim（保留 v6/v7 的安全收敛）|
| **P1-4** | strict / beta fallback 绕开核心安全约束 | strict 错走非 strict + 切 main endpoint | **新增 12.1 节 fallback 协议**：写 tool fail closed 绝不降级，读 tool 白名单降级 + metric，每个 fallback 单测 |
| **P2-5** | tool_choice specific name 简写格式不对 | `{"name": "search_customers"}`（会 400 / 退回 auto）| 完整 `{"type": "function", "function": {"name": "..."}}` |
| **P2-6** | 非 thinking 节点没显式传 disabled（DeepSeek 默认 enabled）| 注释 `# thinking: off` 但调用没传 | 强约束所有非 thinking 调用必须传 `thinking={"type": "disabled"}` + 单测断言 + llm_client 加 helper |
| **P2-7** | "LangGraph 直接可用" 低估了协议适配 | LangChain `ChatOpenAI` 即可，无需适配层 | **保留 `DeepSeekLLMClient` 适配层**（封装 prefix / strict / thinking / cache usage / 5 种 finish_reason / 退避 / 12.1 fallback 分级）；LangGraph 节点用 wrapper 而不是裸 `ChatOpenAI` |

### v3.1 → v3.2（5/2 深夜）— 第二轮 review 收边界问题

3 条 review 意见全部接受：

| 编号 | 问题 | v3.1 | v3.2 |
|---|---|---|---|
| **P1-A** | LangGraph checkpoint 默认 `thread_id = conversation_id` 会让群聊里不同用户共享 ContractState / pending state | spec 只说 SessionMemory per-user，没约束 LangGraph checkpointer key | **新增 §2.1 Per-User 状态隔离**：硬约束 `thread_id = f"{conversation_id}:{hub_user_id}"`；列出 5 个组件统一以 `(conversation_id, hub_user_id)` 为边界；M0/M4 必跑同 conv 不同 user 的 checkpoint 隔离测试 |
| **P1-B** | 默认 `anyOf:[{type:string},{type:null}]` 实际未被 DeepSeek 文档明确背书，17 个 tool 一起改可能 schema 校验整体 400 | "首选 anyOf-null，fallback sentinel" | **默认反转**：写法 B（业务 sentinel `""` + handler 转 None）作为默认批量改造方案；anyOf-null 降级为 M0 单 tool 实验项；通过才升级 |
| **P2-C** | confirm_node 单 pending API 在多 pending 下会替用户选错 action | `get_pending_for_user` 返回单值 → claim | API 改成 `list_pending_for_user` 返回列表；三分支：0 个提示无待办、1 个 claim、>1 个**列摘要让用户选编号**禁止自动 claim；测试 `test_confirm_multi_pending.py` |

### v3.2 → v3.3（5/2 凌晨）— 第三轮 review 收剩余隔离 / 表达 bug

2 条 review 全部接受：

| 编号 | 问题 | v3.2 | v3.3 |
|---|---|---|---|
| **P1** | ConfirmGate API 仍只按 `hub_user_id` 查 pending，与 §2.1 定的 `(conversation_id, hub_user_id)` 复合边界不一致 — 同一 user 在私聊+群聊各有 pending 时会跨会话串确认 | `list_pending_for_user(hub_user_id)`；`PendingAction` 没有 `conversation_id` | `list_pending_for_context(conversation_id, hub_user_id)`；`PendingAction.conversation_id` 必填；`claim` 也要校验 conversation 一致（否则 `CrossContextClaim` 拒）；3 个隔离测试（同 conv 多 pending 不冒认 / 跨 conv 不可见 / 跨 conv claim 拒绝）|
| **P2** | sentinel handler 入口示例是 no-op 表达错误：`x = "" if x == "" else x` 实际什么都不做，下一行又说 `""` 不能落库 → 自相矛盾，实施者照抄会让空串落库 | `shipping_address = "" if shipping_address == "" else shipping_address` | 统一成 `shipping_address = shipping_address or None`；附 sentinel 类型规范表（string `""` / array `[]` / object `{}` 可用；integer `0` / boolean `false` **禁用**因为可能是合法值，必要时拆 tool）|

### v3.3 → v3.4（5/2 凌晨晚段）— 第四轮 review 收一致性问题

2 条 review 全部接受：

| 编号 | 问题 | v3.3 | v3.4 |
|---|---|---|---|
| **P2** | sentinel 归一化只要求"写 tool"，但 §5.2 是 17 个 tool 全部 strict 化 — 读 tool（`search_orders` 日期/客户、`analyze_*` period/filter）也会有可选过滤；不归一化的话 `""` 会被当成真实过滤条件传给 ERP → 400 / 错误结果集 | "每个写 tool 的 handler 入口必须把 sentinel 显式转回 None" | 改成"**所有用了 sentinel 的 tool handler**（无论读/写）入口都必须归一化"；加读 tool 示例（`search_orders` 三个可选字段都 `or None`）；加写/读 tool 对比表（`None` 含义不同 / 错误后果不同 / 单测要求不同）；明确**至少 1 个读 tool** 覆盖归一化测试 |
| **P3** | 故事 6 验收文案没跟上 §6.3 的复合 key 改动 — 仍写"用 hub_user_id 取 ConfirmGate 的 pending action"，会让执行者 / 测试作者漏写跨会话隔离断言 | `confirm_node 用 hub_user_id 取 pending action` | 改成"用 `(conversation_id, hub_user_id)` 复合 key 调 `list_pending_for_context(...)`，claim 也带 conversation_id"；验收点新增"跨会话隔离"变体 case：同一 user 在私聊起 preview 到群聊回"确认"必须看不到 pending |
