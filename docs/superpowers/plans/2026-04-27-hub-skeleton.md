# HUB 骨架 + 部署 + 鉴权 + 加密 Secret 实施计划（Plan 2 / 5）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/Users/lin/Desktop/hub/` 仓库内搭起 HUB 中台运行骨架——4 容器（gateway / worker / postgres / redis）通过 Docker Compose 跑起来，提供健康检查、加密 secret 管理、初始化向导骨架、6 个核心端口/策略接口的 Protocol 定义、HUB Postgres 全部表 schema、RBAC 种子数据、Bootstrap Token 防抢跑——但**不**接钉钉、**不**调 ERP、**不**实现具体业务用例。

**Architecture:** Python 3.11+ FastAPI + Tortoise ORM + PostgreSQL 16 + Redis 7（AOF）+ Vue 3 + Tailwind 4。所有业务 secret 走 AES-256-GCM 加密入库；基础部署 secret（HUB_MASTER_KEY / DB URL / Redis URL 等）走 .env。模型注册一次性完成，flag 在 Plan 3-5 才出现。同机部署（与 ERP-4 共宿主机）+ 5 条隔离配套（独立 Postgres 实例 / 独立 Docker 网络 / 资源限额 / 端口错开 / 监控）。

**Tech Stack:**
- 后端：Python 3.11+ / FastAPI 0.110+ / Tortoise ORM 0.20+ / aerich（迁移）/ asyncpg / redis-py 5+（含 streams）/ Pydantic Settings / cryptography（AES-GCM + HKDF）
- 前端：Vue 3 / Vite 7 / Tailwind 4 / Pinia 3 / Vue Router 4（仅 setup wizard 骨架，完整后台 Plan 5）
- 容器：Docker + OrbStack + docker-compose
- 测试：pytest + pytest-asyncio + httpx + fakeredis

