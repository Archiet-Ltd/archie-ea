"""Launch cut (T-launch-02): the "not ready" switch no longer drops modules. The eight endpoints it
lists are on the All modules page with their recorded reason as a one-line limit, global search finds
them, the switch defaults off, and the sidebar still shows exactly the twelve launch modules."""

from __future__ import annotations

import html as html_lib
import re

import pytest

from tests.test_sidebar_render import _make_logged_in_client, _sidebar_html

pytestmark = pytest.mark.usefixtures("db_session")

_TEMPLATE_RE = re.compile(r"<template[ >].*?</template>", re.S)
_NAV_RE = re.compile(r"<nav\b.*?</nav>", re.S)
_HREF_RE = re.compile(r'<a\s[^>]*?href="([^"]+)"')


def _eight(app):
    from app.utils.role_access import NOT_READY_ENDPOINTS

    assert len(NOT_READY_ENDPOINTS) == 8
    return {e: r for e, r in NOT_READY_ENDPOINTS.items() if e in app.view_functions}


def _directory_page(client):
    resp = client.get("/modules/")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _assert_all_eight_listed(app, client):
    from flask import url_for

    html = _directory_page(client)
    eight = _eight(app)
    assert len(eight) == 8
    with app.test_request_context("/"):
        urls = {e: url_for(e) for e in eight}
    for endpoint, reason in eight.items():
        assert f'href="{urls[endpoint]}"' in html, endpoint
        assert f">{html_lib.escape(reason)}<" in html, (endpoint, reason)
    return html


def test_switch_defaults_off(app):
    assert app.config.get("HIDE_NOT_READY_MODULES") is False


def test_directory_lists_all_eight_with_their_reason_lines(app, db_session, make_org):
    client = _make_logged_in_client(app, db_session, make_org, "platform_admin", "unhide-a")
    with client:
        _assert_all_eight_listed(app, client)


def test_search_finds_a_not_ready_module(app, db_session, make_org):
    client = _make_logged_in_client(app, db_session, make_org, "platform_admin", "unhide-s")
    with client:
        for q, endpoint in (("Agentic Gaps", "main.agentic_gaps_ui"), ("Model Registry", "dynamic_dashboards.model_registry_index")):
            data = client.get("/api/sidebar/search", query_string={"q": q}).get_json()
            ids = {r["id"] for r in data["results"] if r["type"] == "module"}
            assert endpoint in ids, q


@pytest.mark.parametrize("role", ["enterprise_architect", "platform_admin"])
def test_sidebar_still_has_exactly_the_twelve(app, db_session, make_org, role):
    from tests.test_launch_sidebar import EXPECTED_ROUTES

    page = _sidebar_html(app, db_session, make_org, role, f"unhide-{role}"[:30])
    nav = _NAV_RE.search(_TEMPLATE_RE.sub("", page))
    hrefs = _HREF_RE.findall(nav.group(0))
    assert hrefs[:-1] == EXPECTED_ROUTES


def test_setting_the_key_true_no_longer_hides_listing(app, db_session, make_org):
    """HIDE_NOT_READY_MODULES=True is still a valid key but the directory and search ignore it for listing."""
    client = _make_logged_in_client(app, db_session, make_org, "platform_admin", "unhide-t")
    old = app.config.get("HIDE_NOT_READY_MODULES")
    app.config["HIDE_NOT_READY_MODULES"] = True
    try:
        with client:
            _assert_all_eight_listed(app, client)
            data = client.get("/api/sidebar/search", query_string={"q": "Agentic Gaps"}).get_json()
            assert "main.agentic_gaps_ui" in {r["id"] for r in data["results"] if r["type"] == "module"}
    finally:
        app.config["HIDE_NOT_READY_MODULES"] = old
