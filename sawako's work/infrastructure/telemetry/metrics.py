"""
kaoruko/infrastructure/telemetry/metrics.py

Lightweight performance metrics collector.
Tracks latencies, counters, and system health.
No external telemetry service — all local.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Optional

from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("telemetry.metrics")


@dataclass
class LatencySample:
    operation: str
    duration_ms: float
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


class MetricsCollector:
    """
    In-memory metrics collector.

    Usage:
        metrics.increment("wake_detections")
        metrics.increment("actions_executed")

        with metrics.timer("stt_latency"):
            result = await stt.transcribe(audio)

        report = metrics.get_report()
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._latencies: dict[str, deque[LatencySample]] = defaultdict(
            lambda: deque(maxlen=500)
        )
        self._gauges: dict[str, float] = {}
        self._lock = threading.Lock()
        self._started_at: float = 0.0
        self._running = False

    def start(self) -> None:
        self._started_at = time.time()
        self._running = True
        log.info("metrics_started")

    def stop(self) -> None:
        self._running = False
        self._emit_final_report()

    # ── Counters ─────────────────────────────────────────────────────────────

    def increment(self, name: str, by: int = 1) -> None:
        with self._lock:
            self._counters[name] += by

    def get_counter(self, name: str) -> int:
        return self._counters.get(name, 0)

    # ── Gauges ────────────────────────────────────────────────────────────────

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def get_gauge(self, name: str) -> Optional[float]:
        return self._gauges.get(name)

    # ── Latency tracking ─────────────────────────────────────────────────────

    def record_latency(
        self,
        operation: str,
        duration_ms: float,
        metadata: Optional[dict] = None,
    ) -> None:
        with self._lock:
            self._latencies[operation].append(
                LatencySample(
                    operation=operation,
                    duration_ms=duration_ms,
                    metadata=metadata or {},
                )
            )

    class _Timer:
        """Context manager for timing a block of code."""
        def __init__(self, collector: "MetricsCollector", name: str) -> None:
            self._collector = collector
            self._name = name
            self._start: float = 0.0

        def __enter__(self) -> "_Timer":
            self._start = time.perf_counter()
            return self

        def __exit__(self, *_: Any) -> None:
            duration_ms = (time.perf_counter() - self._start) * 1000
            self._collector.record_latency(self._name, duration_ms)

    def timer(self, name: str) -> "_Timer":
        return self._Timer(self, name)

    # ── Reporting ─────────────────────────────────────────────────────────────

    def get_latency_stats(self, operation: str) -> dict[str, float]:
        samples = list(self._latencies.get(operation, []))
        if not samples:
            return {}

        durations = sorted(s.duration_ms for s in samples)
        n = len(durations)
        return {
            "count": n,
            "mean_ms": sum(durations) / n,
            "min_ms": durations[0],
            "max_ms": durations[-1],
            "p50_ms": durations[n // 2],
            "p95_ms": durations[int(n * 0.95)],
            "p99_ms": durations[int(n * 0.99)],
        }

    def get_report(self) -> dict[str, Any]:
        uptime = time.time() - self._started_at if self._started_at else 0
        return {
            "uptime_seconds": round(uptime, 1),
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "latencies": {
                op: self.get_latency_stats(op)
                for op in self._latencies
            },
        }

    def _emit_final_report(self) -> None:
        report = self.get_report()
        log.info(
            "metrics_final_report",
            uptime=report["uptime_seconds"],
            **{f"count_{k}": v for k, v in report["counters"].items()},
        )
