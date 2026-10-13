#!/usr/bin/env python
"""Catch a composer link that carries a parameter the composer never reads.

`/archimate/composer` reads exactly six query parameters — four server-side
in `composer_page` (`archimate_routes.py`): `solution_id`, `viewpoint`,
`layer`, `element`; and two client-side in `composer.js`'s page-load logic:
`viewpoint_id`, `prefill`. Any other parameter name on a literal
`/archimate/composer?...` string is silently ignored by the receiver, which
is exactly the bug class fixed across four separate rounds on the night of
2026-09-19 (dashboard cards, sub-diagram drill-down, AI-chat viewpoint
links, and finally the `layer`-only "+ Add" buttons and the `element_id=`/
`process=`/`elements=`/`solution=` param-name typos this script exists to
catch the next instance of).

`element` moved from the bad list to the known-good list on 2026-09-21,
when the composer gained the ability to read it (select and centre that
element once its viewpoint data has loaded) -- see composer_page()'s and
composer.js's `_selectInitialElement`'s own docstrings/comments. Before
that date, every `?element=...` composer link in the tree was a bug of
exactly the class this gate exists to catch; the four sites fixed then
(architecture/elements.html, traceability_chain.html x2) are the ones that
now legitimately use it.

What is flagged
----------------
Any literal string (in a template, JS file or Python file) matching
`/archimate/composer?...` whose query string contains a parameter name
outside the known-good set. Only statically analyzable literal strings are
scanned — an f-string/template-literal whose *query string itself* is
built from a variable (e.g. `f"/archimate/composer?{qs}"`) is not
analyzable by a regex and is out of scope for this gate; that class needs a
manual audit, same as it always has.

Usage
-----
    python scripts/check_composer_url_params.py                  # scan the whole tree
    python scripts/check_composer_url_params.py --count          # print violation count only
    python scripts/check_composer_url_params.py FILE [FILE ...]  # scan specific files
"""

from __future__ import annotations

import argparse
import glob
import re
import sys

KNOWN_GOOD_PARAMS = {"solution_id", "viewpoint", "layer", "viewpoint_id", "prefill", "element"}

# The literal STANDARD_VIEWPOINTS keys from
# app/services/archimate_viewpoint_service.py. This script is a standalone
# static scanner (no other scripts/check_*.py imports app.* — importing the
# app package here would drag in Flask/DB config for a text scan), so the
# key set is hardcoded and must be kept in sync by hand if a viewpoint is
# added or renamed there.
KNOWN_VIEWPOINT_KEYS = {
    "basic", "layered", "stakeholder", "actor_cooperation", "business_process",
    "application_usage", "application_cooperation", "technology",
    "technology_usage", "implementation_deployment", "information_structure",
    "service_realization", "motivation", "strategy", "capability", "migration",
}

# Matches a literal composer URL with a query string built from a literal
# (possibly with a single `${...}`/`{...}`/`' + var + '`-style interpolated
# VALUE, but the query string's structure — the param names themselves —
# must be static text for this regex to find them). We capture everything
# up to the closing quote/backtick/template-literal boundary. Also matches
# the Jinja `{{ url_for('archimate.composer_page') }}?param=value` pattern
# used throughout traceability_chain.html / fact_sheet.html /
# archimate_views/traceability.html, where the route path itself is never
# spelled out literally.
COMPOSER_URL = re.compile(
    r"(?:/archimate/composer|\{\{\s*url_for\(\s*['\"]archimate\.composer_page['\"]\s*\)\s*\}\})"
    r"\?(?P<qs>[^\"'`\s]+)"
)

# A parameter name and its value, separated by `=`, in the query string.
# Value capture stops at the next `&` (or end of string).
PARAM = re.compile(r"(?:^|&)([A-Za-z_][A-Za-z0-9_]*)=([^&]*)")

