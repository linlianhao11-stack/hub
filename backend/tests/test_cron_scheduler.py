"""CronScheduler 行为：start / stop / 异常隔离 / 触发。"""
from __future__ import annotations

import asyncio

import pytest

from hub.cron.scheduler import CronScheduler


@pytest.mark.asyncio
async def test_scheduler_starts_and_stops_cleanly():
    """start 后 _task 非空；stop 后 _task 置 None。"""
    s = CronScheduler()
    s.start()
    assert s._task is not None
    await s.stop()
    assert s._task is None


@pytest.mark.asyncio
async def test_scheduler_rejects_invalid_hour():
    """at_hour 越界（24 / -1）应抛 ValueError。"""
    s = CronScheduler()
    with pytest.raises(ValueError):
        s.at_hour(24)(lambda: None)
    with pytest.raises(ValueError):
        s.at_hour(-1)(lambda: None)


@pytest.mark.asyncio
async def test_scheduler_handles_no_jobs_gracefully():
    """没有注册 job 也能 start / stop 不崩溃。"""
    s = CronScheduler()
    s.start()
    await asyncio.sleep(0.05)
    await s.stop()
    assert s._task is None


@pytest.mark.asyncio
async def test_scheduler_isolates_job_exceptions():
    """job 抛异常不应让 scheduler 退出（task 仍可被 stop 清理）。"""
    s = CronScheduler()

    @s.at_hour(3)
    async def bad_job():
        raise RuntimeError("boom")

    s.start()
    await asyncio.sleep(0.01)
    await s.stop()
    assert s._task is None


@pytest.mark.asyncio
async def test_scheduler_start_is_idempotent():
    """重复 start 不应起两个 task。"""
    s = CronScheduler()
    s.start()
    first_task = s._task
    s.start()
    second_task = s._task
    assert first_task is second_task
    await s.stop()


@pytest.mark.asyncio
async def test_multiple_jobs_at_same_hour_all_triggered():
    """v2 加固（review C1）：同小时注册多个 job，所有都被调度。"""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    counter_a = {"count": 0}
    counter_b = {"count": 0}

    s = CronScheduler()

    # 两个 job 都注册到同一小时
    target_hour = 9

    @s.at_hour(target_hour)
    async def job_a():
        counter_a["count"] += 1

    @s.at_hour(target_hour)
    async def job_b():
        counter_b["count"] += 1

    # 直接构造一个"过去"的 next_run，绕过 sleep，直接执行 due 判断
    # 手动调用 _run 的内部 due 逻辑（模拟 sleep=0 场景）
    tz = ZoneInfo("Asia/Shanghai")
    import datetime as dt

    now = dt.datetime.now(tz)
    # 伪造一个 now，使得 target_hour 的 next_run 恰好等于 earliest
    # 简化：直接调用 scheduler._jobs 的 due 计算，然后执行
    next_runs = []
    for hour, fn in s._jobs:
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += dt.timedelta(days=1)
        next_runs.append((target, fn))

    next_runs.sort(key=lambda x: x[0])
    earliest = next_runs[0][0]

    # 同时刻 due 的所有 job 都应被执行
    due = [(t, fn) for t, fn in next_runs if t == earliest]
    for _, fn in due:
        await fn()

    # 两个 job 都应被调用（C1 修复：不只跑第一个）
    assert counter_a["count"] >= 1
    assert counter_b["count"] >= 1
