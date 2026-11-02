"""Fail the build when a document quotes a figure the code no longer produces.

Two directions, both necessary. Forward: every metric must appear in at least one checked
document using one of its anchor phrases, so a figure that drifts breaks the build rather
than the reader's trust. Reverse: every decimal in the prose must correspond to some
metric, so a number nothing re-measures cannot quietly take up residence.

Matching is on anchor phrases rather than bare digits. A checker searching for "0.1240"
passes happily in a document that has gone stale around it, and a checker searching for
"10" matches a version string, a heading and half the list items on the page.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METRICS_PATH = ROOT / "docs" / "metrics.json"

# Fenced blocks, inline code and HTML attributes are excluded from the reverse check. An
# example invocation is allowed to contain an invented row count, and flagging it teaches
# the reader to ignore the section.
FENCE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`]*`")
HTML_ATTR = re.compile(r"<[^>]+>")
LINK_TARGET = re.compile(r"\]\([^)]*\)")
# The trailing lookahead forbids another digit or a dot, not any word character. An
# earlier version forbade word characters too, which meant a figure written with its unit
# attached was invisible to the reverse check: "2.47s" was skipped entirely, and so would
# every "0.058s" and "20.6x" in the README. A deliberate break test caught it, which is
# what the break test is for. Forbidding a following digit or dot still excludes version
# strings such as 3.13.16, where the first candidate is followed by a dot and the second
# is preceded by one.
DECIMAL = re.compile(r"(?<![\w.])[+-]?\d+\.\d+(?![\d.])")

# Emphasis markers are removed before matching. A figure in prose is almost always bolded,
# and an anchor reading "worst cell loses {}" then fails against the perfectly correct
# "worst cell loses **-0.1063**". This is the same class of problem as a wrapped line: the
# prose is right and the checker is looking at the wrong string.
EMPHASIS = re.compile(r"\*\*|\*|(?<!\w)_(?!\w)")


def strip_uncheckable(text: str) -> str:
    for pattern in (FENCE, INLINE_CODE, HTML_ATTR, LINK_TARGET):
        text = pattern.sub(" ", text)
    return EMPHASIS.sub("", text)


def normalise(text: str) -> str:
    """Collapse whitespace and case so an anchor matches correct prose.

    Whitespace first: an anchor phrase that straddles a newline in rendered markdown
    matches nothing, while the prose it guards is entirely correct. That rule came from a
    real failure.

    Case second, from another one. An anchor reading "recall of 1.0000" failed against a
    sentence beginning "Recall of 1.0000", which is ordinary English. An anchor mechanism
    that forces an author to avoid starting a sentence with a guarded phrase will be
    worked around rather than obeyed.
    """
    return re.sub(r"\s+", " ", text).lower()


def load_documents(names: list[str]) -> dict[str, str]:
    documents = {}
    for name in names:
        path = ROOT / name
        if not path.exists():
            raise SystemExit(
                f"checked document missing: {name}\n"
                "A document in CHECKED_DOCUMENTS was moved or deleted. Remove it from that "
                "tuple in tools/collect_metrics.py, re-run that script, and check again. "
                "Failing here is deliberate: silently skipping a missing document would "
                "leave whichever figures it guarded unguarded."
            )
        documents[name] = normalise(strip_uncheckable(path.read_text()))
    return documents


def forward_check(payload: dict, documents: dict[str, str]) -> list[str]:
    """Every metric must appear somewhere, through one of its anchors."""
    problems = []
    anchors = payload["anchors"]
    display = payload["display"]

    for key in payload["metrics"]:
        if key not in anchors:
            problems.append(
                f"GAP   {key}: no anchor phrase defined, nothing guards this figure"
            )
            continue
        phrases = [normalise(a.format(display[key])) for a in anchors[key]]
        if not any(p in doc for doc in documents.values() for p in phrases):
            problems.append(
                f"STALE {key}: expected one of {phrases} in a checked document, "
                "found none"
            )
    return problems


def reverse_check(payload: dict, documents: dict[str, str]) -> list[str]:
    """Every decimal in the prose must correspond to a metric."""
    known = set()
    for key, shown in payload["display"].items():
        known.add(shown)
        known.add(shown.lstrip("+"))
        if key in payload["anchors"]:
            known.add(f"{payload['metrics'][key]}")

    problems = []
    for name, text in documents.items():
        for found in DECIMAL.findall(text):
            if found not in known and found.lstrip("+") not in known:
                problems.append(
                    f"LOOSE {name}: {found} matches no metric, nothing re-measures it"
                )
    return problems


def main() -> int:
    if not METRICS_PATH.exists():
        print("docs/metrics.json missing, run tools/collect_metrics.py first", file=sys.stderr)
        return 2

    payload = json.loads(METRICS_PATH.read_text())
    documents = load_documents(payload["checked_documents"])

    problems = forward_check(payload, documents) + reverse_check(payload, documents)

    if problems:
        print("number check failed:\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            f"\n{len(problems)} problem(s). Re-run tools/collect_metrics.py, then update the "
            "prose to match, or add an anchor for a new figure.",
            file=sys.stderr,
        )
        return 1

    print(
        f"number check passed: {len(payload['metrics'])} metrics guarded across "
        f"{len(documents)} documents"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
