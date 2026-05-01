# Plan 6 总结报告

**日期**：2026-05-01
**分支**：`feature/plan6-agent`（HUB 仓库）+ `main`（ERP-4 仓库已合并）
**状态**：**🚧 staging 验收进行中，禁止合并 main**

> ⛔ **release gate 尚未完成，feature/plan6-agent 当前不允许合并到 main**。
> 代码层修复持续进行中（截至 v8 staging review #15 共 ~16 commit）；
> 故事 1（合同 docx）已在 staging 验证可输出，但**故事 2-6（凭证/批量审批/调价/dashboard/决策链）和真 DeepSeek pytest -m eval 未跑**。
> 单测 / ruff / build / static review 是**合并前的最低门槛**（已达成），
> **不能替代** release gate 的实跑验收（覆盖 LLM tool-calling、文件发送、写草稿审批、ERP 幂等写入等运行时行为）。
>
> **合并 main 前必须完成的产物记录**（缺一不可）：
> 1. 6/6 用户故事在 staging 跑通 + 截图 / 录屏存档
> 2. `pytest -m eval` 30 条 gold set 满意度 ≥ 80% 的运行报告（含 token / cost 实测）
> 3. ERP 端跨仓库依赖（GET /customers/{id} + GET /account-sets）已部署
> 4. fresh DB seed → 故事 1 自动可复现（v8 review #14 加 _seed_default_contract_template 已支持）
>
> 任何一项缺失 → 退回 staging 继续跑，**不打 release tag、不部署生产**。

---

## 一、Plan 6 是什么

**目标**：把 HUB 钉钉机器人从"RuleParser + ERP ILIKE 单步查询"升级成"LLM-driven Agent + tool calling"。

**用户原话（C 阶段验收时提的方向）**：
> "后续我就是想把这个东西变成一个 agent。把这个机器人变成 agent 然后利用后台的这些数据库去帮助大家做工作。而不是只是查询。包括后面做合同，其实也是需要去分析理解的。"

**做完了什么**：
1. 钉钉机器人能**接受任意自然语言任务**（不再限"查 X"格式）
2. agent 用 **16 个 tool** 多 round 推理（搜客户/搜商品/查库存/查历史价/聚合分析/生成合同/生成报价/导出 Excel/创建凭证草稿/调价请求/库存调整等）
3. **三层 memory**（会话级 Redis 30 分钟 + 用户/客户/商品 Postgres 持久层）跨对话记关键事实
4. **写操作有审批链路**：销售生成合同直接发给本人；凭证/调价/库存调整生成草稿挂 admin 后台审批
5. **agent 决策链可观测**：admin 后台 task 详情看每次对话调了哪些 tool / 几个 round / 花了多少 token
6. **成本可控**：dashboard 看今日/本月 LLM 成本；超 80% 月预算自动钉钉告警 admin
7. **失败兜底**：LLM 挂了降级 RuleParser；prompt 超 budget / max_rounds 等都有友好兜底

---

## 二、19 个 Task 全景

### 第 1 层：基础设施（Task 1-4）

| Task | 内容 | 关键产出 |
|------|------|---------|
| **1** | 数据模型 + tool_logger | 9 张新表（conversation_log / tool_call_log / user_memory / customer_memory / product_memory / contract_template / contract_draft / voucher_draft / price_adjustment_request / stock_adjustment_request）+ migration v1_xxx_plan6_agent_tables |
| **2** | ToolRegistry + 写门禁 | 自动 schema 生成（从 Python 函数签名 + docstring）+ ConfirmGate（claim_action 原子领取 Redis Lua + restore_action）+ 实体写入路径 |
| **3** | ERP 读 tool（9 个） | search_products / search_customers / get_product_detail / get_customer_history / check_inventory / search_orders / get_customer_balance / get_inventory_aging（依赖 ERP /aging）/ get_order_detail |
| **4** | Memory 三层 | SessionMemory（Redis 30min）+ UserMemoryService / CustomerMemoryService / ProductMemoryService（Postgres）+ MemoryLoader（按对话引用实体加载）+ MemoryWriter（should_extract gate 防 chitchat 浪费）|

