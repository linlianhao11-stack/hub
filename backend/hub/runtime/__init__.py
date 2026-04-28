"""runtime 包：gateway / worker 的通用启动装配代码。"""
from hub.runtime.dingtalk_bootstrap import (
    BootstrappedClients,
    DingtalkConfig,
    Erp4Config,
    bootstrap_dingtalk_clients,
    load_active_dingtalk_app,
    load_active_erp_system,
)

__all__ = [
    "BootstrappedClients",
    "DingtalkConfig",
    "Erp4Config",
    "bootstrap_dingtalk_clients",
    "load_active_dingtalk_app",
    "load_active_erp_system",
]
