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

`--page-shell-block-count` is the narrower, R34 (29 Oct 2026) variant of the same
scan: a page that calls `page_shell(` and ALSO fills `{% block breadcrumb %}`
shows the page header's own breadcrumb (which follows the sidebar) stacked above
the template's hand-written one. Group A of T-crumb-01 found and fixed 32 such
pages by deleting the block; this mode holds that count at its post-fix floor
(ratcheted at 0, since every one of those 32 is now closed) so a new page cannot
reintroduce the same stacked pair. It does not count `page_header(` (the other
header macro, tracked separately as Group B) or a bare inline `<nav>` with no
`page_shell` call at all -- those are the broader `duplicate-breadcrumb` gate's
job, not this one's.

Usage:
    python scripts/check_duplicate_breadcrumb.py            # list findings
    python scripts/check_duplicate_breadcrumb.py --count    # trailing = count
    python scripts/check_duplicate_breadcrumb.py --page-shell-block-count
                                                              # the R34 variant

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
PAGE_SHELL_CALL = re.compile(r"page_shell\s*\(")


def _is_partial(path: Path) -> bool:
    if path.name.startswith("_"):
        return True
    return "partials" in path.parts


_COMMENT = re.compile(r"\{#.*?#\}|<!--.*?-->", re.S)


def _mask_comments(text: str) -> str:
    """Blank out `{# ... #}` / `<!-- ... -->` comment bodies, preserving every
    other character (including newlines) so offsets/line numbers still line
    up with the original text.

    Without this, prose that merely *describes* the pattern -- e.g. a
    breadcrumb-ok comment explaining "a second breadcrumb_nav() call used to
    live in a {% block breadcrumb %} above" -- is itself template-shaped text
    and was matched as a second live source, corrupting the very
    justification comment the escape hatch relies on.
    """
    def _blank(m: re.Match) -> str:
        s = m.group(0)
        return "".join(c if c == "\n" else " " for c in s)

    return _COMMENT.sub(_blank, text)


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


def _calls_page_shell_and_defines_block(text: str) -> bool:
    """True when a template calls ``page_shell(`` AND also fills a non-empty
    ``{% block breadcrumb %}`` -- the page header's own breadcrumb (which follows
    the sidebar, see R34) stacked above a second, hand-written one."""
    scan_text = _mask_comments(text)
    if not PAGE_SHELL_CALL.search(scan_text):
        return False
    for m in BLOCK_BC.finditer(scan_text):
        if m.group(1).strip():
            return True
    return False


def _scan_page_shell_block(root: Path) -> list[str]:
    findings = []
    for path in root.rglob("*.html"):
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if _is_partial(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if not _calls_page_shell_and_defines_block(text):
            continue
        # No escape hatch here, deliberately: R34 holds unconditionally that the page
        # header's own breadcrumb (which already follows the sidebar) is the one and the
        # template's {% block breadcrumb %} goes -- a "they're mutually exclusive at
        # runtime" comment does not change that two independent breadcrumb sources are
        # declared in the template, which is what this mode counts.
        findings.append(path.as_posix())
    return findings


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
    ap.add_argument("--page-shell-block-count", action="store_true",
                     help="count templates calling page_shell( that also fill {% block breadcrumb %}")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    root = Path(args.root).resolve()

    if args.page_shell_block_count:
        psb_findings: list[str] = []
        for r in ROOTS:
            rp = root / r
            if rp.exists():
                psb_findings.extend(_scan_page_shell_block(rp))
        psb_findings.sort()
        if not args.count:
            for path in psb_findings:
                print(f"{path}: calls page_shell( and fills {{% block breadcrumb %}}")
            if psb_findings:
                print()
                print(
                    "The page header's own breadcrumb already follows the sidebar (R34). Delete\n"
                    "the template's {% block breadcrumb %}, carrying any record crumb it named\n"
                    "through page_shell(breadcrumb=[...]) instead."
                )
        print(len(psb_findings))
        return 1 if psb_findings else 0

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
