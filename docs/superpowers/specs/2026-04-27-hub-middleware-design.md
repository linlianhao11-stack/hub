# HUB 数据中台设计文档（C 阶段）

**版本**：v1.0
**日期**：2026-04-27
**状态**：设计待审

---

## 0. 文档说明

本文档是 HUB 数据中台 **C 阶段（中台骨架 + 钉钉机器人最小查询闭环）** 的设计规范。后续 B 阶段（合同生成）、D 阶段（凭证自动化）将各自有独立 spec，本文档仅为它们预留接口。

阅读重点：
- 第 1-3 节：背景、定位、范围
- 第 4-6 节：架构、抽象接口、数据模型
- 第 7-9 节：身份与权限、绑定流程、对话监控
- 第 10-11 节：钉钉接入、ERP 接入
- 第 12 节：HUB Web 后台
- 第 13-14 节：可靠性、安全
- 第 15 节：ERP 改动清单 + 零回归约束
- 第 16 节：部署与初始化向导
- 第 17 节：C 阶段验收标准（Definition of Done）
- 第 18 节：可扩展性设计（未来加新下游/渠道/use case 怎么做）
- 第 19 节：待确认事项（P1/P2，用户 review 阶段决定）
- 第 20 节：后续阶段路线图

---

## 1. 背景与定位

### 1.1 起源

ERP-4 项目目前承担中小贸易/零售企业的核心业务系统，但日常交互入口有效率瓶颈：
- 业务员需要查报价、查库存时要登 ERP 网页
- 财务凭证依赖手工录入（钉钉报销审批通过后会计要手动建凭证）
- 销售合同生成需要业务员复制粘贴产品信息再加工

期望通过钉钉机器人作为"前端"降低使用门槛，并把 ERP 的能力以更高频、更自然的形态暴露给一线员工。

### 1.2 项目定位

HUB 是**"上接多渠道、下连多业务系统"的端口/适配器型业务中台**：

- **接入端**（Channel）：第一天接钉钉一个渠道，未来可加企业微信、Web、邮件等
- **下游端**（Downstream）：第一天接 ERP-4 一个下游系统，未来可加 CRM、OA、其他 ERP
- **能力端**（Capability）：第一天接 DeepSeek 一个 AI 能力，未来可换 Claude 或其他厂商

HUB 不是 ERP 的简单代理，而是一个独立的业务编排层，承担：
- 多渠道接入与统一身份（钉钉 userid → HUB user → 各下游系统的 user）
- 业务用例编排（一个用户请求可能触发多次跨系统调用）
- 任务编排与可靠性保障（队列、重试、审计）
- 可观测性与对话监控

### 1.3 与 ERP 的关系

HUB 与 ERP 是两个**独立部署、独立演进**的系统：
- 通信单向：HUB 主动调 ERP HTTP API；ERP 不感知 HUB 存在
- 代码强物理隔离：ERP 任何代码不允许 import / 调用 HUB
- HUB 故障不影响 ERP；ERP 故障 HUB 自动降级

---

## 2. 范围与阶段路线图

### 2.1 整体三阶段

| 阶段 | 内容 | 估时 | 状态 |
|---|---|---|---|
| **C 阶段** | 中台骨架 + 钉钉机器人最小查询闭环（查商品、查客户历史价、绑定流程） | 20-25 天 | 本文档覆盖 |
| **B 阶段** | 钉钉机器人合同生成（AI 多轮对话 + 模板化 Excel + 用户审核） | 10-12 天 | 单独 spec |
| **D 阶段** | 钉钉报销/付款审批流 → ERP 凭证自动生成 | 12-15 天 | 单独 spec |

### 2.2 C 阶段范围

**包含**：
- HUB 仓库初始化、4 容器部署、初始化向导
- 端口/适配器架构、6 个核心抽象接口
- 钉钉 Stream 模式接入（dingtalk-stream Python SDK）
- 用户绑定流程（绑定码双向确认 + 防误绑二次确认）
- 模型 Y 用户身份代理 + 单 ApiKey + scopes
- HUB 自建 RBAC 权限系统（7 张表，6 个预设角色）
- 完整 HUB Web 后台（5 个核心页 + 3 个对话监控页 + 4 个配置页）
- 钉钉机器人最小查询闭环（"查 SKU + 客户" → 模糊匹配 + 历史价 + 系统价）
- AI fallback（规则解析优先，AI 兜底，低置信度强制人工确认）
- 任务编排（Redis Streams 队列 + 消费组 + ACK + 死信 + 自动重试）
- 错误分层处理 + 失败告警
- ERP 5 项改动 + 零回归约束
- 加密 secret 管理（业务 secret 走 Web UI，基础 secret 走 .env）
- PII 分级存储（元数据 365 天，payload 30 天加密 + 脱敏 + 权限隔离）

**明确不包含（YAGNI 防蔓延）**：
- ❌ 合同生成（B 阶段）
- ❌ 凭证自动化（D 阶段）
- ❌ 多渠道接入（仅钉钉，企微/Web/飞书后续）
- ❌ ES / 拼音模糊匹配增强（用 ERP 现有关键词 + HUB MatchResolver 兜底）
- ❌ HUB 跨实例高可用（C 阶段单实例够用）
- ❌ Prometheus / Grafana 接入（先用日志 + 健康检查）
- ❌ Web 后台聚合看板与统计图表（B 阶段加）
- ❌ 自定义角色编辑器（C 阶段只支持选预设角色，B 阶段加自定义）

---

## 3. 项目元数据

| 项 | 值 |
|---|---|
| 项目代号 | HUB |
| 仓库路径（建议） | `/Users/lin/Desktop/hub`（与 ERP-4 平级） |
| 主语言 | Python 3.11+ |
| Web 框架 | FastAPI（与 ERP 一致） |
| ORM | Tortoise（与 ERP 一致），迁移工具 aerich |
| 队列 | Redis Streams（消费组 + ACK + 死信） |
| 数据库 | PostgreSQL 16（与 ERP 一致，但独立实例） |
| 缓存 | Redis 7（含 AOF 持久化以防队列消息丢失） |
| 前端框架 | Vue 3 + Vite 7 + Tailwind CSS 4 + Pinia 3 + Vue Router 4（与 ERP 一致） |
| 前端组件库 | 起步从 ERP 复制，独立维护，后续按 HUB 需求迭代 |
| 钉钉 SDK | dingtalk-stream（Python 官方） |
| AI 默认厂商 | DeepSeek（沿用 ERP），可切 Claude（出境合规需独立评估） |
| 时区 | Asia/Shanghai（统一） |
| 默认网关端口 | 8091（与 ERP 8090 错开） |

---

## 4. 整体架构

### 4.1 架构图

```
            ┌──────────────────────────────────────────────────┐
            │            接入端 ChannelAdapter                  │
            │  ┌──────────────┐  ┌──────────────┐ ┌──────────┐ │
            │  │ DingTalk ✅  │  │企微（未来）🔌 │ │Web (未来)│ │
            │  │ Stream 模式  │  │              │ │ 🔌       │ │
            │  └──────┬───────┘  └──────────────┘ └──────────┘ │
            └─────────┼────────────────────────────────────────┘
                      │ 统一 InboundMessage
            ┌─────────▼────────────────────────────────────────┐
            │               Gateway 服务（FastAPI）             │
            │  接 Stream / 暴露 HUB 自有 API（仅内网）           │
            │  路由 = /webhook/* + /hub/v1/* + /setup/*         │
            └─────────┬────────────────────────────────────────┘
                      │ Redis Streams（持久化任务队列）
            ┌─────────▼────────────────────────────────────────┐
            │               Worker 服务（FastAPI 进程）         │
            │  消费队列 → 调核心域 → 处理结果 → 回写 Channel     │
            └─────────┬────────────────────────────────────────┘
                      │
            ┌─────────▼────────────────────────────────────────┐
            │               核心域（业务逻辑）                  │
            │  IntentParser → TaskRunner → UseCase 编排         │
            │  ContractDraft / SlotFiller / PricingStrategy     │
            │  MatchResolver / ErrorClassifier / AuditLogger    │
            └─────┬─────────────────────────────────────┬──────┘
                  │                                     │
       ┌──────────▼──────────┐               ┌──────────▼──────────┐
       │ DownstreamAdapter   │               │ CapabilityProvider  │
       │ ┌─────────────────┐ │               │ ┌─────────────────┐ │
       │ │ Erp4 ✅          │ │               │ │ DeepSeek ✅      │ │
       │ │ CRM 🔌           │ │               │ │ Claude 🔌        │ │
       │ │ OA  🔌           │ │               │ │ OCR  🔌          │ │
       │ └─────────────────┘ │               │ └─────────────────┘ │
       └─────────────────────┘               └─────────────────────┘
                  │
            ┌─────▼─────────┐
            │ HUB Postgres  │  ← 只存中台自己的事
            └───────────────┘

✅ = C 阶段实际造的    🔌 = 仅定义接口、第一天不实现的扩展位
```

