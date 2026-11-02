"""Count page-shell conformance violations in templates.

The 14 Aug 2026 UX audit found the platform ships three competing page-header
systems and two page widths, which is why adjacent modules look like different
products (docs/plans/audit-20260814-ai-gaps-and-ux-consistency.md). This
checker makes that drift a number so verify.py can ratchet it downward.

Two violation classes, counted per file:

1. header-less page: a template that ``extends`` an admin layout but imports
   neither ``page_header`` nor ``page_shell`` — it is hand-rolling its own
   header (or shipping none).
2. off-width page: a template using the ``container mx-auto`` wrapper instead
   of the documented ``p-6 space-y-6`` (DESIGN.md).

The page column. A page that draws the library's page column, the way the
design map declares it for the page's block, is not header-less even though it
imports neither macro (check_design_map.py counts both macros as replaced). A
page draws the page column when it carries all three of: a ``design-block:``
marker naming a block docs/design-map.json declares, a ``breadcrumb`` block
with content, and a level-one heading (``<h1``; the library has no heading
macro) in the page body. Comments, script and style bodies, dialogs, drawers,
sheets and templates are removed first, so a heading that is only in one of
those does not count. A page with no heading, an empty breadcrumb block, an
unknown block name, or a heading but no breadcrumb or marker, is still
header-less.

A file-level escape hatch ``{# shell-ok: <reason> #}`` excludes a template
that is deliberately non-standard (e.g. print/export layouts), keeping the
exception reviewable rather than silent.

Three further rules count per page, each its own ratchet in verify.py:

  layout   a page that does not reach an approved layout (admin, public or
           composer) through its chain of ``extends``, or a full HTML
           document that extends nothing;
  chrome   a page drawing its own sidebar, top bar, navigation or breadcrumb
           (an <aside>, <nav>, <header> or breadcrumb landmark) instead of
           the shell macros;
  escapes  every ``shell-ok:`` escape hatch, so the number of exceptions is
           visible and can only fall.

Usage:
    python scripts/check_shell_conformance.py --count   # total violations
    python scripts/check_shell_conformance.py --list    # file: reason lines
    python scripts/check_shell_conformance.py --rule layout|chrome|escapes [--count|--list]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESIGN_MAP = Path(__file__).resolve().parent.parent / "docs" / "design-map.json"

TEMPLATE_DIRS = [
    ROOT / "app" / "templates",
    *(p for p in (ROOT / "app" / "modules").glob("*/templates")),
]

EXTENDS_RE = re.compile(r"{%-?\s*extends\s+['\"]([^'\"]+)['\"]")
HEADER_RE = re.compile(r"\b(page_header|page_shell)\b")
OK_RE = re.compile(r"shell-ok:\s*\S")
BLOCK_MARKER_RE = re.compile(r"{#-?\s*design-block:\s*([a-z][a-z-]*)\s*-?#}")
BREADCRUMB_BLOCK_RE = re.compile(r"{%-?\s*block\s+breadcrumb\b[^%]*%}(.*?){%-?\s*endblock\b", re.DOTALL)
HEADING_RE = re.compile(r"<h1\b", re.IGNORECASE)
JINJA_COMMENT_RE = re.compile(r"{#.*?#}", re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
RAW_BODY_RE = re.compile(r"<(script|style|template|dialog)\b.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
OVERLAY_OPEN_RE = re.compile(
    r"<div\b[^>]*\brole\s*=\s*[\"']?(?:dialog|alertdialog)\b[^>]*>|{%-?\s*call\s+(?:modal|drawer|sheet|dialog)\w*\s*\(",
    re.IGNORECASE)
TAG_TOKEN_RE = re.compile(r"<div\b[^>]*>|</div\s*>|{%-?\s*call\b|{%-?\s*endcall\b", re.IGNORECASE)
CONTAINER_RE = re.compile(r"\bcontainer\s+mx-auto\b")

# Layouts whose children are full pages and owe the platform a header.
PAGE_LAYOUTS = ("layouts/admin_base.html",)


def design_blocks() -> set[str]:
    """The block names docs/design-map.json declares, the one list of valid markers."""
    return set(json.loads(DESIGN_MAP.read_text(encoding="utf-8"))["blocks"])


def _drop_overlays(text: str) -> str:
    """Remove each role=dialog div and each modal/drawer/sheet call, with its body."""
    while True:
        m = OVERLAY_OPEN_RE.search(text)
        if not m:
            return text
        is_call = m.group(0).lstrip().startswith("{%")
        depth, end = 1, len(text)
        if is_call:
            for t in TAG_TOKEN_RE.finditer(text, m.end()):
                tok = t.group(0).lower()
                if tok.startswith("{%") and "endcall" in tok:
                    depth -= 1
                elif tok.startswith("{%"):
                    depth += 1
                if depth == 0:
                    end = t.end()
                    break
        else:
            for t in TAG_TOKEN_RE.finditer(text, m.end()):
                tok = t.group(0).lower()
                if tok.startswith("</div"):
                    depth -= 1
                elif tok.startswith("<div"):
                    depth += 1
                if depth == 0:
                    end = t.end()
                    break
        text = text[:m.start()] + text[end:]


def visible_text(text: str) -> str:
    """The page as a person could see it: no comments, scripts, styles, templates or dialogs."""
    text = JINJA_COMMENT_RE.sub("", text)
    text = HTML_COMMENT_RE.sub("", text)
    text = RAW_BODY_RE.sub("", text)
    return _drop_overlays(text)


def draws_page_column(text: str) -> bool:
    """True for a page with a declared design-block marker, a breadcrumb block with content and a level-one heading."""
    marker = BLOCK_MARKER_RE.search(RAW_BODY_RE.sub("", HTML_COMMENT_RE.sub("", text)))
    if not marker or marker.group(1) not in design_blocks():
        return False
    body = visible_text(text)
    crumb = BREADCRUMB_BLOCK_RE.search(body)
    return bool(crumb and crumb.group(1).strip() and HEADING_RE.search(body))


def find_violations() -> list[tuple[str, str]]:
    violations: list[tuple[str, str]] = []
    for base in TEMPLATE_DIRS:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.html")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if OK_RE.search(text):
                continue
            rel = str(path.relative_to(ROOT)).replace("\\", "/")
            extends = EXTENDS_RE.search(text)
            is_page = bool(extends and extends.group(1) in PAGE_LAYOUTS)
            if is_page and not HEADER_RE.search(text) and not draws_page_column(text):
                violations.append((rel, "no page_header/page_shell"))
            if CONTAINER_RE.search(text):
                violations.append((rel, "container mx-auto wrapper"))
    return violations


APPROVED_LAYOUTS = (
    "layouts/admin_base.html",
    "layouts/public_base.html",
    "layouts/composer_base.html",
)
LAYOUT_DIR = "layouts/"
EXTENDS_ANY_RE = re.compile(r"{%-?\s*extends\b")
HTML_DOC_RE = re.compile(r"<html\b", re.IGNORECASE)
# Navigation or bar chrome a page must take from the shell macros.
CHROME_RES = (
    (re.compile(r"<aside\b", re.IGNORECASE), "own sidebar (<aside>)"),
    (re.compile(r"<nav\b", re.IGNORECASE), "own navigation (<nav>)"),
    (re.compile(r"<header\b", re.IGNORECASE), "own header bar (<header>)"),
    (re.compile(r"aria-label=[\"']breadcrumb[\"']", re.IGNORECASE), "hand-built breadcrumb"),
)
MACRO_BLOCK_RE = re.compile(r"{%-?\s*macro\b.*?{%-?\s*endmacro\s*-?%}", re.DOTALL)
COMMENT_RE = re.compile(r"{#.*?#}", re.DOTALL)


def _templates():
    """(repo path, template name, text) for every .html template, in a stable order."""
    for base in TEMPLATE_DIRS:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.html")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            yield (str(path.relative_to(ROOT)).replace("\\", "/"),
                   str(path.relative_to(base)).replace("\\", "/"), text)


def _blank(match: re.Match) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _pages(templates):
    """Pages as check_design_map.py defines them (a template that extends something),
    plus full HTML documents extending nothing. Layouts themselves are not pages."""
    for rel, name, text in templates:
        if name.startswith(LAYOUT_DIR):
            continue
        if EXTENDS_ANY_RE.search(text) or HTML_DOC_RE.search(text):
            yield rel, name, text


def layout_violations() -> list[tuple[str, str]]:
    templates = list(_templates())
    by_name: dict[str, str] = {}
    for _rel, name, text in templates:
        by_name.setdefault(name, text)
    out: list[tuple[str, str]] = []
    for rel, _name, text in _pages(templates):
        if OK_RE.search(text):
            continue
        seen, current, reason = set(), text, None
        while True:
            m = EXTENDS_RE.search(current)
            if not m:
                reason = ("extends a computed layout" if EXTENDS_ANY_RE.search(current)
                          else "extends no layout")
                break
            parent = m.group(1)
            if parent in APPROVED_LAYOUTS:
                reason = None
                break
            if parent in seen or parent not in by_name:
                reason = f"extends {parent}, which does not reach an approved layout"
                break
            seen.add(parent)
            current = by_name[parent]
        if reason:
            out.append((rel, f"line 1: {reason}"))
    return out


def chrome_violations() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for rel, _name, text in _pages(list(_templates())):
        if OK_RE.search(text):
            continue
        scan = MACRO_BLOCK_RE.sub(_blank, COMMENT_RE.sub(_blank, text))
        for rx, what in CHROME_RES:
            for m in rx.finditer(scan):
                out.append((rel, f"line {_line(text, m.start())}: {what} outside the shell macros"))
    return out


def escape_hatches() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for rel, _name, text in _templates():
        for m in re.finditer(r"shell-ok:[ \t]*(\S[^\n]*?)\s*(?:#}|$)", text, re.MULTILINE):
            out.append((rel, f"line {_line(text, m.start())}: shell-ok: {m.group(1)}"))
    return out


RULES = {
    "layout": layout_violations,
    "chrome": chrome_violations,
    "escapes": escape_hatches,
}


def main() -> int:
    if "--rule" in sys.argv:
        rule = sys.argv[sys.argv.index("--rule") + 1]
        found = RULES[rule]()
        if "--list" in sys.argv:
            for rel, reason in found:
                print(f"{rel}: {reason}")
        print(len(found))
        return 0
    violations = find_violations()
    if "--list" in sys.argv:
        for rel, reason in violations:
            print(f"{rel}: {reason}")
    print(len(violations))
    return 0


if __name__ == "__main__":
    sys.exit(main())
