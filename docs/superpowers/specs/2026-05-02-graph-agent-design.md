# Plan 6 v9 — GraphAgent 架构重构 Design Spec

**日期**：2026-05-02
**作者**：Claude Opus 4.7（与产品 owner 林炼豪共同 brainstorming）
**状态**：Design 阶段（待 user review）
**预估工时**：8 天（约 1.5 周）
**前置 commits**：截至 `af96a93`（v8 staging review patch 收尾）

---

## 0. 背景与动机

Plan 6 v1-v8 在 staging 验收期一直在用 **patch-style prompt engineering** 修 LLM 行为问题：每出一个 bug → 加一条 prompt 行为准则 → LLM 注意力被分散 → 出新 bug → 再加准则。截至 v8，行为准则已堆到 12 条（3a-3l），但用户实测仍频繁出现：

- 反复确认（"梳理一遍" 让用户重新拍板）
- 多余 tool 调用（合同生成场景调 check_inventory × 5）
- 不主动推进流程（调完 tool 停下来等用户重发）
- 编造 ID（customer_id=102 / product_id=89 等不存在 ID）
- 跨轮上下文丢失或串数据

**根本原因**：当前 ChainAgent 是 **single LLM loop + 17 tool 全挂 + 长 system prompt** 的架构。这种架构在简单场景（< 5 tool）能跑，但 17 tool + 6 种业务流程下 LLM **注意力分散**导致行为不一致。

**重构目标**：参考行业标准（Anthropic「Building Effective Agents」推荐的 **Routing pattern** + LangGraph 框架），把 ChainAgent 重写为基于 **state machine** 的 GraphAgent，让对话像主流 LLM 一样自然，从代码层根除上面的 bug。

**真实预期**（避免过度承诺）：
- 重构能根治"流程类"bug（重复确认、调多余 tool、不推进）
- 重构**不能根治** "模型能力类"问题（数字幻觉、复杂语义算错）—— 这些是模型本身天花板
- 用 deepseek-v4-flash 重构后体感**接近 GPT-4o**，但**达不到 ChatGPT / Claude 体验**
- 想达到主流 LLM 体验需要重构 **+ 模型升级**（V3.5 / Sonnet 4.5），月成本 +50% 到 +400%

---

## 1. 架构总览

```
钉钉 inbound message
       ↓
┌─────────────────────────────────────────────────────────┐
│            GraphAgent（基于 LangGraph）                  │
│                                                         │
│            ┌──────────────┐                             │
│            │   router     │ ← 意图分类（轻量 LLM 调用）  │
│            └──────┬───────┘                             │
│                   │                                     │
│   ┌───────────────┼─────────────────────────────────┐   │
│   ↓               ↓                                 ↓   │
│ ┌────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌─────┐ ┌──┐ │
│ │chat│ │query │ │contract│adjust│ │voucher│adjust│ │  │ │
│ │    │ │      │ │       │ │price │ │      │stock │ │..│ │
│ └────┘ └──────┘ └──────┘ └──────┘ └──────┘ └─────┘ └──┘ │
│                                                         │
│  每个子图是 3-5 个节点的小 state machine：               │
│   - 节点用代码控制流转                                  │
│   - LLM 在每节点内只做单一职责判断                       │
│   - tool 数量从 17 降到 3-4 个/子图                     │
└─────────────────────────────────────────────────────────┘
       ↓
SessionMemory（Redis，per-user 隔离已实现）
ConversationLog（Postgres，复合 unique 已实现）
```

**关键差异 vs 当前 ChainAgent**：

| 维度 | 旧（ChainAgent） | 新（GraphAgent）|
|---|---|---|
| 流程控制 | LLM 自己决定（靠 prompt 准则鼓励）| **代码控制 state machine** |
| Tool 数 / 次调用 | 17 个全挂 | 平均 3-4 个/子图 |
| Prompt 长度 | system prompt + 12 条准则 | 短而聚焦的 subgraph prompt |
| Bug 修复方式 | 加一条 prompt 准则（穷举不完）| 加一个节点 / 字段（结构化）|
| 跨轮状态 | 都塞 prompt 让 LLM 解读 | Pydantic typed state object |

---

## 2. 节点拆分（以最复杂的 contract_subgraph 为例）

