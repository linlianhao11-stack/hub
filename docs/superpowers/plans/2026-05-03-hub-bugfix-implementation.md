# HUB 缺陷修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复两轮 Code Review 发现的 47 项缺陷，覆盖安全加固、并发安全、数据库性能、Worker 可靠性、代码 DRY、前端质量和 Docker 运维 7 个域。本计划只规定执行顺序、验收标准和依赖关系，不规定具体实现方式。

**Architecture:** 不改变现有六边形架构。所有修复在现有 ports & adapters 框架内完成，不引入新外部依赖（速率限制用 Redis 原生计数，不引入新库）。

**前置阅读：**
- `CODE_REVIEW.md` — 第一轮 Review 结果
- 本会话两轮深度 Review — 补充 36 项额外问题

**前置依赖：** 无。可立即启动。

**估时：** 10 个工作日（2 周），含回归测试。

---

## 执行流程总览

```
Phase 1: 紧急安全       Day 1-2   ──→  Gate: 安全验收通过
Phase 2a: DB 性能       Day 3     ──→  Gate: 性能指标达标
Phase 2b: Worker 可靠性  Day 4-5   ──→  Gate: 容错测试通过
Phase 2c: 事务安全       Day 5     ──→  Gate: 并发测试通过
Phase 3: 代码质量        Day 6-7   ──→  Gate: lint + test 通过
Phase 4a: 前端修复       Day 8     ──→  Gate: build + Vitest 通过
Phase 4b: Docker 运维    Day 8     ──→  Gate: compose up 验证
Phase 4c: 输入校验       Day 9     ──→  Gate: 422 测试通过
Phase 5: 回归加固        Day 10    ──→  Gate: 全量 + E2E 通过
```

每个 Phase 结束时必须通过 Gate 才能进入下一个 Phase。Gate 不通过则回退修复，不推进。

---

## Phase 1: 紧急安全修复

**目标:** 消除可被直接利用的安全漏洞和唯一的确认生产级并发 Bug。  
**时间:** Day 1-2（~9h）  
**阻塞:** 无前置依赖，立即开始。

### Step 1.1: 登录接口速率限制

- [ ] 在 `backend/hub/auth/` 下新增 `rate_limit.py`，实现基于 Redis 的滑动窗口计数器
- [ ] 维度：IP 地址 + 用户名双维度，各 10 次/分钟
- [ ] 在 `backend/hub/routers/admin/login.py` 的 `POST /login` 路由上应用限流装饰器
- [ ] 超限返回 HTTP 429 + 中文提示"登录尝试过于频繁，请稍后再试"
- [ ] 新增测试 `backend/tests/test_admin_login_ratelimit.py`

**涉及文件:**
- 新增: `backend/hub/auth/rate_limit.py`
- 修改: `backend/hub/routers/admin/login.py`
- 新增: `backend/tests/test_admin_login_ratelimit.py`

**验收标准:**
- [ ] 同 IP 10 次失败后第 11 次返回 429
- [ ] 不同 IP 独立计数
- [ ] 成功登录不计数
- [ ] Redis 不可用时降级为不限流（不阻断登录）

**关联缺陷:** SEC-1

---

### Step 1.2: Cookie 安全属性配置化

- [ ] 在 `backend/hub/config.py` 的 `Settings` 类中新增 `cookie_secure: bool = True` 和 `cookie_samesite: str = "lax"`
- [ ] 修改 `backend/hub/routers/admin/login.py:31-33`，从 Settings 读取 `secure` 和 `samesite`
- [ ] 在 `.env.example` 中添加 `HUB_COOKIE_SECURE=true` 注释说明
- [ ] 补充测试：验证 `Settings(cookie_secure=False)` 时 cookie 的 secure 属性

**涉及文件:**
- 修改: `backend/hub/config.py`
- 修改: `backend/hub/routers/admin/login.py`
- 修改: `.env.example`
- 修改: `backend/tests/test_admin_key_auth.py`（扩展 cookie 相关断言）

**验收标准:**
- [ ] 生产环境（默认）cookie `secure=True, samesite=lax`
- [ ] 本地开发可通过环境变量关闭 secure
- [ ] 现有登录测试全部通过

**关联缺陷:** SEC-2

---

### Step 1.3: 修复 sender.send_text 并发 Bug

- [ ] 在 `backend/hub/handlers/dingtalk_inbound.py` 中消除对 `sender.send_text` 的共享可变状态依赖
- [ ] 方向：handler 自带 wrapper 引用而非修改 sender 实例属性（使用 `contextvars.ContextVar` 或局部闭包）
- [ ] 确认 `sender.send_text` 在 handler 执行前后不被修改
- [ ] 新增并发测试：`asyncio.gather` 模拟两个同时到达的钉钉消息，验证响应各自正确送达

**涉及文件:**
- 修改: `backend/hub/handlers/dingtalk_inbound.py`
- 修改: `backend/tests/test_dingtalk_inbound_handler.py`

**验收标准:**
- [ ] 并发测试：2 个并发请求各自收到正确回复，无交叉
- [ ] 现有 inbound handler 全部测试通过
- [ ] `dingtalk_inbound.py` 不超过 250 行（如超限，同步执行 Step 3.7 拆分）

**关联缺陷:** TXN-1

---

### Step 1.4: assign_user_roles 事务包裹

- [ ] 在 `backend/hub/routers/admin/users.py:135-140` 的 `assign_user_roles` 函数中，将 delete + create 操作包裹在 `in_transaction()` 中
- [ ] 确认事务失败时角色不丢失（回滚验证）
- [ ] 新增测试：事务中断后角色完整保留

**涉及文件:**
- 修改: `backend/hub/routers/admin/users.py`
- 修改: `backend/tests/test_admin_users.py`

