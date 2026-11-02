"""Event loop lag: the diagnostic that names this bug.

Tail latency tells you something is wrong. Loop lag tells you what. A task that asks to
sleep ten milliseconds and wakes up a second late was not slow; it was not scheduled,
because something else refused to yield.

This is the metric almost nobody exports, and it is the one that separates "the database
is slow" from "a handler is blocking the loop". The two produce similar p99 graphs and
have nothing else in common.
"""

from __future__ import annotations

import asyncio
import contextlib
import statistics
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class LagProfile:
    """How late the event loop was, over one measurement."""

    samples: int
    median_ms: float
    p99_ms: float
    max_ms: float

    def as_dict(self) -> dict:
        return {
            "samples": self.samples,
            "median_ms": round(self.median_ms, 4),
            "p99_ms": round(self.p99_ms, 4),
            "max_ms": round(self.max_ms, 4),
        }


class LoopLagProbe:
    """Repeatedly sleep a known interval and record how much longer it actually took.

    The probe is a coroutine on the same loop as the work. That is the whole design: it
    can only observe lag it personally suffered, which is exactly the quantity a request
    on that loop would suffer too.
    """

    def __init__(self, interval_ms: float = 10.0) -> None:
        self._interval = interval_ms / 1000.0
        self._lags: list[float] = []
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            started = time.perf_counter()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            overshoot = (time.perf_counter() - started) - self._interval
            # A negative overshoot means the stop event fired early, not that the loop ran
            # ahead of schedule. Those samples are discarded rather than recorded as
            # impossibly good lag.
            if overshoot >= 0:
                self._lags.append(overshoot * 1000.0)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> LagProfile:
        self._stop.set()
        if self._task is not None:
            await self._task
        return self.profile()

    def profile(self) -> LagProfile:
        if not self._lags:
            return LagProfile(samples=0, median_ms=0.0, p99_ms=0.0, max_ms=0.0)
        ordered = sorted(self._lags)
        index = min(len(ordered) - 1, int(round(0.99 * (len(ordered) - 1))))
        return LagProfile(
            samples=len(ordered),
            median_ms=statistics.median(ordered),
            p99_ms=ordered[index],
            max_ms=ordered[-1],
        )