### 第 2 层：Agent 核心（Task 5-6）

| Task | 内容 | 关键产出 |
|------|------|---------|
| **5** | PromptBuilder | 业务词典 42 条（"压货"→"库龄高商品"等）+ 同义词 22 组 + Few-shots 8 例（含写操作 confirm 模式样板）+ 行为准则 5 条 |
| **6** | ChainAgent 主循环 | MAX_ROUNDS=5 / MAX_PROMPT_TOKEN=18K / LLM_TIMEOUT=30s 三大硬阈值 + AgentLLMClient 包装 deepseek（tools=API）+ ContextBuilder 调用前 token 裁剪（must_keep + can_truncate 优先级降序装填）+ RE_CONFIRM 链路（user_just_confirmed → confirm_all_pending → 把 N 个 (action_id, token) 对拼进 hint 让 LLM 重调） |

### 第 3 层：业务工具（Task 7-9）

| Task | 内容 | 关键产出 |
|------|------|---------|
| **7** | 生成型 tool | ContractRenderer（python-docx 渲染 + 段落+表格双级占位符替换）+ ExcelExporter（openpyxl）+ DocumentStorage（AES-GCM 二进制安全）+ DingTalkSender.send_file（两步上传：媒体 → batchSend sampleFile；含 5xx 重试 / 4xx 立即抛 / 20MB 上限）+ 3 个 GENERATE tool |
| **8** | 写草稿 + 审批 inbox | 3 个 WRITE_DRAFT tool（必须声明 confirmation_action_id；先查 → INSERT → IntegrityError catch 三段幂等保护）+ admin/approvals.py 9 个 endpoint（voucher 两阶段提交：pending→creating→created→approved；creating 5min 租约崩溃恢复；in_progress 暴露；早返回 audit log；client_request_id 幂等键 → ERP 端）|
| **9** | 聚合分析 tool | analyze_top_customers（bounded pagination MAX_ORDERS=1000 / MAX_PERIOD_DAYS=90 / partial_result 标记）+ analyze_slow_moving_products（依赖 Task 18 ERP /aging endpoint）|

### 第 4 层：集成（Task 10）

| Task | 内容 | 关键产出 |
|------|------|---------|
| **10** | inbound handler 升级 | dingtalk_inbound.py 主路径替换 chain_parser → chain_agent；保留 rule 命令 / identity / pending_choice / pending_confirm；新增 RE_CONFIRM 识别（"是/确认/yes/y/ok/确定" 6 词）；BizError 显式上抛中文翻译；其他异常 → RuleParser fallback（rule 命中真执行 use case）；worker.py 注入 9 个 ChainAgent 依赖 |

### 第 5 层：admin UI（Task 11-13）

| Task | 内容 | 关键产出 |
|------|------|---------|
| **11** | 合同模板管理 | 6 个 endpoint（POST 上传 docx + 自动占位符识别 / GET 列表 / GET 占位符 / PUT 元信息 / POST disable / POST enable）+ ContractTemplatesView.vue（AppCard + AppTable + AppModal）+ AdminLayout 菜单 + 路由 |
| **12** | 审批 inbox UI | ApprovalsView.vue 三 tab（凭证/调价/库存调整）+ AppPagination 翻页 + AppCheckbox 全选/单选 + 批量通过/拒绝 modal + 结果详情展示（approved/in_progress/creation_failed/approve_failed 各类）+ AdminLayout perm 改 OR 语义 |
| **13** | task detail 决策链 | GET /admin/tasks/{id} 加 conversation_log + tool_calls 字段（channel_userid + ±30s 时间窗口模糊匹配）+ AgentDecisionChain.vue 子组件（4 个汇总卡片 + 工具调用时间线折叠展开）+ 时间字段 / 状态映射 / cost 4 位小数 |

### 第 6 层：可观测 / cron / eval（Task 14-16）

