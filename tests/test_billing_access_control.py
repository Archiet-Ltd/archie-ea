"""Billing requests must enforce administrator permission before service calls."""

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user


def _billing_test_app(monkeypatch, administrator, *, admin_of=1, home_admin=None):
    from app.modules.admin import billing_routes

    # admin_required also asks rbac_service.is_org_admin about the ACTIVE
    # organisation (g.current_org_id). No database here, so the OrgRole
    # lookup is the storage boundary double: an administrator is an
    # org_admin of organisation 1 (the active one), a non-administrator
    # has no grant.
    from flask import g
    from app.models.org_role import OrgRole
    monkeypatch.setattr(
        OrgRole, "get_role",
        classmethod(lambda cls, org_id, user_id:
                    "org_admin" if administrator and org_id == admin_of else None),
    )

    class User(UserMixin):
        id = "billing-test"
        organization = None
        organization_id = 1

        def can(self, permission):
            return administrator

        def is_admin(self):
            return administrator if home_admin is None else home_admin

    app = Flask(__name__)
    app.secret_key = "isolated-billing-test"

    @app.before_request
    def _active_organisation():
        g.current_org_id = 1

    login = LoginManager(app)
    login.user_loader(lambda user_id: User())
    app.register_blueprint(billing_routes.billing_bp, url_prefix="/admin/billing")
    monkeypatch.setattr(billing_routes, "render_template", lambda *args, **kwargs: "Billing")

    @app.route("/test-entry")
    def test_entry():
        login_user(User())
        return '<a href="/admin/billing/">Billing</a>'

    return app


@pytest.mark.parametrize("method,path,admin_status", [
    ("GET", "/admin/billing/", 200),
    ("POST", "/admin/billing/upgrade", 400),
    ("GET", "/admin/billing/portal", 302),
])
@pytest.mark.parametrize("administrator", [False, True])
def test_billing_requires_administrator(monkeypatch, method, path, admin_status, administrator):
    app = _billing_test_app(monkeypatch, administrator)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "billing-test"
        session["_fresh"] = True
    response = client.open(path, method=method)
    assert response.status_code == (admin_status if administrator else 403)


@pytest.mark.parametrize("method,path", [
    ("GET", "/admin/billing/"), ("POST", "/admin/billing/upgrade"), ("GET", "/admin/billing/portal"),
])
def test_admin_of_a_different_organisation_is_refused(monkeypatch, method, path):
    """Active-org property: holding ADMINISTER and being org_admin of organisation 2
    must not open organisation 1's billing while organisation 1 is the active one."""
    app = _billing_test_app(monkeypatch, True, admin_of=2, home_admin=False)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "billing-test"
        session["_fresh"] = True
    assert client.open(path, method=method).status_code == 403


@pytest.mark.parametrize("administrator", [False, True])
def test_browser_billing_navigation_enforces_permission(monkeypatch, administrator):
    from threading import Thread
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    app = _billing_test_app(monkeypatch, administrator)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/test-entry")
                with page.expect_navigation() as navigation:
                    page.get_by_role("link", name="Billing", exact=True).click()
                assert navigation.value.status == (200 if administrator else 403)
                assert page.locator("body").inner_text().strip().startswith(
                    "Billing" if administrator else "Forbidden"
                )
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
