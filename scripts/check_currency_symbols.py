#!/usr/bin/env python
"""Count dollar signs hard-coded in a money position.

An organisation reports in one currency (``Organization.reporting_currency``). A page that draws ``'$' + total``,
``$${cost}``, ``${{ amount }}`` or a literal ``$50K`` quotes dollars to an organisation that may not use them. Money
is drawn by the shared formatter: ``window.currencyManager.format(x)`` in JavaScript, ``|currency`` and
``currency_symbol`` in templates, ``currency_config`` in Python.

Counted in templates, shipped JS (not js/bundles or vendored code) and the Python under app/modules and
app/services (not the code generators, which write other people's source, and not test files):

* a quoted string ending in ``$`` joined to a value with ``+`` (``'$' + x``, ``'Est. cost: $' + x``)
* in Python, ``$`` immediately before an f-string field (``f"${total:,.0f}"``), a ``str.format`` field
  (``"${:,.0f}".format(t)``) or a ``%`` conversion (``"$%.2f" % t``), except on a logging call
* ``$`` immediately before a template-literal or Jinja interpolation (``$${x}``, ``${{ x }}``)
* a bare ``$`` standing alone in a tag (``>$<``)
* a literal amount: ``$50K``, ``$1.5M``, ``$0.002``, ``$100``
* a currency formatter pinned to ``'USD'`` in JS (``currency: 'USD'``)

A value kept in a stated currency is drawn by the shared formatter with that currency as an explicit argument
(``window.currencyManager.format(x, 'USD')``, ``|currency('USD')``); a line doing so is not counted, because the
figure names the currency it is kept in. A line carrying ``currency-symbol-ok: <reason>`` is excluded and thereby made reviewable (a regular-expression
replacement such as ``'$1 $2'`` is never counted: a single digit is a back-reference, not an amount). The
public pricing pages (app/templates/public, the landing page and the service that feeds them) quote the product's own
price in a stated currency, not the viewer's, and are excluded.

Proven-against: `'$' + total` and `<td>${{ row.cost }}</td>` planted in a temporary tree -- the count rises from 0
to 2 and falls back to 0 once they read through the formatter (tests/test_check_currency_symbols.py).

Usage:
    python scripts/check_currency_symbols.py            # list occurrences
    python scripts/check_currency_symbols.py --count    # count only
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("app/templates", "app/static/js", "app/modules", "app/services")
SUFFIXES = (".html", ".js", ".py")
MARKER = "currency-symbol-ok"
EXCLUDED_DIRS = ("app/templates/public", "app/modules/codegen", "app/modules/solutions_product")
EXCLUDED_FILES = ("app/templates/main/index.html", "app/services/public_pages.py")

PATTERNS = (
    re.compile(r"""['"`][^'"`]*\$['"`]\s*\+"""),
    re.compile(r"\$\$\{"),
    re.compile(r"\$\s*\{\{"),
    re.compile(r">\s*\$\s*<"),
    re.compile(r"\$(?:[0-9]{2,}|[0-9]+[.,][0-9]|[0-9][KMBkmb]\b|0\b)"),
    re.compile(r"""currency\s*:\s*['"]USD['"]"""),
    re.compile(r"""\bf['"].*\$\{"""),
    # str.format and %-style: "Cost ${:,.0f}".format(t), "Cost $%.2f" % t
    re.compile(r"""['"][^'"]*\$\{[^{}]*\}[^'"]*['"]\s*\.format\("""),
    re.compile(r"""['"][^'"]*\$%[-+ 0-9.,]*[sdif][^'"]*['"]\s*%"""),
    # '$'.concat(x) and "Cost $".concat(x) in JavaScript
    re.compile(r"""['"`][^'"`]*\$['"`]\s*\.concat\("""),
    # f"US$ {x}" and f"$ {x}": a sign, optional space, then a field
    re.compile(r"""\bf['"].*\$\s+\{"""),
)
# a spreadsheet absolute reference ($B$12, $B${row}) is a cell address, not money
CELL_REFERENCE = re.compile(r"\$[A-Z]{1,3}\$(?=\{|[0-9])")
# a figure drawn through the formatter with its currency named is removed from the line before it is read, so a
# hard-coded dollar sign elsewhere on the same line still counts; a logging call is not money drawn on a page
# (a call whose own argument carries a hard-coded sign, format('$' + x, 'USD'), is not removed)
STATED_CURRENCY = re.compile(
    r"""(?:format|format_currency|currency)\s*\((?![^)]*\$['"]\s*(?:\+|\.concat))[^)]*['"][A-Z]{3}['"]\s*\)"""
)
LOGGING = re.compile(r"^\s*(?:logger|logging|log|current_app\.logger|app\.logger)\.\w+\(")
COMMENT = re.compile(r"^\s*(#|//|\*|/\*|\{#)")


def occurrences():
    for scan_dir in SCAN_DIRS:
        for path in sorted((ROOT / scan_dir).rglob("*")):
            rel = path.relative_to(ROOT).as_posix()
            if path.suffix not in SUFFIXES or rel in EXCLUDED_FILES or any(rel.startswith(d + "/") for d in EXCLUDED_DIRS):
                continue
            if "tests" in path.parts or path.name.startswith("test_"):
                continue
            if "bundles" in path.parts or ".min." in path.name or "vendor" in path.parts:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if MARKER in line or COMMENT.match(line):
                    continue
                line = CELL_REFERENCE.sub("", STATED_CURRENCY.sub("", line))
                if path.suffix == ".py" and not re.search(r"""['"]""", line):
                    continue
                if path.suffix == ".py":
                    patterns = (PATTERNS[0], PATTERNS[4]) if LOGGING.match(line) else (PATTERNS[0], PATTERNS[4], *PATTERNS[6:9], PATTERNS[10])
                else:
                    patterns = (*PATTERNS[:6], PATTERNS[9])
                if any(p.search(line) for p in patterns):
                    yield rel, lineno, line.strip()


def main() -> int:
    found = list(occurrences())
    if "--count" in sys.argv:
        print(len(found))
        return 0
    for rel, lineno, line in found:
        print(f"{rel}:{lineno}: dollar sign in a money position")
        print(f"    {line[:160]}")
    if not found:
        print("no dollar sign is hard-coded in a money position")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
