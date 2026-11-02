"""Drive load at a handler and record what came back.

This is a closed loop harness: a fixed number of simulated clients, each sending its next
request only once its previous one returned. That is how almost every load test works and
it has a known bias, which is published rather than hidden. When the server stalls, a
closed loop client stops sending, so the requests that would have arrived during the stall
are never counted as slow. The measured tail is therefore optimistic, and the real tail
under a stall is worse than anything printed here. ADR-003 has the detail.

The bias runs in the direction that weakens this repository's own argument, which is the
only reason it is tolerable: the blocking handler's latencies are flattering to the
blocking handler.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from event_loop_tax.config import ScenarioConfig
from event_loop_tax.lag import LagProfile, LoopLagProbe
from event_loop_tax.workload import build


@dataclass(frozen=True)
class RunResult:
    """One pass of the load: how fast, how late, and how badly the loop was starved."""

    wall_seconds: float
    requests: int
    rps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    lag: LagProfile

    def as_dict(self) -> dict:
        return {
            "wall_seconds": round(self.wall_seconds, 5),
            "requests": self.requests,
            "rps": round(self.rps, 2),
            "p50_ms": round(self.p50_ms, 4),
            "p95_ms": round(self.p95_ms, 4),
            "p99_ms": round(self.p99_ms, 4),
            "max_ms": round(self.max_ms, 4),
            "lag": self.lag.as_dict(),
        }


def _percentile(ordered: list[float], q: float) -> float:
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


async def drive_once(config: ScenarioConfig, executor: ThreadPoolExecutor) -> RunResult:
    """Run one pass: warm up, then time `requests` calls at `concurrency`."""
    handler = build(config.handler)

    for _ in range(config.warmup_requests):
        await handler(config, executor)

    probe = LoopLagProbe(config.lag_probe_interval_ms)
    gate = asyncio.Semaphore(config.concurrency)
    latencies: list[float] = []

    async def one() -> None:
        # The wait for a slot is the client not having sent yet, so it is outside the
        # timed region. What is timed is the request's life once in flight.
        async with gate:
            started = time.perf_counter()
            await handler(config, executor)
            latencies.append((time.perf_counter() - started) * 1000.0)

    probe.start()
    # Yield once so the probe is actually scheduled before the flood starts. Without this
    # the probe's first sample includes its own startup and reads as phantom lag.
    await asyncio.sleep(0)
    started = time.perf_counter()
    await asyncio.gather(*[one() for _ in range(config.requests)])
    wall = time.perf_counter() - started
    lag = await probe.stop()

    ordered = sorted(latencies)
    return RunResult(
        wall_seconds=wall,
        requests=config.requests,
        rps=config.requests / wall if wall > 0 else 0.0,
        p50_ms=_percentile(ordered, 0.50),
        p95_ms=_percentile(ordered, 0.95),
        p99_ms=_percentile(ordered, 0.99),
        max_ms=ordered[-1] if ordered else 0.0,
        lag=lag,
    )


@dataclass(frozen=True)
class Measurement:
    """`repeats` passes, summarised. Medians, with the spread published beside them."""

    config: ScenarioConfig
    runs: list[RunResult]

    @property
    def rps(self) -> float:
        return statistics.median(r.rps for r in self.runs)

    @property
    def p99_ms(self) -> float:
        return statistics.median(r.p99_ms for r in self.runs)

    @property
    def p50_ms(self) -> float:
        return statistics.median(r.p50_ms for r in self.runs)

    @property
    def lag_p99_ms(self) -> float:
        return statistics.median(r.lag.p99_ms for r in self.runs)

    @property
    def lag_median_ms(self) -> float:
        return statistics.median(r.lag.median_ms for r in self.runs)

    @property
    def rps_spread(self) -> float:
        """Largest run over smallest, the resolution of this instrument.

        Published because two handler shapes whose throughputs differ by less than this
        have not been shown to differ at all.
        """
        values = [r.rps for r in self.runs]
        low = min(values)
        return max(values) / low if low > 0 else 0.0

    def as_dict(self) -> dict:
        return {
            "config": self.config.as_dict(),
            "rps": round(self.rps, 2),
            "p50_ms": round(self.p50_ms, 4),
            "p99_ms": round(self.p99_ms, 4),
            "lag_median_ms": round(self.lag_median_ms, 4),
            "lag_p99_ms": round(self.lag_p99_ms, 4),
            "rps_spread": round(self.rps_spread, 4),
            "runs": [r.as_dict() for r in self.runs],
        }


async def measure_async(config: ScenarioConfig) -> Measurement:
    """Run the scenario `repeats` times on a fresh executor each time."""
    runs = []
    for _ in range(config.repeats):
        # A fresh pool per pass, so one pass cannot hand warm threads to the next and
        # make a later repeat look faster than the first for a reason unrelated to the
        # handler being measured.
        with ThreadPoolExecutor(max_workers=config.threadpool_size) as executor:
            runs.append(await drive_once(config, executor))
    return Measurement(config=config, runs=runs)


def measure(config: ScenarioConfig) -> Measurement:
    return asyncio.run(measure_async(config))


# Work sizes used to calibrate the harness, in milliseconds. Two points far enough apart
# that a fixed per request cost shows up clearly in the difference.
CALIBRATION_WORK_MS = (4.0, 32.0)


async def calibrate_overhead_async(concurrency: int, requests: int = 300,
                                   repeats: int = 3) -> tuple[float, float]:
    """Measure what the harness costs per request, independently of any comparison.

    Per request wall time is modelled as the handler's work plus a fixed cost: parking a
    coroutine, waking it, acquiring the gate, reading the clock. Running a shape that
    yields properly at two different work sizes gives two equations in that one unknown,
    and the two answers should agree.

    Returns the overhead in seconds and the ratio between the two estimates. A ratio far
    from one means the cost is not fixed and the correction should not be trusted, which
    is reported rather than averaged away.
    """
    estimates = []
    for work_ms in CALIBRATION_WORK_MS:
        config = ScenarioConfig(handler="async_awaited", work_ms=work_ms,
                                concurrency=concurrency, requests=requests,
                                repeats=repeats, warmup_requests=20)
        result = await measure_async(config)
        per_request = concurrency / result.rps if result.rps > 0 else 0.0
        estimates.append(max(0.0, per_request - config.work_seconds))

    low, high = min(estimates), max(estimates)
    consistency = high / low if low > 0 else float("inf")
    return sum(estimates) / len(estimates), consistency


def calibrate_overhead(concurrency: int, requests: int = 300,
                       repeats: int = 3) -> tuple[float, float]:
    return asyncio.run(calibrate_overhead_async(concurrency, requests, repeats))
