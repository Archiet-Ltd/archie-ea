#!/usr/bin/env python
"""Sidebar link-count ratchet gate (shell-overhaul Wave 1, Task 3).

`app/templates/components/admin_sidebar.html` renders
`app.utils.role_access.get_sidebar_zones(current_user)` — a single role ->
zones -> links structure with a 25-link-per-role budget
(`SIDEBAR_LINK_BUDGET`). Nothing enforced that budget before this: a future
edit to role_access.py could quietly push one role's zones past 25 without
any test catching it (tests/test_sidebar_budgets.py asserts the *data*, not
the *rendered template* — a template bug, e.g. a stray link outside the
`link.endpoint in view_functions` guard, would slip past it).

This script renders the real template once per role with a stub user (same
pattern as tests/test_sidebar_role_filtering.py's FakeUser — enough of User
for the sidebar to read `enterprise_role` / `is_admin()`, everything else a
benign placeholder) inside a Flask test request context, counts `<a ` tags,
and reports the worst-case role.

Usage
-----
    python scripts/check_sidebar_links.py           # human-readable table
    python scripts/check_sidebar_links.py --json     # {"max_role": ..., "max_links": ...}
    python scripts/check_sidebar_links.py --count    # bare max_links integer (verify.py gate)

Exit code is 1 if any role's rendered sidebar exceeds SIDEBAR_LINK_BUDGET.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("FLASK_CONFIG", "testing")
os.environ.setdefault("SECRET_KEY", "test-only-not-secret")

LINK_RE = re.compile(r"<a ")
# An Alpine <template> (the sidebar search results and no-match state) renders nothing until Alpine
# instantiates it, so an <a> inside one is not a link the sidebar shows and must not count against the budget.
TEMPLATE_RE = re.compile(r"<template[ >].*?</template>", re.S)


SUBITEM_RE = re.compile(r'<a\s[^>]*data-slot="sidebar-subitem"')
TOGGLE_RE = re.compile(r'data-slot="sidebar-collapsible"')


def count_sublinks(html: str) -> int:
    """Level-2 links (sub-menu items) in rendered sidebar HTML."""
    return len(SUBITEM_RE.findall(TEMPLATE_RE.sub("", html)))


def count_links(html: str) -> int:
    """Level-1 entries in rendered sidebar HTML: every link that is not a sub-menu item, plus each
    collapsible item (a toggle button is a level-1 entry). The header logo, the All modules link and
    the logout link are counted here, as before. Sub-menu items are counted separately by
    count_sublinks and capped by SIDEBAR_SUBLINK_BUDGET (grouped sidebar, 20 Oct 2026)."""
    visible = TEMPLATE_RE.sub("", html)
    return len(LINK_RE.findall(visible)) - len(SUBITEM_RE.findall(visible)) + len(TOGGLE_RE.findall(visible))


class _Any:
    """Permissive placeholder for User attributes the sidebar happens to touch."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Any()

    def __call__(self, *args, **kwargs):
        return _Any()

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __str__(self):
        return ""

    def __html__(self):
        return ""


class _StubUser:
    """Enough of User for the sidebar footer + get_sidebar_zones()."""

    def __init__(self, role):
        self.enterprise_role = role
        self.is_authenticated = True
        self.first_name = "Stub"

    def is_admin(self):
        from app.models.user import ROLE_PLATFORM_ADMIN

        return self.enterprise_role == ROLE_PLATFORM_ADMIN

    def full_name(self):
        return "Stub User"

    @property
    def email(self):
        return "stub@example.com"

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Any()


MEASURED_SUBLINKS: dict[str, int] = {}


def measure() -> dict[str, int]:
    """Render the real sidebar template once per role; return role -> link count."""
    from flask import render_template

    from app import create_app
    from app.utils.role_access import SIDEBAR_ZONES

    app = create_app("testing")
    results: dict[str, int] = {}
    MEASURED_SUBLINKS.clear()
    for role in SIDEBAR_ZONES:
        with app.app_context(), app.test_request_context("/"):
            html = render_template(
                "components/admin_sidebar.html", current_user=_StubUser(role)
            )
        results[role] = count_links(html)
        MEASURED_SUBLINKS[role] = count_sublinks(html)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print {max_role, max_links}")
    parser.add_argument("--count", action="store_true", help="print bare max_links integer (level 1)")
    parser.add_argument("--sublinks", action="store_true", help="print bare worst level-2 link count")
    args = parser.parse_args(argv)

    from app.utils.role_access import SIDEBAR_LINK_BUDGET, SIDEBAR_SUBLINK_BUDGET

    results = measure()
    max_role = max(results, key=results.get)
    max_links = results[max_role]
    max_sub = max(MEASURED_SUBLINKS.values())

    if args.sublinks:
        print(max_sub)
        return 0 if max_sub <= SIDEBAR_SUBLINK_BUDGET else 1
    if args.count:
        print(max_links)
    elif args.json:
        print(json.dumps({"max_role": max_role, "max_links": max_links}))
    else:
        for role, count in sorted(results.items(), key=lambda kv: -kv[1]):
            marker = "FAIL" if count > SIDEBAR_LINK_BUDGET else "ok  "
            print(f"{marker} {role:24} {count} links")
        print(f"\nworst case: {max_role} with {max_links} level-1 links (budget {SIDEBAR_LINK_BUDGET})")
        print(f"worst level-2 count: {max_sub} (budget {SIDEBAR_SUBLINK_BUDGET})")

    return 0 if max_links <= SIDEBAR_LINK_BUDGET and max_sub <= SIDEBAR_SUBLINK_BUDGET else 1


if __name__ == "__main__":
    sys.exit(main())
