"""Re-derive the ceilings and check the recorded run against them.

The timings in docs/experiments belong to one machine and are not re-measured on every
verify, because rerunning them changes them. That could easily mean nothing is checked.
It does not, because the relationships between those numbers are not machine dependent.

Three checks, all re-derived from theory.py rather than read back out of the JSON:

1. No recorded throughput exceeds the ceiling its shape allows. A number above the
   ceiling is physically impossible, so it means the model is wrong or the file was
   edited.
2. The serialised invariant holds: for a fully blocking handler, throughput times
   blocking seconds sits near one at every concurrency in the sweep.
3. The live experiment ran under the apparatus ceiling, so its figures describe handlers
   rather than the load generator.

These hold on any machine, which is what makes them worth running on every verify.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

EXPERIMENT_DIR = ROOT / "docs" / "experiments"

# The invariant is a product that should be one. It is not exactly one because the loop
# and the harness cost something per request, which lowers throughput and so lowers the
# product. It can never legitimately exceed one.
INVARIANT_FLOOR = 0.80
INVARIANT_CEILING = 1.00


def load(name: str) -> dict:
    path = EXPERIMENT_DIR / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"{path.name} not found; run `make bench` first")
    return json.loads(path.read_text())


def main() -> int:
    from event_loop_tax.config import ScenarioConfig
    from event_loop_tax.theory import predicted_rps

    problems: list[str] = []
    checked = 0

    e1 = load("e1_four_shapes")
    for cell in e1["cells"]:
        config = ScenarioConfig(handler=cell["handler"], work_ms=e1["work_ms"],
                                concurrency=e1["concurrency"], requests=e1["requests"],
                                repeats=e1["repeats"])
        ceiling = predicted_rps(config)
        checked += 1
        if cell["rps"] > ceiling * 1.05:
            problems.append(
                f"e1 {cell['handler']}: {cell['rps']} rps exceeds the ceiling "
                f"{ceiling:.1f} rps that its shape allows"
            )

    e2 = load("e2_concurrency_sweep")
    for cell in e2["cells"]:
        if cell["handler"] != "async_blocking":
            continue
        config = ScenarioConfig(handler="async_blocking", work_ms=e2["work_ms"],
                                concurrency=cell["concurrency"])
        product = cell["rps"] * config.blocking_seconds
        checked += 1
        if not INVARIANT_FLOOR <= product <= INVARIANT_CEILING:
            problems.append(
                f"e2 concurrency {cell['concurrency']}: throughput times blocking "
                f"seconds is {product:.4f}, outside [{INVARIANT_FLOOR}, "
                f"{INVARIANT_CEILING}]"
            )

    e4 = load("e4_three_clocks")
    ceiling_rps = e4["apparatus_ceiling_rps"]
    for cell in e4["cells"]:
        checked += 1
        if cell["rps"] > ceiling_rps:
            problems.append(
                f"e4 {cell['shape']}: {cell['rps']} rps is at or above the apparatus "
                f"ceiling {ceiling_rps} rps, so it measured the harness"
            )

    if problems:
        print("the recorded run is not physically consistent:\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print("\nEither the model in theory.py is wrong or docs/experiments was edited "
              "by something other than `make bench`.", file=sys.stderr)
        return 1

    print(f"invariant check passed: {checked} recorded figures re-derived against "
          "theory.py and found consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
