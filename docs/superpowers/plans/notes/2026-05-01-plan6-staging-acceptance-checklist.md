# Plan 6 Staging 验收清单

> **目的**：合并 `feature/plan6-agent` 到 `main` 并部署生产**前**，必须在 staging 跑通的两类 release gate。
> **环境**：本机 docker compose（project=`hub`），代码已切到 `feature/plan6-agent` 最新（含 v8 review 修复 commit `81c16e8` + `63f07a1`）。
> **验收人**：你（产品 owner）+ 运维双签。
> **判定**：6 故事 + 1 LLM eval **全过** → 批准合并；任意一项不过 → 退回开发。

---

## 0. 已就绪状态（我帮你启完了 — 确认即可）

```
gateway:    http://localhost:8091
admin UI:   http://localhost:8091/admin
health:     http://localhost:8091/hub/v1/health
```

| 项 | 状态 |
|---|---|
| `docker compose -p hub up -d` | ✅ Up |
| `hub-postgres-1` | ✅ healthy（数据保留，29h uptime）|
| `hub-redis-1` | ✅ healthy |
| `hub-gateway-1` | ✅ Up（feature/plan6-agent 最新代码） |
| `hub-worker-1` | ✅ Up + 注册 dingtalk_inbound/outbound |
| `hub-migrate-1` | ✅ Exited 0（plan6 9 张表迁移成功） |
| Health endpoint | ✅ `{"status": "healthy", postgres/redis/dingtalk_stream/erp_default 都 ok}` |
| `PromptBuilder.from_db` 方法 | ✅ 在 worker 容器内可 import |
| `AgentMaxRoundsError` 异常类 | ✅ 在 worker 容器内可 import（v8 N818 修复生效）|
| plan6 routers（approvals/contract_templates/dashboard）| ✅ 在 gateway 已加载 |
| plan6 表（10 张）| ✅ 全部存在（contract_template/contract_draft/conversation_log/customer_memory/price_adjustment_request/product_memory/stock_adjustment_request/tool_call_log/user_memory/voucher_draft）|

**手动确认（30 秒）**：
```bash
curl -s http://localhost:8091/hub/v1/health
# 期望：{"status":"healthy","components":{"postgres":"ok","redis":"ok","dingtalk_stream":"connected","erp_default":"ok"},...}
```

如返回不是 `healthy`，先执行 `docker compose -p hub logs --tail 50 hub-gateway hub-worker` 排查后再开始下面的验收。

---

## 1. release gate A：6 个用户故事真 docker e2e

> 每个故事独立完成 → 在框框里勾 ✅；任何一个 ❌ 都阻塞合并。

### 故事 1：销售生成合同 → 钉钉收 docx 📄

**前置**：
- 你账号在 admin 后台已绑定钉钉 userid（`/admin/users` 看绑定列表）
- 你的角色至少包含 `bot_user_sales` 或 `bot_user_sales_lead`（角色含 `usecase.generate_contract.use` 权限）
- ERP 里至少一个客户（如「阿里」）+ 一个商品（如「讯飞 X5」）+ 一个合同模板已上传到 `/admin/contract-templates`

**步骤**：
1. 钉钉打开 hub 机器人会话
2. 发：`给阿里写讯飞x5 50台合同 按上次价`
3. agent 应回："我准备给阿里巴巴生成讯飞 X5 × 50 台的合同（单价 ¥xxx，来自上次价）。确认请回 '是'"
4. 回 `是`
5. 等 ≤ 30s

**通过标准**：
- [ ] 钉钉收到 `.docx` 附件
- [ ] 文件名形如 `阿里巴巴-讯飞X5-合同-20260501.docx`
- [ ] 打开文档客户名/商品名/数量/单价/总金额都正确
- [ ] `/admin/tasks` 列表里能找到该次对话，detail 页能看到 tool 调用链（最少 2-3 个 tool：search_customers / search_products / get_customer_history / generate_contract_doc）

**失败排查**：
- 没收到文件 → `docker logs hub-hub-worker-1 | grep -E "generate_contract|send_file|ERROR"`
- 文件内容错（金额错） → 检查 `get_customer_history` 是否真返回了上次价
- agent 没问"确认" 直接生成 → 写门禁 bug，必报

---

### 故事 2：会计生成凭证草稿 📊

**前置**：
- 你账号有 `bot_user_finance` 角色（含 `usecase.create_voucher.use`）
- ERP 至少 2-3 条本周已开票订单可用作凭证源

