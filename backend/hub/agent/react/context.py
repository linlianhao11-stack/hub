"""Tool 调用 context — 由 ReActAgent 在每次入口 set，tool 内部 get。

不通过 LangChain tool args 传 hub_user_id 等内部字段，避免 LLM 看到 / 误改。
跟当前 worker.py `_tool_ctx` 同一个 ContextVar 实例（迁移期 worker 改 import 路径）。
"""
from __future__ import annotations
from contextvars import ContextVar
from typing import TypedDict


class ToolContext(TypedDict):
    hub_user_id: int
    acting_as: int | None
    conversation_id: str
    channel_userid: str


tool_ctx: ContextVar[ToolContext | None] = ContextVar("hub_react_tool_ctx", default=None)