**验收标准:**
- [ ] 角色删除+创建在同一事务内
- [ ] 模拟事务异常后角色不变
- [ ] 现有用户管理测试通过

**关联缺陷:** TXN-2

---

### Step 1.5: 异常信息脱敏

- [ ] 在 `backend/hub/routers/admin/ai_providers.py` 的 `test_chat` 异常处理中，将 `str(e)` 替换为脱敏后的固定提示（如"AI 服务连接失败，请检查配置"）
- [ ] 在 `backend/hub/adapters/downstream/erp4.py` 的 `_safe_detail` 方法中，对 `r.text` 进行 key/url 模式过滤
- [ ] 确认异常日志仍记录完整信息（`logger.exception`），仅对外响应脱敏

**涉及文件:**
- 修改: `backend/hub/routers/admin/ai_providers.py`
- 修改: `backend/hub/adapters/downstream/erp4.py`
- 修改: `backend/tests/test_admin_ai_providers.py`
- 修改: `backend/tests/test_erp4_adapter.py`

**验收标准:**
- [ ] `test_chat` 失败时前端看不到 API Key / 内部 URL
- [ ] ERP 错误响应不泄漏 API Key 到 HTTP 响应体
- [ ] 后端日志仍包含完整异常信息

**关联缺陷:** SEC-3

---

### Phase 1 Gate: 安全验收

执行以下检查，全部通过方可进入 Phase 2：

- [ ] `pytest backend/tests/ -x` 全量通过
- [ ] `pytest -k "ratelimit"` 新测试通过
- [ ] `pytest -k "concurrent"` 并发测试通过
- [ ] 手动验证：`docker compose up -d` 后连续 11 次错误登录，第 11 次返回 429
- [ ] `ruff check backend/` 无新增告警

---

## Phase 2a: 数据库性能

**目标:** 消除 Dashboard 查询风暴和权限 N+1 问题。  
**时间:** Day 3（~5.5h）  
**前置:** Phase 1 Gate 通过。

### Step 2a.1: Dashboard 改单条 GROUP BY 查询

- [ ] 将 `backend/hub/routers/admin/dashboard.py:86-107` 的 24 个小时桶循环替换为单条 SQL 查询
- [ ] 使用 Tortoise ORM 的 `.annotate()` + `.group_by()` 或 raw SQL（`EXTRACT(HOUR FROM created_at)`）
- [ ] 单条查询返回 24 行（每小时 total / success / failed），在 Python 侧组装为前端需要的数组格式
- [ ] 更新 `backend/tests/test_admin_dashboard.py` 中的断言

**涉及文件:**
- 修改: `backend/hub/routers/admin/dashboard.py`
- 修改: `backend/tests/test_admin_dashboard.py`

**验收标准:**
- [ ] Dashboard API 的 DB 查询数 ≤ 3（总览 + 小时分布 + 可选的渠道统计）
- [ ] 10 万条 TaskLog 时 API 响应 < 500ms
- [ ] 小时数据结构与现有前端图表兼容（不改前端）

**关联缺陷:** DB-1

---

### Step 2a.2: 权限查询改批量预加载

- [ ] 将 `backend/hub/auth/permissions.py:22-28` 的 for 循环替换为单条批量查询
- [ ] 先收集所有 `role_id`，再 `HubRole.filter(id__in=role_ids).prefetch_related("permissions")`
- [ ] 更新 `backend/tests/test_permissions.py`

**涉及文件:**
- 修改: `backend/hub/auth/permissions.py`
- 修改: `backend/tests/test_permissions.py`

**验收标准:**
- [ ] `get_user_permissions()` 固定 2 次查询（1 次角色 + 1 次权限）
- [ ] 返回结果与修复前完全一致
- [ ] N 个角色的执行时间不随 N 线性增长

**关联缺陷:** DB-2

---

### Step 2a.3: 钉钉用户同步改批量 UPDATE

- [ ] 将 `backend/hub/cron/dingtalk_user_sync.py:32-38` 的逐条 `save()` 改为批量 `update()` 
- [ ] 收集所有需要更新的 binding id 列表，一次 `HubUserBinding.filter(id__in=ids).update(status="revoked")`
- [ ] 更新 `backend/tests/test_dingtalk_user_sync.py`

**涉及文件:**
- 修改: `backend/hub/cron/dingtalk_user_sync.py`
- 修改: `backend/tests/test_dingtalk_user_sync.py`

**验收标准:**
- [ ] 同步 1000 条 revoked 绑定只产生 1 次 UPDATE SQL
- [ ] 同步逻辑结果不变

**关联缺陷:** DB-3

---

### Step 2a.4: HubPermission 加复合唯一索引

- [ ] 在 `backend/hub/models/rbac.py` 的 `HubPermission.Meta` 中添加 `unique_together = [("resource", "sub_resource", "action")]`
- [ ] 生成 aerich 迁移文件：`cd backend && aerich migrate --name "add_permission_unique_index"`
- [ ] 编写迁移前数据清洗逻辑：如有重复权限先合并引用再删除冗余行（迁移脚本中处理）
- [ ] 执行 `aerich upgrade` 并验证

**涉及文件:**
- 修改: `backend/hub/models/rbac.py`
- 新增: `backend/migrations/models/<timestamp>_add_permission_unique_index.py`
- 修改: `backend/tests/test_admin_perms.py`

**验收标准:**
- [ ] `aerich upgrade` 无报错
- [ ] 重复权限插入抛 `IntegrityError`
- [ ] 现有种子数据不受影响

**关联缺陷:** DB-4

---

### Phase 2a Gate: 性能验收

