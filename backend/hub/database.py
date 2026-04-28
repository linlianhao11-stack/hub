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
            "HUB_DATABASE_URL 未设置。运行 aerich 命令前请 export HUB_DATABASE_URL=postgres://..."
            "（注意：Tortoise 识别 postgres:// scheme，不识别 postgresql://）"
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
