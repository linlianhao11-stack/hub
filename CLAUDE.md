# CLAUDE.md — HUB 数据中间件项目指南

## 项目定位

HUB 是连接 ERP-4 系统和钉钉的**数据中间件**，定位"端口/适配器架构"。三类适配器：

- **Channel 适配器**：钉钉 Stream（入站消息）+ 钉钉 OpenAPI（出站推送）
- **Downstream 适配器**：ERP-4（业务系统）
- **Capability 适配器**：DeepSeek / Qwen（AI 意图理解）

C 阶段（当前）目标：钉钉机器人闭环 — 用户绑定钉钉账号 / 商品查询 / 客户历史价查询 / AI 兜底意图理解 / Web 后台管理。后续阶段（B/D）补 Excel 合同、报销审批、凭证生成。

## 技术栈

### 后端
- **语言**: Python 3.11+（用 `from __future__ import annotations`）
- **Web 框架**: FastAPI
- **ORM**: Tortoise ORM + aerich 迁移
- **数据库**: PostgreSQL 16
- **缓存/队列**: Redis 7（AOF 持久化 + Streams 任务队列 + Pub/Sub 实时推流）
- **HTTP 客户端**: httpx（含 MockTransport 用于测试）
- **加密**: AES-256-GCM + HKDF（HUB_MASTER_KEY 在 .env，业务密钥加密存库）
- **钉钉 SDK**: `dingtalk-stream`（入站事件订阅）+ httpx 直调钉钉 OpenAPI（出站推送）
- **测试**: pytest + `asyncio_mode=auto` + fakeredis + httpx MockTransport
- **容器**: Docker + OrbStack（4 容器：gateway / worker / postgres / redis；外加 hub-migrate 一次性容器）

### 前端
- **框架**: Vue 3（Composition API，`<script setup>`）+ Vite 7
- **样式**: Tailwind CSS 4 + CSS 变量设计系统（与 ERP-4 共享 token 风格）
- **状态管理**: Pinia 3
- **路由**: Vue Router 4
- **图标**: lucide-vue-next
- **图表**: Chart.js 4.5
- **HTTP**: Axios（baseURL `/hub/v1`，统一 cookie 鉴权）
- **字体**: Geist + Geist Mono（中文回退系统字体）
- **构建产物**: 多阶段 Docker build → `backend/static/` → gateway StaticFiles + SPA fallback 一起 serve

## 开发规范

### 沟通与流程
- **全程使用中文沟通**，包括代码注释、commit message 描述、文档更新等
- **所有代码开发必须使用 superpowers skill 流程**：brainstorming → writing-plans → subagent-driven-development / executing-plans → verification-before-completion → requesting-code-review
- 设计文档在 `docs/superpowers/specs/`，实施计划在 `docs/superpowers/plans/`
- Docker / 容器管理统一通过 OrbStack，启动前先 `orb start`

### 代码质量铁律
- **单个组件 / 模块文件不超过 250 行**，超过必须拆分
  - 拆分原则：按职责分（把渲染、状态、副作用分离），不是按行数硬切
  - 拆完每个文件都应能独立解释"它做什么 / 怎么用 / 依赖谁"
  - 大文件 = 重构信号，不是规范例外
- **保持优雅**：DRY、YAGNI、TDD、单一职责、显式优于隐式
- **拒绝半成品**：占位（TODO / 略 / `...`）一律不允许进 plan / 代码
- **拒绝向后兼容遗留**：删干净，别留 `_unused` / `// removed` / 已废弃的 re-export
- **错误处理只在系统边界**（用户输入、外部 API），内部代码相信类型和不变量

### 250 行豁免清单

以下文件超 250 行但职责单一，拆分会损害可读性，不拆：

| 文件 | 行数 | 豁免理由 |
|---|---|---|
| `backend/hub/agent/tools/confirm_gate.py` | ~840 | Redis CAS 写门禁状态机，单一职责 |
| `backend/hub/agent/document/contract.py` | ~590 | docx 渲染引擎，工具函数是渲染流程的有机组成 |
| `backend/hub/agent/tools/registry.py` | ~410 | tool 注册-调用中心，schema 构建和调用是同一生命周期 |
| `backend/hub/adapters/downstream/erp4.py` | ~380 | HTTP 适配器，18 处引用的全局核心 |
| `backend/hub/agent/llm_client.py` | ~370 | 两个 client 共享 retry/error 逻辑 |
| `backend/hub/handlers/dingtalk_inbound.py` | ~400 | 入站消息处理入口，fallback 是降级分支不是独立职责 |
| `backend/hub/routers/setup_full.py` | ~370 | setup 向导线性流水线 |
| `backend/hub/seed.py` | ~320 | 一次性启动种子脚本 |

