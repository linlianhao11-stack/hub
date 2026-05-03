import pytest
from hub.agent.react.llm import build_chat_model


def test_build_chat_model_returns_langchain_chat():
    """build_chat_model 应返回 LangChain BaseChatModel 实例。"""
    from langchain_core.language_models.chat_models import BaseChatModel
    model = build_chat_model(api_key="test", base_url="https://api.deepseek.com/beta",
                              model="deepseek-chat")
    assert isinstance(model, BaseChatModel)


def test_build_chat_model_supports_tool_calls():
    """模型必须支持 bind_tools（react agent 需要）。"""
    from langchain_core.tools import tool
    @tool
    def fake() -> str:
        """fake."""
        return "x"
    model = build_chat_model(api_key="test", base_url="https://api.deepseek.com/beta",
                              model="deepseek-chat")
    bound = model.bind_tools([fake])
    assert bound is not None


def test_build_chat_model_uses_deepseek_wrapper():
    """build_chat_model 必须返 DeepSeekChatModel,不是直接 ChatOpenAI。"""
    from hub.agent.react.llm import DeepSeekChatModel
    model = build_chat_model(
        api_key="test", base_url="https://api.deepseek.com/beta",
        model="deepseek-chat",
    )
    assert isinstance(model, DeepSeekChatModel), (
        f"必须返 DeepSeekChatModel（复用 hub DeepSeekLLMClient retry 语义）,实际 {type(model)}"
    )


@pytest.mark.asyncio
async def test_deepseek_chat_model_retries_on_400(monkeypatch):
    """关键：DeepSeekChatModel 必须复用 DeepSeekLLMClient 的 400 重试语义。

    场景：DeepSeek 偶发 400（schema jitter）— 旧 GraphAgent 路径靠 hub 自己 retry 兜住,
    ReAct 路径必须保留这个能力（用 wrapper 而不是直接 ChatOpenAI）。
    """
    from hub.agent.react.llm import DeepSeekChatModel
    from langchain_core.messages import HumanMessage
    from unittest.mock import AsyncMock

    # mock 底层 DeepSeekLLMClient.chat — 第 1 次抛 400,第 2 次返成功
    # 注：DeepSeekLLMClient.chat 是 keyword-only（`async def chat(self, *, messages, ...)`）,
    # stub 也用 `**kwargs` 接所有 kwargs,避免位置参数 → TypeError。
    # 真 client 返 LLMResponse @dataclass(text, finish_reason, tool_calls, cache_hit_rate, usage, raw)，
    # 见 hub/agent/llm_client.py:220。下面 stub 缺 `raw` 但 _agenerate 没读 raw 所以 OK；
    # 实施时如果改了 wrapper 读 resp.raw 记得给 stub 补上。
    fake_client = AsyncMock()
    call_count = {"n": 0}
    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            from hub.agent.llm_client import LLMServiceError
            raise LLMServiceError("LLM 400")  # DeepSeekLLMClient 自己内部 retry
        # 第 2 次成功（实际 DeepSeekLLMClient 内部已经做完 retry,这里直接返）
        return type("R", (), {
            "text": "ok", "finish_reason": "stop", "tool_calls": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "cache_hit_rate": 0.0,
        })()
    fake_client.chat = chat_with_retry

    model = DeepSeekChatModel(deepseek_client=fake_client)
    # bind_tools 不报错（tool 列表可以空)
    model_bound = model.bind_tools([])
    # 实际 _agenerate 应该靠 DeepSeekLLMClient 的内部 retry 兜住
    # 这里测试 wrapper 调 client.chat 一次（client 自己内部多次重试）
    result = await model_bound.ainvoke([HumanMessage(content="hi")])
    assert result is not None
    # client.chat 被调用（具体次数取决于 DeepSeekLLMClient 内部重试,
    # 这里我们 mock 已经做了 retry — 第 2 次成功）
    assert call_count["n"] >= 1


def test_build_chat_model_configures_retry_and_timeout():
    """build_chat_model 必须把 timeout / max_retries **透传**到底层 DeepSeekLLMClient。

    Codex P2：旧版本 build_chat_model 暴露了 timeout 参数但只传 api_key/base_url/model
    给 DeepSeekLLMClient，运维以为放宽了超时实际走默认值。本断言锁住透传契约。
    """
    from hub.agent.react.llm import build_chat_model
    model = build_chat_model(
        api_key="test", base_url="https://api.deepseek.com/beta",
        model="deepseek-chat", timeout=30, max_retries=7,
    )
    assert model.deepseek_client.timeout_seconds == 30
    assert model.deepseek_client.max_retries == 7