- [ ] `pytest backend/tests/ -x` 全量通过
- [ ] Dashboard API 查询数 ≤ 3（可通过 Tortoise 日志确认）
- [ ] 权限查询固定 2 次 DB 调用
- [ ] `aerich upgrade && aerich migrate` 无报错

---

## Phase 2b: Worker 可靠性

**目标:** 补全重试机制、配置热更新、SSE 心跳。  
**时间:** Day 4-5（~10h）  
**前置:** Phase 2a Gate 通过。

### Step 2b.1: Worker 失败重试机制

- [ ] 在 `backend/hub/worker_runtime.py:69-72` 的异常处理中，增加最多 3 次重试，指数退避（1s → 2s → 4s）
- [ ] 重试计数通过 Redis Stream message 的 metadata 头传递（`x-retry-count`）
- [ ] 超过重试次数后移入死信队列，死信记录含失败原因和重试历史
- [ ] 更新 `backend/tests/test_worker_runtime.py`

**涉及文件:**
- 修改: `backend/hub/worker_runtime.py`
- 修改: `backend/hub/queue/redis_streams.py`（move_to_dead 增加元数据）
- 修改: `backend/tests/test_worker_runtime.py`

**验收标准:**
- [ ] 模拟 ERP 503，消息重试 3 次后才进死信
- [ ] 重试间隔符合 1s → 2s → 4s
- [ ] 最终进死信的消息包含失败原因和重试次数

**关联缺陷:** REL-1

---

### Step 2b.2: Worker 配置热更新

- [ ] 在 `backend/hub/routers/admin/` 的下游系统/AI Provider/渠道配置变更接口中，变更成功后通过 Redis Pub/Sub 发布 `hub:config:changed` 事件
- [ ] Worker 在启动时订阅 `hub:config:changed` 频道
- [ ] 收到事件后重新加载 `DownstreamSystem` / `AIProvider` / `ChannelApp` 配置，重建 adapter
- [ ] 确保配置切换过程中正在处理的任务不受影响（新任务用新配置，进行中的任务不中断）
- [ ] 更新 `backend/tests/test_worker_runtime.py`

**涉及文件:**
- 新增: `backend/hub/queue/config_watcher.py`
- 修改: `backend/hub/routers/admin/downstreams.py`（变更后发布事件）
- 修改: `backend/hub/routers/admin/ai_providers.py`（变更后发布事件）
- 修改: `backend/hub/routers/admin/channels.py`（变更后发布事件）
- 修改: `backend/worker.py`
- 新增/修改: 相关测试文件

**验收标准:**
- [ ] Admin 修改 ERP API Key 后 Worker 日志显示"配置已刷新"
- [ ] 刷新后新任务使用新配置
- [ ] 刷新过程中进行中任务不受影响
- [ ] Redis Pub/Sub 断连后自动重订阅

**关联缺陷:** REL-2

---

### Step 2b.3: SSE 心跳

- [ ] 在 `backend/hub/observability/live_stream.py` 的 `stream()` 生成器中，添加 `asyncio.wait_for` 超时机制
- [ ] 每 15 秒无新消息时发送 `: keepalive\n\n` SSE 注释
- [ ] 确认 `conversation.py` 的 SSE 端点不被 nginx 60s 超时断开
- [ ] 更新 `backend/tests/test_live_stream.py`

**涉及文件:**
- 修改: `backend/hub/observability/live_stream.py`
- 修改: `backend/tests/test_admin_conversation_live.py`

**验收标准:**
- [ ] 空闲 60 秒 SSE 连接不断开
- [ ] 心跳注释不触发前端 EventListener
- [ ] 实际消息推送延迟 < 1s

**关联缺陷:** REL-3

---

### Step 2b.4: Redis xgroup_create 异常过滤

- [ ] 将 `backend/hub/queue/redis_streams.py:53-56` 的 `except Exception: pass` 改为仅捕获 `BUSYGROUP` 错误（redis 的 `ResponseError` 且包含 `BUSYGROUP` 关键词）
- [ ] 其他异常正常抛出，不吞掉
- [ ] 更新 `backend/tests/test_redis_streams_runner.py`

**涉及文件:**
- 修改: `backend/hub/queue/redis_streams.py`
- 修改: `backend/tests/test_redis_streams_runner.py`

**验收标准:**
- [ ] Consumer group 已存在时不报错（正常通过）
- [ ] Redis 连接失败时异常正常抛出
- [ ] 现有测试通过

**关联缺陷:** REL-5

---

### Step 2b.5: Worker 优雅关停

- [ ] 在 `backend/worker.py` 的 `main()` 中注册 `SIGTERM` / `SIGINT` 信号处理
- [ ] 收到信号后调用 `runtime.stop()`，等待当前正在处理的消息完成后退出
- [ ] 设置 10 秒超时，超时后强制退出
- [ ] 更新 `backend/tests/test_worker_runtime.py`

**涉及文件:**
- 修改: `backend/worker.py`
- 修改: `backend/tests/test_worker_runtime.py`

**验收标准:**
- [ ] `docker compose stop hub-worker` 后日志显示"优雅关闭中..."
- [ ] 当前消息处理完成后再退出
- [ ] 10 秒超时后强制退出

**关联缺陷:** OPS-4

---

### Phase 2b Gate: 可靠性验收

- [ ] `pytest backend/tests/ -x` 全量通过
- [ ] 模拟 ERP 故障场景：重试 3 次后进死信，日志完整
- [ ] Admin 改配置后 Worker 10s 内刷新
- [ ] SSE 连接空闲 60s 不断开
- [ ] `docker compose stop hub-worker` 优雅关闭

---

## Phase 2c: 事务安全

**目标:** 消除竞态条件和原子性缺陷。  
**时间:** Day 5 下午（~2.5h）  
**前置:** Phase 2b Gate 通过。

