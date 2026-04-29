# HUB Agent（Plan 6）设计文档

**日期**：2026-04-29
**作者**：用户 + Claude Opus 4.7（brainstorming session）
**状态**：待 review
**前置依赖**：Plan 1-5（C 阶段）已完成

---

## 0. 背景与愿景

### 0.1 起因

C 阶段（Plan 1-5）落地了"钉钉 → HUB → ERP"的最小闭环：用户在钉钉发 `/绑定 X` / `查 SKU100` 等命令，HUB 用 RuleParser+LLMParser **单步意图解析**调 ERP 单个 endpoint 返回结果。

实际部署后暴露根本性局限：每个自然语言变体都要扩一条正则 / ERP 一条分词规则，不可持续。例：
- `查讯飞x5` → 修了 ERP 中英分词后能用
- `查讯飞x5的库存` → "讯飞x5的库存"被当一个 keyword 塞 ERP，AND ILIKE 匹不上 → 0 命中
- `帮我看那个客户的所有未付款订单` → 完全不能解析

### 0.2 愿景

把钉钉机器人从"单步查询"升级成 **LLM-driven Agent**：

- 用户用自然语言描述任务（不限于"查 X"格式）
- agent 自主决定调哪些工具、几次工具、怎么综合结果
- 覆盖：查询 + 数据分析 + 合同生成 + 凭证 / 调价审批
- 长期愿景：把 ERP 沉淀的客户行为 / 价格历史 / 库存周转**用起来**做决策辅助，而不是当查询接口

### 0.3 范围（C 阶段 → 桥到 B/D 阶段）

Plan 6 是 **C 阶段 → B 阶段（合同）/ D 阶段（凭证）的桥梁**：
- 把 C 阶段的端口接口（IntentParser / DownstreamAdapter / CapabilityProvider）升级成 agent 可用的工具集
- 完成 spec §20.1 B 阶段规划的合同生成核心能力（HUB 这边）
- 启动 D 阶段的凭证 / 调价审批基础架构（ERP 写入路径走审批 inbox）

---

## 1. 写操作的边界（B'）

agent 能"做"的事分两类，处理路径不同：

### 1.1 生成型输出（不写 ERP）

agent 直接生成文档发回请求人，**不需要审核**。请求人自己用，错了自己改。

| 场景 | 输出 |
|---|---|
| 写销售合同 | docx 文件发钉钉（销售拿走自己用） |
| 写报价单 | Excel 文件发钉钉 |
| 数据汇总报告 | docx / Excel / 文本卡片 |
| 多客户名单导出 | Excel 文件 |

**对应 tool**：`generate_contract_draft` / `generate_price_quote` / `export_to_excel` 等。
**权限**：`usecase.generate_contract.use` 等用户级权限即可，不需要审批。

### 1.2 写入 ERP 结构化数据（必须审批）

agent 生成草稿 → 推送到**业务角色审批 inbox** → 通过后落 ERP。

| 场景 | 草稿表 | 审批角色（HUB） | 落 ERP 后的写操作 |
|---|---|---|---|
| 凭证生成（D 阶段核心）| `voucher_draft` | `bot_user_finance`（会计）| ERP `POST /api/v1/vouchers` |
| 价格调整 | `price_adjustment_request` | `bot_user_sales`（销售主管）| ERP `PATCH /api/v1/products/{id}` |
| 库存调整（盘点 / 调拨）| `stock_adjustment_request` | `bot_user_finance`（仓管）| ERP `POST /api/v1/stock/adjust` |

**对应 tool**：`create_voucher_draft` / `create_price_adjustment_request` / `create_stock_adjustment_request`。
**权限**：tool 调用要 `usecase.X.use`；审批要专门的 `usecase.X.approve` 权限（新增）。

### 1.3 排除项

明确不在 Plan 6 范围（留给后续）：
- 创建 ERP 主数据（客户 / 产品 / 供应商）— 高风险写操作，需独立设计
- 删除任何 ERP 数据 — agent 永远不能 delete
- 跨企业 / 跨账套写操作 — 暂只支持单账套
- 工资单 / 薪酬类敏感数据 — 暂不开放给 agent

---

## 2. 系统架构

### 2.1 整体数据流

```
钉钉用户消息 → DingTalkStreamAdapter → 入站任务 → Worker
    ↓
RuleParser（命中显式命令直接走，如 /绑定 /解绑）
    ↓ rule miss
ChainAgent（替代 Plan 4 的 ChainParser）
    ├── 加载 memory（会话 + 用户 + 涉及实体）
    ├── 组装 system prompt（schema + 业务词典 + few-shots）
    ├── LLM round 1: 决策调哪些 tool
    ├── 执行 tool calls（每个走 require_permissions）
    ├── LLM round 2: 综合结果 / 决定再调 tool / 反问澄清
    ├── ...（最多 5 round）
    └── 输出文本回复 / 文档 / 草稿提交审批
    ↓
DingTalkSender 发回钉钉用户
    ↓
异步：Memory 抽取 task（LLM mini round 抽事实回写 user/customer/product memory）
异步：conversation_log + tool_call_log 写入（admin 后台决策链溯源）
```

### 2.2 与 C 阶段的关系

| C 阶段组件 | Plan 6 处理 |
|---|---|
| `RuleParser` | **保留**，作为兜底（B 选项失败兜底）+ 处理显式命令 |
| `LLMParser`（schema-guided 单步） | **替换**为 `ChainAgent`（multi-round + tool calling） |
| `ChainParser`（rule → llm） | **替换**为 `RuleParser → ChainAgent`（结构相同，agent 替代 LLMParser） |
| `Erp4Adapter` | **保留**，方法变成 agent 的 tool registry 来源 |
| `IdentityService + require_permissions` | **保留**，每次 tool call 必跑 |
| `ConversationStateRepository`（5min TTL） | **扩展**为 `ConversationContext`（30min TTL + 对话历史 + 实体引用） |
| `task_logger` | **升级**为 `conversation_log + tool_call_log`（决策链溯源） |
| `LiveStreamPublisher` | **保留**，事件 schema 加 agent_decision 字段 |
| `BindingService` 等业务 service | **保留**，被 tool 调用 |

C 阶段 90% 代码无改动，只是 ChainParser → ChainAgent 这一层被替换。

---

## 3. 核心组件

### 3.1 Tool Registry（自动 Schema 生成 + 写操作硬门禁）

