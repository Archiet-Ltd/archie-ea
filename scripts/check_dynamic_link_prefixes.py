#!/usr/bin/env python
"""ARCH-043: dead links built by string-concatenating an id onto a literal prefix.

scripts/check_broken_surfaces.py's dead-link/dead-fetch classes deliberately
SKIP any href/fetch target built by concatenation (`'/x/' + id`) — its own
docstring says so, because guessing at the interpolated segment costs more
false positives than it's worth. That skip is correct for the general case,
but it left an entire class of real, reproducible 404s invisible to CI: a
route migration (`/dashboard/application/<id>` -> `/applications/<id>`,
`/vendors/view/<id>` -> `/applications/vendors/<id>`) left concatenated
Alpine `:href` bindings pointing at the old, dead prefix while every plain
`href="..."` reference had already been caught and fixed.

This gate does not try to guess the interpolated segment. It only checks the
LITERAL PREFIX before the first `+` — the part that is not a guess, because it
is exactly what the app is about to send the browser to. If no live route
starts with that prefix, the concatenated link is dead for every possible id.

Usage:
    python scripts/check_dynamic_link_prefixes.py            # human-readable
    python scripts/check_dynamic_link_prefixes.py --count     # bare integer
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"
STATIC_JS = ROOT / "app" / "static" / "js"

_SKIP_PREFIX = ("http://", "https://", "//", "mailto:", "tel:", "#", "javascript:", "data:")

# `:href="'/prefix/' + expr"`  or  `href="'/prefix/' + expr"` (Alpine x-bind) or
# a bare JS string-concat assigned to a link-ish variable, e.g.
#   const url = '/dashboard/application/' + id;
# Group 1 is the literal prefix (must start with '/' and end just before the '+').
_CONCAT_RE = re.compile(
    r"""['"](/[^'"]*?)['"]\s*\+\s*[A-Za-z_$][\w.$]*"""
)


def _route_prefixes() -> list[str]:
    """Static (non-dynamic) prefixes of every real route, longest first."""
    os.environ.setdefault("FLASK_CONFIG", "testing")
    sys.path.insert(0, str(ROOT))
    try:
        from app import create_app

        app = create_app("testing")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "check_dynamic_link_prefixes cannot run: the Flask app failed to "
            "import or boot (%s: %s). This gate needs a bootable app, like "
            "boot-health and check_broken_surfaces - it is NOT a static gate."
            % (type(exc).__name__, str(exc)[:200])
        )
    prefixes: set[str] = set()
    for rule in app.url_map.iter_rules():
        # Static prefix = everything before the first '<...>' converter.
        # Byte-identical to hygiene_text.HTML_TAG_RE, not imported from it:
        # that constant names an HTML tag; this is a Flask route converter
        # (<int:id>) inside a url_map rule string, an unrelated grammar that
        # happens to share the shape -- coupling the two would repoint an
        # HTML-focused change at this file's own, unrelated parsing.
        static = re.split(r"<[^>]+>", rule.rule, maxsplit=1)[0]
        if static:
            prefixes.add(static)
    return sorted(prefixes, key=len, reverse=True)


def _prefix_is_live(literal: str, route_prefixes: list[str]) -> bool:
    # A literal like "/applications/vendors/" is live if some route's static
    # prefix equals it (the literal IS the whole static part before the id)
    # or starts with it (the literal is a truncated prefix of a longer static
    # segment, e.g. literal "/applications/" for rule "/applications/<int:id>").
    for p in route_prefixes:
        if p == literal or p.startswith(literal) or literal.startswith(p):
            return True
    return False


def scan() -> list[str]:
    route_prefixes = _route_prefixes()
    findings: list[str] = []

    def rel(p):
        return p.relative_to(ROOT).as_posix()

    def line_of(text, idx):
        return text[: idx].count("\n") + 1

    files = sorted(TEMPLATES.rglob("*.html")) + sorted(STATIC_JS.rglob("*.js"))
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in _CONCAT_RE.finditer(text):
            literal = m.group(1)
            if not literal.startswith("/") or literal.startswith(_SKIP_PREFIX):
                continue
            if "{{" in literal or "{%" in literal:
                continue
            if not _prefix_is_live(literal, route_prefixes):
                findings.append(
                    "%s:%d: dead-link-dynamic: literal prefix \"%s\" (before "
                    "string concatenation) matches no live route - every id "
                    "substituted in produces a 404"
                    % (rel(path), line_of(text, m.start()), literal)
                )
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", action="store_true", help="print only the finding count")
    args = ap.parse_args()

    findings = scan()
    if args.count:
        print(len(findings))
        return 0

    if not findings:
        print("check_dynamic_link_prefixes: 0 findings")
        return 0

    for f in findings:
        print(f)
    print("\ncheck_dynamic_link_prefixes: %d findings" % len(findings))
    return 1


if __name__ == "__main__":
    sys.exit(main())