| Task | 内容 | 关键产出 |
|------|------|---------|
| **14** | dashboard 成本指标 + 预算告警 | dashboard 加 llm_cost 子对象（today_calls / today_tokens / today_cost / month_to_date_cost / month_budget / used_pct / alert）+ DashboardView 加 4 卡片 + 进度条（alert 时变色）+ budget_alert.py cron（09:00 跑；< 80% / cooldown / 无 admin / 正常发送 4 分支；24h cooldown 防风暴）+ system_config 白名单加 month_llm_budget_yuan |
| **15** | cron 草稿催促 | draft_reminder.py（超 7 天未审批 voucher/price/stock 草稿 → 按 requester_hub_user_id 聚合 → 钉钉提醒请求人；同 user 多张合并 1 条；无 dingtalk 绑定 / 发送失败计 skipped）+ scheduler 加修同小时多 job 都触发的隐藏 bug |
| **16** | LLM Eval 框架 | gold_set.yaml 30 条标注（query 12 / multi_step 6 / write 5 / business_dict 4 / error 3）+ EvalRunner（mock LLM 回放 + stub agent 走真 ChainAgent loop）+ CI 阈值 80% 框架（pytest.mark.eval 隔离真 LLM 测试给运维侧）|

### 第 7 层：配置 + 跨仓库 + 收尾（Task 17-19）

| Task | 内容 | 关键产出 |
|------|------|---------|
| **17** | seed 升级 | 加 13 新权限码（usecase.query_customer / .query_inventory / .query_orders / .query_customer_balance / .query_inventory_aging / .analyze / .generate_quote / .export / .adjust_price.use|.approve / .adjust_stock.use|.approve / .create_voucher.approve）+ 2 lead 角色（bot_user_sales_lead / bot_user_finance_lead）+ 既有 3 角色升级 + business_dict seed（与 prompt/business_dict.py 共享真相源）|
| **18** | ERP 跨仓库改动 | ERP-4 仓库：customer_price_rule 表 + 5 个 endpoint + inventory/aging endpoint + voucher.client_request_id 幂等键 + migration v059_plan6_erp + 顺手修 ERP main 分支 PEP 604 兼容 bug |
| **19** | 端到端验证记录 | 全量 pytest（v8 staging review 多轮加测后 **650 case 全过** ~4:30）+ 验证记录文档（性能 / 成本预测 / follow-up 列表）|

---

## 三、代码改动统计

### HUB 仓库（feature/plan6-agent）

```
94 files changed, 17834 insertions(+), 220 deletions(-)
37 commits（feat + polish 交替）
```

**关键新文件**：
- `backend/hub/agent/`（核心 agent 模块）
  - `chain_agent.py` 主循环
  - `context_builder.py` token 裁剪
  - `llm_client.py` AgentLLMClient（OpenAI tools= 协议）
  - `types.py` AgentResult / AgentLLMResponse / ToolCall / 错误类
  - `tools/registry.py` ToolRegistry + ConfirmGate
  - `tools/types.py` ToolType / 错误类
  - `tools/confirm_gate.py` 确认状态管理（Redis Lua 原子领取）
  - `tools/entity_extractor.py` 从 tool 结果提取实体写回 session
  - `tools/erp_tools.py` 9 个读 tool
  - `tools/generate_tools.py` 3 个生成 tool
  - `tools/draft_tools.py` 3 个写草稿 tool
  - `tools/analyze_tools.py` 2 个聚合 tool
  - `memory/types.py` Memory dataclass + ConversationHistory
  - `memory/session.py` SessionMemory（Redis）
  - `memory/persistent.py` UserMemoryService / CustomerMemoryService / ProductMemoryService
  - `memory/loader.py` MemoryLoader
  - `memory/writer.py` MemoryWriter（should_extract gate）
  - `prompt/builder.py` PromptBuilder
  - `prompt/business_dict.py` 业务词典
  - `prompt/synonyms.py` 同义词
  - `prompt/few_shots.py` Few-shots
  - `document/contract.py` ContractRenderer
  - `document/excel.py` ExcelExporter
  - `document/storage.py` DocumentStorage（AES-GCM）
  - `eval/runner.py` EvalRunner + EvalReport
  - `eval/gold_set.yaml` 30 条标注