```
contract_subgraph:
   ┌─────────────────────┐
   │ 1. resolve_customer │ ← 用 search_customers 拿 customer_id
   └──────┬──────────────┘
          ↓
   ┌──────────────────────┐
   │ 2. resolve_products  │ ← 批量 search_products 拿所有 product_id
   └──────┬───────────────┘
          ↓
   ┌──────────────────────┐
   │ 3. validate_inputs   │ ← 代码验证：customer / items / 价格 / 数量
   └──────┬───────────────┘
          ↓ (有缺失) → ┌─────────────┐
          ↓            │ 4a. ask_user │ → END（等用户回复 round 2）
          ↓            └─────────────┘
   ┌──────────────────────┐
   │ 4b. generate_contract│ ← 调 generate_contract_draft tool
   └──────┬───────────────┘
          ↓
   ┌──────────────────────┐
   │ 5. format_response   │ ← 输出钉钉文本（不再让 LLM 写）
   └──────────────────────┘
```

**节点设计原则**：

- **每节点一个 LLM 调用 or 一组确定的 tool 调用**——不混合
- **节点输入输出都是 Pydantic typed state**——不传字符串
- **流转条件在 add_conditional_edges 里写代码**——不在 prompt 鼓励
- **任何节点失败可以走错误兜底节点**

7 个子图各自的节点结构（详细在 implementation plan 里展开）：

| Subgraph | 节点数 | 主要节点 |
|---|---|---|
| contract | 5-6 | resolve_customer / resolve_products / validate / generate / format |
| quote | 4 | resolve_customer / resolve_products / generate / format |
| voucher | 5 | resolve_orders / preview_drafts / wait_confirm / create_drafts / format |
| adjust_price | 5 | resolve_customer / resolve_product / preview / wait_confirm / create / format |
| adjust_stock | 4 | resolve_product / preview / wait_confirm / create |
| query | 3 | classify_query_type / call_tool / format |
| chat | 1 | direct_response（短 prompt 直接回复）|

---

## 3. State Schema（Pydantic typed）

跨节点共享的 state 用 Pydantic BaseModel 强制类型。

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
    # === 输入（router 解析后）===
    user_message: str
    hub_user_id: int
    conversation_id: str
    extracted_hints: dict  # 用户消息抽出的非结构化 hints

    # === 渐进建立 ===
    customer: CustomerInfo | None = None
    products: list[ProductInfo] = []
    items: list[ContractItem] = []
    shipping: ShippingInfo = ShippingInfo()  # 可选字段

    # === 错误 / 缺失 ===
    missing_fields: list[str] = []  # ["customer", "items[1].price", ...]
    errors: list[str] = []

    # === 输出 ===
    final_response: str | None = None
    file_sent: bool = False
    draft_id: int | None = None
```

**好处**：

- LLM 看到 typed schema 后**填字段**（不再凭印象输出）
- 节点之间数据传递无歧义（不像旧 prompt 有"我以为 LLM 知道"陷阱）
- 缺什么字段一目了然 → 自动决定下一节点（validate 节点直接看 missing_fields）

---

## 4. Tool 重组：17 个 → 子图本地化

| Subgraph | 挂载的 tool（少 + 聚焦） | 数量 |
|---|---|---|
| **router** | 0 个 — 自己用 LLM JSON 输出 | 0 |
| **chat** | 0 个 — 直接 LLM 文本回复 | 0 |
| **query** | search_products / search_customers / get_customer_history / check_inventory / search_orders / get_order_detail / get_customer_balance / get_inventory_aging / get_product_detail / analyze_top_customers / analyze_slow_moving_products | 11 |
| **contract** | search_customers / search_products / get_customer_history / generate_contract_draft | 4 |
| **quote** | search_customers / search_products / generate_price_quote | 3 |
| **voucher** | search_orders / get_order_detail / create_voucher_draft | 3 |
| **adjust_price** | search_customers / search_products / get_product_customer_prices / adjust_price_request | 4 |
| **adjust_stock** | search_products / check_inventory / adjust_stock_request | 3 |

**LLM 在写场景看到的 tool 数从 17 → 3-4**，attention 集中度大幅提升。

**query 子图挂 11 个**有点多，但已是只读 tool，并且查询本身就是"高歧义"场景（"查 X" 可能是商品/客户/订单/库存等）—— 这种情况让 LLM 在 11 个查询 tool 里挑比拆成 3 个子图（用户意图分类不准导致路由错）更合理。

---

## 5. Router 设计

### 5.1 Intent 候选

```python
class Intent(str, Enum):
    CHAT = "chat"                # "在吗" / "你能做什么" / 闲聊
    QUERY = "query"              # 查 X / 看 X / 多少 / 余额等
    CONTRACT = "contract"        # 写合同 / 出合同 / 生成合同
    QUOTE = "quote"              # 报价 / 出报价单
    VOUCHER = "voucher"          # 做凭证 / 报销
    ADJUST_PRICE = "adjust_price" # 调价 / 改价格
    ADJUST_STOCK = "adjust_stock" # 调库存 / 库存校正
    CONFIRM = "confirm"          # 用户回"是" / "确认" / "对" 等
    UNKNOWN = "unknown"          # 兜底
