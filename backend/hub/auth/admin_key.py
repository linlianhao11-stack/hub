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
