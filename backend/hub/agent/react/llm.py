"""DeepSeek 适配为 LangChain BaseChatModel。

包装 hub 现有 DeepSeekLLMClient（已经内置 staging 实战补过的 retry 语义,见
backend/hub/agent/llm_client.py）,让 LangGraph create_react_agent 能直接用。

关键：**不**直接用 langchain-openai.ChatOpenAI,因为 ChatOpenAI 默认不重试 400
（DeepSeek 偶发 schema-jitter 400 是 staging 真踩坑出来的）+ 不写 hub chat_log
（admin 决策链审计依赖)。
"""
from __future__ import annotations
import json
import logging

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import ConfigDict, Field

from hub.agent.llm_client import DeepSeekLLMClient


logger = logging.getLogger(__name__)


def _messages_to_openai_format(messages: list[BaseMessage]) -> list[dict]:
    """LangChain BaseMessage → OpenAI ChatCompletion messages 格式。"""
    out = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            out.append({"role": "system", "content": msg.content})
        elif isinstance(msg, HumanMessage):
            out.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            entry: dict = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"]),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            out.append(entry)
        elif isinstance(msg, ToolMessage):
            out.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id,
                "content": msg.content if isinstance(msg.content, str) else json.dumps(msg.content),
            })
        else:
            logger.warning("Unknown message type: %s", type(msg).__name__)
    return out


def _tools_to_openai_schemas(tools: list[BaseTool]) -> list[dict]:
    """LangChain BaseTool → OpenAI function calling schema dict。

    LangChain Tool.args_schema 是 Pydantic; LangChain 已有 utility 转 OpenAI schema。
    """
    from langchain_core.utils.function_calling import convert_to_openai_function
    return [
        {"type": "function", "function": convert_to_openai_function(tool)}
        for tool in tools
    ]