```

### 5.2 Router 节点实现

```python
async def router_node(state: AgentState) -> AgentState:
    """轻量 LLM 调用做意图分类。

    prompt 极短（< 500 token）+ few-shot examples，返回 JSON。
    """
    prompt = build_router_prompt(state.user_message)
    resp = await llm.chat(messages=prompt, response_format={"type": "json_object"})
    parsed = json.loads(resp.text)
    state.intent = Intent(parsed["intent"])
    state.confidence = parsed["confidence"]
    state.extracted_hints = parsed.get("hints", {})

    # 兜底：confidence 低 → fallback chat（让 LLM 反问澄清）
    if state.confidence < 0.7:
        state.intent = Intent.CHAT
    return state
```

### 5.3 Confidence 阈值与兜底

- `confidence ≥ 0.9` → 直接路由
- `0.7 ≤ confidence < 0.9` → 路由 + 在子图开头加"如果不是 X 意图请告知"友好提示
- `confidence < 0.7` → 走 chat 子图请求澄清

### 5.4 特殊 Intent 处理

- **CONFIRM**（用户回 "是" / "确认" / "对" 等）：
  - **不**作为独立子图；router 检测到 CONFIRM 后从 SessionMemory 读上轮 `round_state.last_intent.tool`，路由到对应子图（如上轮是 voucher → 走 voucher 子图的 `confirm` 节点）
  - 如果 round_state 为空（首条消息就是"是"）→ 走 chat 子图请求澄清
- **UNKNOWN**：直接 fallback chat 子图，让 LLM 反问澄清意图

---

## 6. 旧代码处理

| 文件 | 处理 | 原因 |
|---|---|---|
| `chain_agent.py` (505 行) | **删除** | 整体被 GraphAgent 替代 |
| `context_builder.py` (363 行) | **删除** | state schema 替代了 budget 截断 + must_keep 逻辑 |
| `prompt/builder.py` (340 行) | **保留**业务词典 + 同义词 + render_synonyms，**删除** 12 条行为准则、render_few_shots 旧版 | 业务词典仍然有用 |
| `prompt/few_shots.py` | **删除**或重构 | 各子图自己的 few-shots 散落在 `subgraph_prompts/` |
| `tools/registry.py` | **保留**核心，加 `subgraph_filter` 参数让 schema_for_user 按子图过滤 | 写门禁 / ConfirmGate / 实体提取等都还要用 |
| `tools/confirm_gate.py` | **保留** | 写操作门禁仍然需要（voucher / price / stock）|
| `tool_logger.py` | **保留** | 新架构同样需要 tool 调用日志 |
| `memory/session.py` | **保留** | per-user round_state 已就绪（v8 review #16-#19）|
| `memory/loader.py` | **保留** | 用户/客户/商品 memory 仍然要加载到 prompt |
| `llm_client.py` | **保留**核心，可能加 router 专用的轻量 chat 方法 | retry + DeepSeek 协议复用 |
| 12 条行为准则（3a-3l）| **全部删除** | 流程逻辑搬到 state machine 代码层 |

---

## 7. 文件结构（新）

```
backend/hub/agent/
  chain_agent.py            ← 删
  context_builder.py        ← 删

  graph/                    ← 新
    __init__.py
    agent.py                ← GraphAgent 顶层入口（替代 ChainAgent）
    state.py                ← Pydantic state schemas（每个子图一个）
    router.py               ← 意图分类节点 + intent enum
    subgraphs/
      __init__.py
      chat.py
      query.py
      contract.py
      quote.py
      voucher.py
      adjust_price.py
      adjust_stock.py
    nodes/                  ← 跨子图复用的节点
      __init__.py
      resolve_customer.py
      resolve_products.py
      validate_inputs.py
      ask_user.py
      format_response.py

  prompt/
    builder.py              ← 留业务词典/同义词，删行为准则
    intent_router.py        ← 新：意图分类专用 prompt
    subgraph_prompts/       ← 新：每个子图自己的短 prompt
      __init__.py
      chat.py
      query.py
      contract.py
      quote.py
      voucher.py
      adjust_price.py
      adjust_stock.py

  tools/                    ← 全保留，registry 加 subgraph_filter
  memory/                   ← 全保留
  llm_client.py             ← 保留，可能加 chat_json() 方法供 router 用
