"""轻量熔断器：threshold + window + open + half-open。

**只统计系统级故障**：网络错误 / 5xx / 超时（即 ErpSystemError 及子类）。
业务 4xx（权限不足 / 资源不存在 / 参数错）**不**计入失败统计——这些是单个用户/请求
的问题，不应该影响其他正常用户。
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class CircuitOpenError(Exception):
    """熔断器开启状态下拒绝请求。"""


class CircuitBreaker:
    def __init__(
        self,
        *, threshold: int = 5, window_seconds: float = 30.0,
        open_seconds: float = 60.0,
        countable_exceptions: tuple[type[BaseException], ...] | None = None,
    ):
        """
        Args:
            countable_exceptions: 计入失败统计的异常类型。其他异常 raise 但不累计。
                None = 所有 Exception 计入（向后兼容）。
                生产建议传 (ErpSystemError,) 等系统级异常元组。
        """
        self.threshold = threshold
        self.window = window_seconds
        self.open_window = open_seconds
        self.countable = countable_exceptions
        self._failures: list[float] = []
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is not None:
            if time.monotonic() - self._opened_at < self.open_window:
                return "open"
            return "half_open"
        return "closed"

    def _should_count(self, exc: BaseException) -> bool:
        if self.countable is None:
            return True
        return isinstance(exc, self.countable)

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        st = self.state
        if st == "open":
            raise CircuitOpenError("ERP 调用熔断中，请稍后重试")

        try:
            result = await fn()
        except Exception as e:
            if self._should_count(e):
                now = time.monotonic()
                # half-open 状态下任一失败立刻重新开熔，不等阈值
                if st == "half_open":
                    self._opened_at = now
                    self._failures.clear()
                else:
                    self._failures.append(now)
                    self._failures = [t for t in self._failures if now - t < self.window]
                    if len(self._failures) >= self.threshold:
                        self._opened_at = now
                        self._failures.clear()
            raise

        # 成功 → 如果是 half_open 则重置；closed 则保持
        if st == "half_open":
            self._opened_at = None
            self._failures.clear()
        return result
