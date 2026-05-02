"""Plan 6 v9：最小 metrics 适配层。

当前是 stub（log 出来）；后续替换为 Prometheus / DataDog / 阿里云 SLS / 等。
让 agent 代码可以在 fallback / 异常路径调 `metrics.incr("name", tags={...})`，
不依赖具体后端，便于切换。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("hub.metrics")


def incr(name: str, *, tags: dict[str, Any] | None = None, value: int = 1) -> None:
    """打点 counter（stub：当前只记 INFO log）。

    生产替换示例：
        from prometheus_client import Counter
        _counters[name].labels(**tags).inc(value)
    """
    logger.info("metric.incr name=%s tags=%s value=%d", name, tags or {}, value)


def gauge(name: str, *, value: float, tags: dict[str, Any] | None = None) -> None:
    """打点 gauge（stub）。"""
    logger.info("metric.gauge name=%s tags=%s value=%s", name, tags or {}, value)


def timing(name: str, *, ms: float, tags: dict[str, Any] | None = None) -> None:
    """打点 timing（stub）。"""
    logger.info("metric.timing name=%s tags=%s ms=%.2f", name, tags or {}, ms)
