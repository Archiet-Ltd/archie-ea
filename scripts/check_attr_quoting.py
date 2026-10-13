#!/usr/bin/env python
"""Find `| tojson` inside a DOUBLE-quoted HTML attribute, where it truncates the attribute.

Why this kills a component rather than merely looking untidy
------------------------------------------------------------
Flask/Jinja's ``tojson`` filter emits *HTML-safe* JSON. It escapes ``<``, ``>``,
``&`` and ``'`` (the apostrophe becomes ``\\u0027``) so the payload is safe to
drop inside a ``<script>`` block or a single-quoted attribute -- but it
deliberately leaves the double quote ``"`` as a literal double quote, because
JSON without ``"`` is not JSON.

So a double-quoted attribute is the one place the output cannot go::

    <div x-data="completenessChecker({{ report | tojson }})">

renders as::

    x-data="completenessChecker({"score": 3, ...})"

and the JSON's own first ``"`` closes the attribute early. The browser keeps the
truncated fragment ``completenessChecker({`` as the attribute value and turns
the remainder into garbage attributes. Alpine's CSP-safe parser
(``app/static/js/csp/csp-evaluator.js``) is then handed an unterminated
expression and throws::

    Uncaught SyntaxError: expected } got ""

which aborts ``x-data`` initialisation, so the **entire component is dead** --
every ``x-show``, ``x-text`` and ``@click`` inside it silently does nothing.
Observed live in a browser on ``/solutions/1/completeness`` and ``/modules/``.

Single quotes are safe *precisely because* ``tojson`` escapes ``'``::

    <div x-data='completenessChecker({{ report | tojson }})'>

The fix is therefore normally a delimiter swap. Where the expression already
contains its own single quotes (a Jinja-interpolated id, an inline
``'a' if x else 'b'``), swapping naively produces broken HTML instead -- pass
the value through a ``data-*`` attribute or a JSON ``<script>`` block and read
it in the component.

What is flagged
---------------
Every ``tojson`` appearing inside a double-quoted attribute value, without
exception. It is tempting to reason "this particular value is an integer / a
short slug, so its JSON can never contain a quote" -- that is not a safe
assumption. The data shape is owned by a model and a route far from the
template, and a field that is a number today is a string tomorrow; the failure
mode when it changes is a silently dead component, not a test failure. So the
gate is on the *pattern*, not on today's data.

Jinja comment regions (``{# ... #}``) are masked out first, since markup inside
them never reaches the browser. ``{{ ... }}`` / ``{% ... %}`` regions are masked
before attribute parsing (but still inspected for ``tojson``), so a quote
character inside a Jinja filter argument cannot desynchronise the parse.

Escape hatch: append ``attr-quoting-ok: <reason>`` on the flagged line or the
line above it.

Usage:
    python scripts/check_attr_quoting.py            # list findings
    python scripts/check_attr_quoting.py --count    # trailing line = count
    python scripts/check_attr_quoting.py --root DIR # scan another tree
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import hygiene_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".worktrees"}

# Any HTML attribute whose value is delimited by double quotes. Alpine
# attributes are the ones that break loudly, but a truncated `title=` or
# `data-*` is equally wrong, so the gate covers them all.
ATTR_RE = re.compile(
    r"""(?P<name>[A-Za-z_@:][A-Za-z0-9_@:.\-\[\]]*)\s*=\s*"(?P<val>[^"]*)\"""",
    re.S,
)

# Shared with check_public_repo_hygiene.py and the other checkers that mask
# the same two shapes -- see hygiene_text's own module docstring.
JINJA_COMMENT_RE = hygiene_text.JINJA_COMMENT_RE
JINJA_RE = hygiene_text.JINJA_EXPR_OR_STMT_RE

TOJSON_RE = re.compile(r"\|\s*tojson\b|\btojson\s*\(")

ALLOW_RE = re.compile(r"attr-quoting-ok\s*:")


def _mask(text: str, start: int, end: int) -> str:
    """Blank out [start:end) but keep newlines, so line numbers stay true."""
    seg = text[start:end]
    return text[:start] + re.sub(r"[^\n]", " ", seg) + text[end:]


def mask(text: str, pattern: re.Pattern[str]) -> str:
    for m in reversed(list(pattern.finditer(text))):
        text = _mask(text, m.start(), m.end())
    return text


def templates(base: str):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if name.endswith(".html"):
                yield os.path.join(dirpath, name)


def scan_file(path: str, rel: str) -> list[tuple[str, int, str]]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return []
    if "tojson" not in raw:
        return []

    lines = raw.splitlines()
    # Jinja comments never render: drop them entirely.
    live = mask(raw, JINJA_COMMENT_RE)
    # Mask Jinja expressions for attribute *parsing* only; spans are preserved,
    # so the raw text under the matched span is still available for inspection.
    parseable = mask(live, JINJA_RE)

    findings: list[tuple[str, int, str]] = []
    for m in ATTR_RE.finditer(parseable):
        span = live[m.start("val") : m.end("val")]
        for hit in TOJSON_RE.finditer(span):
            lineno = raw.count("\n", 0, m.start("val") + hit.start()) + 1
            context = lines[lineno - 1] if lineno - 1 < len(lines) else ""
            prev = lines[lineno - 2] if lineno >= 2 else ""
            if ALLOW_RE.search(context) or ALLOW_RE.search(prev):
                continue
            findings.append((rel, lineno, m.group("name")))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", action="store_true", help="print only the count")
    ap.add_argument(
        "--root",
        default=ROOT,
        help="tree to scan (its app/ subdirectory); defaults to the repository. "
        "Exists so the checker can be exercised against fixtures rather than "
        "against today's count.",
    )
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    findings: list[tuple[str, int, str]] = []
    for path in templates(os.path.join(root, "app")):
        rel = os.path.relpath(path, root).replace("\\", "/")
        findings.extend(scan_file(path, rel))

    findings.sort()
    if not args.count:
        for rel, line, attr in findings:
            print(f"{rel}:{line}: `tojson` inside double-quoted attribute {attr}=\"...\"")
        if findings:
            print()
            print(
                "tojson leaves `\"` literal, so the JSON closes the attribute early\n"
                "and Alpine's CSP parser throws `expected } got \"\"` -- the whole\n"
                "component dies silently. Switch the attribute delimiter to single\n"
                "quotes (tojson escapes `'` to \\u0027, so that is safe). If the\n"
                "expression already contains single quotes, pass the value via a\n"
                "data-* attribute or a JSON <script> block instead of swapping.\n"
                "Worked example: app/templates/partials/_capability_heatmap.html.\n"
                "If a hit is genuinely fine, append 'attr-quoting-ok: <reason>'."
            )
    print(len(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
