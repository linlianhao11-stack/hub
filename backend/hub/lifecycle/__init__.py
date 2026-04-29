"""HUB 进程生命周期组件（lifespan 内的后台 task 等）。"""
from hub.lifecycle.dingtalk_connect import (
    connect_dingtalk_stream_when_ready,
    connect_with_reload,
)

__all__ = ["connect_dingtalk_stream_when_ready", "connect_with_reload"]
