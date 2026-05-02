import pytest
from fakeredis.aioredis import FakeRedis
from hub.agent.memory.session import SessionMemory


@pytest.mark.asyncio
async def test_append_strips_reasoning_content():
    """assistant message append 时必须剥离 reasoning_content（DeepSeek 多轮 400 陷阱）。"""
    r = FakeRedis(decode_responses=False)
    sm = SessionMemory(r)
    await sm.append(
        conversation_id="c1", hub_user_id=1,
        message={
            "role": "assistant",
            "content": "答案是 9.11 < 9.9",
            "reasoning_content": "比较小数点后位数...",  # 这个必须被剥离
        },
    )
    msgs = await sm.get_messages(conversation_id="c1", hub_user_id=1)
    assert msgs[-1].get("reasoning_content") is None
    assert msgs[-1]["content"] == "答案是 9.11 < 9.9"
    await r.aclose()