**步骤**：
1. 钉钉发：`把这周差旅做凭证`（或 `把本月销售做凭证`）
2. agent 第 1 round 应调 `search_orders` 拉本周订单
3. agent 应回："我准备创建 N 张凭证草稿（订单 #xxx, #yyy, #zzz...）。确认请回 '是'"
4. 回 `是`
5. agent 自动重调 N 次 `create_voucher_draft`

**通过标准**：
- [ ] agent 输出预览里 N 与 search_orders 命中数一致
- [ ] 钉钉收到 "已创建 N 张凭证草稿，等待 admin 审批"
- [ ] `/admin/approvals` → 「凭证」tab 看到 N 行 `pending` 状态草稿
- [ ] 每行带请求人（你）+ 订单号 + 金额 + 创建时间
- [ ] **重发同样的话确认幂等**：再次发 `把这周差旅做凭证`，回 `是`，应当返回 "已存在草稿，未重复创建" 或 N 不变（不会创建 2N 张）

---

### 故事 3：批量通过 → ERP 真落库 🏦

**前置**：
- 故事 2 已生成至少 3 张 pending 凭证草稿
- 你账号有 `usecase.create_voucher.approve` 权限（`bot_user_finance_lead` 或 `platform_admin`）

**步骤**：
1. 浏览器打开 `http://localhost:8091/admin/approvals`
2. 「凭证」tab → 全选（顶部 checkbox）
3. 点「批量通过」按钮
4. 确认 modal → 确认

**通过标准**：
- [ ] modal 显示成功 N，失败 0
- [ ] 列表刷新后这 N 行状态变成 `approved` 或 `created`（看 ERP 创建是否成功）
- [ ] **去 ERP-4 后台 `/finance/vouchers`（http://localhost:5173 ERP UI）→ 应能搜到这 N 张凭证号**
- [ ] 点 admin task 详情 → 能看到 audit_log 写了 `voucher.batch_approve`
- [ ] **崩溃恢复测试**（可选）：批量通过过程中如果某张失败 → 状态应是 `creation_failed` + 错误原因（不是傻傻地黑盒"卡住"）

---

### 故事 4：调价审批链路 💰

**前置**：
- 你账号有 `usecase.adjust_price.use` 权限
- 销售主管账号有 `usecase.adjust_price.approve` 权限

**步骤**：
1. 钉钉发：`把阿里的讯飞x5单价改成 ¥1200`
2. agent 应预览："我准备给阿里巴巴 - 讯飞 X5 创建调价请求（旧价 ¥xxx → 新价 ¥1200）。确认请回 '是'"
3. 回 `是`
4. agent 创建 `price_adjustment_request` 草稿

**通过标准**：
- [ ] 钉钉回 "已提交调价请求，等待主管审批"
- [ ] `/admin/approvals` → 「调价」tab 看到该行 `pending`
- [ ] 切换到主管账号（或用 admin）→ 通过 → 状态 `approved`
- [ ] **ERP-4 端**：`customer_price_rule` 表新增一条该客户-商品的覆盖记录
- [ ] 销售再发 `查阿里讯飞x5上次价` → 返回新价 ¥1200（说明 ERP 客价规则已生效）

---

### 故事 5：dashboard 看 LLM 成本 📈

**步骤**：
1. 浏览器打开 `http://localhost:8091/admin/dashboard`
2. 滚到 "LLM 成本" section（页面下方）

**通过标准**：
- [ ] 4 张卡片：今日调用次数 / 今日 token 数 / 今日成本 (¥) / 月累计成本 (¥)
- [ ] 进度条：当月预算（默认 100¥）+ 已用百分比 + 剩余
- [ ] 跑完前 4 个故事后回看，调用次数应 ≥ 4，今日成本 > 0
- [ ] **故意触发预算告警**（可选）：在 admin/system_config 把 `month_llm_budget_yuan` 改成 0.01 → 09:00 等 cron 跑（或手动调 `budget_alert.py`） → 钉钉应收到管理员告警

---

### 故事 6：task detail 看决策链 🔍

**步骤**：
1. 浏览器打开 `http://localhost:8091/admin/tasks`
2. 找到故事 1（销售生成合同）那次对话的 task → 点进 detail

**通过标准**：
- [ ] 顶部 4 个汇总卡片：round 数 / tool 调用数 / 总 token / 总成本
- [ ] 中部"Agent 决策链" section
- [ ] 每个 tool 调用一行：name / args（JSON 折叠）/ result（JSON 折叠）/ duration / round / 错误（如有）
- [ ] 时间线按时间正序
- [ ] 故意调一个会失败的（如 `查不存在的客户`）→ task detail 应能看到 tool error + agent 怎么自我纠错（最少应该再调一次别的 tool 而不是直接成功）