**前置阅读：**
- [HUB Spec §3 项目元数据](../specs/2026-04-27-hub-middleware-design.md#3-项目元数据)
- [HUB Spec §4.2 部署形态](../specs/2026-04-27-hub-middleware-design.md#42-部署形态)
- [HUB Spec §5 核心抽象接口](../specs/2026-04-27-hub-middleware-design.md#5-核心抽象接口端口策略)
- [HUB Spec §6 数据模型](../specs/2026-04-27-hub-middleware-design.md#6-数据模型hub-postgres-schema)
- [HUB Spec §14 安全](../specs/2026-04-27-hub-middleware-design.md#14-安全)
- [HUB Spec §16 部署与初始化](../specs/2026-04-27-hub-middleware-design.md#16-部署与初始化)

**前置依赖：** Plan 1（ERP 集成改动）不强制先完成——本 Plan 不调 ERP，可独立开发。但 ERP 集成实测（端到端连通）在 Plan 3 才需要；Plan 1 作为并行任务推进。

**估时：** 5-7 天

---

## 文件结构

### Backend (`/Users/lin/Desktop/hub/backend/`)

| 文件 | 职责 |
|---|---|
| `main.py` | Gateway 进程入口（FastAPI app + lifespan） |
| `worker.py` | Worker 进程入口（消费 Redis Streams） |
| `pyproject.toml` | Python 项目元数据 + 依赖锁定 + aerich 配置 + setuptools 包发现 |
| `hub/__init__.py` | 包初始化（暴露 version 等） |
| `hub/config.py` | Pydantic Settings（基础部署 secret + flag 占位） |
| `hub/database.py` | Tortoise.init / close + 连接池 |
| `hub/logger.py` | 结构化日志（含 task_id 串联） |
| `hub/exceptions.py` | HUB 业务异常（BizError / SystemError 等） |
| `hub/crypto/__init__.py` | 加密入口（encrypt_secret / decrypt_secret） |
| `hub/crypto/aes_gcm.py` | AES-256-GCM 实现 |
| `hub/crypto/hkdf.py` | HUB_MASTER_KEY 派生子密钥 |
| `hub/auth/__init__.py` | 鉴权入口 |
| `hub/auth/admin_key.py` | X-HUB-Admin-Key 校验依赖（紧急 admin 用） |
| `hub/auth/erp_session.py` | ERP JWT session 包装（Plan 5 主用） |
| `hub/models/__init__.py` | 模型聚合 import |
| `hub/models/identity.py` | hub_user / channel_user_binding / downstream_identity |
| `hub/models/rbac.py` | hub_role / hub_permission / hub_role_permission / hub_user_role |
| `hub/models/config.py` | downstream_system / channel_app / ai_provider / system_config |
| `hub/models/audit.py` | task_log / task_payload / audit_log / meta_audit_log |
| `hub/models/cache.py` | erp_user_state_cache |
| `hub/models/bootstrap.py` | bootstrap_token |
| `hub/ports/__init__.py` | 6 个 Protocol 聚合 |
| `hub/ports/channel_adapter.py` | ChannelAdapter Protocol |
| `hub/ports/downstream_adapter.py` | DownstreamAdapter Protocol |
| `hub/ports/capability_provider.py` | CapabilityProvider + AICapability Protocol |
| `hub/ports/intent_parser.py` | IntentParser Protocol + ParsedIntent dataclass |
| `hub/ports/task_runner.py` | TaskRunner Protocol + TaskStatus enum |
| `hub/ports/pricing_strategy.py` | PricingStrategy Protocol + PriceInfo dataclass |
| `hub/queue/__init__.py` | 队列入口 |
| `hub/queue/redis_streams.py` | RedisStreamsRunner（TaskRunner 实现） |
| `hub/seed.py` | 启动时跑预设角色 / 权限码种子（幂等） |
| `hub/routers/__init__.py` | router 聚合 |
| `hub/routers/health.py` | `/hub/v1/health` |
| `hub/routers/setup.py` | 初始化向导 6 步（无业务逻辑骨架） |
| `hub/routers/admin/__init__.py` | admin 路由（Plan 5 填充，本 Plan 仅占位） |
| `migrations/` | aerich 自动生成的迁移文件 |

### Frontend (`/Users/lin/Desktop/hub/frontend/`)

| 文件 | 职责 |
|---|---|
| `package.json` | npm 项目元数据 |
| `vite.config.js` | Vite 配置（无 alias，跟 ERP 一致） |
| `index.html` | SPA 入口 |
| `src/main.js` | Vue app 启动 |
| `src/App.vue` | 根组件（路由出口） |
| `src/router/index.js` | 路由配置（仅 /setup/* 与 / 重定向到 setup or login） |
| `src/api/index.js` | axios 实例（baseURL `/hub/v1`） |
| `src/api/setup.js` | 向导接口封装 |
| `src/views/setup/SetupWizard.vue` | 向导 6 步壳（每步占位） |
| `src/views/setup/Step01Welcome.vue` | 步骤 1：自检（Plan 2 实现） |
| `src/views/setup/Step02Erp.vue` | 步骤 2：注册 ERP（Plan 5 实现，Plan 2 仅占位） |
| `src/views/setup/Step03Admin.vue` | 步骤 3：创建 admin（Plan 5 实现） |
| `src/views/setup/Step04Dingtalk.vue` | 步骤 4：钉钉应用（Plan 5 实现） |
| `src/views/setup/Step05AI.vue` | 步骤 5：AI 提供商（Plan 5 实现） |
| `src/views/setup/Step06Done.vue` | 步骤 6：完成（Plan 5 实现） |

### 顶层基础设施 (`/Users/lin/Desktop/hub/`)

| 文件 | 职责 |
|---|---|
| `docker-compose.yml` | 4 容器编排 + 资源限额 + 独立网络 |
| `Dockerfile.gateway` | gateway 镜像 |
| `Dockerfile.worker` | worker 镜像 |
| `Dockerfile.frontend` | frontend 构建产物 nginx 镜像（也可由 gateway serve 静态） |
| `.env.example` | 部署级 secret 模板（不入库 secret） |
| `.gitignore` | 排除 .env / __pycache__ / node_modules / dist 等 |
| `.dockerignore` | 排除测试 / 文档 / 本地缓存 |
| `CLAUDE.md` | HUB 项目说明（参考 ERP 风格） |
| `README.md` | 部署 / 本地开发说明 |

### 测试 (`/Users/lin/Desktop/hub/backend/tests/`)

| 文件 | 职责 |
|---|---|
| `conftest.py` | pytest fixture（app/db/redis） |
| `test_config.py` | Pydantic Settings 加载与必填校验 |
| `test_crypto_aes_gcm.py` | AES-256-GCM 加解密 round-trip |
| `test_crypto_hkdf.py` | HKDF 子密钥派生 |
| `test_models_smoke.py` | 所有模型可创建/查询的 smoke 测试 |
| `test_ports_protocol.py` | 6 个 Protocol 接口的契约测试 |
| `test_redis_streams_runner.py` | TaskRunner 投递+消费+ACK+死信 |
| `test_health.py` | /hub/v1/health 各组件状态 |
| `test_setup_wizard_skeleton.py` | 初始化向导路由骨架 |
| `test_bootstrap_token.py` | token 生成/校验/过期/速率限制 |
| `test_admin_key_auth.py` | X-HUB-Admin-Key 校验 |
| `test_seed.py` | 种子脚本幂等 + 数据完整 |

---

## Task 1：仓库结构 + Python 项目初始化

**Files:**
- Create: `/Users/lin/Desktop/hub/backend/pyproject.toml`
- Create: `/Users/lin/Desktop/hub/backend/.python-version`
- Create: `/Users/lin/Desktop/hub/backend/hub/__init__.py`
- Create: `/Users/lin/Desktop/hub/.gitignore`
- Create: `/Users/lin/Desktop/hub/.dockerignore`
- Create: `/Users/lin/Desktop/hub/CLAUDE.md`
- Create: `/Users/lin/Desktop/hub/README.md`

- [ ] **Step 1: 创建顶层目录结构**

```bash
cd /Users/lin/Desktop/hub
mkdir -p backend/hub/{crypto,auth,models,ports,queue,routers/admin}
mkdir -p backend/tests
mkdir -p backend/migrations
mkdir -p frontend/src/{api,router,views/setup,components}
mkdir -p infra
```

验证：
```bash
find . -type d -not -path './.git*' -not -path './node_modules*' | sort
```
期望输出包含 backend/hub/{crypto,auth,models,ports,queue,routers/admin} 等目录。

- [ ] **Step 1.5: 先创建所有 hub/ 子包的 `__init__.py`（pip editable 安装前置条件）**

`pip install -e .` 需要 setuptools 能扫描到 Python 包；本 Step 必须**早于** Step 3 安装依赖，否则 setuptools 找不到任何 package 会报错。

文件 `/Users/lin/Desktop/hub/backend/hub/__init__.py`：
```python
"""HUB 数据中台。"""

__version__ = "0.1.0"
```

文件 `/Users/lin/Desktop/hub/backend/hub/crypto/__init__.py`：
```python
"""加密 secret 管理（AES-256-GCM + HKDF）。"""
```

文件 `/Users/lin/Desktop/hub/backend/hub/auth/__init__.py`：
```python
"""HUB 鉴权（admin key + ERP session）。"""
```

文件 `/Users/lin/Desktop/hub/backend/hub/models/__init__.py`：
```python
"""HUB 数据模型聚合。"""
```

文件 `/Users/lin/Desktop/hub/backend/hub/ports/__init__.py`：
```python
"""6 个核心端口/策略 Protocol 接口。"""
```

文件 `/Users/lin/Desktop/hub/backend/hub/queue/__init__.py`：
```python
"""任务队列封装（Redis Streams）。"""
```

文件 `/Users/lin/Desktop/hub/backend/hub/routers/__init__.py`：
```python
"""HUB 路由聚合。"""
```

文件 `/Users/lin/Desktop/hub/backend/hub/routers/admin/__init__.py`：
```python
"""Admin 路由（Plan 5 填充）。"""
```

- [ ] **Step 2: 创建 pyproject.toml（依赖锁定）**

文件 `/Users/lin/Desktop/hub/backend/pyproject.toml`：
```toml
[build-system]
requires = ["setuptools>=61", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "hub-backend"
version = "0.1.0"
description = "HUB 数据中台后端"
requires-python = ">=3.11"

dependencies = [
    "fastapi>=0.110,<0.120",
    "uvicorn[standard]>=0.27",
    "tortoise-orm[asyncpg]>=0.20,<0.22",
    "aerich>=0.7,<0.8",
    "pydantic>=2.5",
    "pydantic-settings>=2.1",
    "python-multipart>=0.0.6",
    "httpx>=0.26",
    "redis>=5.0",
    "cryptography>=42.0",
    "passlib[bcrypt]>=1.7",
    "python-dateutil>=2.8",
    "structlog>=24.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "fakeredis>=2.20",
    "ruff>=0.3",
    "mypy>=1.8",
]

# setuptools 包发现：仅扫描 hub/ 这个 namespace，避免把 tests/ migrations/ 当包安装
[tool.setuptools.packages.find]
where = ["."]
include = ["hub", "hub.*"]
exclude = ["tests*", "migrations*"]

[tool.aerich]
tortoise_orm = "hub.database.TORTOISE_ORM"
location = "./migrations"
src_folder = "./hub"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
python_files = "test_*.py"
filterwarnings = [
    "ignore::DeprecationWarning",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP"]
ignore = ["E501"]
```

文件 `/Users/lin/Desktop/hub/backend/.python-version`：
```
3.11
```

- [ ] **Step 3: 安装依赖（验证 pyproject.toml 可解析）**

```bash
cd /Users/lin/Desktop/hub/backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```
期望：依赖解析成功 + 可执行 `python -c "import fastapi, tortoise, redis"`。

- [ ] **Step 4: 创建 .gitignore / .dockerignore**

文件 `/Users/lin/Desktop/hub/.gitignore`：
```
# Python
__pycache__/
*.py[cod]
.venv/
*.egg-info/

# Env
.env
.env.local

# IDE
.vscode/
.idea/

# Frontend
node_modules/
dist/
.vite/

# Tests
.pytest_cache/
.coverage
htmlcov/

# Misc
.DS_Store
*.log
tmp/
```

文件 `/Users/lin/Desktop/hub/.dockerignore`：
```
.git
.gitignore
**/__pycache__
**/.pytest_cache
**/node_modules
**/dist
.venv
*.md
docs/
tests/
.env
.env.*
```

- [ ] **Step 5: 创建 CLAUDE.md（HUB 项目指南）**

文件 `/Users/lin/Desktop/hub/CLAUDE.md`：
```markdown
# CLAUDE.md — HUB 数据中台项目指南

## 项目定位

HUB 是"上接多渠道、下连多业务系统"的端口/适配器型业务中台。第一天接钉钉一个渠道、ERP-4 一个下游、DeepSeek 一个能力，但所有三类对接点都按可插拔接口设计。详见 `docs/superpowers/specs/2026-04-27-hub-middleware-design.md`。

## 技术栈

### 后端
- **框架**: FastAPI + Tortoise ORM + PostgreSQL 16
- **队列**: Redis Streams + 消费组 + ACK + 死信
- **加密**: AES-256-GCM + HKDF（业务 secret 加密入库）
- **认证**: ApiKey（admin） + ERP JWT session（用户）
- **迁移**: aerich
- **容器**: Docker + OrbStack（与 ERP-4 一致）

### 前端
- **框架**: Vue 3 (Composition API, `<script setup>`) + Vite 7
- **样式**: Tailwind CSS 4 + CSS 变量设计系统（独立维护，初始从 ERP-4 复制）
- **状态**: Pinia 3
- **路由**: Vue Router 4

## 开发规范
- **全程使用中文沟通**（注释 / commit message / 文档）
- **所有代码开发必须使用 superpowers skill 流程**：brainstorming → writing-plans → executing-plans → verification → review
- Docker 启动前先 `orb start`
- Python 3.11+；前后端代码物理隔离 backend/ frontend/

## 设计系统

### 唯一真相源
- **Spec**: `docs/superpowers/specs/2026-04-27-hub-middleware-design.md`
- HUB 的设计 token / 主题 / 组件库**独立维护**（起步从 ERP-4 复制，不再同步）
- 后续 ERP-4 UI 升级 HUB 不自动同步

### UI 大白话原则（继承自 ERP-4）
- UI 文案**必须中文大白话**，禁止暴露代码标识符（permission code / role code / API endpoint 路径等）
- 所有"角色"、"权限"、"功能"、"按钮"、"错误提示"，必须有对应的中文显示名 + 中文说明
- 后端可保留 code 字段做内部 ID（如 hub_role.code = 'platform_admin'），UI 渲染一律用 hub_role.name（如"HUB 系统管理员"）+ description
- 错误提示同理：返回"你没有'商品查询'功能的使用权限"，不是 "PERMISSION_DENIED: usecase.query_product.use missing"

## 反模式清单（禁止出现）
- ❌ 在 UI 上暴露 permission code / role code / API path 等技术标识符
- ❌ 在 .env 之外硬编码任何 secret
- ❌ HUB 模块直接 import ERP 代码（HUB → ERP 通信只走 HTTP API）
- ❌ ERP 代码 import HUB（反向依赖严禁）
- ❌ 模型注册受 flag 控制（模型永远注册，flag 控制路由/中间件/鉴权分支）
- ❌ Tortoise.init 修改不同步 conftest.py（生产 + 测试两侧都要改）
- ❌ Frontend 用 `@/` alias（ERP-4 没配，HUB 也不配；用相对路径）
- ❌ AppTable 用 `:data` + `#cell-*` slot（实际是 columns + 默认 slot 手写 tr v-for）
- ❌ AppModal 用 `@confirm` / `show-confirm`（实际是 #footer slot）

## 关键架构约束

1. **HUB 不暴露公网 inbound 端口**：钉钉走 Stream 反向连接，HUB Web 后台仅内网访问
2. **业务 secret 加密入库**：钉钉 AppSecret / ERP ApiKey / AI Key 等用 AES-256-GCM 存
3. **基础 secret 在 .env**：HUB_MASTER_KEY / HUB_DATABASE_URL / HUB_REDIS_URL 等
4. **同机部署**：HUB 与 ERP 共宿主机，但独立 Postgres 实例 + 独立 Docker 网络 + 资源限额
5. **模型 Y**：HUB 调 ERP 业务接口必须带 `X-Acting-As-User-Id`（在 Plan 3+ 实现）
```

- [ ] **Step 6: 创建 README.md 骨架**

文件 `/Users/lin/Desktop/hub/README.md`：
```markdown
# HUB 数据中台

HUB 是"上接多渠道、下连多业务系统"的业务中台。详见 [设计文档](docs/superpowers/specs/2026-04-27-hub-middleware-design.md)。

## 快速开始

### 前置
- Docker + OrbStack
- Python 3.11+（仅本地开发用）
- Node 20+（仅前端开发用）

### 部署

```bash
# 1. 配置 .env
cp .env.example .env
# 编辑 .env，至少填好 HUB_MASTER_KEY（用 openssl rand -hex 32 生成）

# 2. 启动 OrbStack（如未启动）
orb start

# 3. 启动 4 容器
docker compose up -d

# 4. 看启动日志拿初始化 token
docker compose logs hub-gateway | grep -A5 "初始化 Token"

# 5. 浏览器访问 http://<host>:8091/setup
#    粘贴上面的 token，按向导走完
```

### 本地开发

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

### 项目结构

```
hub/
├── backend/         # FastAPI gateway + worker
├── frontend/        # Vue 3 SPA（setup wizard + admin）
├── docs/            # 设计文档与实施计划
├── docker-compose.yml
└── .env.example
```

详细文档见 `docs/`。
```

- [ ] **Step 7: 提交**

```bash
cd /Users/lin/Desktop/hub
git add -A
git commit -m "feat: HUB 项目结构初始化（pyproject + .gitignore + CLAUDE.md + README）"
```

---

## Task 2：Pydantic Settings 配置层（hub/config.py）

**Files:**
- Create: `backend/hub/config.py`
- Create: `.env.example`
- Test: `backend/tests/test_config.py`

- [ ] **Step 1: 写配置加载测试（先失败）**

文件 `backend/tests/test_config.py`：
```python
import os
import pytest
from unittest.mock import patch


def test_config_requires_master_key():
    """缺 HUB_MASTER_KEY 必须启动失败并明确报错。"""
    with patch.dict(os.environ, {}, clear=True):
        os.environ["HUB_DATABASE_URL"] = "postgresql://x@y/z"
        os.environ["HUB_REDIS_URL"] = "redis://r:6379/0"
        os.environ.pop("HUB_MASTER_KEY", None)
        from hub.config import Settings
        with pytest.raises(Exception) as exc:
            Settings()
        assert "HUB_MASTER_KEY" in str(exc.value)


def test_config_master_key_must_be_64_hex():
    """HUB_MASTER_KEY 必须是 64 位 hex（32 字节）。"""
    from hub.config import Settings
    with patch.dict(os.environ, {
        "HUB_DATABASE_URL": "postgresql://x@y/z",
        "HUB_REDIS_URL": "redis://r:6379/0",
        "HUB_MASTER_KEY": "tooshort",
    }):
        with pytest.raises(ValueError) as exc:
            Settings()
        assert "64" in str(exc.value) or "hex" in str(exc.value).lower()


def test_config_full_load():
    """完整 env 下能正常加载所有字段。"""
    from hub.config import Settings
    with patch.dict(os.environ, {
        "HUB_DATABASE_URL": "postgresql://hub@localhost/hub",
        "HUB_REDIS_URL": "redis://localhost:6379/0",
        "HUB_MASTER_KEY": "a" * 64,
        "HUB_GATEWAY_PORT": "8091",
        "HUB_LOG_LEVEL": "info",
        "HUB_TIMEZONE": "Asia/Shanghai",
    }):
        s = Settings()
        assert s.gateway_port == 8091
        assert s.log_level == "info"
        assert s.timezone == "Asia/Shanghai"
        assert s.master_key_bytes == bytes.fromhex("a" * 64)


def test_config_setup_token_optional():
    """HUB_SETUP_TOKEN 可选；未设置时为 None。"""
    from hub.config import Settings
    with patch.dict(os.environ, {
        "HUB_DATABASE_URL": "postgresql://x@y/z",
        "HUB_REDIS_URL": "redis://r:6379/0",
        "HUB_MASTER_KEY": "a" * 64,
    }, clear=True):
        s = Settings()
        assert s.setup_token is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/lin/Desktop/hub/backend
pytest tests/test_config.py -v
```
期望：`ModuleNotFoundError: No module named 'hub.config'`。

- [ ] **Step 3: 实现 hub/config.py**

文件 `backend/hub/config.py`：
```python
"""HUB 配置（基础部署 secret + 运行时常量）。

业务 secret（钉钉 AppSecret / ERP ApiKey / AI Key 等）**不**在这里——
它们走 Web UI + 数据库加密存储（见 hub.crypto + hub.models.config）。
"""
from __future__ import annotations
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="HUB_",
        case_sensitive=False,
        extra="ignore",
    )

    # --- 部署级 secret（必填）---
    database_url: str = Field(..., description="HUB Postgres 连接字符串")
    redis_url: str = Field(..., description="HUB Redis 连接字符串")
    master_key: str = Field(..., description="32 字节 hex（64 字符）AES-GCM 主密钥")

    # --- 运行时常量 ---
    gateway_port: int = Field(default=8091)
    log_level: str = Field(default="info")
    timezone: str = Field(default="Asia/Shanghai")

    # --- 一次性初始化（启动时由 hub 自动生成或运维显式指定）---
    setup_token: str | None = Field(default=None)
    setup_token_ttl_seconds: int = Field(default=1800)

    # --- TTL 配置 ---
    task_payload_ttl_days: int = Field(default=30)
    task_log_ttl_days: int = Field(default=365)

    @field_validator("master_key")
    @classmethod
    def validate_master_key(cls, v: str) -> str:
        if len(v) != 64:
            raise ValueError("HUB_MASTER_KEY 必须为 64 位 hex 字符（32 字节）")
        try:
            bytes.fromhex(v)
        except ValueError:
            raise ValueError("HUB_MASTER_KEY 不是合法 hex")
        return v

    @property
    def master_key_bytes(self) -> bytes:
        return bytes.fromhex(self.master_key)


# 模块级单例（懒初始化）
_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局 Settings 单例。每次启动只读一次环境变量。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

- [ ] **Step 4: 创建 .env.example**

文件 `/Users/lin/Desktop/hub/.env.example`：
```bash
# === HUB 部署级 secret（必填）===
# 数据库连接字符串（独立于 ERP 数据库）
HUB_DATABASE_URL=postgresql://hub:CHANGE_ME@hub-postgres:5432/hub

# Redis 连接字符串（独立于 ERP Redis）
HUB_REDIS_URL=redis://hub-redis:6379/0

# AES-GCM 主密钥（用于加密业务 secret）
# 生成方式：openssl rand -hex 32
HUB_MASTER_KEY=

# === 运行时常量（可选，有默认值）===
HUB_GATEWAY_PORT=8091
HUB_LOG_LEVEL=info
HUB_TIMEZONE=Asia/Shanghai

# === 初始化 Token（可选）===
# 留空时 HUB 启动自动生成并打印到容器日志（推荐）
# 显式设置时 HUB 沿用此值（适合用 Vault / SOPS 注入）
# HUB_SETUP_TOKEN=

# Token 有效期（秒），默认 1800 = 30 分钟
HUB_SETUP_TOKEN_TTL_SECONDS=1800

# === 业务 secret 不在这里 ===
# 钉钉 AppKey/AppSecret / ERP ApiKey / AI Key 等
# 全部通过 HUB Web 后台「配置中心」管理，加密存数据库
```

- [ ] **Step 5: 跑测试确认通过**

```bash
pytest tests/test_config.py -v
```
期望：4 个测试全 PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/hub/config.py backend/tests/test_config.py .env.example
git commit -m "feat(hub): Pydantic Settings 配置层 + .env.example"
```

---

## Task 3：加密模块（HKDF + AES-256-GCM）

**Files:**
- Create: `backend/hub/crypto/aes_gcm.py`
- Create: `backend/hub/crypto/hkdf.py`
- Modify: `backend/hub/crypto/__init__.py`（导出 encrypt_secret / decrypt_secret）
- Test: `backend/tests/test_crypto_aes_gcm.py`
- Test: `backend/tests/test_crypto_hkdf.py`

- [ ] **Step 1: 写 AES-GCM 测试（失败）**

文件 `backend/tests/test_crypto_aes_gcm.py`：
```python
import pytest
import secrets


def test_aes_gcm_round_trip():
    """加密后能解密回原文。"""
    from hub.crypto.aes_gcm import encrypt, decrypt
    key = secrets.token_bytes(32)
    plaintext = "hello 世界 🚀"
    ciphertext = encrypt(key, plaintext)
    assert ciphertext != plaintext.encode()
    assert decrypt(key, ciphertext) == plaintext


def test_aes_gcm_tampered_ciphertext_rejected():
    """密文被篡改时解密抛异常（GCM auth tag 校验）。"""
    from hub.crypto.aes_gcm import encrypt, decrypt, DecryptError
    key = secrets.token_bytes(32)
    ciphertext = encrypt(key, "secret")
    tampered = bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:]
    with pytest.raises(DecryptError):
        decrypt(key, tampered)


def test_aes_gcm_wrong_key_rejected():
    """用错 key 解密应失败。"""
    from hub.crypto.aes_gcm import encrypt, decrypt, DecryptError
    key1 = secrets.token_bytes(32)
    key2 = secrets.token_bytes(32)
    ciphertext = encrypt(key1, "secret")
    with pytest.raises(DecryptError):
        decrypt(key2, ciphertext)


def test_aes_gcm_unique_nonce_per_call():
    """每次加密 nonce 都不同（防 nonce 重用）。"""
    from hub.crypto.aes_gcm import encrypt
    key = secrets.token_bytes(32)
    c1 = encrypt(key, "same plaintext")
    c2 = encrypt(key, "same plaintext")
    assert c1 != c2  # nonce 不同 → 密文不同


def test_aes_gcm_key_length_validated():
    """key 必须 32 字节（AES-256）。"""
    from hub.crypto.aes_gcm import encrypt
    with pytest.raises(ValueError):
        encrypt(b"too_short", "x")
    with pytest.raises(ValueError):
        encrypt(b"x" * 16, "x")  # AES-128 也拒绝（统一 256）
```

- [ ] **Step 2: 写 HKDF 测试（失败）**

文件 `backend/tests/test_crypto_hkdf.py`：
```python
def test_hkdf_derive_distinct_keys_per_purpose():
    """同 master key + 不同 purpose → 不同子密钥。"""
    from hub.crypto.hkdf import derive_key
    master = b"\x01" * 32
    k1 = derive_key(master, purpose="config_secrets")
    k2 = derive_key(master, purpose="task_payload")
    assert k1 != k2
    assert len(k1) == 32
    assert len(k2) == 32


def test_hkdf_deterministic():
    """同输入产出同输出（无随机性）。"""
    from hub.crypto.hkdf import derive_key
    master = b"\xab" * 32
    k1 = derive_key(master, purpose="x")
    k2 = derive_key(master, purpose="x")
    assert k1 == k2


def test_hkdf_different_master_yields_different():
    from hub.crypto.hkdf import derive_key
    k1 = derive_key(b"\x01" * 32, purpose="x")
    k2 = derive_key(b"\x02" * 32, purpose="x")
    assert k1 != k2
```

- [ ] **Step 3: 跑测试确认失败**

```bash
pytest tests/test_crypto_aes_gcm.py tests/test_crypto_hkdf.py -v
```
期望：ImportError。

- [ ] **Step 4: 实现 hub/crypto/aes_gcm.py**

文件 `backend/hub/crypto/aes_gcm.py`：
```python
"""AES-256-GCM 加密原语。

存储格式：12 字节 nonce + 密文 + 16 字节 GCM 标签（一体存储到 bytea 字段）。
nonce 由系统随机生成，每次加密一个新的 nonce。
"""
from __future__ import annotations
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


NONCE_LENGTH = 12


class DecryptError(Exception):
    """解密失败（密钥错误 / 密文被篡改 / 数据损坏）。"""


def encrypt(key: bytes, plaintext: str | bytes, *, associated_data: bytes | None = None) -> bytes:
    """AES-256-GCM 加密。

    Args:
        key: 32 字节 AES-256 密钥
        plaintext: 待加密内容（str 自动 utf-8 编码）
        associated_data: 可选关联数据（不加密但参与认证）

    Returns:
        nonce(12B) + ciphertext + tag(16B) 的拼接字节串
    """
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("AES-256 key 必须是 32 字节 bytes")
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    nonce = secrets.token_bytes(NONCE_LENGTH)
    aead = AESGCM(key)
    ct = aead.encrypt(nonce, plaintext, associated_data)
    return nonce + ct


def decrypt(key: bytes, ciphertext: bytes, *, associated_data: bytes | None = None) -> str:
    """AES-256-GCM 解密。

    Returns: 原文 str（utf-8 解码后）

    Raises:
        DecryptError: 密钥错误 / 密文被篡改 / 数据格式错
    """
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("AES-256 key 必须是 32 字节 bytes")
    if len(ciphertext) < NONCE_LENGTH + 16:  # nonce + 至少 GCM tag
        raise DecryptError("密文长度不足")
    nonce, body = ciphertext[:NONCE_LENGTH], ciphertext[NONCE_LENGTH:]
    aead = AESGCM(key)
    try:
        plain = aead.decrypt(nonce, body, associated_data)
    except Exception as e:
        raise DecryptError(f"解密失败: {e.__class__.__name__}") from e
    return plain.decode("utf-8")
```

- [ ] **Step 5: 实现 hub/crypto/hkdf.py**

文件 `backend/hub/crypto/hkdf.py`：
```python
"""HKDF 派生子密钥。

用途：HUB_MASTER_KEY 是单一根密钥；不同用途（业务 secret 加密 / task_payload 加密 /
bootstrap token 哈希等）应使用各自派生的子密钥，避免密钥多用。
"""
from __future__ import annotations
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


HUB_HKDF_SALT = b"hub-middleware-2026"


def derive_key(master: bytes, purpose: str, length: int = 32) -> bytes:
    """从 master 派生指定用途的子密钥。

    Args:
        master: HUB_MASTER_KEY 字节串（32 字节）
        purpose: 用途字符串，如 "config_secrets" / "task_payload"
        length: 派生密钥长度（字节）

    Returns: length 字节子密钥
    """
    if not isinstance(master, bytes) or len(master) != 32:
        raise ValueError("master 必须是 32 字节 bytes")
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=HUB_HKDF_SALT,
        info=purpose.encode("utf-8"),
    )
    return hkdf.derive(master)
```

- [ ] **Step 6: 实现 hub/crypto/__init__.py（高阶 API）**

文件 `backend/hub/crypto/__init__.py`：
```python
"""加密入口：encrypt_secret / decrypt_secret 高阶 API。

使用方式：
    >>> from hub.crypto import encrypt_secret, decrypt_secret
    >>> ct = encrypt_secret("钉钉 AppSecret xxx", purpose="config_secrets")
    >>> decrypt_secret(ct, purpose="config_secrets")
    '钉钉 AppSecret xxx'
"""
from __future__ import annotations
from functools import lru_cache
from hub.config import get_settings
from hub.crypto.aes_gcm import encrypt, decrypt, DecryptError
from hub.crypto.hkdf import derive_key


@lru_cache(maxsize=8)
def _purpose_key(purpose: str) -> bytes:
    """缓存每个 purpose 的派生密钥（启动后只派生一次）。"""
    master = get_settings().master_key_bytes
    return derive_key(master, purpose=purpose)


def encrypt_secret(plaintext: str | bytes, *, purpose: str) -> bytes:
    """业务 secret 加密入库。"""
    return encrypt(_purpose_key(purpose), plaintext)


def decrypt_secret(ciphertext: bytes, *, purpose: str) -> str:
    """业务 secret 取出解密。"""
    return decrypt(_purpose_key(purpose), ciphertext)


__all__ = ["encrypt_secret", "decrypt_secret", "DecryptError"]
```

- [ ] **Step 7: 跑测试确认通过**

```bash
pytest tests/test_crypto_aes_gcm.py tests/test_crypto_hkdf.py -v
```
期望：8 个测试全 PASS。

- [ ] **Step 8: 提交**

```bash
git add backend/hub/crypto/ backend/tests/test_crypto_*.py
git commit -m "feat(hub): 加密模块（AES-256-GCM + HKDF + 高阶 encrypt_secret API）"
```

---

## Task 4：HUB 数据模型（Tortoise）

**Files:**
- Create: `backend/hub/models/identity.py`
- Create: `backend/hub/models/rbac.py`
- Create: `backend/hub/models/config.py`
- Create: `backend/hub/models/audit.py`
- Create: `backend/hub/models/cache.py`
- Create: `backend/hub/models/bootstrap.py`
- Modify: `backend/hub/models/__init__.py`

按 spec §6.2 表结构对齐字段。所有模型放在同一 module（`hub.models`）下注册到 Tortoise。

**注意：本 Task 仅创建模型文件，不写 smoke 测试**——测试需要 setup_db fixture（Task 5 才创建）和 Tortoise.init 配置（Task 5 才有）。smoke 测试由 Task 5 创建并在同一个 commit 中跑通。

- [ ] **Step 1: 实现 identity 模型**

文件 `backend/hub/models/identity.py`：
```python
from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class HubUser(Model):
    id = fields.IntField(pk=True)
    display_name = fields.CharField(max_length=100)
    status = fields.CharField(max_length=20, default="active")  # active / suspended / revoked
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "hub_user"


class ChannelUserBinding(Model):
    id = fields.IntField(pk=True)
    hub_user = fields.ForeignKeyField("models.HubUser", related_name="channel_bindings")
    channel_type = fields.CharField(max_length=30)  # dingtalk / wecom / web
    channel_userid = fields.CharField(max_length=200)
    display_meta = fields.JSONField(default=dict)
    status = fields.CharField(max_length=20, default="active")  # active / revoked
    bound_at = fields.DatetimeField(auto_now_add=True)
    revoked_at = fields.DatetimeField(null=True)
    revoked_reason = fields.CharField(max_length=100, null=True)

    class Meta:
        table = "channel_user_binding"
        unique_together = (("channel_type", "channel_userid"),)


class DownstreamIdentity(Model):
    id = fields.IntField(pk=True)
    hub_user = fields.ForeignKeyField("models.HubUser", related_name="downstream_identities")
    downstream_type = fields.CharField(max_length=30)  # erp / crm / oa
    downstream_user_id = fields.IntField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "downstream_identity"
        unique_together = (("hub_user_id", "downstream_type"),)
```

- [ ] **Step 2: 实现 RBAC 模型**

文件 `backend/hub/models/rbac.py`：
```python
from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class HubRole(Model):
    id = fields.IntField(pk=True)
    code = fields.CharField(max_length=80, unique=True)
    name = fields.CharField(max_length=100)  # UI 显示中文名
    description = fields.TextField(null=True)
    is_builtin = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)

    permissions: fields.ManyToManyRelation["HubPermission"] = fields.ManyToManyField(
        "models.HubPermission", related_name="roles", through="hub_role_permission",
    )

    class Meta:
        table = "hub_role"


class HubPermission(Model):
    id = fields.IntField(pk=True)
    code = fields.CharField(max_length=120, unique=True)  # 三段式 platform.tasks.read
    resource = fields.CharField(max_length=40)
    sub_resource = fields.CharField(max_length=40)
    action = fields.CharField(max_length=20)  # read / write / use / admin
    name = fields.CharField(max_length=100)  # UI 中文名
    description = fields.TextField(null=True)

    roles: fields.ManyToManyRelation["HubRole"]

    class Meta:
        table = "hub_permission"


class HubUserRole(Model):
    """中间表显式建模便于带审计字段（assigned_by / assigned_at）。"""
    id = fields.IntField(pk=True)
    hub_user = fields.ForeignKeyField("models.HubUser", related_name="user_roles")
    role = fields.ForeignKeyField("models.HubRole", related_name="user_roles")
    assigned_at = fields.DatetimeField(auto_now_add=True)
    assigned_by_hub_user_id = fields.IntField(null=True)

    class Meta:
        table = "hub_user_role"
        unique_together = (("hub_user_id", "role_id"),)
```

- [ ] **Step 3: 实现 config 模型**

文件 `backend/hub/models/config.py`：
```python
from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class DownstreamSystem(Model):
    id = fields.IntField(pk=True)
    downstream_type = fields.CharField(max_length=30)
    name = fields.CharField(max_length=100)
    base_url = fields.CharField(max_length=500)
    encrypted_apikey = fields.BinaryField()
    apikey_scopes = fields.JSONField(default=list)
    status = fields.CharField(max_length=20, default="active")
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "downstream_system"


class ChannelApp(Model):
    id = fields.IntField(pk=True)
    channel_type = fields.CharField(max_length=30)
    name = fields.CharField(max_length=100)
    encrypted_app_key = fields.BinaryField()
    encrypted_app_secret = fields.BinaryField()
    robot_id = fields.CharField(max_length=200, null=True)
    status = fields.CharField(max_length=20, default="active")
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "channel_app"


class AIProvider(Model):
    id = fields.IntField(pk=True)
    provider_type = fields.CharField(max_length=30)  # deepseek / qwen / claude
    name = fields.CharField(max_length=100)
    encrypted_api_key = fields.BinaryField()
    base_url = fields.CharField(max_length=500)
    model = fields.CharField(max_length=100)
    config = fields.JSONField(default=dict)
    status = fields.CharField(max_length=20, default="active")

    class Meta:
        table = "ai_provider"


class SystemConfig(Model):
    """key-value 配置表（告警接收人、TTL、运行时常量等）。"""
    key = fields.CharField(max_length=100, pk=True)
    value = fields.JSONField()
    description = fields.TextField(null=True)
    updated_at = fields.DatetimeField(auto_now=True)
    updated_by_hub_user_id = fields.IntField(null=True)

    class Meta:
        table = "system_config"
```

- [ ] **Step 4: 实现 audit 模型**

文件 `backend/hub/models/audit.py`：
```python
from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class TaskLog(Model):
    """元数据，长保留 365 天。"""
    id = fields.IntField(pk=True)
    task_id = fields.CharField(max_length=64, unique=True)
    task_type = fields.CharField(max_length=80)
    channel_type = fields.CharField(max_length=30)
    channel_userid = fields.CharField(max_length=200)
    hub_user_id = fields.IntField(null=True)
    status = fields.CharField(max_length=40)
    intent_parser = fields.CharField(max_length=20, null=True)  # rule / llm
    intent_confidence = fields.FloatField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    finished_at = fields.DatetimeField(null=True)
    duration_ms = fields.IntField(null=True)
    error_classification = fields.CharField(max_length=50, null=True)
    error_summary = fields.CharField(max_length=500, null=True)
    retry_count = fields.IntField(default=0)

    class Meta:
        table = "task_log"


class TaskPayload(Model):
    """敏感数据，加密 + 短保留 30 天。"""
    id = fields.IntField(pk=True)
    task_log = fields.OneToOneField("models.TaskLog", related_name="payload", on_delete=fields.CASCADE)
    encrypted_request = fields.BinaryField()
    encrypted_erp_calls = fields.BinaryField(null=True)
    encrypted_response = fields.BinaryField()
    created_at = fields.DatetimeField(auto_now_add=True)
    expires_at = fields.DatetimeField()

    class Meta:
        table = "task_payload"


class AuditLog(Model):
    """admin 操作审计（创建 ApiKey / 解绑 / 改角色等）。"""
    id = fields.IntField(pk=True)
    who_hub_user_id = fields.IntField()
    action = fields.CharField(max_length=80)
    target_type = fields.CharField(max_length=50, null=True)
    target_id = fields.CharField(max_length=64, null=True)
    detail = fields.JSONField(default=dict)
    ip = fields.CharField(max_length=45, null=True)
    user_agent = fields.CharField(max_length=500, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "audit_log"


class MetaAuditLog(Model):
    """看 payload 留痕（"谁在监控监控员"）。"""
    id = fields.IntField(pk=True)
    who_hub_user_id = fields.IntField()
    viewed_task_id = fields.CharField(max_length=64)
    viewed_at = fields.DatetimeField(auto_now_add=True)
    ip = fields.CharField(max_length=45, null=True)

    class Meta:
        table = "meta_audit_log"
```

- [ ] **Step 5: 实现 cache + bootstrap 模型**

文件 `backend/hub/models/cache.py`：
```python
from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class ErpUserStateCache(Model):
    """缓存 hub_user 对应 ERP 是否启用（10 分钟 TTL）。"""
    hub_user = fields.OneToOneField("models.HubUser", pk=True, related_name="erp_state_cache")
    erp_active = fields.BooleanField()
    checked_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "erp_user_state_cache"
```

文件 `backend/hub/models/bootstrap.py`：
```python
from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class BootstrapToken(Model):
    """初始化向导一次性 token。

    HUB 启动时若数据库为空（system_initialized=false）：
    - 自动生成 token（除非 .env 设置了 HUB_SETUP_TOKEN）
    - 哈希存数据库
    - 校验时按 hash 比对，验证通过后立即标记 used
    - 30 分钟 TTL（可配置）
    """
    id = fields.IntField(pk=True)
    token_hash = fields.CharField(max_length=255)
    expires_at = fields.DatetimeField()
    used_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "bootstrap_token"
```

- [ ] **Step 6: 实现 hub/models/__init__.py（聚合）**

文件 `backend/hub/models/__init__.py`：
```python
"""HUB 数据模型聚合。

注意：所有模型必须在此模块（或被它 import）下注册，
让 Tortoise.init(modules={"models": ["hub.models"]}) 一次扫描全部。
"""
from hub.models.identity import HubUser, ChannelUserBinding, DownstreamIdentity
from hub.models.rbac import HubRole, HubPermission, HubUserRole
from hub.models.config import DownstreamSystem, ChannelApp, AIProvider, SystemConfig
from hub.models.audit import TaskLog, TaskPayload, AuditLog, MetaAuditLog
from hub.models.cache import ErpUserStateCache
from hub.models.bootstrap import BootstrapToken

__all__ = [
    "HubUser", "ChannelUserBinding", "DownstreamIdentity",
    "HubRole", "HubPermission", "HubUserRole",
    "DownstreamSystem", "ChannelApp", "AIProvider", "SystemConfig",
    "TaskLog", "TaskPayload", "AuditLog", "MetaAuditLog",
    "ErpUserStateCache",
    "BootstrapToken",
]
```

- [ ] **Step 7: 提交（仅模型；测试与 conftest 在 Task 5 一并跑通）**

```bash
git add backend/hub/models/
git commit -m "feat(hub): 16 张数据模型（identity / rbac / config / audit / cache / bootstrap）"
```

注意：本提交不含测试。Task 5 会创建 database.py + conftest.py + test_models_smoke.py，并在同一个 commit 跑通 + 提交。

---

## Task 5：数据库连接 + 测试基础设施 + 模型 smoke 测试 + aerich 迁移

**Files:**
- Create: `backend/hub/database.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_models_smoke.py`
- Create: `backend/migrations/`（aerich 自动生成）

**aerich 配置说明：** 本 plan **不**单独创建 `aerich.ini`。`pyproject.toml` 中已有 `[tool.aerich]` 区块定义 `tortoise_orm = "hub.database.TORTOISE_ORM"` —— aerich 会从 pyproject 读取，不需要额外文件。Dockerfile 中亦不要 COPY `aerich.ini`。

- [ ] **Step 1: 实现 hub/database.py（TORTOISE_ORM 从 env 读取真实 URL，不用字面量）**

文件 `backend/hub/database.py`：
```python
"""Tortoise ORM 连接池管理。

TORTOISE_ORM 字典在模块加载时即从 os.environ 读取 HUB_DATABASE_URL，
aerich CLI（运行时是另一个 Python 进程）也会 import 本模块得到同样的字典。
"""
from __future__ import annotations
import os
from tortoise import Tortoise


def _resolve_db_url() -> str:
    """从环境变量读取数据库连接字符串。

    必须在模块加载时即可解析（aerich 也走这条路径），否则迁移命令拿到空字符串会报错。
    """
    url = os.environ.get("HUB_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "HUB_DATABASE_URL 未设置。运行 aerich 命令前请 export HUB_DATABASE_URL=postgresql://..."
        )
    return url


# aerich 读取此字典；模块加载时即解析 URL
TORTOISE_ORM = {
    "connections": {
        "default": _resolve_db_url() if os.environ.get("HUB_DATABASE_URL") else "",
    },
    "apps": {
        "models": {
            "models": ["hub.models", "aerich.models"],
            "default_connection": "default",
        },
    },
    "use_tz": True,
    "timezone": "Asia/Shanghai",
}


async def init_db():
    """运行时初始化 Tortoise（不建表，不跑迁移）。

    生产环境表由 aerich upgrade 在容器入口脚本中跑（见 Task 14）。
    dev/test 用 init_dev_schema()（generate_schemas）。
    """
    from hub.config import get_settings  # 延迟 import 避免 aerich CLI 时加载 settings
    await Tortoise.init(
        db_url=get_settings().database_url,
        modules={"models": ["hub.models"]},
        use_tz=True,
        timezone="Asia/Shanghai",
    )


async def init_dev_schema():
    """仅 dev/test 用：按 ORM 模型自动建表。生产用 aerich 迁移。"""
    await Tortoise.generate_schemas(safe=True)


async def close_db():
    await Tortoise.close_connections()
```

- [ ] **Step 2: 实现测试 conftest.py**

文件 `backend/tests/conftest.py`：
```python
"""HUB 测试基础设施。"""
import os
import pytest
from tortoise import Tortoise


# 测试数据库连接（CI 通常注入；本地用临时 postgres 5433）
TEST_DATABASE_URL = os.environ.get(
    "HUB_TEST_DATABASE_URL", "postgres://hub:hub@localhost:5433/hub_test"
)

TABLES_TO_TRUNCATE = [
    # 顺序：FK 依赖逆序
    "meta_audit_log", "audit_log", "task_payload", "task_log",
    "erp_user_state_cache",
    "hub_user_role", "hub_role_permission",
    "channel_user_binding", "downstream_identity",
    "hub_role", "hub_permission",
    "downstream_system", "channel_app", "ai_provider", "system_config",
    "bootstrap_token",
    "hub_user",
]


@pytest.fixture(scope="session", autouse=True)
def _set_test_env():
    """整个测试会话注入必填 env（让 hub.config 通过校验）。"""
    os.environ.setdefault("HUB_DATABASE_URL", TEST_DATABASE_URL)
    os.environ.setdefault("HUB_REDIS_URL", "redis://localhost:6380/0")
    os.environ.setdefault("HUB_MASTER_KEY", "0" * 64)


@pytest.fixture(autouse=True)
async def setup_db():
    """每条测试前清表，测试后断开。"""
    await Tortoise.init(
        db_url=TEST_DATABASE_URL,
        modules={"models": ["hub.models"]},
        use_tz=True,
        timezone="Asia/Shanghai",
    )
    await Tortoise.generate_schemas(safe=True)

    from tortoise import connections
    conn = connections.get("default")
    for table in TABLES_TO_TRUNCATE:
        try:
            await conn.execute_query(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE')
        except Exception:
            pass  # 表可能还没建，跳过

    yield

    await Tortoise.close_connections()
```

- [ ] **Step 3: 创建模型 smoke 测试（依赖 Step 2 的 setup_db fixture）**

文件 `backend/tests/test_models_smoke.py`：
```python
import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_hub_user_create():
    from hub.models import HubUser
    u = await HubUser.create(display_name="测试")
    assert u.id is not None
    assert u.status == "active"


@pytest.mark.asyncio
async def test_channel_user_binding_unique():
    from hub.models import HubUser, ChannelUserBinding
    u = await HubUser.create(display_name="x")
    await ChannelUserBinding.create(
        hub_user=u, channel_type="dingtalk", channel_userid="m1",
    )
    from tortoise.exceptions import IntegrityError
    with pytest.raises(IntegrityError):
        await ChannelUserBinding.create(
            hub_user=u, channel_type="dingtalk", channel_userid="m1",
        )


@pytest.mark.asyncio
async def test_downstream_identity_unique_per_downstream():
    from hub.models import HubUser, DownstreamIdentity
    u = await HubUser.create(display_name="y")
    await DownstreamIdentity.create(hub_user=u, downstream_type="erp", downstream_user_id=42)
    from tortoise.exceptions import IntegrityError
    with pytest.raises(IntegrityError):
        await DownstreamIdentity.create(hub_user=u, downstream_type="erp", downstream_user_id=999)


@pytest.mark.asyncio
async def test_hub_role_permission_many_to_many():
    from hub.models import HubRole, HubPermission
    role = await HubRole.create(code="r1", name="角色 1", is_builtin=False)
    perm = await HubPermission.create(
        code="p1", resource="platform", sub_resource="x", action="read",
        name="测试权限",
    )
    await role.permissions.add(perm)
    fetched = await HubRole.get(id=role.id).prefetch_related("permissions")
    perms = [p async for p in fetched.permissions]
    assert len(perms) == 1
    assert perms[0].code == "p1"


@pytest.mark.asyncio
async def test_task_log_and_payload_relationship():
    from hub.models import TaskLog, TaskPayload
    t = await TaskLog.create(
        task_id="abc-123", task_type="query_product",
        channel_type="dingtalk", channel_userid="m1", status="queued",
    )
    p = await TaskPayload.create(
        task_log=t,
        encrypted_request=b"\x00" * 32,
        encrypted_response=b"\x00" * 32,
        expires_at=datetime.now(timezone.utc),
    )
    assert p.task_log_id == t.id


@pytest.mark.asyncio
async def test_downstream_system_encrypted_apikey_field():
    from hub.models import DownstreamSystem
    ds = await DownstreamSystem.create(
        downstream_type="erp", name="ERP 测试",
        base_url="http://localhost:8090", encrypted_apikey=b"\x00" * 32,
        apikey_scopes=["act_as_user", "system_calls"],
    )
    assert ds.id is not None
    assert ds.apikey_scopes == ["act_as_user", "system_calls"]
```

- [ ] **Step 4: 起测试数据库（本地 Docker 临时实例）**

```bash
# 启动一个独立的 postgres 容器，仅用于测试（端口 5433）
docker run -d --name hub-pg-test \
    -e POSTGRES_USER=hub -e POSTGRES_PASSWORD=hub -e POSTGRES_DB=hub_test \
    -p 5433:5432 postgres:16

# 起一个 redis 测试实例（端口 6380）
docker run -d --name hub-redis-test -p 6380:6379 redis:7
```

- [ ] **Step 5: 跑模型 smoke 测试确认通过**

```bash
cd backend
pytest tests/test_models_smoke.py -v
```
期望：6 个测试全 PASS。

- [ ] **Step 6: 初始化 aerich 迁移（用独立干净数据库，避免污染测试库）**

**关键：** 不要复用 Step 4 / Step 5 用过的测试数据库（端口 5433 / `hub_test`）—— Step 5 已用 `generate_schemas` 在那个库建好了表，aerich `init-db` 在已有表的库上跑会触发 `relation already exists`，且会把已有表当成手动创建状态记录到迁移历史中，污染初始迁移文件。

正确做法：起一个**专门的迁移 dev 库**（端口 5434 / 库名 `hub_dev`），它只用来生成初始迁移。aerich 跑完后这个库可以保留也可以删除——重要的是仓库里 `migrations/` 目录干净的 baseline。

```bash
# 0. 清理可能残留的同名容器（重复执行 plan 时不会因冲突失败）
docker rm -f hub-pg-aerich-init 2>/dev/null || true

# 1. 起一个独立的、空的 postgres 实例供 aerich init-db 用
docker run -d --name hub-pg-aerich-init \
    -e POSTGRES_USER=hub -e POSTGRES_PASSWORD=hub -e POSTGRES_DB=hub_dev \
    -p 5434:5432 postgres:16

# 2. 等 Postgres 真正 ready（pg_isready 轮询，避免 sleep 不稳）
until docker exec hub-pg-aerich-init pg_isready -U hub > /dev/null 2>&1; do
    echo "等待 hub-pg-aerich-init ready..."
    sleep 1
done

# 3. 跑 aerich init / init-db（用真实 DSN，不引用任何不存在的 shell 变量）
cd backend
export HUB_DATABASE_URL="postgres://hub:hub@localhost:5434/hub_dev"
aerich init -t hub.database.TORTOISE_ORM --location ./migrations
aerich init-db

ls migrations/models/
```
期望：生成 `models/0_xxx_init.py` 初始迁移文件，记录建出全部 16 张表。

```bash
# 4. 清理临时 aerich-init 容器（保留产出的 migrations/ 在仓库里）
docker rm -f hub-pg-aerich-init
unset HUB_DATABASE_URL
```

注意：`models/0_xxx_init.py` 是干净的全量 baseline，未来通过 `aerich migrate` 增量加 v1/v2/...。生产环境靠 `Dockerfile.migrate` 跑 `aerich upgrade` 就能从空库一路 upgrade 到最新。

- [ ] **Step 7: 提交（database + conftest + smoke test + 迁移 同一个 commit）**

```bash
git add backend/hub/database.py \
        backend/tests/conftest.py \
        backend/tests/test_models_smoke.py \
        backend/migrations/
git commit -m "feat(hub): Tortoise.init + 测试 conftest + 模型 smoke 测试 + aerich 迁移"
```

---

## Task 6：6 个核心 Protocol 接口（端口/策略）

**Files:**
- Create: `backend/hub/ports/channel_adapter.py`
- Create: `backend/hub/ports/downstream_adapter.py`
- Create: `backend/hub/ports/capability_provider.py`
- Create: `backend/hub/ports/intent_parser.py`
- Create: `backend/hub/ports/task_runner.py`
- Create: `backend/hub/ports/pricing_strategy.py`
- Modify: `backend/hub/ports/__init__.py`
- Test: `backend/tests/test_ports_protocol.py`

本 Task **只定义接口**，不实现具体 Adapter（DingTalk/Erp4/DeepSeek 等留给 Plan 3-4）。本 Task 验收：协议定义合法，可被 Mock 实现满足。

- [ ] **Step 1: 写 Protocol 契约测试**

文件 `backend/tests/test_ports_protocol.py`：
```python
import pytest


def test_channel_adapter_protocol_imports():
    from hub.ports import (
        ChannelAdapter, InboundMessage, OutboundMessage, OutboundMessageType
    )
    assert ChannelAdapter is not None
    msg = InboundMessage(
        channel_type="dingtalk", channel_userid="m1",
        conversation_id="c1", content="hi", content_type="text",
        timestamp=1700000000, raw_payload={},
    )
    assert msg.channel_type == "dingtalk"


def test_downstream_adapter_protocol_imports():
    from hub.ports import DownstreamAdapter
    assert DownstreamAdapter is not None


def test_capability_provider_protocol_imports():
    from hub.ports import CapabilityProvider, AICapability
    assert AICapability is not None


def test_intent_parser_imports():
    from hub.ports import IntentParser, ParsedIntent
    intent = ParsedIntent(intent_type="query_product", fields={"sku": "X"}, confidence=0.9)
    assert intent.confidence == 0.9


def test_task_runner_imports():
    from hub.ports import TaskRunner, TaskStatus
    assert TaskStatus.QUEUED.value == "queued"


def test_pricing_strategy_imports():
    from hub.ports import PricingStrategy, PriceInfo
    p = PriceInfo(unit_price="100.00", source="retail", customer_id=None)
    assert p.unit_price == "100.00"


def test_mock_implementation_satisfies_channel_adapter():
    """实例化一个 Mock 实现确认 Protocol 鸭子类型成立。"""
    from hub.ports import ChannelAdapter

    class MockChannel:
        channel_type = "mock"
        async def start(self): pass
        async def stop(self): pass
        async def send_message(self, channel_userid, message): pass
        def on_message(self, handler): pass

    m: ChannelAdapter = MockChannel()  # 类型注解兼容
    assert m.channel_type == "mock"
```

- [ ] **Step 2: 实现 ChannelAdapter**

文件 `backend/hub/ports/channel_adapter.py`：
```python
"""ChannelAdapter Protocol：接入端协议适配（钉钉/企微/Web 等）。"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, Callable, Awaitable, Any


class OutboundMessageType(str, Enum):
    TEXT = "text"
    MARKDOWN = "markdown"
    ACTIONCARD = "actioncard"


@dataclass
class InboundMessage:
    """统一入站消息（不同渠道转换到同一格式）。"""
    channel_type: str
    channel_userid: str
    conversation_id: str
    content: str
    content_type: str  # text / image / file / button_click
    timestamp: int  # epoch seconds
    raw_payload: dict = field(default_factory=dict)


@dataclass
class OutboundMessage:
    """统一出站消息。"""
    type: OutboundMessageType
    text: str | None = None
    markdown: str | None = None
    actioncard: dict | None = None


class ChannelAdapter(Protocol):
    """渠道接入适配器协议。

    生命周期：start() 建立到渠道的连接 → on_message() 注册回调 → 渠道事件触发回调
    → send_message() 主动 push 回应 → stop() 优雅关闭。
    """
    channel_type: str

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send_message(self, channel_userid: str, message: OutboundMessage) -> None: ...
    def on_message(self, handler: Callable[[InboundMessage], Awaitable[None]]) -> None: ...
```

- [ ] **Step 3: 实现 DownstreamAdapter**

文件 `backend/hub/ports/downstream_adapter.py`：
```python
"""DownstreamAdapter Protocol：下游业务系统协议（ERP / CRM / OA 等）。

具体方法签名由各 Adapter 根据下游 API 决定，但所有"代用户"调用必须接受
acting_as_user_id 参数（模型 Y 强制约束）。
"""
from __future__ import annotations
from typing import Protocol


class DownstreamAdapter(Protocol):
    downstream_type: str

    async def health_check(self) -> bool: ...
```

- [ ] **Step 4: 实现 CapabilityProvider**

文件 `backend/hub/ports/capability_provider.py`：
```python
"""CapabilityProvider Protocol：通用能力（AI / OCR / SMS 等）。"""
from __future__ import annotations
from typing import Protocol


class CapabilityProvider(Protocol):
    capability_type: str


class AICapability(CapabilityProvider):
    """AI 能力（聊天 + 意图解析）。"""

    async def parse_intent(self, text: str, schema: dict) -> dict:
        """根据 schema 把自然语言解析成结构化字段。"""
        ...

    async def chat(self, messages: list[dict], **kwargs) -> str:
        """通用对话（system / user / assistant 消息列表）。"""
        ...
```

- [ ] **Step 5: 实现 IntentParser**

文件 `backend/hub/ports/intent_parser.py`：
```python
"""IntentParser Protocol：意图解析。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ParsedIntent:
    intent_type: str  # query_product / query_customer_history / generate_contract / ...
    fields: dict = field(default_factory=dict)
    confidence: float = 0.0  # 0.0 ~ 1.0
    parser: str = "unknown"  # rule / llm
    notes: str | None = None


class IntentParser(Protocol):
    async def parse(self, text: str, context: dict) -> ParsedIntent: ...
```

- [ ] **Step 6: 实现 TaskRunner**

文件 `backend/hub/ports/task_runner.py`：
```python
"""TaskRunner Protocol：任务异步执行。"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED_USER = "failed_user"
    FAILED_SYSTEM_RETRYING = "failed_system_retrying"
    FAILED_SYSTEM_FINAL = "failed_system_final"


@dataclass
class TaskInfo:
    task_id: str
    task_type: str
    status: TaskStatus
    payload: dict


class TaskRunner(Protocol):
    """任务投递与状态查询。"""
    async def submit(self, task_type: str, payload: dict) -> str:
        """投递任务，返回 task_id。"""
        ...

    async def get_status(self, task_id: str) -> TaskStatus | None: ...
```

- [ ] **Step 7: 实现 PricingStrategy**

文件 `backend/hub/ports/pricing_strategy.py`：
```python
"""PricingStrategy Protocol：价格策略（fallback 链可插拔）。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


@dataclass
class PriceInfo:
    unit_price: str  # Decimal as str（避免 float 精度丢失）
    source: str  # retail / customer_history / customer_special / fallback_default
    customer_id: int | None = None
    notes: str | None = None


class PricingStrategy(Protocol):
    async def get_price(
        self, product_id: int, customer_id: int | None, *, acting_as: int,
    ) -> PriceInfo: ...
```

- [ ] **Step 8: 聚合 ports/__init__.py**

文件 `backend/hub/ports/__init__.py`：
```python
"""6 个核心端口/策略 Protocol 聚合。"""
from hub.ports.channel_adapter import (
    ChannelAdapter, InboundMessage, OutboundMessage, OutboundMessageType,
)
from hub.ports.downstream_adapter import DownstreamAdapter
from hub.ports.capability_provider import CapabilityProvider, AICapability
from hub.ports.intent_parser import IntentParser, ParsedIntent
from hub.ports.task_runner import TaskRunner, TaskStatus, TaskInfo
from hub.ports.pricing_strategy import PricingStrategy, PriceInfo

__all__ = [
    "ChannelAdapter", "InboundMessage", "OutboundMessage", "OutboundMessageType",
    "DownstreamAdapter",
    "CapabilityProvider", "AICapability",
    "IntentParser", "ParsedIntent",
    "TaskRunner", "TaskStatus", "TaskInfo",
    "PricingStrategy", "PriceInfo",
]
```

- [ ] **Step 9: 跑测试确认通过**

```bash
pytest tests/test_ports_protocol.py -v
```
期望：7 个测试全 PASS。

- [ ] **Step 10: 提交**

```bash
git add backend/hub/ports/ backend/tests/test_ports_protocol.py
git commit -m "feat(hub): 6 个核心 Protocol 接口（ChannelAdapter / DownstreamAdapter / CapabilityProvider / IntentParser / TaskRunner / PricingStrategy）"
```

---

## Task 7：Redis Streams 队列封装（TaskRunner 实现）

**Files:**
- Create: `backend/hub/queue/redis_streams.py`
- Modify: `backend/hub/queue/__init__.py`
- Test: `backend/tests/test_redis_streams_runner.py`

- [ ] **Step 1: 写测试（用 fakeredis，无需真实 Redis）**

文件 `backend/tests/test_redis_streams_runner.py`：
```python
import pytest
import asyncio
from fakeredis import aioredis as fakeredis_aio


@pytest.fixture
async def fake_redis():
    client = fakeredis_aio.FakeRedis()
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_submit_returns_task_id(fake_redis):
    from hub.queue.redis_streams import RedisStreamsRunner
    runner = RedisStreamsRunner(redis_client=fake_redis, stream_name="hub:tasks:default")
    tid = await runner.submit("test_task", {"foo": "bar"})
    assert isinstance(tid, str)
    assert len(tid) > 0


@pytest.mark.asyncio
async def test_consume_one_task(fake_redis):
    from hub.queue.redis_streams import RedisStreamsRunner
    runner = RedisStreamsRunner(redis_client=fake_redis, stream_name="hub:tasks:default")
    tid = await runner.submit("test_task", {"k": "v"})

    # 启动消费组
    await runner.ensure_consumer_group("hub-workers")
    msgs = await runner.read_one("hub-workers", "consumer-1", block_ms=10)
    assert len(msgs) == 1
    msg_id, payload = msgs[0]
    assert payload["task_type"] == "test_task"
    assert payload["task_id"] == tid


@pytest.mark.asyncio
async def test_ack_marks_handled(fake_redis):
    from hub.queue.redis_streams import RedisStreamsRunner
    runner = RedisStreamsRunner(redis_client=fake_redis, stream_name="hub:tasks:default")
    tid = await runner.submit("t", {})
    await runner.ensure_consumer_group("hub-workers")
    msgs = await runner.read_one("hub-workers", "c1", block_ms=10)
    msg_id, _ = msgs[0]
    await runner.ack("hub-workers", msg_id)

    # ACK 后 PEL 不再有这条
    pending = await runner.pending_count("hub-workers")
    assert pending == 0


@pytest.mark.asyncio
async def test_dead_letter_after_max_retries(fake_redis):
    from hub.queue.redis_streams import RedisStreamsRunner
    runner = RedisStreamsRunner(
        redis_client=fake_redis,
        stream_name="hub:tasks:default",
        dead_stream_name="hub:tasks:dead",
    )
    await runner.submit("bad_task", {})
    await runner.ensure_consumer_group("hub-workers")
    msgs = await runner.read_one("hub-workers", "c1", block_ms=10)
    msg_id, _ = msgs[0]

    # 模拟 3 次重试都失败 → 移到死信
    for _ in range(3):
        await runner.mark_failed(msg_id, msg_data=msgs[0][1])
    await runner.move_to_dead("hub-workers", msg_id, msg_data=msgs[0][1])

    dead_count = await fake_redis.xlen("hub:tasks:dead")
    assert dead_count == 1
```

- [ ] **Step 2: 实现 RedisStreamsRunner**

文件 `backend/hub/queue/redis_streams.py`：
```python
"""Redis Streams + 消费组 + ACK + 死信的 TaskRunner 实现。

Stream 设计：
- hub:tasks:default 为主流
- hub:tasks:dead 为死信流（手动重试 / 告警）
- 消费组名约定：hub-workers

每条消息字段：
  task_id（uuid 字符串）
  task_type
  payload_json
  retry_count
  submitted_at
"""
from __future__ import annotations
import json
import secrets
import time
from typing import Any
from redis.asyncio import Redis


class RedisStreamsRunner:
    def __init__(
        self,
        redis_client: Redis,
        stream_name: str = "hub:tasks:default",
        dead_stream_name: str = "hub:tasks:dead",
        max_len: int = 100000,
    ):
        self.redis = redis_client
        self.stream = stream_name
        self.dead_stream = dead_stream_name
        self.max_len = max_len

    async def submit(self, task_type: str, payload: dict) -> str:
        task_id = secrets.token_urlsafe(16)
        await self.redis.xadd(
            self.stream,
            {
                "task_id": task_id,
                "task_type": task_type,
                "payload_json": json.dumps(payload, ensure_ascii=False),
                "retry_count": "0",
                "submitted_at": str(int(time.time())),
            },
            maxlen=self.max_len, approximate=True,
        )
        return task_id

    async def ensure_consumer_group(self, group: str) -> None:
        try:
            await self.redis.xgroup_create(self.stream, group, id="0", mkstream=True)
        except Exception:
            pass  # 已存在

    async def read_one(self, group: str, consumer: str, *, block_ms: int = 5000) -> list[tuple[str, dict]]:
        result = await self.redis.xreadgroup(
            group, consumer, {self.stream: ">"}, count=1, block=block_ms,
        )
        if not result:
            return []
        out = []
        for stream_name, messages in result:
            for msg_id, data in messages:
                # data 字段 bytes → str
                decoded = {k.decode(): v.decode() for k, v in data.items()}
                if "payload_json" in decoded:
                    decoded["payload"] = json.loads(decoded.pop("payload_json"))
                out.append((msg_id.decode(), decoded))
        return out

    async def ack(self, group: str, msg_id: str) -> None:
        await self.redis.xack(self.stream, group, msg_id)

    async def pending_count(self, group: str) -> int:
        info = await self.redis.xpending(self.stream, group)
        return info.get("pending", 0) if isinstance(info, dict) else info[0]

    async def mark_failed(self, msg_id: str, msg_data: dict) -> None:
        """单次失败 → 不 ack，留在 PEL 等下次 claim。retry_count 由 worker 在 claim 时更新。"""
        # 这里不主动 inc retry_count，因为消息体不可变；retry_count 由 worker 进程内逻辑维护
        # 也可以写到一个独立的 hash 表里跟踪每个 msg_id 的重试次数
        pass

    async def move_to_dead(self, group: str, msg_id: str, msg_data: dict) -> None:
        await self.redis.xadd(
            self.dead_stream,
            {
                "original_msg_id": msg_id,
                "task_id": msg_data.get("task_id", ""),
                "task_type": msg_data.get("task_type", ""),
                "payload_json": json.dumps(msg_data.get("payload", {}), ensure_ascii=False),
                "moved_at": str(int(time.time())),
            },
        )
        await self.ack(group, msg_id)  # 主流 ACK 让消息从 PEL 出去
```

文件 `backend/hub/queue/__init__.py`：
```python
from hub.queue.redis_streams import RedisStreamsRunner

__all__ = ["RedisStreamsRunner"]
```

- [ ] **Step 3: 跑测试确认通过**

```bash
pytest tests/test_redis_streams_runner.py -v
```
期望：4 个测试全 PASS。

- [ ] **Step 4: 提交**

```bash
git add backend/hub/queue/ backend/tests/test_redis_streams_runner.py
git commit -m "feat(hub): RedisStreamsRunner（任务队列 + 消费组 + ACK + 死信）"
```

---

## Task 8：Bootstrap Token 模块

**Files:**
- Create: `backend/hub/auth/bootstrap_token.py`
- Test: `backend/tests/test_bootstrap_token.py`

- [ ] **Step 1: 写测试（失败）**

文件 `backend/tests/test_bootstrap_token.py`：
```python
import pytest
from datetime import datetime, timezone, timedelta


@pytest.mark.asyncio
async def test_generate_and_verify_token():
    from hub.auth.bootstrap_token import generate_token, verify_token
    plaintext = await generate_token(ttl_seconds=300)
    assert len(plaintext) >= 32
    assert await verify_token(plaintext) is True


@pytest.mark.asyncio
async def test_verify_wrong_token():
    from hub.auth.bootstrap_token import generate_token, verify_token
    await generate_token(ttl_seconds=300)
    assert await verify_token("wrong_token_xxx") is False


@pytest.mark.asyncio
async def test_expired_token():
    from hub.auth.bootstrap_token import generate_token, verify_token
    plaintext = await generate_token(ttl_seconds=-10)  # 已过期
    assert await verify_token(plaintext) is False


@pytest.mark.asyncio
async def test_used_token():
    from hub.auth.bootstrap_token import generate_token, verify_token, mark_used
    plaintext = await generate_token(ttl_seconds=300)
    assert await verify_token(plaintext) is True
    await mark_used(plaintext)
    assert await verify_token(plaintext) is False  # 已使用


@pytest.mark.asyncio
async def test_explicit_token_from_env(monkeypatch):
    """HUB_SETUP_TOKEN 环境变量显式指定时应被采纳。"""
    monkeypatch.setenv("HUB_SETUP_TOKEN", "explicit_token_123456789012345")
    from hub.auth.bootstrap_token import generate_token, verify_token
    # 重新读 settings
    from hub import config
    config._settings = None  # 清缓存
    await generate_token(ttl_seconds=300)
    assert await verify_token("explicit_token_123456789012345") is True
```

- [ ] **Step 2: 实现 bootstrap_token**

文件 `backend/hub/auth/bootstrap_token.py`：
```python
"""Bootstrap Token：HUB 首次启动一次性 token 防抢跑。"""
from __future__ import annotations
import secrets
from datetime import datetime, timezone, timedelta
from passlib.hash import bcrypt
from hub.config import get_settings
from hub.models import BootstrapToken


async def generate_token(ttl_seconds: int = 1800) -> str:
    """生成（或采用 .env 显式指定）一次性 token。

    Returns: 明文 token（运维一次性使用）
    """
    settings = get_settings()
    plaintext = settings.setup_token or secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    token_hash = bcrypt.hash(plaintext)
    await BootstrapToken.create(token_hash=token_hash, expires_at=expires_at)
    return plaintext


async def verify_token(plaintext: str) -> bool:
    """校验 token 合法性。

    Returns:
        True: 合法且未使用未过期
        False: 不存在 / 已使用 / 已过期 / 哈希不匹配
    """
    if not plaintext or len(plaintext) < 8:
        return False
    candidates = await BootstrapToken.filter(
        used_at__isnull=True,
        expires_at__gt=datetime.now(timezone.utc),
    ).order_by("-created_at").limit(20)

    for candidate in candidates:
        try:
            if bcrypt.verify(plaintext, candidate.token_hash):
                return True
        except Exception:
            continue
    return False


async def mark_used(plaintext: str) -> None:
    """初始化完成后标记 token 已使用（非原子，调用方负责互斥；
    并发场景请用 verify_and_consume_token）。"""
    candidates = await BootstrapToken.filter(used_at__isnull=True)
    for candidate in candidates:
        try:
            if bcrypt.verify(plaintext, candidate.token_hash):
                candidate.used_at = datetime.now(timezone.utc)
                await candidate.save()
                return
        except Exception:
            continue


async def verify_and_consume_token(plaintext: str) -> bool:
    """**原子**校验 + 消费 token：通过的同时立即标记 used。

    并发场景下两个请求同时拿到同一 token：两边 bcrypt.verify 都返回 True，但
    `UPDATE ... WHERE used_at IS NULL` 只有先到的那一行影响 1 行，后到的影响 0 行。
    Returns:
        True: 校验通过且本次成功消费（赢家）
        False: 不存在 / 已使用 / 已过期 / 哈希不匹配 / 并发输家
    """
    if not plaintext or len(plaintext) < 8:
        return False
    candidates = await BootstrapToken.filter(
        used_at__isnull=True,
        expires_at__gt=datetime.now(timezone.utc),
    ).order_by("-created_at").limit(20)

    for candidate in candidates:
        try:
            if not bcrypt.verify(plaintext, candidate.token_hash):
                continue
        except Exception:
            continue
        # 哈希命中 → 用 UPDATE WHERE used_at IS NULL 原子标记
        rows = await BootstrapToken.filter(
            id=candidate.id, used_at__isnull=True,
        ).update(used_at=datetime.now(timezone.utc))
        return rows > 0  # 1 = 我赢了，0 = 并发别人先消费了
    return False
```

- [ ] **Step 3: 跑测试确认通过**

```bash
pytest tests/test_bootstrap_token.py -v
```
期望：5 个测试全 PASS。

- [ ] **Step 4: 提交**

```bash
git add backend/hub/auth/bootstrap_token.py backend/tests/test_bootstrap_token.py
git commit -m "feat(hub): Bootstrap Token 防抢跑（生成 / 校验 / 使用 / 过期 / 显式覆盖）"
```

---

## Task 9：Gateway FastAPI app + 健康检查 + 启动 lifecycle

**Files:**
- Create: `backend/main.py`
- Create: `backend/hub/routers/health.py`
- Test: `backend/tests/test_health.py`

- [ ] **Step 1: 写健康检查测试**

文件 `backend/tests/test_health.py`：
```python
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def app_client():
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_returns_200(app_client):
    resp = await app_client.get("/hub/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("healthy", "degraded", "unhealthy")
    assert "components" in body
    assert "uptime_seconds" in body
    assert "version" in body


@pytest.mark.asyncio
async def test_health_lists_components(app_client):
    resp = await app_client.get("/hub/v1/health")
    body = resp.json()
    assert "postgres" in body["components"]
    assert "redis" in body["components"]
```

- [ ] **Step 2: 实现 health router**

文件 `backend/hub/routers/health.py`：
```python
"""健康检查 endpoint。"""
from __future__ import annotations
import time
from fastapi import APIRouter
from tortoise import connections
from hub import __version__

router = APIRouter(prefix="/hub/v1", tags=["health"])

_app_started_at = time.time()


async def _check_postgres() -> str:
    try:
        conn = connections.get("default")
        await conn.execute_query("SELECT 1")
        return "ok"
    except Exception:
        return "down"


async def _check_redis() -> str:
    try:
        from hub.queue import RedisStreamsRunner  # noqa
        # Redis 检查推迟到 worker 真正连接时；gateway 这里返回 unknown
        return "unknown"
    except Exception:
        return "down"


@router.get("/health")
async def health():
    components = {
        "postgres": await _check_postgres(),
        "redis": await _check_redis(),
        "dingtalk_stream": "not_started",  # Plan 3 启用
        "erp_default": "not_configured",  # Plan 3 启用
    }
    bad = [k for k, v in components.items() if v == "down"]
    status = "healthy" if not bad else "degraded"
    if "postgres" in bad:
        status = "unhealthy"
    return {
        "status": status,
        "components": components,
        "uptime_seconds": int(time.time() - _app_started_at),
        "version": __version__,
    }
```

- [ ] **Step 2.5: 创建 hub/seed.py stub（避免 Task 9 commit 后启动报 ImportError）**

`main.py:lifespan` 引用 `hub.seed.run_seed`，但真实实现在 Task 11 才完成。本 Step 先放一个空 stub 让进程能起来；Task 11 直接覆盖此文件为真实实现。

文件 `backend/hub/seed.py`：
```python
"""HUB 启动时跑预设角色 + 权限码种子。

本文件由 Task 9 创建为 stub，Task 11 替换为真实实现。
"""
from __future__ import annotations


async def run_seed() -> None:
    """空实现：Task 11 替换为真实角色 / 权限码种子逻辑。"""
    pass
```

- [ ] **Step 3: 实现 main.py（gateway 入口）**

文件 `backend/main.py`：
```python
"""HUB Gateway 进程入口。"""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from hub.config import get_settings
from hub.database import init_db, close_db
from hub.routers import health


logger = logging.getLogger("hub")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(f"HUB Gateway 启动 - 端口 {settings.gateway_port}")

    # 1. 初始化数据库连接（迁移已由 hub-migrate 容器跑完，这里只连）
    await init_db()

    # 2. 跑种子数据（Task 9 创建 stub，Task 11 替换为真实实现）
    from hub.seed import run_seed
    await run_seed()

    # 3. 首启动检测：未初始化（system_initialized=false）且无未使用 token → 生成并打印
    from hub.models import SystemConfig, BootstrapToken
    from datetime import datetime, timezone
    initialized = await SystemConfig.filter(key="system_initialized", value=True).exists()
    if not initialized:
        active_token = await BootstrapToken.filter(
            used_at__isnull=True, expires_at__gt=datetime.now(timezone.utc),
        ).exists()
        if not active_token:
            from hub.auth.bootstrap_token import generate_token
            plaintext = await generate_token(ttl_seconds=settings.setup_token_ttl_seconds)
            ttl_min = settings.setup_token_ttl_seconds // 60
            logger.warning(
                f"\n{'='*60}\n"
                f"  HUB 首次启动 - 初始化模式\n\n"
                f"  请用浏览器打开：\n"
                f"      http://<HUB-host>:{settings.gateway_port}/setup\n\n"
                f"  一次性初始化 Token（{ttl_min} 分钟内有效）：\n\n"
                f"      {plaintext}\n\n"
                f"  将此 token 粘贴到向导第一步。完成初始化或超时后自动失效。\n"
                f"{'='*60}"
            )

    yield

    logger.info("HUB Gateway 关闭")
    await close_db()


app = FastAPI(
    title="HUB Gateway",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",  # Plan 5 加 admin 鉴权
)

# CORS（仅内网访问，宽松配置即可）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 内网部署，安全靠 ApiKey + ERP session
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册
app.include_router(health.router)
```

- [ ] **Step 4: 跑健康检查测试**

```bash
pytest tests/test_health.py -v
```
期望：2 个测试 PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/main.py \
        backend/hub/seed.py \
        backend/hub/routers/health.py \
        backend/tests/test_health.py
git commit -m "feat(hub): Gateway FastAPI app + lifespan + 健康检查 endpoint + seed stub"
```

---

## Task 10：HUB 内部 API 鉴权（admin key）

**Files:**
- Create: `backend/hub/auth/admin_key.py`
- Test: `backend/tests/test_admin_key_auth.py`

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_admin_key_auth.py`：
```python
import pytest
from fastapi import FastAPI, Depends
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_admin_key_valid(monkeypatch):
    monkeypatch.setenv("HUB_ADMIN_KEY", "test_admin_key_xyz")
    from hub import config
    config._settings = None
    from hub.auth.admin_key import require_admin_key

    app = FastAPI()

    @app.get("/admin/test")
    async def endpoint(_=Depends(require_admin_key)):
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/admin/test", headers={"X-HUB-Admin-Key": "test_admin_key_xyz"})
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_key_missing(monkeypatch):
    monkeypatch.setenv("HUB_ADMIN_KEY", "test_admin_key_xyz")
    from hub import config
    config._settings = None
    from hub.auth.admin_key import require_admin_key

    app = FastAPI()
    @app.get("/admin/test")
    async def endpoint(_=Depends(require_admin_key)):
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/admin/test")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_key_wrong(monkeypatch):
    monkeypatch.setenv("HUB_ADMIN_KEY", "test_admin_key_xyz")
    from hub import config
    config._settings = None
    from hub.auth.admin_key import require_admin_key

    app = FastAPI()
    @app.get("/admin/test")
    async def endpoint(_=Depends(require_admin_key)):
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/admin/test", headers={"X-HUB-Admin-Key": "wrong"})
        assert resp.status_code == 403
```

- [ ] **Step 2: 扩展 hub.config 支持 admin_key**

修改 `backend/hub/config.py`，在 Settings 类内追加：
```python
admin_key: str | None = Field(default=None, description="紧急 admin API Key（运维专用）")
```

- [ ] **Step 3: 实现 admin_key 鉴权依赖**

文件 `backend/hub/auth/admin_key.py`：
```python
"""HUB admin API Key 鉴权（紧急运维用，与 ERP session 并存）。

使用场景：
- 启动期间还没用户态时，用 admin key 调内部接口
- 自动化脚本批量操作

设计：
- 静态 ApiKey 配在 .env: HUB_ADMIN_KEY
- 请求头 X-HUB-Admin-Key
- 缺失 → 401；不匹配 → 403
"""
from __future__ import annotations
import secrets
from fastapi import Header, HTTPException
from hub.config import get_settings


async def require_admin_key(x_hub_admin_key: str | None = Header(default=None)):
    """FastAPI 依赖：校验 X-HUB-Admin-Key 头。"""
    if x_hub_admin_key is None:
        raise HTTPException(status_code=401, detail="缺少 X-HUB-Admin-Key 头")
    expected = get_settings().admin_key
    if not expected:
        raise HTTPException(status_code=503, detail="HUB_ADMIN_KEY 未配置")
    # 常时间比较防 timing attack
    if not secrets.compare_digest(x_hub_admin_key, expected):
        raise HTTPException(status_code=403, detail="ApiKey 无效")
    return True
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_admin_key_auth.py -v
```
期望：3 个测试 PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/hub/auth/admin_key.py backend/hub/config.py backend/tests/test_admin_key_auth.py
git commit -m "feat(hub): admin API Key 鉴权依赖（X-HUB-Admin-Key + timing-safe 比对）"
```

---

## Task 11：RBAC 种子数据（6 预设角色 + 全部权限码）

**Files:**
- Create: `backend/hub/seed.py`
- Modify: `backend/main.py`（lifespan 调用 seed）
- Test: `backend/tests/test_seed.py`

- [ ] **Step 1: 写种子测试**

文件 `backend/tests/test_seed.py`：
```python
import pytest


@pytest.mark.asyncio
async def test_seed_creates_6_roles(setup_db):
    from hub.seed import run_seed
    from hub.models import HubRole
    await run_seed()
    roles = await HubRole.all()
    codes = {r.code for r in roles}
    expected = {
        "platform_admin", "platform_ops", "platform_viewer",
        "bot_user_basic", "bot_user_sales", "bot_user_finance",
    }
    assert expected.issubset(codes)


@pytest.mark.asyncio
async def test_seed_creates_all_permissions(setup_db):
    from hub.seed import run_seed
    from hub.models import HubPermission
    await run_seed()
    perms = await HubPermission.all()
    codes = {p.code for p in perms}
    # 至少包含 spec §7.4 列出的核心权限码
    must_have = {
        "platform.tasks.read", "platform.flags.write", "platform.users.write",
        "platform.alerts.write", "platform.audit.read", "platform.audit.system_read",
        "platform.conversation.monitor", "platform.apikeys.write",
        "downstream.erp.use",
        "usecase.query_product.use", "usecase.query_customer_history.use",
        "channel.dingtalk.use",
    }
    assert must_have.issubset(codes)


@pytest.mark.asyncio
async def test_seed_idempotent(setup_db):
    """跑两次种子结果不变（不重复创建）。"""
    from hub.seed import run_seed
    from hub.models import HubRole, HubPermission
    await run_seed()
    n_roles_1 = await HubRole.all().count()
    n_perms_1 = await HubPermission.all().count()
    await run_seed()  # 再跑一次
    n_roles_2 = await HubRole.all().count()
    n_perms_2 = await HubPermission.all().count()
    assert n_roles_1 == n_roles_2
    assert n_perms_1 == n_perms_2


@pytest.mark.asyncio
async def test_seed_role_permission_links(setup_db):
    """platform_admin 应有所有 platform.* 权限。"""
    from hub.seed import run_seed
    from hub.models import HubRole
    await run_seed()
    admin = await HubRole.get(code="platform_admin").prefetch_related("permissions")
    perms = [p async for p in admin.permissions]
    platform_perms = [p for p in perms if p.code.startswith("platform.")]
    assert len(platform_perms) >= 8  # 至少覆盖所有 platform.* 权限码
```

- [ ] **Step 2: 实现 seed.py（覆盖 Task 9 创建的 stub）**

文件 `backend/hub/seed.py`（覆盖 Task 9 的 stub）：
```python
"""启动时跑预设角色 + 权限码种子（幂等）。"""
from __future__ import annotations
from hub.models import HubRole, HubPermission


# 全部权限码（spec §7.4）
PERMISSIONS = [
    # platform.*
    ("platform.tasks.read", "platform", "tasks", "read", "查看任务记录",
     "可以在后台看到每次机器人调用的详细执行记录"),
    ("platform.flags.write", "platform", "flags", "write", "调整功能开关",
     "可以打开或关闭系统的某些功能模块"),
    ("platform.users.write", "platform", "users", "write", "管理后台用户",
     "可以在后台添加用户、分配角色"),
    ("platform.alerts.write", "platform", "alerts", "write", "配置告警接收人",
     "可以设置出问题时通知谁"),
    ("platform.audit.read", "platform", "audit", "read", "查看操作日志",
     "可以看到管理员们的操作历史"),
    ("platform.audit.system_read", "platform", "audit", "system_read", "查看系统级审计",
     "可以看到 '谁查看了用户对话' 等敏感审计"),
    ("platform.conversation.monitor", "platform", "conversation", "monitor", "对话监控",
     "可以查看用户与机器人的实时对话和历史对话内容"),
    ("platform.apikeys.write", "platform", "apikeys", "write", "管理 API 密钥",
     "可以创建、吊销、查看下游系统对接密钥"),
    # downstream.*
    ("downstream.erp.use", "downstream", "erp", "use", "使用 ERP 数据",
     "允许机器人访问 ERP 系统的客户、商品、订单等数据"),
    # usecase.*
    ("usecase.query_product.use", "usecase", "query_product", "use", "商品查询",
     "允许在钉钉用机器人查询商品信息"),
    ("usecase.query_customer_history.use", "usecase", "query_customer_history", "use",
     "客户历史价查询", "允许查询某客户的历史成交价"),
    ("usecase.generate_contract.use", "usecase", "generate_contract", "use", "合同生成",
     "允许在钉钉用机器人自动生成销售合同（B 阶段启用）"),
    ("usecase.create_voucher.use", "usecase", "create_voucher", "use", "凭证生成",
     "允许审批通过的报销/付款自动生成会计凭证（D 阶段启用）"),
    # channel.*
    ("channel.dingtalk.use", "channel", "dingtalk", "use", "使用钉钉接入",
     "允许通过钉钉机器人交互"),
]


# 6 预设角色 + 权限映射
ROLES = {
    "platform_admin": {
        "name": "HUB 系统管理员",
        "description": "拥有所有功能权限，可以管理用户、角色、系统配置",
        "permissions": [p[0] for p in PERMISSIONS],  # 全部
    },
    "platform_ops": {
        "name": "运维人员",
        "description": "可以查看任务记录、调整系统开关、配置告警接收人，但不能管理用户",
        "permissions": [
            "platform.tasks.read", "platform.flags.write",
            "platform.alerts.write", "platform.audit.read",
        ],
    },
    "platform_viewer": {
        "name": "只读观察员",
        "description": "只能查看任务记录和操作日志，不能做任何修改",
        "permissions": ["platform.tasks.read", "platform.audit.read"],
    },
    "bot_user_basic": {
        "name": "机器人 - 基础查询",
        "description": "可以在钉钉里让机器人查商品、查客户、查报价",
        "permissions": [
            "channel.dingtalk.use",
            "downstream.erp.use",
            "usecase.query_product.use",
            "usecase.query_customer_history.use",
        ],
    },
    "bot_user_sales": {
        "name": "机器人 - 销售（B 阶段启用）",
        "description": "在 '基础查询' 之上，还可以让机器人生成销售合同",
        "permissions": [
            "channel.dingtalk.use",
            "downstream.erp.use",
            "usecase.query_product.use",
            "usecase.query_customer_history.use",
            "usecase.generate_contract.use",
        ],
    },
    "bot_user_finance": {
        "name": "机器人 - 财务（D 阶段启用）",
        "description": "在 '基础查询' 之上，还可以让机器人自动生成报销/付款凭证",
        "permissions": [
            "channel.dingtalk.use",
            "downstream.erp.use",
            "usecase.query_product.use",
            "usecase.create_voucher.use",
        ],
    },
}


async def run_seed():
    """幂等：已存在则跳过，新增则插入。"""
    # 1. 权限码
    perm_objs = {}
    for code, resource, sub, action, name, desc in PERMISSIONS:
        perm, _ = await HubPermission.get_or_create(
            code=code,
            defaults={
                "resource": resource, "sub_resource": sub, "action": action,
                "name": name, "description": desc,
            },
        )
        perm_objs[code] = perm

    # 2. 角色 + 权限关联
    for role_code, info in ROLES.items():
        role, _ = await HubRole.get_or_create(
            code=role_code,
            defaults={
                "name": info["name"], "description": info["description"],
                "is_builtin": True,
            },
        )
        # 同步权限关联（只增不减）
        existing_codes = {p.code async for p in role.permissions}
        for pcode in info["permissions"]:
            if pcode not in existing_codes:
                await role.permissions.add(perm_objs[pcode])
```

- [ ] **Step 3: lifespan 调用 seed（已在 Task 9 内置，本步仅为校验）**

确认 `backend/main.py:lifespan` 在 `await init_db()` 之后已经包含：
```python
    from hub.seed import run_seed
    await run_seed()
```
（如果按 Task 9 的代码块直接复制，这部分已经存在；本 Step 只是验证。）

- [ ] **Step 4: 跑种子测试**

```bash
pytest tests/test_seed.py -v
```
期望：4 个测试 PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/hub/seed.py backend/main.py backend/tests/test_seed.py
git commit -m "feat(hub): RBAC 种子（6 预设角色 + 14 权限码 + 关联，幂等）"
```

---

## Task 12：Setup Wizard 路由骨架

**Files:**
- Create: `backend/hub/routers/setup.py`
- Modify: `backend/main.py`（注册 setup router）
- Test: `backend/tests/test_setup_wizard_skeleton.py`

本 Task 实现**步骤 1（系统自检）+ token 校验路由骨架**。步骤 2-6 由 Plan 5 实现具体业务。

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_setup_wizard_skeleton.py`：
```python
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def app_client(setup_db):
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_setup_welcome_returns_self_check(app_client):
    resp = await app_client.get("/hub/v1/setup/welcome")
    assert resp.status_code == 200
    body = resp.json()
    assert "checks" in body
    assert "postgres" in body["checks"]
    assert "redis" in body["checks"]
    assert "master_key" in body["checks"]


@pytest.mark.asyncio
async def test_setup_verify_token_rejects_wrong(app_client):
    resp = await app_client.post("/hub/v1/setup/verify-token", json={"token": "wrong"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_setup_verify_token_accepts_valid(app_client):
    from hub.auth.bootstrap_token import generate_token
    plain = await generate_token(ttl_seconds=300)
    resp = await app_client.post("/hub/v1/setup/verify-token", json={"token": plain})
    assert resp.status_code == 200
    body = resp.json()
    assert "session" in body  # 简单 cookie / token 后续步骤用


@pytest.mark.asyncio
async def test_setup_blocked_after_initialized(app_client):
    """system_initialized=true 后所有 /setup/* 路由应返回 404。"""
    from hub.models import SystemConfig
    await SystemConfig.create(key="system_initialized", value=True)
    resp = await app_client.get("/hub/v1/setup/welcome")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_setup_token_one_time_use(app_client):
    """一次性语义：同一 token 第二次 verify 应失败（消费后失效）。"""
    from hub.auth.bootstrap_token import generate_token
    plain = await generate_token(ttl_seconds=300)

    # 第一次 verify 成功
    resp1 = await app_client.post("/hub/v1/setup/verify-token", json={"token": plain})
    assert resp1.status_code == 200
    assert "session" in resp1.json()

    # 第二次 verify 同一 token 应失败
    resp2 = await app_client.post("/hub/v1/setup/verify-token", json={"token": plain})
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_setup_token_concurrent_consume_only_one_wins(app_client):
    """并发场景：两个请求同时 verify 同一 token，只有一个能拿到 session。"""
    import asyncio
    from hub.auth.bootstrap_token import generate_token
    plain = await generate_token(ttl_seconds=300)

    async def attempt():
        return await app_client.post("/hub/v1/setup/verify-token", json={"token": plain})

    r1, r2 = await asyncio.gather(attempt(), attempt())
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 401], f"期望 [200, 401] 但拿到 {statuses}"
```

- [ ] **Step 2: 实现 setup router**

文件 `backend/hub/routers/setup.py`：
```python
"""初始化向导路由（仅在 system_initialized=false 时可用）。

本 Plan 实现 step 1（自检）+ token 校验骨架。
其余步骤（注册 ERP / 创建 admin / 钉钉 / AI / 完成）由 Plan 5 实现。
"""
from __future__ import annotations
import secrets
from fastapi import APIRouter, HTTPException, Request, Body
from pydantic import BaseModel
from tortoise import connections
from hub.config import get_settings
from hub.auth.bootstrap_token import verify_and_consume_token
from hub.models import SystemConfig

router = APIRouter(prefix="/hub/v1/setup", tags=["setup"])


# 简单进程级 session 存储（PoC 阶段；Plan 5 升级为真正的 session）
_active_setup_sessions: dict[str, bool] = {}


async def _is_initialized() -> bool:
    cfg = await SystemConfig.filter(key="system_initialized").first()
    return bool(cfg and cfg.value is True)


@router.get("/welcome")
async def welcome(request: Request):
    """步骤 1：系统自检。"""
    if await _is_initialized():
        raise HTTPException(status_code=404, detail="HUB 已完成初始化")

    settings = get_settings()
    checks = {
        "master_key": "ok" if settings.master_key else "missing",
    }
    # Postgres
    try:
        conn = connections.get("default")
        await conn.execute_query("SELECT 1")
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"down: {e}"

    # Redis（简化：检查配置，不实际连）
    checks["redis"] = "configured" if settings.redis_url else "missing"

    return {
        "checks": checks,
        "next_step": "verify-token",
    }


class VerifyTokenRequest(BaseModel):
    token: str


@router.post("/verify-token")
async def verify_token_endpoint(payload: VerifyTokenRequest = Body(...)):
    """步骤 1.5：原子校验 + 消费初始化 token，通过后建立 setup session。

    并发安全：使用 verify_and_consume_token，两个并发请求同 token 只有一个赢。
    """
    if await _is_initialized():
        raise HTTPException(status_code=404, detail="HUB 已完成初始化")

    if not await verify_and_consume_token(payload.token):
        raise HTTPException(status_code=401, detail="初始化 Token 错误或已过期")

    session_id = secrets.token_urlsafe(16)
    _active_setup_sessions[session_id] = True
    return {"session": session_id}


@router.get("/status")
async def setup_status():
    """查询当前初始化进度（前端轮询用）。"""
    return {"initialized": await _is_initialized()}
```

- [ ] **Step 3: 在 main.py 注册 setup router**

修改 `backend/main.py`，在 health.router 注册之后追加：
```python
from hub.routers import setup
app.include_router(setup.router)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_setup_wizard_skeleton.py -v
```
期望：4 个测试 PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/hub/routers/setup.py backend/main.py backend/tests/test_setup_wizard_skeleton.py
git commit -m "feat(hub): Setup Wizard 路由骨架（welcome 自检 + verify-token）"
```

---

## Task 13：Worker 进程骨架

**Files:**
- Create: `backend/worker.py`
- Create: `backend/hub/worker_runtime.py`

Worker 业务逻辑由 Plan 4 填充；本 Task 仅起进程框架（消费 Redis Streams + 任务路由调度）。

- [ ] **Step 1: 实现 worker_runtime**

文件 `backend/hub/worker_runtime.py`：
```python
"""Worker 运行时：消费 Redis Streams，按 task_type 路由到 handler。

设计要点（为可测试性优化）：
- block_ms 可注入：测试用短 block 防止 xreadgroup 长阻塞
- redis_client 可注入：测试用 fakeredis；生产 None 时由 hub.config 创建
- 自创建 redis 才在退出时关闭；外部注入的不动
- 提供 run_once()：测试单步消费，避免 stop() 无法中断长 block 的问题
"""
from __future__ import annotations
import asyncio
import logging
from typing import Awaitable, Callable
from hub.queue import RedisStreamsRunner

logger = logging.getLogger("hub.worker")


TaskHandler = Callable[[dict], Awaitable[None]]


class WorkerRuntime:
    def __init__(
        self,
        *,
        group: str = "hub-workers",
        consumer: str | None = None,
        block_ms: int = 5000,
        redis_client=None,  # 注入 fakeredis 用于测试；None 时由 config 创建
    ):
        self.group = group
        self.consumer = consumer or f"worker-{id(self)}"
        self.block_ms = block_ms
        self._handlers: dict[str, TaskHandler] = {}
        self._stop = False
        self._redis_client = redis_client
        self._owns_redis = redis_client is None  # 自己 new 的才负责关

    def register(self, task_type: str, handler: TaskHandler) -> None:
        self._handlers[task_type] = handler

    def _get_redis(self):
        if self._redis_client is not None:
            return self._redis_client
        from redis.asyncio import Redis
        from hub.config import get_settings
        return Redis.from_url(get_settings().redis_url, decode_responses=False)

    async def _process_one(self, runner: RedisStreamsRunner) -> bool:
        """单步消费：拉一条消息并分发给 handler。

        Returns: True 如有消息处理，False 如 block 超时空返回。
        """
        msgs = await runner.read_one(self.group, self.consumer, block_ms=self.block_ms)
        if not msgs:
            return False
        msg_id, data = msgs[0]
        task_type = data.get("task_type")
        handler = self._handlers.get(task_type)
        if not handler:
            logger.warning(f"无 handler 处理 task_type={task_type}, msg_id={msg_id}")
            await runner.move_to_dead(self.group, msg_id, msg_data=data)
            return True
        try:
            await handler(data)
            await runner.ack(self.group, msg_id)
        except Exception as e:
            logger.exception(f"task {task_type} 失败: {e}")
            # 简化：直接死信；Plan 4 加重试次数控制
            await runner.move_to_dead(self.group, msg_id, msg_data=data)
        return True

    async def run_once(self, runner: RedisStreamsRunner | None = None) -> bool:
        """跑一轮消费（测试用）。"""
        if runner is None:
            redis = self._get_redis()
            runner = RedisStreamsRunner(redis_client=redis)
            await runner.ensure_consumer_group(self.group)
        return await self._process_one(runner)

    async def run(self) -> None:
        redis = self._get_redis()
        runner = RedisStreamsRunner(redis_client=redis)
        await runner.ensure_consumer_group(self.group)

        logger.info(f"Worker {self.consumer} 启动，已注册 task_types: {list(self._handlers)}")

        try:
            while not self._stop:
                try:
                    await self._process_one(runner)
                except Exception:
                    logger.exception("worker loop 错误，1 秒后重试")
                    await asyncio.sleep(1)
        finally:
            if self._owns_redis:
                await redis.aclose()

    async def stop(self):
        self._stop = True
```

- [ ] **Step 2: 实现 worker.py 入口**

文件 `backend/worker.py`：
```python
"""HUB Worker 进程入口。"""
import asyncio
import logging
from hub.database import init_db, close_db
from hub.worker_runtime import WorkerRuntime


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hub.worker")


async def main():
    await init_db()
    try:
        runtime = WorkerRuntime()
        # Plan 4 在这里注册具体 handler，例如：
        # from hub.usecases.query_product import handler as query_product_handler
        # runtime.register("query_product", query_product_handler)
        await runtime.run()
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: 写 worker_runtime 测试**

文件 `backend/tests/test_worker_runtime.py`：
```python
import pytest
from fakeredis import aioredis as fakeredis_aio


@pytest.fixture
async def fake_redis():
    client = fakeredis_aio.FakeRedis()
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_handler_invoked_and_acked(fake_redis):
    """注册 handler 后投递任务 → run_once 拉到 → handler 被调用 → ACK。

    用 run_once + 注入 redis_client + 短 block_ms 避免阻塞和 client 关闭问题。
    """
    from hub.queue import RedisStreamsRunner
    from hub.worker_runtime import WorkerRuntime

    runner = RedisStreamsRunner(redis_client=fake_redis)
    await runner.submit("noop_task", {"k": "v"})
    await runner.ensure_consumer_group("hub-workers")

    runtime = WorkerRuntime(
        consumer="test-consumer-1",
        block_ms=100,           # 短 block：拉不到立刻返回
        redis_client=fake_redis,  # 外部注入，runtime 不会 close
    )

    received: dict = {}
    async def handler(msg_data: dict):
        received.update(msg_data.get("payload", {}))
    runtime.register("noop_task", handler)

    handled = await runtime.run_once(runner)
    assert handled is True
    assert received == {"k": "v"}

    # ACK 后 PEL 应为空（仍可访问 fake_redis，因为 runtime 没关它）
    pending = await runner.pending_count("hub-workers")
    assert pending == 0


@pytest.mark.asyncio
async def test_unknown_task_type_goes_to_dead_stream(fake_redis):
    """未注册的 task_type → 直接进死信流。"""
    from hub.queue import RedisStreamsRunner
    from hub.worker_runtime import WorkerRuntime

    runner = RedisStreamsRunner(redis_client=fake_redis)
    await runner.submit("unknown_task", {})
    await runner.ensure_consumer_group("hub-workers")

    runtime = WorkerRuntime(
        consumer="test-consumer-2",
        block_ms=100,
        redis_client=fake_redis,
    )
    # 不注册任何 handler

    handled = await runtime.run_once(runner)
    assert handled is True

    dead_count = await fake_redis.xlen("hub:tasks:dead")
    assert dead_count == 1


@pytest.mark.asyncio
async def test_run_once_returns_false_on_empty(fake_redis):
    """流空时 run_once block 短超时后返回 False，不阻塞。"""
    from hub.queue import RedisStreamsRunner
    from hub.worker_runtime import WorkerRuntime

    runner = RedisStreamsRunner(redis_client=fake_redis)
    await runner.ensure_consumer_group("hub-workers")

    runtime = WorkerRuntime(
        consumer="test-consumer-3",
        block_ms=50,
        redis_client=fake_redis,
    )
    handled = await runtime.run_once(runner)
    assert handled is False
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_worker_runtime.py -v
```
期望：3 个测试 PASS（handler+ACK / unknown→死信 / 空流 run_once 返回 False）。

- [ ] **Step 5: 提交**

```bash
git add backend/worker.py backend/hub/worker_runtime.py backend/tests/test_worker_runtime.py
git commit -m "feat(hub): Worker 进程骨架（消费 Redis Streams + task_type 路由调度）+ 单测"
```

---

## Task 14：Docker 化（4 容器 + 资源限额 + AOF）

**Files:**
- Create: `Dockerfile.gateway`
- Create: `Dockerfile.worker`
- Create: `docker-compose.yml`
- Create: `infra/redis.conf`

- [ ] **Step 1: 创建 Dockerfile.gateway**

**关键：先 COPY 整个 backend/ 源码再 pip install -e .**——pyproject 里 setuptools `packages.find` 需要扫描 `hub/` 目录才能确定包结构；只 COPY pyproject 会导致 pip editable 安装失败。**也不复制 `aerich.ini`**（本 plan 不创建该文件，aerich 配置在 pyproject 中）。

文件 `/Users/lin/Desktop/hub/Dockerfile.gateway`：
```dockerfile
FROM python:3.11-slim

WORKDIR /app
# 先 COPY 整个 backend 源码再安装（setuptools 需扫包结构）
COPY backend/ ./

RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
EXPOSE 8091

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8091"]
```

- [ ] **Step 2: 创建 Dockerfile.worker**

文件 `/Users/lin/Desktop/hub/Dockerfile.worker`：
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY backend/ ./
RUN pip install --no-cache-dir -e .
ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
CMD ["python", "worker.py"]
```

- [ ] **Step 2.5: 创建 Dockerfile.migrate（一次性迁移容器）**

迁移必须在 gateway / worker 启动**之前**跑完，否则 lifespan 里的 seed 会因表不存在失败。用一个 short-lived 的 migrate 容器跑 `aerich upgrade` 然后退出。

文件 `/Users/lin/Desktop/hub/Dockerfile.migrate`：
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY backend/ ./
RUN pip install --no-cache-dir -e .
ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
# aerich 从 hub.database.TORTOISE_ORM 读 db_url（环境变量在容器启动时注入）
CMD ["sh", "-c", "aerich upgrade && echo 'migrations done'"]
```

- [ ] **Step 3: 创建 redis.conf（开 AOF）**

文件 `/Users/lin/Desktop/hub/infra/redis.conf`：
```
appendonly yes
appendfsync everysec
maxmemory 512mb
maxmemory-policy noeviction
```

- [ ] **Step 4: 创建 docker-compose.yml（含 hub-migrate 一次性服务）**

**关键：** gateway / worker 必须等 hub-migrate **成功完成** 后才启动，否则 init_db + seed 会因表不存在失败。用 `depends_on: { condition: service_completed_successfully }`（Docker Compose 2.x 支持）。

文件 `/Users/lin/Desktop/hub/docker-compose.yml`：
```yaml
networks:
  hub-net:
    driver: bridge

services:
  hub-postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: hub
      POSTGRES_PASSWORD: ${HUB_POSTGRES_PASSWORD:-hub}
      POSTGRES_DB: hub
      TZ: Asia/Shanghai
    volumes:
      - hub_pgdata:/var/lib/postgresql/data
    networks:
      - hub-net
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U hub"]
      interval: 5s
      timeout: 3s
      retries: 5
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 1g
    restart: unless-stopped

  hub-redis:
    image: redis:7
    volumes:
      - ./infra/redis.conf:/usr/local/etc/redis/redis.conf:ro
      - hub_redisdata:/data
    command: ["redis-server", "/usr/local/etc/redis/redis.conf"]
    networks:
      - hub-net
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512m
    restart: unless-stopped

  # 一次性迁移容器：跑完 aerich upgrade 即退出
  hub-migrate:
    build:
      context: .
      dockerfile: Dockerfile.migrate
    environment:
      HUB_DATABASE_URL: postgresql://hub:${HUB_POSTGRES_PASSWORD:-hub}@hub-postgres:5432/hub
    depends_on:
      hub-postgres:
        condition: service_healthy
    networks:
      - hub-net
    restart: "no"  # 跑完就退出，不重启

  hub-gateway:
    build:
      context: .
      dockerfile: Dockerfile.gateway
    env_file:
      - .env
    environment:
      HUB_DATABASE_URL: postgresql://hub:${HUB_POSTGRES_PASSWORD:-hub}@hub-postgres:5432/hub
      HUB_REDIS_URL: redis://hub-redis:6379/0
    ports:
      - "8091:8091"
    depends_on:
      hub-migrate:
        condition: service_completed_successfully
      hub-postgres:
        condition: service_healthy
      hub-redis:
        condition: service_healthy
    networks:
      - hub-net
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512m
    restart: unless-stopped

  hub-worker:
    build:
      context: .
      dockerfile: Dockerfile.worker
    env_file:
      - .env
    environment:
      HUB_DATABASE_URL: postgresql://hub:${HUB_POSTGRES_PASSWORD:-hub}@hub-postgres:5432/hub
      HUB_REDIS_URL: redis://hub-redis:6379/0
    depends_on:
      hub-migrate:
        condition: service_completed_successfully
      hub-postgres:
        condition: service_healthy
      hub-redis:
        condition: service_healthy
    networks:
      - hub-net
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 1g
    restart: unless-stopped

volumes:
  hub_pgdata:
  hub_redisdata:
```

- [ ] **Step 5: 验证 docker compose 可起**

```bash
cd /Users/lin/Desktop/hub
# 用 openssl 生成 master key 写入 .env
echo "HUB_MASTER_KEY=$(openssl rand -hex 32)" > .env
echo "HUB_POSTGRES_PASSWORD=hub" >> .env

orb start
docker compose up -d
docker compose ps
```
期望：4 容器全部 Running。

```bash
# 等 5 秒让服务起好，然后健康检查
sleep 5
curl -s http://localhost:8091/hub/v1/health | python3 -m json.tool
```
期望：返回 status 字段（不一定 healthy，但能返回 JSON）。

- [ ] **Step 6: 提交**

```bash
git add Dockerfile.gateway Dockerfile.worker Dockerfile.migrate \
        docker-compose.yml infra/redis.conf
git commit -m "feat(hub): Docker 化（4 服务 + 一次性 hub-migrate + 健康检查 depends_on + AOF）"
```

---

## Task 15：自审 + 端到端验证

- [ ] **Step 1: 整体跑通测试套件**

```bash
cd /Users/lin/Desktop/hub/backend
pytest -v
```
期望：所有测试 PASS（约 30+ 条）。

- [ ] **Step 2: docker compose 完整启动验证**

```bash
cd /Users/lin/Desktop/hub
docker compose down -v  # 清干净
docker compose up -d
sleep 8
docker compose logs hub-gateway --tail 30
```
期望日志：
- `HUB Gateway 启动 - 端口 8091`
- 任何关于 Postgres / Redis 连接错误都不应该有

```bash
curl http://localhost:8091/hub/v1/health
```
期望：JSON，`status: healthy` 或 `degraded`（dingtalk_stream 等组件 not_started 是正常）。

- [ ] **Step 3: 验证 setup wizard 可访问 + setup token 已自动打印**

```bash
curl http://localhost:8091/hub/v1/setup/welcome
```
期望：返回 `checks` 字典。

```bash
docker compose logs hub-gateway | grep -A5 "初始化 Token"
```
期望：看到自动生成的 setup token（Task 9 lifespan 在数据库为空时自动生成并打印）。

- [ ] **Step 4: 提交最终验证记录**

文件 `docs/superpowers/plans/notes/2026-04-27-plan2-end-to-end-verification.md`（创建）：
```markdown
# Plan 2 端到端验证记录

日期：____
执行人：____

## 验证步骤
1. docker compose up：✅ / ❌
2. 4 容器 Running：✅ / ❌
3. /hub/v1/health 返回 200：✅ / ❌
4. /hub/v1/setup/welcome 返回 200：✅ / ❌
5. pytest 全绿：____ PASSED / ____ FAILED

## 已知缺口（留 Plan 3-5 处理）
- HUB_ADMIN_KEY 自动生成（首次部署）：仅在 .env 缺失时；建议运维显式设置
- ERP session 鉴权（基于 ERP /auth/me 包装 HUB session）：Plan 5 实现
- /setup/connect-erp 等步骤 2-6 业务逻辑：Plan 5
- 钉钉 Stream 实际连接：Plan 3
- 完整 Web 后台 UI（用户 / 角色 / 配置 / 任务流水 / 对话监控）：Plan 5

## 已在 Plan 2 实现（不再列为缺口）
- ✅ 自动生成 setup token 并打印日志：Task 9 lifespan 内置（数据库为空 + 无未使用 token 时触发）
- ✅ 启动时跑迁移：hub-migrate 一次性容器（gateway/worker depends_on completed_successfully）
- ✅ 启动时跑种子：lifespan 内调用 run_seed（幂等）
```

- [ ] **Step 5: 提交**

```bash
git add docs/superpowers/plans/notes/
git commit -m "docs(hub): Plan 2 端到端验证记录"
```

---

## Self-Review（v4，应用第三轮 review 反馈后）

### Spec 覆盖检查

| Spec 章节 | Plan 任务 | ✓ |
|---|---|---|
| §3 项目元数据（Python/FastAPI/Tortoise/Vue 等） | Task 1（pyproject + 顶层结构） | ✓ |
| §4.2 部署形态 + §4.2.1 同机部署 5 条隔离 | Task 14（独立 hub-net + 资源限额 + 端口错开 + 独立 Postgres） | ✓ |
| §5 6 个核心抽象接口 | Task 6（Protocol 定义） | ✓ |
| §6 数据模型 18 张表 | Task 4（16 张表，setup_token 由 BootstrapToken 实现） | ✓ |
| §13.1 Redis Streams 队列 | Task 7 | ✓ |
| §13.5 健康检查 | Task 9 | ✓ |
| §14.1 secret 分级 | Task 2 + Task 3（业务 secret 加密 + 基础 secret .env） | ✓ |
| §14.2 加密细节（AES-256-GCM + HKDF + bytea） | Task 3 | ✓ |
| §16.1 .env 模板 | Task 2 Step 4 | ✓ |
| §16.2 初始化向导（welcome + verify-token） | Task 12（步骤 1 完整 + 2-6 由 Plan 5） | ✓ |
| §16.3 HUB_SETUP_TOKEN 双路径 | Task 8（generate_token 支持 .env 显式 + 自动生成） | ✓ |
| §7 RBAC + 6 预设角色 + 14 权限码 | Task 11 | ✓ |
| 整体可运行 | Task 14 + 15（docker compose up + health 200） | ✓ |

### Placeholder Scan

- ✓ 无 "TODO" / "TBD" / "implement later"
- ✓ 所有 step 都有实际代码或命令
- ✓ TaskHandler 在 Plan 4 注册的位置已用占位注释（不影响 Plan 2 跑通）

### 类型一致性

- ✓ Settings.master_key（str）/ master_key_bytes（bytes，property）一致使用
- ✓ AES-GCM nonce 长度 12 字节常量化
- ✓ Tortoise 模型表名与 spec §6.2 完全对齐
- ✓ Protocol 接口签名（async + 关键字参数）跨文件一致
- ✓ docker-compose 中容器名（hub-gateway / hub-worker / hub-postgres / hub-redis）与 .env 中环境变量、CLAUDE.md / README 描述一致
- ✓ HUB Postgres / Redis 端口在容器外不映射（只内部 hub-net 通信），仅 8091 暴露宿主机；与 spec §4.2 表格一致

### 范围检查

Plan 2 完成后，HUB 是"骨架完整、可启动、可扩展、但无业务"的状态：
- ✅ 4 容器跑起来
- ✅ 健康检查 200
- ✅ 数据库表全部建好
- ✅ 6 预设角色 + 14 权限码已写入
- ✅ 加密 secret 系统就绪
- ✅ 测试基础设施完整
- ❌ 不接钉钉（Plan 3）
- ❌ 不调 ERP（Plan 3+4）
- ❌ 不实现具体业务用例（Plan 4）
- ❌ Web 后台 UI 仅有向导骨架（Plan 5）

### 与 Plan 1 的接口对齐

- HUB 调 ERP 时通过 `DownstreamSystem.encrypted_apikey` 取出 ApiKey + 加 `X-API-Key` 头（Plan 1 实现接收方）
- HUB 调 ERP `/internal/binding-codes/generate` 时用 system scope ApiKey（Plan 1 实现）
- HUB 接收 ERP `/internal/binding/confirm-final` 反向通知（Plan 3 实现接收路由）
- 模型 `DownstreamIdentity` 字段与 Plan 1 `dingtalk_binding_code` 表的 erp_user_id 对应

---

### v2 第一轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P1-A | pyproject.toml 缺 `[build-system]` + setuptools package discovery，pip editable 安装失败；`__init__.py` 也在 pip install 之后才创建 | (1) Task 1 加 Step 1.5 在 Step 3（pip install）之前先创建所有 `hub/__init__.py`；(2) pyproject 加 `[build-system] requires=["setuptools>=61", "wheel"]` + `[tool.setuptools.packages.find] include=["hub", "hub.*"]`；(3) Task 1 原 Step 4 重复创建 `__init__.py` 删除，Step 5-8 重新编号；(4) Dockerfile.gateway / .worker / .migrate 改为先 `COPY backend/ ./` 整个源码再 `pip install -e .`，不再单独 COPY pyproject + aerich.ini |
| P1-B | Task 4 写 smoke test 但依赖 setup_db（Task 5 才创建），且 git add 未含测试文件 | Task 4 删除 smoke test 创建步骤（仅创建模型）；Task 5 重构为"database + conftest + smoke test 同 commit"，跑通后一次性提交 4 个文件 + 迁移 |
| P1-C | aerich.ini 引用但未创建，TORTOISE_ORM 字面量 `${HUB_DATABASE_URL}` 不会展开 | (1) 不再创建 aerich.ini，aerich 配置统一在 pyproject `[tool.aerich]`；(2) `hub/database.py` `TORTOISE_ORM` 改为模块加载时从 `os.environ.get("HUB_DATABASE_URL")` 读取真实 URL；(3) Dockerfile 不再 COPY `aerich.ini`；(4) `aerich init` 命令前明确要求 `export HUB_DATABASE_URL` |
| P1-D | docker-compose 没跑迁移，gateway 启动时表不存在导致 seed 失败 | 新增 `Dockerfile.migrate`（一次性 `aerich upgrade`）+ docker-compose 增加 `hub-migrate` service；gateway / worker `depends_on: { hub-migrate: { condition: service_completed_successfully } }`；postgres / redis 加 healthcheck，gateway / worker 等其 healthy |
| P2-E | setup token 验收口径冲突（README/.env 让从日志拿，Task 15 又说是 Plan 3-5 缺口） | 在 Plan 2 实现自动生成：Task 9 lifespan 内检测"未初始化 + 无未使用 token"时自动生成并 logger.warning 打印；Task 15 Step 3 改为 grep "初始化 Token"；"已知缺口" 列表移除该项，新增"已在 Plan 2 实现" |

---

### v3 第二轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P1-V3-A | aerich init 引用不存在的 shell 变量 `$TEST_DATABASE_URL`，且会污染 Step 5 已 generate_schemas 过的测试库 | Task 5 Step 6 改写：起一个**独立的 aerich-init 数据库**（端口 5434 / 库名 hub_dev / 容器 hub-pg-aerich-init）专门跑 init-db；用真实完整 DSN `postgres://hub:hub@localhost:5434/hub_dev`，不引用任何不存在的 shell 变量；跑完后 docker rm 临时容器，保留 migrations/ 产物 |
| P2-V3-A | Task 9 lifespan 引用 `hub.seed.run_seed` 但 hub/seed.py 在 Task 11 才创建，commit 后启动 ImportError | Task 9 Step 2.5 新增创建 `hub/seed.py` stub（空 run_seed），Step 5 git add 包含；Task 11 Step 2 明确"覆盖 Task 9 的 stub" |
| P2-V3-B | verify-token 成功后没 mark_used，token 在 TTL 内可被重复换取多个 session | setup.py `verify_token_endpoint` 校验通过后立即 `await mark_used(payload.token)`；新增测试 `test_setup_token_one_time_use` 验证第二次 verify 同一 token 返回 401 |
| P2-V3-C | Worker 骨架无测试，违背 TDD 原则与 30+ 测试验收口径 | Task 13 新增 Step 3-5：创建 `tests/test_worker_runtime.py`（fakeredis + 注册 fake handler + 投递 + 断言被调用 + ACK；unknown task_type 进死信）；commit 包含测试文件 |

---

### v4 第三轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P1-V4-A | WorkerRuntime 测试 block_ms=5000 + stop() 无法中断 + run() 自动关 redis 导致测试卡住或不稳 | (1) `WorkerRuntime` 加 `block_ms` / `redis_client` 注入参数，加 `_owns_redis` 标志，外部注入的 client 不关；(2) 提取 `_process_one()` 单步消费方法 + 暴露 `run_once()` 给测试用；(3) 测试改用 `run_once + block_ms=100 + 注入 fake_redis`，不再用 `gather + timeout` 强退；(4) 加第三个测试 `test_run_once_returns_false_on_empty` 覆盖空流路径 |
| P2-V4-A | setup token 消费不原子，并发可让两个请求都拿到 session | (1) `bootstrap_token.py` 新增 `verify_and_consume_token()` —— 哈希命中后用 `UPDATE ... WHERE used_at IS NULL` 原子标记，0 行影响 = 输家；(2) `verify_token_endpoint` 改调 `verify_and_consume_token`；(3) 新增并发测试 `test_setup_token_concurrent_consume_only_one_wins` 验证 `[200, 401]` 而非 `[200, 200]` |
| P2-V4-B | aerich init `sleep 2` 等 Postgres 不稳 + 同名容器残留时 docker run 失败 | Task 5 Step 6 改为：(1) 先 `docker rm -f hub-pg-aerich-init 2>/dev/null \|\| true` 清理残留；(2) 用 `until docker exec ... pg_isready -U hub; do sleep 1; done` 轮询 readiness 替代 `sleep 2` |

---

**Plan 2 v4 结束（已修复 v1 + v2 + v3 + v4 四轮 review 反馈，共 12 处问题）**