### 4.2 部署形态

**部署位置**：与 ERP 同机部署（节省资源，跟 ERP-4 现有部署保持一致）。

**容器管理**：通过 **OrbStack**（与 ERP-4 团队习惯一致），启动前 `orb start`。

**Docker Compose 4 容器**：

| 容器 | 进程 | 端口 | 是否暴露 |
|---|---|---|---|
| `gateway` | FastAPI（含钉钉 Stream 客户端 + HUB 自有 API + Web 后台静态） | 8091 | 仅内网（不暴露公网，靠 Stream 连出钉钉） |
| `worker` | FastAPI 进程（消费队列） | - | 不暴露 |
| `postgres` | PostgreSQL 16 | 5432 | 不暴露（容器间通信） |
| `redis` | Redis 7 + AOF | 6379 | 不暴露（容器间通信） |

**关键：HUB 不暴露任何公网入口**。钉钉走 Stream 反向连接（HUB 主动连出），HUB Web 后台仅内网访问。

#### 4.2.1 同机部署的隔离配套（必须）

同机部署节省资源，但要保证"HUB 故障不影响 ERP"的目标，下面 5 条隔离措施**必须配套实施**：

| # | 措施 | 目的 |
|---|---|---|
| 1 | **独立 Postgres 实例**（HUB 自己的 postgres 容器，不共用 ERP 数据库） | 数据完全隔离；HUB 数据库爆掉不波及 ERP |
| 2 | **独立 Docker network**（HUB compose 用 `hub-net` 网络，跟 ERP 网络物理隔离） | 容器间通信收敛在自己的网络；防止意外串扰 |
| 3 | **Docker 资源限制**（在 docker-compose.yml 的 `deploy.resources.limits` 中限定 HUB 各容器的 cpus / memory） | HUB 队列高峰不抢 ERP 的 CPU/RAM；任何容器 OOM 不会拖垮宿主机 |
| 4 | **端口错开**（HUB Gateway 8091 vs ERP 8090；HUB Postgres / Redis 在容器内不映射到宿主机端口）| 避免端口冲突 |
| 5 | **宿主机监控**（CPU / RAM / Disk IO 双向观测，任一容器接近资源上限触发告警） | 提前发现资源争抢，避免传染故障 |

**资源限额建议（C 阶段起步值，按观测调整）**：

| 容器 | CPU 上限 | 内存上限 |
|---|---|---|
| gateway | 1.0 core | 512MB |
| worker | 1.0 core | 1GB（payload 加解密 + AI 调用峰值） |
| postgres | 1.0 core | 1GB |
| redis | 0.5 core | 512MB |
| **HUB 合计上限** | **3.5 cores** | **~3GB** |

留给 ERP 的资源 = 宿主机总资源 − HUB 上限 − 系统 0.5 core / 1GB 缓冲。
上线前必须确认宿主机资源 ≥ ERP 当前用量 + HUB 合计上限 + 缓冲，否则需要扩容硬件再部署。

### 4.3 端口/适配器三类抽象

| 抽象 | 接口 | 职责 | 第一天实现 |
|---|---|---|---|
| 接入端 | `ChannelAdapter` | 把渠道协议转成统一的 InboundMessage / OutboundMessage | `DingTalkStreamAdapter` |
| 下游端 | `DownstreamAdapter` | 调下游系统 API（鉴权、重试、错误归类） | `Erp4Adapter` |
| 能力端 | `CapabilityProvider` | 通用能力（AI 解析、OCR 等） | `DeepSeekProvider` |

---

## 5. 核心抽象接口（端口/策略）

C 阶段必须定义的 **6 个核心端口/策略接口**。每个第一天只需要一个最简实现，但接口必须定义清楚，未来加第二个实现时业务代码 0 改动。

### 5.1 ChannelAdapter（接入端）

```python
class ChannelAdapter(Protocol):
    channel_type: str  # 'dingtalk' / 'wecom' / 'web' / ...

    async def start(self) -> None:
        """启动渠道（如建立 Stream 连接）"""

    async def stop(self) -> None:
        """优雅关闭"""

    async def send_message(self, channel_userid: str, message: OutboundMessage) -> None:
        """主动 push 消息到指定用户"""

    def on_message(self, handler: Callable[[InboundMessage], Awaitable[None]]) -> None:
        """注册消息回调"""
```

**InboundMessage** 标准字段：channel_type, channel_userid, conversation_id, content, content_type, timestamp, raw_payload。
**OutboundMessage**：text / markdown / actioncard / 等模板化卡片格式。

### 5.2 DownstreamAdapter（下游端）

```python
class DownstreamAdapter(Protocol):
    downstream_type: str  # 'erp' / 'crm' / 'oa' / ...

    async def health_check(self) -> bool: ...

    # 业务接口（每个 adapter 自己定义具体方法）
    # 共性约束：所有"代用户"调用必须接受 acting_as_user_id 参数
```

`Erp4Adapter` 的具体方法包括：`login(username, password)`, `get_me(jwt)`, `search_products(q, acting_as)`, `search_customers(q, acting_as)`, `get_product_customer_prices(product_id, customer_id, limit, acting_as)` 等。

**强约束**：`Erp4Adapter` 任何业务调用必须带 `acting_as_user_id`，否则抛 `RuntimeError`。单元测试覆盖。

### 5.3 CapabilityProvider（能力端）

```python
class CapabilityProvider(Protocol):
    capability_type: str  # 'ai' / 'ocr' / ...

class AICapability(CapabilityProvider):
    async def parse_intent(self, text: str, schema: dict) -> dict:
        """根据 schema 解析自然语言为结构化字段"""

    async def chat(self, messages: list[dict], **kw) -> str:
        """通用对话"""
```

第一天实现：`DeepSeekProvider`。

### 5.4 IntentParser（意图解析）

```python
class IntentParser(Protocol):
    async def parse(self, text: str, context: dict) -> ParsedIntent:
        """返回 intent 类型 + 字段 + confidence"""
```

实现链：`RuleParser → LLMParser`（规则匹配命中即返回，否则丢给 AI）。

低置信度（< 0.7）必须走"用户确认卡片"，不直接执行。

### 5.5 TaskRunner（任务异步执行）

```python
class TaskRunner(Protocol):
    async def submit(self, task_type: str, payload: dict) -> str:
        """投递任务到队列，返回 task_id"""

    async def get_status(self, task_id: str) -> TaskStatus: ...
```

第一天实现：`RedisStreamsRunner`（投递到 Redis Streams 消费组）。

### 5.6 PricingStrategy（价格策略）

```python
class PricingStrategy(Protocol):
    async def get_price(self, product_id: int, customer_id: int | None,
                        acting_as: int) -> PriceInfo:
        """返回价格信息（含来源标注）"""
```

第一天实现：`DefaultPricingStrategy` —— 客户最近 N 次成交价 → 系统零售价 fallback。

### 5.7 辅助基础设施（不算端口，是工具）

- `MatchResolver`：模糊匹配的统一入口（关键词 → 候选列表 → 多命中让用户选）。第一天实现走 ERP 现有关键词查询，未来可换 ES/拼音。
- `ErrorClassifier`：把异常分类成 UserError / SystemError / ConflictError / PermissionError / TimeoutError 五类，每类独立处理策略。
- `AuditLogger`：HUB 内部审计日志统一入口。

---

## 6. 数据模型（HUB Postgres Schema）

### 6.1 总览（约 18 张表）

按职责分组：