参考 ERP AIChatBot 的 `schema_registry.py` — 从代码自动生成 LLM 可读的 tool schema，避免手工维护两份。

**关键约束**：写类 tool 不能只靠 system prompt 教 LLM 自觉先确认，必须在 ToolRegistry 层**前置硬校验**。LLM 偶尔会跳过 confirm 直接调写 tool — 这层必须挡住。

```python
# hub/agent/tools/registry.py
from enum import StrEnum

class ToolType(StrEnum):
    READ = "read"                # search_*, get_*, check_*（无副作用）
    GENERATE = "generate"        # generate_contract / quote / Excel（生成文件给请求人本人）
    WRITE_DRAFT = "write_draft"  # create_voucher_draft / create_price_adjustment_request 等
    WRITE_ERP = "write_erp"      # 直接落 ERP（暂无 — 批量审批走 admin 路由不走 tool）

class ToolRegistry:
    def register(self, name, fn, *, perm: str, description: str,
                 tool_type: ToolType):
        """从函数签名 + docstring 自动生成 OpenAI function schema；
        写类 tool 必须传 tool_type=WRITE_DRAFT/WRITE_ERP。"""

    async def schema_for_user(self, hub_user_id: int) -> list[dict]:
        """按用户权限过滤后的 tool schema list（注入 LLM）。"""

    async def call(self, name, args, *, hub_user_id: int, acting_as: int,
                   conversation_id: str, round_idx: int) -> Any:
        """统一入口：写操作硬门禁 → require_permissions → 调 fn → 记 tool_call_log。"""
        tool = self._tools[name]

        # ❗ 写类 tool 硬门禁：必须带有效 confirmation_token
        if tool.tool_type in (ToolType.WRITE_DRAFT, ToolType.WRITE_ERP):
            token = args.pop("confirmation_token", None)  # 从 args 取出（不传给 fn）
            if not await self._is_confirmed(conversation_id, name, args, token):
                raise UnconfirmedWriteToolError(
                    f"写类 tool '{name}' 必须先经用户确认。"
                    "请用 text 把操作预览发给用户，待用户回'是'后由 agent 重新发起带 token 的调用。"
                )
        # ... require_permissions / call fn / log
```

**confirmation_token 协议**：

`token = sha256(conversation_id + ":" + tool_name + ":" + canonical_json(args))`

- 第一次 LLM 调写 tool 不带 token → `UnconfirmedWriteToolError` 抛 LLM；
- ChainAgent 把 error 注入 message → LLM 改用 text 给用户发预览；
- 用户回 "是"/"确认" → ChainAgent 算出待确认动作的 token 写 Redis（`hub:agent:confirmed:<conversation_id>` SET，TTL 30min）+ 在下一轮 system message 中告知 LLM "用户已确认，token=<x>，请重试调用并带上这个 token"；
- LLM 第二次调用带正确 token → ToolRegistry `_is_confirmed` 命中 Redis → 通过；
- args 任何变化 → token 不同 → 必须重新确认（防"用户确认了 ¥1000，LLM 偷偷改成 ¥10000"攻击）。

测试覆盖（必须）：
- 未带 token 调写 tool → 拦截
- 错 token 调写 tool → 拦截
- 正确 token + args 变化 → 拦截
- 正确 token + args 一致 → 通过

第一版注册的 tool（档 3）：

| Tool | 实现来源 | 权限码 | 类型 |
|---|---|---|---|
| `search_products` | Erp4Adapter | `usecase.query_product.use` | 读 |
| `search_customers` | Erp4Adapter | `usecase.query_customer.use`（新增）| 读 |
| `get_product_detail` | Erp4Adapter | `usecase.query_product.use` | 读 |
| `get_customer_history` | Erp4Adapter | `usecase.query_customer_history.use` | 读 |
| `check_inventory` | Erp4Adapter（新加 endpoint）| `usecase.query_inventory.use`（新增）| 读 |
| `search_orders` | Erp4Adapter（新加 endpoint）| `usecase.query_orders.use`（新增）| 读 |
| `get_customer_balance` | Erp4Adapter（新加 endpoint）| `usecase.query_customer_balance.use`（新增）| 读 |
| `get_inventory_aging` | Erp4Adapter（新加 endpoint）| `usecase.query_inventory_aging.use`（新增）| 读 |
| `analyze_top_customers` | HUB 自己（聚合多 tool）| `usecase.analyze.use`（新增）| 读 |
| `analyze_slow_moving_products` | HUB 自己 | `usecase.analyze.use` | 读 |
| `generate_contract_draft` | HUB（python-docx）| `usecase.generate_contract.use` | 生成 |
| `generate_price_quote` | HUB | `usecase.generate_quote.use`（新增）| 生成 |
| `export_to_excel` | HUB（openpyxl）| `usecase.export.use`（新增）| 生成 |
| `create_voucher_draft` | HUB（写 voucher_draft 表）| `usecase.create_voucher.use` | 写（草稿）|
| `create_price_adjustment_request` | HUB | `usecase.adjust_price.use`（新增）| 写（草稿）|
| `create_stock_adjustment_request` | HUB | `usecase.adjust_stock.use`（新增）| 写（草稿）|

合计 **16 个 tool**。

### 3.2 ChainAgent（Agent Loop + 调用前裁剪）

```python
# hub/agent/chain_agent.py
class ChainAgent:
    MAX_ROUNDS = 5
    MAX_PROMPT_TOKEN = 18_000   # 留 2K buffer 给 LLM 输出（模型 context 上限 32K-128K）
    LLM_TIMEOUT = 30.0

    async def run(self, user_message, *, hub_user_id, conversation_id, acting_as):
        memory = await self.memory_loader.load(hub_user_id, conversation_id)
        tools = await self.registry.schema_for_user(hub_user_id)

        for round_idx in range(self.MAX_ROUNDS):
            # ❗ 调用前 token 估算 + 裁剪（不能等响应回来才算 — 那时已经超过 context 报错了）
            messages = await self.context_builder.build_round(
                round_idx=round_idx,
                base_memory=memory,
                tools_schema=tools,
                conversation_history=self.history,
                latest_user_message=user_message if round_idx == 0 else None,
                budget_token=self.MAX_PROMPT_TOKEN,
            )

            llm_resp = await asyncio.wait_for(
                self.llm.chat(messages, tools=tools, temperature=0.0),
                timeout=self.LLM_TIMEOUT,
            )
            self.history.append(llm_resp)

            if llm_resp.is_tool_call:
                for call in llm_resp.tool_calls:
                    try:
                        result = await self.registry.call(
                            call.name, call.args,
                            hub_user_id=hub_user_id, acting_as=acting_as,
                            conversation_id=conversation_id, round_idx=round_idx,
                        )
                        self.history.append(ToolResult(call.id, result))
                    except UnconfirmedWriteToolError as e:
                        # 写门禁拦截 → 把错误注入 LLM message 让它走"先用 text 预览"路径
                        self.history.append(ToolResult(call.id, {"error": str(e)}))
                continue

            if llm_resp.is_clarification:
                return AgentResult.clarification(llm_resp.text)
            return AgentResult.text(llm_resp.text)

        raise AgentMaxRoundsExceeded()
```

