"""DownstreamAdapter Protocol：下游业务系统协议（ERP / CRM / OA 等）。

具体方法签名由各 Adapter 根据下游 API 决定，但所有"代用户"调用必须接受
acting_as_user_id 参数（模型 Y 强制约束）。
"""
from __future__ import annotations

from typing import Protocol


class DownstreamAdapter(Protocol):
    downstream_type: str

    async def health_check(self) -> bool: ...
