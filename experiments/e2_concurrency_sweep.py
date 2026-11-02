"""E2: what happens when you add load.

The usual response to a slow service is more concurrency: more workers, a bigger pool, a
higher connection limit. For three of the four shapes that works. For the fourth it
cannot, and the reason is arithmetic rather than tuning.

A coroutine that holds the loop executes its blocking portion one request at a time. The
ceiling is therefore one divided by that blocking time, and concurrency is not in the
expression. The prediction is a flat line, and a flat line is easy to falsify.
"""

from __future__ import annotations

from _common import table, write

from event_loop_tax.config import ScenarioConfig
from event_loop_tax.report import build_report

WORK_MS = 5.0
CONCURRENCIES = (1, 5, 10, 25, 50, 100, 200)
SHAPES = ("async_awaited", "async_blocking")


def main() -> int:
    print(__doc__)
    print(f"{WORK_MS:g}ms of work, concurrency from {CONCURRENCIES[0]} to "
          f"{CONCURRENCIES[-1]}.\n")

    cells, grid = [], {}
    for shape in SHAPES:
        grid[shape] = {}
        for concurrency in CONCURRENCIES:
            config = ScenarioConfig(handler=shape, work_ms=WORK_MS,
                                    concurrency=concurrency, requests=400,
                                    repeats=3, warmup_requests=30)
            report = build_report(config)
            m = report.measurement
            # The invariant: for a serialised handler this product should be one at every
            # concurrency. The harness never tells the measurement what it should be.
            invariant = m.rps * config.blocking_seconds
            grid[shape][concurrency] = (m.rps, invariant, report.predicted_rps)
            cells.append({
                "handler": shape, "concurrency": concurrency,
                "rps": round(m.rps, 1), "ceiling_rps": round(report.predicted_rps, 1),
                "rps_times_blocking_seconds": round(invariant, 4),
                "lag_p99_ms": round(m.lag_p99_ms, 2),
            })
            print(f"  {shape:15s} c={concurrency:4d}  {m.rps:9.1f} rps   "
                  f"rps x blocking_s = {invariant:.4f}")

    print("\nthroughput against concurrency\n")
    table(["concurrency"] + list(SHAPES),
          [[str(c)] + [f"{grid[s][c][0]:.1f}" for s in SHAPES] for c in CONCURRENCIES],
          [13, 16, 16])

    blocking = [grid["async_blocking"][c][1] for c in CONCURRENCIES]
    awaited_rps = [grid["async_awaited"][c][0] for c in CONCURRENCIES]
    blocking_rps = [grid["async_blocking"][c][0] for c in CONCURRENCIES]

    low, high = min(blocking), max(blocking)
    drift = high / low

    print("\nthe invariant: throughput times blocking seconds, which should be one\n")
    table(["concurrency", "rps x blocking_s"],
          [[str(c), f"{grid['async_blocking'][c][1]:.4f}"] for c in CONCURRENCIES],
          [13, 18])

    print(f"\nAcross a {CONCURRENCIES[-1] // CONCURRENCIES[0]}-fold range of concurrency "
          f"the product varies by a factor of {drift:.3f}, staying between {low:.4f} and "
          f"{high:.4f}.")
    print("The sweep knows nothing about that identity, so the agreement is evidence that "
          "the ceiling is real rather than a coincidence of one setting.")

    awaited_growth = awaited_rps[-1] / awaited_rps[0]
    blocking_growth = blocking_rps[-1] / blocking_rps[0]
    print(f"\nOver the same range the correct coroutine's throughput grows "
          f"{awaited_growth:.0f}-fold. The blocking one grows {blocking_growth:.2f}-fold, "
          "which is to say it does not. Every request added to a blocked loop waits "
          "behind the ones already there.")

    write("e2_concurrency_sweep", {
        "experiment": "e2_concurrency_sweep",
        "question": "Does adding concurrency help a handler that holds the loop?",
        "work_ms": WORK_MS, "concurrencies": list(CONCURRENCIES), "cells": cells,
        "invariant_min": round(low, 4), "invariant_max": round(high, 4),
        "invariant_drift": round(drift, 3),
        "awaited_growth": round(awaited_growth, 1),
        "blocking_growth": round(blocking_growth, 2),
        "concurrency_range": CONCURRENCIES[-1] // CONCURRENCIES[0],
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