### Step 2c.1: Binding confirm 快路径移入事务

- [ ] 将 `backend/hub/services/binding_service.py:106-111` 的 early return 检查移入 `in_transaction()` 块内
- [ ] 确保从 check 到 create 的整个过程是原子的
- [ ] 更新 `backend/tests/test_binding_service.py`（已有并发测试，验证修复后通过）

**涉及文件:**
- 修改: `backend/hub/services/binding_service.py`
- 修改: `backend/tests/test_binding_service.py`

**验收标准:**
- [ ] 并发 confirm-final（相同 token）只有一个成功
- [ ] 现有绑定流程不受影响

**关联缺陷:** TXN-3

---

### Step 2c.2: erp_active_cache 改用 update_or_create

- [ ] 将 `backend/hub/services/erp_active_cache.py:32-38` 的先 update 后 create 改为 `ErpUserStateCache.update_or_create()`
- [ ] 更新 `backend/tests/test_erp_active_cache.py`

**涉及文件:**
- 修改: `backend/hub/services/erp_active_cache.py`
- 修改: `backend/tests/test_erp_active_cache.py`

**验收标准:**
- [ ] 并发 upsert 不抛 IntegrityError
- [ ] 结果与现有逻辑一致

**关联缺陷:** TXN-4

---

### Step 2c.3: create_ai 操作顺序调整

- [ ] 在 `backend/hub/routers/admin/ai_providers.py:59` 中，调整顺序为：先创建新 provider 并设为 active，再禁用其他所有 provider
- [ ] 将两步操作包裹在事务中
- [ ] 更新 `backend/tests/test_admin_ai_providers.py`

**涉及文件:**
- 修改: `backend/hub/routers/admin/ai_providers.py`
- 修改: `backend/tests/test_admin_ai_providers.py`

**验收标准:**
- [ ] 创建过程中始终有至少一个 active provider（事务内）
- [ ] 现有测试通过

**关联缺陷:** TXN-5

---

### Phase 2c Gate: 事务安全验收

- [ ] `pytest backend/tests/ -x` 全量通过
- [ ] `pytest -k "binding"` 并发绑定测试通过
- [ ] `pytest -k "cache"` 缓存并发测试通过

---

## Phase 3: 代码质量 / DRY

**目标:** 消除重复代码，拆分超长文件，符合 CLAUDE.md 250 行规范。  
**时间:** Day 6-7（~10.5h）  
**前置:** Phase 2c Gate 通过。

### Step 3.1: 提取 _send_message 共享模块

- [ ] 在 `backend/hub/usecases/` 下新增 `_send.py`，提取 `_send_message()` 公共方法
- [ ] `query_product.py` 和 `query_customer_history.py` 改为导入调用
- [ ] 更新对应测试文件

**涉及文件:**
- 新增: `backend/hub/usecases/_send.py`
- 修改: `backend/hub/usecases/query_product.py`
- 修改: `backend/hub/usecases/query_customer_history.py`

**验收标准:**
- [ ] 两个 use case 中无重复的 send_message 逻辑
- [ ] 现有测试通过

**关联缺陷:** DRY-1

---

### Step 3.2: 提取 payload 解密共享模块

- [ ] 在 `backend/hub/observability/` 或 `backend/hub/crypto/` 下新增 `payload_access.py`，提取解密+JSON解析+审计日志写入逻辑
- [ ] `tasks.py` 和 `conversation.py` 改为导入调用
- [ ] 此步骤同时修复 MISC-1（解密失败时不写 MetaAuditLog）
- [ ] 更新对应测试文件

**涉及文件:**
- 新增: `backend/hub/observability/payload_access.py`
- 修改: `backend/hub/routers/admin/tasks.py`
- 修改: `backend/hub/routers/admin/conversation.py`

**验收标准:**
- [ ] 解密逻辑不重复
- [ ] 解密失败时不写 MetaAuditLog
- [ ] 解密成功时正常写审计

**关联缺陷:** DRY-2, MISC-1

---

### Step 3.3: 集中 AI_DEFAULTS 常量

- [ ] 在 `backend/hub/capabilities/` 下新增或使用现有 `constants.py`，定义 `_AI_DEFAULTS`
- [ ] `setup_full.py` 和 `ai_providers.py` 改为从同一处导入
- [ ] 更新对应测试

**涉及文件:**
- 新增/修改: `backend/hub/capabilities/constants.py`
- 修改: `backend/hub/routers/setup_full.py`
- 修改: `backend/hub/routers/admin/ai_providers.py`

**验收标准:**
- [ ] `rg "_AI_DEFAULTS"` 只出现一处定义

**关联缺陷:** DRY-3

---

### Step 3.4: 提取 main/worker 共享启动工厂

- [ ] 在 `backend/hub/` 下新增 `bootstrap.py`，提取 `build_dependencies()` 共享工厂
- [ ] 包含：Redis 连接、DB 初始化、配置读取、adapter 构造
- [ ] `main.py` 和 `worker.py` 改为调用 `build_dependencies()`
- [ ] 更新测试

**涉及文件:**
- 新增: `backend/hub/bootstrap.py`
- 修改: `backend/main.py`
- 修改: `backend/worker.py`

**验收标准:**
- [ ] `main.py` 和 `worker.py` 无重复的启动逻辑
- [ ] 现有测试通过

**关联缺陷:** DRY-4

---

### Step 3.5: 提取 erp4 熔断调用装饰器

- [ ] 在 `backend/hub/circuit_breaker/` 下新增装饰器或上下文管理器，封装 `_breaker.call(_do)` 模式
- [ ] `erp4.py` 中 6 处重复调用改为使用装饰器/上下文管理器
- [ ] 更新 `backend/tests/test_erp4_adapter.py`