- `backend/hub/cron/`
  - `budget_alert.py` 月预算告警
  - `draft_reminder.py` 草稿催促

- `backend/hub/routers/admin/`
  - `contract_templates.py` 模板 CRUD
  - `approvals.py` 审批 inbox 9 endpoint

- `frontend/src/components/admin/`
  - `AgentDecisionChain.vue`（Task 13 抽出，Task 14 dashboard 也可复用）

- `frontend/src/views/admin/`
  - `ContractTemplatesView.vue`
  - `ApprovalsView.vue`

- `backend/hub/observability/tool_logger.py` ToolCallLog 写入

**修改文件**：
- `backend/hub/handlers/dingtalk_inbound.py`：业务主路径换 chain_agent + RE_CONFIRM
- `backend/worker.py`：构造 9 个 ChainAgent 依赖
- `backend/main.py`：注册 router + cron + tool wiring
- `backend/hub/seed.py`：13 新权限 + 2 角色 + business_dict
- `backend/hub/adapters/channel/dingtalk_sender.py`：加 send_file 两步协议
- `backend/hub/adapters/downstream/erp4.py`：加读 endpoint + create_voucher 含 client_request_id
- `frontend/src/views/admin/AdminLayout.vue`：菜单
- `frontend/src/router/index.js`：路由
- `frontend/src/stores/auth.js`：hasPerm OR 语义
- `frontend/src/utils/format.js`：状态映射

### ERP-4 仓库（main，已合并）

```
13 files changed, ~600 insertions
1 commit（c5dce51）
```

**新文件**：
- `backend/app/models/customer_price_rule.py`
- `backend/app/routers/customer_price_rules.py`
- `backend/app/migrations/v059_plan6_erp.py`
- `backend/tests/test_customer_price_rules.py`

**修改**：
- `backend/app/models/voucher.py` + `backend/app/schemas/accounting.py`：加 client_request_id
- `backend/app/routers/vouchers.py`：create_voucher 加幂等回放
- `backend/app/routers/stock.py`：加 /api/v1/inventory/aging
- `backend/app/integration/feature_flags.py`：加 ENABLE_CUSTOMER_PRICE_RULES
- `backend/app/main.py`：注册 customer_price_rules router
- `backend/app/config.py`：加 `from __future__ import annotations`（pre-existing fix）
- `backend/tests/conftest.py`：加 customer_price_rule + voucher 索引到 fixture

---

## 四、测试覆盖

### HUB 仓库
- **650 case 全过**（pytest -q ~4:30，v8 staging review 多轮加固后口径）
- Plan 1-5 baseline 315 → +332 case
- 关键 case：
  - **写门禁链路**：MissingConfirmation vs ClaimFailed 错误分流 / 并发 claim 只跑 1 次 tool.fn / token 一次性消费 / args 篡改防御 / 跨 action 复用拦截 / 多 pending action_id 隔离
  - **两阶段提交 + 崩溃恢复**：phase1 失败回滚 / phase2 失败保持 created / creating 租约 5min / 全 in_progress 早返回 audit / 并发抢锁 in_progress 反馈
  - **三段幂等**：先查 → INSERT → IntegrityError catch + 回查 / asyncio.gather 真并发 / 极端情况 reraise
  - **bounded pagination**：MAX_ORDERS=1000 截断 / period 90 天截断 / partial_result 双 partial 拼接 / clamp 真验证
  - **scheduler 多 job**：同小时两 job 都触发 / cooldown / fail-soft

### ERP-4 仓库（Task 18 隔离）
- **11 case 全过**（隔离运行）
  - create / 幂等回放 / asyncio.gather 并发幂等 / upsert / PATCH / list filter / get / delete
  - inventory aging basic / aging with data
  - voucher 幂等回放

### Frontend
- **npm run build 全过**（每个 Task 都验证）

---

## 五、Code Review 累计

