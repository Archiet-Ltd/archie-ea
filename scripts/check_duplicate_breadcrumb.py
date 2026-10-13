#!/usr/bin/env python
"""Find a page that renders TWO independent breadcrumb trails at once.

The mirror image of `check_breadcrumb_coverage.py` (which catches a page with
NO breadcrumb): this catches a page with more than one. Found in production
on the ArchiMate element detail page (fixed in e30ad429) -- a `{% block
breadcrumb %}` called `breadcrumb_nav()`, AND a second, independent inline
`<nav>` breadcrumb sat in `{% block content %}`, so the rendered page showed
two crumb trails stacked on top of each other. Duplicate breadcrumbs were
independently found and fixed on at least two other pages earlier this
session -- one hand-fixed instance is not evidence the class is closed.

Four ways a page can contribute a breadcrumb; more than one present (outside
an already-counted span) is a duplicate:

  block-breadcrumb     a non-empty `{% block breadcrumb %}...{% endblock %}`
  breadcrumb-nav-call  a `breadcrumb_nav(` invocation outside that block
  header-arg           a `breadcrumb=`/`breadcrumbs=` kwarg passed to
                        page_shell()/page_header()
  inline-nav           a `<nav>...</nav>` elsewhere in the page whose content
                        is breadcrumb-shaped -- it contains a chevron/slash
                        separator icon or `aria-label="breadcrumb"`, the same
                        shape `components/breadcrumb_nav.html` renders

Partials (`_`-prefixed, or under a `partials/` directory) and the macro/
component definition files themselves (`macros/`, `components/` directories)
are excluded -- they define the pattern, a page instantiates it.

Escape hatch: append `duplicate-breadcrumb-ok: <reason>` on any one of the
flagged lines, or the line immediately above it.

Usage:
    python scripts/check_duplicate_breadcrumb.py            # list findings
    python scripts/check_duplicate_breadcrumb.py --count    # trailing = count

Proven-against: app/templates/archimate_crud/detail.html before e30ad429 --
a `{% block breadcrumb %}` calling `breadcrumb_nav(...)` alongside a second,
independent `<nav>` containing chevron-separated `<a>`/`<span>` elements in
`{% block content %}` -- flagged with two sources (block-breadcrumb,
inline-nav); removing the `{% block breadcrumb %}` (as landed) clears it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import hygiene_text

ROOTS = ("app/templates", "app/modules")
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".worktrees", ".claude",
    "macros", "components",
}
ALLOW = re.compile(r"duplicate-breadcrumb-ok:")

BLOCK_BC = re.compile(r"\{%\s*block\s+breadcrumb\s*%\}(.*?)\{%\s*endblock\s*%\}", re.S)
BC_NAV_CALL = re.compile(r"breadcrumb_nav\s*\(")
HEADER_CALL = re.compile(r"page_(?:shell|header)\s*\(")
HEADER_BC_ARG = re.compile(r"breadcrumbs?\s*=")
INLINE_NAV = re.compile(r"<nav\b[^>]*>(.*?)</nav>", re.S)
BREADCRUMB_SHAPED = re.compile(r"chevron|aria-label\s*=\s*[\"']breadcrumb[\"']", re.I)


def _is_partial(path: Path) -> bool:
    if path.name.startswith("_"):
        return True
    return "partials" in path.parts


def _mask_comments(text: str) -> str:
    """Blank out `{# ... #}` / `<!-- ... -->` comment bodies, preserving every
    other character (including newlines) so offsets/line numbers still line
    up with the original text.

    Without this, prose that merely *describes* the pattern -- e.g. a
    breadcrumb-ok comment explaining "a second breadcrumb_nav() call used to
    live in a {% block breadcrumb %} above" -- is itself template-shaped text
    and was matched as a second live source, corrupting the very
    justification comment the escape hatch relies on.

    The pattern and the blanking primitive live in hygiene_text, shared
    with the checkers below that need the identical comment shape or the
    identical length-preserving blank.
    """
    return hygiene_text.mask(text, hygiene_text.HTML_OR_JINJA_COMMENT_RE)


def _sources(text: str) -> list[tuple[int, str]]:
    scan_text = _mask_comments(text)
    hits: list[tuple[int, str]] = []
    block_spans: list[tuple[int, int]] = []

    for m in BLOCK_BC.finditer(scan_text):
        body = m.group(1)
        if body.strip():
            idx = scan_text.count("\n", 0, m.start())
            hits.append((idx + 1, "block-breadcrumb"))
        block_spans.append((m.start(), m.end()))

    def _in_block(pos: int) -> bool:
        return any(a <= pos < b for a, b in block_spans)

    for m in BC_NAV_CALL.finditer(scan_text):
        if _in_block(m.start()):
            continue
        idx = scan_text.count("\n", 0, m.start())
        hits.append((idx + 1, "breadcrumb-nav-call"))

    for m in HEADER_CALL.finditer(scan_text):
        window = scan_text[m.start():m.start() + 800]
        if HEADER_BC_ARG.search(window):
            idx = scan_text.count("\n", 0, m.start())
            hits.append((idx + 1, "header-arg"))

    for m in INLINE_NAV.finditer(scan_text):
        if _in_block(m.start()):
            continue
        if BREADCRUMB_SHAPED.search(m.group(1)):
            idx = scan_text.count("\n", 0, m.start())
            hits.append((idx + 1, "inline-nav"))

    return hits


def _excused(text: str, lines: list[str], hit_lines: list[int]) -> bool:
    for ln in hit_lines:
        idx = ln - 1
        if idx < len(lines) and ALLOW.search(lines[idx]):
            return True
        if idx > 0 and ALLOW.search(lines[idx - 1]):
            return True
    return False


def _scan(root: Path) -> list[tuple[str, list[tuple[int, str]]]]:
    findings = []
    for path in root.rglob("*.html"):
        # Check skip-dirs against the path RELATIVE to root -- the absolute
        # root itself may sit under a dir named in SKIP_DIRS (e.g. a
        # .worktrees checkout), which would otherwise skip every file in the
        # tree and make this gate silently scan nothing.
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if _is_partial(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        sources = _sources(text)
        if len(sources) < 2:
            continue
        lines = text.split("\n")
        if _excused(text, lines, [ln for ln, _ in sources]):
            continue
        findings.append((path.as_posix(), sources))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", action="store_true")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    findings: list[tuple[str, list[tuple[int, str]]]] = []
    for r in ROOTS:
        rp = root / r
        if rp.exists():
            findings.extend(_scan(rp))
    findings.sort()

    if not args.count:
        for path, sources in findings:
            kinds = ", ".join(f"{kind}@{line}" for line, kind in sorted(sources))
            print(f"{path}: {len(sources)} breadcrumb sources ({kinds})")
        if findings:
            print()
            print(
                "A page must render exactly one breadcrumb trail. Keep the one\n"
                "actually wired up (Alpine handlers, dynamic segments) and delete\n"
                "the rest, or mark a line 'duplicate-breadcrumb-ok: <reason>' if a\n"
                "second nav is genuinely not a breadcrumb."
            )
    print(len(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
