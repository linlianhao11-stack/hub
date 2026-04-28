# ERP 集成改动实施计划（Plan 1 / 5）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ERP-4 项目中实现 HUB 数据中台对接所需的 5 项后端/前端改动（ApiKey 鉴权、绑定码功能、历史成交价接口、模糊搜索盘点、调用审计），全部满足"零回归"硬约束。

**Architecture:** 所有改动通过 feature flag 包裹（默认关闭），代码物理隔离到独立子目录与文件，不修改现有路径行为。改动开关全关时 ERP 行为字节级一致；逐项打开后 HUB 才能使用。所有新表通过纯 CREATE TABLE 迁移引入，不动现有表 schema。

**Tech Stack:** Python 3.9+ / FastAPI / Tortoise ORM / PostgreSQL 16 / pytest (asyncio_mode=auto) / Vue 3 + Vite + Tailwind 4 / 原始 SQL 迁移

**前置阅读：** [HUB Spec §15 ERP 改动清单 + 零回归约束](../specs/2026-04-27-hub-middleware-design.md#15-erp-改动清单5-项--零回归约束)

**估时：** 4-5 天

---

## 文件结构

### Backend 新增

| 文件 | 职责 |
|---|---|
| `backend/app/integration/__init__.py` | 集成模块入口（HUB 集成所有新代码物理隔离在此目录） |
| `backend/app/integration/feature_flags.py` | 4 个 feature flag 的统一定义 + env var 解析 |
| `backend/app/integration/models/__init__.py` | 模型导出 |
| `backend/app/integration/models/service_account.py` | ServiceAccount + ApiKey 模型 |
| `backend/app/integration/models/dingtalk_binding.py` | DingTalkBindingCode + DingTalkBinding 模型 |
| `backend/app/integration/models/service_call_log.py` | ServiceCallLog 审计日志模型 |
| `backend/app/integration/auth/__init__.py` | - |
| `backend/app/integration/auth/api_key.py` | ApiKey 鉴权依赖（独立分支，不与 JWT 共享代码） |
| `backend/app/integration/auth/scopes.py` | scope 常量 + 校验函数 |
| `backend/app/integration/middleware/audit.py` | 调用审计中间件 |
| `backend/app/integration/routers/admin_api_keys.py` | ApiKey 管理 admin 接口 |
| `backend/app/integration/routers/internal_dingtalk.py` | 内部接口：generate-binding-code / users/exists / users/{id}/active-state |
| `backend/app/integration/routers/internal_binding.py` | ERP 个人中心绑定页面用：verify-token / confirm-final |
| `backend/app/integration/routers/customer_prices.py` | 历史成交价接口 |
| `backend/app/migrations/v058_hub_integration.py` | 4 张新表 + 索引（纯 CREATE TABLE，Python `up()/down()` 形式） |

### Backend 修改

| 文件 | 修改 |
|---|---|
| `backend/app/auth/dependencies.py` | `_authenticate_user` 函数前加 X-API-Key 早返回分支（同时 import `Request`） |
| `backend/app/config.py` | 追加 4 个 feature flag 引用注释（实际值定义在 `app.integration.feature_flags`） |
| `backend/main.py` | 按 flag 条件注册新 router；安装审计中间件 |
| `backend/app/database.py` | `Tortoise.init` 的 `modules` 字典追加 `app.integration.models`（无条件，让 4 个 integration 模型在 ORM 注册） |

### Frontend 新增

| 文件 | 职责 |
|---|---|
| `frontend/src/views/system/ApiKeyManagement.vue` | ApiKey 管理页（系统设置 - API 密钥管理） |
| `frontend/src/views/profile/DingTalkBindingTab.vue` | 个人中心钉钉绑定 tab |
| `frontend/src/components/dingtalk/BindingConfirmDialog.vue` | 二次确认对话框 |
| `frontend/src/api/integration.js` | HUB 集成相关前端 API 封装 |

### Frontend 修改

| 文件 | 修改 |
|---|---|
| `frontend/src/views/SettingsView.vue` 或路由配置 | 在系统设置加一个 ApiKey 管理入口（受 flag 控制） |
| 个人中心 view（按现有 ERP 布局适配） | 加"钉钉绑定" tab（受 flag 控制） |

### 测试新增

| 文件 | 职责 |
|---|---|
| `backend/tests/test_integration_feature_flags.py` | flag 开关行为测试 |
| `backend/tests/test_integration_apikey_model.py` | ApiKey 模型测试 |
| `backend/tests/test_integration_apikey_auth.py` | ApiKey 鉴权依赖测试 |
| `backend/tests/test_integration_admin_apikey.py` | ApiKey admin 接口测试 |
| `backend/tests/test_integration_binding_code.py` | 绑定码生成 + 验证测试 |
| `backend/tests/test_integration_customer_prices.py` | 历史成交价接口测试 |
| `backend/tests/test_integration_audit.py` | 审计中间件测试 |
| `backend/tests/test_integration_no_regression.py` | 零回归证据链 |

---

## Task 1：分支创建 + Feature Flag 基础设施

**Files:**
- Create: `backend/app/integration/__init__.py`
- Create: `backend/app/integration/feature_flags.py`
- Modify: `backend/app/config.py`（追加 4 个 flag 引用注释）
- Test: `backend/tests/test_integration_feature_flags.py`

- [ ] **Step 1: 创建 feature 分支**

```bash
cd /Users/lin/Desktop/ERP-4
git checkout -b feat/hub-integration
git status  # 期望: clean working tree on feat/hub-integration
```

- [ ] **Step 2: 创建 integration 子目录并加占位 __init__.py**

文件 `backend/app/integration/__init__.py`：
```python
"""HUB 集成模块。所有改动在此子目录内物理隔离，受 feature flag 控制。"""
```

- [ ] **Step 3: 写 feature_flags.py 失败测试**

文件 `backend/tests/test_integration_feature_flags.py`：
```python
import os
import importlib
from unittest.mock import patch


def test_all_flags_default_false():
    """验证 4 个 flag 默认值（audit 默认 True，其余 False）。"""
    with patch.dict(os.environ, {}, clear=False):
        for key in ['ENABLE_API_KEY_AUTH', 'ENABLE_DINGTALK_BINDING',
                    'ENABLE_HUB_CUSTOMER_PRICES']:
            os.environ.pop(key, None)
        os.environ.pop('ENABLE_SERVICE_CALL_AUDIT', None)
        from app.integration import feature_flags
        importlib.reload(feature_flags)
        assert feature_flags.ENABLE_API_KEY_AUTH is False
        assert feature_flags.ENABLE_DINGTALK_BINDING is False
        assert feature_flags.ENABLE_HUB_CUSTOMER_PRICES is False
        assert feature_flags.ENABLE_SERVICE_CALL_AUDIT is True


def test_flag_parses_truthy():
    with patch.dict(os.environ, {'ENABLE_API_KEY_AUTH': '1'}):
        from app.integration import feature_flags
        importlib.reload(feature_flags)
        assert feature_flags.ENABLE_API_KEY_AUTH is True

    for v in ['true', 'TRUE', 'yes', 'Yes', 'on']:
        with patch.dict(os.environ, {'ENABLE_API_KEY_AUTH': v}):
            importlib.reload(feature_flags)
            assert feature_flags.ENABLE_API_KEY_AUTH is True


def test_flag_parses_falsy():
    for v in ['', '0', 'false', 'no', 'off']:
        with patch.dict(os.environ, {'ENABLE_API_KEY_AUTH': v}):
            from app.integration import feature_flags
            importlib.reload(feature_flags)
            assert feature_flags.ENABLE_API_KEY_AUTH is False
```

- [ ] **Step 4: 运行测试确认失败**

```bash
cd /Users/lin/Desktop/ERP-4/backend
pytest tests/test_integration_feature_flags.py -v
```
期望输出：`ModuleNotFoundError: No module named 'app.integration.feature_flags'`

- [ ] **Step 5: 实现 feature_flags.py**

文件 `backend/app/integration/feature_flags.py`：
```python
"""HUB 集成 feature flag 集中定义。

每个 flag 通过环境变量控制，默认值见下方。flag 关闭时对应代码路径不生效，
ERP 行为与未引入本模块时字节级一致——这是"零回归"约束的根基。
"""
import os


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


# ApiKey 鉴权（HUB 调 ERP 业务接口必需）
ENABLE_API_KEY_AUTH = _parse_bool(os.environ.get('ENABLE_API_KEY_AUTH', ''))

# 钉钉绑定接口与个人中心绑定页面
ENABLE_DINGTALK_BINDING = _parse_bool(os.environ.get('ENABLE_DINGTALK_BINDING', ''))

# 历史成交价查询接口（无登录依赖，但 HUB 才会调用）
ENABLE_HUB_CUSTOMER_PRICES = _parse_bool(os.environ.get('ENABLE_HUB_CUSTOMER_PRICES', ''))

# 调用审计中间件（默认开启，依赖 ENABLE_API_KEY_AUTH 才真正生效）
ENABLE_SERVICE_CALL_AUDIT = _parse_bool(os.environ.get('ENABLE_SERVICE_CALL_AUDIT', 'true'))
```

- [ ] **Step 6: 运行测试确认通过**

```bash
pytest tests/test_integration_feature_flags.py -v
```
期望：3 个测试全 PASS。

- [ ] **Step 7: 在 config.py 末尾加 flag 引用注释（不重复定义，只是引用提示）**

修改 `backend/app/config.py` 末尾添加：
```python
# === HUB 集成 feature flag（实际定义在 app.integration.feature_flags）===
# ENABLE_API_KEY_AUTH / ENABLE_DINGTALK_BINDING / ENABLE_HUB_CUSTOMER_PRICES /
# ENABLE_SERVICE_CALL_AUDIT
# 详见 docs/superpowers/specs/2026-04-27-hub-middleware-design.md §15
```

- [ ] **Step 8: 提交**

```bash
cd /Users/lin/Desktop/ERP-4
git add backend/app/integration/__init__.py backend/app/integration/feature_flags.py \
        backend/app/config.py backend/tests/test_integration_feature_flags.py
git commit -m "feat(integration): 引入 HUB 集成 feature flag 基础设施"
```

---

## Task 1.5：测试基础设施扩展（fixture + TABLES_TO_TRUNCATE）

**为什么先做：** 当前 ERP `backend/tests/conftest.py` 只提供 `client / test_user / auth_token / auth_client / base_master_data` 等 fixture。本 plan 后续 Task 引用的 `system_apikey / act_as_apikey / sample_orders_data / fresh_user_with_no_orders / normal_user_token` 都是新 fixture，必须先在 conftest 加好，否则 pytest 收集阶段就会失败。

同时新增 4 张表必须加入 `TABLES_TO_TRUNCATE`，否则测试间数据残留会污染断言。

**Files:**
- Modify: `backend/tests/conftest.py`（追加 fixture + 扩展 TABLES_TO_TRUNCATE）

**注意顺序：** conftest 的 `Tortoise.init(modules=[...])` 修改**不**在本 Task 完成（如果在这里改，但 Task 3 还没创建 `app.integration.models` 包，跑测试会 ModuleNotFoundError）。conftest 的 modules 修改放到 **Task 3 Step 4**，与 `database.py` 修改和模型创建同一个 commit。本 Task 只做 TABLES_TO_TRUNCATE + fixtures（这些都不依赖 integration.models 是否已创建）。

- [ ] **Step 1: TABLES_TO_TRUNCATE 加新表**

修改 `backend/tests/conftest.py:24` 起的 `TABLES_TO_TRUNCATE` 列表，**在最前面**（明细/子表先清的位置）追加新表：

```python
TABLES_TO_TRUNCATE = [
    # === HUB 集成（最先清，因为有 FK 依赖）===
    "service_call_log",
    "dingtalk_binding_code",
    "api_key",
    "service_account",
    # === 以下为原有表 ===
    # 明细 / 子表先清
    "dropship_return_items",
    # ... （保持原有）
]
```

注意顺序：service_call_log → dingtalk_binding_code → api_key → service_account（service_call_log.api_key_id 和 api_key.service_account_id 各自有 FK）。

- [ ] **Step 2: 在 conftest 末尾追加 fixtures**

**注意：** 本 Task 1.5 期间 `app.integration.models` 还不存在（Task 3 才创建），所以下面 fixtures 内部的 `from app.integration.models import ...` import 必须在**函数体内**做（延迟 import），不要放在文件顶部 module level——这样 fixture 定义时不会立刻 import，等到测试真正用到（Task 3 之后）才解析。这点本身已经满足（下面所有 fixture 都用函数内 import）。

在 `backend/tests/conftest.py` 末尾追加：

```python
# ============================================================
# HUB 集成测试 fixtures
# ============================================================

import secrets


@pytest.fixture
async def normal_user():
    """创建非 admin 普通用户（区别于 test_user 是 admin）。"""
    from app.models.user import User
    from app.auth.password import hash_password
    user = await User.create(
        username="testnormal",
        password_hash=hash_password("TestPass123"),
        display_name="测试普通用户",
        role="user",
        permissions=["sales", "customer"],
        is_active=True,
        must_change_password=False,
        token_version=0,
    )
    return user


@pytest.fixture
async def normal_user_token(normal_user):
    from app.auth.jwt import create_access_token
    return create_access_token(data={
        "user_id": normal_user.id,
        "username": normal_user.username,
        "role": normal_user.role,
        "token_version": normal_user.token_version,
    })


async def _create_apikey(scopes: list[str], name_prefix: str = "test"):
    """创建一把 ApiKey 并返回 (api_key 实例, 明文 key)。"""
    from app.integration.models import ServiceAccount, ApiKey
    from app.auth.password import async_hash_password

    sa = await ServiceAccount.create(name=f"{name_prefix}-{secrets.token_hex(4)}")
    plaintext = secrets.token_urlsafe(32)
    key = await ApiKey.create(
        service_account=sa,
        name=name_prefix,
        key_hash=await async_hash_password(plaintext),
        key_prefix=plaintext[:8],
        scopes=scopes,
    )
    return key, plaintext


@pytest.fixture
async def system_apikey():
    """带 system_calls scope 的 ApiKey 明文（用于调系统级白名单接口）。"""
    _, plaintext = await _create_apikey(["system_calls"], "sys")
    return plaintext


@pytest.fixture
async def act_as_apikey():
    """带 act_as_user scope 的 ApiKey 明文（用于代理用户调业务接口）。"""
    _, plaintext = await _create_apikey(["act_as_user"], "actas")
    return plaintext


@pytest.fixture
async def sample_orders_data(test_user, base_master_data):
    """构造一个客户 + 一个商品 + 3 笔订单的样本数据，用于历史成交价测试。

    base_master_data 返回 dict，含 'account_set' / 'warehouse' / 'customer' / 'product'
    等模型对象（不是 *_id 字段）。我们这里复用其中的 customer / product / account_set /
    warehouse，避免重复创建。OrderItem 必须含 cost_price 字段（NOT NULL）。
    """
    from app.models.order import Order, OrderItem
    from datetime import datetime, timezone, timedelta
    from decimal import Decimal

    account_set = base_master_data["account_set"]
    warehouse = base_master_data["warehouse"]
    customer = base_master_data["customer"]
    product = base_master_data["product"]

    base_dt = datetime.now(timezone.utc)
    cost_price = Decimal("60.000000")
    for i, price_str in enumerate(["95.000000", "98.000000", "99.000000"]):
        unit_price = Decimal(price_str)
        amount = Decimal(price_str)
        order = await Order.create(
            order_no=f"TEST-ORD-{i+1}",
            order_type="sale",
            customer=customer,
            account_set=account_set,
            warehouse=warehouse,
            total_amount=amount,
            created_at=base_dt - timedelta(days=i),
        )
        await OrderItem.create(
            order=order,
            product=product,
            quantity=1,
            unit_price=unit_price,
            cost_price=cost_price,
            amount=amount,
        )

    return {
        "user_id": test_user.id,
        "customer_id": customer.id,
        "product_id": product.id,
    }


@pytest.fixture
async def fresh_user_with_no_orders(test_user):
    """返回 (user_id, product_id, customer_id)，user 与 product/customer 之间没有任何订单。"""
    from app.models.customer import Customer
    from app.models.product import Product
    from decimal import Decimal

    customer = await Customer.create(
        name="无订单客户",
        balance=Decimal("0"),
        rebate_balance=Decimal("0"),
        is_active=True,
    )
    product = await Product.create(
        sku="NOORDER-SKU", name="无订单商品", unit="件",
        retail_price=Decimal("50.00"),
        cost_price=Decimal("30.00"),
        is_active=True,
    )
    return (test_user.id, product.id, customer.id)
```

`sample_orders_data` 复用 `base_master_data` 已经创建的 customer / product / account_set / warehouse 对象（注意是模型对象而非 `*_id` 字段），并必须给 OrderItem 传 `cost_price`（NOT NULL 字段）。

- [ ] **Step 3: 跑一次现有测试确认 conftest 改动不破坏现有测试**

```bash
pytest tests/ --ignore=tests/test_integration_*.py -x -q
```
期望：现有测试 100% 通过（一条不少）。本 Task 仅扩展了 TABLES_TO_TRUNCATE 列表与新增了延迟 import 的 fixture，对现有路径行为无影响（TABLES_TO_TRUNCATE 的 4 个新表名在 setup_db 阶段 `IF EXISTS` truncate，4 张表此时还没建出来——这正常，PG 的 `TRUNCATE TABLE IF EXISTS` 会跳过不存在的表；如果 ERP 现有 truncate 实现没用 IF EXISTS，则需在 Task 3 完成、表已建出来之后再跑此测试，本 Step 仅做 sanity check 现有路径）。

如果 ERP `setup_db` fixture 使用普通 `TRUNCATE TABLE name CASCADE` 而**没有** `IF EXISTS`，本步骤会因为 4 张表不存在而失败。这种情况下：
- 把这一步往后挪到 Task 3 完成、表已建好之后
- 或在本 Task 临时跑 `psql "$DATABASE_URL" -c "CREATE TABLE IF NOT EXISTS service_account (id SERIAL); ..."` 等 stub（不推荐，污染 dev 库）

实施者按 ERP `setup_db` 的实际写法决定。

- [ ] **Step 4: 提交**

```bash
git add backend/tests/conftest.py
git commit -m "test(integration): conftest 扩展（HUB 集成 fixture + TABLES_TO_TRUNCATE）"
```

---

## Task 2：版本化迁移文件（v058，4 张新表 + 索引）

**核心修正：** ERP 实际迁移 runner 在 `backend/app/migrations/runner.py`，扫描 `backend/app/migrations/v{NNN}_*.py` 文件，启动时自动调用 `up(conn)` 执行。**不**会扫描 `backend/migrations/*.sql`。当前最新版本是 v057，本次新增为 v058。

**Files:**
- Create: `backend/app/migrations/v058_hub_integration.py`

- [ ] **Step 1: 创建版本化迁移 Python 文件**

文件 `backend/app/migrations/v058_hub_integration.py`：
```python
"""v058: HUB 集成 - 新增 4 张表

新增表（不动现有表）:
- service_account
- api_key
- dingtalk_binding_code
- service_call_log

回滚: DROP 4 张表（外键依赖逆序）。down() 提供，但生产建议从备份恢复。
"""
from __future__ import annotations

from app.logger import get_logger

logger = get_logger("migrations")


async def up(conn) -> None:
    """创建 4 张 HUB 集成表 + 必要索引"""
    # 1. ServiceAccount
    await conn.execute_query("""
        CREATE TABLE IF NOT EXISTS service_account (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL UNIQUE,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # 2. ApiKey - key_prefix 必须 UNIQUE，审计中间件按 prefix 唯一定位 ApiKey
    await conn.execute_query("""
        CREATE TABLE IF NOT EXISTS api_key (
            id SERIAL PRIMARY KEY,
            service_account_id INTEGER NOT NULL REFERENCES service_account(id) ON DELETE CASCADE,
            name VARCHAR(100) NOT NULL,
            key_hash VARCHAR(255) NOT NULL,
            key_prefix VARCHAR(16) NOT NULL UNIQUE,
            scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
            expires_at TIMESTAMPTZ,
            last_used_at TIMESTAMPTZ,
            is_revoked BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at TIMESTAMPTZ
        )
    """)
    await conn.execute_query(
        "CREATE INDEX IF NOT EXISTS idx_api_key_service_account ON api_key(service_account_id)"
    )

    # 3. DingTalkBindingCode
    await conn.execute_query("""
        CREATE TABLE IF NOT EXISTS dingtalk_binding_code (
            id SERIAL PRIMARY KEY,
            code_hash VARCHAR(255) NOT NULL,
            erp_username VARCHAR(100) NOT NULL,
            dingtalk_userid VARCHAR(100) NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute_query(
        "CREATE INDEX IF NOT EXISTS idx_binding_code_username "
        "ON dingtalk_binding_code(erp_username) WHERE used_at IS NULL"
    )
    await conn.execute_query(
        "CREATE INDEX IF NOT EXISTS idx_binding_code_expires "
        "ON dingtalk_binding_code(expires_at) WHERE used_at IS NULL"
    )

    # 4. ServiceCallLog
    await conn.execute_query("""
        CREATE TABLE IF NOT EXISTS service_call_log (
            id BIGSERIAL PRIMARY KEY,
            api_key_id INTEGER REFERENCES api_key(id) ON DELETE SET NULL,
            api_key_prefix VARCHAR(16),
            acting_as_user_id INTEGER,
            method VARCHAR(10) NOT NULL,
            path VARCHAR(500) NOT NULL,
            status_code INTEGER NOT NULL,
            duration_ms INTEGER,
            ip VARCHAR(45),
            user_agent VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute_query(
        "CREATE INDEX IF NOT EXISTS idx_service_call_log_created_at "
        "ON service_call_log(created_at DESC)"
    )
    await conn.execute_query(
        "CREATE INDEX IF NOT EXISTS idx_service_call_log_api_key "
        "ON service_call_log(api_key_id, created_at DESC)"
    )

    logger.info("v058: HUB 集成 4 张表创建成功")


async def down(conn) -> None:
    """回滚（按外键依赖逆序 DROP）。生产建议从备份恢复，不要 down。"""
    await conn.execute_query("DROP TABLE IF EXISTS service_call_log")
    await conn.execute_query("DROP TABLE IF EXISTS dingtalk_binding_code")
    await conn.execute_query("DROP TABLE IF EXISTS api_key")
    await conn.execute_query("DROP TABLE IF EXISTS service_account")
    logger.warning("v058 down(): 已删除 4 张 HUB 集成表（数据丢失）")
```

- [ ] **Step 2: 启动 ERP 触发迁移自动执行**

```bash
cd /Users/lin/Desktop/ERP-4/backend
# 启动 ERP（按现有方式，会自动调 run_migrations 跑 v058）
python main.py  # 或 uvicorn main:app --port 8090
# 启动日志期望看到：
# "v058: HUB 集成 4 张表创建成功"
```

或者用一次性脚本（不启动整个 app，只跑迁移）：
```bash
python -c "
import asyncio
from app.database import init_db_connections, close_db
from app.migrations import run_migrations
async def main():
    await init_db_connections()
    await run_migrations()
    await close_db()
asyncio.run(main())
"
```

- [ ] **Step 3: 校验 4 张表已创建**

```bash
psql "$DATABASE_URL" -c "\dt service_account api_key dingtalk_binding_code service_call_log"
```
期望：4 张表都列出。

```bash
psql "$DATABASE_URL" -c "SELECT version FROM migration_history ORDER BY version DESC LIMIT 3"
```
期望：v058、v057、v056（v058 在最前）。

- [ ] **Step 4: 校验本迁移内只 CREATE TABLE 不 ALTER 现有表**

```bash
grep -ci "ALTER TABLE" backend/app/migrations/v058_hub_integration.py
```
期望：0。这是零回归约束的物理保证。

- [ ] **Step 5: 提交**

```bash
git add backend/app/migrations/v058_hub_integration.py
git commit -m "feat(integration): v058 新增 4 张 HUB 集成表（service_account / api_key / dingtalk_binding_code / service_call_log）"
```

---

## Task 3：ServiceAccount + ApiKey Tortoise 模型

**Files:**
- Create: `backend/app/integration/models/__init__.py`
- Create: `backend/app/integration/models/service_account.py`
- Test: `backend/tests/test_integration_apikey_model.py`

- [ ] **Step 1: 写模型测试（失败）**

文件 `backend/tests/test_integration_apikey_model.py`：
```python
import pytest
from datetime import datetime, timezone, timedelta


@pytest.mark.asyncio
async def test_service_account_create():
    from app.integration.models import ServiceAccount
    sa = await ServiceAccount.create(name='HUB-test-1', description='test')
    assert sa.id is not None
    assert sa.is_active is True
    await sa.delete()


@pytest.mark.asyncio
async def test_apikey_create_with_scopes():
    from app.integration.models import ServiceAccount, ApiKey
    sa = await ServiceAccount.create(name='HUB-test-2')
    key = await ApiKey.create(
        service_account=sa,
        name='HUB prod',
        key_hash='$2b$12$abc',
        key_prefix='hub_a3f9',
        scopes=['act_as_user', 'system_calls'],
    )
    assert key.id is not None
    assert key.scopes == ['act_as_user', 'system_calls']
    assert key.is_revoked is False
    await key.delete()
    await sa.delete()


@pytest.mark.asyncio
async def test_apikey_has_scope_method():
    from app.integration.models import ApiKey
    key = ApiKey(scopes=['act_as_user'])
    assert key.has_scope('act_as_user') is True
    assert key.has_scope('system_calls') is False


@pytest.mark.asyncio
async def test_apikey_is_expired():
    from app.integration.models import ApiKey
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert ApiKey(expires_at=past).is_expired() is True
    assert ApiKey(expires_at=future).is_expired() is False
    assert ApiKey(expires_at=None).is_expired() is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_integration_apikey_model.py -v
```
期望：`ModuleNotFoundError: No module named 'app.integration.models'`

- [ ] **Step 3: 创建 models 子模块**

文件 `backend/app/integration/models/__init__.py`：
```python
from app.integration.models.service_account import ServiceAccount, ApiKey

__all__ = ['ServiceAccount', 'ApiKey']
```

文件 `backend/app/integration/models/service_account.py`：
```python
from __future__ import annotations
from datetime import datetime, timezone
from tortoise import fields
from tortoise.models import Model


class ServiceAccount(Model):
    """外部系统服务账号（HUB / 未来其他集成）"""
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=100, unique=True)
    description = fields.TextField(null=True)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'service_account'

    def __str__(self):
        return f'<ServiceAccount {self.name}>'


class ApiKey(Model):
    """服务账号的 ApiKey（带 scope）"""
    id = fields.IntField(pk=True)
    service_account = fields.ForeignKeyField(
        'models.ServiceAccount', related_name='api_keys', on_delete=fields.CASCADE
    )
    name = fields.CharField(max_length=100)
    key_hash = fields.CharField(max_length=255)
    key_prefix = fields.CharField(max_length=16, unique=True)
    scopes = fields.JSONField(default=list)
    expires_at = fields.DatetimeField(null=True)
    last_used_at = fields.DatetimeField(null=True)
    is_revoked = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    revoked_at = fields.DatetimeField(null=True)

    class Meta:
        table = 'api_key'

    def has_scope(self, scope: str) -> bool:
        return scope in (self.scopes or [])

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    def is_usable(self) -> bool:
        return (not self.is_revoked) and (not self.is_expired())
```

- [ ] **Step 4: 把 integration models 无条件加入 Tortoise modules（生产 + 测试两处）**

**设计修正：模型注册不受 flag 控制（表始终存在 = 0 行；flag 只控制路由/中间件/鉴权分支）。** 这避免了"启动时模型未注册导致表/查询找不到"的问题。

**关键：必须同时改两个文件**（生产环境用 database.py，测试环境用 conftest.py，两侧分别独立调 Tortoise.init）：

(1) 修改 `backend/app/database.py:init_db_connections`：
```python
async def init_db_connections():
    """仅初始化 Tortoise ORM 连接池，不做建表。"""
    await Tortoise.init(
        db_url=_get_db_url(),
        modules={"models": ["app.models", "app.integration.models"]},
    )
```

(2) 修改 `backend/tests/conftest.py:94` 起的 Tortoise.init 调用（变量名按 conftest 实际为 `TEST_DATABASE_URL`，不要复制错）：
```python
await Tortoise.init(
    db_url=TEST_DATABASE_URL,
    modules={"models": ["app.models", "app.integration.models"]},
)
```

注意 modules 字典的 key 仍是 "models"（后面 ForeignKeyField 引用 User 时用 `"models.User"` 字符串），但值数组多加一个 module path——Tortoise 会把两个 module 下的所有模型合并到 "models" 这个命名空间。

为了让本地测试用的 `init_dev_schema`（`Tortoise.generate_schemas(safe=True)`）也能自动建 integration 表，**Step 4 这个修改即生效**——因为 generate_schemas 会扫所有已注册模型并建对应表（dev/test 自动建；生产靠 v058 迁移建）。

- [ ] **Step 5: 临时跑测试（flag 无关，模型已注册）**

```bash
pytest tests/test_integration_apikey_model.py -v
```
期望：4 个测试全 PASS（生产/测试都不依赖 flag 来建表/注册模型）。

- [ ] **Step 6: 提交**

```bash
git add backend/app/integration/models/ \
        backend/app/database.py \
        backend/tests/conftest.py \
        backend/tests/test_integration_apikey_model.py
git commit -m "feat(integration): 新增 ServiceAccount + ApiKey Tortoise 模型 + 注册到生产/测试 Tortoise modules"
```

**注意：** 必须同时包含 `backend/app/database.py` **和** `backend/tests/conftest.py`——前者负责生产/dev 环境注册，后者负责测试环境注册。任一漏 commit，要么生产挂要么测试挂；这两个 Tortoise.init 调用是各自独立的，不会互相覆盖。漏 commit 是 review 高频陷阱。

---

## Task 4：scope 常量与校验函数

**Files:**
- Create: `backend/app/integration/auth/__init__.py`
- Create: `backend/app/integration/auth/scopes.py`
- Test: `backend/tests/test_integration_apikey_auth.py`（部分）

- [ ] **Step 1: 写 scope 测试（先写一部分，鉴权测试在 Task 5 完成）**

文件 `backend/tests/test_integration_apikey_auth.py`（创建）：
```python
import pytest
from fastapi import HTTPException


def test_known_scopes():
    from app.integration.auth.scopes import KNOWN_SCOPES, ACT_AS_USER, SYSTEM_CALLS, ADMIN_OPERATIONS
    assert ACT_AS_USER in KNOWN_SCOPES
    assert SYSTEM_CALLS in KNOWN_SCOPES
    assert ADMIN_OPERATIONS in KNOWN_SCOPES
    assert len(KNOWN_SCOPES) == 3  # C 阶段就这 3 个


def test_validate_scope_list_valid():
    from app.integration.auth.scopes import validate_scope_list
    validate_scope_list(['act_as_user', 'system_calls'])  # 不抛


def test_validate_scope_list_invalid():
    from app.integration.auth.scopes import validate_scope_list
    with pytest.raises(ValueError) as exc:
        validate_scope_list(['act_as_user', 'unknown_scope'])
    assert 'unknown_scope' in str(exc.value)


def test_check_scope_pass():
    from app.integration.auth.scopes import check_scope
    check_scope(['act_as_user', 'system_calls'], 'act_as_user')  # 不抛


def test_check_scope_fail():
    from app.integration.auth.scopes import check_scope
    with pytest.raises(HTTPException) as exc:
        check_scope(['system_calls'], 'act_as_user')
    assert exc.value.status_code == 403
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_integration_apikey_auth.py::test_known_scopes -v
```
期望：ImportError。

- [ ] **Step 3: 实现 scopes.py**

文件 `backend/app/integration/auth/__init__.py`：
```python
"""HUB 集成鉴权模块。"""
```

文件 `backend/app/integration/auth/scopes.py`：
```python
"""ApiKey scope 定义与校验。

scope 是 ApiKey 上配置的能力声明。每次请求时按 endpoint 要求的 scope 校验，
对不上就 403。这是模型 Y（用户身份代理）+ 最小权限的实现机制。
"""
from fastapi import HTTPException


# scope 常量
ACT_AS_USER = 'act_as_user'              # 代理用户调业务接口（必须带 X-Acting-As-User-Id）
SYSTEM_CALLS = 'system_calls'            # 调系统级白名单接口
ADMIN_OPERATIONS = 'admin_operations'    # 管理员级操作（C 阶段不开放）

KNOWN_SCOPES = {ACT_AS_USER, SYSTEM_CALLS, ADMIN_OPERATIONS}


def validate_scope_list(scopes: list[str]) -> None:
    """创建/更新 ApiKey 时校验 scope 字符串合法性。"""
    unknown = [s for s in scopes if s not in KNOWN_SCOPES]
    if unknown:
        raise ValueError(f'未知的 scope: {unknown}')


def check_scope(api_key_scopes: list[str], required: str) -> None:
    """请求处理时校验 ApiKey 是否拥有所需 scope，缺失抛 403。"""
    if required not in (api_key_scopes or []):
        raise HTTPException(
            status_code=403,
            detail=f'ApiKey 缺少必要权限：{required}',
        )
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_integration_apikey_auth.py::test_known_scopes \
       tests/test_integration_apikey_auth.py::test_validate_scope_list_valid \
       tests/test_integration_apikey_auth.py::test_validate_scope_list_invalid \
       tests/test_integration_apikey_auth.py::test_check_scope_pass \
       tests/test_integration_apikey_auth.py::test_check_scope_fail -v
```
期望：5 个测试全 PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/integration/auth/ backend/tests/test_integration_apikey_auth.py
git commit -m "feat(integration): 新增 scope 常量与校验函数"
```

---

## Task 5：ApiKey 鉴权依赖（独立分支，与 JWT 物理隔离）

**Files:**
- Create: `backend/app/integration/auth/api_key.py`
- Modify: `backend/app/auth/dependencies.py`（仅前置 X-API-Key 早返回分支）
- Test: `backend/tests/test_integration_apikey_auth.py`（追加更多用例）

- [ ] **Step 1: 追加 ApiKey 鉴权测试**

在 `backend/tests/test_integration_apikey_auth.py` 末尾追加：
```python
@pytest.mark.asyncio
async def test_authenticate_apikey_valid(monkeypatch):
    """合法 ApiKey + 用户已存在 + Acting-As 头 → 返回目标用户。"""
    from app.integration.auth.api_key import authenticate_api_key
    from app.integration.models import ServiceAccount, ApiKey
    from app.models.user import User
    from app.auth.password import async_hash_password

    sa = await ServiceAccount.create(name='HUB-test-auth-1')
    plain_key = 'test_key_abcdefgh'
    key_hash = await async_hash_password(plain_key)
    await ApiKey.create(
        service_account=sa, name='k', key_hash=key_hash,
        key_prefix=plain_key[:8], scopes=['act_as_user'],
    )
    target_user = await User.create(
        username='target_apikey_user', password_hash='dummy',
        role='user', permissions=['sales'], is_active=True,
    )

    user = await authenticate_api_key(
        api_key=plain_key,
        acting_as_user_id=target_user.id,
        required_scope='act_as_user',
    )
    assert user.id == target_user.id

    await target_user.delete()
    await ApiKey.filter(service_account=sa).delete()
    await sa.delete()


@pytest.mark.asyncio
async def test_authenticate_apikey_invalid_key():
    from app.integration.auth.api_key import authenticate_api_key
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key(
            api_key='nonexistent_key_zzz', acting_as_user_id=1, required_scope='act_as_user',
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_authenticate_apikey_revoked(monkeypatch):
    from app.integration.auth.api_key import authenticate_api_key
    from app.integration.models import ServiceAccount, ApiKey
    from app.auth.password import async_hash_password

    sa = await ServiceAccount.create(name='HUB-test-auth-2')
    plain_key = 'revoked_key_xxxxxxxx'
    await ApiKey.create(
        service_account=sa, name='k', key_hash=await async_hash_password(plain_key),
        key_prefix=plain_key[:8], scopes=['act_as_user'], is_revoked=True,
    )
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key(api_key=plain_key, acting_as_user_id=1, required_scope='act_as_user')
    assert exc.value.status_code == 401
    await ApiKey.filter(service_account=sa).delete()
    await sa.delete()


@pytest.mark.asyncio
async def test_authenticate_apikey_missing_scope(monkeypatch):
    from app.integration.auth.api_key import authenticate_api_key
    from app.integration.models import ServiceAccount, ApiKey
    from app.auth.password import async_hash_password

    sa = await ServiceAccount.create(name='HUB-test-auth-3')
    plain_key = 'system_only_key_xxxxxx'
    await ApiKey.create(
        service_account=sa, name='k', key_hash=await async_hash_password(plain_key),
        key_prefix=plain_key[:8], scopes=['system_calls'],
    )
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key(api_key=plain_key, acting_as_user_id=1, required_scope='act_as_user')
    assert exc.value.status_code == 403
    await ApiKey.filter(service_account=sa).delete()
    await sa.delete()


@pytest.mark.asyncio
async def test_authenticate_apikey_act_as_requires_user_id():
    from app.integration.auth.api_key import authenticate_api_key
    from app.integration.models import ServiceAccount, ApiKey
    from app.auth.password import async_hash_password

    sa = await ServiceAccount.create(name='HUB-test-auth-4')
    plain_key = 'act_key_no_user_xxxxx'
    await ApiKey.create(
        service_account=sa, name='k', key_hash=await async_hash_password(plain_key),
        key_prefix=plain_key[:8], scopes=['act_as_user'],
    )
    with pytest.raises(HTTPException) as exc:
        await authenticate_api_key(api_key=plain_key, acting_as_user_id=None, required_scope='act_as_user')
    assert exc.value.status_code == 400
    assert 'X-Acting-As-User-Id' in exc.value.detail
    await ApiKey.filter(service_account=sa).delete()
    await sa.delete()
```

- [ ] **Step 2: 运行确认失败**

```bash
ENABLE_API_KEY_AUTH=1 pytest tests/test_integration_apikey_auth.py -v
```
期望：5 个 ApiKey 鉴权测试 ImportError。

- [ ] **Step 3: 实现 api_key.py**

文件 `backend/app/integration/auth/api_key.py`：
```python
"""ApiKey 鉴权依赖（与 JWT 物理隔离的独立模块）。

设计要点：
- 与 JWT 流程完全独立，不共享代码（防止意外牵连）
- ApiKey 哈希查询走 key_prefix 索引 + bcrypt 比对（防 timing attack）
- act_as_user scope 强制要求 X-Acting-As-User-Id 头
- 加载目标用户作为 current_user，下游 require_permission 自然生效
"""
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import HTTPException, Request
from app.auth.password import async_verify_password
from app.integration.models import ApiKey
from app.integration.auth.scopes import check_scope, ACT_AS_USER, SYSTEM_CALLS
from app.models.user import User


async def authenticate_api_key(
    api_key: str,
    acting_as_user_id: int | None,
    required_scope: str,
) -> User | None:
    """校验 ApiKey 并返回目标用户（act_as_user 模式）或 None（system_calls 模式）。

    Returns:
        User: act_as_user 模式下的目标用户
        None: system_calls 模式下不返回用户

    Raises:
        HTTPException 401: ApiKey 不存在/已吊销/已过期
        HTTPException 403: scope 不足
        HTTPException 400: act_as_user 缺 X-Acting-As-User-Id
    """
    if not api_key or len(api_key) < 8:
        raise HTTPException(status_code=401, detail='ApiKey 无效')

    key_prefix = api_key[:8]
    candidates = await ApiKey.filter(
        key_prefix=key_prefix, is_revoked=False
    ).select_related('service_account')

    matched: ApiKey | None = None
    for candidate in candidates:
        if await async_verify_password(api_key, candidate.key_hash):
            matched = candidate
            break

    if matched is None:
        raise HTTPException(status_code=401, detail='ApiKey 无效')

    if matched.is_expired():
        raise HTTPException(status_code=401, detail='ApiKey 已过期')

    if not matched.service_account.is_active:
        raise HTTPException(status_code=401, detail='服务账号已停用')

    check_scope(matched.scopes, required_scope)

    if required_scope == ACT_AS_USER:
        if acting_as_user_id is None:
            raise HTTPException(status_code=400, detail='缺少 X-Acting-As-User-Id 请求头')
        target_user = await User.filter(id=acting_as_user_id, is_active=True).first()
        if not target_user:
            raise HTTPException(status_code=401, detail='目标用户不存在或已停用')
        await ApiKey.filter(id=matched.id).update(last_used_at=datetime.now(timezone.utc))
        return target_user

    if required_scope == SYSTEM_CALLS:
        await ApiKey.filter(id=matched.id).update(last_used_at=datetime.now(timezone.utc))
        return None

    raise HTTPException(status_code=400, detail=f'不支持的 scope：{required_scope}')


async def get_current_user_via_api_key(request: Request) -> User:
    """FastAPI 依赖：从 X-API-Key + X-Acting-As-User-Id 头加载目标用户（act_as_user）。"""
    api_key = request.headers.get('X-API-Key', '')
    acting_as = request.headers.get('X-Acting-As-User-Id')
    acting_as_int = int(acting_as) if acting_as and acting_as.isdigit() else None

    user = await authenticate_api_key(
        api_key=api_key,
        acting_as_user_id=acting_as_int,
        required_scope=ACT_AS_USER,
    )
    if user is None:
        raise HTTPException(status_code=500, detail='内部错误：act_as_user 应返回用户')
    return user


async def authenticate_system_call(request: Request) -> None:
    """FastAPI 依赖：white-list 系统接口的 ApiKey 校验（不要求 Acting-As）。"""
    api_key = request.headers.get('X-API-Key', '')
    await authenticate_api_key(
        api_key=api_key,
        acting_as_user_id=None,
        required_scope=SYSTEM_CALLS,
    )
```

- [ ] **Step 4: 修改 auth/dependencies.py 加 X-API-Key 早返回**

修改 `backend/app/auth/dependencies.py`：

(1) 修改 import 行（顶部第 2 行 `from fastapi import Depends, HTTPException`）—— **`Request` 必须加进去**，否则 NameError：
```python
from fastapi import Depends, HTTPException, Request
```

(2) 修改 `_authenticate_user` 函数签名 + 函数体最开始（`if not credentials:` 之前）：
```python
async def _authenticate_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """验证Token并返回用户，不检查must_change_password"""
    # === HUB 集成 ApiKey 早返回分支（受 ENABLE_API_KEY_AUTH 控制）===
    from app.integration.feature_flags import ENABLE_API_KEY_AUTH
    if ENABLE_API_KEY_AUTH and request.headers.get('X-API-Key'):
        from app.integration.auth.api_key import get_current_user_via_api_key
        return await get_current_user_via_api_key(request)
    # === 以下为原 JWT 流程（一字不改）===
    if not credentials:
        raise HTTPException(status_code=401, detail="未授权")
    # ...（原代码保持完整不动）
```

`Request` 是 FastAPI 注入兼容的：FastAPI 会自动把当前请求的 Request 对象注入参数。下游 `get_current_user` 等依赖签名只有 `user: User = Depends(_authenticate_user)`，FastAPI 解依赖时会自动给 `_authenticate_user` 传 request——这部分**无需修改任何调用方**。

- [ ] **Step 5: 运行 ApiKey 鉴权测试确认通过**

```bash
ENABLE_API_KEY_AUTH=1 pytest tests/test_integration_apikey_auth.py -v
```
期望：所有测试 PASS（共 10 个）。

- [ ] **Step 6: 运行 ERP 现有 auth 相关测试确保零回归**

```bash
pytest tests/ -k "auth" -v
```
期望：所有现有 auth 测试 PASS（不少一条）。

- [ ] **Step 7: 提交**

```bash
git add backend/app/integration/auth/api_key.py \
        backend/app/auth/dependencies.py \
        backend/tests/test_integration_apikey_auth.py
git commit -m "feat(integration): 新增 ApiKey 鉴权依赖与 JWT 流程并存"
```

---

## Task 6：ApiKey Admin 接口（CRUD）

**Files:**
- Create: `backend/app/integration/routers/__init__.py`
- Create: `backend/app/integration/routers/admin_api_keys.py`
- Modify: `backend/main.py`（按 flag 注册新 router）
- Test: `backend/tests/test_integration_admin_apikey.py`

- [ ] **Step 1: 写 Admin 接口测试（失败）**

文件 `backend/tests/test_integration_admin_apikey.py`：
```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_apikey_admin_only(client: AsyncClient, auth_token: str, normal_user_token: str):
    """普通用户不能创建 ApiKey，admin 可以。"""
    payload = {
        'service_account_name': 'HUB-test-create',
        'key_name': 'k1',
        'scopes': ['act_as_user', 'system_calls'],
    }
    resp = await client.post(
        '/api/v1/admin/api-keys',
        json=payload,
        headers={'Authorization': f'Bearer {normal_user_token}'},
    )
    assert resp.status_code == 403

    resp = await client.post(
        '/api/v1/admin/api-keys',
        json=payload,
        headers={'Authorization': f'Bearer {auth_token}'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert 'plaintext_key' in body
    assert len(body['plaintext_key']) >= 32
    assert body['key_prefix'] == body['plaintext_key'][:8]
    assert body['scopes'] == ['act_as_user', 'system_calls']


@pytest.mark.asyncio
async def test_list_apikeys(client: AsyncClient, auth_token: str):
    resp = await client.get(
        '/api/v1/admin/api-keys',
        headers={'Authorization': f'Bearer {auth_token}'},
    )
    assert resp.status_code == 200
    items = resp.json()['items']
    for item in items:
        assert 'plaintext_key' not in item
        assert 'key_hash' not in item
        assert 'key_prefix' in item
        assert 'scopes' in item


@pytest.mark.asyncio
async def test_revoke_apikey(client: AsyncClient, auth_token: str):
    payload = {'service_account_name': 'HUB-test-revoke', 'key_name': 'k', 'scopes': ['system_calls']}
    create_resp = await client.post(
        '/api/v1/admin/api-keys', json=payload,
        headers={'Authorization': f'Bearer {auth_token}'},
    )
    key_id = create_resp.json()['id']

    revoke_resp = await client.post(
        f'/api/v1/admin/api-keys/{key_id}/revoke',
        headers={'Authorization': f'Bearer {auth_token}'},
    )
    assert revoke_resp.status_code == 200

    plaintext = create_resp.json()['plaintext_key']
    from app.integration.models import ApiKey
    key = await ApiKey.filter(id=key_id).first()
    assert key.is_revoked is True


@pytest.mark.asyncio
async def test_create_invalid_scope(client: AsyncClient, auth_token: str):
    payload = {'service_account_name': 'X', 'key_name': 'k', 'scopes': ['unknown']}
    resp = await client.post(
        '/api/v1/admin/api-keys', json=payload,
        headers={'Authorization': f'Bearer {auth_token}'},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: 确认 Task 1.5 已经把所需 fixture 加到 conftest**

Task 1.5（测试基础设施扩展）必须先于本 Task 完成。本 Task 用到的 fixture：
- `client`（现有）/ `auth_token`（现有）
- `normal_user_token` / `system_apikey` / `act_as_apikey` / `sample_orders_data` / `fresh_user_with_no_orders`（Task 1.5 新增）

校验它们已经在 conftest 中：
```bash
grep -E "system_apikey|act_as_apikey|normal_user_token|sample_orders_data" \
     /Users/lin/Desktop/ERP-4/backend/tests/conftest.py | head -5
```
期望：4 行匹配（每个 fixture 一行 `@pytest.fixture` 后的 `async def` 名）。如果没有，回到 Task 1.5 完成。

- [ ] **Step 3: 运行测试确认失败**

```bash
ENABLE_API_KEY_AUTH=1 pytest tests/test_integration_admin_apikey.py -v
```
期望：404（路由未注册）或 ImportError。

- [ ] **Step 4: 实现 admin router**

文件 `backend/app/integration/routers/__init__.py`：
```python
"""HUB 集成 router 模块。"""
```

文件 `backend/app/integration/routers/admin_api_keys.py`：
```python
from __future__ import annotations
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.auth.dependencies import require_role
from app.auth.password import async_hash_password
from app.integration.models import ServiceAccount, ApiKey
from app.integration.auth.scopes import validate_scope_list

router = APIRouter(prefix='/admin/api-keys', tags=['HUB 集成 - ApiKey 管理'])


class CreateApiKeyRequest(BaseModel):
    service_account_name: str = Field(..., min_length=1, max_length=100)
    key_name: str = Field(..., min_length=1, max_length=100)
    scopes: list[str] = Field(..., min_items=1)
    description: str | None = None
    expires_at: datetime | None = None


class CreateApiKeyResponse(BaseModel):
    id: int
    plaintext_key: str  # 只在创建时返回一次
    key_prefix: str
    scopes: list[str]


class ApiKeyListItem(BaseModel):
    id: int
    service_account_id: int
    service_account_name: str
    name: str
    key_prefix: str
    scopes: list[str]
    is_revoked: bool
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


@router.post('', response_model=CreateApiKeyResponse, dependencies=[Depends(require_role('admin'))])
async def create_api_key(payload: CreateApiKeyRequest):
    """创建 ApiKey（admin only）。明文 key 只返回一次。"""
    try:
        validate_scope_list(payload.scopes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    sa = await ServiceAccount.filter(name=payload.service_account_name).first()
    if sa is None:
        sa = await ServiceAccount.create(
            name=payload.service_account_name, description=payload.description
        )

    plaintext = secrets.token_urlsafe(32)
    key_hash = await async_hash_password(plaintext)
    key_prefix = plaintext[:8]

    key = await ApiKey.create(
        service_account=sa,
        name=payload.key_name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        scopes=payload.scopes,
        expires_at=payload.expires_at,
    )

    return CreateApiKeyResponse(
        id=key.id, plaintext_key=plaintext, key_prefix=key_prefix, scopes=payload.scopes,
    )


@router.get('', dependencies=[Depends(require_role('admin'))])
async def list_api_keys(include_revoked: bool = False):
    qs = ApiKey.all().select_related('service_account')
    if not include_revoked:
        qs = qs.filter(is_revoked=False)
    items = await qs.order_by('-created_at')
    return {
        'items': [
            ApiKeyListItem(
                id=k.id, service_account_id=k.service_account.id,
                service_account_name=k.service_account.name,
                name=k.name, key_prefix=k.key_prefix, scopes=k.scopes,
                is_revoked=k.is_revoked, last_used_at=k.last_used_at,
                expires_at=k.expires_at, created_at=k.created_at,
            ).dict()
            for k in items
        ]
    }


@router.post('/{key_id}/revoke', dependencies=[Depends(require_role('admin'))])
async def revoke_api_key(key_id: int):
    key = await ApiKey.filter(id=key_id).first()
    if key is None:
        raise HTTPException(status_code=404, detail='ApiKey 不存在')
    if key.is_revoked:
        raise HTTPException(status_code=400, detail='ApiKey 已吊销')
    key.is_revoked = True
    key.revoked_at = datetime.now(timezone.utc)
    await key.save()
    return {'success': True}
```

- [ ] **Step 5: 在 main.py 按 flag 注册 router**

**关键顺序：FastAPI `app.include_router(api_v1)` 在被调用的瞬间会"复制"当前 api_v1 上的所有路由到 app；之后再往 api_v1 追加路由不会自动同步到 app。** 当前 ERP `backend/main.py:307` 在最后一个 `api_v1.include_router(brand_dict.router)`（约 306 行）之后立刻调用 `app.include_router(api_v1)`。

集成路由必须插在两者中间——**在 `api_v1.include_router(brand_dict.router)` 之后、`app.include_router(api_v1)` 之前**。

修改 `backend/main.py`，定位到现有最后一个 `api_v1.include_router(brand_dict.router)` 和 `app.include_router(api_v1)` 之间，插入：
```python
# === HUB 集成路由（按 flag 条件注册）===
# 必须在 app.include_router(api_v1) 之前，否则路由不会被复制到 app
from app.integration.feature_flags import (
    ENABLE_API_KEY_AUTH, ENABLE_DINGTALK_BINDING, ENABLE_HUB_CUSTOMER_PRICES,
)
if ENABLE_API_KEY_AUTH:
    from app.integration.routers import admin_api_keys
    api_v1.include_router(admin_api_keys.router)

# app.include_router(api_v1)  ← 已存在，不要动
```

- [ ] **Step 6: 运行测试确认通过**

```bash
ENABLE_API_KEY_AUTH=1 pytest tests/test_integration_admin_apikey.py -v
```
期望：4 个测试 PASS。

- [ ] **Step 7: 提交**

```bash
git add backend/app/integration/routers/ backend/main.py \
        backend/tests/test_integration_admin_apikey.py
git commit -m "feat(integration): 新增 ApiKey admin 接口（CRUD + 吊销）"
```

---

## Task 7：ApiKey 管理前端页面

**Files:**
- Create: `frontend/src/views/system/ApiKeyManagement.vue`
- Create: `frontend/src/api/integration.js`
- Modify: `frontend/src/views/SettingsView.vue`（在 navItems / settingsValidTabs / 内容区 v-else-if 各追加 'api-keys'，受 flag 控制；**不**新增独立路由）

- [ ] **Step 1: 创建 API 封装**

**关键：** ERP `frontend/src/api/index.js` 已经把 axios 实例的 `baseURL = '/api/v1'`（见 `api/config.js:API_PREFIX`）。所以 `api.get()` 路径**不要**重复加 `/api/v1` 前缀，直接写相对路径。**前端没有 `@/` alias**（`vite.config.js` 无 alias 定义），import 必须用相对路径。

文件 `frontend/src/api/integration.js`：
```javascript
import api from './index'

export const integrationApi = {
  // baseURL 已经是 /api/v1，下面路径不加 /api/v1 前缀
  listApiKeys: (params = {}) => api.get('/admin/api-keys', { params }),
  createApiKey: (payload) => api.post('/admin/api-keys', payload),
  revokeApiKey: (id) => api.post(`/admin/api-keys/${id}/revoke`),
}
```

- [ ] **Step 2: 创建 ApiKey 管理 Vue 页面**

**关键 UI 适配：**
- ERP `<AppTable>` 用法：`columns` prop 定义列头（`{ key, label, align, width }`，注意是 `label` 不是 `title`）+ 默认 slot 由调用方手写 `<tr v-for>` 和 `<td>`（**不是** `data` prop + `#cell-*` 插槽）。
- ERP `<AppModal>` 用法：`visible` + `title` props，body 走默认 slot，按钮走 `#footer` slot（**没有** `@confirm` 事件，**没有** `show-confirm` prop）。
- 前端**无 `@/` alias**，import 用相对路径（如 `../../components/ui/AppCard.vue`）。

文件 `frontend/src/views/system/ApiKeyManagement.vue`：
```vue
<template>
  <div class="page-toolbar">
    <h2 class="page-title">API 密钥管理</h2>
    <AppButton variant="primary" @click="showCreate = true">创建密钥</AppButton>
  </div>

  <AppCard>
    <AppTable :columns="columns" :empty="!keys.length">
      <tr v-for="k in keys" :key="k.id">
        <td class="app-td">{{ k.name }}</td>
        <td class="app-td">{{ k.service_account_name }}</td>
        <td class="app-td std-num">{{ k.key_prefix }}</td>
        <td class="app-td">
          <span class="scope-pill" v-for="s in k.scopes" :key="s">{{ scopeLabel(s) }}</span>
        </td>
        <td class="app-td">{{ k.last_used_at || '-' }}</td>
        <td class="app-td">{{ k.created_at }}</td>
        <td class="app-td">
          <AppButton v-if="!k.is_revoked" variant="danger" size="xs" @click="revoke(k)">
            吊销
          </AppButton>
          <span v-else class="revoked-tag">已吊销</span>
        </td>
      </tr>
    </AppTable>
  </AppCard>

  <CreateApiKeyDialog
    v-model:visible="showCreate"
    @created="handleCreated"
  />

  <PlaintextKeyDialog
    v-model:visible="showPlaintext"
    :plaintext="newPlaintext"
    :prefix="newPrefix"
  />
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { integrationApi } from '../../api/integration'
import AppCard from '../../components/ui/AppCard.vue'
import AppTable from '../../components/common/AppTable.vue'
import AppButton from '../../components/ui/AppButton.vue'
import CreateApiKeyDialog from '../../components/integration/CreateApiKeyDialog.vue'
import PlaintextKeyDialog from '../../components/integration/PlaintextKeyDialog.vue'

const keys = ref([])
const showCreate = ref(false)
const showPlaintext = ref(false)
const newPlaintext = ref('')
const newPrefix = ref('')

const SCOPE_LABELS = {
  act_as_user: '代理用户调用业务接口',
  system_calls: '调用系统级白名单接口',
  admin_operations: '管理员级操作（高危）',
}
const scopeLabel = (s) => SCOPE_LABELS[s] || s

// AppTable columns 用 label，不是 title
const columns = [
  { key: 'name', label: '名称' },
  { key: 'service_account_name', label: '服务账号' },
  { key: 'key_prefix', label: '密钥前缀' },
  { key: 'scopes', label: '授予能力' },
  { key: 'last_used_at', label: '最后使用时间' },
  { key: 'created_at', label: '创建时间' },
  { key: 'actions', label: '操作' },
]

async function load() {
  const { data } = await integrationApi.listApiKeys({ include_revoked: true })
  keys.value = data.items
}

async function revoke(row) {
  if (!confirm(`确认吊销密钥「${row.name}」？吊销后无法恢复。`)) return
  await integrationApi.revokeApiKey(row.id)
  await load()
}

function handleCreated(result) {
  showCreate.value = false
  newPlaintext.value = result.plaintext_key
  newPrefix.value = result.key_prefix
  showPlaintext.value = true
  load()
}

onMounted(load)
</script>

<style scoped>
.page-toolbar { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; }
.page-title { font-size: 20px; font-weight: 600; }
.scope-pill { display: inline-block; padding: 2px 8px; margin-right: 4px; border-radius: 4px; background: var(--bg-soft); font-size: 12px; }
.revoked-tag { color: var(--text-muted); font-size: 12px; }
</style>
```

注意：
- `<tr v-for>` 写在 AppTable 默认 slot 内（AppTable tbody 是 `<slot />`）
- 表头自动由 `columns` prop 渲染
- `<td>` 上加 `app-td` 类（AppTable 通过非 scoped CSS 注入样式）
- 数字/SKU 列加 `std-num`

- [ ] **Step 3: 创建配套对话框组件**

文件 `frontend/src/components/integration/CreateApiKeyDialog.vue`：
```vue
<template>
  <AppModal :visible="visible" title="创建 API 密钥" @update:visible="(v) => emit('update:visible', v)">
    <div class="form-grid">
      <label>服务账号名称
        <AppInput v-model="form.service_account_name" placeholder="如 HUB-生产" />
      </label>
      <label>密钥名称
        <AppInput v-model="form.key_name" placeholder="如 prod-key-1" />
      </label>
      <fieldset>
        <legend>授予的能力（可多选）</legend>
        <label v-for="opt in scopeOptions" :key="opt.code" class="scope-option">
          <input type="checkbox" :value="opt.code" v-model="form.scopes" />
          <span class="scope-label">{{ opt.label }}</span>
          <span class="hint">{{ opt.hint }}</span>
        </label>
      </fieldset>
    </div>
    <template #footer>
      <AppButton variant="ghost" @click="emit('update:visible', false)">取消</AppButton>
      <AppButton variant="primary" :disabled="!canSubmit" @click="submit">创建</AppButton>
    </template>
  </AppModal>
</template>
<script setup>
import { reactive, computed } from 'vue'
import AppModal from '../ui/AppModal.vue'
import AppInput from '../ui/AppInput.vue'
import AppButton from '../ui/AppButton.vue'
import { integrationApi } from '../../api/integration'

defineProps({ visible: Boolean })
const emit = defineEmits(['update:visible', 'created'])

const form = reactive({
  service_account_name: '',
  key_name: '',
  scopes: [],
})

const scopeOptions = [
  { code: 'act_as_user', label: '代理用户调用业务接口', hint: '带用户身份头时按目标用户权限执行' },
  { code: 'system_calls', label: '调用系统级白名单接口', hint: '生成绑定码、查询用户存在等系统操作' },
]

const canSubmit = computed(() =>
  form.service_account_name.trim() && form.key_name.trim() && form.scopes.length > 0
)

async function submit() {
  const { data } = await integrationApi.createApiKey(form)
  emit('created', data)
  Object.assign(form, { service_account_name: '', key_name: '', scopes: [] })
}
</script>

<style scoped>
.scope-option { display: block; margin-bottom: 8px; }
.scope-label { font-weight: 500; }
.hint { display: block; color: var(--text-muted); font-size: 12px; margin-left: 24px; }
</style>
```

文件 `frontend/src/components/integration/PlaintextKeyDialog.vue`：
```vue
<template>
  <AppModal :visible="visible" title="API 密钥（仅显示一次）" @update:visible="(v) => emit('update:visible', v)">
    <div class="warning-box">
      ⚠️ 此密钥<strong>只显示一次</strong>，请立即复制并妥善保存。关闭后无法再次查看。
    </div>
    <div class="key-display">
      <code>{{ plaintext }}</code>
      <AppButton variant="primary" size="sm" @click="copy">复制</AppButton>
    </div>
    <div class="prefix-info">前缀：<code>{{ prefix }}</code>（用于识别）</div>
    <template #footer>
      <AppButton variant="primary" @click="emit('update:visible', false)">我已保存</AppButton>
    </template>
  </AppModal>
</template>
<script setup>
import AppModal from '../ui/AppModal.vue'
import AppButton from '../ui/AppButton.vue'

const props = defineProps({ visible: Boolean, plaintext: String, prefix: String })
const emit = defineEmits(['update:visible'])

async function copy() {
  await navigator.clipboard.writeText(props.plaintext)
  alert('已复制到剪贴板')
}
</script>

<style scoped>
.warning-box { background: var(--warning-bg); padding: 12px; border-radius: 4px; margin-bottom: 16px; }
.key-display { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.key-display code { flex: 1; padding: 8px; background: var(--bg-soft); border-radius: 4px; word-break: break-all; }
.prefix-info { color: var(--text-muted); font-size: 12px; }
</style>
```

- [ ] **Step 4: 把 ApiKey 管理挂进 SettingsView 作为新 nav 项**

**修正：** ERP 当前 SettingsView 用 `navItems = computed(() => [{ key, label, icon, show }, ...])` + `<button v-for="item in navItems">` 直接渲染左侧 nav，内容区是 `<div v-if="settingsTab === 'company'">...<div v-else-if="...">...</div>` 顺序判断。**没有 TabItem / TabsBar 组件**。新增按这个实际 pattern 改。

修改 `frontend/src/views/SettingsView.vue`：

(1) 顶部 import 区加（受 flag 控制实际是不变 import，组件由 v-if 决定渲染）：
```javascript
import ApiKeyManagement from './system/ApiKeyManagement.vue'
import { Key } from 'lucide-vue-next'  // 用作 nav icon（按 SettingsView 现有 lucide 图标风格）
```

(2) `navItems` computed 末尾追加（`show` 同时校验 flag 与 admin 权限）：
```javascript
const apiKeyAuthEnabled = import.meta.env.VITE_ENABLE_API_KEY_AUTH === 'true'

const navItems = computed(() => [
  // ... 原有 13 项保持不动
  {
    key: 'api-keys',
    label: 'API 密钥',
    icon: Key,
    show: apiKeyAuthEnabled && hasPermission('admin'),
  },
])
```

(3) `settingsValidTabs` 数组追加（受 flag 动态扩展）：
```javascript
const settingsValidTabs = [
  'company', 'employees', 'departments', 'warehouses', 'users',
  'payment', 'bank', 'daily-report', 'logs', 'permissions', 'ai',
  'brands', 'asset-categories',
  ...(apiKeyAuthEnabled ? ['api-keys'] : []),
]
```

(4) 内容区按现有 `v-else-if` 模式在最后一个 tab 后追加一段（约第 121 行 `'ai'` 分支后追加）：
```vue
<div v-else-if="settingsTab === 'api-keys'">
  <ApiKeyManagement />
</div>
```

`ApiKeyManagement.vue` 作为嵌入式组件，**保持其内部表格与对话框结构不变**，但移除自身 `<template>` 顶层 `<div class="page-container">` 的外层 padding（嵌入到 SettingsView 后由 SettingsView 负责外层布局）。

- [ ] **Step 5: 手工 smoke test（vite 环境变量）**

```bash
cd /Users/lin/Desktop/ERP-4/frontend
VITE_ENABLE_API_KEY_AUTH=true npm run build
# 或起 dev
VITE_ENABLE_API_KEY_AUTH=true npm run dev
```
浏览器访问 `/settings?tab=api-keys`，登录 admin → 创建一把 ApiKey → 看到一次性明文 → 关闭 → 列表 → 吊销 → 列表显示已吊销。

flag 关闭复测：
```bash
npm run build  # 不带 VITE_ENABLE_API_KEY_AUTH
```
访问 `/settings`，确认 tab 列表里**不出现** "API 密钥"，URL 强填 `?tab=api-keys` 时回退到 'company'（因为不在 settingsValidTabs 里）。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/views/system/ApiKeyManagement.vue \
        frontend/src/api/integration.js \
        frontend/src/components/integration/ \
        frontend/src/views/SettingsView.vue
git commit -m "feat(integration): 新增 ApiKey 管理 SettingsView tab"
```

---

## Task 8：DingTalkBindingCode 模型 + 内部接口（generate / users.exists / users.active-state）

**Files:**
- Create: `backend/app/integration/models/dingtalk_binding.py`
- Create: `backend/app/integration/routers/internal_dingtalk.py`
- Modify: `backend/main.py`（追加 router 注册）
- Test: `backend/tests/test_integration_binding_code.py`

- [ ] **Step 1: 写测试（失败）**

文件 `backend/tests/test_integration_binding_code.py`（创建）：
```python
import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_generate_binding_code_requires_system_scope(client: AsyncClient, system_apikey: str, test_user):
    """system_calls scope ApiKey 调用 generate 应成功。"""
    resp = await client.post(
        '/api/v1/internal/binding-codes/generate',
        json={'erp_username': test_user.username, 'dingtalk_userid': 'manager4521'},
        headers={'X-API-Key': system_apikey},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body['code']) == 6
    assert body['expires_in'] == 300


@pytest.mark.asyncio
async def test_generate_binding_code_unknown_user(client: AsyncClient, system_apikey: str):
    resp = await client.post(
        '/api/v1/internal/binding-codes/generate',
        json={'erp_username': '___nonexistent___', 'dingtalk_userid': 'x'},
        headers={'X-API-Key': system_apikey},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_users_exists(client: AsyncClient, system_apikey: str, test_user):
    resp = await client.post(
        '/api/v1/internal/users/exists',
        json={'username': test_user.username},
        headers={'X-API-Key': system_apikey},
    )
    assert resp.json() == {'exists': True}

    resp = await client.post(
        '/api/v1/internal/users/exists',
        json={'username': '___no_such_user___'},
        headers={'X-API-Key': system_apikey},
    )
    assert resp.json() == {'exists': False}


@pytest.mark.asyncio
async def test_user_active_state(client: AsyncClient, system_apikey: str, test_user):
    resp = await client.get(
        f'/api/v1/internal/users/{test_user.id}/active-state',
        headers={'X-API-Key': system_apikey},
    )
    assert resp.json()['is_active'] is True


@pytest.mark.asyncio
async def test_internal_endpoints_require_system_scope(client: AsyncClient, act_as_apikey: str, test_user):
    """act_as_user scope 不能调系统级接口。"""
    resp = await client.post(
        '/api/v1/internal/users/exists',
        json={'username': test_user.username},
        headers={'X-API-Key': act_as_apikey},
    )
    assert resp.status_code == 403
```

需要 conftest.py 提供 `system_apikey` 和 `act_as_apikey` fixture（创建带不同 scope 的 ApiKey 并返回明文）。

- [ ] **Step 2: 实现 DingTalkBindingCode 模型**

文件 `backend/app/integration/models/dingtalk_binding.py`：
```python
from __future__ import annotations
from datetime import datetime, timezone
from tortoise import fields
from tortoise.models import Model


class DingTalkBindingCode(Model):
    """钉钉绑定一次性码（HUB 调 ERP 生成，5 分钟过期）。"""
    id = fields.IntField(pk=True)
    code_hash = fields.CharField(max_length=255)
    erp_username = fields.CharField(max_length=100)
    dingtalk_userid = fields.CharField(max_length=100)
    expires_at = fields.DatetimeField()
    used_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'dingtalk_binding_code'

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    def is_used(self) -> bool:
        return self.used_at is not None
```

修改 `backend/app/integration/models/__init__.py`：
```python
from app.integration.models.service_account import ServiceAccount, ApiKey
from app.integration.models.dingtalk_binding import DingTalkBindingCode

__all__ = ['ServiceAccount', 'ApiKey', 'DingTalkBindingCode']
```

- [ ] **Step 3: 实现 internal_dingtalk router**

文件 `backend/app/integration/routers/internal_dingtalk.py`：
```python
from __future__ import annotations
import secrets
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.auth.password import async_hash_password
from app.integration.auth.api_key import authenticate_system_call
from app.integration.models import DingTalkBindingCode
from app.models.user import User

router = APIRouter(
    prefix='/internal',
    tags=['HUB 集成 - 内部接口'],
    dependencies=[Depends(authenticate_system_call)],  # 整个 router 需要 system_calls scope
)

BINDING_CODE_TTL_SECONDS = 300


class GenerateBindingCodeRequest(BaseModel):
    erp_username: str = Field(..., min_length=1)
    dingtalk_userid: str = Field(..., min_length=1)


class GenerateBindingCodeResponse(BaseModel):
    code: str
    expires_in: int  # 秒


class UserExistsRequest(BaseModel):
    username: str


@router.post('/binding-codes/generate', response_model=GenerateBindingCodeResponse)
async def generate_binding_code(payload: GenerateBindingCodeRequest):
    user = await User.filter(username=payload.erp_username, is_active=True).first()
    if user is None:
        raise HTTPException(status_code=404, detail='ERP 用户不存在')

    code = ''.join(secrets.choice('0123456789') for _ in range(6))
    code_hash = await async_hash_password(code)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=BINDING_CODE_TTL_SECONDS)
    await DingTalkBindingCode.create(
        code_hash=code_hash,
        erp_username=payload.erp_username,
        dingtalk_userid=payload.dingtalk_userid,
        expires_at=expires_at,
    )
    return GenerateBindingCodeResponse(code=code, expires_in=BINDING_CODE_TTL_SECONDS)


@router.post('/users/exists')
async def user_exists(payload: UserExistsRequest):
    exists = await User.filter(username=payload.username, is_active=True).exists()
    return {'exists': exists}


@router.get('/users/{user_id}/active-state')
async def user_active_state(user_id: int):
    user = await User.filter(id=user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail='用户不存在')
    return {'is_active': user.is_active, 'username': user.username}
```

- [ ] **Step 4: 在 main.py 注册（按 spec §4.2 的 flag 边界，钉钉相关由 ENABLE_DINGTALK_BINDING 控制）**

继续在 `api_v1.include_router(brand_dict.router)` 之后、`app.include_router(api_v1)` 之前的 HUB 集成路由块里追加。**注意**：`internal_dingtalk.router` 包含的 `/internal/binding-codes/generate` 和 `/internal/users/*` 接口属于钉钉绑定专用，必须由 **`ENABLE_DINGTALK_BINDING`** 控制（同时也依赖 `ENABLE_API_KEY_AUTH` 才有 ApiKey 鉴权机制可用）：

```python
if ENABLE_API_KEY_AUTH:
    from app.integration.routers import admin_api_keys
    api_v1.include_router(admin_api_keys.router)

# 钉钉绑定相关接口（generate / users.exists / users.active-state）由
# ENABLE_DINGTALK_BINDING 控制，避免单开 ApiKey 时绑定接口意外暴露
if ENABLE_API_KEY_AUTH and ENABLE_DINGTALK_BINDING:
    from app.integration.routers import internal_dingtalk
    api_v1.include_router(internal_dingtalk.router)
```

flag 行为矩阵：
| ENABLE_API_KEY_AUTH | ENABLE_DINGTALK_BINDING | admin_api_keys 路由 | internal_dingtalk 路由 |
|---|---|---|---|
| False | False | ❌ | ❌ |
| True | False | ✅ | ❌（钉钉接口不暴露） |
| True | True | ✅ | ✅ |
| False | True | ❌（鉴权机制都没起，钉钉接口跑不通） | ❌ |

Task 8 的测试 fixture 同时开 `ENABLE_API_KEY_AUTH=1 ENABLE_DINGTALK_BINDING=1`，所以测试不受影响。

- [ ] **Step 5: 运行测试确认通过**

```bash
ENABLE_API_KEY_AUTH=1 ENABLE_DINGTALK_BINDING=1 pytest tests/test_integration_binding_code.py -v
```
期望：5 个测试 PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/integration/models/dingtalk_binding.py \
        backend/app/integration/models/__init__.py \
        backend/app/integration/routers/internal_dingtalk.py \
        backend/main.py \
        backend/tests/test_integration_binding_code.py
git commit -m "feat(integration): 新增钉钉绑定码生成 + 内部用户查询接口"
```

---

## Task 9：ERP 个人中心绑定页面（前端 + 后端验证）

**Files:**
- Create: `backend/app/integration/routers/internal_binding.py`
- Create: `frontend/src/views/profile/DingTalkBindingTab.vue`
- Create: `frontend/src/components/dingtalk/BindingConfirmDialog.vue`
- Modify: 个人中心路由（按现有 ERP 布局）

- [ ] **Step 1: 实现 binding verify + confirm 接口（用户态 JWT）**

文件 `backend/app/integration/routers/internal_binding.py`：
```python
"""ERP 个人中心绑定页面用的接口（用户态 JWT，非 ApiKey）。

流程：
1. 用户登录 ERP 后访问"个人中心 - 钉钉绑定"
2. 输入 6 位绑定码 → 后端 verify-token 校验
3. 后端返回二次确认信息（钉钉那边的姓名手机号 + ERP 用户信息）
4. 用户点"确认绑定" → 后端 confirm-final 通知 HUB（HTTP 调 HUB）
5. HUB 写入 binding 关系，回调成功
"""
from __future__ import annotations
import os
import httpx
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.auth.dependencies import get_current_user
from app.auth.password import async_verify_password
from app.integration.models import DingTalkBindingCode
from app.models.user import User

router = APIRouter(prefix='/profile/dingtalk', tags=['个人中心 - 钉钉绑定'])


class VerifyTokenRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class VerifyTokenResponse(BaseModel):
    token_id: int
    dingtalk_userid: str
    dingtalk_display_meta: dict | None = None  # 钉钉那边的姓名/手机尾号


class ConfirmFinalRequest(BaseModel):
    token_id: int


@router.post('/verify-code', response_model=VerifyTokenResponse)
async def verify_binding_code(payload: VerifyTokenRequest, user: User = Depends(get_current_user)):
    """用户输入绑定码后，校验是否合法且属于当前用户。"""
    candidates = await DingTalkBindingCode.filter(
        erp_username=user.username, used_at__isnull=True,
    ).order_by('-created_at').limit(20)

    matched: DingTalkBindingCode | None = None
    for c in candidates:
        if await async_verify_password(payload.code, c.code_hash):
            matched = c
            break

    if matched is None:
        raise HTTPException(status_code=400, detail='绑定码错误或已使用')
    if matched.is_expired():
        raise HTTPException(status_code=400, detail='绑定码已过期，请重新发起')

    return VerifyTokenResponse(
        token_id=matched.id,
        dingtalk_userid=matched.dingtalk_userid,
        dingtalk_display_meta=None,
    )


@router.post('/confirm')
async def confirm_binding(payload: ConfirmFinalRequest, user: User = Depends(get_current_user)):
    code_record = await DingTalkBindingCode.filter(id=payload.token_id).first()
    if code_record is None:
        raise HTTPException(status_code=404, detail='绑定记录不存在')
    if code_record.is_used():
        raise HTTPException(status_code=400, detail='已确认过')
    if code_record.is_expired():
        raise HTTPException(status_code=400, detail='绑定码已过期')
    if code_record.erp_username != user.username:
        raise HTTPException(status_code=403, detail='绑定码不属于当前用户')

    hub_url = os.environ.get('HUB_BASE_URL', '').rstrip('/')
    erp_to_hub_secret = os.environ.get('ERP_TO_HUB_SECRET', '')
    if not hub_url or not erp_to_hub_secret:
        raise HTTPException(status_code=500, detail='HUB 通信配置缺失')

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(
                f'{hub_url}/internal/binding/confirm-final',
                json={
                    'token_id': code_record.id,
                    'erp_user_id': user.id,
                    'erp_username': user.username,
                    'erp_display_name': user.display_name or user.username,
                    'dingtalk_userid': code_record.dingtalk_userid,
                },
                headers={'X-ERP-Secret': erp_to_hub_secret},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f'HUB 通信失败：{e}')

    # 原子更新：仅当 used_at 仍为 NULL 时设置（防双击 / 并发重复）
    updated = await DingTalkBindingCode.filter(
        id=code_record.id, used_at__isnull=True,
    ).update(used_at=datetime.now(timezone.utc))
    # updated == 0 表示已被并发更新过；HUB confirm-final 自身保证幂等（按 token_id 唯一）
    # 所以即便此处 updated==0，也仍然返回成功（幂等友好）
    return {'success': True}
```

**幂等性双保险：**
- **ERP 侧**：用 `filter(...used_at__isnull=True).update(...)` 原子更新，避免双击导致 used_at 被重写
- **HUB 侧**：HUB `confirm-final` 接口必须按 token_id 做幂等（重复调用同 token_id 不重复创建 binding，直接返回成功）—— 这条约束写进 Plan 3（HUB 钉钉接入）的对应 Task

ERP 这边即使 HUB 调用因网络抖动重试，HUB 自身去重，ERP 这边 used_at 原子更新——两侧都不会写脏。

- [ ] **Step 2: 在 main.py 按 flag 注册（同 Task 6 Step 5 的位置约束）**

继续在 `api_v1.include_router(brand_dict.router)` 之后、`app.include_router(api_v1)` 之前的 HUB 集成路由块里追加：
```python
if ENABLE_DINGTALK_BINDING:
    from app.integration.routers import internal_binding
    api_v1.include_router(internal_binding.router)
```

- [ ] **Step 3: 实现前端绑定 tab 页面**

**关键 UI 适配（同 Task 7）：**
- 无 `@/` alias，import 用相对路径
- api 实例 baseURL 是 `/api/v1`，调用路径**不要**重复加前缀
- AppModal 用 `:visible` + `#footer` slot 放按钮，**没有** `@confirm` 事件

文件 `frontend/src/views/profile/DingTalkBindingTab.vue`：
```vue
<template>
  <div class="binding-tab">
    <h3>钉钉账号绑定</h3>
    <p class="hint">如果你在钉钉跟 HUB 机器人对话需要绑定 ERP 账号，
       请先在钉钉里发送 <code>/绑定 {{ username }}</code>，机器人会回复 6 位绑定码，
       在下方输入完成确认。</p>

    <div v-if="step === 'input'" class="input-row">
      <AppInput v-model="code" placeholder="输入 6 位绑定码" maxlength="6" />
      <AppButton variant="primary" @click="verify" :disabled="code.length !== 6">下一步</AppButton>
    </div>

    <BindingConfirmDialog
      v-if="step === 'confirm'"
      :visible="step === 'confirm'"
      :token-id="tokenId"
      :dingtalk-userid="dingtalkUserid"
      :erp-username="username"
      :erp-display-name="displayName"
      @confirmed="handleConfirmed"
      @cancelled="step = 'input'"
    />

    <div v-if="step === 'done'" class="success-box">
      ✓ 绑定成功！现在可以在钉钉里使用 HUB 机器人了。
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useAuthStore } from '../../stores/auth'
import api from '../../api/index'
import AppInput from '../../components/ui/AppInput.vue'
import AppButton from '../../components/ui/AppButton.vue'
import BindingConfirmDialog from '../../components/dingtalk/BindingConfirmDialog.vue'

// 用户信息走 ERP 现有 useAuthStore（stores/user.js 不存在）
const authStore = useAuthStore()
const username = computed(() => authStore.user?.username || '')
const displayName = computed(() => authStore.user?.display_name || username.value)

const code = ref('')
const step = ref('input')
const tokenId = ref(null)
const dingtalkUserid = ref('')

async function verify() {
  try {
    // baseURL 已经是 /api/v1，下面路径不加 /api/v1 前缀
    const { data } = await api.post('/profile/dingtalk/verify-code', { code: code.value })
    tokenId.value = data.token_id
    dingtalkUserid.value = data.dingtalk_userid
    step.value = 'confirm'
  } catch (err) {
    alert(err.response?.data?.detail || '验证失败')
  }
}

function handleConfirmed() {
  step.value = 'done'
}
</script>

<style scoped>
.binding-tab { display: flex; flex-direction: column; gap: 16px; padding: 16px; max-width: 480px; }
.hint { color: var(--text-muted); font-size: 14px; }
.input-row { display: flex; gap: 8px; }
.success-box { padding: 12px; background: var(--success-bg); border-radius: 4px; color: var(--success-text); }
</style>
```

文件 `frontend/src/components/dingtalk/BindingConfirmDialog.vue`：
```vue
<template>
  <AppModal
    :visible="visible"
    title="确认绑定钉钉账号"
    @update:visible="(v) => !v && emit('cancelled')"
  >
    <div class="confirm-box">
      <p>请确认下面的信息无误：</p>
      <table class="info-table">
        <tr><th>钉钉账号</th><td><code>{{ dingtalkUserid }}</code></td></tr>
        <tr><th>ERP 账号</th><td>{{ erpUsername }}（{{ erpDisplayName }}）</td></tr>
      </table>
      <p class="warning">
        ⚠️ 绑定后，钉钉账号 <code>{{ dingtalkUserid }}</code> 将能以 ERP 账号
        「{{ erpDisplayName }}」的身份在钉钉里使用机器人。
      </p>
    </div>
    <template #footer>
      <AppButton variant="ghost" @click="emit('cancelled')">取消</AppButton>
      <AppButton variant="primary" @click="confirm">确认绑定</AppButton>
    </template>
  </AppModal>
</template>

<script setup>
import AppModal from '../ui/AppModal.vue'
import AppButton from '../ui/AppButton.vue'
import api from '../../api/index'

const props = defineProps({
  visible: Boolean,
  tokenId: Number,
  dingtalkUserid: String,
  erpUsername: String,
  erpDisplayName: String,
})
const emit = defineEmits(['confirmed', 'cancelled'])

async function confirm() {
  try {
    await api.post('/profile/dingtalk/confirm', { token_id: props.tokenId })
    emit('confirmed')
  } catch (err) {
    alert(err.response?.data?.detail || '确认失败')
  }
}
</script>

<style scoped>
.info-table { border-collapse: collapse; margin: 12px 0; }
.info-table th { text-align: left; padding: 4px 12px 4px 0; color: var(--text-muted); font-weight: 400; }
.info-table td { padding: 4px 0; }
.warning { color: var(--warning-text); font-size: 13px; }
</style>
```

- [ ] **Step 4: 把 DingTalkBindingTab 挂进 SettingsView 作为新 nav 项**

**修正：** 同 Task 7 Step 4，按 SettingsView 实际 pattern（navItems + v-else-if）改。

修改 `frontend/src/views/SettingsView.vue`：

(1) 顶部 import 加：
```javascript
import DingTalkBindingTab from './profile/DingTalkBindingTab.vue'
import { Link2 } from 'lucide-vue-next'  // 钉钉绑定 nav icon
```

(2) `navItems` computed 中追加一项（show 不需要 admin 权限——所有用户都能绑自己的钉钉账号）：
```javascript
const dingtalkBindingEnabled = import.meta.env.VITE_ENABLE_DINGTALK_BINDING === 'true'

const navItems = computed(() => [
  // ... 已有项 + Task 7 加的 api-keys 项
  {
    key: 'dingtalk',
    label: '钉钉绑定',
    icon: Link2,
    show: dingtalkBindingEnabled,
  },
])
```

(3) `settingsValidTabs` 数组在已加 'api-keys' 后再追加 'dingtalk'：
```javascript
const settingsValidTabs = [
  // ...
  ...(apiKeyAuthEnabled ? ['api-keys'] : []),
  ...(dingtalkBindingEnabled ? ['dingtalk'] : []),
]
```

(4) 内容区追加 `v-else-if` 分支：
```vue
<div v-else-if="settingsTab === 'dingtalk'">
  <DingTalkBindingTab />
</div>
```

**用户访问路径**：登录 ERP → 设置 → 左侧导航点"钉钉绑定" → 输入绑定码 → 二次确认。

flag 关闭时该 nav 项不显示（show=false 过滤），且 settingsValidTabs 不含 'dingtalk' → 强填 URL `?tab=dingtalk` 自动回退到 'company'。

- [ ] **Step 5: 提交**

```bash
git add backend/app/integration/routers/internal_binding.py \
        backend/main.py \
        frontend/src/views/profile/DingTalkBindingTab.vue \
        frontend/src/components/dingtalk/BindingConfirmDialog.vue \
        frontend/src/views/SettingsView.vue
git commit -m "feat(integration): 新增钉钉绑定 SettingsView tab + 二次确认对话框"
```

**注意：** 必须包含 `frontend/src/views/SettingsView.vue`——Step 4 在它里面新加了 navItems 项 / settingsValidTabs / v-else-if 分支；漏 commit 会让钉钉绑定入口看不到（用户只能看到老 tab）。漏 commit 是 review 高频陷阱。

---

## Task 10：历史成交价接口

**Files:**
- Create: `backend/app/integration/routers/customer_prices.py`
- Modify: `backend/main.py`
- Test: `backend/tests/test_integration_customer_prices.py`

- [ ] **Step 1: 写测试（失败）**

文件 `backend/tests/test_integration_customer_prices.py`：
```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_customer_prices_returns_recent_n(client: AsyncClient, act_as_apikey: str, sample_orders_data):
    """根据 product_id + customer_id 返回最近 N 次成交价（test_user 默认有 sales 权限）。"""
    pid = sample_orders_data['product_id']
    cid = sample_orders_data['customer_id']
    user_id = sample_orders_data['user_id']

    resp = await client.get(
        f'/api/v1/products/{pid}/customer-prices',
        params={'customer_id': cid, 'limit': 3},
        headers={'X-API-Key': act_as_apikey, 'X-Acting-As-User-Id': str(user_id)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body['records']) <= 3
    for rec in body['records']:
        assert 'unit_price' in rec
        # 价格必须是字符串（Decimal），不是 float（避免精度丢失）
        assert isinstance(rec['unit_price'], str)
        assert 'order_no' in rec
        assert 'order_date' in rec


@pytest.mark.asyncio
async def test_customer_prices_empty(client: AsyncClient, act_as_apikey: str, fresh_user_with_no_orders):
    user_id, pid, cid = fresh_user_with_no_orders
    resp = await client.get(
        f'/api/v1/products/{pid}/customer-prices',
        params={'customer_id': cid},
        headers={'X-API-Key': act_as_apikey, 'X-Acting-As-User-Id': str(user_id)},
    )
    assert resp.status_code == 200
    assert resp.json()['records'] == []


@pytest.mark.asyncio
async def test_customer_prices_limit_max(client: AsyncClient, act_as_apikey: str, sample_orders_data):
    pid = sample_orders_data['product_id']
    cid = sample_orders_data['customer_id']
    uid = sample_orders_data['user_id']
    resp = await client.get(
        f'/api/v1/products/{pid}/customer-prices',
        params={'customer_id': cid, 'limit': 999},
        headers={'X-API-Key': act_as_apikey, 'X-Acting-As-User-Id': str(uid)},
    )
    assert resp.status_code == 200
    assert len(resp.json()['records']) <= 50


@pytest.mark.asyncio
async def test_customer_prices_requires_permission(client: AsyncClient, act_as_apikey: str, sample_orders_data):
    """无 sales / finance / customer 权限的用户调用应被 ERP 拒（403）。"""
    from app.models.user import User
    from app.auth.password import hash_password

    no_perm_user = await User.create(
        username='noperm_user', password_hash=hash_password('x'),
        role='user', permissions=[], is_active=True, must_change_password=False,
        token_version=0,
    )

    pid = sample_orders_data['product_id']
    cid = sample_orders_data['customer_id']

    resp = await client.get(
        f'/api/v1/products/{pid}/customer-prices',
        params={'customer_id': cid},
        headers={'X-API-Key': act_as_apikey, 'X-Acting-As-User-Id': str(no_perm_user.id)},
    )
    assert resp.status_code == 403
    await no_perm_user.delete()
```

- [ ] **Step 2: 实现 customer_prices router**

文件 `backend/app/integration/routers/customer_prices.py`：
```python
from __future__ import annotations
from fastapi import APIRouter, Depends, Query
from app.auth.dependencies import require_permission
from app.models.order import Order, OrderItem
from app.models.user import User

router = APIRouter(prefix='/products', tags=['HUB 集成 - 历史成交价'])

MAX_LIMIT = 50


@router.get(
    '/{product_id}/customer-prices',
    # 价格历史属于销售/财务敏感数据，必须经业务权限校验
    # require_permission 是 OR 关系：拥有 sales 或 finance 或 customer 任一即可
    dependencies=[Depends(require_permission('sales', 'finance', 'customer'))],
)
async def get_customer_prices(
    product_id: int,
    customer_id: int = Query(...),
    limit: int = Query(5, le=MAX_LIMIT, ge=1),
):
    items = await OrderItem.filter(
        product_id=product_id,
        order__customer_id=customer_id,
    ).select_related('order').order_by('-order__created_at').limit(limit)

    # 金额用 str(Decimal)，不用 float（防精度丢失）
    records = [
        {
            'unit_price': str(it.unit_price) if it.unit_price is not None else None,
            'quantity': it.quantity,
            'order_no': it.order.order_no,
            'order_date': it.order.created_at.isoformat(),
        }
        for it in items
    ]
    return {'records': records}
```

**权限模型说明**：当 HUB 通过 `X-API-Key + X-Acting-As-User-Id` 调用此接口时：
1. ApiKey 鉴权早返回分支加载目标用户（X-Acting-As-User-Id 指向）作为 `current_user`
2. `require_permission('sales', 'finance', 'customer')` 校验该目标用户是否拥有任一权限
3. 用户没权限 → 403 → HUB 翻译给最终钉钉用户"你没有该客户的查看权限"

这就是模型 Y 在权限层面的完整闭环：HUB 不绕过 ERP 业务权限校验。

- [ ] **Step 3: 在 main.py 按 flag 注册（同 Task 6 Step 5 的位置约束）**

继续在 `api_v1.include_router(brand_dict.router)` 之后、`app.include_router(api_v1)` 之前的 HUB 集成路由块里追加：
```python
if ENABLE_HUB_CUSTOMER_PRICES:
    from app.integration.routers import customer_prices
    api_v1.include_router(customer_prices.router)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
ENABLE_API_KEY_AUTH=1 ENABLE_HUB_CUSTOMER_PRICES=1 pytest tests/test_integration_customer_prices.py -v
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/integration/routers/customer_prices.py \
        backend/main.py \
        backend/tests/test_integration_customer_prices.py
git commit -m "feat(integration): 新增历史成交价查询接口"
```

---

## Task 11：调用审计中间件

**Files:**
- Create: `backend/app/integration/models/service_call_log.py`
- Create: `backend/app/integration/middleware/__init__.py`
- Create: `backend/app/integration/middleware/audit.py`
- Modify: `backend/main.py`（安装中间件）
- Test: `backend/tests/test_integration_audit.py`

- [ ] **Step 1: 实现 ServiceCallLog 模型**

文件 `backend/app/integration/models/service_call_log.py`：
```python
from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class ServiceCallLog(Model):
    """外部 ApiKey 调用审计日志。"""
    id = fields.BigIntField(pk=True)
    api_key_id = fields.IntField(null=True)
    api_key_prefix = fields.CharField(max_length=16, null=True)
    acting_as_user_id = fields.IntField(null=True)
    method = fields.CharField(max_length=10)
    path = fields.CharField(max_length=500)
    status_code = fields.IntField()
    duration_ms = fields.IntField(null=True)
    ip = fields.CharField(max_length=45, null=True)
    user_agent = fields.CharField(max_length=500, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'service_call_log'
```

修改 `backend/app/integration/models/__init__.py` 添加 `ServiceCallLog`。

- [ ] **Step 2: 实现审计中间件**

文件 `backend/app/integration/middleware/audit.py`：
```python
"""调用审计中间件：异步写日志，请求侧延迟 ≤ 5ms。"""
from __future__ import annotations
import asyncio
import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger('integration.audit')


class ServiceCallAuditMiddleware(BaseHTTPMiddleware):
    """只对带 X-API-Key 的请求生效，JWT 请求完全跳过。"""

    async def dispatch(self, request: Request, call_next):
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            return await call_next(request)

        start = time.perf_counter()
        response: Response = await call_next(request)
        duration_ms = int((time.perf_counter() - start) * 1000)

        asyncio.create_task(self._write_log(request, response, duration_ms))
        return response

    async def _write_log(self, request: Request, response: Response, duration_ms: int):
        try:
            from app.integration.models import ServiceCallLog, ApiKey
            api_key = request.headers.get('X-API-Key', '')
            acting_as = request.headers.get('X-Acting-As-User-Id')
            acting_as_int = int(acting_as) if acting_as and acting_as.isdigit() else None
            prefix = api_key[:8] if len(api_key) >= 8 else None

            # key_prefix 在 ApiKey 模型上是 UNIQUE，安全 first()（无碰撞风险）
            api_key_id = None
            if prefix:
                key = await ApiKey.filter(key_prefix=prefix).first()
                if key:
                    api_key_id = key.id

            await ServiceCallLog.create(
                api_key_id=api_key_id,
                api_key_prefix=prefix,
                acting_as_user_id=acting_as_int,
                method=request.method,
                path=str(request.url.path)[:500],
                status_code=response.status_code,
                duration_ms=duration_ms,
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get('user-agent', '')[:500],
            )
        except Exception as e:
            logger.warning(f'审计日志写入失败：{e}')
```

文件 `backend/app/integration/middleware/__init__.py`：
```python
from app.integration.middleware.audit import ServiceCallAuditMiddleware

__all__ = ['ServiceCallAuditMiddleware']
```

- [ ] **Step 3: 在 main.py 按 flag 安装中间件**

中间件位置不同于路由（中间件作用于 `app`，跟 api_v1 无关）——必须在 `app = FastAPI(...)` 创建之后、`app.include_router(api_v1)` **之前**（确保中间件包住所有路由）。具体放在 ERP 现有 CORS / 其他 add_middleware 调用旁边即可。

```python
from app.integration.feature_flags import ENABLE_SERVICE_CALL_AUDIT, ENABLE_API_KEY_AUTH
if ENABLE_SERVICE_CALL_AUDIT and ENABLE_API_KEY_AUTH:
    from app.integration.middleware import ServiceCallAuditMiddleware
    app.add_middleware(ServiceCallAuditMiddleware)
```

- [ ] **Step 4: 写中间件测试**

文件 `backend/tests/test_integration_audit.py`：
```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_audit_skips_jwt_requests(client: AsyncClient, auth_token: str):
    """JWT 请求不应该写审计日志。"""
    from app.integration.models import ServiceCallLog
    before = await ServiceCallLog.all().count()
    await client.get(
        '/api/v1/auth/me',
        headers={'Authorization': f'Bearer {auth_token}'},
    )
    await asyncio.sleep(0.1)  # 给异步任务一点时间
    after = await ServiceCallLog.all().count()
    assert after == before


@pytest.mark.asyncio
async def test_audit_logs_apikey_requests(client: AsyncClient, system_apikey: str, test_user):
    """ApiKey 请求应该写审计日志。"""
    from app.integration.models import ServiceCallLog
    before = await ServiceCallLog.all().count()
    await client.post(
        '/api/v1/internal/users/exists',
        json={'username': test_user.username},
        headers={'X-API-Key': system_apikey},
    )
    await asyncio.sleep(0.2)
    after = await ServiceCallLog.all().count()
    assert after == before + 1
```

加 `import asyncio` 到测试顶部。

- [ ] **Step 5: 跑测试确认通过**

```bash
ENABLE_API_KEY_AUTH=1 ENABLE_SERVICE_CALL_AUDIT=1 pytest tests/test_integration_audit.py -v
```

- [ ] **Step 6: 提交**

```bash
git add backend/app/integration/models/service_call_log.py \
        backend/app/integration/models/__init__.py \
        backend/app/integration/middleware/ \
        backend/main.py \
        backend/tests/test_integration_audit.py
git commit -m "feat(integration): 新增 ApiKey 调用审计中间件（异步写日志）"
```

---

## Task 12：客户/商品模糊搜索盘点（产出报告 + 决定改动）

这是 spec §15 表格里的"先盘点后改"任务。

**Deliverable:** 一份 markdown 报告 + 决策。

- [ ] **Step 1: 创建盘点报告骨架**

文件 `docs/integration/2026-04-27-fuzzy-search-audit.md`（注意这个文件在 ERP 仓库，不在 HUB 仓库）：
```markdown
# 客户/商品模糊搜索能力盘点

**目的：** 评估 ERP 现有 `/api/v1/customers?q=` 和 `/api/v1/products?q=` 接口在 HUB 调用场景下是否够用。HUB 用户输入"阿里"要能命中"阿里巴巴集团"。

## 客户搜索（GET /api/v1/customers）
... （执行盘点者填写）...

## 商品搜索（GET /api/v1/products）
... （执行盘点者填写）...

## 结论与建议
... （是否需要改动）...
```

- [ ] **Step 2: 阅读 ERP 现有客户/商品搜索代码**

```bash
grep -A 30 "def list_customers\|search.*customer" /Users/lin/Desktop/ERP-4/backend/app/routers/customers.py | head -80
grep -A 30 "def list_products\|search.*product" /Users/lin/Desktop/ERP-4/backend/app/routers/products.py | head -80
```

把代码片段贴进报告，描述：
- 支持的搜索字段（仅 name？还是含 brand / category / tax_id？）
- 是否支持别名（"阿里" → "阿里巴巴"）？
- 是否支持拼音容错？
- 是否模糊匹配（ILIKE %x%）还是前缀（LIKE x%）？

- [ ] **Step 3: 写测试场景跑一遍现有接口**

通过 admin token 调用现有接口，记录 5-10 个测试场景的实际命中情况：
```python
# 示例
# 场景 1：q="阿里" → 期望命中"阿里巴巴集团"
# 场景 2：q="alibaba" → 期望也命中"阿里巴巴集团"
# 场景 3：q="阿里bb" → 模糊命中
# ...
```

把结果填进报告"客户搜索"小节。

- [ ] **Step 4: 给出结论**

按报告结论分两种情况：

**情况 A：现有够用**
- 不需要改 ERP 代码
- HUB 端 MatchResolver 直接使用现有 `/customers?q=` `/products?q=`

**情况 B：现有不够（最常见的不足是无拼音容错、不搜别名字段）**
- 短期：HUB 端 MatchResolver 自己做拼音映射（C 阶段够用）
- 长期：B 阶段在 ERP 后端加 ES 或 trgm 索引

- [ ] **Step 5: 把报告 commit 到 ERP 仓库**

```bash
cd /Users/lin/Desktop/ERP-4
git add docs/integration/2026-04-27-fuzzy-search-audit.md
git commit -m "docs(integration): 客户/商品模糊搜索能力盘点报告"
```

---

## Task 13：零回归证据链

这是 spec §15 G 节的硬要求。下面是逐项证据。

- [ ] **Step 1: ERP 现有测试套件 100% 通过（不少一条）**

所有 flag 关闭情况下：
```bash
cd /Users/lin/Desktop/ERP-4/backend
unset ENABLE_API_KEY_AUTH ENABLE_DINGTALK_BINDING ENABLE_HUB_CUSTOMER_PRICES
pytest tests/ --ignore=tests/test_integration_*.py -v --tb=short 2>&1 | tee /tmp/erp-baseline.log
echo "Baseline test count: $(grep -c PASSED /tmp/erp-baseline.log)"
```
记录通过数。然后 flag 全开重新跑：
```bash
ENABLE_API_KEY_AUTH=1 ENABLE_DINGTALK_BINDING=1 ENABLE_HUB_CUSTOMER_PRICES=1 \
  pytest tests/ --ignore=tests/test_integration_*.py -v --tb=short 2>&1 | tee /tmp/erp-with-flags.log
echo "With-flags test count: $(grep -c PASSED /tmp/erp-with-flags.log)"
```
**期望：两次 PASSED 数完全相同**。

- [ ] **Step 2: OpenAPI schema 兼容性比对（用 git worktree，不动主工作区）**

**修正：** 不能用 `git stash` 切分支——若工作区有未提交改动会被卷入 stash，风险高。改用 `git worktree` 在独立目录 checkout 主分支，互不干扰。

```bash
cd /Users/lin/Desktop/ERP-4

# (1) 当前分支（feat/hub-integration），flag 全关下导出 OpenAPI
unset ENABLE_API_KEY_AUTH ENABLE_DINGTALK_BINDING ENABLE_HUB_CUSTOMER_PRICES ENABLE_SERVICE_CALL_AUDIT
cd backend
python -c "
import os
os.environ['DEBUG'] = '1'  # 让 SECRET_KEY 等自动生成
from main import app
import json
with open('/tmp/openapi-baseline.json', 'w') as f:
    json.dump(app.openapi(), f, sort_keys=True, indent=2, ensure_ascii=False)
print('baseline saved')
"
cd ..

# (2) 用 git worktree 在 /tmp/erp-mainline 把 main 分支 checkout 出来（不动当前工作区）
git worktree add /tmp/erp-mainline main
cd /tmp/erp-mainline/backend
python -c "
import os
os.environ['DEBUG'] = '1'
from main import app
import json
with open('/tmp/openapi-mainline.json', 'w') as f:
    json.dump(app.openapi(), f, sort_keys=True, indent=2, ensure_ascii=False)
print('mainline saved')
"

# (3) 比对
diff /tmp/openapi-mainline.json /tmp/openapi-baseline.json | head -30

# (4) 清理 worktree
cd /Users/lin/Desktop/ERP-4
git worktree remove /tmp/erp-mainline
```
**期望：diff 输出为空（flag 关闭时 OpenAPI schema 与 main 分支一致）**。

- [ ] **Step 3: 关键 endpoint P95 延迟对比**

需要安装 wrk（macOS：`brew install wrk`）。整个流程用两个独立终端：终端 A 启 ERP，终端 B 跑压测。

**先准备一个 admin JWT token**（用于 Authorization 头）：
```bash
cd /Users/lin/Desktop/ERP-4/backend
python -c "
import os, asyncio
os.environ['DEBUG']='1'
from app.database import init_db_connections, close_db
from app.models.user import User
from app.auth.jwt import create_access_token

async def main():
    await init_db_connections()
    u = await User.filter(role='admin').first()
    print(create_access_token({'user_id': u.id, 'username': u.username,
                               'role': u.role, 'token_version': u.token_version}))
    await close_db()
asyncio.run(main())
" > /tmp/admin-token.txt
TOKEN=$(cat /tmp/admin-token.txt)
```

**Baseline（flag 全关）**：

终端 A：
```bash
cd /Users/lin/Desktop/ERP-4/backend
unset ENABLE_API_KEY_AUTH ENABLE_DINGTALK_BINDING ENABLE_HUB_CUSTOMER_PRICES ENABLE_SERVICE_CALL_AUDIT
DEBUG=1 uvicorn main:app --port 8090 --workers 1
```
等 ERP 起好（看到 `Uvicorn running on http://0.0.0.0:8090`）。

终端 B：
```bash
TOKEN=$(cat /tmp/admin-token.txt)
wrk -t4 -c20 -d20s -H "Authorization: Bearer $TOKEN" \
    --latency http://localhost:8090/api/v1/auth/me \
    | tee /tmp/perf-baseline.txt
```

终端 A 按 Ctrl+C 停 ERP。

**With flags（flag 全开）**：

终端 A：
```bash
ENABLE_API_KEY_AUTH=1 ENABLE_DINGTALK_BINDING=1 ENABLE_HUB_CUSTOMER_PRICES=1 \
   ENABLE_SERVICE_CALL_AUDIT=1 DEBUG=1 \
   uvicorn main:app --port 8090 --workers 1
```

终端 B（同样的命令）：
```bash
wrk -t4 -c20 -d20s -H "Authorization: Bearer $TOKEN" \
    --latency http://localhost:8090/api/v1/auth/me \
    | tee /tmp/perf-with-flags.txt
```

**对比 P95**（wrk 输出的 99% / 95% latency 行）：
```bash
grep -E "^\s+(50|75|90|99)%" /tmp/perf-baseline.txt
grep -E "^\s+(50|75|90|99)%" /tmp/perf-with-flags.txt
```
**期望**：每个分位数 with-flags 比 baseline 增加 ≤ 5%（spec §15 性能预算硬约束）。

如果差异超过 5%：
- 检查 ApiKey 早返回分支是否有不必要的工作（比如不该查数据库的地方查了）
- 检查审计中间件是否在 JWT 路径意外触发（不该触发的）

终端 A 按 Ctrl+C 停 ERP。

- [ ] **Step 4: 数据库 schema diff 校验**

```bash
# 当前分支 vs main 分支的 migrations 目录对比
cd /Users/lin/Desktop/ERP-4
git diff main..feat/hub-integration -- backend/app/migrations/
```
**期望：仅含新增 Python 文件 `v058_hub_integration.py`，且文件内只有 CREATE TABLE 没有 ALTER TABLE**。

```bash
grep -ci "ALTER TABLE" backend/app/migrations/v058_hub_integration.py
# 期望：0
```

并补充检查：现有 v001..v057 全部未被改动：
```bash
git diff main..feat/hub-integration -- backend/app/migrations/v0*.py | grep -v v058 | head
# 期望：无输出（除 v058 外没有修改任何旧迁移）
```

- [ ] **Step 5: flag 全关 = 行为字节级一致 校验**

启动 flag 全关的 ERP，用 admin token 跑一遍核心用户操作（登录 / 查产品 / 创建订单 / 记凭证），与 main 分支版本对比响应（结构、状态码、数据）。可以用一个简单的 e2e 脚本比对几个关键接口的响应。

记录到 `docs/integration/2026-04-27-no-regression-evidence.md`。

- [ ] **Step 6: 把所有证据汇总到报告**

文件 `docs/integration/2026-04-27-no-regression-evidence.md`（汇总文档）：
```markdown
# HUB 集成零回归证据链

## 1. 测试套件
- baseline (flags off)：N PASSED
- with-flags (all on)：N PASSED
- 差异：0

## 2. OpenAPI 兼容
- diff main..feat/hub-integration with flags off：empty

## 3. 性能
- /auth/me P95 baseline: X ms / with flags: Y ms / 差异 < 5%

## 4. 数据库 schema
- 新增表 4 张，无 ALTER

## 5. flag 全关字节级一致
- 已跑 5 个核心场景，响应字节一致

签字：____ 日期：2026-XX-XX
```

- [ ] **Step 7: 提交**

```bash
git add docs/integration/2026-04-27-no-regression-evidence.md
git commit -m "docs(integration): 零回归证据链（5 项均通过）"
```

---

## Task 14：合并到 main + 部署灰度

**注意：** 这一步需要管理员权限决定，**不是工程师自动操作**。Plan 写到这里是给"如何做" 的指引，**实际灰度由人决定何时按按钮**。

- [ ] **Step 1: 提 PR 到 main**

```bash
git push origin feat/hub-integration
gh pr create --title "feat: HUB 集成 ERP 改动（5 项 + 零回归证据链）" \
   --body "$(cat <<'EOF'
## 摘要

实现 HUB 数据中台对接 ERP 所需的 5 项改动，全部满足零回归约束：
- ApiKey 鉴权（独立模块，X-API-Key 头早返回分支）
- 钉钉绑定码生成 + 个人中心绑定页面 + 二次确认
- 历史成交价接口
- 客户/商品模糊搜索盘点（结论见 docs/integration/）
- 调用审计中间件

## 零回归证据
见 docs/integration/2026-04-27-no-regression-evidence.md

## 灰度发布顺序
按 spec §15 D 节执行（dev → staging → prod 逐 flag 开）

## 关联 spec
docs/superpowers/specs/2026-04-27-hub-middleware-design.md（HUB 仓库）
EOF
)"
```

- [ ] **Step 2: 灰度发布（按 spec §15 D 节，6 阶段，每阶段都是人决定继续）**

```
阶段 1：dev 环境全开 flag → 跑 1 周观察
阶段 2：staging 全开 → HUB dev 实例对接 → 跑 1 周
阶段 3：生产部署 ERP 新代码，但 flag 仍 False → 观察 24h
阶段 4：生产逐个开 flag，每开一个观察 24h
  4a: ENABLE_SERVICE_CALL_AUDIT （已经默认 True，但 ENABLE_API_KEY_AUTH=False 时不生效）
  4b: ENABLE_API_KEY_AUTH=true → 24h 观察
  4c: ENABLE_HUB_CUSTOMER_PRICES=true → 24h 观察
  4d: ENABLE_DINGTALK_BINDING=true → 24h 观察
阶段 5：HUB 生产对接，先 1-2 个测试用户
阶段 6：扩大到全员
```

每阶段如果发现异常 → 立即关 flag → 排查根因 → 修复 → 重新走该阶段。

---

## Self-Review（v6，应用第五轮 ERP HEAD 3b16126 review 反馈后）

### Spec 覆盖检查

| Spec 章节 | Plan 任务 | ✓ |
|---|---|---|
| §15.1 4.1 ApiKey + ServiceAccount + 鉴权中间件 + admin UI | Task 3, 4, 5, 6, 7 | ✓ |
| §15.1 4.2 钉钉绑定码 + 个人中心绑定页 + 二次确认 | Task 8, 9 | ✓ |
| §15.1 4.3 历史成交价接口 | Task 10 | ✓ |
| §15.1 4.4 模糊搜索盘点 | Task 12 | ✓ |
| §15.1 4.5 调用审计 | Task 11 | ✓ |
| §15.2 A 改动开关化 | Task 1 + 各 Task 的 main.py 注册条件 | ✓ |
| §15.2 B 物理隔离 | 所有新代码在 backend/app/integration/ | ✓ |
| §15.2 C 测试矩阵 | 各 Task 都有 TDD + Task 13 零回归 | ✓ |
| §15.2 D 灰度发布 | Task 14 | ✓ |
| §15.2 E 数据迁移零风险 | Task 2（v058 Python 迁移，仅 CREATE TABLE） | ✓ |
| §15.2 F 性能预算 | Task 13 Step 3 | ✓ |
| §15.2 G 无回归证据链 | Task 13 全部 | ✓ |

### Review 反馈修复清单（v2 引入）

| # | 反馈 | 修复 |
|---|---|---|
| P1-1 | 迁移文件不会被 ERP 启动 runner 执行 | Task 2 改为 `backend/app/migrations/v058_hub_integration.py`，Python 形式 + `up(conn)` / `down(conn)` 函数 |
| P1-2 | integration 模型注册不完整 | Task 3 Step 4 改为修改 `database.py:Tortoise.init` 的 modules（无条件加 `app.integration.models`），不再受 flag 控制；4 张表/4 个模型一次性注册全 |
| P1-3 | 测试 fixture 与现有 conftest 不匹配 | 新增 **Task 1.5 测试基础设施扩展**（fixture: `system_apikey` / `act_as_apikey` / `sample_orders_data` / `fresh_user_with_no_orders` / `normal_user` 等 + TABLES_TO_TRUNCATE 加 4 张新表） |
| P1-4 | 历史成交价接口权限过宽 | Task 10 改为加 `Depends(require_permission('sales', 'finance', 'customer'))` + Decimal 用 `str()` 不用 `float()` + 新增无权限测试 |
| P2-5 | 零回归 git stash 危险 + 性能测试占位 | Task 13 改为 `git worktree add /tmp/erp-mainline main`；性能测试给完整 wrk + uvicorn 启动命令 + admin token 生成脚本 |
| 调整-A | Task 5 没说要 import Request | Task 5 Step 4 明确 `from fastapi import Depends, HTTPException, Request` |
| 调整-B | Task 7/9 前端路径不存在 /profile | Task 7 / 9 改为挂进 `SettingsView.vue` 的 tab 体系（`settingsValidTabs` 加 'api-keys' / 'dingtalk'，受 `VITE_ENABLE_*` 控制） |
| 调整-C | 绑定确认幂等性风险 | Task 9 `confirm_binding` 改为：先调 HUB（HUB 自身按 token_id 幂等）→ 用 `filter(...used_at__isnull=True).update(...)` 原子更新；ERP 这边 `update == 0` 也返回成功 |
| 调整-D | key_prefix 无 unique 约束碰撞风险 | Task 2 迁移 + Task 3 模型同时加 `UNIQUE` 约束；Task 11 审计中间件 `.first()` 注释 "无碰撞风险" |
| 调整-E | Task 1 写"5 个 flag" | 改为"4 个 flag" |

### Placeholder Scan

- ✓ 无 "TODO" / "TBD" / "implement later"
- ✓ 所有 step 都有实际代码或命令
- ✓ Task 1.5 已新增 fixture，所有后续 Task 引用的 fixture 均有定义
- ✓ Task 13 性能测试用具体的 wrk 命令 + uvicorn 启动命令，不再是 `<restart>` 占位
- ✓ Task 13 OpenAPI 比对用 git worktree 不动主工作区，不再 `git stash`
- ✓ 前端 Task 7 / 9 已直接对齐 ERP 实际写法（navItems + v-else-if，无 TabItem / TabsBar）

### 类型一致性

- ✓ ApiKey 模型字段（key_prefix UNIQUE）与迁移 v058 字段一致
- ✓ scope 常量名（act_as_user / system_calls）在所有 Task 中一致
- ✓ X-API-Key / X-Acting-As-User-Id header 名贯穿一致
- ✓ DingTalkBindingCode 表名 / 模型名 / 路由命名一致
- ✓ async_client → client（用现有 conftest 名字）；admin_token → auth_token；user_token → normal_user_token（Task 1.5 新增）

### 范围检查

C 阶段 ERP 改动 5 项全部覆盖。Plan 1 完成后，HUB（Plan 2-5）才能正常对接 ERP。

---

### v3 第二轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P1-A | sample_orders_data 不可执行（base_master_data 返回对象不是 _id；OrderItem 缺 cost_price） | Task 1.5 fixture 改为复用 base_master_data['account_set'] / ['warehouse'] / ['customer'] / ['product'] 对象；OrderItem 显式传 cost_price=Decimal('60.000000')；去掉"字段不同则调整"模糊措辞 |
| P1-B | Task 3 git add 漏 database.py | Task 3 Step 6 git add 增加 `backend/app/database.py`；文件结构顶表把 `backend/app/models/__init__.py` 改为 `backend/app/database.py`；commit 步骤加红字提示"漏 commit 是 review 高频陷阱" |
| P2-A | SettingsView 用 navItems + v-if/v-else-if 不是 TabItem/TabsBar | Task 7 / 9 Step 4 重写：在 `navItems` computed 末尾追加 `{ key, label, icon, show }` 项；`settingsValidTabs` 加 'api-keys' / 'dingtalk'；内容区按 `v-else-if="settingsTab === 'xxx'"` pattern 加分支；删除 TabItem / TabsBar 引用 |
| P3 | 顶部 v1 残留（"5 个 feature flag" / Task 7 "添加 /system/api-keys 路由"） | 文件结构顶表改"4 个"；test docstring 改"4 个 flag 默认值（audit 默认 True，其余 False）"；Task 7 顶部 modify 行改为"在 SettingsView 追加 nav 项 / valid tabs / 内容区分支，不新增独立路由" |

---

### v4 第三轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P1-V4-A | conftest 单独 Tortoise.init 没注册 integration models | Task 1.5 新增 Step 1：把 `backend/tests/conftest.py:94` 的 `modules={"models": ["app.models"]}` 改为 `["app.models", "app.integration.models"]`（与 database.py 同步）；commit msg 也同步更新 |
| P1-V4-B | integration 路由必须在 `app.include_router(api_v1)` **之前**注册 | Task 6 / 8 / 9 / 10 Step（main.py 注册）全部明确"在 `api_v1.include_router(brand_dict.router)` 之后、`app.include_router(api_v1)` 之前"；中间件位置另文说明（在 app 创建后、include_router(api_v1) 前） |
| P1-V4-C | Task 8 测试硬编码 `'admin'` 用户但 conftest 创建 `testadmin` | 5 处测试改为依赖 `test_user` fixture，用 `test_user.username` / `test_user.id` 替代字面量 'admin' |
| P1-V4-D | 前端代码与 ERP 前端基础设施不兼容 | 全面改写：(1) 所有 `import '@/...'` → 相对路径（无 alias）；(2) `api.get('/api/v1/...')` → `api.get('/admin/...')`（baseURL 已是 /api/v1）；(3) AppTable 用 `:columns` + 默认 slot 手写 `<tr v-for>` `<td class="app-td">`，不再用 `:data` + `#cell-*`；(4) AppModal 用 `:visible` + `#footer` slot 放按钮，删除 `@confirm` / `show-confirm` |
| P2-V4-A | Task 9 修改 SettingsView 但 git add 漏提交 | Task 9 Step 5 git add 加 `frontend/src/views/SettingsView.vue` + 加红字提示 |
| 文档残留 | Self-Review 还有过期"TabItem"警告 | 改为 "✓ 前端 Task 7 / 9 已直接对齐 ERP 实际写法（navItems + v-else-if）" |

---

### v5 第四轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P1-V5-A | Task 1.5 把 conftest 指向尚未创建的 `app.integration.models`，会 ModuleNotFoundError | 把 conftest 的 Tortoise.init `modules` 修改**整个移到 Task 3 Step 4**（与 `database.py` 修改 + 模型创建同一个 commit）；Task 1.5 不再动 Tortoise.init，仅做 TABLES_TO_TRUNCATE + fixtures（fixtures 内部 import 是延迟的，不影响）；Task 1.5 commit msg 简化为"conftest 扩展（HUB 集成 fixture + TABLES_TO_TRUNCATE）" |
| P1-V5-B | DingTalkBindingTab 用了不存在的 `useUserStore`（ERP 实际是 `useAuthStore`） | Task 9 DingTalkBindingTab.vue 改为 `import { useAuthStore } from '../../stores/auth'` + `const authStore = useAuthStore()` + `authStore.user?.username` |
| P3-V5-C | Task 1.5 示例用 `db_url=db_url`，conftest 实际变量是 `TEST_DATABASE_URL` | Task 3 Step 4 (2) 的 conftest 修改示例直接用 `db_url=TEST_DATABASE_URL`（不让实施者整段复制错） |
| 实施者陷阱 | Task 1.5 Step 3 跑测试时 4 张 integration 表还没建出来 | 给出明确说明：如果 ERP `setup_db` 用 `TRUNCATE ... IF EXISTS` 则可正常跳过；否则把这一步往后挪到 Task 3 完成后再跑 |

---

### v6 第五轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P2-V6-A | `internal_dingtalk` 路由仅由 `ENABLE_API_KEY_AUTH` 控制，违背 spec §4.2 的 `ENABLE_DINGTALK_BINDING` 边界 | Task 8 Step 4 改为：`admin_api_keys.router` 仍由 `ENABLE_API_KEY_AUTH` 单独控制；`internal_dingtalk.router` 由 `ENABLE_API_KEY_AUTH and ENABLE_DINGTALK_BINDING` 双 flag 共同控制（缺任一钉钉接口不暴露）；附 flag 行为矩阵表清晰呈现 |

---

**Plan 1 v6 结束（已修复 v1+v2+v3+v4+v5+v6 六轮 review 反馈，共 23 处问题）**
