#!/usr/bin/env python
"""Find `await` / `async` inside an inline Alpine expression, where it silently lies.

Why this is a data-integrity gate, not a style gate
---------------------------------------------------
Archie serves its pages under a CSP with no ``'unsafe-eval'``, so Alpine
expressions written in HTML attributes are not run by the browser's JS engine.
They are parsed and executed by our own interpreter,
``app/static/js/csp/csp-evaluator.js`` -- a synchronous tree-walking evaluator.
That evaluator has no event loop and no coroutine machinery, so it treats
``await`` as a **pass-through**: the unary case returns its operand unchanged
(``case 'await': return a;``). ``async`` is worse -- it is not in the
evaluator's keyword table at all, so ``async load() { ... }`` inside an
``x-data`` object literal is a parse error and the *entire component* fails to
initialise.

The pass-through is the dangerous one, because nothing breaks visibly. Written
in an attribute,

    x-data="{ total: 0, async init() { this.total = await api.count(); } }"

assigns the *Promise object* to ``this.total`` -- or, once the parse fails,
leaves it at its initialiser. Either way the tile renders the literal ``0`` the
component was seeded with, and a reader cannot distinguish that from a measured
zero. That is exactly what happened: the strategic-roadmap statistic tiles
(``app/templates/strategic_roadmap/enhanced_roadmap_fixed.html``) and the sprint
burndown/velocity charts (``app/templates/sprints/charts.html``) both sat on
fabricated zeros for months, on pages that looked perfectly healthy. Archie is a
system of record; a screen that shows a plausible number it never measured is
worse than one that shows nothing, because the user acts on it.

The fix at each site is a promise chain -- ``.then()`` / ``.catch()`` -- which
the evaluator executes correctly, because the callbacks are ordinary closures
it hands to the real Promise implementation. On the failure path the value must
go to ``null``, never ``0`` and never ``''``, so the UI renders an em dash.

What is NOT flagged
-------------------
``await`` inside a ``<script>`` block is ordinary JavaScript run by the browser
and is completely fine -- including ``Alpine.data('foo', () => ({ async init()
{...} }))``, because that factory is browser-evaluated and only its *return
value* reaches Alpine. This checker therefore looks only at Alpine attribute
expressions (``x-data``, ``x-init``, ``x-effect``, ``x-on:*`` / ``@*``, and
every other ``x-``/``@`` attribute), never at script bodies. It also ignores
``await`` appearing inside a string literal or a JS comment within such an
attribute, and masks Jinja ``{{ ... }}`` / ``{% ... %}`` regions first so that
constructs like ``replace("'", "\\'")`` inside a double-quoted attribute do not
desynchronise attribute parsing.

Escape hatch: append ``alpine-await-ok: <reason>`` on the flagged line or the
line above it.

Usage:
    python scripts/check_alpine_await.py            # list findings
    python scripts/check_alpine_await.py --count    # trailing line = count
    python scripts/check_alpine_await.py --root DIR # scan another tree
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import hygiene_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".worktrees"}

# Alpine attribute expressions -- the ones the CSP evaluator parses.
ATTR_RE = re.compile(
    r"""(?P<name>(?:x-[A-Za-z0-9_:.\-]+|@[A-Za-z0-9_:.\-]+))\s*=\s*"""
    r"""(?P<q>["'])(?P<val>.*?)(?P=q)""",
    re.S,
)

# Jinja regions are masked before attribute parsing: a Jinja filter argument may
# legally contain the same quote character that delimits the HTML attribute.
# Shared with check_public_repo_hygiene.py and the other checkers that mask
# the same shape -- see hygiene_text's own module docstring.
JINJA_RE = hygiene_text.JINJA_ANY_RE

AWAIT_RE = re.compile(r"(?<![\w$.])await(?![\w$])")
# Only the executable forms of `async`: `async foo(`, `async (`, `async x =>`,
# `async function`. A property literally named `async` is not a coroutine.
ASYNC_RE = re.compile(r"(?<![\w$.])async(?=\s*(?:\(|[A-Za-z_$]))")

ALLOW_RE = re.compile(r"alpine-await-ok\s*:")


def _mask(text: str, start: int, end: int) -> str:
    """Blank out [start:end) but keep newlines, so line numbers stay true."""
    seg = text[start:end]
    return text[:start] + re.sub(r"[^\n]", " ", seg) + text[end:]


def mask_jinja(text: str) -> str:
    for m in reversed(list(JINJA_RE.finditer(text))):
        text = _mask(text, m.start(), m.end())
    return text


def mask_strings_and_comments(expr: str) -> str:
    """Blank string literals, template literals and JS comments in an expression.

    ``await`` inside quotes or a comment is prose, not code -- the fixed
    templates carry a long explanatory comment about this very bug, and flagging
    their own documentation would make the gate impossible to keep at zero.
    """
    out = list(expr)
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c == "/" and i + 1 < n and expr[i + 1] == "/":
            j = expr.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        if c == "/" and i + 1 < n and expr[i + 1] == "*":
            j = expr.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        if c in "\"'`":
            j = i + 1
            while j < n:
                if expr[j] == "\\":
                    j += 2
                    continue
                if expr[j] == c:
                    j += 1
                    break
                j += 1
            for k in range(i, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def templates(base: str):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if name.endswith(".html"):
                yield os.path.join(dirpath, name)


def scan_file(path: str, rel: str) -> list[tuple[str, int, str, str]]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return []
    if "await" not in raw and "async" not in raw:
        return []

    lines = raw.splitlines()
    masked = mask_jinja(raw)
    findings: list[tuple[str, int, str, str]] = []

    for m in ATTR_RE.finditer(masked):
        expr = mask_strings_and_comments(m.group("val"))
        base = m.start("val")
        for kind, rx in (("await", AWAIT_RE), ("async", ASYNC_RE)):
            for hit in rx.finditer(expr):
                lineno = raw.count("\n", 0, base + hit.start()) + 1
                context = lines[lineno - 1] if lineno - 1 < len(lines) else ""
                prev = lines[lineno - 2] if lineno >= 2 else ""
                if ALLOW_RE.search(context) or ALLOW_RE.search(prev):
                    continue
                findings.append((rel, lineno, m.group("name"), kind))
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
    findings: list[tuple[str, int, str, str]] = []
    for path in templates(os.path.join(root, "app")):
        rel = os.path.relpath(path, root).replace("\\", "/")
        findings.extend(scan_file(path, rel))

    findings.sort()
    if not args.count:
        for rel, line, attr, kind in findings:
            print(f"{rel}:{line}: `{kind}` inside Alpine attribute {attr}")
        if findings:
            print()
            print(
                "The CSP evaluator does not await: the expression assigns the\n"
                "Promise, so the tile renders its initialiser and a fabricated\n"
                "zero is indistinguishable from a measured one. Rewrite as a\n"
                "promise chain (.then/.catch), and set the value to null -- never\n"
                "0 or '' -- on the error path so the UI renders an em dash.\n"
                "Worked examples: app/templates/sprints/charts.html and\n"
                "app/templates/strategic_roadmap/enhanced_roadmap_fixed.html.\n"
                "If a hit is genuinely fine, append 'alpine-await-ok: <reason>'."
            )
    print(len(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
