"""LangGraph config helper — per-(conversation_id, hub_user_id) thread_id 复合 key。

强约束：所有 LangGraph checkpoint / SessionMemory / ConfirmGate 都以 (conv, user) 为边界，
钉钉群聊里不同用户必须互不可见（spec §2.1）。
"""
from __future__ import annotations


def build_thread_id(*, conversation_id: str, hub_user_id: int) -> str:
    """构造 LangGraph checkpoint 用的复合 thread_id。

    格式：f"{conversation_id}:{hub_user_id}"
    """
    if not conversation_id:
        raise ValueError("conversation_id 不能为空")
    if not hub_user_id or hub_user_id <= 0:
        raise ValueError(f"hub_user_id 必须是正整数，不能是 {hub_user_id!r}")
    return f"{conversation_id}:{hub_user_id}"


def parse_thread_id(thread_id: str) -> tuple[str, int]:
    """从 thread_id 反解 (conversation_id, hub_user_id)。"""
    if ":" not in thread_id:
        raise ValueError(f"thread_id 格式错误：{thread_id!r}，应为 'conv:user'")
    conv, user_str = thread_id.rsplit(":", 1)
    return conv, int(user_str)


def build_langgraph_config(
    *,
    conversation_id: str,
    hub_user_id: int,
    extra: dict | None = None,
) -> dict:
    """构造 LangGraph ainvoke 用的 config。永远不要传 None / 漏写 thread_id。"""
    return {
        "configurable": {
            "thread_id": build_thread_id(
                conversation_id=conversation_id, hub_user_id=hub_user_id,
            ),
            **(extra or {}),
        }
    }
