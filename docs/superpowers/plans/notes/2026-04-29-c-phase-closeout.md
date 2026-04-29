# C 阶段（Plan 1-5）收官记录

日期：2026-04-29
执行人：Claude Opus 4.7（自动模式）

## C 阶段范围回顾

| Plan | 标题 | 状态 |
|---|---|---|
| Plan 1 | ERP-4 集成（ApiKey scope / 模型 Y / 钉钉绑定接口 / 历史成交价接口） | ✅ 完成（已推送）|
| Plan 2 | HUB 项目骨架（端口 + 任务队列 + 加密 + bootstrap token + setup wizard 骨架） | ✅ 完成 |
| Plan 3 | 钉钉绑定（ChannelAdapter / DingTalkSender / BindingService / IdentityService） | ✅ 完成 |
| Plan 4 | 业务用例 + AI fallback（RuleParser / LLMParser / ChainParser / QueryProductUseCase / 熔断器） | ✅ 完成 |
| Plan 5 | HUB Web 后台（admin 鉴权 / 9 个管理页 / SSE / 审计 / cron / 6 步初始化向导 / 多阶段 Docker） | ✅ 完成 |

**总计**：13 commits（Plan 5 自身）+ 4 个端到端 bug fix + 315 单元测试全绿 + ruff 全绿。

## C 阶段端到端验收（真实部署 + 钉钉对话）

### 验收日志（2026-04-29 16:00-17:30）

通过 OrbStack docker compose up -d 起 4 容器（gateway/worker/postgres/redis），同时拉起 ERP-4 stack（erp/db/redis）联调。期间发现并修复：

| commit | bug | 描述 |
|---|---|---|
| `81ca803` | Erp4Adapter.health_check 路径 | 调 `/api/v1/meta/health` 但实际 ERP 是 `/health`；旧代码只看 200 不验 JSON 导致 SPA fallback 误判通过 |
| `7297a28` | health endpoint 占位 + connect_with_reload 死锁 | health.py dingtalk_stream/erp_default 占位字符串从未真查；adapter.start() SDK 长连接 block 导致 state holder 永远不被赋值 |
| `6458495`（ERP 仓库） | ERP 调 HUB confirm-final URL 缺前缀 | ERP 拼 `${HUB_BASE_URL}/internal/binding/confirm-final` 但 HUB 路由前缀是 `/hub/v1`，导致 405 |
| `23fc2e6` | BindingService 误把已有 hub_user 当冲突 | setup wizard step 3 创建 hub_user + downstream_identity 后，钉钉绑定走 confirm_final 时进了"全新创建"分支撞唯一约束 |
| `a0ac44c`（ERP 仓库） | ERP keyword 不支持中英混排分词 | "讯飞x5" ILIKE 匹不到"科大讯飞智能办公本X5"；分词改 `[一-鿿]+\|[a-zA-Z0-9]+` |

### 端到端通路验证 ✅

- 4 容器健康：dashboard 4 个组件灯全绿
- setup wizard 6 步完整跑通（含 ERP 测试连接 / 创建 admin / 钉钉应用 / DeepSeek / 完成）
- admin 后台 cookie 鉴权（包装 ERP JWT）+ /me 返回权限码
- 钉钉机器人 Stream 长连接已建立
- `/绑定 X` → 拿绑定码 → ERP 个人中心输码 → ERP 调 HUB confirm-final → 钉钉收"绑定成功"+ 隐私告知
- `查讯飞x5` → ERP 多命中 → 钉钉返编号选择卡 → 用户回 `1` → 命中 SKU50139 显示"科大讯飞智能办公本X5 Pro 库存 49"
- 整条对话进 task_log + task_payload（加密） + 实时会话 SSE 流

### C 阶段结束时的产品痛点（真实使用反馈）

测试期间用户输入实际样本，暴露 RuleParser+ERP keyword 的本质局限：

| 用户输入 | 实际行为 | 问题 |
|---|---|---|
| `查讯飞x5` | ✅ 多命中卡 | 修了分词后能用 |
| `查讯飞x5的库存` | ❌ 0 命中 | 关键字提取整段含"的库存"塞 ERP，AND ILIKE 匹不上 |
| `查讯飞x5还有多少库存` | ❌ 0 命中 | 同上，"还有多少库存"被当关键字 |

**根因**：RuleParser 正则简化、ERP 端 keyword AND ILIKE 简单子串。每加一种自然语言形式都要再扩 1 条正则 / 1 条 ERP 分词规则——不可持续。

**用户产品判断（验收期间提出）**：
> 后续我就是想把这个东西变成一个 agent。把这个机器人变成 agent 然后利用后台的这些数据库去帮助大家做工作。而不是只是查询。包括后面做合同，其实也是需要去分析理解的。

→ 该判断与 spec D/E 阶段（B 阶段销售合同 / D 阶段会计凭证）一致。
→ 进入 Plan 6（业务 Agent 化）作为 C → D 阶段桥梁。

## Plan 6 起点

| 维度 | C 阶段当前状态 | Plan 6 升级方向 |
|---|---|---|
| 意图理解 | RuleParser 正则 + LLMParser schema-guided 单步解析 | LLM-driven Agent + tool calling 多步推理 |
| 数据访问 | UseCase 直接调 Erp4Adapter 单 endpoint | Tool registry：把现有 UseCase 包装成 LLM tool |
| 多轮上下文 | ConversationStateRepository（pending_choice / pending_confirm，5 分钟） | ConversationContext（对话历史 + 实体引用 + 30 分钟+） |
| 写操作 | 仅 binding 一类（绑定/解绑） | 合同生成 / 凭证生成 / 价格调整 — 需人工确认链路 |
| 可观察性 | task_log = 1 入站消息 1 行 | conversation_log + tool_call_log（决策链溯源） |
| 模型 | DeepSeek (chat completions) | 同上 + 评估是否升级 Claude/GPT 用 tool calling |

C 阶段代码 90% 是 Plan 6 的工具集基础，不会浪费：
- `Erp4Adapter` 各 endpoint = 天然 tool
- `IdentityService + require_permissions` = 每 tool call 鉴权
- `task_logger` = conversation_log 骨架
- `ChainParser.rule` 路径 = agent 时代仍处理 `/绑定` 等显式命令（杀鸡用牛刀）
- `admin 后台对话监控` = 观测 agent 决策链的天然 UI

## 下一步

Plan 1-5 收官，C 阶段达标。进入 Plan 6 brainstorming → spec → plan → 实施流程。