**ContextBuilder 调用前裁剪策略**（按优先级裁，从上到下保留率递减）：

| 优先级 | 内容 | 裁剪规则 |
|---|---|---|
| **必保** | system prompt（行为准则 + tool schema + 业务词典）| 全保 |
| **必保** | 当前 user 消息 + 最近 1 round LLM 输出 | 全保 |
| **必保** | 当前轮 confirm_state（若有写操作待确认）| 全保 |
| 高 | 用户层 memory（用户偏好）| 全保（已有 1K 上限）|
| 高 | 当前对话引用的客户/商品 memory | 全保（每个 500 / 200 上限）|
| 中 | 最近 3 round 对话历史 | 全保 |
| 中 | tool result 摘要：单条 < 500 token 全保；> 500 token **保留 type/count 删除明细列表** | 摘要：保 schema 信息删数据行 |
| 低 | 4 round 之前的对话历史 | 摘要：每 round 压成 1-2 句"调了 X 拿到 Y" |
| 低 | 没引用到的 customer/product memory | 不注入 |

每 round 前估算 `total_token = sum(len(msg) for msg in messages)`（用 tiktoken 或 simple `len // 3` 估算）；超 18K 按上面优先级降级裁剪直到达标。

测试覆盖：
- 大 tool result（搜出 1000 个商品）→ 摘要后注入
- memory 多客户（10 个引用）→ 优先注入对话最新提到的，超的不注入
- 多 round 后历史压缩 → 4 round 之前的对话变成"action summary"

### 3.3 Memory 三层（ChatGPT 模式）

```python
# hub/agent/memory/
class MemoryLoader:
    async def load(self, hub_user_id: int, conversation_id: str) -> Memory:
        """组装当前对话的完整 memory 上下文。"""
        return Memory(
            session=await self.session_layer.load(conversation_id),  # Redis 30min
            user=await self.user_layer.load(hub_user_id),  # Postgres
            customers=await self.customer_layer.load_referenced(conversation_id),
            products=await self.product_layer.load_referenced(conversation_id),
        )

class MemoryWriter:
    """每次成功对话后异步触发：LLM mini round 抽事实回写。
    必须经 should_extract gate 才进 LLM 抽取，避免闲聊/失败/无业务对话浪费 token。"""

    async def extract_and_write(self, conversation_id: str,
                                conversation_log_id: int,
                                tool_call_logs: list[ToolCallLog]):
        if not self.should_extract(tool_call_logs):
            return  # gate 拦截，不调 LLM 不抽事实
        # 调 LLM 抽：用户偏好 / 客户备注 / 商品异常 → 写库

    @staticmethod
    def should_extract(tool_call_logs: list) -> bool:
        """重要性 gate：满足任一即抽：
        1. 至少 1 次 search/get tool 命中实体（customer_id / product_id）
        2. 至少 1 次写 tool 调用（凭证 / 调价 / 库存调整 / 合同）
        3. 对话 round_count >= 4
        否则跳过抽取（节省成本）。"""
```

**引用实体写入路径**（这是 review P2-#8 的关键）：

ToolRegistry.call 在 tool 返回后**统一提取实体引用**写回会话 memory，下一 round MemoryLoader.load_referenced 才能注入对应客户/商品 memory：

```python
# hub/agent/tools/registry.py 内（tool fn 返回后）
async def call(self, name, args, ..., conversation_id):
    result = await tool.fn(...)
    # ❗ 统一提取实体引用，写回 session
    await self._extract_and_record_entities(conversation_id, result)
    return result

async def _extract_and_record_entities(self, conversation_id, result):
    """从 tool 返回的 dict / list 中提取 customer_id / product_id，
    写入 Redis session_memory.referenced_entities。"""
    customer_ids = self._extract_ids(result, key_patterns=["customer_id", "customer_ids"])
    product_ids = self._extract_ids(result, key_patterns=["product_id", "product_ids", "items[].id"])
    if customer_ids or product_ids:
        await self.session_memory.add_entity_refs(
            conversation_id, customer_ids=customer_ids, product_ids=product_ids,
        )
```

测试覆盖：
- search_customers 返多个 customer → 全部 ID 写入 session
- 下一 round MemoryLoader.load_referenced 取到这些 ID 的 customer_memory 注入 prompt
- get_product_detail 返单个 product → 写入 session

| 层 | 存储 | TTL | 触发注入 |
|---|---|---|---|
| 会话层 | Redis `hub:agent:conv:<conversation_id>` | 30 分钟 | 全量注入 |
| 用户层 | Postgres `user_memory` 表 | 永久（admin 可清）| 当前用户全量注入 |
| 客户层 | Postgres `customer_memory` 表 | 永久 | 仅 session.referenced_entities.customer_ids 注入 |
| 商品层 | Postgres `product_memory` 表 | 永久 | 仅 session.referenced_entities.product_ids 注入 |

每层 token 上限：会话 4K / 用户 1K / 单个客户 500 / 单个商品 200。
总注入预算：10K token（system prompt + memory + 5 个客户 + 5 个商品 ≈ 10K）。

### 3.4 失败兜底（B+D）

```python
# 工程层（B）
try:
    result = await agent.run(message, ...)
    return result
except (LLMServiceError, AgentMaxRoundsExceeded, json.JSONDecodeError):
    # 降级到 RuleParser
    return await rule_parser.handle(message, ...)
except Exception as e:
    # 完全失败，friendly 提示
    return "AI 处理出了点问题，已切回基础查询。请用更明确的方式描述（例：查 SKU100）"

# Prompt 层（D）—— system prompt 模板里：
"""
你是 HUB 业务 agent，帮销售/会计/仓管等用钉钉处理业务任务。

行为准则：
1. 不确定时**先反问澄清**，不要硬猜：
   ✅ 用户："查那个客户" → 反问"你说的是哪一家？"
   ❌ 不要直接选最近一个客户硬查
2. 工具结果与用户期望不符时**说出来**，不掩盖
3. 写操作（创建凭证 / 调价请求）一定要用户**确认草稿内容**后再调 tool
"""
```