### UI 大白话原则（最重要）

**UI 文案必须中文大白话，绝对禁止暴露任何代码标识符**：

- ❌ 永远不要在 UI 上显示：permission code（如 `platform.apikeys.write`）、role code（如 `system_admin`）、错误码字符串（如 `AUTH_FAILED`）、enum value（如 `status: pending`）、API endpoint 路径
- ✅ 必须显示：中文角色名（"HUB 系统管理员"）、中文状态（"运行中"/"已成功"/"已失败"）、中文错误描述（"密码错误，请重试"）、中文权限说明（"管理 API 密钥"）
- 后端可保留 code 字段做内部 ID（如 `user.role = 'admin'`），但前端渲染一律用 `name + description` 中文字段
- 状态枚举翻译：定义 store / utils 把 enum 翻成中文，不在模板里硬编码 if/else
- 错误提示：catch axios 错误显示 `response.data.detail`，不显示 `code` / `error_classification`

这条原则也写进 ERP-4 的 CLAUDE.md，两个项目对齐。

### Python 规范
- 文件顶部必加 `from __future__ import annotations`（兼容 Python 3.9 的 type hint）
- 公共函数必有 docstring，类型注解必须完整
- 加密 / 解密一律用 `hub.crypto` 的 `encrypt_secret(plain, purpose=...)` 和 `decrypt_secret(blob, purpose=...)`，不要绕开走 EncryptedField 之类不存在的 API
- 异步代码统一 `async def` + `httpx.AsyncClient`；阻塞调用（bcrypt 等）用 `loop.run_in_executor`
- 数据库迁移必须经 aerich，禁止手写 SQL 改 schema
- 加密字段：用 `BinaryField`，命名以 `encrypted_` 开头

### 前端规范
- 组件库优先：所有原子 UI（按钮 / 输入 / 表格 / 弹窗 / 分页）必须用 `frontend/src/components/ui/` 下的封装，禁止手搓
- 表格规范：用 `<AppTable>` + `<AppPagination>` + `usePagination`，禁止手写 `<table>`
- 列表/详情风格：参考 ERP-4 现有 view（`/Users/lin/Desktop/ERP-4/frontend/src/views/CustomersView.vue` 是最近的"列表 + 编辑"模板）
- 表单：用 `<AppInput>` / `<AppSelect>` / `<AppTextarea>` / `<AppButton>` / `<AppModal>`
- API 模块：`frontend/src/api/<domain>.js` 一个域一个文件，业务页面只 import API 模块，不直接 axios

## 架构关键决策

### 模型 Y（Acting-As 模型）
HUB 不持有 ERP 业务数据，所有 ERP 调用通过 `X-Acting-As-User-Id` header 代用户操作。HUB 自己只存：
- 钉钉 ↔ HUB 用户绑定（`channel_user_binding`）
- HUB ↔ ERP 账号关联（`downstream_identity`）
- 加密的渠道 / AI / ERP 凭据（`channel_app` / `ai_provider` / `downstream_system`）
- 任务流水（`task_log` 元数据 365 天 + `task_payload` 加密敏感数据 30 天 TTL）
- 操作审计（`audit_log` + `meta_audit_log` 看 PII 要留痕）

### RBAC 7 表 + 6 预设角色
- 表：`hub_user` / `channel_user_binding` / `downstream_identity` / `hub_role` / `hub_permission` / `hub_role_permission` / `hub_user_role`
- 6 预设角色（中文名）：HUB 系统管理员 / 渠道管理员 / 下游管理员 / 审计员 / 业务用户 / 只读运维
- C 阶段预设角色固定，不开放自定义角色编辑器（B 阶段补）

### 配置变更热重载
- 渠道 / AI / 系统配置通过 admin 后台改完，立即生效，**不要求重启 gateway**
- 实现：`asyncio.Event` reload 信号 + `connect_with_reload` 循环（连上 → 等 reload event → stop 旧 adapter → 重读配置 → 启动新 adapter）

### cron 调度器
- asyncio 任务，每天 03:00 跑：钉钉员工离职巡检（C 路径兜底）+ 过期 task_payload 清理
- 实现要点：job 异常隔离（不能炸 scheduler）、配置缺失跳过（WARN 不抛异常）、OpenAPI 失败重试 1 次

### 安全基线
- HUB_MASTER_KEY 必须放 `.env`，永远不入库不入日志
- 业务密钥（app_secret / api_key / ERP password）一律加密存储 + `purpose` HKDF 派生
- session cookie 用 ERP 已签的 JWT 包装，HUB 不自己签 token
- bootstrap token 一次性，写入即销毁；setup wizard 6 步用 X-Setup-Session 校验

