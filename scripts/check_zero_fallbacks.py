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
    r"coverage|oee|compliance|adoption|similarity|strategic_fit|completeness|quality)",
    re.IGNORECASE,
)
# A tally of rows ("scored_count", "total_scored", "not_ready", "rows_with_cost")
# is a counter, and a counter that really is zero is not a fallback.
COUNTER = re.compile(
    r"(count|counts|length|total_scored|ready|rows_with_\w+|violations|accepted|rejected|pending_review|_gaps|mappings|mappings_found|seq|passed|total)['\"\])]*$",
    re.IGNORECASE,
)
ZERO = r"0(?:\.0+)?(?![\d.\w])"
# Each form names the operand that is replaced by 0 when it is missing. A form
# may span lines: the whole file is read, not one line at a time.
OPERATOR = re.compile(
    r"(?:\|\|\s*" + ZERO + r"|\?\?\s*" + ZERO + r"|\bor\s+" + ZERO
    + r"|\|\s*default\(\s*" + ZERO[:-len(r"(?![\d.\w])")] + r"\s*[,)])"
)
VALUE = r"[\w.\[\]'\"()]+"
# `x ? x : 0`, `x if x else 0` and `s.get('score', 0)` carry the operand inside.
TERNARY = re.compile(r"\?\s*(" + VALUE + r")\s*:\s*" + ZERO)
INLINE_IF = re.compile(r"(" + VALUE + r")\s+if\s+[^\n%{}]+?\s+else\s+" + ZERO)
DICT_GET = re.compile(r"\.get\(\s*['\"]([^'\"]*)['\"]\s*,\s*" + ZERO[:-len(r"(?![\d.\w])")] + r"\s*\)")
COMPUTED_KEY = re.compile(r"\[[^\]'\"]*[A-Za-z_][^\]'\"]*\]$")


def _operand(text: str, pos: int):
    """The expression immediately left of the fallback operator, and where it starts."""
    head = text[:pos].rstrip()
    depth = 0
    i = len(head)
    while i > 0:
        c = head[i - 1]
        if c in ")]":
            depth += 1
        elif c in "([":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and not (c.isalnum() or c in "_.?'\""):
            break
        i -= 1
    return i, head[i:]


FIGURE_WORDS = {"score", "pct", "percent", "percentage", "cost", "spend", "budget", "price", "revenue", "saving",
                "amount", "health", "maturity", "rating", "roi", "npv", "tco", "margin", "readiness", "confidence",
                "probability", "coverage", "oee", "compliance", "adoption", "similarity", "completeness", "quality"}


def _words(text: str) -> set:
    """Identifier words of a line, split at underscores and camelCase, so `textContent` is not `tco`."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return {w.lower().removesuffix("s") for w in re.findall(r"[A-Za-z]+", spaced)}


def _is_figure(operand: str, line: str) -> bool:
    if COMPUTED_KEY.search(operand):
        # `obj[dim.key]` hides the name in the key, so the line names it; the object decides if it is a counter.
        base = operand[:operand.index("[")]
        return bool(_words(line) & FIGURE_WORDS) and not COUNTER.search(base)
    return bool(NAME.search(operand)) and not COUNTER.search(operand)


def _candidates(text: str):
    for match in OPERATOR.finditer(text):
        start, operand = _operand(text, match.start())
        yield start, operand
    for pattern in (TERNARY, INLINE_IF, DICT_GET):
        for match in pattern.finditer(text):
            yield match.start(1), match.group(1)


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
            lines = text.splitlines()
            seen = set()
            for start, operand in sorted(_candidates(text)):
                lineno = text.count("\n", 0, start) + 1
                line = lines[lineno - 1] if lineno <= len(lines) else ""
                if lineno in seen or MARKER in line or not _is_figure(operand, line):
                    continue
                seen.add(lineno)
                yield path.relative_to(ROOT), lineno, operand, line.strip()


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