---

## 2. release gate B：真 LLM eval（30 case ≥ 80% 阈值）

> 这一步**用真 DeepSeek-V3 API**跑 30 条 gold set，验证 agent 在标准用例下的满意度。
> **运行环境**：建议在 worker 容器内跑（已有 LLM key + DB 连接）；或 host 机 backend venv 跑。

### 步骤

```bash
# 进入 worker 容器
docker exec -it hub-hub-worker-1 bash

# 跑 eval gold set（30 case，串行约 5 分钟）
pytest tests/test_eval_gold_set.py -m eval -v
```

或者本机 venv：

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend
.venv/bin/pytest tests/test_eval_gold_set.py -m eval -v
```

> **注意**：默认 `pytest -m eval` 跳过；需设环境变量 `HUB_REAL_LLM_EVAL=1`（或看 conftest 控制）。

### 通过标准

- [ ] 30 case 全跑完不卡死 / 不超 budget / 不超时
- [ ] **总满意度 ≥ 80%**（24/30 case 通过即达标）
- [ ] 实测 token / round / cost 输出到 stdout（用来反推月成本）

### 关注点（即使过了也看一眼）

- [ ] 5 个写 case（contract / voucher / price / stock）有没有 confirm 路径正确触发（"是"才执行）
- [ ] 4 个 business_dict case（"压货"/"周转"/"回款"等）能否被正确理解
- [ ] 6 个 multi_step case（先搜后查再综合）的 round 数应在 2-4 之间
- [ ] 任何 case 接近 18K token 上限时被裁剪逻辑触发（查 stdout 是否有 "context_truncated" 标记）

### 失败处理

- 总满意度 < 80% → **不许合并**。把 fail 的 case 类型分类（query / multi_step / write / business_dict / error）→ 改 prompt / few-shots / business_dict 后重跑。
- 个别 case 因 ERP 真数据问题 fail（如客户/商品不存在）→ 记一笔到 follow-up，但不算 release gate fail（gold set 应该针对纯 prompt 行为，不依赖真 ERP 数据）。

---

## 3. 全过后我做什么

```bash
# 1. 切到 main
cd /Users/lin/Desktop/hub
git checkout main && git pull

# 2. 合并 plan6
git merge --no-ff feature/plan6-agent -m "feat(hub): Plan 6 LLM-driven Agent + tool calling 上线"
git push origin main

# 3. 部署生产（同样的 docker compose 流程）
docker compose -p hub up -d --build

# 4. 用 hotpath 监控前 24h（成本告警 / approvals 队列长度 / agent error 率）

# 5. 删 worktree（可选）
cd /Users/lin/Desktop/hub
git worktree remove .worktrees/plan6-agent
git branch -d feature/plan6-agent
```

---

## 4. 一项不过怎么办

| 失败项 | 应对 |
|---|---|
| 故事 1（合同没生成） | `docker logs hub-hub-worker-1 \| grep ERROR` → 看是 LLM 调用 / send_file / 模板渲染哪一层 |
| 故事 2（凭证没创建） | 检查 `bot_user_finance` 角色权限 + ERP `search_orders` 是否真返回数据 |
| 故事 3（批量通过 ERP 落库失败）| ERP-4 端 `customer_price_rule` 或 `vouchers` 表 schema / 权限问题；可单独走 `/api/voucher` 测一次 |
| 故事 4（调价没生效）| ERP-4 `customer_price_rule` 表是否有该记录 + ERP `query_price` 是否查 rule 表 |
| 故事 5（成本看不到）| `tool_call_log` 表里是否有数据 → `dashboard` 聚合查询是否正确 |
| 故事 6（决策链空）| `conversation_log.task_id` 关联是否生效（当前是 ±30s 时间窗口模糊匹配，已知 follow-up） |
| LLM eval < 80% | 不许合并；按 case 类型反推 prompt / few-shots / business_dict 改 |

---

## 5. v8 review 修复回滚预案

如果 staging 验收期间发现 v8 修复（ruff / from_db / report）任何一项有副作用，可单独 revert 这两个 commit 不影响其他 19 个 task：

```bash
git revert --no-edit 63f07a1  # docs only
git revert --no-edit 81c16e8  # 主修复
git push origin feature/plan6-agent
```

然后回滚后单独 push 修复 + 重新跑 release gate。

---

**总结**：上面 7 项（6 故事 + 1 eval）全 ✅ → 直接合并；任意 ❌ → 把失败现象贴回来我接着改。