## 验证策略

- **首选 `pytest`**：测试通过即可覆盖 90% 问题，大部分改动到此即可
- **build 通过**：前端 `npm run build` + 后端 `python -c "import main"` 不抛异常
- **端到端**：`docker compose up -d` 跑通完整向导 + 钉钉收发消息
- **视觉/CSS 变更**：用 MCP 截图验证关键页面，不要逐页逐 tab 循环截图
- **机械性改动**：批量重命名 / 改 class，build 通过即可，跳过截图
- **禁止**：mock 真实业务数据库 / 假装跑过测试 / 提交带 `pass` 占位的测试

## 反模式清单（禁止出现）

### 代码层面
- ❌ 单文件超 250 行不拆
- ❌ 占位代码（`...` / `TODO` / `略` / `pass` 当真实测试）
- ❌ 函数内 late import 还想被 monkeypatch（要么提顶层，要么 patch 真实路径）
- ❌ 用不存在的 API（如 `EncryptedField.decrypt`，应该是 `decrypt_secret(..., purpose=...)`）
- ❌ 异常被静默 swallow（必须 logger.exception 留痕）
- ❌ 数据库字段瞎猜（每次 create 前查 model 真实 schema）

### UI 层面
- ❌ 任何 code / enum / 错误码暴露给用户
- ❌ 硬编码色值（必须 `var(--xxx)` 引用 token）
- ❌ 手搓 UI 组件（必须用组件库）
- ❌ 工具栏放在卡片内（必须独立在卡片外）
- ❌ 灰色文字在彩色背景（无障碍 WCAG AA 底线）
- ❌ `<div @click>` 代替 `<button>`
- ❌ 弹窗嵌套 3 层以上

### 流程层面
- ❌ 跳过 brainstorming 直接写代码
- ❌ plan 里塞占位等"实施时再说"
- ❌ 不审查就放行 plan / PR
- ❌ commit 信息只写 "fix" / "update" 这种没营养的话

## 与 ERP-4 项目的关系

- HUB 通过 `Erp4Adapter` 调 ERP 的 `/api/v1/*` 接口；ERP 侧需要为 HUB 提供 `ServiceAccount + ApiKey + scopes` 体系（Plan 1 完成）
- 共享设计语言：tokens.css / theme.css / 字体方案 / 组件库风格
- 共享原则：UI 大白话原则同步到两个项目的 CLAUDE.md
- 不共享数据库：HUB 是独立 PostgreSQL，通过 HTTP API 调 ERP，不直连 ERP 数据库

## 文档与目录

```
hub/
├── CLAUDE.md                          # 本文件
├── docs/superpowers/
│   ├── specs/                         # 设计文档
│   │   └── 2026-04-27-hub-middleware-design.md
│   └── plans/                         # 实施计划
│       ├── 2026-04-27-erp-integration-changes.md
│       ├── 2026-04-27-hub-skeleton.md
│       ├── 2026-04-27-hub-dingtalk-binding.md
│       ├── 2026-04-27-hub-business-usecase.md
│       └── 2026-04-27-hub-web-admin.md
├── backend/
│   ├── main.py                        # gateway 入口（FastAPI）
│   ├── worker.py                      # 任务消费 worker
│   ├── pyproject.toml
│   └── hub/
│       ├── adapters/{channel,downstream,capability}/
│       ├── auth/                      # session_auth + admin_perms
│       ├── crypto.py                  # encrypt_secret / decrypt_secret
│       ├── routers/{admin,setup_full}.py
│       ├── services/                  # binding_service / identity_service
│       ├── usecases/                  # query_product / query_customer_history
│       ├── handlers/                  # dingtalk_inbound / dingtalk_outbound
│       ├── observability/             # task_logger + live_stream
│       ├── lifecycle/                 # connect_with_reload
│       ├── cron/                      # scheduler + jobs + dingtalk_user_client
│       └── models.py                  # Tortoise ORM 模型
└── frontend/
    ├── package.json
    ├── vite.config.js                 # build.outDir = "./dist"（Docker 多阶段拷贝）
    └── src/
        ├── api/                       # 12 个域 API 模块
        ├── components/{ui,common}/
        ├── stores/                    # Pinia
        ├── router/index.js
        ├── styles/{tokens,theme,base}.css
        └── views/
            ├── setup/                 # Step01-06
            ├── auth/LoginView.vue
            └── admin/                 # 12 个管理页

