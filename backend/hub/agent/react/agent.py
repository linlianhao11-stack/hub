"""ReActAgent — HUB v10 主 agent 类。

封装 langgraph.prebuilt.create_react_agent,对外保持 .run() 接口跟 GraphAgent 兼容,
让现有 DingTalk inbound handler / GraphAgentAdapter 不动。
"""
from __future__ import annotations
import logging
from typing import Any, Awaitable, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError

from hub.agent.react.context import tool_ctx, ToolContext
from hub.agent.react.prompts import SYSTEM_PROMPT


logger = logging.getLogger(__name__)


class ReActAgent:
    """ReAct agent 主类。

    对外接口跟 GraphAgent 兼容（worker.py + dingtalk_inbound 不动）。

    用法：
        agent = ReActAgent(chat_model=..., tools=ALL_TOOLS, checkpointer=...)
        reply = await agent.run(
            user_message="...",
            hub_user_id=1,
            conversation_id="cv-1",
            acting_as=None,
            channel_userid="ding-u",
        )
    """

    def __init__(
        self,
        *,
        chat_model: BaseChatModel,
        tools: list[BaseTool],
        checkpointer: BaseCheckpointSaver | None,
        recursion_limit: int = 15,
    ):
        self.chat_model = chat_model
        self.tools = tools
        self.checkpointer = checkpointer
        self.recursion_limit = recursion_limit

        self.compiled_graph = create_react_agent(
            model=chat_model,
            tools=tools,
            prompt=SYSTEM_PROMPT,
            checkpointer=checkpointer,
        )

    def _build_config(self, *, conversation_id: str, hub_user_id: int) -> dict:
        """thread_id = f'react:{conv}:{user}' — 跟旧 GraphAgent checkpoint 隔离 namespace。"""
        return {
            "configurable": {
                "thread_id": f"react:{conversation_id}:{hub_user_id}",
            },
            "recursion_limit": self.recursion_limit,
        }

    async def run(
        self,
        *,
        user_message: str,
        hub_user_id: int,
        conversation_id: str,
        acting_as: int | None = None,
        channel_userid: str = "",
    ) -> str | None:
        """跑一轮对话,返 LLM 最终自然语言回复。

        流程:
          1. set ContextVar tool_ctx（hub_user_id / acting_as / conv / channel）
          2. ainvoke compiled_graph 传入 messages 增量（HumanMessage(user_message)）
          3. 拿最后一条 AIMessage.content 当 reply
          4. reset ContextVar
        """
        config = self._build_config(
            conversation_id=conversation_id, hub_user_id=hub_user_id,
        )
        ctx: ToolContext = {
            "hub_user_id": hub_user_id,
            "acting_as": acting_as,
            "conversation_id": conversation_id,
            "channel_userid": channel_userid,
        }
        token = tool_ctx.set(ctx)
        try:
            result = await self.compiled_graph.ainvoke(
                {"messages": [HumanMessage(content=user_message)]},
                config=config,
            )
            messages = result.get("messages", [])
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    return msg.content
            return None
        except GraphRecursionError:
            # recursion_limit 触发（DeepSeek 死循环 / LLM 反复调相同 tool 等)→ 友好返
            logger.warning(
                "ReActAgent recursion_limit 触发 conv=%s user=%s msg=%r",
                conversation_id, hub_user_id, user_message[:200],
            )
            return "推理步骤超限,请简化请求或联系管理员。"
        except Exception:
            logger.exception(
                "ReActAgent 抛异常 conv=%s user=%s msg=%r",
                conversation_id, hub_user_id, user_message[:200],
            )
            raise
        finally:
            tool_ctx.reset(token)
