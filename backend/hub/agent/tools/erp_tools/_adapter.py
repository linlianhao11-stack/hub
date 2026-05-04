# hub/agent/tools/erp_tools/_adapter.py
"""ERP adapter 单例持有者（避免子模块 ↔ __init__ 循环导入）。"""
from __future__ import annotations

from hub.adapters.downstream.erp4 import Erp4Adapter

_erp_adapter: Erp4Adapter | None = None


def set_erp_adapter(adapter: Erp4Adapter | None) -> None:
    """app startup 时挂 adapter；shutdown 时建议传 None 防 stale 引用。

    用法（Task 6 集成时在 main.py lifespan 实施）：
        # startup
        adapter = Erp4Adapter(...)
        set_erp_adapter(adapter)
        ...
        # shutdown
        set_erp_adapter(None)
    """
    global _erp_adapter
    _erp_adapter = adapter


def current_erp_adapter() -> Erp4Adapter:
    """tool 内部访问当前 adapter；未挂时显式抛错。"""
    if _erp_adapter is None:
        raise RuntimeError("ERP adapter 未初始化（startup 必须先调 set_erp_adapter）")
    return _erp_adapter