### 3.5 ERP AIChatBot 借鉴的 10 项能力

按 brainstorming 选定（⭐⭐⭐ 5 项 + ⭐⭐ 5 项）：

| # | 能力 | Plan 6 落点 |
|---|---|---|
| 1 | 业务词典 | `hub/agent/prompt/business_dict.py`（"压货"="库龄高商品"等映射注入 system prompt）|
| 2 | 同义词映射 | `hub/agent/prompt/synonyms.py`（输入预处理时归一）|
| 3 | 预设查询模板 | 升级 RuleParser，加更多预设（例 `今日销售` `本月库存周转`）|
| 4 | 意图预分类 | 与 RuleParser 合并 |
| 5 | Schema 自动生成 | `ToolRegistry` 从函数签名 + docstring 自动生成 OpenAI function schema |
| 6 | 权限映射 | `ToolRegistry.schema_for_user(hub_user_id)` 按权限过滤 tool list |
| 7 | Few-shot 示例 | system prompt 塞 5-10 个范例（"用户问 X → agent 调 tool Y → 返 Z"）|
| 11 | temperature=0.0 | LLM 调用统一参数 |
| 13 | Excel 导出 | `export_to_excel` tool；钉钉 SDK 发 .xlsx 文件 |

> 注：原 brainstorming 列表里的 #12「用户反馈收集」**已剔除**（用户决策：不做）— spec / plan 全文不再涉及反馈表 / 反馈 UI / 反馈 UX。

---

## 4. 业务用例（按用户类型）

### 4.1 销售生成合同（核心场景）

**场景**：销售在客户那边没电脑，钉钉发消息让 agent 写合同发回手机。

**对话样例**：
```
用户："给阿里写讯飞x5 50 台的合同，按上次报价"
agent: search_customers("阿里")
       → 多命中 → 反问"你说的是哪一家？1. 阿里巴巴集团  2. 阿里云"
用户："1"
agent: get_customer_history(product=讯飞x5, customer=阿里巴巴, limit=3)
       → 最近 3 单平均 ¥2,499（这就是"上次报价"）
       check_inventory(讯飞x5)
       → 当前库存 49（不够 50，缺 1 台）
       → 反问"阿里巴巴最近平均价 ¥2,499，但 X5 当前库存 49 不够 50 台。改 49 台还是带保留 1 台到货补？"
用户："改 49"
agent: generate_contract_draft(
    template_id=<销售合同标准模板>,
    customer_id=<阿里巴巴>,
    items=[{product=讯飞x5, qty=49, unit_price=2499, total=122451}],
    payment_terms=<从客户 memory 取，默认 30 天>,
)
agent: 发 docx 文件到钉钉 + "合同草稿已生成，请确认"
```

涉及 tool：`search_customers / get_customer_history / check_inventory / generate_contract_draft`
涉及 memory：客户层（阿里巴巴付款习惯）+ 用户层（销售偏好的合同模板）

### 4.2 会计审批凭证（D 阶段）

**场景**：会计每天有几十张零散付款 / 报销，agent 帮忙生成草稿，会计在 HUB 后台批量审。

**对话样例**：
```
会计："把这周差旅报销做凭证"
agent: search_orders(type=expense_reimburse, since=this_week)
       → 12 条
agent: 按规则匹配凭证模板（差旅 → 借：管理费用-差旅 / 贷：库存现金）
       create_voucher_draft × 12（每条单独草稿；ChainAgent 单 round 内可批量调）
agent: 钉钉提醒："本周差旅 12 张凭证草稿已生成，请到 HUB 后台『待审批』审核。"
       同时发钉钉链接：http://hub.example.com/admin/approvals/voucher
```

**审批 inbox**（两阶段提交 + 幂等键 + creating 租约恢复）：

会计登 HUB 后台 → 「待审批」页 → 列表 12 条 → 全选 → 一键通过 → HUB `POST /admin/approvals/voucher/batch-approve`：

**Phase 1（pending → creating → created）**：HUB 端给每条 draft 拿乐观锁标记 `status=creating + creating_started_at=now`，逐个调 ERP `POST /api/v1/vouchers`，body 里塞 `client_request_id="hub-draft-{id}"`：
- 创建成功：`erp_voucher_id=...`、`status=created`、清 `creating_started_at`
- 创建失败：回滚 `status=pending` + 清 `creating_started_at`（下次重试可继续）
- HUB 进程崩溃：draft 卡在 `creating`，下次 batch 看到 `creating_started_at` 已过 5 min 租约就接管，仍用同 `client_request_id`；ERP 端 `voucher.client_request_id` 唯一索引保证不会重复创建（返 `idempotent_replay=True` 的已存在 voucher）

**Phase 2（created → approved）**：所有 phase 1 落 ERP 的 + 入参中本来 status=created 的（重试场景）一次性调 ERP `POST /api/v1/vouchers/batch-approve`：
- 通过：`status=approved + approved_by_hub_user_id + approved_at`
- 失败：保持 `status=created`，可重试 phase 2（不会回到 pending，避免重复创建）

**关键不变量**：
1. 一条 voucher_draft 全程最多对应一条 ERP voucher（client_request_id + 部分唯一索引）
2. 任何中间状态崩溃都可恢复（pending / creating-with-expired-lease / created 都能继续推进）
3. 拒绝（rejected）只能从 pending 走（已 created 的要去 ERP 反审）

涉及 tool：`search_orders / create_voucher_draft`
涉及表：`voucher_draft`（HUB 新增，含 status / creating_started_at / erp_voucher_id）+ ERP `voucher`（**Plan 6 加 client_request_id 字段 + 部分唯一索引 + 幂等回放语义**，见 §14.2）

### 4.3 销售主管审批调价（部分 D 阶段）

**场景**：销售要给老客户特价，请求主管批。