**11 轮 plan review**（编码前完成 v1→v11）：
- v1：原始；v2-v11：每轮发现 confirmation 链路 / 写操作幂等 / 错误分流 / 持久终态等 P1 问题
- 关键演进：
  - v1 → v2：confirmation_token 加 hub_user_id 隔离（防群聊 B 确认 A 的写）
  - v2 → v3：confirmed 改 hash {action_id}（支持单 round 多 pending）
  - v3 → v4：creating 5min 租约 + ERP voucher.client_request_id 幂等
  - v4 → v5：token 一次性消费（防重放）
  - v5 → v6：claim 改 Redis Lua 原子（防并发 → 真 atomicity）
  - v6 → v7：权限/schema 校验前置 + claim Lua 跨两 hash 持久终态 + spec 同步
  - v7 → v8：args 不污染 + Missing/ClaimFailed 错误分流 + 端到端样例同步
  - v8 → v9：写 tool 真幂等（action_id 注入 + DB 唯一索引 + IntegrityError catch）
  - v9 → v10：register-time fail fast + price/stock tool 完整展开
  - v10 → v11：action_id 32-hex 防长期碰撞 + 第二处 docstring 修正

**18 轮 code review**（每个 task 独立）：
- 累计修复 ~12 Critical + ~50 Important + ~120 Minor
- 关键发现：
  - audit_log target_id 64 字符上限 batch 必崩（C1）
  - main.py lifespan wiring 缺 draft_tools.set_erp_adapter（C2）
  - ChannelUserBinding 缺 channel_type="dingtalk" 过滤（C2）
  - admin GET endpoint 缺 perm 检查（C1）
  - 4 写 endpoint 缺 audit log（C2）
  - scheduler 同小时多 job 只跑第一个（隐藏 C1）
  - gold_set 6 round case 在真 ChainAgent 必挂（C1）
  - 80% 阈值测试是同义反复（C2）
  - business_dict 两套真相源（I1）

---

## 六、Plan 6 给生产带来什么

### 用户层
1. **钉钉机器人能干更多事**：
   - 之前：只能查（`查 SKU100`）
   - 之后：能搜+查+生成+提请审批（`给阿里写讯飞x5 50台合同 按上次价` → 自动备齐客户/商品/历史价/库存 → 生成 docx 发回手机）

2. **复杂请求多 round 推理**：
   - 用户："上月哪个客户买得最多"
   - agent 第 1 round 调 `analyze_top_customers(period=last_month, top_n=10)` → 拿数据
   - agent 第 2 round 综合返回"上月 top：阿里 ¥350K / 京东 ¥220K..."

3. **写操作有人工确认**：
   - 用户："把这周差旅做凭证"
   - agent 第 1 round 搜订单 → 12 条
   - agent 第 2 round 准备调 create_voucher_draft × 12 → 写门禁拦截 → 加 pending
   - agent 输出预览："我准备创建 12 张凭证..."
   - 用户回 "是"
   - agent 自动重调 12 个 tool 带 (action_id, token) → ConfirmGate 原子 claim → tool 真执行 → 生成 12 张草稿
   - 会计 admin 后台批量审 → 通过 → ERP 真落库

### 运维层
1. **可观测性**：admin 后台 task 详情看每次对话的 LLM round 数 / tool 调用链 / token 消耗 / 错误原因
2. **成本可控**：dashboard 看月成本 + 80% 自动钉钉告警
3. **失败兜底**：LLM 挂了降级 RuleParser；prompt 超 budget 友好提示；max_rounds 超限 raise 让 inbound handler 翻译
4. **审批管理**：admin 三 tab inbox 处理凭证/调价/库存调整草稿；批量通过/拒绝；崩溃恢复（5min 租约）
5. **草稿催促 cron**：超 7 天未审批草稿每天 09:00 钉钉提醒请求人

### 财务安全层
1. **凭证不重复创建**：HUB phase1 + ERP voucher.client_request_id 双层幂等
2. **写操作可审计**：每个 admin 写操作（upload/update/disable/approve/reject）都写 audit_log
3. **金额硬上限**：单凭证 ¥1M（system_config 可调）超限拦截