| 组 | 表 | 用途 |
|---|---|---|
| 身份 | `hub_user` / `channel_user_binding` / `downstream_identity` | 多渠道身份 + 多下游身份映射 |
| 权限 | `hub_role` / `hub_permission` / `hub_role_permission` / `hub_user_role` | RBAC 模型 |
| 配置 | `downstream_system` / `channel_app` / `ai_provider` / `system_config` | 业务 secret + 系统配置 |
| 任务 | `task_log` / `task_payload`（加密存储） | 任务流水（元数据 365 天 + payload 30 天） |
| 审计 | `audit_log` / `meta_audit_log` | 操作审计 + 看 payload 留痕 |
| 绑定 | `bootstrap_token` / `binding_code_local`（如有） | 一次性 token 与本地绑定状态 |
| 缓存 | `erp_user_state_cache` | hub_user 对应 ERP 是否启用的缓存（10 分钟 TTL） |

### 6.2 关键表 schema 草图

#### hub_user
```
id (PK)
display_name      -- 中文显示名
status            -- active / suspended（管理员手动停用）/ revoked
created_at
updated_at
```

#### channel_user_binding
```
id (PK)
hub_user_id (FK -> hub_user)
channel_type      -- 'dingtalk' / 'wecom' / ...
channel_userid    -- 钉钉 userid 等
display_meta      -- JSON：姓名、手机尾号等渠道侧元数据（防误绑确认页用）
status            -- active / revoked
bound_at
revoked_at
revoked_reason
UNIQUE (channel_type, channel_userid)
```

#### downstream_identity
```
id (PK)
hub_user_id (FK)
downstream_type   -- 'erp' / 'crm' / ...
downstream_user_id  -- ERP user.id 等
created_at
UNIQUE (hub_user_id, downstream_type)
```

#### hub_role
```
id (PK)
code              -- 'platform_admin' 等内部 ID
name              -- 'HUB 系统管理员' UI 显示名
description       -- 中文说明
is_builtin        -- 内置不可删
```

#### hub_permission
```
id (PK)
code              -- 'platform.tasks.read' 三段式
resource          -- 'platform' / 'downstream' / 'usecase' / 'channel'
sub_resource
action            -- 'read' / 'write' / 'use' / 'admin'
name              -- '查看任务记录' UI 显示名
description       -- 中文说明
```

#### task_log（元数据，长保留）
```
id (PK)
task_id           -- UUID
task_type         -- 'query_product' / 'binding' / ...
channel_type
channel_userid
hub_user_id (nullable，绑定前为 null)
status            -- queued / running / success / failed_user / failed_system_retrying / failed_system_final
intent_parser     -- 'rule' / 'llm'
intent_confidence
created_at
finished_at
duration_ms
error_classification  -- UserError / SystemError / ConflictError / ...
error_summary     -- 简短错误描述（不含敏感信息）
retry_count
保留期：365 天
```

#### task_payload（敏感数据，短保留 + 加密）
```
id (PK)
task_id (FK -> task_log)
encrypted_request  -- AES-256-GCM 密文（含原始用户消息、解析结果等）
encrypted_erp_calls  -- ERP 调用入参/出参列表，加密
encrypted_response   -- 最终回复给用户的内容
created_at
expires_at         -- 默认 created_at + 30 天
保留期：30 天，过期自动 cron 删除
```

#### audit_log（普通审计）
```
id (PK)
who_hub_user_id
action            -- 'create_apikey' / 'unbind_user' / 'change_role' / ...
target_type
target_id
detail (JSON)
ip
user_agent
created_at
保留期：365 天
```

#### meta_audit_log（看 payload 留痕）
```
id (PK)
who_hub_user_id
viewed_task_id
viewed_at
ip
保留期：365 天，仅 platform.audit.system_read 权限可看
```

#### downstream_system（下游系统配置）
```
id (PK)
downstream_type   -- 'erp' / 'crm'
name              -- '生产 ERP' UI 显示名
base_url
encrypted_apikey  -- AES-256-GCM 加密
apikey_scopes     -- 缓存 ERP 那边返回的 scope 列表（仅展示用）
status            -- active / disabled
created_at
updated_at
```

#### channel_app（渠道凭证）
```
id (PK)
channel_type      -- 'dingtalk'
name              -- '钉钉 - 公司主企业'
encrypted_app_key
encrypted_app_secret
robot_id          -- 钉钉机器人 ID
status
created_at
```

#### ai_provider
```
id (PK)
provider_type     -- 'deepseek' / 'claude'
name
encrypted_api_key
model
config (JSON)
status
```

#### system_config
```
key (PK)          -- 'alert_receivers' / 'task_payload_ttl_days' / ...
value (JSON)
description
updated_at
updated_by
```

### 6.3 加密细节

- 所有 `encrypted_*` 字段：AES-256-GCM，存 nonce + ciphertext + auth_tag
- 派生密钥：HUB_MASTER_KEY → HKDF → 不同用途子密钥（业务 secret 用一把，task_payload 用另一把）
- 启动时一次性解密 secret 加载到内存缓存，运行时不重复 decrypt

---

## 7. 身份与权限模型

### 7.1 三层身份映射

```
┌──────────────────┐      ┌──────────────────────────┐
│   hub_user       │◄─┬──┤ channel_user_binding     │
│  (HUB 内部用户)   │  │   │ 钉钉/企微/Web 等渠道身份  │
└──────────────────┘  │   └──────────────────────────┘
                      │   ┌──────────────────────────┐
                      └──┤ downstream_identity      │
                          │ ERP/CRM/OA 等下游身份     │
                          └──────────────────────────┘
```

每个 hub_user 可以：
- 绑定多个渠道身份（一份钉钉、一份未来企微）
- 对应多个下游系统的本地账号（在 ERP 是 user_id=42、在未来 CRM 是 user_id=89）

### 7.2 RBAC 模型

`hub_user → hub_user_role → hub_role → hub_role_permission → hub_permission`，标准多对多。

### 7.3 6 个预设角色（中文 UI 名）

| 内部 code | UI 显示名 | 说明 |
|---|---|---|
| `platform_admin` | HUB 系统管理员 | 拥有所有功能权限，可以管理用户、角色、系统配置 |
| `platform_ops` | 运维人员 | 可以查看任务记录、调整系统开关、配置告警接收人，但不能管理用户 |
| `platform_viewer` | 只读观察员 | 只能查看任务记录和操作日志，不能做任何修改 |
| `bot_user_basic` | 机器人 - 基础查询 | 可以在钉钉里让机器人查商品、查客户、查报价 |
| `bot_user_sales` | 机器人 - 销售（B 阶段启用） | 在"基础查询"之上，还可以让机器人生成销售合同 |
| `bot_user_finance` | 机器人 - 财务（D 阶段启用） | 在"基础查询"之上，还可以让机器人自动生成报销/付款凭证 |

### 7.4 权限码命名规范（三段式）

`<resource>.<sub_resource>.<action>`

| 类别 | 示例 code | UI 名称 | UI 说明 |
|---|---|---|---|
| 平台 | `platform.tasks.read` | 查看任务记录 | 可以在后台看到每次机器人调用的详细执行记录 |
|  | `platform.flags.write` | 调整功能开关 | 可以打开或关闭系统的某些功能模块 |
|  | `platform.users.write` | 管理后台用户 | 可以在后台添加用户、分配角色 |
|  | `platform.alerts.write` | 配置告警接收人 | 可以设置出问题时通知谁 |
|  | `platform.audit.read` | 查看操作日志 | 可以看到管理员们的操作历史 |
|  | `platform.audit.system_read` | 查看系统级审计 | 可以看到"谁查看了用户对话"等敏感审计 |
|  | `platform.conversation.monitor` | 对话监控 | 可以查看用户与机器人的实时对话和历史对话内容 |
|  | `platform.apikeys.write` | 管理 API 密钥 | 可以创建、吊销、查看下游系统对接密钥 |
| 下游 | `downstream.erp.use` | 使用 ERP 数据 | 允许机器人访问 ERP 系统的客户、商品、订单等数据 |
| 用例 | `usecase.query_product.use` | 商品查询 | 允许在钉钉用机器人查询商品信息 |
|  | `usecase.query_customer_history.use` | 客户历史价查询 | 允许查询某客户的历史成交价 |
|  | `usecase.generate_contract.use` | 合同生成（B 阶段） | 允许在钉钉用机器人自动生成销售合同 |
|  | `usecase.create_voucher.use` | 凭证生成（D 阶段） | 允许审批通过的报销/付款自动生成会计凭证 |
| 渠道 | `channel.dingtalk.use` | 使用钉钉接入 | 允许通过钉钉机器人交互 |

