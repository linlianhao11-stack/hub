"""TaskRunner Protocol：任务异步执行。"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED_USER = "failed_user"
    FAILED_SYSTEM_RETRYING = "failed_system_retrying"
    FAILED_SYSTEM_FINAL = "failed_system_final"


@dataclass
class TaskInfo:
    task_id: str
    task_type: str
    status: TaskStatus
    payload: dict


class TaskRunner(Protocol):
    """任务投递与状态查询。"""
    async def submit(self, task_type: str, payload: dict) -> str:
        """投递任务，返回 task_id。"""
        ...

    async def get_status(self, task_id: str) -> TaskStatus | None: ...