---

## 七、技术决策亮点

### 1. 写门禁的演进（v1 → v11）
最初是简单 token check；最终落地为：
- **Redis Lua 原子领取**：防 asyncio.gather 同 token N 个并发都跑 tool
- **跨 confirmed_hash + pending_hash 一次 GETDEL**：避免"删了 confirmed 但 pending 残留"的中间态
- **三重一致性校验**：token + tool_name + normalized_args 任一不一致 → 原子 restore + 拒绝
- **错误分流**：MissingConfirmation（add_pending）vs ClaimFailed（不 add_pending）防"幽灵 pending"
- **真幂等**：confirmation_action_id 注入 tool fn → DB 部分唯一索引 → "先查 → INSERT → IntegrityError catch" 三段防御
- **register fail-fast**：写类 tool 必须声明 confirmation_action_id 参数（启动期就拒绝），不延迟到运行期才发现

### 2. 上下文裁剪（ContextBuilder）
- must_keep（system_prompt + user_msg + recent_round + confirm_hint）超 budget 直接 raise（不静默丢失关键内容）
- can_truncate 按优先级降序装填（5: 旧 tool result 摘要 / 2: 旧对话压缩）
- > 500 token 的 tool result 自动摘要（保 type+count+前几个 keys）
- tools_schema 也计入 budget（OpenAI 把 tools= 算 input token）

### 3. 两阶段提交 + 5min 租约
- phase 1: pending → creating（拿乐观锁记 creating_started_at=now）→ ERP create_voucher（含 client_request_id）→ created 或 失败回滚 pending
- phase 2: created → ERP batch_approve_vouchers → approved
- 崩溃恢复：creating + lease 过期（>5min）→ 视为残留 → 重新拿锁 → 同 client_request_id 让 ERP 端唯一索引兜底（不会重复创建）
- in_progress 暴露：creating + lease 未过期 → response.in_progress 数组（UI 显示"处理中"）

### 4. UI 大白话原则
- 全程禁暴露 perm code / role code / status enum / API 路径
- voucher final_status：success/failed_user/failed_system/fallback_to_rule → 完成/用户问题导致失败/系统出错/已切换简单规则解析
- voucher in_progress reason："另一审批员正在处理（5 分钟内自动释放）"
- 所有按钮 / 列名 / 错误提示中文

### 5. 业务词典单一真相源
- seed.py 和 prompt/business_dict.py **同一对象引用**（`from ... import DEFAULT_DICT as DEFAULT_BUSINESS_DICT_SEED`）
- admin 编辑写 SystemConfig；**PromptBuilder.from_db() 启动时合并 DEFAULT + admin 覆盖**（worker 启动注入）
- admin 删 key 不会让 LLM 失忆：`merged = dict(DEFAULT_DICT); merged.update(admin_dict)`，DEFAULT 始终兜底
- value 不是 dict（异常上游数据）→ 回落 DEFAULT，记 warning
- 热重载：当前需重启 worker 生效；后续可加 reload endpoint 或定时 reload

---

## 八、release gate / follow-up

### release gate（**生产部署前必须完成**，不是事后补丁）

> 这两项是合并 main + 部署生产的**门禁条件**，不是上线后慢慢做的优化。
> 任何一项不过都视为 Plan 6 未完成验收，主分支不许打 release tag。

1. **真 docker e2e（6 个用户故事跑通）**：
   - **跑哪 6 个**：销售生成合同 → 收 docx；会计生成凭证 → 后台收草稿；批量通过 → ERP 真落库；调价审批；dashboard 看成本；task detail 看决策链（plan §3556）
   - **执行人**：运维 + 产品 owner 双签
   - **环境**：staging（真 ERP-4 / 真钉钉应用 / 真 deepseek-V3 key）
   - **门槛**：6/6 全过；任何一个故事失败都阻塞 release
   - **产出物**：录屏 + 钉钉收文件截图 + ERP 后台凭证截图 + audit_log 抓取
   - **回滚预案**：一旦发现写操作 happy path 异常立即停发并保留全部日志

