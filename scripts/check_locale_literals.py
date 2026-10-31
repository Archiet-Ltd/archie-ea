#!/usr/bin/env python
"""Count the places a template turns a date, number or amount of money into text itself.

Every figure a person reads is written by the four filters in ``app/utils/locale_format.py`` (``fmt_date``,
``fmt_number``, ``fmt_percent``, ``fmt_money``), which follow the organisation's locale and currency. A template that
calls ``strftime``, formats with ``'%.2f' | format`` or ``{:,}``, rounds a figure with ``round(`` or writes a ``£``,
``€`` or ``$`` in front of an expression decides the format for every organisation at once, so a company in Berlin
reads London's.

Counted: ``strftime(`` and ``'%.2f' | format`` / ``{:,`` inside a template, ``round(`` inside ``{{ }}``, a ``£`` or
``€``, and a ``$`` directly before ``{{``. A line carrying ``locale-ok: <reason>`` is excluded and thereby reviewable.
The count can only fall (``verification_baseline.json``, ``locale_literals``).

Proven-against: adding ``{{ x.strftime('%d %b') }}`` to a template raises the count by one and the locale-literals gate
fails; removing it returns the count to the recorded baseline.

Usage:
    python scripts/check_locale_literals.py            # list occurrences
    python scripts/check_locale_literals.py --count    # count only
"""

from __future__ import annotations

import argparse
import glob
import re
import sys

ALLOW = "locale-ok:"
EXCLUDED_DIRS = ("/vendor/", "/node_modules/")
RULES = (
    ("strftime", re.compile(r"strftime\(")),
    ("percent-f format", re.compile(r"""['"]%[0-9.]*[fd]['"]\s*\|\s*format""")),
    ("thousands format", re.compile(r"\{:,")),
    ("round in expression", re.compile(r"\{\{[^}]*\bround\(")),
    ("currency symbol", re.compile("[£€]")),
    ("dollar before expression", re.compile(r"\$\s*\{\{")),
)


def occurrences() -> list[str]:
    found: list[str] = []
    for path in sorted(glob.glob("app/templates/**/*.html", recursive=True)):
        norm = path.replace(chr(92), "/")
        if any(part in norm for part in EXCLUDED_DIRS):
            continue
        with open(path, encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, 1):
                if ALLOW in line:
                    continue
                for name, pattern in RULES:
                    if pattern.search(line):
                        found.append(f"{norm}:{number}: {name}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--count", action="store_true", help="print the number only")
    args = parser.parse_args(argv)
    found = occurrences()
    if args.count:
        print(len(found))
    else:
        for item in found:
            print(item)
        print(f"{len(found)} places format a figure outside the locale filters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
