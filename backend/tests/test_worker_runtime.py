import pytest
from fakeredis import aioredis as fakeredis_aio


@pytest.fixture
async def fake_redis():
    client = fakeredis_aio.FakeRedis()
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_handler_invoked_and_acked(fake_redis):
    """注册 handler 后投递任务 → run_once 拉到 → handler 被调用 → ACK。

    用 run_once + 注入 redis_client + 短 block_ms 避免阻塞和 client 关闭问题。
    """
    from hub.queue import RedisStreamsRunner
    from hub.worker_runtime import WorkerRuntime

    runner = RedisStreamsRunner(redis_client=fake_redis)
    await runner.submit("noop_task", {"k": "v"})
    await runner.ensure_consumer_group("hub-workers")

    runtime = WorkerRuntime(
        consumer="test-consumer-1",
        block_ms=100,           # 短 block：拉不到立刻返回
        redis_client=fake_redis,  # 外部注入，runtime 不会 close
    )

    received: dict = {}
    async def handler(msg_data: dict):
        received.update(msg_data.get("payload", {}))
    runtime.register("noop_task", handler)

    handled = await runtime.run_once(runner)
    assert handled is True
    assert received == {"k": "v"}

    # ACK 后 PEL 应为空（仍可访问 fake_redis，因为 runtime 没关它）
    pending = await runner.pending_count("hub-workers")
    assert pending == 0


@pytest.mark.asyncio
async def test_unknown_task_type_goes_to_dead_stream(fake_redis):
    """未注册的 task_type → 直接进死信流。"""
    from hub.queue import RedisStreamsRunner
    from hub.worker_runtime import WorkerRuntime

    runner = RedisStreamsRunner(redis_client=fake_redis)
    await runner.submit("unknown_task", {})
    await runner.ensure_consumer_group("hub-workers")

    runtime = WorkerRuntime(
        consumer="test-consumer-2",
        block_ms=100,
        redis_client=fake_redis,
    )
    # 不注册任何 handler

    handled = await runtime.run_once(runner)
    assert handled is True

    dead_count = await fake_redis.xlen("hub:tasks:dead")
    assert dead_count == 1


@pytest.mark.asyncio
async def test_run_once_returns_false_on_empty(fake_redis):
    """流空时 run_once block 短超时后返回 False，不阻塞。"""
    from hub.queue import RedisStreamsRunner
    from hub.worker_runtime import WorkerRuntime

    runner = RedisStreamsRunner(redis_client=fake_redis)
    await runner.ensure_consumer_group("hub-workers")

    runtime = WorkerRuntime(
        consumer="test-consumer-3",
        block_ms=50,
        redis_client=fake_redis,
    )
    handled = await runtime.run_once(runner)
    assert handled is False
