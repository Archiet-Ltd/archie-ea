"""Count page-shell conformance violations in templates.

The 14 Aug 2026 UX audit found the platform ships three competing page-header
systems and two page widths, which is why adjacent modules look like different
products (docs/plans/audit-20260814-ai-gaps-and-ux-consistency.md). This
checker makes that drift a number so verify.py can ratchet it downward.

Two violation classes, counted per file:

1. header-less page: a template that ``extends`` an admin layout but imports
   neither ``page_header`` nor ``page_shell`` and draws no library breadcrumb page — it is hand-rolling its own
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

Known limits. This is a ratchet that stops new pages without a page column; a static scan of source cannot
prove what renders. It does not see: a level-one heading in a body set by ``{% set %}`` or in a block the
layout never defines; a condition that is false without being the literal ``false`` (``{% if 0 == 1 %}``);
a breadcrumb expression that renders an icon only or a blank; or a hidden parent of the heading. Review and
the browser walk (tests/smoke/) cover those.

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

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_design_map import MARKER_RE, load_map  # noqa: E402

TEMPLATE_DIRS = [
    ROOT / "app" / "templates",
    *(p for p in (ROOT / "app" / "modules").glob("*/templates")),
]

EXTENDS_RE = re.compile(r"{%-?\s*extends\s+['\"]([^'\"]+)['\"]")
# A page also has its header when it draws it from the library: a breadcrumb trail ending in the page
# (ui.breadcrumb_page) above its own title.
HEADER_RE = re.compile(r"\b(page_header|page_shell|breadcrumb_page)\b")
OK_RE = re.compile(r"shell-ok:\s*\S")
HEADING_RE = re.compile(r"<h1\b([^>]*)>(.*?)</h1\s*>", re.DOTALL | re.IGNORECASE)
JINJA_COMMENT_RE = re.compile(r"{#.*?#}", re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
RAW_BODY_RE = re.compile(r"<(script|style|template|dialog)\b.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
LIBRARY_DIR = ROOT / "app" / "templates" / "basecoat"
MACRO_NAME_RE = re.compile(r"{%-?\s*macro\s+(\w+)")
# Library files whose macros render content hidden until it is opened or expanded: every overlay, menu,
# popup, toast and disclosure. A library file added to app/templates/basecoat must be named here or in
# SHOWN_LIBRARY_FILES (tests/test_template_shell_rules.py fails until it is), so the list cannot drift.
OVERLAY_LIBRARY_FILES = (
    "accordion", "alert-dialog", "collapsible", "combobox", "context-menu", "dialog", "drawer",
    "dropdown-menu", "hover-card", "menubar", "navigation-menu", "popover", "select", "sheet", "sonner", "toast",
    "tooltip",
)
SHOWN_LIBRARY_FILES = (
    "alert", "aspect-ratio", "attachment", "avatar", "badge", "breadcrumb", "bubble", "button",
    "button-group", "calendar", "card", "carousel", "chart", "checkbox", "code-block", "command", "direction",
    "empty", "field", "form", "index", "input", "input-group", "input-otp", "item", "kbd", "label", "macros",
    "marker", "message", "message-scroller", "native-select", "pagination", "progress", "questionnaire",
    "radio-group", "resizable", "scroll-area", "separator", "sidebar", "skeleton", "slider", "spinner",
    "switch", "table", "tabs", "textarea", "toggle", "toggle-group", "trace",
)
# Macros the library does not define but the app does, plus the two overlay macros of files shown otherwise.
EXTRA_OVERLAY_MACROS = (
    "modal", "confirmation_dialog", "command_dialog", "trace_collapsible", "trace_collapsible_content",
)


def library_overlay_macros() -> set[str]:
    """Every macro name the library's overlay files define under their own name, read from the files."""
    names = set(EXTRA_OVERLAY_MACROS)
    for stem in OVERLAY_LIBRARY_FILES:
        path = LIBRARY_DIR / f"{stem}.html"
        if not path.is_file():
            continue
        prefix = stem.replace("-", "_")
        names.update(n for n in MACRO_NAME_RE.findall(path.read_text(encoding="utf-8"))
                     if n == prefix or n.startswith(prefix + "_"))
    return names


OVERLAY_MACROS = tuple(sorted(library_overlay_macros()))
OVERLAY_OPEN_RE = re.compile(
    r"<(\w+)\b[^>]*\b(?:role\s*=\s*[\"']?(?:dialog|alertdialog)\b|aria-modal\s*=\s*[\"']?true\b)[^>]*>"
    r"|{%-?\s*call\s*(?:\([^)]*\)\s*)?(?:\w+\.)?(?:" + "|".join(OVERLAY_MACROS) + r")\s*\(",
    re.IGNORECASE)
