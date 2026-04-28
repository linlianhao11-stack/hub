
import pytest
from fakeredis import aioredis as fakeredis_aio


@pytest.fixture
async def fake_redis():
    client = fakeredis_aio.FakeRedis()
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_submit_returns_task_id(fake_redis):
    from hub.queue.redis_streams import RedisStreamsRunner
    runner = RedisStreamsRunner(redis_client=fake_redis, stream_name="hub:tasks:default")
    tid = await runner.submit("test_task", {"foo": "bar"})
    assert isinstance(tid, str)
    assert len(tid) > 0


@pytest.mark.asyncio
async def test_consume_one_task(fake_redis):
    from hub.queue.redis_streams import RedisStreamsRunner
    runner = RedisStreamsRunner(redis_client=fake_redis, stream_name="hub:tasks:default")
    tid = await runner.submit("test_task", {"k": "v"})

    # 启动消费组
    await runner.ensure_consumer_group("hub-workers")
    msgs = await runner.read_one("hub-workers", "consumer-1", block_ms=10)
    assert len(msgs) == 1
    msg_id, payload = msgs[0]
    assert payload["task_type"] == "test_task"
    assert payload["task_id"] == tid


@pytest.mark.asyncio
async def test_ack_marks_handled(fake_redis):
    from hub.queue.redis_streams import RedisStreamsRunner
    runner = RedisStreamsRunner(redis_client=fake_redis, stream_name="hub:tasks:default")
    await runner.submit("t", {})
    await runner.ensure_consumer_group("hub-workers")
    msgs = await runner.read_one("hub-workers", "c1", block_ms=10)
    msg_id, _ = msgs[0]
    await runner.ack("hub-workers", msg_id)

    # ACK 后 PEL 不再有这条
    pending = await runner.pending_count("hub-workers")
    assert pending == 0


@pytest.mark.asyncio
async def test_dead_letter_after_max_retries(fake_redis):
    from hub.queue.redis_streams import RedisStreamsRunner
    runner = RedisStreamsRunner(
        redis_client=fake_redis,
        stream_name="hub:tasks:default",
        dead_stream_name="hub:tasks:dead",
    )
    await runner.submit("bad_task", {})
    await runner.ensure_consumer_group("hub-workers")
    msgs = await runner.read_one("hub-workers", "c1", block_ms=10)
    msg_id, _ = msgs[0]

    # 模拟 3 次重试都失败 → 移到死信
    for _ in range(3):
        await runner.mark_failed(msg_id, msg_data=msgs[0][1])
    await runner.move_to_dead("hub-workers", msg_id, msg_data=msgs[0][1])

    dead_count = await fake_redis.xlen("hub:tasks:dead")
    assert dead_count == 1
