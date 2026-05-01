"""HUB 后台：系统设置 key-value 配置 API。

提供 2 个 endpoint：
- GET /{key}  读取已知 key 的当前值
- PUT /{key}  写入已知 key（带类型校验）

权限码：platform.flags.write。
- 已知 key 白名单：防止误写未知 key 污染配置表
- 类型校验：每个 key 限定预期 Python 类型
- 写入触发 audit_log
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from hub.auth.admin_perms import require_hub_perm
from hub.models import AuditLog, SystemConfig

router = APIRouter(prefix="/hub/v1/admin/config", tags=["admin-config"])


# 已知 key 白名单（防误写）
_KNOWN_KEYS = {
    "alert_receivers": list,            # list[str] 钉钉 userid
    "task_payload_ttl_days": int,
    "task_log_ttl_days": int,
    "daily_audit_hour": int,            # 0-23
    "low_confidence_threshold": float,  # 0-1
    "month_llm_budget_yuan": float,     # 月度 LLM 预算（元）；int 自动转 float
    "business_dict": dict,              # Plan 6 Task 17：业务词典；admin 可编辑覆盖默认
}


@router.get("/{key}", dependencies=[Depends(require_hub_perm("platform.flags.write"))])
async def get_config(key: str):
    if key not in _KNOWN_KEYS:
        raise HTTPException(400, f"未知配置 key: {key}")
    rec = await SystemConfig.filter(key=key).first()
    return {"key": key, "value": rec.value if rec else None}


class SetConfigRequest(BaseModel):
    value: object


@router.put("/{key}", dependencies=[Depends(require_hub_perm("platform.flags.write"))])
async def set_config(request: Request, key: str, body: SetConfigRequest = Body(...)):
    if key not in _KNOWN_KEYS:
        raise HTTPException(400, f"未知配置 key: {key}")

    expected_type = _KNOWN_KEYS[key]
    # JSON 没有 float 类型时（如 0），int 也是合法 float；在此放过 int → float
    if expected_type is float and isinstance(body.value, int) and not isinstance(body.value, bool):
        actual_value: object = float(body.value)
    elif not isinstance(body.value, expected_type) or isinstance(body.value, bool) and expected_type is not bool:
        raise HTTPException(400, f"类型错误：期望 {expected_type.__name__}")
    else:
        actual_value = body.value

    actor = request.state.hub_user
    await SystemConfig.update_or_create(
        key=key,
        defaults={"value": actual_value, "updated_by_hub_user_id": actor.id},
    )
    await AuditLog.create(
        who_hub_user_id=actor.id,
        action="update_system_config",
        target_type="system_config",
        target_id=key,
        detail={"value": actual_value},
    )
    return {"success": True}