**涉及文件:**
- 新增: `backend/hub/circuit_breaker/breaker_call.py`
- 修改: `backend/hub/adapters/downstream/erp4.py`

**验收标准:**
- [ ] `erp4.py` 中无重复的 `_breaker.call` 模式
- [ ] 熔断行为不变

**关联缺陷:** DRY-5

---

### Step 3.6: 拆分系统/业务熔断器

- [ ] 在 `erp4.py` 中将系统调用（`login`, `get_me`）和业务调用（`search_products` 等）使用独立的熔断器实例
- [ ] 系统熔断器阈值宽松：10 次/60s → 120s 开路
- [ ] 业务熔断器保持现有：5 次/30s → 60s 开路
- [ ] 更新测试

**涉及文件:**
- 修改: `backend/hub/adapters/downstream/erp4.py`
- 修改: `backend/tests/test_erp_breaker.py`

**验收标准:**
- [ ] 业务接口 5xx 不影响系统接口（login 仍可用）
- [ ] 两套熔断器独立计数、独立开路/半开

**关联缺陷:** DRY-6

---

### Step 3.7: 拆分 dingtalk_inbound.py 到 250 行内

- [ ] 将 `backend/hub/handlers/dingtalk_inbound.py`（295 行）按职责拆分
- [ ] 拆分方案建议：提取意图路由和低置信度确认逻辑为独立模块
- [ ] handler 入口保持简洁（接收消息 → 调路由 → 发回复）
- [ ] 更新 `backend/tests/test_dingtalk_inbound_handler.py`

**涉及文件:**
- 修改: `backend/hub/handlers/dingtalk_inbound.py`
- 新增: 按职责拆分的子模块
- 修改: 相关测试

**验收标准:**
- [ ] `dingtalk_inbound.py` ≤ 250 行
- [ ] 拆分后的每个文件职责单一
- [ ] 现有测试通过

**关联缺陷:** CLAUDE.md 规范

---

### Phase 3 Gate: 代码质量验收

- [ ] `pytest backend/tests/ -x` 全量通过
- [ ] `ruff check backend/` 无告警
- [ ] 所有修改文件 ≤ 250 行
- [ ] `rg` 确认无 DRY 违反残留

---

## Phase 4a: 前端修复

**目标:** 修复前端 Bug 和 UX 问题，引入测试基础设施。  
**时间:** Day 8（~7h）  
**前置:** Phase 3 Gate 通过。

### Step 4a.1: 引入 Vitest 基础设施

- [ ] 安装 `vitest` + `@vue/test-utils` 依赖
- [ ] 创建 `frontend/vitest.config.js`
- [ ] 编写 3 个种子测试（`AppButton` 渲染、`auth store` hasPerm、API 模块 mock）
- [ ] 在 `package.json` 中添加 `test` script

**涉及文件:**
- 新增: `frontend/vitest.config.js`
- 修改: `frontend/package.json`
- 新增: `frontend/src/components/ui/__tests__/AppButton.spec.js`
- 新增: `frontend/src/stores/__tests__/auth.spec.js`

**验收标准:**
- [ ] `npm test` 通过
- [ ] 至少 3 个测试覆盖组件和 store

**关联缺陷:** FE-7

---

### Step 4a.2: Complete.vue setTimeout 清理

- [ ] 在 `frontend/src/views/setup/steps/Complete.vue` 中添加 `onBeforeUnmount` 钩子，清除 setTimeout
- [ ] 存储 timer ref，卸载时 `clearTimeout`

**涉及文件:**
- 修改: `frontend/src/views/setup/steps/Complete.vue`

**验收标准:**
- [ ] 组件卸载后不再执行 `router.replace`
- [ ] 正常流程不受影响

**关联缺陷:** FE-1

---

### Step 4a.3: Dashboard 自动刷新

- [ ] 在 `frontend/src/views/admin/DashboardView.vue` 中添加 `setInterval` 每 30 秒重新加载数据
- [ ] `onBeforeUnmount` 中清除 interval
- [ ] 使用 `document.visibilitychange` 事件，页面不可见时暂停刷新

**涉及文件:**
- 修改: `frontend/src/views/admin/DashboardView.vue`

**验收标准:**
- [ ] 页面可见时每 30s 自动刷新
- [ ] 切换 tab 后停止刷新，回来后恢复

**关联缺陷:** FE-2

---

### Step 4a.4: window.confirm 替换为 AppModal

- [ ] 在 `ChannelsView.vue` 和 `DownstreamsView.vue` 中，将 `window.confirm()` 替换为 `AppModal` 确认弹窗
- [ ] 复用现有 `AppModal` 组件，保持与其他页面一致的交互风格

**涉及文件:**
- 修改: `frontend/src/views/admin/ChannelsView.vue`
- 修改: `frontend/src/views/admin/DownstreamsView.vue`

**验收标准:**
- [ ] 停用/删除操作使用 AppModal 确认
- [ ] 风格与其他页面一致

**关联缺陷:** FE-3

---

### Step 4a.5: Chart.js 按需导入

- [ ] 在 `DashboardView.vue` 中，将 `Chart.register(...registerables)` 改为只导入 `LineController`, `LineElement`, `PointElement`, `LinearScale`, `CategoryScale`, `Filler`, `Legend`, `Title`, `Tooltip`
- [ ] 验证图表正常渲染

**涉及文件:**
- 修改: `frontend/src/views/admin/DashboardView.vue`

**验收标准:**
- [ ] 图表渲染正常
- [ ] 构建产物减小 ~80KB（可通过 `vite build` 产物对比确认）

**关联缺陷:** FE-4

---

### Step 4a.6: 系统配置页并行加载

