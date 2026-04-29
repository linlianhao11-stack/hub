"""asyncio cron 调度器：每天 03:00 / 04:00 等时刻触发 job。

设计：
- 用 asyncio 后台 task 实现，不引入 APScheduler 等额外依赖
- `at_hour(hour)` 装饰器注册 job；scheduler.start() 启动；scheduler.stop() 停
- 运行循环：算出最近一个未来触发时刻 → asyncio.sleep 到那时 → 调 job
- 异常隔离：单个 job 抛错只记日志，不影响 scheduler 主循环
- 优雅关闭：stop() 标记 stop=True + cancel task；CancelledError 安静吞掉
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger("hub.cron")

JobFn = Callable[[], Awaitable[None]]


class CronScheduler:
    """以小时粒度触发的 asyncio cron 调度器。"""

    def __init__(self, *, tz_name: str = "Asia/Shanghai"):
        self.tz = ZoneInfo(tz_name)
        self._jobs: list[tuple[int, JobFn]] = []  # (target_hour, callable)
        self._task: asyncio.Task | None = None
        self._stop = False

    def at_hour(self, hour: int):
        """装饰器：注册一个在每天指定整点触发的 job。

        用法：
            @scheduler.at_hour(3)
            async def my_job():
                ...
        """
        if not (0 <= hour <= 23):
            raise ValueError(f"hour 必须 0-23，收到 {hour}")

        def decorator(fn: JobFn):
            self._jobs.append((hour, fn))
            return fn

        return decorator

    async def _run(self):
        """主循环：算最近触发时刻 → 睡到那时 → 调 job → 重复。"""
        while not self._stop:
            if not self._jobs:
                # 没注册 job，每分钟醒一次看看
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    break
                continue

            now = datetime.now(self.tz)
            next_runs = []
            for hour, fn in self._jobs:
                target = now.replace(
                    hour=hour, minute=0, second=0, microsecond=0,
                )
                if target <= now:
                    target += timedelta(days=1)
                next_runs.append((target, fn))
            next_runs.sort(key=lambda x: x[0])
            target, fn = next_runs[0]
            sleep_seconds = (target - now).total_seconds()
            try:
                await asyncio.sleep(sleep_seconds)
                if self._stop:
                    break
                logger.info(f"cron 触发: {fn.__name__}")
                try:
                    await fn()
                except Exception:
                    logger.exception(f"cron job {fn.__name__} 失败")
            except asyncio.CancelledError:
                break

    def start(self):
        """启动后台 task；幂等（已启动则跳过）。"""
        if self._task is not None:
            return
        self._stop = False
        self._task = asyncio.create_task(self._run(), name="hub.cron.scheduler")

    async def stop(self):
        """优雅停止：标记 stop + cancel task，吞掉 CancelledError。"""
        self._stop = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
