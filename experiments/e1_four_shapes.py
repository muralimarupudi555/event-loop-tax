"""E1: four handler shapes, the same work, one concurrency.

Run alone, all four cost the same. Under concurrency they do not, and the spread is the
whole argument. The question this answers is not "is blocking bad", which everyone says
yes to, but how bad, against what ceiling, and which of the shapes people are actually
warned about turns out to be the safe one.
"""

from __future__ import annotations

from _common import table, write

from event_loop_tax.config import HANDLERS, ScenarioConfig
from event_loop_tax.report import build_report

WORK_MS = 5.0
CONCURRENCY = 50
REQUESTS = 600
REPEATS = 5


def main() -> int:
    print(__doc__)
    print(f"{WORK_MS:g}ms of work per request, {CONCURRENCY} concurrent, "
          f"{REQUESTS} requests, {REPEATS} repeats.\n")

    cells = []
    for handler in HANDLERS:
        config = ScenarioConfig(handler=handler, work_ms=WORK_MS,
                                concurrency=CONCURRENCY, requests=REQUESTS,
                                repeats=REPEATS, warmup_requests=50)
        report = build_report(config)
        m = report.measurement
        cells.append({
            "handler": handler,
            "rps": round(m.rps, 1),
            "ceiling_rps": round(report.predicted_rps, 1),
            "share_of_ceiling": round(report.achieved_share, 3),
            "p50_ms": round(m.p50_ms, 2),
            "p99_ms": round(m.p99_ms, 2),
            "lag_median_ms": round(m.lag_median_ms, 2),
            "lag_p99_ms": round(m.lag_p99_ms, 2),
            "loop_blocked": report.loop_blocked,
            "serial_floor_ratio": round(report.serial_floor_ratio, 3),
            "rps_spread": round(m.rps_spread, 3),
        })
        print(f"  {handler:17s} {m.rps:8.1f} rps   p99 {m.p99_ms:7.2f}ms   "
              f"loop lag p99 {m.lag_p99_ms:9.2f}ms   blocked={report.loop_blocked}")

    print("\nthroughput against the ceiling for each shape\n")
    table(["handler", "rps", "ceiling", "share", "spread"],
          [[c["handler"], f"{c['rps']:.1f}", f"{c['ceiling_rps']:.1f}",
            f"{c['share_of_ceiling']:.3f}", f"x{c['rps_spread']:.2f}"] for c in cells],
          [18, 10, 10, 9, 9])

    print("\nwhat the two latency readings say\n")
    table(["handler", "p50 ms", "p99 ms", "loop lag p99 ms"],
          [[c["handler"], f"{c['p50_ms']:.2f}", f"{c['p99_ms']:.2f}",
            f"{c['lag_p99_ms']:.2f}"] for c in cells], [18, 10, 10, 17])

    by_name = {c["handler"]: c for c in cells}
    blocking, threadpool = by_name["async_blocking"], by_name["sync_threadpool"]
    awaited = by_name["async_awaited"]

    throughput_gap = awaited["rps"] / blocking["rps"]
    sync_gap = threadpool["rps"] / blocking["rps"]

    print(f"\nThe correct coroutine does {throughput_gap:.1f} times the throughput of the "
          "blocking one.")
    print(f"A plain def handler, the shape people are warned away from, does "
          f"{sync_gap:.1f} times the throughput of a coroutine that blocks.")

    worse_p99 = [c["handler"] for c in cells
                 if c["handler"] != "async_blocking" and c["p99_ms"] > blocking["p99_ms"]]
    if worse_p99:
        print(f"\nAnd the reading that should have caught it did not: {len(worse_p99)} of "
              f"the {len(cells) - 1} healthy shapes have a WORSE p99 latency than the "
              f"blocking one ({blocking['p99_ms']:.2f}ms), because a closed loop client "
              "stops sending while the loop is held and never records the wait.")
        print("Loop lag is the reading that separates them: "
              f"{blocking['lag_p99_ms']:.0f}ms against "
              f"{awaited['lag_p99_ms']:.2f}ms for the correct coroutine.")

    write("e1_four_shapes", {
        "experiment": "e1_four_shapes",
        "question": "What do the four handler shapes cost under concurrency?",
        "work_ms": WORK_MS, "concurrency": CONCURRENCY, "requests": REQUESTS,
        "repeats": REPEATS, "cells": cells,
        "throughput_gap_awaited_over_blocking": round(throughput_gap, 1),
        "throughput_gap_sync_over_blocking": round(sync_gap, 1),
        "blocking_p99_ms": blocking["p99_ms"],
        "blocking_lag_p99_ms": blocking["lag_p99_ms"],
        "awaited_lag_p99_ms": awaited["lag_p99_ms"],
        "healthy_shapes_with_worse_p99": worse_p99,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
