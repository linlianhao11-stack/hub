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