- [ ] 在 `SystemConfigView.vue` 中，将 `for` 循环的串行 `await getConfig()` 改为 `Promise.all()`
- [ ] 加 loading 状态管理

**涉及文件:**
- 修改: `frontend/src/views/admin/SystemConfigView.vue`

**验收标准:**
- [ ] 5 个配置项并行加载，总耗时 ≈ 最慢的单项（而非 5 项之和）
- [ ] Loading 状态正确显示

**关联缺陷:** FE-5

---

### Step 4a.7: login() 错误处理拆分

- [ ] 在 `frontend/src/stores/auth.js` 的 `login()` 中，将 `fetchMe()` 的错误与 `api.post` 的错误分开处理
- [ ] 登录成功但 fetchMe 失败时，显示"登录成功，正在加载..."并重试 fetchMe

**涉及文件:**
- 修改: `frontend/src/stores/auth.js`
- 修改: `frontend/src/views/auth/LoginView.vue`

**验收标准:**
- [ ] 登录成功后 fetchMe 失败不误报"登录失败"
- [ ] 重试机制正常工作

**关联缺陷:** FE-6

---

### Phase 4a Gate: 前端验收

- [ ] `npm run build` 通过
- [ ] `npm test` 通过（至少 3 个测试）
- [ ] `vite build` 产物大小对比确认 Chart.js 优化生效
- [ ] 手动验证：Dashboard 自动刷新、停用确认弹窗

---

## Phase 4b: Docker / 运维

**目标:** 补全 Docker 健康检查和日志轮转。  
**时间:** Day 8 下午（~2.5h）  
**前置:** Phase 4a Gate 通过（可与 4a 并行执行）。

### Step 4b.1: Gateway / Worker Docker 健康检查

- [ ] Gateway: 添加 `healthcheck: test: ["CMD", "curl", "-f", "http://localhost:8091/hub/v1/health"]`
- [ ] Worker: 在 Worker 进程中添加心跳文件写入（每 30s 写 `/tmp/worker-heartbeat`），Docker healthcheck 检查文件修改时间
- [ ] 两个服务都配置 `interval: 30s, timeout: 5s, retries: 3, start_period: 30s`

**涉及文件:**
- 修改: `docker-compose.yml`
- 修改: `backend/worker.py`（心跳文件）

**验收标准:**
- [ ] `docker inspect hub-gateway | jq '.[0].State.Health.Status'` 返回 `healthy`
- [ ] `docker inspect hub-worker | jq '.[0].State.Health.Status'` 返回 `healthy`
- [ ] 手动 kill worker 进程后 90s 内 Docker 标记为 unhealthy 并重启

**关联缺陷:** OPS-1

---

### Step 4b.2: 日志轮转配置

- [ ] 在 `docker-compose.yml` 的所有服务中添加 `logging: driver: json-file options: max-size: "10m" max-file: "3"`

**涉及文件:**
- 修改: `docker-compose.yml`

**验收标准:**
- [ ] `docker inspect` 显示 logging 配置
- [ ] 日志文件不超过 30MB/服务

**关联缺陷:** OPS-3

---

### Step 4b.3: 默认密码加固

- [ ] 将 `docker-compose.yml:10` 的 `HUB_POSTGRES_PASSWORD` 默认值从 `"hub"` 改为空字符串（强制通过 .env 设置）
- [ ] 在 `.env.example` 中添加注释：此值必须修改
- [ ] 启动脚本检测密码为空时拒绝启动

**涉及文件:**
- 修改: `docker-compose.yml`
- 修改: `.env.example`

**验收标准:**
- [ ] 不设置密码时 `docker compose up` 报错退出
- [ ] 设置密码后正常启动

**关联缺陷:** OPS-2

---

### Phase 4b Gate: Docker 验收

- [ ] `docker compose up -d` 全部 healthy
- [ ] 日志轮转配置生效
- [ ] 弱密码无法启动

---

## Phase 4c: 输入校验与杂项

**目标:** 补全输入校验，修复边界条件 Bug。  
**时间:** Day 9（~5h）  
**前置:** Phase 4b Gate 通过。

### Step 4c.1: channel_type 输入校验

- [ ] 在 `backend/hub/routers/admin/channels.py` 的 Pydantic schema 中为 `channel_type` 添加 `Field(pattern="^(dingtalk)$")` 约束
- [ ] 更新测试

**涉及文件:**
- 修改: `backend/hub/routers/admin/channels.py`
- 修改: `backend/tests/test_admin_channels.py`

**验收标准:**
- [ ] 非法 channel_type 返回 422
- [ ] "dingtalk" 正常通过

**关联缺陷:** SEC-4（部分）

---

### Step 4c.2: base_url 格式校验

- [ ] 在 `backend/hub/routers/admin/downstreams.py` 的 Pydantic schema 中为 `base_url` 添加 `HttpUrl` 类型或自定义 URL 校验
- [ ] 更新测试

**涉及文件:**
- 修改: `backend/hub/routers/admin/downstreams.py`
- 修改: `backend/tests/test_admin_downstreams.py`

**验收标准:**
- [ ] "not-a-url" 返回 422
- [ ] 合法 URL 正常通过

**关联缺陷:** SEC-4（部分）

---

### Step 4c.3: system_config 类型校验修复

- [ ] 在 `backend/hub/routers/admin/system_config.py:54` 中，修复 bool 可混入 float 的类型检查逻辑
- [ ] 在 float 分支前显式拒绝 `bool` 类型值
- [ ] 更新 `backend/tests/test_admin_system_config.py`

**涉及文件:**
- 修改: `backend/hub/routers/admin/system_config.py`
- 修改: `backend/tests/test_admin_system_config.py`

