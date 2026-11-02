"""Shared plumbing for the experiments: paths, JSON output, and a table printer."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "experiments"

sys.path.insert(0, str(ROOT / "src"))


def machine() -> dict:
    """Record the machine. Every timing in this repository belongs to one."""
    try:
        cores = int(subprocess.run(["nproc"], capture_output=True, text=True,
                                   check=True).stdout.strip())
    except Exception:  # noqa: BLE001
        cores = 0
    return {"python": platform.python_version(), "platform": platform.platform(),
            "logical_cores": cores}


def write(name: str, payload: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps({"machine": machine(), **payload}, indent=2) + "\n")
    print(f"\nwrote {path}")
    return path


def table(headers: list[str], rows: list[list], widths: list[int] | None = None) -> None:
    widths = widths or [max(len(str(h)), 12) for h in headers]
    print("  ".join(str(h).ljust(w) for h, w in zip(headers, widths, strict=False)))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(row, widths, strict=False)))
