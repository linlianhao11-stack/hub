import asyncio

import pytest

from hub.circuit_breaker.erp_breaker import CircuitBreaker, CircuitOpenError


@pytest.mark.asyncio
async def test_closed_passes_through():
    cb = CircuitBreaker(threshold=5, window_seconds=30, open_seconds=60)

    async def ok():
        return "result"
    assert await cb.call(ok) == "result"


@pytest.mark.asyncio
async def test_opens_after_threshold_failures():
    cb = CircuitBreaker(threshold=3, window_seconds=30, open_seconds=60)

    async def bad():
        raise RuntimeError("err")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.call(bad)

    assert cb.state == "open"
    with pytest.raises(CircuitOpenError):
        await cb.call(bad)


@pytest.mark.asyncio
async def test_half_open_after_open_window():
    cb = CircuitBreaker(threshold=2, window_seconds=30, open_seconds=0.1)

    async def bad():
        raise RuntimeError("err")
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(bad)
    assert cb.state == "open"

    await asyncio.sleep(0.15)

    async def ok():
        return "ok"
    result = await cb.call(ok)
    assert result == "ok"
    assert cb.state == "closed"


@pytest.mark.asyncio
async def test_half_open_failure_reopens():
    cb = CircuitBreaker(threshold=2, window_seconds=30, open_seconds=0.1)

    async def bad():
        raise RuntimeError("err")
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(bad)
    await asyncio.sleep(0.15)

    with pytest.raises(RuntimeError):
        await cb.call(bad)
    assert cb.state == "open"


@pytest.mark.asyncio
async def test_failures_outside_window_reset():
    cb = CircuitBreaker(threshold=3, window_seconds=0.1, open_seconds=60)

    async def bad():
        raise RuntimeError("err")

    with pytest.raises(RuntimeError):
        await cb.call(bad)
    await asyncio.sleep(0.15)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(bad)
    assert cb.state == "closed"


@pytest.mark.asyncio
async def test_only_countable_exceptions_count():
    """业务 4xx（不在 countable_exceptions 里）不计入熔断统计。"""
    class FakeSystemError(Exception):
        pass

    class FakeBizError(Exception):
        pass

    cb = CircuitBreaker(
        threshold=3, window_seconds=30, open_seconds=60,
        countable_exceptions=(FakeSystemError,),
    )

    async def biz_bad():
        raise FakeBizError("403 perm")

    for _ in range(5):
        with pytest.raises(FakeBizError):
            await cb.call(biz_bad)
    assert cb.state == "closed"

    async def sys_bad():
        raise FakeSystemError("503")
    for _ in range(3):
        with pytest.raises(FakeSystemError):
            await cb.call(sys_bad)
    assert cb.state == "open"