# A viewpoint value that looks like a numeric id, or a dynamic
# interpolation, rather than a literal STANDARD_VIEWPOINTS key string. This
# is exactly the shape of `?viewpoint={diagram.id}`,
# `?viewpoint=' + existingId`, `?viewpoint=42` and an f-string
# `?viewpoint={expr}` — a numeric-id value being passed where a named
# viewpoint key is expected.
DYNAMIC_VALUE_MARKERS = ("{", "}", "${", "' +", "+ '", '" +', '+ "')


def _viewpoint_value_is_suspect(value: str) -> bool:
    """True when a `viewpoint=` value is a numeric literal or an
    interpolation, rather than a known STANDARD_VIEWPOINTS key."""
    if value in KNOWN_VIEWPOINT_KEYS:
        return False
    if value == "":
        # The literal string closed right after `viewpoint=` with no
        # value — e.g. `'/archimate/composer?viewpoint=' + existingId`,
        # where the real value is a JS/Python concatenation the regex
        # cannot see past the closing quote. A legitimate literal always
        # has a non-empty value here.
        return True
    if value.isdigit():
        return True
    if any(marker in value for marker in DYNAMIC_VALUE_MARKERS):
        return True
    # Anything else that isn't a known key and isn't obviously dynamic is
    # still worth flagging as an unknown viewpoint name — but only if it
    # looks like a plain identifier (letters/digits/underscore), so we
    # don't double-flag interpolation syntax already caught above.
    return bool(re.fullmatch(r"[A-Za-z0-9_]+", value)) and value not in KNOWN_VIEWPOINT_KEYS


def default_paths() -> list[str]:
    return sorted(
        glob.glob("app/**/*.html", recursive=True)
        + glob.glob("app/**/*.js", recursive=True)
        + glob.glob("app/**/*.py", recursive=True)
    )


def scan_file(path: str) -> list[tuple[int, str, str]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(lines, start=1):
        if "composer-url-ok" in line:
            continue
        for match in COMPOSER_URL.finditer(line):
            qs = match.group("qs")
            # HTML/Jinja-escaped separators: `&amp;amp;` (double-escaped)
            # must be normalized before `&amp;` (single-escaped), since the
            # former contains the latter as a substring.
            qs = qs.replace("&amp;amp;", "&").replace("&amp;", "&")
            for pname, pvalue in PARAM.findall(qs):
                if pname not in KNOWN_GOOD_PARAMS:
                    findings.append((lineno, match.group(0), pname))
                elif pname == "viewpoint" and _viewpoint_value_is_suspect(pvalue):
                    findings.append((lineno, match.group(0), f"viewpoint={pvalue}"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="files to scan (default: whole app/ tree)")
    parser.add_argument("--count", action="store_true", help="print only the total count")
    args = parser.parse_args(argv)

    paths = args.paths or default_paths()
    paths = [p for p in paths if p.endswith((".html", ".jinja", ".jinja2", ".js", ".py"))]

    total = 0
    report: list[str] = []
    for path in paths:
        findings = scan_file(path)
        total += len(findings)
        for lineno, text, pname in findings:
            if pname.startswith("viewpoint="):
                report.append(
                    f"{path}:{lineno}: {text}  ->  {pname!r} looks like a numeric id or "
                    f"interpolated value where a literal STANDARD_VIEWPOINTS key is expected "
                    f"(known keys: {', '.join(sorted(KNOWN_VIEWPOINT_KEYS))})"
                )
            else:
                report.append(
                    f"{path}:{lineno}: {text}  ->  '{pname}' is not read by the composer "
                    f"(known-good: {', '.join(sorted(KNOWN_GOOD_PARAMS))})"
                )

    if args.count:
        print(total)
        return 0

    if report:
        print("\n".join(report))
        print(f"\n{total} composer link(s) carry a parameter the composer does not read.")
        print("Fix the param name, route through create_diagram() for a real "
              "?viewpoint_id=, or mark a deliberate exception with 'composer-url-ok: <reason>'.")
    else:
        print(f"No unread composer URL parameters found in {len(paths)} file(s).")

    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
