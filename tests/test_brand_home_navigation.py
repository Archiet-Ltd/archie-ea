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

from app.utils.role_access import get_sidebar_zones

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def brand_browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture(params=["solution_architect", "platform_admin"])
def brand_shell(request):
    role = request.param
    administrator = role == "platform_admin"
    user = SimpleNamespace(
        enterprise_role=role, is_authenticated=True, is_platform_admin=administrator,
        is_org_admin=administrator, is_admin=lambda: administrator,
        first_name="Synthetic", full_name=lambda: "Synthetic fixture", email="fixture@example.invalid",
    )
    shell = Flask("brand_shell", template_folder=str(ROOT / "app/templates"))
    shell.config["APP_NAME"] = "Entelim"
    shell.add_url_rule("/dashboard/overview", "dashboard.overview", lambda: "<h1>Dashboard</h1>")
    shell.add_url_rule("/admin/", "admin.index", lambda: (
        ("<h1>Command Center</h1>", 200) if administrator else ("Forbidden", 403)))
    shell.add_url_rule("/account/logout", "account.logout", lambda: "Signed out")
    for zone in get_sidebar_zones(user):
        for link in zone["links"]:
            if link["endpoint"] not in shell.view_functions:
                shell.add_url_rule("/fixture/" + link["endpoint"], link["endpoint"], lambda: "Fixture destination")

    @shell.get("/fixture/start")
    def start():
        # No full shell/CSS/Alpine: the production anchor and role filtering are
        # under test, not drawer state or a reimplementation of authentication.
        return render_template_string(
            '<!doctype html><html><body>{% include "components/admin_sidebar.html" %}</body></html>',
            current_user=user, flask=flask, get_sidebar_zones=get_sidebar_zones,
        )

    server = make_server("127.0.0.1", 0, shell, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", administrator
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_brand_click_returns_to_shared_home_without_removing_admin_navigation(brand_browser, brand_shell):
    base, administrator = brand_shell
    page = brand_browser.new_page()
    try:
        assert page.goto(base + "/fixture/start").status == 200
        sidebar = page.get_by_test_id("sidebar")
        command_center = sidebar.get_by_role("link", name="Command Center", exact=True)
        expect(command_center).to_have_count(1 if administrator else 0)
        if administrator:
            expect(command_center).to_have_attribute("href", "/admin/")
        brand = sidebar.get_by_role("link", name="Entelim", exact=True)
        with page.expect_navigation() as destination:
            brand.click()
        assert destination.value.status == 200
        expect(page).to_have_url(base + "/dashboard/overview")
        expect(page.get_by_role("heading", name="Dashboard", exact=True)).to_be_visible()
    finally:
        page.close()
