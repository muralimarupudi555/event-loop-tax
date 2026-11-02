"""Render the committed architecture SVG.

Hand written rather than produced by a diagramming library, for the reasons in ADR-004: a
committed SVG with intrinsic dimensions and an explicit light background renders the same
on GitHub in either theme, which a Mermaid fence does not reliably do.
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "architecture.svg"

W, H = 960, 500
INK = "#1b1f23"
MUTED = "#57606a"
BAD = "#b42318"
GOOD = "#116329"
LINE = "#8c959f"


def box(x, y, w, h, title, subtitle="", stroke=MUTED, fill="#ffffff", bold=False):
    weight = "600" if bold else "400"
    parts = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="1.5"/>',
        f'<text x="{x + w / 2}" y="{y + (20 if subtitle else h / 2 + 4)}" '
        f'text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="13" '
        f'font-weight="{weight}" fill="{INK}">{title}</text>',
    ]
    if subtitle:
        for index, line in enumerate(subtitle.split("|")):
            parts.append(
                f'<text x="{x + w / 2}" y="{y + 38 + index * 14}" text-anchor="middle" '
                f'font-family="Helvetica,Arial,sans-serif" font-size="11" '
                f'fill="{MUTED}">{line}</text>'
            )
    return "".join(parts)


def arrow(x1, y1, x2, y2, dashed=False):
    dash = ' stroke-dasharray="4 3"' if dashed else ""
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{LINE}" '
        f'stroke-width="1.5" marker-end="url(#a)"{dash}/>'
    )


def label(x, y, text, fill=MUTED, size=11, anchor="middle", weight="400"):
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" '
        f'font-family="Helvetica,Arial,sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="{fill}">{text}</text>'
    )


def render() -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="Architecture of event-loop-tax">',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        f'markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="{LINE}"/>'
        "</marker></defs>",
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        label(W / 2, 26, "The same work, four ways of waiting for it",
              INK, 14, weight="600"),
    ]

    parts.append(box(24, 150, 178, 76, "one unit of work",
                     "5ms, identical in|all four shapes", bold=True))

    shapes = [
        ("async_awaited", "yields", GOOD, "#f2fbf4"),
        ("async_blocking", "holds the loop", BAD, "#fff5f4"),
        ("sync_threadpool", "worker thread", GOOD, "#f2fbf4"),
        ("async_offloaded", "worker thread", GOOD, "#f2fbf4"),
    ]
    for index, (name, note, stroke, fill) in enumerate(shapes):
        y = 62 + index * 62
        parts.append(box(262, y, 206, 48, name, note, stroke=stroke, fill=fill))
        parts.append(arrow(202, 185, 262, y + 24))

    parts.append(box(536, 72, 180, 60, "the event loop",
                     "one thread, serving|every request", bold=True))
    for index in range(len(shapes)):
        parts.append(arrow(468, 86 + index * 62, 536, 102))

    parts.append(box(536, 186, 180, 56, "throughput",
                     "sees it: 43.6x apart", stroke=GOOD, fill="#f2fbf4", bold=True))
    parts.append(box(536, 262, 180, 56, "loop lag",
                     "sees it: 3144ms vs 1ms", stroke=GOOD, fill="#f2fbf4", bold=True))
    parts.append(box(536, 338, 180, 56, "handler timer",
                     "blind: 53ms of 573ms", stroke=BAD, fill="#fff5f4", bold=True))
    parts.append(arrow(626, 132, 626, 186))

    parts.append(box(778, 186, 158, 132, "three readings|of one failure",
                     "two name it,|one reports|business as usual",
                     stroke=MUTED, bold=True))
    for y in (214, 290, 366):
        parts.append(arrow(716, y, 778, 252))

    parts.append(
        box(130, 420, 700, 56,
            "run alone, all four cost the same; only concurrency separates them",
            "which is why the broken one passes review, passes a local test, "
            "and fails in production", bold=True)
    )
    parts.append(arrow(857, 318, 700, 420))
    parts.append(arrow(365, 310, 420, 420))

    parts.append("</svg>")
    return "".join(parts)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render() + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
