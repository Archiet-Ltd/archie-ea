"""Launch cut: the sidebar lists exactly twelve modules for every role, and every
other module is listed on the /modules/ page under "Not in the launch cut".
Nothing is removed: the sidebar and the page together cover every module the
role data names."""

from __future__ import annotations

import re

import pytest

from app.utils.role_access import (
    LAUNCH_ENDPOINTS,
    LAUNCH_MODULES,
    SIDEBAR_LINK_BUDGET,
    SIDEBAR_ZONES,
    get_launch_sidebar_zones,
)
from tests.test_sidebar_render import _make_logged_in_client, _sidebar_html

EXPECTED_ROUTES = [
    "/dashboard/overview",
    "/applications/",
    "/capability-map/",
    "/intelligence/ask",
    "/strategic/impact-analysis",
    "/architecture/traceability",
    "/enterprise/implementation/gap-analysis",
    "/enterprise/implementation/work-packages",
    "/capability-roadmap",
    "/applications/vendors",
    "/risks/",
    "/architecture/import/oef",
]
EXPECTED_LABELS = [
    "Dashboard Overview", "Applications", "Capability Map", "Ask a question",
    "Impact Analysis", "Traceability Matrix", "Gap Analysis", "Work Packages",
    "Roadmaps", "Vendors", "Risk Register", "Import",
]

_TEMPLATE_RE = re.compile(r"<template[ >].*?</template>", re.S)
_NAV_RE = re.compile(r"<nav\b.*?</nav>", re.S)
_HREF_RE = re.compile(r'<a\s[^>]*?href="([^"]+)"')


def test_launch_module_list_is_the_twelve_in_order():
    assert [m["label"] for m in LAUNCH_MODULES] == EXPECTED_LABELS
    assert len(LAUNCH_ENDPOINTS) == 12
    # Grouped sidebar (T-nav-01): the twelve are still the launch modules; they sit in five groups.
    assert [z["zone"] for z in get_launch_sidebar_zones(None)] == [
        "overview", "answers", "model", "change", "catalogue"
    ]
    # 21 level-1 entries + logo + All modules + logout; the sub-menus are capped separately (35).
    assert SIDEBAR_LINK_BUDGET == 24


def test_every_demoted_module_keeps_its_role_data_entry():
    """Nothing is deleted: role data still names more than the twelve."""
    endpoints = {m["endpoint"] for zones in SIDEBAR_ZONES.values() for z in zones for m in z["links"]}
    # Import is the one launch module that had no sidebar entry before the cut.
    assert LAUNCH_ENDPOINTS - {"architect_ui.import_oef"} <= endpoints
    assert len(endpoints - LAUNCH_ENDPOINTS) > 30


@pytest.mark.parametrize("role", sorted(SIDEBAR_ZONES))
def test_sidebar_lists_all_twelve_routes_and_all_modules_in_the_footer(app, db_session, make_org, role):
    """The twelve launch routes are all still in the sidebar (some now as sub-menu items), and the
    way to the All modules page is the footer link. The full tree is pinned in test_grouped_sidebar.py."""
    html = _TEMPLATE_RE.sub("", _sidebar_html(app, db_session, make_org, role, f"launch-{role}"[:30]))
    nav = _NAV_RE.search(html)
    assert nav, "sidebar nav not found"
    hrefs = _HREF_RE.findall(nav.group(0))
    missing = [route for route in EXPECTED_ROUTES if route not in hrefs]
    assert not missing, f"{role}: {missing}"
    assert 'href="/modules/"' in html[html.index("</nav>"):]


@pytest.mark.parametrize("role", ["enterprise_architect", "platform_admin", "procurement"])
def test_more_tools_page_lists_every_demoted_module(app, db_session, make_org, role):
    from app.modules.modules_directory.routes import _resolve, visible_module_links

    client = _make_logged_in_client(app, db_session, make_org, role, f"mt-{role}"[:20])
    with client:
        resp = client.get("/modules/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        before = _resolve(visible_module_links())
    demoted = [m for m in before if m["endpoint"] not in LAUNCH_ENDPOINTS]
    assert "Not in the launch cut" in html
    assert html.count('data-testid="not-in-launch-cut-badge"') == len(demoted)
    for link in demoted:
        assert link["href"].startswith("/")
        assert f'href="{link["href"]}"' in html, link["endpoint"]
    # Nothing lost: the twelve in the sidebar plus the page entries are every module there was.
    launch_seen = [m for m in before if m["endpoint"] in LAUNCH_ENDPOINTS]
    assert len(launch_seen) + len(demoted) == len(before)
    # Composer and Tech Radar go first.
    body = html[html.index('data-testid="modules-directory"'):]
    page_hrefs = [h.replace("&amp;", "&") for h in _HREF_RE.findall(body)]
    by_endpoint = {m["endpoint"]: m["href"] for m in before}
    assert page_hrefs[:2] == [by_endpoint["archimate.composer_page"], by_endpoint["tech_radar.index"]]