CALL_TOKEN_RE = re.compile(r"{%-?\s*call\b|{%-?\s*endcall\b", re.IGNORECASE)
RAW_JINJA_RE = re.compile(r"{%-?\s*raw\s*-?%}.*?{%-?\s*endraw\s*-?%}", re.DOTALL)
MACRO_DEF_RE = re.compile(r"{%-?\s*macro\b.*?{%-?\s*endmacro\s*-?%}", re.DOTALL)
IF_FALSE_RE = re.compile(r"{%-?\s*if\s+(?:false|False|0|none|None)\s*-?%}", re.IGNORECASE)
IF_TOKEN_RE = re.compile(r"{%-?\s*(if|endif)\b[^%]*%}")
BLOCK_TOKEN_RE = re.compile(r"{%-?\s*(block\s+(\w+)|endblock)\b[^%]*%}")
HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
BREADCRUMB_CALL_RE = re.compile(r"\bbreadcrumb\w*\s*\(", re.IGNORECASE)
EMPTY_EXPR_RE = re.compile(r"{{-?\s*(?:(['\"])\s*\1|super\(\s*\))\s*-?}}")
JINJA_TAG_RE = re.compile(r"{%.*?%}", re.DOTALL)
HTML_TAG_RE = re.compile(r"<[^>]*>")
CONTAINER_RE = re.compile(r"\bcontainer\s+mx-auto\b")

# Layouts whose children are full pages and owe the platform a header.
PAGE_LAYOUTS = ("layouts/admin_base.html",)


def _drop_overlays(text: str) -> str:
    """Remove each overlay with its body: any element with role=dialog or aria-modal, and a call to any overlay
    macro, with or without the library namespace and with or without caller arguments
    (``{% call(row) ui.dialog(...) %}``).

    The overlay macros are read from the library's own files (see OVERLAY_LIBRARY_FILES): dialogs, sheets,
    drawers, menus, popovers, comboboxes, selects, navigation menus, toasts, accordions and collapsibles.
    """
    while True:
        m = OVERLAY_OPEN_RE.search(text)
        if not m:
            return text
        end = len(text)
        if m.group(1):
            tag = re.escape(m.group(1))
            depth = 1
            for t in re.finditer(rf"<{tag}\b[^>]*?(/?)>|</{tag}\s*>", text[m.end():], re.IGNORECASE):
                if t.group(0).startswith("</"):
                    depth -= 1
                elif not t.group(1):
                    depth += 1
                if depth == 0:
                    end = m.end() + t.end()
                    break
        else:
            depth = 1
            for t in CALL_TOKEN_RE.finditer(text, m.end()):
                depth += -1 if "endcall" in t.group(0).lower() else 1
                if depth == 0:
                    end = t.end()
                    break
        text = text[:m.start()] + text[end:]


def _drop_modals_block(text: str) -> str:
    """Remove the body of ``{% block modals %}``, which the layout renders outside the page column."""
    while True:
        m = re.search(r"{%-?\s*block\s+modals\b[^%]*%}", text)
        if not m:
            return text
        depth, end = 1, len(text)
        for t in BLOCK_TOKEN_RE.finditer(text, m.end()):
            depth += -1 if t.group(0).lstrip("{%- ").startswith("endblock") else 1
            if depth == 0:
                end = t.end()
                break
        text = text[:m.start()] + text[end:]


def _drop_if_false(text: str) -> str:
    """Remove each ``{% if false %}`` body (to its matching endif), which never renders."""
    while True:
        m = IF_FALSE_RE.search(text)
        if not m:
            return text
        depth, end = 1, len(text)
        for t in IF_TOKEN_RE.finditer(text, m.end()):
            depth += 1 if t.group(1) == "if" else -1
            if depth == 0:
                end = t.end()
                break
        text = text[:m.start()] + text[end:]


def _block_bodies(text: str) -> dict[str, str]:
    """name -> body for every block; a block's body includes any block nested in it."""
    bodies: dict[str, list[str]] = {}
    stack: list[tuple[str, int]] = []
    for t in BLOCK_TOKEN_RE.finditer(text):
        if t.group(2):
            stack.append((t.group(2), t.end()))
        elif stack:
            name, start = stack.pop()
            bodies.setdefault(name, []).append(text[start:t.start()])
    return {k: "\n".join(v) for k, v in bodies.items()}


def _inside_blocks(text: str) -> str:
    """The text a child template renders: only what sits inside a block, as Jinja drops the rest."""
    parts, depth, last = [], 0, 0
    for t in BLOCK_TOKEN_RE.finditer(text):
        if t.group(2):
            if depth == 0:
                last = t.end()
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                parts.append(text[last:t.start()])
    return "\n".join(parts)


