"""HUB 权限校验：hub_user → 拥有的权限码集合（聚合所有 role 的 permissions）。"""
from __future__ import annotations

from hub.error_codes import BizError, BizErrorCode
from hub.models import HubRole, HubUserRole

# 权限 code → BizErrorCode 映射（用于把权限不足翻译成具体中文文案）
_PERM_TO_BIZ = {
    "usecase.query_product.use": BizErrorCode.PERM_NO_PRODUCT_QUERY,
    "usecase.query_customer_history.use": BizErrorCode.PERM_NO_CUSTOMER_HISTORY,
    "downstream.erp.use": BizErrorCode.PERM_DOWNSTREAM_DENIED,
}
BIZ_DEFAULT = BizErrorCode.PERM_DOWNSTREAM_DENIED


def _permission_to_error_code(perm_code: str) -> BizErrorCode:
    return _PERM_TO_BIZ.get(perm_code, BIZ_DEFAULT)


async def get_user_permissions(hub_user_id: int) -> set[str]:
    """返回 hub_user 通过所有 role 聚合的所有权限 code 集合。"""
    user_roles = await HubUserRole.filter(hub_user_id=hub_user_id)
    perms: set[str] = set()
    for ur in user_roles:
        role = await HubRole.get(id=ur.role_id).prefetch_related("permissions")
        async for p in role.permissions:
            perms.add(p.code)
    return perms


async def has_permission(hub_user_id: int, perm_code: str) -> bool:
    perms = await get_user_permissions(hub_user_id)
    return perm_code in perms


async def require_permissions(hub_user_id: int, perm_codes: list[str]) -> None:
    """所有 perm_codes 必须都拥有，否则抛 BizError（按缺失的第一个 code 决定文案）。"""
    perms = await get_user_permissions(hub_user_id)
    for code in perm_codes:
        if code not in perms:
            raise BizError(_permission_to_error_code(code))