**验收标准:**
- [ ] `True` / `False` 不能作为 float 值存入
- [ ] 正常 int / float / str / bool 各类型正常工作

**关联缺陷:** MISC-3

---

### Step 4c.4: Dashboard 0 任务返回 null

- [ ] 将 `backend/hub/routers/admin/dashboard.py:66` 的 `else 100.0` 改为 `else None`
- [ ] 前端 `DashboardView.vue` 处理 `null` 时显示"暂无数据"而非 100%
- [ ] 更新测试

**涉及文件:**
- 修改: `backend/hub/routers/admin/dashboard.py`
- 修改: `frontend/src/views/admin/DashboardView.vue`

**验收标准:**
- [ ] 0 任务时 API 返回 `success_rate: null`
- [ ] 前端显示"暂无数据"而非 100%

**关联缺陷:** MISC-2

---

### Step 4c.5: BindingService(erp_adapter=None) 重构

- [ ] 将 `backend/hub/services/binding_service.py` 的 `confirm_final` 提取为独立函数（不依赖 `self.erp`）
- [ ] `internal_callbacks.py:50` 直接调用独立函数而非创建残缺的 Service 实例
- [ ] 更新测试

**涉及文件:**
- 修改: `backend/hub/services/binding_service.py`
- 修改: `backend/hub/routers/internal_callbacks.py`

**验收标准:**
- [ ] `BindingService` 构造函数强制要求 `erp_adapter`（无默认 None）
- [ ] confirm-final 调用路径不受影响

**关联缺陷:** MISC-4

---

### Step 4c.6: BFS 改用 deque

- [ ] 将 `backend/hub/cron/dingtalk_user_client.py:104` 的 `queue: list[int]` 改为 `collections.deque`
- [ ] `queue.pop(0)` 改为 `queue.popleft()`
- [ ] 更新测试

**涉及文件:**
- 修改: `backend/hub/cron/dingtalk_user_client.py`

**验收标准:**
- [ ] BFS 遍历结果不变
- [ ] 大型组织树遍历性能改善（可测试 1000 部门的遍历时间）

**关联缺陷:** MISC-5

---

### Phase 4c Gate: 校验验收

- [ ] `pytest backend/tests/ -x` 全量通过
- [ ] 非法输入均返回 422
- [ ] `npm run build` 通过

---

## Phase 5: 回归与加固

**目标:** 全量回归、端到端验证、依赖清理、遗留小项收尾。  
**时间:** Day 10（~6h）  
**前置:** Phase 4c Gate 通过。

### Step 5.1: 全量回归测试

- [ ] `pytest backend/tests/ -v --tb=short` 全量通过
- [ ] `npm run build` 通过
- [ ] 确认新增测试数量（目标：从 57 个测试文件增长到 65+）

---

### Step 5.2: 端到端验证

- [ ] `docker compose down -v && docker compose up -d` 全新启动
- [ ] 走完 Setup Wizard 6 步
- [ ] 完成钉钉绑定流程
- [ ] 发送业务查询消息，验证 AI 意图解析 → ERP 查询 → 钉钉回复
- [ ] 验证 SSE 实时监控
- [ ] 验证 Dashboard 数据展示

---

### Step 5.3: 并发压力测试

- [ ] 模拟 10 并发绑定请求
- [ ] 模拟 20 并发查询请求
- [ ] 验证无竞态、无数据不一致、无 500 错误

---

### Step 5.4: 依赖清理

- [ ] 从 `pyproject.toml` 移除 `structlog` 依赖（MISC-7）
- [ ] 确认无 import 引用残留
- [ ] 迁移 `passlib` → `bcrypt`（MISC-6）：新密码用 bcrypt，登录时兼容验证旧 passlib 哈希并自动 re-hash

**涉及文件:**
- 修改: `backend/pyproject.toml`
- 修改: `backend/hub/auth/` 相关文件

**验收标准:**
- [ ] `pip install -e .` 不含 structlog
- [ ] 现有用户密码仍可登录（兼容期）
- [ ] 新注册/重置密码使用 bcrypt 哈希

**关联缺陷:** MISC-6, MISC-7

---

### Step 5.5: ERP Session 缓存驱逐

- [ ] 在 `backend/hub/auth/erp_session.py` 的 `_cache` 中添加 LRU 驱逐策略（上限 1000 条）
- [ ] 或使用 Python 标准库 `functools.lru_cache` 的思路实现（async 兼容）
- [ ] 更新测试

**涉及文件:**
- 修改: `backend/hub/auth/erp_session.py`
- 修改: `backend/tests/test_erp_session_auth.py`

**验收标准:**
- [ ] 缓存上限 1000 条
- [ ] 超过上限时淘汰最旧条目
- [ ] 过期条目不返回

**关联缺陷:** REL-7

---

### Step 5.6: CronScheduler 同小时多任务触发

- [ ] 在 `backend/hub/cron/scheduler.py:69` 中，将 `next_runs[0]` 改为取所有与最近时间相同的任务，依次执行
- [ ] 更新 `backend/tests/test_cron_scheduler.py`

**涉及文件:**
- 修改: `backend/hub/cron/scheduler.py`

**验收标准:**
- [ ] 注册两个同一小时的任务，两个都触发
- [ ] 单任务场景不受影响

**关联缺陷:** REL-6

---

### Step 5.7: 钉钉连接异常 state_holder 清理

- [ ] 在 `backend/hub/lifecycle/dingtalk_connect.py:174` 的 except 块中，清理 `state_holder["adapter"]` 为 None
- [ ] 更新 `backend/tests/test_dingtalk_connect.py`

**涉及文件:**
- 修改: `backend/hub/lifecycle/dingtalk_connect.py`

**验收标准:**
- [ ] 连接失败后健康检查返回"未连接"而非"已连接"

