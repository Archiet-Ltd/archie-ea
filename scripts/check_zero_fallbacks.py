#!/usr/bin/env python
"""Count score, percentage and money figures that fall back to an invented zero.

CLAUDE.md: "Never invent data ... A `0` that means 'not computed' is
indistinguishable from a measured zero; use `None` -> an explained value."

`(x.score || 0)`, `(x.cost ?? 0)`, `{{ x.cost or 0 }}` and `x.score|default(0)`
all turn "nothing was measured" into a confident 0. The reader cannot tell it
from a measured zero. A figure that was not measured says so in words
("Not scored", "Not recorded"): `Platform.figure.*` in JS, and the `dash`
filter's `missing` argument in templates.

Python payloads are read too (`"total_savings": float(x) if x else 0`, under app/).
Only fallbacks whose operand names a score, percentage or money figure are
counted. A counter that really is zero (a count of rows) is not a fallback and
is not matched. A line carrying a ``zero-fallback-ok: <reason>`` marker (form
defaults, chart arithmetic that is guarded, ...) is excluded and thereby made
reviewable. Excluded: vendored code and js/bundles/*. No page is left out: a page that still falls back is
counted, and the baseline is the count on the tree as it stands.

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
SCAN_DIRS = ("app/static/js", "app/templates", "app")
MARKER = "zero-fallback-ok"

NAME = re.compile(
    r"(score|pct|percent|percentage|cost|spend|budget|price|revenue|saving|investment|amount|"
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
# A name ending in "total" is a tally of rows, except where it names money: `cost_total` is a sum.
MONEY_TOTAL = re.compile(r"(cost|spend|budget|price|revenue|saving|investment|amount)\w*_total['\"\])]*$", re.IGNORECASE)


def _is_counter(name: str) -> bool:
    return bool(COUNTER.search(name)) and not MONEY_TOTAL.search(name)


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
# `s.get(dim_key, 0)`: the key is a variable, so the line names the figure, as for `obj[dim.key] || 0`.
DICT_GET_VAR = re.compile(r"(" + VALUE + r")\.get\(\s*([A-Za-z_][\w.]*)\s*,\s*" + ZERO[:-len(r"(?![\d.\w])")] + r"\s*\)")
# `x.score|float` turns a missing value into 0.0.
FLOAT_CAST = re.compile(r"\|\s*float\b")
# Python payloads: `"total_savings": float(x) if x else 0` names the figure by its dict key, and
# `value = x if x else 0` by its operand. Only .py files under app/ are read with these.
PY_PAYLOAD = re.compile(
    r"""['"](\w+)['"]\s*:\s*[^\n]*?\bif\b[^\n]*?\belse\s+""" + ZERO
)
PY_ASSIGN = re.compile(r"^\s*(\w+)\s*=(?!=)\s*[^\n]*?\bif\b[^\n]*?\belse\s+" + ZERO, re.MULTILINE)
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
        return bool(_words(line) & FIGURE_WORDS) and not _is_counter(base)
    return bool(NAME.search(operand)) and not _is_counter(operand)


def _py_candidates(text: str):
    for pattern in (PY_PAYLOAD, PY_ASSIGN):
        for match in pattern.finditer(text):
            name = match.group(1)
            condition = re.split(r"\bif\b", match.group(0), maxsplit=1)[1]
            # `1.0 if a == b else 0.0` is a rule being applied, not a missing value standing in as 0.
            if re.search(r"==|!=", condition):
                continue
            if NAME.search(name) and not _is_counter(name):
                yield match.start(1), name


def _candidates(text: str):
    for match in OPERATOR.finditer(text):
        start, operand = _operand(text, match.start())
        yield start, operand
    for match in FLOAT_CAST.finditer(text):
        start, operand = _operand(text, match.start())
        yield start, operand
    for match in DICT_GET_VAR.finditer(text):
        yield match.start(1), "%s[%s]" % (match.group(1), match.group(2))
    for pattern in (TERNARY, INLINE_IF, DICT_GET):
        for match in pattern.finditer(text):
            yield match.start(1), match.group(1)


def occurrences():
    for scan_dir in SCAN_DIRS:
        for path in sorted((ROOT / scan_dir).rglob("*")):
            if path.suffix not in (".js", ".html", ".py"):
                continue
            if path.suffix == ".py" and scan_dir != "app":
                continue
            if path.suffix != ".py" and scan_dir == "app":
                continue
            if "bundles" in path.parts or ".min." in path.name or "vendor" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            seen = set()
            found = _py_candidates(text) if path.suffix == ".py" else _candidates(text)
            for start, operand in sorted(found):
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
