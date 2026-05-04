"""ReActAgent.run 的会话级观测落库。

写 ConversationLog 表(plan 6 task 14 设计了但漏了 production 写入,
导致 admin dashboard LLM 成本指标永远显示 0)。

设计:
  - RunStats dataclass 收集一轮 run() 的元数据(开始/结束/rounds/tokens/status)
  - log_conversation() 异步 update_or_create,失败仅 log 不阻塞业务
  - 复合 unique (conversation_id, hub_user_id):钉钉群聊里多人共享同一 cid,
    必须按 (cid, user) 二维隔离(参见 ConversationLog Meta unique_together)
  - tokens_cost_yuan 当前留 None(成本估算需要 model 单价表,未实现);dashboard
    自动用 0 兜底,后续 follow-up 加 cost calculator
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("hub.agent.react.run_logger")


@dataclass
class RunStats:
    """单次 ReActAgent.run() 的运行元数据。"""
    conversation_id: str
    hub_user_id: int
    channel_userid: str
    started_at: datetime
    ended_at: datetime | None = None
    rounds_count: int = 0
    tokens_used: int = 0
    final_status: str = "running"  # success / fallback_to_rule / failed_system_final
    error_summary: str | None = None


def estimate_rounds(messages: list) -> int:
    """估算本轮对话的 LLM round 数 = AIMessage 条数。"""
    from langchain_core.messages import AIMessage
    return sum(1 for m in messages if isinstance(m, AIMessage))


def sum_tokens_used(messages: list) -> int:
    """汇总 LangGraph state 里所有 AIMessage 的 usage_metadata.total_tokens。

    LangChain 的 AIMessage.usage_metadata 在 ChatOpenAI 等返回里自带。
    没有就当 0,跳过(不阻塞 ConversationLog 写入)。
    """
    total = 0
    for m in messages:
        usage = getattr(m, "usage_metadata", None)
        if not usage:
            continue
        if isinstance(usage, dict):
            total += int(usage.get("total_tokens") or 0)
        else:
            total += int(getattr(usage, "total_tokens", 0) or 0)
    return total


async def log_conversation(stats: RunStats) -> None:
    """fire-and-forget 写 ConversationLog;失败仅 log。

    用 update_or_create 兼容多次写入(理论上一个 run 只调一次,但加幂等更稳)。
    """
    try:
        from hub.models.conversation import ConversationLog

        await ConversationLog.update_or_create(
            conversation_id=stats.conversation_id,
            hub_user_id=stats.hub_user_id,
            defaults={
                "channel_userid": stats.channel_userid or "",
                "started_at": stats.started_at,
                "ended_at": stats.ended_at,
                "rounds_count": stats.rounds_count,
                "tokens_used": stats.tokens_used,
                "final_status": stats.final_status,
                "error_summary": stats.error_summary,
                # tokens_cost_yuan 暂留 None(model 单价表 follow-up)
            },
        )
    except Exception:
        logger.exception(
            "ConversationLog.update_or_create 失败 conv=%s（不阻塞业务）",
            stats.conversation_id,
        )