```

---

## 8. 测试策略

### 8.1 单元测试（每个节点）

每个节点是 async 函数，输入/输出 typed state，**易于单测**。

```python
async def test_resolve_customer_node_happy():
    state = ContractState(
        user_message="给得帆做合同",
        hub_user_id=1, conversation_id="c1",
        extracted_hints={"customer_name": "得帆"},
    )
    state = await resolve_customer_node(state, mock_erp)
    assert state.customer.id == 11
    assert state.customer.name == "广州市得帆..."

async def test_validate_inputs_missing_qty():
    state = ContractState(...)
    state.items = [ContractItem(product_id=5030, qty=0, price=4000)]
    state = await validate_inputs_node(state)
    assert "items[0].qty" in state.missing_fields
```

### 8.2 子图集成测试

完整跑一个子图（mock LLM + 真 state schema），断言流转正确。

### 8.3 Router 准确率测试

50+ 标注 case 测意图分类，target ≥ 95%。失败时输出 confusion matrix 看哪类误判。

```python
ROUTER_TEST_CASES = [
    ("给阿里写讯飞x5 合同", Intent.CONTRACT),
    ("查 SKU100 库存", Intent.QUERY),
    ("把这周差旅做凭证", Intent.VOUCHER),
    ("是", Intent.CONFIRM),
    ("在吗", Intent.CHAT),
    # ... 50 条
]
```

### 8.4 端到端真 LLM eval

用真 DeepSeek + 真 ERP 跑：
- Plan 6 既有 30 case gold set（迁移）
- 加 6 个用户故事的 happy path

target：满意度 ≥ 80%。

### 8.5 旧测试迁移策略

旧 ChainAgent 660+ 单测：

| 类别 | 处理 |
|---|---|
| `test_chain_agent.py` | **重写**为 `test_graph_agent.py` |
| `test_context_builder.py` | **删除**（context_builder 删了）|
| `test_prompt_builder.py` | **保留**业务词典/同义词部分，删行为准则相关 |
| `test_memory_session.py` | **全保留**（已是 per-user 隔离）|
| `test_tool_registry.py` | **保留**+ 加 subgraph_filter 测试 |
| `test_generate_tools.py` | **全保留**（tool 实现没变）|
| `test_admin_tasks_with_agent.py` | **保留**（任务详情页对接 ConversationLog）|
| `test_eval_gold_set.py` | **保留**（30 case 迁到新架构）|

---

## 9. Timeline 与 Milestone

| Milestone | 内容 | 工时 | 验收 |
|---|---|---|---|
| **M1: 基建** | LangGraph 接入；GraphAgent 框架；state schemas；router 节点；chat 子图（最简，1 节点）| 1 天 | "在吗" / "你能做什么" 走通 |
| **M2: query 子图** | 11 个 ERP/analyze 读 tool 重组；query_subgraph 3 节点 | 1 天 | 用户故事 query 类（查产品/客户/订单/库存）跑通 |
| **M3: contract 子图** | 5 节点（resolve_customer / resolve_products / validate / generate / format）；最复杂 | 1.5 天 | 故事 1（合同生成）跑通含 shipping 顶层参数 |
| **M4: 写操作子图** | voucher / adjust_price / adjust_stock 三子图；ConfirmGate 接入 | 1.5 天 | 故事 2-4（凭证/调价/库存草稿）跑通 |
| **M5: quote 子图** | 与合同结构相似但更简单 | 0.5 天 | 报价单生成跑通 |
| **M6: 接入** | dingtalk_inbound handler 切换；删 ChainAgent / context_builder / 12 条 prompt 准则；旧测试迁移 | 0.5 天 | 全部代码切换到新架构，旧文件不存在 |
| **M7: 测试 + 真 LLM eval** | 单测全过；真 DeepSeek 跑 6 故事 + 30 case eval；满意度 ≥ 80% | 2 天 | release gate 双项达标 |
| **总** | | **8 天** | |

---

## 10. 风险点 + 缓解

| 风险 | 缓解 |
|---|---|
| LangGraph 学习曲线 / 调试难度 | M1 先做最简 chat 子图练手；用 LangGraph 的 `graph.get_state(...)` debug; 复杂场景必要时用 LangSmith |
| Router 分类不准（confusion） | 准确率测试 ≥ 95% 才进 M2；confidence < 0.7 兜底走 chat |
| State schema 频繁变更 | Pydantic 容错（多余字段 ignore）+ 跨轮加载用 schema version 字段做兼容 |
| 真 LLM eval 不达 80% | M3 后用 5 case 跑 eval 看趋势；不达预期 M4-M5 期间调整 prompt / few-shots |
| Plan 6 staging 进度暂停 | 接受暂停 1.5 周；重构完一次性回归 6 用户故事 |
| 17 个 tool 业务逻辑 bug 隐藏 | tool 实现保留不动（我们只重组架构），减少风险面 |
| LangGraph 依赖体积（~50MB） | 接受；Plan 6 已包了 dingtalk-stream / tiktoken / openpyxl 等大依赖 |

---

## 11. Success Criteria（重构完成的标准）

| 维度 | 目标 | 验证方式 |
|---|---|---|
| **自然对话** | 6 个用户故事每个 1-2 round 完成 | staging 跑 6 故事全过，截图存档 |
| **意图准确率** | router ≥ 95% | 50+ case 自动测 |
| **不再 patch bug** | 用户日常使用 1 周内不出现"反复确认 / 调多余 tool / 不推进"类 bug | staging 用户主观评价 |
| **延迟** | p50 < 5s, p99 < 15s | 真 LLM eval 期间统计 |
| **成本** | 月预算 ≤ ¥3K（router 增 ¥200，但 main LLM context 短了省回来）| 1 周 staging 数据外推 |
| **真 LLM eval** | 30 case ≥ 80% 满意度 | pytest -m eval |
| **测试覆盖** | 单测 ≥ 95% pass，含每个节点 + 每个子图 + router | pytest -q |

---

## 12. 不在本次重构范围内的事（YAGNI）

- ❌ **不做** LangSmith 接入（observability，将来要做）
- ❌ **不做** streaming 输出（钉钉机器人不要求 streaming）
- ❌ **不做** multi-agent 协作（单 agent + 子图就够 17 tool 场景）
- ❌ **不做** 工具调用结果向量化检索（目前 tool 数和数据量不需要）
- ❌ **不改**任何 tool 的实现（17 个 tool 业务逻辑保留不动，只重组挂载方式）
- ❌ **不改**模型（继续用 deepseek-v4-flash；模型升级是重构完后单独评估）
- ❌ **不改** ConfirmGate / 写门禁逻辑（已在 v6/v7 review 反复打磨稳定）

---

## 13. 重构完后的下一步建议（不属于本 spec）

1. **测体感**：用真 v4-flash 跑完 staging 6 故事 + 30 eval case
2. **如果体感够好** → 维持 v4-flash，月成本不变
3. **如果想更自然** → 单独花 1 天测 DeepSeek-V3.5 的体感差异 → 评估是否升级模型（月成本 +50%）
4. **如果要"跟 Claude 聊天一样"** → 考虑 Claude Sonnet 4.5（月成本 +200-400%）

---

## 14. 决策记录（brainstorming 期间已 fix 的）

1. **重构策略**：A 一次性重写（不渐进，因 staging 还没合并 main）
2. **State machine 框架**：B LangGraph（行业标准，社区生态）
3. **意图前置**：B 轻量 LLM router（行业最佳实践，Anthropic 官方推荐）
4. **预期管理**：重构能根治"流程类"bug 80%，但模型本身能力是天花板（不超 GPT-4o）

---

## 附录 A：参考资料

- Anthropic 「Building Effective Agents」（2024 末）：<https://www.anthropic.com/engineering/building-effective-agents>
- LangGraph 文档：<https://langchain-ai.github.io/langgraph/>
- OpenAI Swarm（multi-agent handoff 模式）：<https://github.com/openai/swarm>
- 项目内既有文档：
  - `docs/superpowers/plans/2026-04-29-hub-agent-implementation.md`（Plan 6 原始 plan）
  - `docs/superpowers/plans/notes/2026-05-01-plan6-final-report.md`（v8 staging 总结）
