"""Command line entry point.

Exit code 4 is the one worth having. It does not say the service is slow, which a latency
graph already says. It says the event loop is being held by something that will not yield,
which is a different problem with a different fix, and no standard metric reports it.

Exit code 5 is the one that protects the rest. If a measurement comes back faster than the
model says is possible, the model or the harness is wrong, and the tool refuses to print a
number rather than publishing one nobody should trust.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from event_loop_tax.config import HANDLERS, ConfigError, ScenarioConfig
from event_loop_tax.report import DEFAULT_LAG_BUDGET_MULTIPLE, Report, build_report

EXIT_HEALTHY = 0
EXIT_DEGRADED = 1
EXIT_BAD_CONFIG = 2
EXIT_INTERNAL_ERROR = 3
EXIT_LOOP_BLOCKED = 4
EXIT_IMPOSSIBLE = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="event-loop-tax",
        description="Measure what a handler shape costs the event loop.",
    )
    parser.add_argument("--handler", choices=HANDLERS, default="async_blocking")
    parser.add_argument("--work-ms", type=float, default=5.0)
    parser.add_argument("--blocking-fraction", type=float, default=1.0)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--requests", type=int, default=400)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--threadpool-size", type=int, default=40)
    parser.add_argument("--lag-budget-multiple", type=float,
                        default=DEFAULT_LAG_BUDGET_MULTIPLE,
                        help="loop lag above this many probe intervals exits 4")
    parser.add_argument("--calibrate", action="store_true",
                        help="measure the harness cost first and use it if it holds up")
    parser.add_argument("--json", action="store_true")
    return parser


def classify(report: Report) -> int:
    if report.exceeds_ceiling:
        return EXIT_IMPOSSIBLE
    if report.loop_blocked:
        return EXIT_LOOP_BLOCKED
    if not report.agrees_with_theory:
        return EXIT_DEGRADED
    return EXIT_HEALTHY


def render(report: Report) -> str:
    config, m = report.config, report.measurement
    floor = ("none" if report.serial_floor_rps == float("inf")
             else f"{report.serial_floor_rps:.1f}")
    lines = [
        f"{config.handler} at concurrency {config.concurrency}, "
        f"{config.work_ms:g}ms work, {config.blocking_fraction:g} of it blocking",
        "",
        f"{'reading':<28}{'value':>14}",
        "-" * 42,
        f"{'throughput':<28}{m.rps:>11.1f} rps",
        f"{'ceiling for this shape':<28}{report.predicted_rps:>11.1f} rps",
        f"{'share of ceiling reached':<28}{report.achieved_share:>14.2f}",
        f"{'fully serialised ceiling':<28}{floor:>11} rps",
        "-" * 42,
        f"{'p50 latency':<28}{m.p50_ms:>12.2f} ms",
        f"{'p99 latency':<28}{m.p99_ms:>12.2f} ms",
        f"{'loop lag, median':<28}{m.lag_median_ms:>12.2f} ms",
        f"{'loop lag, p99':<28}{m.lag_p99_ms:>12.2f} ms",
        f"{'loop lag budget':<28}{report.lag_budget_ms:>12.2f} ms",
        "-" * 42,
        f"{'run to run spread':<28}{m.rps_spread:>13.2f}x",
    ]
    if report.loop_blocked:
        lines.append("")
        lines.append(
            f"the event loop was {m.lag_p99_ms:.0f}ms late at p99, against a budget of "
            f"{report.lag_budget_ms:.0f}ms: something in this handler is not yielding"
        )
        if report.serial_floor_ratio > 0.8:
            lines.append(
                f"throughput is {report.serial_floor_ratio:.2f} of the fully serialised "
                "ceiling, so the blocking portion is running one request at a time and "
                "adding concurrency will not help"
            )
    if report.exceeds_ceiling:
        lines.append("")
        lines.append(
            "measured faster than the model allows; the model or the harness is wrong "
            "and this number should not be used"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = ScenarioConfig(
            handler=args.handler, work_ms=args.work_ms,
            blocking_fraction=args.blocking_fraction, concurrency=args.concurrency,
            requests=args.requests, repeats=args.repeats,
            threadpool_size=args.threadpool_size,
        )
    except ConfigError as exc:
        print(f"invalid scenario: {exc}", file=sys.stderr)
        return EXIT_BAD_CONFIG

    try:
        report = build_report(config, args.lag_budget_multiple, calibrate=args.calibrate)
    except Exception as exc:  # noqa: BLE001
        print(f"measurement failed to complete: {exc}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    print(json.dumps(report.as_dict(), indent=2) if args.json else render(report))
    return classify(report)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
