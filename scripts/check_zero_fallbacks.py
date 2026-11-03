#!/usr/bin/env python
"""Count score, percentage and money figures that fall back to an invented zero.

CLAUDE.md: "Never invent data ... A `0` that means 'not computed' is
indistinguishable from a measured zero; use `None` -> an explained value."

`(x.score || 0)`, `(x.cost ?? 0)`, `{{ x.cost or 0 }}` and `x.score|default(0)`
all turn "nothing was measured" into a confident 0. The reader cannot tell it
from a measured zero. A figure that was not measured says so in words
("Not scored", "Not recorded"): `Platform.figure.*` in JS, and the `dash`
filter's `missing` argument in templates.

Only fallbacks whose operand names a score, percentage or money figure are
counted. A counter that really is zero (a count of rows) is not a fallback and
is not matched. A line carrying a ``zero-fallback-ok: <reason>`` marker (form
defaults, chart arithmetic that is guarded, ...) is excluded and thereby made
reviewable. Excluded: vendored code and js/bundles/*.

Proven-against: `(x.quality_score || 0)` planted in a template (and
`{{ x.cost or 0 }}` beside it) -- the count rises from 0 to 2, and falls back
to 0 once they read through Platform.figure / the dash filter
(tests/test_check_zero_fallbacks.py).

Usage:
    python scripts/check_zero_fallbacks.py            # list occurrences
    python scripts/check_zero_fallbacks.py --count    # count only
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("app/static/js", "app/templates")
MARKER = "zero-fallback-ok"

# The investment pages and the components only they include are corrected in
# their own change; they are left out here so the two changes do not collide.
INVESTMENT_PAGES = {
    "app/templates/strategic/investment_matrix.html",
    "app/templates/enterprise/investment_matrix.html",
    "app/templates/components/roi_calculator_modal.html",
    "app/templates/components/scenario_comparison_modal.html",
    "app/static/js/roi_calculator.js",
    "app/static/js/capability_map/investment_bubble.js",
}

NAME = re.compile(
    r"(score|pct|percent|percentage|cost|spend|budget|price|revenue|saving|amount|"
    r"health|maturity|rating|roi|npv|tco|margin|readiness|confidence|probability|"
    r"coverage|oee|compliance|adoption|similarity|strategic_fit|completeness)",
    re.IGNORECASE,
)
# A tally of rows ("scored_count", "total_scored", "not_ready", "rows_with_cost")
# is a counter, and a counter that really is zero is not a fallback.
COUNTER = re.compile(
    r"(count|counts|length|total_scored|ready|rows_with_\w+|violations|_gaps|mappings|mappings_found|seq|passed|total)['\"\])]*$",
    re.IGNORECASE,
)
FALLBACK = re.compile(
    r"(\|\|\s*0(?![\d.\w])|\?\?\s*0(?![\d.\w])|\bor\s+0(?![\d.\w])"
    r"|\|\s*default\(\s*0\s*[,)])"
)


def _operand(line: str, pos: int) -> str:
    """The expression immediately left of the fallback operator."""
    text = line[:pos].rstrip()
    depth = 0
    i = len(text)
    while i > 0:
        c = text[i - 1]
        if c in ")]":
            depth += 1
        elif c in "([":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and not (c.isalnum() or c in "_.?'\""):
            break
        i -= 1
    return text[i:]


def occurrences():
    for scan_dir in SCAN_DIRS:
        for path in sorted((ROOT / scan_dir).rglob("*")):
            if path.suffix not in (".js", ".html"):
                continue
            if "bundles" in path.parts or ".min." in path.name or "vendor" in path.parts:
                continue
            if path.relative_to(ROOT).as_posix() in INVESTMENT_PAGES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                if MARKER in line:
                    continue
                for match in FALLBACK.finditer(line):
                    operand = _operand(line, match.start())
                    if NAME.search(operand) and not COUNTER.search(operand):
                        yield path.relative_to(ROOT), lineno, operand, line.strip()
                        break


def main() -> int:
    found = list(occurrences())
    if "--count" in sys.argv:
        print(len(found))
        return 0
    for rel, lineno, operand, line in found:
        print(f"{rel}:{lineno}: [{operand}] falls back to 0")
        print(f"    {line[:160]}")
    if not found:
        print("no score, percentage or money figure falls back to 0")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