**对话样例**：
```
销售："给阿里巴巴讯飞x5 调价 8 折"
agent: search_customers / search_products / get_customer_history
       → 当前定价 ¥3,999，历史平均 ¥2,499（已经 6 折）
       → 反问"阿里巴巴这款历史平均 ¥2,499，已经是 6 折。你说的 8 折是基于现价还是基于历史价？"
销售："现价 8 折"
agent: create_price_adjustment_request(
    customer_id=<阿里巴巴>,
    product_id=<讯飞x5>,
    new_price=3199.20,  # 现价 0.8
    discount_pct=0.20,
    reason=<销售备注>,
)
agent: "调价请求已提交销售主管审批"
```

主管 HUB 后台审批 → 通过 → HUB 调 ERP `PATCH /api/v1/customer-price-rules` 落库。

### 4.4 数据分析查询（档 3 高级）

**场景**：管理层问"上个月哪个客户买得最多"。

**对话样例**：
```
管理者："上月客户销售 top 10"
agent: analyze_top_customers(period=last_month, top_n=10)
       → 内部组合：search_orders + group by customer + sort by total
       → 返回 [{customer, total, orders, avg_order}, ...]
agent: 文本卡片 + Excel 导出按钮
```

涉及新加的"聚合 tool"：`analyze_top_customers / analyze_slow_moving_products`。
注意：聚合 tool 内部仍走 ERP HTTP API（多次调用 + HUB 这边聚合），**不直连 ERP DB**。

**Bounded pagination + partial_result 语义**：

聚合 tool 不能无限拉 ERP 订单（1000+ 单聚合慢且容易超时）。短期方案：

```python
async def analyze_top_customers(period="last_month", top_n=10, *, acting_as_user_id):
    """top N 客户销售排行（bounded）。"""
    MAX_ORDERS = 1000     # 硬上限
    MAX_PERIOD_DAYS = 90  # 硬上限
    PER_PAGE = 200        # ERP 分页

    days = parse_period_days(period)
    if days > MAX_PERIOD_DAYS:
        days = MAX_PERIOD_DAYS
        partial_period = True

    orders = []
    page = 1
    truncated = False
    while len(orders) < MAX_ORDERS:
        resp = await erp.search_orders(
            since=now()-timedelta(days=days),
            page=page, page_size=PER_PAGE,
            acting_as_user_id=acting_as_user_id,
        )
        orders.extend(resp["items"])
        if len(resp["items"]) < PER_PAGE:
            break
        page += 1
    if len(orders) > MAX_ORDERS:
        orders = orders[:MAX_ORDERS]
        truncated = True

    # 聚合
    return {
        "items": [...],
        "partial_result": truncated or partial_period,
        "data_window": f"近 {days} 天，{len(orders)} 单",
        "notes": "结果不完整：实际订单超 1000 单，仅基于最近 1000 单聚合" if truncated else None,
    }
```

agent 在收到 `partial_result=True` 时**必须**在最终回复中向用户透明说明数据不完整（system prompt 教这条）。

**长期方案**（不在 Plan 6 范围）：ERP 加 analytics endpoint（数据库直接 GROUP BY），HUB 直接调免聚合。Spec §14 加一行"未来 ERP analytics endpoint"。

---

## 5. 数据模型

### 5.1 conversation_log + tool_call_log（新增）

```sql
CREATE TABLE conversation_log (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL UNIQUE,  -- 用户单次对话 (Redis session 同 ID)
    hub_user_id INT REFERENCES hub_user(id),
    channel_userid TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    rounds_count INT DEFAULT 0,
    tokens_used INT DEFAULT 0,
    tokens_cost_yuan DECIMAL(10,4),  -- 估算成本
    final_status TEXT,  -- success / failed_user / failed_system / fallback_to_rule
    error_summary TEXT,
    INDEX idx_user_started (hub_user_id, started_at)
);

CREATE TABLE tool_call_log (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversation_log(conversation_id),
    round_idx INT NOT NULL,  -- 在哪个 round 调的
    tool_name TEXT NOT NULL,
    args_json JSONB,
    result_json JSONB,
    duration_ms INT,
    error TEXT,
    called_at TIMESTAMPTZ DEFAULT NOW(),
    INDEX idx_conv (conversation_id, round_idx)
);

```

### 5.2 三层 memory（新增）