2. **真 LLM eval（pytest -m eval）**：
   - **执行命令**：`pytest -m eval` 跑 30 条 gold set
   - **后端**：真 DeepSeek-V3 API（不是 mock）
   - **门槛**：满意度 ≥ 80%（plan 当初定的 CI 阈值）
   - **附带产出**：实测 token / round / cost → 验证 plan §10 成本预测（月 3K-8K¥ 估算）
   - **失败处理**：< 80% 不许合并；找出哪些 case 类型崩了（query? multi_step? write? business_dict?）→ 改 prompt / few-shots / business_dict 后重跑
   - **预算护栏**：30 case × 5 round × 18K token ≈ 0.27M token；按 deepseek-V3 ¥0.01/K input 计单次 eval ≤ ¥3，可承担

### 必做 follow-up（与上线不冲突，可上线后短期内补）

3. **ConversationLog.task_id 直接关联**（Task 13 follow-up）：
   - 当前 channel_userid + ±30s 时间窗口模糊匹配
   - 多轮对话只能匹配首轮 task；后续轮次因 started_at 锚定首轮 → 落出 30s 窗口
   - 应加 task_id 字段或 turn_idx

4. **ERP-4 测试基础设施 fixture pollution**（pre-existing，不阻塞 Task 18）：
   - 全量 pytest 跑 158 fail；隔离/小批量跑全过
   - 与 Task 18 改动无关（stash 后该 fail 仍存在）
   - 影响 ERP-4 自身的 CI 流；HUB 和 ERP-4 分别 CI 不冲突

5. **scheduler 加分钟级精度**（Task 15 follow-up）：
   - 当前 at_hour 整点；budget_alert + draft_reminder 都 09:00
   - 已修同小时多 job 都触发的 bug；但分钟级触发还需扩 scheduler 接口

6. **business_dict 热重载**（v8 review P2-#3 follow-up）：
   - 当前 PromptBuilder.from_db() 在 worker 启动时读一次 SystemConfig
   - admin 改完业务词典需重启 worker 才生效
   - 应加 reload endpoint 或定时 reload（30min 周期）

### 优化（中期）
7. **DashboardView LLM 成本 section 拆子组件**（Task 14 M5）：当前 390 行
8. **ContractTemplatesView 拆 3 模态对话框**（Task 11 M3）：当前 492 行
9. **ApprovalsView 拆 VoucherTab/PriceTab/StockTab**（Task 12 M8）：当前 998 行
10. **AppBadge 全站 slot 修复**：Plan 5 引入的 pre-existing bug，影响所有 admin badge 文案；Task 13 review 已用 spawn_task 标记
11. **AppBadge slot 修复后清理 manual class workaround**

### 可选（长期）
12. **EvalRunner 加 max_concurrency**：Task 19 真 LLM eval 30 case 串行 5min；并发 5 路降到 1min
13. **业务词典 admin UI**（独立页面）：当前可在 system_config 通用 KV 编辑器改，但不直观
14. **真 docker e2e 自动化脚本**：写脚本起容器 + 跑用户故事 + 抓日志（release gate 当前是手动跑）

---

## 九、Plan 6 当前进展（**代码完成，待 staging 验收**）

### 已完成的合并前最低门槛

| 维度 | 状态 |
|------|------|
| Spec §1-15 全覆盖 | ✅ |
| 19 个 task 全实现 | ✅ |
| HUB 650 单测全过（~270s，v8 staging review 加固）| ✅ |
| ERP 11 单测全过（隔离）| ✅ |
| Frontend build 全过 | ✅ |
| **ruff check . 全过（0 error）** | ✅ |
| 18 轮 code review 全修 | ✅ |
| 11 轮 plan review v1→v11 | ✅（编码前）|
| 文档化（验证记录 + 总结报告）| ✅ |
| business_dict admin 配置生效（PromptBuilder.from_db）| ✅ |

### release gate（合并 main + 部署生产前必过 — 见 §八）

