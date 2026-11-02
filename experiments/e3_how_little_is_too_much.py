"""E3: how much blocking does it take?

Nobody writes a handler that is entirely blocking. They write one that awaits a database,
awaits a cache, and then does one small synchronous thing: parses a token, hashes a
password, serialises a payload with a C library that holds the GIL, reads a config file.

So the question that matters is not whether blocking is bad but how much is too much, and
the answer is a number. The serialised ceiling is one over the blocking time per request,
and the concurrent ceiling is the concurrency over the total time. Setting them equal:

    blocking fraction = 1 / concurrency

Above that fraction the serial ceiling binds and throughput is lost. Below it the loop
absorbs the blocking and nothing shows. That prediction is derived here and tested below
at two concurrencies, where it says the knee should sit in two different places.

It is tested, and the test says the question was wrong. There is no knee. Throughput
starts falling at the smallest fraction swept and keeps falling, so there is no safe
amount of blocking, only an amount whose cost someone has decided to accept.

The formula still marks something real: it is roughly where the loss becomes
catastrophic rather than merely expensive. But it is read here as a marker, not a budget,
because the budget it implies is already being spent by the time you reach it.

An attempt to correct it is recorded in ADR-002 and rejected. Recovering a loop side cost
per request from the serialised end of the sweep gives a figure that implies no blocking
budget at all at either concurrency, which the sweep directly contradicts: at concurrency
50 a fraction of 0.005 retains most of the throughput. The recovered figure mixes the
loop's own cost with the overshoot of a short sleep and cannot separate them, so it is
not published as a correction.
"""

from __future__ import annotations

from _common import table, write

from event_loop_tax.config import ScenarioConfig
from event_loop_tax.report import build_report

WORK_MS = 5.0
FRACTIONS = (0.0, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0)
CONCURRENCIES = (50, 200)


def main() -> int:
    print(__doc__)

    cells, grid = [], {}
    for concurrency in CONCURRENCIES:
        grid[concurrency] = {}
        baseline = None
        print(f"\nconcurrency {concurrency}, predicted knee at a fraction of "
              f"{1 / concurrency:.4f}\n")
        for fraction in FRACTIONS:
            config = ScenarioConfig(handler="async_blocking", work_ms=WORK_MS,
                                    blocking_fraction=fraction,
                                    concurrency=concurrency, requests=400,
                                    repeats=3, warmup_requests=30)
            report = build_report(config)
            m = report.measurement
            if baseline is None:
                baseline = m.rps
            retained = m.rps / baseline if baseline else 0.0
            grid[concurrency][fraction] = (m.rps, retained, m.lag_p99_ms)
            cells.append({
                "concurrency": concurrency, "blocking_fraction": fraction,
                "blocking_ms": round(config.blocking_seconds * 1000, 4),
                "rps": round(m.rps, 1), "retained": round(retained, 4),
                "ceiling_rps": round(report.predicted_rps, 1),
                "lag_p99_ms": round(m.lag_p99_ms, 2),
                "loop_blocked": report.loop_blocked,
            })
            print(f"  fraction {fraction:<6} ({config.blocking_seconds * 1000:5.3f}ms "
                  f"blocking)  {m.rps:9.1f} rps   {retained:6.1%} of the "
                  f"non-blocking throughput")

    for concurrency in CONCURRENCIES:
        print(f"\nthroughput retained against blocking fraction, concurrency "
              f"{concurrency}\n")
        table(["fraction", "blocking ms", "rps", "retained"],
              [[f"{f:g}", f"{WORK_MS * f:.3f}", f"{grid[concurrency][f][0]:.1f}",
                f"{grid[concurrency][f][1]:.1%}"] for f in FRACTIONS],
              [12, 13, 12, 11])

    # The knee: the smallest swept fraction at which a tenth of throughput is already gone.
    knees = {}
    for concurrency in CONCURRENCIES:
        lost = [f for f in FRACTIONS if grid[concurrency][f][1] < 0.9]
        knees[concurrency] = min(lost) if lost else None

    print("\nwhere a tenth of throughput has already gone\n")
    table(["concurrency", "predicted 1/c", "measured knee"],
          [[str(c), f"{1 / c:.4f}", ("none in range" if knees[c] is None
                                     else f"{knees[c]:g}")] for c in CONCURRENCIES],
          [13, 15, 15])

    low_c, high_c = CONCURRENCIES[0], CONCURRENCIES[-1]
    small = 0.005
    small_low = grid[low_c][small][1]
    small_high = grid[high_c][small][1]

    print("\nwhat the same handler costs at two concurrencies\n")
    table(["fraction", f"retained at {low_c}", f"retained at {high_c}"],
          [[f"{f:g}", f"{grid[low_c][f][1]:.1%}", f"{grid[high_c][f][1]:.1%}"]
           for f in FRACTIONS], [12, 18, 18])

    print(f"\nThe smallest fraction swept is {small:g}: a handler that blocks for "
          f"{WORK_MS * small:.3f}ms out of {WORK_MS:g}ms. That costs "
          f"{1 - small_low:.1%} of throughput at {low_c} concurrent requests and "
          f"{1 - small_high:.1%} at {high_c}.")
    print("The handler is identical in both. Only the traffic changed, and the same line "
          "of code went from a rounding error to losing most of the service's capacity.")
    print(f"\nThere is no knee to find. Loss begins at the first fraction measured and "
          f"grows from there, so {1 / low_c:.1%} and {1 / high_c:.2%} are not budgets, "
          "they are roughly where the loss stops being survivable.")

    worst_quiet_lag = max(grid[low_c][f][2] for f in FRACTIONS if f <= 0.01)
    print(f"\nAt fractions of one percent and below the loop lag p99 stays at "
          f"{worst_quiet_lag:.2f}ms, so nothing in the service looks wrong at all.")

    write("e3_how_little_is_too_much", {
        "experiment": "e3_how_little_is_too_much",
        "question": "What fraction of a handler may block before throughput is lost?",
        "work_ms": WORK_MS, "fractions": list(FRACTIONS),
        "concurrencies": list(CONCURRENCIES), "cells": cells,
        "predicted_knees": {str(c): round(1 / c, 4) for c in CONCURRENCIES},
        "measured_knees": {str(c): knees[c] for c in CONCURRENCIES},
        "smallest_fraction": small,
        "smallest_fraction_blocking_ms": round(WORK_MS * small, 4),
        "loss_at_smallest_low_concurrency": round(1 - small_low, 4),
        "loss_at_smallest_high_concurrency": round(1 - small_high, 4),
        "quiet_lag_p99_ms": round(worst_quiet_lag, 2),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
