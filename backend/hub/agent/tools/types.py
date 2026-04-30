# hub/agent/tools/types.py
from enum import StrEnum
from dataclasses import dataclass
from typing import Callable


class ToolType(StrEnum):
    READ = "read"
    GENERATE = "generate"
    WRITE_DRAFT = "write_draft"
    WRITE_ERP = "write_erp"


@dataclass
class ToolDef:
    name: str
    fn: Callable
    perm: str
    description: str
    tool_type: ToolType
    schema: dict


class UnconfirmedWriteToolError(Exception):
    """写类 tool 未确认或确认链路失败的基类（v7 round 2 P1：拆两个 subclass）。"""


class MissingConfirmationError(UnconfirmedWriteToolError):
    """LLM 第一次调写 tool 完全没传 confirmation_action_id / confirmation_token。

    ChainAgent 处理：调 add_pending（用户尚未见过预览，应该走"先生成 text 预览"路径）。
    """


class ClaimFailedError(UnconfirmedWriteToolError):
    """LLM 传了 confirmation 字段但 claim 失败（token 错 / args 篡改 / 已被并发领取 / stale）。

    ChainAgent 处理：**不调 add_pending**（v7 round 2 P1-#2：避免重复 pending）；
    只输出"重新预览并请用户重新确认"提示给 LLM。
    """


class ToolNotFoundError(Exception): ...
class ToolArgsValidationError(Exception): ...