```sql
CREATE TABLE user_memory (
    hub_user_id INT PRIMARY KEY REFERENCES hub_user(id),
    facts JSONB NOT NULL DEFAULT '[]',  -- [{fact, source_conversation, confidence, created_at}]
    preferences JSONB DEFAULT '{}',     -- 偏好的合同模板/付款条款等
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE customer_memory (
    erp_customer_id INT PRIMARY KEY,
    facts JSONB NOT NULL DEFAULT '[]',  -- 议价习惯/付款记录摘要等
    last_referenced_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE product_memory (
    erp_product_id INT PRIMARY KEY,
    facts JSONB NOT NULL DEFAULT '[]',  -- 断货/停产/替代品等
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 5.3 合同模板 + 草稿（新增）

```sql
CREATE TABLE contract_template (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    template_type TEXT NOT NULL,  -- sales / purchase / framework / etc
    file_storage_key TEXT NOT NULL,  -- 加密存的 docx 文件 key
    placeholders JSONB NOT NULL,  -- [{name, type, required}, ...]
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_by_hub_user_id INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 合同草稿表（agent 生成，发给请求人本人，不需审批）
-- 我们仍存它是为了：1. 后续审计 2. 销售改完发回 HUB 重生成
CREATE TABLE contract_draft (
    id SERIAL PRIMARY KEY,
    template_id INT REFERENCES contract_template(id),
    requester_hub_user_id INT NOT NULL,
    customer_id INT NOT NULL,  -- ERP customer_id
    items JSONB NOT NULL,
    rendered_file_storage_key TEXT,  -- 生成的 docx
    status TEXT DEFAULT 'generated',  -- generated / sent / superseded
    conversation_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 5.4 写操作草稿表（新增）

```sql
CREATE TABLE voucher_draft (
    id SERIAL PRIMARY KEY,
    requester_hub_user_id INT NOT NULL,
    voucher_data JSONB NOT NULL,  -- 凭证内容（科目/金额/摘要）
    rule_matched TEXT,  -- 匹配的凭证模板
    -- 状态机（v3 round 2 加 creating 租约）：
    --   pending → creating → created → approved
    --                      ↘  pending（创建失败回滚）
    --   pending → rejected
    --   creating（崩溃残留，5 min 租约过期后下次 batch 接管）→ created
    status TEXT DEFAULT 'pending',
    creating_started_at TIMESTAMPTZ,  -- creating 状态进入时间，用于 5 min 租约判断；created/pending/rejected 时为 NULL
    approved_by_hub_user_id INT,
    approved_at TIMESTAMPTZ,
    rejection_reason TEXT,
    erp_voucher_id INT,  -- 落 ERP 后的 voucher ID（status>=created 时非空）
    conversation_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    INDEX idx_pending (status, created_at),
    INDEX idx_creating_lease (status, creating_started_at)  -- 崩溃恢复扫描用
);
-- 注：HUB 端发到 ERP 创建 voucher 时塞 client_request_id = "hub-draft-{id}"
-- ERP 端给 voucher 表加 client_request_id 字段 + 部分唯一索引（NOT NULL 时唯一）做幂等
-- 见 §14.2 ERP 改动清单

CREATE TABLE price_adjustment_request (
    id SERIAL PRIMARY KEY,
    requester_hub_user_id INT NOT NULL,
    customer_id INT NOT NULL,
    product_id INT NOT NULL,
    current_price DECIMAL(12,2),
    new_price DECIMAL(12,2),
    discount_pct DECIMAL(5,4),
    reason TEXT,
    status TEXT DEFAULT 'pending',
    approved_by_hub_user_id INT,
    approved_at TIMESTAMPTZ,
    rejection_reason TEXT,
    conversation_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE stock_adjustment_request (
    -- 类似 price_adjustment_request
);
```

---

## 6. HUB 后台 UI（admin 部分）

### 6.1 待审批 inbox（新增）

| 子页 | 路由 | 权限 | 功能 |
|---|---|---|---|
| 凭证待审批 | `/admin/approvals/voucher` | `usecase.create_voucher.approve`（新增）| 列表 + 批量勾选 + 一键通过/拒绝；详情抽屉看草稿明细；通过后调 ERP API 落库 |
| 调价待审批 | `/admin/approvals/price` | `usecase.adjust_price.approve`（新增）| 同上 |
| 库存调整待审批 | `/admin/approvals/stock` | `usecase.adjust_stock.approve`（新增）| 同上 |

UI 形态：复用 Plan 5 的 AppTable + AppPagination + AppModal；勾选后顶部 sticky bar 显示"已选 N 项"+ "通过 / 拒绝" 按钮。

### 6.2 合同模板管理（新增）

`/admin/contract-templates` — 上传 docx + 标记占位符（用 `{{customer_name}}` 等）+ 描述 + 启用/禁用。
权限：`platform.contract_templates.write`（新增）。

### 6.3 Agent 决策链查看（升级 Plan 5）

Plan 5 已有 `/admin/tasks/{task_id}` task 详情页（解密 payload + 时间线）。Plan 6 升级：
- 时间线增加 LLM round + tool call 节点
- 显示每 round 的 LLM 决策原因（thought）+ 调的 tool + 结果
- 显示总 token 消耗 + 估算成本

---

## 7. 钉钉端 UX

### 7.1 用户消息分类

| 用户输入 | 处理路径 |
|---|---|
| `/绑定 X` / `/解绑` / `/帮助` | RuleParser 命令路由（不变）|
| 自然语言任务 | ChainAgent |
| 数字（在 pending_choice 状态）| RuleParser select_choice（不变）|
| `是` / `确认` / `yes`（在写操作待确认状态）| ChainAgent 确认 confirm_state（写 Redis confirmed_actions） |

### 7.2 写操作的钉钉提醒

凭证 / 调价 / 库存调整草稿生成后：
- 钉钉给**请求人**："草稿已生成，请到 HUB 后台『待审批』查看（链接）"
- 钉钉给**审批人**：日终汇总（不实时打扰）："今天有 3 张凭证 / 1 个调价请求待审批"

### 7.3 文件下发（合同 / Excel）

合同 docx 和 Excel 通过钉钉机器人发给请求人。**钉钉文件下发的真实链路**：

1. `POST https://oapi.dingtalk.com/media/upload?access_token=<token>&type=file` 上传文件 → 返 `media_id`
2. `POST https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend` body：
   ```json
   {
     "robotCode": "<robotCode>",
     "userIds": ["<dingtalk_userid>"],
     "msgKey": "sampleFile",
     "msgParam": "{\"mediaId\":\"<media_id>\",\"fileName\":\"销售合同_X.docx\",\"fileType\":\"docx\"}"
   }
   ```

**DingTalkSender 必须新增 `send_file` 方法**：

```python
class DingTalkSender:
    async def send_file(self, *, dingtalk_userid: str, file_bytes: bytes,
                        file_name: str, file_type: str = "docx",
                        max_retry: int = 2) -> None:
        """发文件给单个用户。

        实现：
        1. 调 /media/upload 拿 media_id（带 1 次重试）
        2. 调 oToMessages/batchSend 发 sampleFile（带 1 次重试）
        3. 任一步失败 → 抛 DingTalkSendError，由 worker 转死信流
        """
        media_id = await self._upload_media(file_bytes, file_name, file_type)
        await self._send_oto(
            user_ids=[dingtalk_userid], msg_key="sampleFile",
            msg_param={"mediaId": media_id, "fileName": file_name, "fileType": file_type},
        )
```

钉钉 sampleFile 支持的 fileType：`docx / pdf / xlsx / zip` 等。
单文件大小限制：钉钉 API 上限 20MB（实测；超出走分块或退回链接）。

测试覆盖（必须）：
- send_file 上传成功 → 拼对 batchSend 参数
- 上传 5xx → 重试 1 次成功
- 上传 4xx（鉴权失败）→ 立即抛错不重试
- batchSend 失败 → 抛 DingTalkSendError

---

## 8. 安全与权限

### 8.1 权限模型（扩展 Plan 5）

新增权限码：

```
usecase.query_customer.use            # search_customers
usecase.query_inventory.use           # check_inventory
usecase.query_orders.use              # search_orders
usecase.query_customer_balance.use
usecase.query_inventory_aging.use
usecase.analyze.use                   # 数据分析类 tool
usecase.generate_quote.use            # generate_price_quote
usecase.export.use                    # export_to_excel
usecase.adjust_price.use              # 创建调价请求（销售用）
usecase.adjust_price.approve          # 审批调价（销售主管用）
usecase.adjust_stock.use
usecase.adjust_stock.approve
usecase.create_voucher.approve        # 审批凭证（会计用，已有 .use）
platform.contract_templates.write     # 管模板
```

合计新增 **14 个权限码**。

### 8.2 角色升级（扩展 Plan 5）

```
bot_user_basic         → 加 query_customer / query_inventory / query_orders
bot_user_sales         → 加 generate_contract / generate_quote / export / adjust_price.use
bot_user_finance       → 加 create_voucher.use（已有）+ adjust_stock.use
+ 新角色：
  bot_user_sales_lead    → bot_user_sales + adjust_price.approve（销售主管）
  bot_user_finance_lead  → bot_user_finance + create_voucher.approve + adjust_stock.approve
```

### 8.3 写操作审计

每次审批通过 → ERP 写入操作（POST/PATCH）→ 写入 `audit_log`：
- who_hub_user_id（审批人）
- action: `approve_voucher / approve_price_adjust / approve_stock_adjust`
- target_type: `voucher_draft / price_adjustment_request / stock_adjustment_request`
- target_id
- detail：草稿全文 + ERP 返回的资源 ID

### 8.4 防 LLM 幻觉

- **tool 参数严格 schema**：所有 ID 字段（customer_id / product_id / amount）schema 标 type+required+min/max；LLM 输出非法值 → call() 直接拒绝
- **写操作前强制 confirm（前置硬门禁）**：见 §3.1 — ToolRegistry 在 call() 入口对 WRITE_DRAFT/WRITE_ERP 类 tool 强制要求 `confirmation_token`（Redis 已确认动作的 sha256），未确认或 token 不匹配 args 直接抛 `UnconfirmedWriteToolError`，**不依赖 LLM 自觉**。LLM 看到 error 自然走"先用 text 预览给用户"路径
- **金额硬上限**：单凭证金额上限 ¥1M（system_config 可调），超限 agent 直接拒绝创建
- **客户/产品 ID 必须从 search_* 结果引用**：不能 LLM 自己编 ID — 如果 LLM 把没出现在过去 tool result 的 customer_id 传进来，记 warning 但仍由 ERP 端 404 兜底（HUB 端不做白名单校验，因为分页/上下文裁剪后旧 result 可能丢）

---

## 9. 成本控制

### 9.1 5 项硬阈值

| 阈值 | 默认值 | 触发动作 |
|---|---|---|
| 单对话 max LLM rounds | 5 | 第 6 round 直接降级 RuleParser |
| 单对话 max token | 20,000 | 超出 → 失败兜底"对话太复杂" |
| 单用户每天 max conversations | 50 | 超出 → 拒绝 + 友好提示 |
| LLM 调用 timeout | 30s | 超时 → 降级 RuleParser |
| 月总成本预警 | ¥1,000（system_config 可调）| admin 后台告警 + 钉钉发管理员 |

### 9.2 成本可视化

dashboard 加 4 个新指标：
- 今日 LLM 调用数
- 今日总 token（按 input/output 拆）
- 今日估算成本（按当前 active provider 价格表）
- 本月累计成本 / 预算占比（80% / 100% 预警）

每月 1 号自动归档上月数据到 `cost_monthly` 表。

---

## 10. 测试策略

### 10.1 单元测试

| 模块 | 关注点 | 估计 case 数 |
|---|---|---|
| `ToolRegistry` | schema 自动生成 / 权限过滤 / **写门禁拦截 4 case**（无 token / 错 token / args 变化 / 正确 token 通过）/ 实体写入 session | 11 |
| `ChainAgent` | 单 round / 多 round / max rounds / clarification / fallback / **token 调用前裁剪**（大 tool result / 多客户 memory / 历史压缩）/ 写门禁错误 LLM 重试 | 16 |
| `ContextBuilder`（裁剪策略）| 各优先级裁剪规则 / token 上限达成 | 6 |
| `MemoryLoader` | 三层加载 / 引用实体 resolve / token 截断 | 8 |
| `MemoryWriter` | should_extract gate / 闲聊跳过 / 异步抽取 / 写库幂等 | 8 |
| `PromptBuilder` | schema 注入 / 业务词典 / few-shots / 历史限制 | 6 |
| 各 tool | 权限校验 / ERP 错误处理 / 输出格式 | 16 × 4 = 64 |
| `DingTalkSender.send_file` | 上传 + 发送 / 重试 / 5xx 4xx 区别处理 | 5 |
| 审批 inbox | 列表 / 批量通过（用 ERP batch-approve）/ 批量拒绝 / 部分失败响应 | 12 |
| 合同生成 | 模板渲染 / 占位符校验 / 文件发送 / send_file 集成 | 10 |

合计 **~140 单元测试**。

### 10.2 LLM Eval 框架（新增）

> 这是 Plan 6 跟之前所有 plan 的最大区别 — agent 是非确定性系统，**单元测试不够**。

`hub/agent/eval/` 新增：
- **gold_set.yaml**：30-50 条"用户问 X → 期望 agent 走 Y 路径 → 输出 Z"的标注样本
- **eval_runner.py**：跑一遍 gold set，统计每条的 expected vs actual（路径匹配 / 关键字段匹配 / 满意度）
- **集成 CI**：每次 prompt 改动 / 模型切换都跑一遍 eval；阈值（满意度 < 80%）阻止 merge

样例：
```yaml
- id: contract-by-history-price
  user_input: "给阿里写讯飞x5 50 台合同 按上次报价"
  expected_tools: [search_customers, get_customer_history, check_inventory, generate_contract_draft]
  expected_clarification: false  # 不应该反问
  evaluator: |
    生成的合同 docx 是否含 customer_name="阿里巴巴集团" 且 unit_price 来自历史价（约 ¥2,499）
```

### 10.3 端到端测试

- docker compose up + 真实钉钉测试组织 + ERP staging
- 跑 10 条预设用户故事（每个角色 2-3 条）
- 检查 conversation_log + tool_call_log + audit_log 完整性

---

## 11. 不在 Plan 6 范围（YAGNI）

- ❌ 多语言（仅中文）
- ❌ 跨账套查询（暂只支持单账套）
- ❌ 创建 ERP 主数据（客户 / 产品 / 供应商）
- ❌ 删除 ERP 数据
- ❌ 工资单 / 薪酬类查询
- ❌ Claude Memory Tool 协议（沿用 ChatGPT 模式 — Postgres 三层结构化）
- ❌ 向量库 / 嵌入检索
- ❌ Plan 5 后台对话监控页换 React 流式（仍用 SSE）
- ❌ Agent 自主写邮件 / 发短信（钉钉以外的 channel）

---

## 12. 风险与不确定性

### 12.1 已知风险

| # | 风险 | 缓解 |
|---|---|---|
| 1 | DeepSeek/Qwen tool calling 稳定性差，LLM 编 tool 名 / 参数 | tool schema 严格 + JSON Schema 校验 + 失败重试 1 次 + 兜底 RuleParser |
| 2 | LLM 幻觉客户/产品 ID 导致写错数据 | tool 参数必须从 search_* 结果引用；金额硬上限；写操作 confirm 流 |
| 3 | 月成本超预算 | 5 项硬阈值 + dashboard 监控 + 80% 预算告警 |
| 4 | 合同 docx 模板与实际业务不符 → 销售投诉 | 合同模板由销售/法务自己上传维护；admin 后台一键替换 |
| 5 | 审批 inbox 没人审 → 草稿堆积 | 草稿超 7 天未审批自动钉钉催促请求人确认是否仍要 |
| 6 | LLM eval 阈值过严 / 过松 | 第一版 gold set 30 条人工标注；阈值 80% 满意度（可调）|

### 12.2 未知不确定性（需实施验证）

- DeepSeek-V3 在 5 round 内能否稳定收敛复杂任务（合同生成需要 8-10 个 tool call）— 实施时实测
- 中文业务词典覆盖度 — 第一版 50 条术语，跑一两周看实际命中率扩
- Plan 6 在档 3 范围下的实际工作量是 6 周（按 5 round budget × 16 tool × 模板 / 审批 inbox 拆分）— 实施时按周 review

---

## 13. C 阶段已就绪的 Plan 6 工具集基础

C 阶段 90% 代码无改动 — Plan 6 在它上面加层。明确"哪些直接复用"：

| C 阶段成果 | Plan 6 用途 |
|---|---|
| `Erp4Adapter` 6 个方法 | 6 个 read tool 直接包装 |
| `IdentityService.resolve` | tool 调用前必跑 |
| `require_permissions` | tool 调用前必跑 |
| `task_logger.log_inbound_task` | 升级成 conversation_log + tool_call_log（结构调整，逻辑不变）|
| `LiveStreamPublisher` | 加 agent 决策链事件 schema |
| `ConversationStateRepository` | 升级成 ConversationContext |
| `BindingService / 各 UseCase` | 仍由 RuleParser 直接调（兜底用）|
| `ChainParser.rule` | 保留（处理 `/绑定` 等显式命令）|
| HUB 后台 9 个 admin 路由 | 加 3 个审批子路由 + 1 个合同模板，复用 SSE / 审计 / dashboard |
| `cron 调度器` | 加 1 个 job：超 7 天未审批草稿钉钉催促 |
| 6 步初始化向导 | 不变（钉钉 / AI 已配） |

---

## 14. ERP 改动清单（Plan 6 跨仓库依赖）

> Plan 6 实施前必须确认 ERP 端 endpoint 状态。已实测 ERP-4 现状：

### 14.1 ERP 已有可直接复用（不需要改动）

| HUB tool | ERP endpoint | 状态 |
|---|---|---|
| `search_orders` | `GET /api/v1/orders` | ✅ |
| `get_order_detail` | `GET /api/v1/orders/{id}` | ✅ |
| `get_customer_history` | `GET /api/v1/products/{id}/customer-prices` | ✅（C 阶段已用）|
| `get_customer_balance` | `GET /api/v1/finance/customer-statement/{customer_id}` | ✅ |
| `check_inventory` | `GET /api/v1/products/{id}`（含 stocks 字段）| ✅ |
| `create_stock_adjustment_request → 落 ERP` | `POST /api/v1/stock/adjust` | ✅ |
| 凭证批量审批落地 | `POST /api/v1/vouchers/batch-approve` | ✅（已有批量能力）|

### 14.2 ERP 需要新增/修改（Plan 6 阻塞依赖）

| HUB tool / 场景 | 需要的 ERP 改动 | 估时 |
|---|---|---|
| **凭证两阶段提交幂等键**（HUB Task 8 强阻塞）| `voucher` 表加 `client_request_id VARCHAR(64) NULL` + 部分唯一索引（NOT NULL 时唯一）；`POST /api/v1/vouchers` body 接收 `client_request_id` 可选字段；冲突时返已存在的 voucher（HTTP 200 + `idempotent_replay=True`） | 0.5 天 |
| `create_price_adjustment_request → 落 ERP` | `POST/PATCH /api/v1/customer-price-rules`（客户专属定价规则）| 1-2 天（ERP 侧）|
| `get_inventory_aging` | `GET /api/v1/inventory/aging`（按库龄聚合）| 0.5 天 |
| `analyze_top_customers` | 可用现有 `/api/v1/orders` + `/api/v1/customers` 在 HUB 端聚合，**不阻塞** | 0 |
| `analyze_slow_moving_products` | 同上，HUB 端聚合 | 0 |

> **凭证幂等键为 Plan 6 强阻塞**：没有这一步，HUB phase 1 调 ERP create_voucher 进程崩溃后下次 batch 重试会重复创建 ERP voucher（脏数据）。详见 §4.2 + plan Task 18 Step 3。

### 14.3 ERP 仓库改动估时

约 **2-3 天 ERP 工作量**（含 voucher 幂等键改动）。Plan 6 的 ERP 部分单独成 commit 提交（在 ERP-4 仓库）；HUB 这边可以先做不依赖这些改动的 tool（读类的 search_*、check_inventory 等都能先做），最后写类 tool（凭证草稿审批 / 调价 / 库龄）等 ERP merge 后再做。

---

## 15. Plan 6 不变更的 spec 决策

明确**保留** Plan 1-5 的设计决策：

- HUB 不直连 ERP 数据库（一切走 ERP HTTP API）
- ApiKey scope=`act_as_user` + X-Acting-As-User-Id（模型 Y）
- 钉钉端 UI 大白话原则（无 enum / code 暴露）
- 6 角色权限模型（在此基础上扩展，不重做）
- AES-GCM + HKDF 加密
- Redis Streams + 消费组 + 死信
- 多阶段 docker build

---

**Spec 结束（v4）**

> v4 改动（同步 plan 第三轮 review 6 条修复）：
> - §4.2 凭证审批场景改为两阶段提交 + 幂等键 + creating 5 min 租约恢复语义
> - §5.4 voucher_draft 加 creating_started_at 字段 + status 五值（含 creating）+ idx_creating_lease 索引
> - §14.2 把 ERP voucher 从"已有可直接复用"挪到"需要修改"，明确 client_request_id 字段为 Plan 6 强阻塞依赖