def visible_text(text: str) -> str:
    """The page as a person could see it: no comments, raw text, macro definitions, ``if false`` bodies,
    scripts, styles, templates, the layout's modals block, or overlays."""
    text = JINJA_COMMENT_RE.sub("", text)
    text = HTML_COMMENT_RE.sub("", text)
    text = RAW_JINJA_RE.sub("", text)
    text = MACRO_DEF_RE.sub("", text)
    text = _drop_if_false(text)
    text = RAW_BODY_RE.sub("", text)
    return _drop_overlays(_drop_modals_block(text))


SHOWN_AT_WIDTH_RE = re.compile(r"^[\w-]+:(?:block|flex|grid|inline|inline-block|inline-flex|table|contents)$")
NOT_SR_ONLY_RE = re.compile(r"^[\w-]+:not-sr-only$")


def _is_hidden(attrs: str) -> bool:
    """True for a tag hidden by the hidden class or attribute, sr-only, x-show="false" or an inline style.

    A class that hides at one width and shows at another (``hidden md:block``, ``sr-only sm:not-sr-only``) is
    visible at some width and does not count as hidden.
    """
    classes = re.search(r"\bclass\s*=\s*[\"']([^\"']*)[\"']", attrs)
    tokens = classes.group(1).split() if classes else []
    if any(SHOWN_AT_WIDTH_RE.match(t) or NOT_SR_ONLY_RE.match(t) for t in tokens):
        tokens = [t for t in tokens if t not in ("hidden", "sr-only")]
    rest = attrs[:classes.start()] + attrs[classes.end():] if classes else attrs
    return bool(
        "hidden" in tokens or "sr-only" in tokens
        or re.search(r"(?:^|\s)hidden(?=\s|=|$)", rest)
        or re.search(r"x-show\s*=\s*[\"']\s*false\s*[\"']", attrs)
        or HIDDEN_STYLE_RE.search(attrs))


def _has_text(fragment: str) -> bool:
    """True when the fragment holds something a person could read once tags are removed."""
    fragment = EMPTY_EXPR_RE.sub("", fragment)
    fragment = JINJA_TAG_RE.sub("", fragment)
    return bool(HTML_TAG_RE.sub("", fragment).strip())


def has_visible_heading(body: str) -> bool:
    """True for a level-one heading that has text and is not marked hidden.

    Source cannot settle visibility in general: a parent that is hidden, a rule in a stylesheet or a state set
    by script all escape this check. It rejects what the source states outright (an empty heading, the hidden
    class or attribute, sr-only, ``x-show="false"``, an inline display:none) and leaves the rest to the rendered
    checks in tests/smoke/.
    """
    return any(not _is_hidden(m.group(1)) and _has_text(m.group(2)) for m in HEADING_RE.finditer(body))


def has_visible_breadcrumb(body: str) -> bool:
    """True when the breadcrumb block holds a breadcrumb macro call or visible text.

    ``{{ '' }}``, ``{{ super() }}`` alone, an empty element and an element marked hidden do not count.
    """
    crumb = _block_bodies(body).get("breadcrumb")
    if crumb is None:
        return False
    crumb = EMPTY_EXPR_RE.sub("", crumb)
    if BREADCRUMB_CALL_RE.search(crumb):
        return True
    shown = re.sub(r"<(\w+)\b([^>]*)>.*?</\1\s*>",
                   lambda m: "" if _is_hidden(m.group(2)) else m.group(0), crumb, flags=re.DOTALL)
    return _has_text(shown)


def draws_page_column(text: str, rel: str | None = None) -> bool:
    """True for a child page with a design-block marker, a visible breadcrumb and a visible level-one heading.

    The marker must name a block docs/design-map.json declares; when the map has a row for the page (``rel``,
    the repo-relative path) it must name that row's own block. The heading and the breadcrumb must sit inside
    a ``{% block %}`` body, outside ``{% raw %}``, ``{% if false %}``, macro definitions, comments, scripts,
    styles, templates and overlays (see _drop_overlays).
    """
    marker = MARKER_RE.search(RAW_BODY_RE.sub("", HTML_COMMENT_RE.sub("", text)))
    if not marker:
        return False
    design_map = load_map()
    entry = design_map["pages"].get(rel) if rel else None
    allowed = {entry["block"]} if entry and entry.get("block") else set(design_map["blocks"])
    if marker.group(1) not in allowed:
        return False
    body = _inside_blocks(visible_text(text))
    return has_visible_breadcrumb(visible_text(text)) and has_visible_heading(body)


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
            if is_page and not HEADER_RE.search(text) and not draws_page_column(text, rel):
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
