#!/usr/bin/env python
"""Count the banned placeholder phrases in shipped templates and scripts.

An empty region says what is missing and offers the next action; it never
reads "Nothing here yet", "No data" or "N/A". A bare placeholder gives a person
nothing to do, and a dash that stands for "not measured" looks the same as a
measured value. The wording rule is the help-and-overlays standard; the one
implementation of an empty region is the ``empty_state`` macro in
``app/templates/components/empty_state.html``.

Excluded: vendored code and ``js/bundles/*`` (a concatenation of ``js/core/*``,
so counting both counts the same source twice). A line carrying a
``placeholder-ok: <reason>`` marker is excluded and thereby made reviewable.

Proven-against: a template containing the bare word "N/A" -- the count rises by
one and the content-placeholders gate fails; removing the file returns it to the
recorded baseline.

Usage:
    python scripts/check_content_placeholders.py            # list occurrences
    python scripts/check_content_placeholders.py --count    # count only
"""

from __future__ import annotations

import argparse
import glob
import re
import sys

BACKSLASH = chr(92)
PHRASE = re.compile(r"Nothing here yet|No data|N/A")
ALLOW = "placeholder-ok:"
EXCLUDED_DIRS = ("/vendor/", "/js/bundles/", "/node_modules/")
PATTERNS = (
    "app/static/js/**/*.js",
    "app/templates/**/*.html",
    "app/templates/**/*.js",
)


def occurrences() -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern in PATTERNS:
        for path in glob.glob(pattern, recursive=True):
            norm = path.replace(BACKSLASH, "/")
            if norm in seen or any(part in norm for part in EXCLUDED_DIRS):
                continue
            seen.add(norm)
            with open(path, encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, 1):
                    if ALLOW in line:
                        continue
                    for match in PHRASE.finditer(line):
                        found.append(f"{norm}:{number}: {match.group(0)}")
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", action="store_true", help="print the count only")
    args = parser.parse_args(argv)
    found = occurrences()
    if args.count:
        print(len(found))
        return 0
    for line in found:
        print(line)
    print(f"{len(found)} placeholder phrase(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