**UI 严格不展示 code 字段**，永远显示中文 name + description。

### 7.5 4 层权限校验链路

```
[1] 渠道身份识别
    钉钉消息 → channel_user_binding(dingtalk, manager4521) → hub_user_id=100
    未找到 → 触发绑定流程

[2] HUB 平台权限检查（"第一层"）
    检查 hub_user 100 是否有 channel.dingtalk.use
    检查 hub_user 100 是否有 usecase.query_product.use
    检查 hub_user 100 是否有 downstream.erp.use
    任一缺失 → 拒绝并友好提示

[3] 下游身份解析
    downstream_identity(hub_user 100, 'erp') → erp_user_id=42
    未找到 → "你的 HUB 账号未关联 ERP 用户，请联系管理员"

[4] 下游业务权限检查（"第二层"，由 ERP 自己执行）
    HUB 调 ERP，带 X-API-Key + X-Acting-As-User-Id: 42
    ERP 加载 user 42 的 permissions（销售/财务/会计 等业务权限）
    走 ERP 现有 require_permission 流程
    返回数据 / 403
```

[1]+[2]+[3] 是"HUB 层"，[4] 是"下游层"。两层都通过才返回数据给用户。

### 7.6 第一个 platform_admin Bootstrapping

通过初始化向导步骤 3 创建：
1. 用户在向导填 ERP 账号 + 密码
2. HUB 用步骤 2 配置的 ERP 连接 + 系统级 ApiKey scope 调 ERP `/auth/login` 验证
3. 通过 → 调 ERP `/me` 拿 erp_user_id
4. 创建 hub_user + channel_user_binding（如果同时填了钉钉 userid，可选）+ downstream_identity（关联 ERP）+ hub_user_role（绑 platform_admin）

---

## 8. 绑定流程

### 8.1 首次绑定（绑定码双向确认）

完整流程：

```
1. 用户钉钉单聊机器人发：/绑定 张三
2. HUB 校验：
   - 在 ERP 查 username='张三' 是否存在（用 system scope ApiKey 调白名单接口）
   - 不存在 → "未找到 ERP 用户'张三'，请检查输入"
3. HUB 在自己数据库生成 6 位绑定码 + 5 分钟过期
   生成时记录：dingtalk_userid + erp_username
4. HUB 钉钉回复：
   "请在 5 分钟内登录 ERP，进入'个人中心 - 钉钉绑定'，
    输入绑定码 742815 完成确认"
5. 用户登 ERP → 找到绑定页面 → 输入 742815 → 提交
6. ERP 调 HUB 接口 POST /internal/binding/confirm-token
   入参：token, erp_user_id, erp_username, erp_display_name
7. HUB 校验：
   - token 存在且未过期
   - erp_user_id 与生成时记录的 username 匹配
8. HUB 弹"二次确认"信息（通过响应回 ERP）：
   "你正在把钉钉账号 manager4521（手机尾号 1234）
    绑定到 ERP 账号「张三 - 销售部」。"
9. ERP 前端显示二次确认对话框 → 用户点"确认绑定"
10. ERP 再次调 HUB POST /internal/binding/confirm-final
11. HUB 写入：
    - hub_user（如尚不存在）
    - channel_user_binding(dingtalk, dingtalk_userid, hub_user_id, status='active')
    - downstream_identity(hub_user, 'erp', erp_user_id)
    - 默认绑定 bot_user_basic 角色
12. HUB push 钉钉确认消息："绑定成功，欢迎 张三！发送'帮助'查看可用功能"
13. HUB push 隐私告知（首次绑定）："为了功能改进和问题排查，你跟我的对话内容会
    被记录 30 天后自动删除，仅授权管理员可查看。"
```

**关键安全点**：
- 第 4 步钉钉里只显示 token，不显示 ERP 用户密码
- 第 8 步二次确认环节防误绑（双向显示信息让用户检查）
- 第 11 步绑定写入由 ERP 反向触发（HUB 进程不能凭空写 binding，防止内部攻击）

### 8.2 解绑（自助 + 后台双通道）

**自助**：用户钉钉发 `/解绑` → HUB 校验发起者就是 binding 持有人 → 标记 `status='revoked'` + revoked_reason='self_unbind' + 审计日志 + 回复"已解绑，下次发消息会重新触发绑定流程"

**管理员后台**：HUB 后台用户管理 → 用户详情 → "强制解绑" → 标记 status='revoked' + revoked_reason='admin_force' + 审计日志（记录 admin id）+ 通过钉钉 push 通知该用户"你的 HUB 绑定已被管理员解除"

### 8.3 离职/踢出钉钉自动同步

**A 路径（实时事件订阅）**：
- HUB Stream 订阅钉钉"员工离职"事件
- 收到事件 → 找到对应 channel_user_binding → 标记 revoked + revoked_reason='dingtalk_offboard'
- 通知告警接收人

**C 路径（每日巡检兜底）**：
- 每日凌晨 cron（默认 03:00 Asia/Shanghai）调钉钉"获取企业全员"接口
- 跟 channel_user_binding(dingtalk) 对比
- 钉钉那边已无的 → 标记 revoked + revoked_reason='daily_audit'

A+C 兜底保证最终一致性，A 主路径实时性好，C 防止 Stream 断线期间事件丢失。

### 8.4 ERP 用户禁用同步

HUB 缓存 + TTL：
- hub_user 表加 `erp_active_cache`（bool）+ `erp_active_checked_at`（timestamp）
- 每次钉钉消息进入 → 查缓存：
  - 缓存有效（< 10 分钟）→ 直接用
  - 缓存过期 → 调 ERP `/me` 校验，刷新缓存
- 缓存为 false → 拒绝并回复"你的 ERP 账号已停用，请联系管理员"
- HUB 后台用户管理页加"立即同步状态"按钮（platform.users.write 权限），强制刷新

---

## 9. 对话监控

### 9.1 三个子页面（C 阶段都做）

#### 实时对话流（Live）
- SSE 推流（Server-Sent Events）
- 每个 task 状态变更（queued / running / success / failed）→ Redis Pub/Sub channel `conversation:live` → SSE → 前端
- 前端 EventSource API，新消息插入列表头部
- 鉴权：SSE 连接建立时校验 session + `platform.conversation.monitor` 权限
- UI：见 §12.4 草图

#### 历史对话搜索
- 表格分页
- 筛选维度：时间范围 / hub_user / channel_userid / task_type / status / intent_parser / 关键字搜索（仅元数据，不搜 payload）
- 默认按时间倒序

#### 会话详情页
- 单 task 完整链路：用户消息 → 解析（rule / llm + confidence）→ ERP 调用列表（method, path, 耗时, status_code, 入参/出参）→ AI 调用（model, tokens, 耗时）→ 最终回复
- 时间线形式展示
- 看明文 payload 必须有 `platform.conversation.monitor`（自动）+ 触发 meta_audit_log 记录

### 9.2 对话监控权限

新增权限 `platform.conversation.monitor`：
- 默认给 `platform_admin`
- 不给 `platform_ops` / `platform_viewer`
- 后续可新建"对话观察员"自定义角色单独授权（B 阶段加自定义角色编辑器）

### 9.3 看了就留痕（Meta Audit）

**触发粒度精确定义**：
- **进对话监控列表页**（实时流 / 历史搜索）：仅展示元数据（task_id / 用户 / 类型 / 状态 / 摘要），**不解密 payload，不触发 meta_audit_log**
- **进会话详情页**（展示原始用户消息、ERP 调用入参出参、AI 解析结果等明文 payload）：服务端解密 task_payload 时**触发**写一条 `meta_audit_log`（who / 何时 / 看了哪个 task）

字段：`who_hub_user_id` / `viewed_task_id` / `viewed_at` / `ip`

**审计的审计**：
- 普通 admin 看不到 meta_audit_log
- 需要 `platform.audit.system_read` 权限才能查询
- 这是"谁在监控监控员"的设计

