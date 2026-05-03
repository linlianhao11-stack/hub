"""ReAct tool 调用底层业务函数的统一包装。

ReAct @tool 函数都通过本 helper 调 erp_tools / analyze_tools / generate_tools /
draft_tools 真业务函数。统一做：
  1. require_permissions(perm) — 权限 fail-closed（无权限直接抛 `BizError(BizErrorCode.PERM_NO_*)`，dingtalk_inbound 已有 `except BizError → 翻译中文给用户`）
  2. log_tool_call — 写 tool_call_log（admin 决策链审计）
  3. 注入 ctx kwargs — acting_as_user_id 来自 ContextVar tool_ctx

不通过 ToolRegistry.call 是因为：
  - ReAct write tool 的 confirm 流程跟 ToolRegistry 内置的两步协议不兼容
  - ToolRegistry.call 会再做 strict schema 校验,跟 LangChain @tool 自带 schema 重复
"""
from __future__ import annotations
from typing import Any, Awaitable, Callable

from hub.agent.react.context import tool_ctx
from hub.observability.tool_logger import log_tool_call
from hub.permissions import require_permissions


async def invoke_business_tool(
    *,
    tool_name: str,
    perm: str,
    args: dict,
    fn: Callable[..., Awaitable[Any]],
    extra_ctx_kwargs: dict | None = None,
) -> Any:
    """统一调底层业务函数。

    Args:
        tool_name: 写 tool_call_log 用的名字（按底层函数名,如 "search_customers"）。
        perm: 权限 code（如 "usecase.query_customer.use"）。
        args: LLM 传的业务 args（不含 hub_user_id 等 ctx 字段）。
        fn: 底层业务函数（如 erp_tools.search_customers）。函数必须接
            `acting_as_user_id` kwarg；其它 ctx kwargs 通过 extra_ctx_kwargs 加。
        extra_ctx_kwargs: write 类底层需要的额外 ctx（如 hub_user_id /
            conversation_id / confirmation_action_id）。read 类一般为 None。

    Returns:
        fn 的返值。
    """
    c = tool_ctx.get()
    if c is None:
        raise RuntimeError("tool_ctx 未 set — react agent 入口必须先 set 才能调 tool")

    # 1. 权限 fail-closed
    await require_permissions(c["hub_user_id"], [perm])

    # 2. 审计 log + 调 fn
    async with log_tool_call(
        conversation_id=c["conversation_id"],
        hub_user_id=c["hub_user_id"],
        round_idx=0,
        tool_name=tool_name,
        args=args,
    ) as log_ctx:
        kwargs = {
            **args,
            "acting_as_user_id": c.get("acting_as") or c["hub_user_id"],
        }
        if extra_ctx_kwargs:
            kwargs.update(extra_ctx_kwargs)
        result = await fn(**kwargs)
        log_ctx.set_result(result)
        return result