**关联缺陷:** REL-4

---

### Step 5.8: 审计日志敏感字段过滤 + PII 脱敏补邮箱

- [ ] 在 `backend/hub/routers/admin/system_config.py` 的审计写入前，对 `alert_receivers` 等字段进行脱敏（如只记录长度或类型）
- [ ] 在 `backend/hub/observability/task_logger.py:28-30` 的 PII 正则中补充邮箱模式 `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}`
- [ ] 更新测试

**涉及文件:**
- 修改: `backend/hub/routers/admin/system_config.py`
- 修改: `backend/hub/observability/task_logger.py`
- 修改: `backend/tests/test_task_logger.py`

**验收标准:**
- [ ] 审计日志不含完整员工 ID 列表
- [ ] 邮箱地址在 SSE 预览中被脱敏

**关联缺陷:** SEC-5, SEC-6

---

### Step 5.9: Setup session TTL 清理

- [ ] 在 `backend/hub/routers/setup.py` 的 `active_setup_sessions` 中，为每个 session 添加时间戳
- [ ] 每次 verify-token 时清理超过 TTL（30 分钟）的 session
- [ ] 更新测试

**涉及文件:**
- 修改: `backend/hub/routers/setup.py`

**验收标准:**
- [ ] 超过 30 分钟的 setup session 被自动清理
- [ ] 字典大小有界

**关联缺陷:** REL-8

---

### Step 5.10: 更新 CODE_REVIEW.md

- [ ] 将所有已修复项标记为 ✅
- [ ] 更新总体评分
- [ ] 记录未修复项（如有）

**涉及文件:**
- 修改: `CODE_REVIEW.md`

---

### Phase 5 Gate: 最终验收

- [ ] `pytest backend/tests/ -v` 全量通过，0 失败
- [ ] `npm run build && npm test` 通过
- [ ] `docker compose up -d` 全部 healthy
- [ ] 端到端流程无阻断
- [ ] 并发测试无数据不一致
- [ ] `ruff check backend/` 无告警
- [ ] 所有文件 ≤ 250 行
- [ ] `CODE_REVIEW.md` 已更新

---

## 风险管控矩阵

| 风险 ID | 风险描述 | 影响阶段 | 概率 | 影响 | 应对策略 |
|---|---|---|---|---|---|
| R1 | Worker 配置热更新引入新竞态 | 2b | 中 | 高 | 配置切换用 `asyncio.Lock` 保证原子；先写测试再写实现 |
| R2 | 熔断器拆分后阈值需调优 | 3 | 低 | 中 | 系统熔断器初始阈值宽松（10/60s），上线后观察一周 |
| R3 | `HubPermission` 加唯一索引需数据迁移 | 2a | 中 | 高 | 迁移脚本先 `SELECT DISTINCT` 检查重复，有则合并后再加索引 |
| R4 | 拆分 `dingtalk_inbound.py` 可能影响 handler 注册 | 3 | 低 | 高 | 拆分后跑全量 handler 测试 + 端到端消息收发 |
| R5 | 前端引入 Vitest 需基建投入 | 4a | 低 | 低 | 先写 3 个种子测试验证基建可用，不追求高覆盖率 |
| R6 | `passlib` → `bcrypt` 迁移需兼容现有哈希 | 5 | 中 | 高 | 登录时先尝试 bcrypt，失败回退 passlib 并自动 re-hash |
| R7 | Docker healthcheck 探针设计不当误重启 | 4b | 中 | 中 | start_period 30s 宽容启动；Worker 用文件心跳而非 HTTP |
| R8 | Phase 间 Gate 不通过导致整体延期 | 全程 | 中 | 高 | 每个 Phase 内按 Step 顺序执行，Step 内快速失败快速修复 |

### 风险应对流程

```
发现问题
    │
    ├─ 可在当前 Phase 内修复 → 立即修，不推进下一个 Step
    │
    ├─ 需回退到上一个 Phase → 标记阻塞原因，回退到上一个 Gate
    │
    └─ 超出本计划范围 → 记录到 CODE_REVIEW.md "遗留项"，不阻塞当前进度
```

---

## 交付标准总表

| 维度 | 标准 | 验证方式 |
|---|---|---|
| 测试 | 后端 65+ 测试文件全部通过，前端 3+ 测试通过 | `pytest` / `npm test` |
| 构建 | 前后端 build 无错误 | `npm run build` / `python -c "import main"` |
| 安全 | 登录限流、Cookie 安全、异常脱敏 | 手动 + 自动化测试 |
| 性能 | Dashboard ≤ 3 查询，权限 ≤ 2 查询 | Tortoise query log |
| 并发 | 绑定/查询并发无竞态 | `asyncio.gather` 测试 |
| Docker | 全部 healthy，日志轮转生效 | `docker inspect` |
| 规范 | 所有文件 ≤ 250 行，无 DRY 违反 | `ruff` + `rg` |
| 文档 | `CODE_REVIEW.md` 已更新 | 人工确认 |

---

## 资源配置

| 角色 | 工作量 | 说明 |
|---|---|---|
| 后端开发 | ~55h（Day 1-7 + Day 9-10） | 所有后端修复 + 测试编写 |
| 前端开发 | ~12h（Day 8-9） | 前端修复 + Vitest 基建 |
| 运维/Docker | ~4h（Day 8） | Docker 配置 + 健康检查 |
| 代码审查 | ~6h（每个 Phase Gate） | 每个 Gate 审查 30-60 分钟 |
| **合计** | **~66h / 10 个工作日** | 单人全栈场景 |

如 2 人协作（后端 1 人 + 前端 1 人），Phase 4a-4b 可与 Phase 2-3 并行，总工期可压缩到 **7 个工作日**。
