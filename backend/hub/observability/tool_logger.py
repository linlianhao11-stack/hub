"""tool_call_log 写入 context manager（Plan 6 Task 1）。

Plan 5 task_logger 的姊妹模块：
  - task_logger 是入站消息级（每条钉钉消息一行）
  - tool_logger 是 round 内 tool 级（每次 tool 调用一行）

设计原则：
  - 业务永远不被可观察性阻塞——写入失败仅打 exception log
  - 超过 10KB 的 args_json / result_json 自动截断（保留 keys + 前 N 个 items + _truncated: true 标记）
  - 异常照常向上抛，确保 tool 层错误不被吞
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager

from hub.models.conversation import ToolCallLog

logger = logging.getLogger("hub.observability.tool_logger")


@asynccontextmanager
async def log_tool_call(
    *,
    conversation_id: str,
    round_idx: int,
    tool_name: str,
    args: dict,
):
    """Context manager：进入时无操作，出口写一行 tool_call_log。

    用法：
        async with log_tool_call(conversation_id=..., round_idx=..., tool_name=..., args=...) as ctx:
            result = await tool.fn(...)
            ctx.set_result(result)

    注意：即使 tool 抛异常，也会写入一行带 error 字段的记录，然后重新抛出异常。
    """
    started = time.monotonic()
    ctx = _ToolCallContext()
    raised: Exception | None = None
    try:
        yield ctx
    except Exception as e:
        raised = e
        ctx._error = str(e)[:500]
    finally:
        try:
            await ToolCallLog.create(
                conversation_id=conversation_id,
                round_idx=round_idx,
                tool_name=tool_name,
                args_json=truncate_for_log(args, max_size_kb=10),  # M1: args 同样截断，防止超大 JSONB
                result_json=ctx._result,
                duration_ms=int((time.monotonic() - started) * 1000),
                error=ctx._error,
                # called_at 由 ToolCallLog.called_at(auto_now_add=True) 自动填充，无需显式传入
            )
        except Exception:
            logger.exception("tool_call_log 写入失败（不阻塞业务）")
        if raised is not None:
            raise raised


class _ToolCallContext:
    """tool_call 内部上下文，供 handler 设置调用结果。"""

    def __init__(self) -> None:
        self._result: object = None
        self._error: str | None = None

    def set_result(self, result: object) -> None:
        """设置 tool 调用结果，超过 10KB 自动截断。"""
        self._result = truncate_for_log(result, max_size_kb=10)


def truncate_for_log(value: object, max_size_kb: int = 10) -> object:
    """截断大 JSON，防止 JSONB 写入过大。

    策略：
    1. 先序列化为 JSON 字符串，计算字节大小。
    2. 若未超限，原样返回（保留原对象）。
    3. 若超限：
       - dict：保留所有 keys（值替换为 "..."），输出前 N 个 k-v（N 视剩余空间），标记 _truncated: true。
       - list：输出前 N 个元素，标记 _truncated: true。
       - 其他：转 str 并截断。

    返回值始终是 JSON 可序列化的 Python 对象（dict/list/str/None 等）。

    边界：单 key value 太大时（>max_size_kb），返回值仅含 schema 提示，实际数据不保留。
    例如当字典中某个 value 本身就超限时：
        >>> truncate_for_log({"data": "x" * 20000})
        {"_truncated": True, "_original_keys": ["data"]}
    调用方须注意：此时只能知道原来有哪些 key，具体内容不在日志中。
    """
    max_bytes = max_size_kb * 1024

    # 快速检查：小对象直接跳过
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        # 不可序列化：转 str
        return str(value)[:max_bytes]

    if len(serialized.encode("utf-8")) <= max_bytes:
        # v1 加固：把 Decimal / datetime / set / bytes 等非 JSON 原生类型预转 str，
        # 避免 JSONField 写库时抛 TypeError 后被 try/except 静默吞日志
        return json.loads(serialized)

    # 超限截断
    if isinstance(value, dict):
        # 先保留所有 key（展示结构），再尝试逐个填入 value
        truncated: dict = {"_truncated": True, "_original_keys": list(value.keys())}
        for k, v in value.items():
            candidate = dict(truncated)
            candidate[k] = v
            if len(json.dumps(candidate, ensure_ascii=False, default=str).encode("utf-8")) > max_bytes:
                break
            truncated[k] = v
        return json.loads(json.dumps(truncated, ensure_ascii=False, default=str))

    if isinstance(value, list):
        # 逐个追加元素，直到超限
        result_list: list = []
        for item in value:
            candidate = result_list + [item]
            candidate_json = json.dumps({"_truncated": True, "items": candidate}, ensure_ascii=False, default=str)
            if len(candidate_json.encode("utf-8")) > max_bytes:
                break
            result_list.append(item)
        out = {"_truncated": True, "items": result_list, "_original_count": len(value)}
        return json.loads(json.dumps(out, ensure_ascii=False, default=str))

    # 其他类型：str 截断
    return serialized[:max_bytes]
