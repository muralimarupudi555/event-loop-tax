"""Scenario configuration, validated on construction.

A load measurement with a silently wrong setting produces numbers that look plausible and
mean something else, which is worse than a crash. Everything that would invalidate a
result is refused here rather than discovered in the output.
"""

from __future__ import annotations

from dataclasses import dataclass

HANDLERS = ("async_awaited", "async_blocking", "sync_threadpool", "async_offloaded")

# Percentiles are the point of this repository, and a p99 estimated from fewer than this
# many samples is the maximum with extra steps.
MIN_REQUESTS = 200
MIN_REPEATS = 3


class ConfigError(ValueError):
    """Raised when a scenario would produce a measurement that cannot be trusted."""


@dataclass(frozen=True)
class ScenarioConfig:
    """One load measurement: a handler shape, how much work it does, and how hard to push."""

    handler: str = "async_awaited"
    work_ms: float = 5.0
    blocking_fraction: float = 1.0
    concurrency: int = 50
    requests: int = 400
    repeats: int = 5
    warmup_requests: int = 100
    threadpool_size: int = 40
    lag_probe_interval_ms: float = 10.0
    seed: int = 20260219

    def __post_init__(self) -> None:
        if self.handler not in HANDLERS:
            raise ConfigError(f"handler must be one of {HANDLERS}, got {self.handler!r}")
        if self.work_ms <= 0:
            raise ConfigError(
                f"work_ms={self.work_ms} must be positive. A handler that does no work "
                "measures the harness, not the handler."
            )
        if not 0.0 <= self.blocking_fraction <= 1.0:
            raise ConfigError(
                f"blocking_fraction={self.blocking_fraction} must be between 0 and 1"
            )
        if self.concurrency < 1:
            raise ConfigError(f"concurrency={self.concurrency} must be at least 1")
        if self.requests < MIN_REQUESTS:
            raise ConfigError(
                f"requests={self.requests} is below {MIN_REQUESTS}. The published figures "
                "are tail latencies, and a p99 over fewer samples than this is just the "
                "slowest request wearing a percentile's name."
            )
        if self.repeats < MIN_REPEATS:
            raise ConfigError(
                f"repeats={self.repeats} is below {MIN_REPEATS}. A median over fewer runs "
                "is one run with extra steps, and load measurements on a shared machine "
                "are noisy enough to invert a comparison."
            )
        if self.warmup_requests < 1:
            raise ConfigError(
                "at least one warmup request is required: the first call through any of "
                "these paths pays for imports, thread creation and a cold allocator, and "
                "publishing it as a tail latency would describe a request no steady state "
                "client ever makes."
            )
        if self.threadpool_size < 1:
            raise ConfigError(f"threadpool_size={self.threadpool_size} must be at least 1")
        if self.lag_probe_interval_ms <= 0:
            raise ConfigError("lag_probe_interval_ms must be positive")

    @property
    def work_seconds(self) -> float:
        return self.work_ms / 1000.0

    @property
    def blocking_seconds(self) -> float:
        return self.work_seconds * self.blocking_fraction

    @property
    def awaited_seconds(self) -> float:
        return self.work_seconds * (1.0 - self.blocking_fraction)

    def as_dict(self) -> dict:
        return {
            "handler": self.handler, "work_ms": self.work_ms,
            "blocking_fraction": self.blocking_fraction,
            "concurrency": self.concurrency, "requests": self.requests,
            "repeats": self.repeats, "warmup_requests": self.warmup_requests,
            "threadpool_size": self.threadpool_size,
            "lag_probe_interval_ms": self.lag_probe_interval_ms, "seed": self.seed,
        }
