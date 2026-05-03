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
    """DeepSeekChatModel 必须委托给 DeepSeekLLMClient.chat() — 由 client 内部完成 400 retry,
    wrapper 不重复 retry（防失败路径 client_retries × wrapper_retries 翻倍延迟）。

    场景：DeepSeek 偶发 400（schema jitter）— 旧 GraphAgent 路径靠 hub 自己 retry 兜住。
    本测试验证 wrapper 调 chat() 仅一次,retry 完全由底层 client 完成（mock 模拟"已完成 retry 后成功"）。
    """
    from hub.agent.react.llm import DeepSeekChatModel
    from hub.agent.llm_client import DeepSeekLLMClient
    from langchain_core.messages import HumanMessage
    from unittest.mock import MagicMock

    call_count = {"n": 0}
    async def chat_stub(*, messages, **kwargs):
        call_count["n"] += 1
        # client 内部已完成 retry，这里直接返成功（模拟 client 内部 retry 后恢复）
        return type("R", (), {
            "text": "ok", "finish_reason": "stop", "tool_calls": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "cache_hit_rate": 0.0,
        })()

    # MagicMock(spec=DeepSeekLLMClient) 通过 isinstance(x, DeepSeekLLMClient) — 让 Pydantic v2 接受
    fake_client = MagicMock(spec=DeepSeekLLMClient)
    fake_client.chat = chat_stub

    model = DeepSeekChatModel(deepseek_client=fake_client)
    model_bound = model.bind_tools([])
    result = await model_bound.ainvoke([HumanMessage(content="hi")])
    assert result is not None
    # 关键断言: wrapper 只调 client.chat 一次（retry 在底层 client 内部，不在 wrapper 重复）
    assert call_count["n"] == 1, (
        f"wrapper 必须委托给 client retry,只调 chat() 一次（实际 {call_count['n']} 次）— "
        f"否则 client_retries × wrapper_retries 双重 retry 会让失败路径延迟翻倍"
    )


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
