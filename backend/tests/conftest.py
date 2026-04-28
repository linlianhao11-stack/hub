"""HUB 测试基础设施。"""
import os
import sys
from pathlib import Path

import pytest
from tortoise import Tortoise

# 让测试能 import backend/main.py（不在 hub/ 包内）
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# 测试数据库连接（CI 通常注入；本地用临时 postgres 5433）
TEST_DATABASE_URL = os.environ.get(
    "HUB_TEST_DATABASE_URL", "postgres://hub:hub@localhost:5435/hub_test"
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
