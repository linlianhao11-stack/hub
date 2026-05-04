from __future__ import annotations

from hub.adapters.downstream.erp4 import Erp4Adapter

_erp_adapter: Erp4Adapter | None = None


def set_erp_adapter(adapter: Erp4Adapter | None) -> None:
    """app startup 挂；测试 fixture 注入 mock。"""
    global _erp_adapter
    _erp_adapter = adapter


def current_erp_adapter() -> Erp4Adapter:
    if _erp_adapter is None:
        raise RuntimeError("ERP adapter 未初始化（startup 必须先调 set_erp_adapter）")
    return _erp_adapter
