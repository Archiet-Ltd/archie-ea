#!/usr/bin/env python
"""Catch malformed or duplicated versioned asset URLs. Gated at ZERO.

    python scripts/check_asset_urls.py            # report problems
    python scripts/check_asset_urls.py --count     # count only

Why
---
`app/_bootstrap/context_processors.py` overrides Jinja's `url_for` globally so
that EVERY `url_for('static', ...)` call already comes back with a `?v=<build
id>` cache-buster attached (see ARCH-062 / build_info.get_build_id()). Templates
that then hand-append their own `?v=2` on top of that produce a URL with two `?`
characters — e.g. `/static/js/ui/modal.js?v=1786981206?v=1786981206`. Everything
after the second `?` becomes part of the first query value rather than a second
parameter, which silently defeats cache-busting for exactly that file (ARCH-060).
The fix is never to hand-concatenate `?v=` in a template — `url_for` (and the
`asset_url` filter in app/_bootstrap/assets.py) already does it.

Separately, ARCH-061 found the same stylesheet included twice on one page at two
different version stamps — non-deterministic because whichever tag loads second
wins on equal-specificity rules. This script also flags a `<link rel="stylesheet"
href=.../css/X>` or `<script src=.../js/X>` appearing more than once in the same
rendered template's static includes, keyed on the static filename (ignoring any
`?v=` suffix) — a template that legitimately loads the same layout partial twice
would false-positive here, which is why base layouts should include a partial
once and let child templates extend it, not re-declare shared assets locally.

Both checks are static (regex over template source), not a rendered-DOM check —
they cannot see an asset pulled in only via an `{% include %}` inherited from a
different file, so a duplicate split across a base layout and a child template
is not caught here. That gap is acceptable for a ratchet-style regression gate:
the two duplicates found in production (accessibility.css, and the three doubled
`?v=` scripts) were both single-file, and this script keeps them from coming back.

Proven-against: app/templates/layouts/admin_base.html before the asset URL cleanup,
where hand-appended `?v=` parameters produced doubled `?` static URLs and the same
stylesheet loaded twice in one template.
"""

from __future__ import annotations

import argparse
import glob
import re
import sys

TEMPLATE_GLOB = "app/templates/**/*.html"

# A src/href attribute value, whether produced by a Jinja expression
# ({{ url_for(...) }}...) or a plain string.
ASSET_TAG = re.compile(
    r"""<(script|link)\b[^>]*\b(?:src|href)\s*=\s*"([^"]*(?:static|/js/|/css/)[^"]*)"[^>]*>""",
    re.I,
)

# Everything inside the attribute value up to and including the *second* '?'.
DOUBLE_QUERY = re.compile(r"\?[^?\"]*\?")

STATIC_FILE = re.compile(r"""(?:filename=['"]([^'"]+)['"]|/static/([^"'?]+))""")


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def check_file(path: str) -> list[str]:
    problems: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return problems

    seen: dict[str, int] = {}
    for m in ASSET_TAG.finditer(text):
        tag_kind, attr_value = m.group(1), m.group(2)
        line = _line_of(text, m.start())

        if DOUBLE_QUERY.search(attr_value):
            problems.append(
                f"{path}:{line}: doubled '?' in {tag_kind} URL (ARCH-060) -> {attr_value}"
            )

        fm = STATIC_FILE.search(attr_value)
        if not fm:
            continue
        static_name = (fm.group(1) or fm.group(2) or "").split("?")[0]
        if not static_name or not static_name.startswith(("css/", "js/")):
            continue
        if static_name in seen:
            problems.append(
                f"{path}:{line}: '{static_name}' already included at line "
                f"{seen[static_name]} (ARCH-061 — duplicate stylesheet/script "
                f"in one template)"
            )
        else:
            seen[static_name] = line

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--count", action="store_true")
    args = parser.parse_args(argv)

    problems: list[str] = []
    for path in sorted(glob.glob(TEMPLATE_GLOB, recursive=True)):
        problems.extend(check_file(path))

    if args.count:
        print(len(problems))
        return 0

    if problems:
        print("\n".join(problems))
        print(f"\n{len(problems)} malformed/duplicated asset URL(s).")
        return 1

    print("No doubled '?' or duplicate stylesheet/script includes found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