---

## 10. 钉钉接入（Stream 模式）

### 10.1 钉钉企业内部应用注册（管理员手工）

由 HUB admin（钉钉主管理员或子管理员）在 [open.dingtalk.com](https://open.dingtalk.com) 完成：
1. 应用开发 → 企业内部开发 → H5 微应用 → 创建应用
2. 名称："HUB"，Logo / 简介自定，选"企业内部自主开发"
3. 开发配置 → 事件订阅 → 选 **Stream 模式推送** → 验证 Stream 通道 → 保存
4. 拿到 `AppKey` / `AppSecret`（这是后续 HUB 配置中心的输入）
5. 应用能力 → 添加机器人能力 + 单聊
6. 添加测试组织 → 把开发组成员加进去 → 授权
7. （后期发布时）走企业认证

### 10.2 Stream 连接管理

- HUB Gateway 启动时调用 dingtalk-stream Python SDK 建立 WebSocket 反向连接
- 鉴权由 SDK 用 AppKey/AppSecret 自动完成
- 断线指数退避重连（5s → 10s → 30s → 60s → 60s 持续）
- 连接状态写入 health 检查端点 `/hub/v1/health`
- 断线超过 60s 触发告警 push 给 alert_receivers
- 处理失败的事件不 ack，钉钉会重推（at-least-once 语义）

### 10.3 订阅的事件类型

| 事件 | 用途 | C 阶段 |
|---|---|---|
| 机器人收消息（@机器人 / 单聊） | 用户与机器人对话 | ✅ |
| 卡片回调（用户点 ActionCard 按钮） | 多命中选编号、二次确认等 | ✅ |
| 员工离职事件 | 自动作废 binding | ✅ |
| 审批状态变更 | D 阶段（凭证自动化） | ❌ B 阶段后启用 |

### 10.4 配额管理

- Webhook + Stream 共享同一配额池：标准版 5,000 次/月，专业版 50,000 次/月
- HUB 后台暴露调用量统计（按月聚合）
- 接近配额阈值（≥ 80%）→ 告警

---

## 11. ERP 接入（模型 Y + 单 ApiKey + scopes）

### 11.1 鉴权模型

ERP 现有 `auth/dependencies.py:get_current_user()` 改造，新增 ApiKey 分支：

```
请求进入 ERP：
  if Authorization: Bearer xxx (JWT):
      → 走原 JWT 流程（一字不改）
  elif X-API-Key:
      → 走新 ApiKey 鉴权（独立模块 auth/api_key_auth.py）
      → 步骤：
        1. 校验 ApiKey 合法 + 未吊销 + 未过期
        2. 看请求 endpoint 需要哪个 scope:
           - 业务 endpoint → 需要 'act_as_user' scope
           - 白名单系统 endpoint → 需要 'system_calls' scope
        3. ApiKey.scopes 包含所需 scope → 通过；否则 403
        4. endpoint 需要 'act_as_user' → 强制要求 X-Acting-As-User-Id header
        5. 加载目标用户权限作为 current_user，走原有 require_permission
  else:
      → 401（与原行为一致）
```

### 11.2 ApiKey 模型（ERP 新增表）

```
service_account
  id, name, scope_template, is_active, created_at

api_key
  id, service_account_id, key_hash (bcrypt), key_prefix (前 6 位明文)
  name (UI 显示名)
  scopes (JSON 数组，存权限码: ['act_as_user', 'system_calls', ...])
  last_used_at, expires_at, is_revoked, created_at
```

ERP 后台 admin UI（"系统设置 - API 密钥管理"）：
- 创建：填名称 + 多选 scopes（中文显示）+ 有效期 → 生成明文（**只显示一次**）→ admin 复制
- 列表：名称、prefix（如 `hub_a3...`）、scopes、最后使用时间、状态
- 操作：吊销、复制 prefix、查看使用统计

### 11.3 scope 列表（ERP 端定义）

| code | UI 名称 | 说明 |
|---|---|---|
| `act_as_user` | 代理用户调用业务接口 | 必须带 X-Acting-As-User-Id 头，按目标用户权限执行 |
| `system_calls` | 调用系统级白名单接口 | 调绑定码生成、用户存在性查询等不需要"代表谁"的接口 |
| `admin_operations` | 管理员级操作（高危） | 批量数据导出等，C 阶段不开放 |

### 11.4 白名单系统接口（system_calls scope）

| Method + Path | 用途 |
|---|---|
| `POST /api/v1/internal/binding-codes/generate` | HUB 调用生成绑定码 |
| `POST /api/v1/internal/users/exists` | 校验 ERP 用户名存在性 |
| `GET /api/v1/internal/users/{id}/active-state` | 查 user.is_active（HUB 缓存刷新用） |

> 说明：ERP 个人中心绑定页面的"输入绑定码确认"接口由 ERP 前端调 ERP 后端，属 ERP 内部业务，**不在 ApiKey 白名单范围**。HUB 不调用此接口；HUB 只接收 ERP 反向通知（见 §8.1 第 6/10 步）。

### 11.5 业务接口（act_as_user scope）

举例：
| Method + Path | 必带 Acting-As | 说明 |
|---|---|---|
| `POST /api/v1/auth/login` | ❌ 不需要 | 用户登录 |
| `GET /api/v1/auth/me` | ❌ 不需要（已带 JWT） | 获取当前用户 |
| `GET /api/v1/products?q=...` | ✅ 必须 | 商品搜索（按目标用户权限筛选） |
| `GET /api/v1/customers?q=...` | ✅ 必须 | 客户搜索 |
| `GET /api/v1/products/{id}/customer-prices?customer_id=...` | ✅ 必须 | 历史成交价 |

---

## 12. HUB Web 后台

### 12.1 总体形态

- 独立的 Vue 3 SPA，由 Gateway 容器作为静态资源 serve（`/` → index.html）
- 路由：`/` 登录 / `/setup/*` 初始化向导（仅初始化期可访问）/ `/admin/*` 主后台
- 登录方式：复用 ERP 用户系统（HUB 调 ERP `/auth/login` → 拿到 JWT 包装成 HUB session）

### 12.2 路由与页面（C 阶段必含）

#### 初始化向导（仅一次性）
- `/setup/welcome` 系统自检
- `/setup/connect-erp` 注册 ERP 连接
- `/setup/admin` 创建第一个管理员
- `/setup/dingtalk` 注册钉钉应用（可跳过）
- `/setup/ai` 注册 AI 提供商（可跳过）
- `/setup/done` 完成

#### 主后台 - 用户与权限
- `/admin/users` 用户管理：列出 hub_user，可看每个人绑了哪些渠道身份和下游身份
- `/admin/roles` 角色管理：列出 hub_role，可看角色含哪些权限码（C 阶段只读预设）
- `/admin/user-roles` "谁是什么角色"：给 hub_user 加/减角色
- `/admin/account-links` "账号关联"：给 hub_user 加/改 downstream_identity（绑定 ERP user_id 等）
- `/admin/permissions` "功能权限说明"：列出所有 hub_permission（中文名 + 说明）

#### 主后台 - 配置中心
- `/admin/downstreams` 下游系统管理：ERP / 未来 CRM/OA 配置
- `/admin/channels` 渠道管理：钉钉应用配置 + Stream 连接状态
- `/admin/ai-providers` AI 提供商管理：DeepSeek / Claude key 配置 + 调用统计
- `/admin/system-config` 系统设置：告警接收人、TTL、日志保留期

#### 主后台 - 任务与监控
- `/admin/tasks` 任务流水：搜索、筛选、详情
- `/admin/conversation/live` 对话监控 - 实时
- `/admin/conversation/history` 对话监控 - 历史
- `/admin/conversation/{task_id}` 对话监控 - 详情
- `/admin/audit` 操作审计

#### 主后台 - 系统
- `/admin/dashboard` 仪表盘：连接状态、今日任务计数、错误率、配额使用
- `/admin/health` 健康检查页

### 12.3 视觉规范（应用 UI 大白话原则）

- 所有按钮、菜单、字段名、状态、错误提示必须中文大白话
- 严禁在 UI 上展示：permission code、role code、API endpoint 路径、错误堆栈、HTTP 状态码（>=400 时翻译成"请求失败，请重试"等）
- 所有"代码味"标识符仅作为后端内部 ID
- 状态枚举一律中文（"运行中"/"已完成"/"失败"/"等待重试"，不出现 running/success/failed）

### 12.4 实时对话流 UI 草图

```
┌────────────────────────────────────────────────────────┐
│  对话监控 - 实时                          [筛选] [导出]  │
├────────────────────────────────────────────────────────┤
│  ⏺ 实时模式  |  暂停  |  搜索关键字: [____________]    │
├────────────────────────────────────────────────────────┤
│  14:23  manager4521 (李销售)                           │
│         "查 SKU100 给阿里"                              │
│         ▸ 解析: 商品查询 + 客户=阿里巴巴集团              │
│         ▸ ERP: 取商品+客户历史价                         │
│         ▸ 回复: 卡片(零售价 ¥120, 历史价 ¥110)          │
│         ✓ 耗时 380ms  [查看详情]                        │
├────────────────────────────────────────────────────────┤
│  14:22  manager3812 (王销售)                           │
│         "帮我看下阿里那个 sku100 多少钱"                 │
│         ▸ 解析: AI 兜底 (置信度 0.85)                    │
│         ✓ 耗时 1.2s  [查看详情]                         │
└────────────────────────────────────────────────────────┘
```

---

## 13. 可靠性

### 13.1 任务编排（Redis Streams）

- 流名：`hub:tasks:<priority>`（只用一档 default 也够，预留 high/low）
- 消费组：`hub-workers`
- 投递：Gateway 投到 stream，Worker `XREADGROUP` 消费
- ACK 时机：处理成功才 ACK；失败保留在 PEL（Pending Entries List）
- 死信：单条任务超过 max_retries（默认 3）→ 移到 `hub:tasks:dead`，不再消费 + 告警
- 持久化：Redis AOF 必须开（appendonly yes，appendfsync everysec）

### 13.2 重试策略

| 错误类型 | 处理 | 重试 |
|---|---|---|
| UserError（用户输错） | 立即回复用户 + 不重试 | 0 |
| SystemError（ERP 5xx / 网络超时） | 退避重试 | 3 次（30s / 2min / 10min） |
| ConflictError（数据冲突） | 立即回复 + 让用户决策 | 0 |
| PermissionError（权限不够） | 立即回复 + 不重试 | 0 |
| TimeoutError（超时） | 走 SystemError 流程 | 3 次 |

最终失败 → push 告警给 alert_receivers + 钉钉里告诉用户"系统暂时异常，已记录"

### 13.3 钉钉断线处理

- SDK 自动指数退避重连
- 连接状态健康检查暴露在 `/hub/v1/health`
- 断超 60s → 告警
- 期间钉钉事件靠 ack 机制重推（at-least-once）

### 13.4 ERP 故障降级

- ERP 不可达 → SystemError → 自动重试 + 告警
- 历史价查询超 3s → 降级返回空历史价 + 仅给系统零售价 + 日志标记
- AI 不可达 → 降级到纯规则解析 + 提示用户使用更结构化命令

### 13.5 健康检查

`GET /hub/v1/health` 返回：
```json
{
  "status": "healthy" | "degraded" | "unhealthy",
  "components": {
    "postgres": "ok",
    "redis": "ok",
    "dingtalk_stream": "connected" | "reconnecting" | "down",
    "erp_default": "reachable" | "unreachable"
  },
  "uptime_seconds": 12345,
  "version": "0.1.0"
}
```

---

## 14. 安全

### 14.1 secret 分级

| 层级 | 内容 | 存哪 |
|---|---|---|
| 部署级 | HUB_DATABASE_URL / HUB_REDIS_URL / HUB_MASTER_KEY / HUB_GATEWAY_PORT / HUB_LOG_LEVEL / HUB_TIMEZONE | `.env` 必须 |
| 业务级 | ERP ApiKey / 钉钉 AppKey/AppSecret / DeepSeek Key / 未来 CRM Key 等 | HUB Postgres（AES-256-GCM 加密） |
| 一次性 | HUB_SETUP_TOKEN（首次启动） | 默认 HUB 自动生成打印日志 / 可 .env 显式覆盖 |

### 14.2 加密细节

- 算法：AES-256-GCM（带认证标签防篡改）
- 派生：HUB_MASTER_KEY → HKDF → 不同用途子密钥
- 存储：bytea(nonce + ciphertext + auth_tag)
- 密钥轮换：`hubctl rotate-master-key`（C 阶段先实现该工具的最简版本）

### 14.3 钉钉接入安全

- Stream 模式自带 TLS + 应用鉴权，不需要签名校验
- HUB 不暴露公网 inbound 端口
- HUB Web 后台只对内网开放（部署在公司内网，靠 VPN / 内网网络访问）

### 14.4 PII 处理

- task_log 元数据保留 365 天（只含元数据，无敏感）
- task_payload 加密保留 30 天，过期自动 cron 删除
- 手机号、身份证、银行卡号自动脱敏（正则识别 + 替换）
- 看明文 payload 需 `platform.conversation.monitor` 权限 + 写入 meta_audit_log
- 用户首次绑定 push 隐私告知
- HUB 后台暴露隐私说明页（spec 提供样本，HR/法务可调整）

### 14.5 后台访问审计

每次 admin 操作（创建 ApiKey、解绑用户、改角色、改 secret 等）→ 写 audit_log

### 14.6 速率限制

- `/setup/verify-token` 每 IP 每分钟最多 5 次
- 钉钉机器人每用户每分钟最多 30 次消息（防滥用）
- ERP 调用按 ErpAdapter 配置全局限流（默认 100 QPS，可改）

---

## 15. ERP 改动清单（5 项 + 零回归约束）

### 15.1 改动清单

| # | 内容 | 估时 | 优先级 |
|---|---|---|---|
| 4.1 | ServiceAccount + ApiKey 模型 + 鉴权中间件 + admin UI | 2 天 | 必须 |
| 4.2 | 钉钉绑定码功能 + ERP 个人中心绑定页面 + 二次确认 | 1.5 天 | 必须 |
| 4.3 | 历史成交价接口 `GET /products/{id}/customer-prices` | 0.5 天 | 必须 |
| 4.4 | 客户/商品现有模糊搜索盘点（先看后改） | 0.5-2 天 | 视情况 |
| 4.5 | 外部调用审计日志中间件 | 0.5 天 | 建议 |

### 15.2 零回归约束（写进每个 PR）

#### A. 改动开关化

| 改动 | feature flag | 默认值 | 关闭后行为 |
|---|---|---|---|
| 4.1 ApiKey 鉴权 | `ENABLE_API_KEY_AUTH` | False（生产先关，dev 先开） | X-API-Key 头被忽略，原认证流程不变 |
| 4.2 钉钉绑定 | `ENABLE_DINGTALK_BINDING` | False | 接口/前端 tab 都不存在 |
| 4.3 历史成交价 | （无 flag，纯新接口） | - | 新接口不被调用即等同不存在 |
| 4.5 调用审计 | `ENABLE_SERVICE_CALL_AUDIT` | True（依赖 4.1 才生效） | 中间件早返回 |

#### B. 物理隔离

- 新增模块全部放在 `routers/integration/` 子目录
- 新模块 import 现有模块**单向**（只允许新→旧，禁旧→新）
- code review 时强制检查

#### C. 测试矩阵（每个改动 PR 必须）

1. 现有 ERP 测试套件 100% 通过（不少一条）
2. 新功能正例 + 反例
3. 混合用例：JWT 与 X-API-Key 同时存在时走 ApiKey 分支
4. flag 关闭测试：每个 flag 关闭时行为等同于"改动不存在"

#### D. 灰度发布顺序

```
阶段 1：dev 环境全开 → 跑 1 周自动测试 + 手工抽查
阶段 2：staging 环境全开 → HUB dev 实例对接 → 跑 1 周端到端
阶段 3：生产部署 ERP 新代码，但 flag 仍 False → 观察 24h
阶段 4：生产逐个开 flag，每开一个观察 24h
阶段 5：HUB 生产对接，先 1-2 个测试用户
阶段 6：扩大到全员
```

#### E. 数据迁移零风险

- 新增表：`service_account` / `api_key` / `dingtalk_binding_code` / `service_call_log`
- 迁移脚本只 CREATE TABLE，**不允许 ALTER 任何现有表**
- 回滚路径：DROP TABLE 即可

#### F. 性能预算

- 4.1 鉴权：JWT 路径延迟变化 ≤ 1ms
- 4.5 审计中间件：异步写日志，请求侧延迟 ≤ 5ms
- 4.3 历史价查询：单次 ≤ 3s，超时降级
- HUB 引入的额外 ERP 压力 < 现有容量的 5%

#### G. 无回归证据链（C 阶段交付前）

| 证明 | 形式 |
|---|---|
| 单元测试 | ERP 测试套件 100% 通过；新增 ≥ 30 条覆盖新模块 |
| 手工回归 | 一份"现有功能验收清单"（约 30 个核心用户操作）改动前后逐项跑一遍 + 截图存档 |
| API 兼容 | 现有所有 endpoint OpenAPI schema 改动前后自动 diff，无变化 |
| 性能 | 关键 endpoint P95 延迟改动前后对比，差异 ≤ 5% |
| 数据库 | schema diff 仅含 CREATE TABLE，无 ALTER；核心表行数与抽样数据对比一致 |
| flag 全关 | 所有 flag 关闭后 HUB 完全不能工作但 ERP 现有功能字节级一致 |

---

## 16. 部署与初始化

### 16.1 .env 模板

```bash
# 部署级 — 一次配好就不改
HUB_DATABASE_URL=postgresql://hub:CHANGE_ME@postgres:5432/hub
HUB_REDIS_URL=redis://redis:6379/0
HUB_MASTER_KEY=<openssl rand -hex 32 生成>
HUB_GATEWAY_PORT=8091
HUB_LOG_LEVEL=info
HUB_TIMEZONE=Asia/Shanghai

# 可选：显式指定初始化 token（不指定则 HUB 自动生成打印日志）
# HUB_SETUP_TOKEN=<openssl rand -hex 16>

# 可选：覆盖默认 TTL
# HUB_SETUP_TOKEN_TTL_SECONDS=1800
# HUB_TASK_PAYLOAD_TTL_DAYS=30
```

业务 secret（钉钉、ERP、AI）**全部走 Web UI 配置中心，不在 .env 里**。

### 16.2 初始化向导（首次部署，6 步）

**前置：种子数据建立**
HUB 首次启动时（数据库为空），在进入向导**之前**自动跑种子脚本写入：
- 6 个预设角色（platform_admin / platform_ops / platform_viewer / bot_user_basic / bot_user_sales / bot_user_finance）
- 全部权限码（约 15 条，见 §7.4）
- 角色-权限关联（hub_role_permission）

种子完成后再进入向导，确保步骤 3 创建第一个 admin 时角色和权限码已经在数据库里、可以正常分配。

**向导步骤**：

1. **欢迎与系统自检** — 检查 Postgres/Redis 连通性、HUB_MASTER_KEY 配置、种子数据已建立；任一异常不能进下一步
2. **注册 ERP 系统连接** — 系统名称、ERP base URL、ERP ApiKey（已勾选 act_as_user + system_calls scope）、测试连接按钮；通过后加密存数据库
3. **创建第一个超级管理员** — 用步骤 2 配置的 ERP 连接验证 admin 的 ERP 账号 + 密码 → 创建 hub_user + downstream_identity（关联 ERP）+ hub_user_role（绑已存在的 platform_admin）
4. **注册钉钉应用**（可跳过） — AppKey / AppSecret / 机器人 ID；测试 Stream 通道连接
5. **注册 AI 提供商**（可跳过） — DeepSeek / Claude / 自定义；填 API Key + 模型名
6. **完成** — 写入 system_initialized=true → 关闭 /setup/* 路由 → 跳转登录页

### 16.3 HUB_SETUP_TOKEN 防抢跑

**默认路径（自动生成）**：
- HUB 启动检测到数据库为空 + .env 没设 → 自动生成 32 字符随机 token
- 打印到容器 stdout 日志（含访问 URL + token + TTL 提示）
- 同时写入 HUB Postgres `bootstrap_token` 表（bcrypt 哈希 + expires_at = now + 30min）
- 用户在向导第一步粘贴 → HUB 校验通过 → 删除 token 记录
- 失效条件：初始化完成 / 超过 30 分钟 / HUB 重启则重生成新的

**显式路径（运维控制）**：
- `.env` 设置 `HUB_SETUP_TOKEN=xxx`
- HUB 启动看到环境变量已设置 → 跳过自动生成
- 完成后 HUB 把 token 标记为已使用（即使 .env 还在，也不再生效）

**安全**：
- token 长度 32 字符（128 bit）
- 数据库存 bcrypt 哈希
- `/setup/verify-token` 速率限制 5 次/分钟/IP
- 初始化模式只在数据库完全空时启用，system_initialized=true 后永久关闭

---

## 17. C 阶段验收标准（Definition of Done）

### 17.1 必须满足（缺一项不算交付）

#### A. 端到端打通
- [ ] A1 用户在钉钉单聊机器人发"/绑定 张三" → 收到绑定码 → 登 ERP 输入码 → 二次确认 → 绑定成功 + 收到隐私告知
- [ ] A2 已绑定用户发"查 SKU123" → 返回商品名/SKU/库存/系统零售价；不存在的 SKU 返回模糊匹配建议
- [ ] A3 已绑定用户发"查 SKU123 给阿里" → 模糊匹配出客户 → 返回最近 5 次成交价 + 系统零售价
- [ ] A4 模糊匹配命中多个 → 返回 ActionCard 让用户回复编号
- [ ] A5 模糊匹配 0 命中 → 友好提示
- [ ] A6 业务员尝试查"超出自己权限的客户" → ERP 拒 → HUB 翻译"你没有该客户的查看权限"
- [ ] A7 AI 兜底跑通：自然语言"帮我看下阿里那个 sku123 多少钱"能解析 → 校验 → 低置信度走确认卡片

#### B. 可靠性
- [ ] B1 HUB Postgres 能按 task_id / 用户 / 时间 / 类型查 task_log
- [ ] B2 ERP 故障 → 自动重试 3 次（30s/2min/10min）→ 最终失败 push 告警
- [ ] B3 HUB 重启时 Redis Streams 中未处理任务不丢，重启后 worker 自动消费
- [ ] B4 钉钉 Stream 断线 → 自动重连 → 断超 60s 触发告警
- [ ] B5 处理失败的钉钉事件不 ack，重推后能正确重试

#### C. 安全
- [ ] C1 模型 Y 强制：HUB ErpAdapter 任何业务调用必须带 X-Acting-As-User-Id（单元测试覆盖）
- [ ] C2 ApiKey scopes：调业务接口缺 act_as_user scope 被拒；缺 Acting-As 头被拒
- [ ] C3 ERP 数据库 api_key.key_hash 是 bcrypt
- [ ] C4 binding 写入只能通过 ERP `/internal/binding/confirm-final` 触发（HUB 内部不能直接写）
- [ ] C5 secret 不入库不入仓库（基础 secret 在 .env，业务 secret 加密存数据库）
- [ ] C6 HUB_MASTER_KEY 缺失时 HUB 启动失败并明确报错
- [ ] C7 setup token 速率限制 + bcrypt 哈希 + 一次性
- [ ] C8 PII 自动脱敏（手机号、身份证、银行卡号）
- [ ] C9 看 payload 留 meta_audit_log

#### D. ERP 零回归
- [ ] D1 现有 ERP 测试套件 100% 通过
- [ ] D2 OpenAPI schema 兼容（无现有 endpoint 变化）
- [ ] D3 关键 endpoint P95 延迟变化 ≤ 5%
- [ ] D4 数据库 schema 只增不改（无 ALTER）
- [ ] D5 所有 flag 关闭后行为字节级一致
- [ ] D6 灰度发布演练在 staging 跑通

#### E. 可观测性
- [ ] E1 `/hub/v1/health` 返回 Postgres / Redis / 钉钉 Stream / ERP 连通性
- [ ] E2 关键日志结构化（含 task_id 串联）
- [ ] E3 admin 能查询任意 task_id 完整链路（钉钉消息 → 解析 → ERP → 回写）

#### F. Web 后台核心页（C 阶段必含）
- [ ] F1 完整初始化向导可跑通
- [ ] F2 用户管理 / 角色管理 / 用户角色分配 / 账号关联 / 功能权限说明 5 页可用
- [ ] F3 配置中心：下游系统 / 渠道 / AI 提供商 / 系统设置 4 页可用
- [ ] F4 任务流水列表 + 详情可用
- [ ] F5 对话监控：实时流 + 历史搜索 + 会话详情 3 页可用
- [ ] F6 操作审计 + 仪表盘可用
- [ ] F7 全 UI 中文大白话，无 code 标识符暴露

### 17.2 加分项（不阻断，但有更好）

- [ ] G1 单元测试覆盖核心域 ≥ 70%
- [ ] G2 CI 流水线（lint + 类型检查 + 测试）
- [ ] G3 完整 README（部署步骤、env 清单、本地开发）
- [ ] G4 Docker Compose 一键起（干净机器 `docker compose up`）

### 17.3 明确不做

见 §2.2。

---

## 18. 可扩展性设计

### 18.1 加新下游系统（最常见的扩展场景）

**场景**：HUB 接入 CRM。

| 步骤 | 修改位置 | 工作量 |
|---|---|---|
| 写 CrmAdapter 实现 DownstreamAdapter | HUB 代码 `hub/adapters/downstream/crm.py` | 1-2 天 |
| 注册 downstream_type | HUB 配置（admin UI） | 5 分钟 |
| 加权限码 `downstream.crm.use` + 相关 usecase | hub_permission INSERT | 5 分钟 |
| 给 hub_user 加 CRM 身份 | downstream_identity INSERT（admin UI） | 配置 |
| 给需要的角色绑 `downstream.crm.use` | admin UI 改角色 | 配置 |

**业务核心代码 0 改动**。

### 18.2 加新渠道

**场景**：HUB 接入企业微信。

| 步骤 | 修改位置 |
|---|---|
| 写 WeComChannelAdapter | HUB 代码 |
| 加权限码 `channel.wecom.use` | hub_permission INSERT |
| channel_user_binding 自然支持新 channel_type | 0 改动 |
| 给 hub_user 加企微绑定 | 钉钉 + 企微同时绑定，channel_user_binding 多一行 |

### 18.3 加新 use case

**场景**：B 阶段加合同生成。

| 步骤 | 修改位置 |
|---|---|
| 写新 UseCase 类 | HUB 代码 `hub/usecases/generate_contract.py` |
| 加权限码 `usecase.generate_contract.use` | hub_permission INSERT |
| 加预设角色 `bot_user_sales` | hub_role + hub_role_permission INSERT |
| 启用 IntentParser 识别合同生成意图 | RuleParser 加 pattern + LLMParser schema 加意图类型 |

---

## 19. 待确认事项（P1/P2，用户 review 阶段决定）

### 19.1 P1（spec 中先给默认，用户 review 时调）

| 项 | spec 默认 | 备选 |
|---|---|---|
| 机器人帮助命令 | 用户发 `help`/`帮助`/`?` 返回功能列表卡片；首次绑定后 push 欢迎语 + 可用功能 | - |
| ERP 故障熔断阈值 | 30s 内连续 5 次失败 → 熔断 60s（半开重试） | 可调 |
| HUB 部署位置 | **已确认**：与 ERP 同机部署，OrbStack 管理；必须配套 §4.2.1 的 5 条隔离措施（独立 Postgres / 独立 Docker 网络 / 资源限额 / 端口错开 / 宿主机监控） | - |
| HUB 数据库备份 | 每日凌晨全量 + 保留 30 天；task_payload 表单独排除（已 30 天 TTL） | - |
| 告警接收方默认 | 第一个 platform_admin 的钉钉号；可在系统设置改为多人 | - |
| 测试策略 | 单元测试 mock ERP/钉钉 SDK；端到端用钉钉测试应用 + ERP staging | - |
| 时区 | Asia/Shanghai 统一 | - |
| HUB task_log 元数据保留期 | 365 天 | - |
| 错误码体系 | spec 提供初始码表（约 20 条），后续按需扩展 | - |
| **HUB 后台 session 续期** | 用户登录时 HUB 转发 ERP /auth/login 拿到 ERP JWT（24h 有效）→ HUB 把它包装成 HUB session cookie；session 在 ERP JWT 还有效时每次请求自动续期；ERP JWT 过期时 HUB 拒绝并要求重新登录 | 备选：HUB 自己签 short-lived session JWT + refresh token；C 阶段先用最简方案 |

### 19.2 P2（spec 提一笔即可）

| 项 | spec 默认 |
|---|---|
| Python 版本 | 3.11+ |
| HUB 端口 | 8091 |
| HTTP 超时 | ERP 调用 5s / 钉钉回写 10s / DeepSeek 30s |
| HUB 自身的 OpenAPI 文档 | FastAPI 自带 /docs（仅 admin 可访问） |
| HUB 仓库的 CLAUDE.md | 必须有，参考 ERP 风格 + 引用本 spec |
| AI 厂商出境合规 | 默认国内厂商（DeepSeek / Qwen），切境外厂商需独立评估 |
| **AI 厂商默认配置（多厂商内置）** | HUB 内置 **DeepSeek** + **Qwen（通义千问）** 两家国内厂商作为默认选项，初始化向导和"AI 提供商管理"页面都提供下拉选择。每家厂商**预填**基础配置（API base URL + 推荐模型名），用户只需要补 API Key 即可使用；如需修改 base URL 或换模型，点"编辑"按钮自行调整。预填值参考各厂商最新文档（实施时按当时最新值落库种子数据）：<br/>**DeepSeek**：base URL `https://api.deepseek.com/v1`，推荐模型 `deepseek-chat` / `deepseek-reasoner`<br/>**Qwen（通义千问）**：base URL `https://dashscope.aliyuncs.com/compatible-mode/v1`（OpenAI 兼容模式），推荐模型 `qwen-plus` / `qwen-max`<br/>未来加新厂商（Claude / 自定义 OpenAI 兼容）走"添加自定义提供商"流程，不再硬编码进 HUB |
| 迁移工具 | aerich（Tortoise 官方） |
| **HUB 后台访问形态** | C 阶段通过 IP+端口访问（如 `http://10.0.0.5:8091`）；B 阶段后期或运维到位时再加内网域名（如 `hub.internal.company`） |
| **ERP CLAUDE.md 同步 UI 大白话原则** | 在 ERP 的 CLAUDE.md 里加一段"UI 文案禁止暴露代码标识符"原则，与 HUB 项目对齐——这条是用户群体决定的全局原则，对 ERP 同等适用 |

---

## 20. 后续阶段路线图

### 20.1 B 阶段（合同生成）

主要新增：
- 合同模板管理（HUB 后台上传 Excel 模板 + 占位符约定）
- ContractDraft 状态机（draft → collecting → previewing → confirmed → generated）
- SlotFiller 接口（多轮对话补全字段）
- AI 多轮对话编排（D 选项：A+B 混合，AI 解析 + 缺啥追问）
- 价格策略增强（PricingStrategy 加更多 fallback 链）
- HUB 后台加：合同模板管理 / 合同草稿审核流水 / 价格策略配置 / 自定义角色编辑器
- 新 use case：`usecase.generate_contract.use`

C 阶段已为 B 阶段预留：
- ChannelAdapter 已支持卡片 / 文件回传
- IntentParser 接口可加意图类型不动调用方
- TaskRunner 已支持长任务异步执行
- PricingStrategy 接口已存在

### 20.2 D 阶段（凭证自动化）

主要新增：
- 钉钉审批 outgoing webhook 走 ChannelAdapter 接收
- 凭证字段映射规则配置（HUB 后台 + 财务一起出规则）
- 新 use case：`usecase.create_voucher.use`
- HUB 调 ERP `POST /api/v1/vouchers` 创建凭证
- 凭证生成预览/审核流（生成后默认 draft 状态，财务在 ERP 审核）

C 阶段已为 D 阶段预留：
- ChannelAdapter 已支持事件订阅
- DownstreamAdapter Erp4Adapter 加 create_voucher 方法即可
- 权限模型已支持新 use case 直接配置

---

**Spec 结束**
