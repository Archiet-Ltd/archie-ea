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
    for _ep in _TEMPLATE_TO_ENDPOINTS.get(template_path, []):
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
        "pages render both a {% block breadcrumb %} and a shell-rendered "
        "breadcrumb (page_trail via page_shell/page_header): %s" % offenders
    )