| 维度 | 状态 |
|------|------|
| 真 docker e2e（6 个用户故事在 staging 跑通）| ⏳ 待运维 + 产品 owner 双签 |
| 真 LLM eval（pytest -m eval 30 case ≥ 80% 满意度）| ⏳ 待跑 |

### 流转

```
当前 → 用户 review feature/plan6-agent
   → staging 部署
   → 跑 release gate 两项（真 e2e + 真 LLM eval）
   → 全过则批准合并 main + 生产 deploy
```

**任何 release gate 任意一项不过 → 阻塞合并主分支**。

---

## 十、commit chain

HUB 仓库 `feature/plan6-agent` → 38 个 commit（feat + polish 交替 + v8 review 修复 1 个）：

```
81c16e8 polish(hub): Plan 6 v8 review 修复（ruff 134→0 + PromptBuilder.from_db + uv.lock 加 .gitignore）
f653f2b docs(hub): Plan 6 端到端验证记录补 docker 健康检查（/loop 验证）
6c5b8b2 docs(hub): Plan 6 总结报告（用户 review 用）
d437cf3 docs(hub): Plan 6 端到端验证记录（Task 19）
d14fb72 polish(hub): Plan 6 Task 17 review 修复
9c944f0 feat(hub): Plan 6 Task 17（seed 升级）
19e6f9a polish(hub): Plan 6 Task 16 review 修复
c42028a feat(hub): Plan 6 Task 16（LLM Eval 框架）
a282614 polish(hub): Plan 6 Task 15 review 修复
ba2ab35 feat(hub): Plan 6 Task 15（cron 草稿催促）
b8dda33 polish(hub): Plan 6 Task 14 review 修复
caf4f13 feat(hub): Plan 6 Task 14（dashboard + 预算告警）
343be1f polish(hub): Plan 6 Task 13 review 修复
4d12995 feat(hub): Plan 6 Task 13（task detail 决策链）
900b07f polish(hub): Plan 6 Task 12 review 修复
e13cd14 feat(hub): Plan 6 Task 12（审批 inbox UI）
2f50b45 polish(hub): Plan 6 Task 11 review 修复
37a94f0 feat(hub): Plan 6 Task 11（合同模板）
e4b4fb8 polish(hub): Plan 6 Task 10 review 修复
045a336 feat(hub): Plan 6 Task 10（inbound 升级）
1a1e2b9 polish(hub): Plan 6 Task 9 review 修复
bef8b33 feat(hub): Plan 6 Task 9（聚合分析）
14d58e3 polish(hub): Plan 6 Task 8 review 修复
6e026c6 feat(hub): Plan 6 Task 8（写草稿 + 审批）
fc3fc65 polish(hub): Plan 6 Task 7 review 修复
5bf0500 feat(hub): Plan 6 Task 7（生成型 tool）
b6720bf polish(hub): Plan 6 Task 6 review 修复
89e2b5e feat(hub): Plan 6 Task 6（ChainAgent）
ac29941 polish(hub): Plan 6 Task 5 review 修复
7c4161f feat(hub): Plan 6 Task 5（PromptBuilder）
3479135 polish(hub): Plan 6 Task 4 review 修复
a4b493d feat(hub): Plan 6 Task 4（Memory 三层）
95a19cc polish(hub): Plan 6 Task 3 review 修复
5b08c99 feat(hub): Plan 6 Task 3（ERP 读 tool 9 个）
d646e53 polish(hub): Plan 6 Task 2 review 修复
41f64ba feat(hub): Plan 6 Task 2（ToolRegistry）
786f457 polish(hub): Plan 6 Task 1 review 修复
cb2144a fix(hub): Plan 6 Task 1 review
dbebdb5 test(hub): Plan 6 Task 1 case 4 修正
17b0b5e feat(hub): Plan 6 Task 1（9 张新表）
```

ERP-4 仓库 `main` → 1 个 commit：
```
c5dce51 feat(plan6): HUB Plan 6 跨仓库依赖（customer-price-rules + inventory/aging + voucher.client_request_id 幂等键）
```