class DeepSeekChatModel(BaseChatModel):
    """DeepSeekLLMClient 包装成 LangChain BaseChatModel。

    复用 hub 现有 DeepSeekLLMClient 的:
    - retry 语义（{400, 408, 425, 429, 500-504, TransportError} + 指数退避）
    - chat_log 写入（cache_hit_rate / token usage）
    - 错误分类

    仅实现 ReAct 用得上的 _agenerate + bind_tools 接口。
    """

    # Pydantic v2 配置（langchain-core 0.3.x 已用 Pydantic 2;`class Config` 无效）
    model_config = ConfigDict(arbitrary_types_allowed=True)  # DeepSeekLLMClient 不是 Pydantic

    deepseek_client: DeepSeekLLMClient
    # 可变默认值用 Field(default_factory=...),避免 Pydantic v2 的 mutable default 校验报错
    bound_tools: list[dict] = Field(default_factory=list)  # OpenAI schema dict 列表（bind_tools 后填）
    temperature: float = 0.0
    max_tokens: int = 4096

    @property
    def _llm_type(self) -> str:
        return "deepseek-react"

    def bind_tools(self, tools: list[BaseTool], **kwargs) -> "DeepSeekChatModel":
        """转成 OpenAI schema list,存进新实例的 bound_tools。
        LangGraph create_react_agent 内部调本方法把 ALL_TOOLS bind 上去。
        """
        new = self.__class__(
            deepseek_client=self.deepseek_client,
            bound_tools=_tools_to_openai_schemas(tools),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return new

    async def _agenerate(
        self, messages: list[BaseMessage], stop=None,
        run_manager=None, **kwargs,
    ) -> ChatResult:
        """LangChain async generation 入口 — 转 hub DeepSeekLLMClient.chat()。

        DeepSeekLLMClient 内部已做完整 retry（400/408/425/429/500-504/TransportError +
        指数退避）。wrapper 不重复 retry — 若 client 抛 LLMServiceError 直接上报,
        LangGraph 收到后由 ReActAgent.run 顶层 except 兜底（→ 友好错误 / fallback）。
        """
        oai_messages = _messages_to_openai_format(messages)
        resp = await self.deepseek_client.chat(
            messages=oai_messages,
            tools=self.bound_tools or None,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        # resp.text / resp.tool_calls / resp.finish_reason
        ai_msg_kwargs: dict = {"content": resp.text or ""}
        if resp.tool_calls:
            valid_calls = []
            invalid_calls = []
            for tc in resp.tool_calls:
                try:
                    parsed_args = json.loads(tc["function"]["arguments"])
                    # AIMessage.tool_calls[*].args 期望 dict — 合法 JSON 但不是 object
                    # （如 "[]" / "null" / "\"some string\""）也要降级,防 Pydantic
                    # ValidationError 在 LangGraph 编排里炸掉整个 turn。
                    if not isinstance(parsed_args, dict):
                        raise TypeError(
                            f"tool_call.function.arguments 必须是 JSON object,"
                            f"实际 {type(parsed_args).__name__}: {parsed_args!r}"
                        )
                    valid_calls.append({
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "args": parsed_args,
                    })
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    logger.warning(
                        "DeepSeek 返回 tool_call 解析失败,降级为 invalid_tool_call: %s — tc=%r",
                        e, tc,
                    )
                    invalid_calls.append({
                        "id": tc.get("id", ""),
                        "name": tc.get("function", {}).get("name", ""),
                        "args": tc.get("function", {}).get("arguments", ""),
                        "error": str(e),
                    })
            if valid_calls:
                ai_msg_kwargs["tool_calls"] = valid_calls
            if invalid_calls:
                ai_msg_kwargs["invalid_tool_calls"] = invalid_calls
        # v12: 把 DeepSeek 返的 usage 写到 AIMessage.usage_metadata,
        # 让 ReActAgent.sum_tokens_used 能聚合 → ConversationLog.tokens_used 有真实数。
        # DeepSeekLLMClient.chat 返 LLMResponse,usage 是 dict(对应 OpenAI usage 字段)。
        usage = getattr(resp, "usage", None) or {}
        prompt_t = int(usage.get("prompt_tokens") or 0)
        completion_t = int(usage.get("completion_tokens") or 0)
        if prompt_t or completion_t:
            ai_msg_kwargs["usage_metadata"] = {
                "input_tokens": prompt_t,
                "output_tokens": completion_t,
                "total_tokens": prompt_t + completion_t,
            }
        ai_msg = AIMessage(**ai_msg_kwargs)
        return ChatResult(generations=[ChatGeneration(message=ai_msg)])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """同步入口 — ReAct 全异步,本方法理论上不会被调,raise NotImplementedError。"""
        raise NotImplementedError(
            "DeepSeekChatModel 仅支持 async（ReAct agent 全 async）。"
            "请用 model.ainvoke() 而非 invoke()。"
        )


def build_chat_model(
    *,
    api_key: str,
    base_url: str,
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    timeout: int = 60,
    max_retries: int = 4,
) -> BaseChatModel:
    """构造 DeepSeekChatModel（包装 hub DeepSeekLLMClient）。

    LangGraph create_react_agent 拿这个 model 后会调 .bind_tools(ALL_TOOLS) +
    .ainvoke() 驱动 ReAct 循环。底层每次 LLM call 都走 DeepSeekLLMClient,
    自动获得 hub 历史踩坑出来的 retry / 错误分类 / chat_log 审计语义。

    timeout / max_retries 必须**透传**到底层 client —— 否则配置看起来生效但实际走
    DeepSeekLLMClient 默认值（很容易让运维误以为已经放宽超时 / 加大重试次数）。
    """
    client = DeepSeekLLMClient(
        api_key=api_key, base_url=base_url, model=model,
        timeout_seconds=timeout, max_retries=max_retries,
    )
    return DeepSeekChatModel(
        deepseek_client=client,
        temperature=temperature,
        max_tokens=max_tokens,
    )
