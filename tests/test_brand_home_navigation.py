"""Rendered sidebar click regression, with explicit app-shell boundaries.

Real sidebar Jinja, role-zone selection and ordinary Chromium navigation run
against a loopback Flask harness. Users and destination handlers are synthetic;
this is not real login, authorization, dashboard content, database or mobile
shell qualification. Full-app role coverage lives in tests/smoke/.
"""

from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import flask
import pytest
from flask import Flask, render_template_string
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def brand_browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture(params=["solution_architect", "platform_admin"])
def brand_shell(request):
    """Navigation v3: the sidebar renders the navigation registry. The harness registers a
    fixture destination for every registry endpoint and hands the sidebar the same
    ``navigation()`` the application does."""
    from app.navigation import registry

    role = request.param
    administrator = role == "platform_admin"
    user = SimpleNamespace(
        id=0, organization_id=None,
        enterprise_role=role, is_authenticated=True, is_platform_admin=False,
        is_org_admin=administrator, is_admin=lambda: administrator, can=lambda _p: administrator,
        first_name="Synthetic", full_name=lambda: "Synthetic fixture", email="fixture@example.invalid",
    )
    shell = Flask("brand_shell", template_folder=str(ROOT / "app/templates"), static_folder=str(ROOT / "app/static"))
    shell.config["APP_NAME"] = "Entelim"
    shell.add_url_rule("/dashboard/overview", "dashboard.overview", lambda: "<h1>Dashboard</h1>")
    shell.add_url_rule("/admin/", "admin.index", lambda: (
        ("<h1>Command Center</h1>", 200) if administrator else ("Forbidden", 403)))
    shell.add_url_rule("/account/logout", "account.logout", lambda: "Signed out")
    shell.add_url_rule("/account/switch-organization", "account.switch_organization", lambda: "", methods=["POST"])
    links = list(registry.ALL_ENTRIES) + [c for link in registry.MAIN for c in link.children]
    links += [link.fallback for link in registry.ALL_ENTRIES if link.fallback is not None]
    for link in links:
        if link.endpoint not in shell.view_functions:
            rule = "/fixture/" + link.endpoint + "".join("/<%s>" % k for k, _ in link.url_values)
            shell.add_url_rule(rule, link.endpoint, lambda **_kw: "Fixture destination")
    # The admin routes' own guards, as the registry asks them (no database here).
    import app.utils.role_access as role_access

    def navigation(_user=None):
        nav = registry.sidebar_for(user, flask.request.endpoint)
        nav["connected"], nav["connect_url"] = True, None
        return nav

    @shell.get("/fixture/start")
    def start():
        # No full shell/CSS/Alpine: the production anchors and role filtering are
        # under test, not drawer state or a reimplementation of authentication.
        return render_template_string(
            '<!doctype html><html><body>{% include "components/admin_sidebar.html" %}</body></html>',
            current_user=user, flask=flask, sidebar_navigation=navigation, csrf_token=lambda: "fixture",
            organization_memberships=[], active_organization=None, active_organization_name="Synthetic org",
        )

    original = role_access.link_requires_satisfied
    role_access.link_requires_satisfied = lambda u, requires: requires is None or (
        administrator and requires in ("admin", "org_or_platform_admin"))
    server = make_server("127.0.0.1", 0, shell, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", administrator
    finally:
        role_access.link_requires_satisfied = original
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_home_returns_to_the_shared_home_and_settings_stays_for_administrators(brand_browser, brand_shell):
    base, administrator = brand_shell
    page = brand_browser.new_page()
    try:
        assert page.goto(base + "/fixture/start").status == 200
        sidebar = page.get_by_test_id("sidebar")
        settings = sidebar.locator('[data-testid="user-menu-settings"]')
        expect(settings).to_have_count(1 if administrator else 0)
        if administrator:
            expect(settings).to_have_attribute("href", "/admin/")
        home = sidebar.get_by_role("link", name="Home", exact=True)
        with page.expect_navigation() as destination:
            home.click()
        assert destination.value.status == 200
        expect(page).to_have_url(base + "/dashboard/overview")
        expect(page.get_by_role("heading", name="Dashboard", exact=True)).to_be_visible()
    finally:
        page.close()
