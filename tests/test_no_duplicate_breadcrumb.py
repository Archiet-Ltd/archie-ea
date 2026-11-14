"""No page renders two breadcrumb trails at once.

A page that is held by the navigation registry gets a shell-rendered breadcrumb
from page_trail() via page_shell()/page_header() even without a breadcrumbs=
argument. If that same page also carries a {% block breadcrumb %}, the rendered
page shows two crumb trails stacked on top of each other.

This gate scans every routed template and asserts no page combines both.
Before the fix, 46 pages did (capability_roadmap plus 45 others); after the
fix, the block breadcrumb was removed from every page that also gets the
shell-rendered trail. entity_diagram.html is the single exception: its two
page_shell() calls sit in mutually exclusive if/else branches, so only one
ever renders, and it carries the duplicate-breadcrumb-ok marker.

No database is needed.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.navigation.registry import entry_for_endpoint  # noqa: E402

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
BLOCK_BC = re.compile(r"{%\s*block\s+breadcrumb\s*%}")
HEADER_CALL = re.compile(r"page_(?:shell|header)\s*\(")
ALLOW = re.compile(r"duplicate-breadcrumb-ok:")

_INVENTORY = json.loads(Path("docs/component-inventory.json").read_text())
_TEMPLATE_TO_ENDPOINTS: dict[str, list[str]] = {}
for _route in _INVENTORY.get("routes", []):
    if _route.get("page") and _route.get("templates"):
        for _t in _route["templates"]:
            _TEMPLATE_TO_ENDPOINTS.setdefault(_t, []).append(_route["endpoint"])


def _in_nav_registry(template_path: str) -> bool:
    for _ep in _TEMPLATE_TO_ENDPOINTS.get("app/templates/" + template_path, []):
        _entry = entry_for_endpoint(_ep)
        if _entry is not None and _entry.key != "home":
            return True
    return False


def _offenders() -> list[str]:
    found = []
    for path in TEMPLATES.rglob("*.html"):
        rel = path.relative_to(TEMPLATES).as_posix()
        if rel.startswith("_") or "partials" in path.parts or "macros" in path.parts or "components" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not BLOCK_BC.search(text):
            continue
        if not HEADER_CALL.search(text):
            continue
        if not _in_nav_registry(rel):
            continue
        if ALLOW.search(text):
            continue
        found.append(rel)
    return sorted(found)


def test_no_page_renders_two_breadcrumb_trails():
    offenders = _offenders()
    assert offenders == [], (
        f"pages render both a {{% block breadcrumb %}} and a shell-rendered "
        f"breadcrumb (page_trail via page_shell/page_header): {offenders}"
    )


# --- No trail at all -------------------------------------------------------
# The other half of the rule: a shell page must show one trail. A page whose
# template calls page_shell/page_header but passes no breadcrumb argument and
# has no {% block breadcrumb %} relies on the header macro drawing the registry
# trail (page_trail()); if the page's endpoint is not held by the registry, or
# the header macro in use does not consult page_trail, the page ships with no
# breadcrumb at all. Before the fix, the four enterprise/strategic pages that
# call page_header(title=...) only fell into this hole; after the fix every
# shell page is registry-held and both header macros draw page_trail().

BC_ARG = re.compile(r"breadcrumb(?:s)?\s*=")
HEADER_USE = re.compile(r"page_(shell|header)\s*\(")
HEADER_COMPONENT = {
    "shell": TEMPLATES / "macros" / "page_shell.html",
    "header": TEMPLATES / "components" / "page_header.html",
}


def _trailless_offenders() -> list[str]:
    found: list[str] = []
    seen: set[tuple[str, str]] = set()
    for _route in _INVENTORY.get("routes", []):
        if not (_route.get("page") and _route.get("templates")):
            continue
        _entry = entry_for_endpoint(_route["endpoint"])
        _held = _entry is not None and _entry.key != "home"
        for _t in _route["templates"]:
            if (_route["endpoint"], _t) in seen:
                continue
            seen.add((_route["endpoint"], _t))
            _path = Path(__file__).resolve().parents[1] / _t
            if not _path.exists():
                continue
            _text = _path.read_text(encoding="utf-8", errors="ignore")
            _uses = sorted(set(HEADER_USE.findall(_text)))
            if not _uses:
                continue
            if BLOCK_BC.search(_text) or BC_ARG.search(_text):
                continue
            if not _held:
                found.append(f"{_route['endpoint']} ({_t}) — not held by the navigation registry")
                continue
            for _kind in _uses:
                _component = HEADER_COMPONENT[_kind].read_text(encoding="utf-8", errors="ignore")
                if "page_trail" not in _component:
                    found.append(
                        f"{_route['endpoint']} ({_t}) — {_kind}() does not draw the registry trail"
                    )
    return sorted(set(found))


def test_every_shell_page_has_one_breadcrumb_trail():
    offenders = _trailless_offenders()
    assert offenders == [], (
        "shell pages with no breadcrumb trail of their own must be held by the "
        "navigation registry and drawn via page_trail() by the header macro they "
        f"call: {offenders}"
    )
