"""Both administration implementations enforce the same permission boundary."""

import importlib
from unittest.mock import Mock

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user


MODULES = [
    ("app.modules.admin.routes.deprecation_routes", "deprecation_bp"),
    ("app.modules.admin.v2.routes.deprecation_routes", "deprecation_bp_v2"),
]


def _test_app(monkeypatch, module_name, blueprint_name, administrator=False, admin_of=1):
    module = importlib.import_module(module_name)
    app = Flask(__name__)
    app.secret_key = "isolated-deprecation-test"
    login = LoginManager(app)

    # admin_required also asks rbac_service.is_org_admin about the ACTIVE
    # organisation (g.current_org_id). No database here, so OrgRole.get_role
    # is the storage boundary double: an administrator holds org_admin in
    # organisation ``admin_of``; the active organisation is 1.
    from flask import g
    from app.models.org_role import OrgRole
    monkeypatch.setattr(
        OrgRole, "get_role",
        classmethod(lambda cls, org_id, user_id:
                    "org_admin" if administrator and org_id == admin_of else None),
    )

    @app.before_request
    def _active_organisation():
        g.current_org_id = 1

    class User(UserMixin):
        id = "monitor-test"
        organization_id = 1
        # Monitoring spans every tenant, so these routes also sit behind
        # platform_admin_required (flag AND ADMINISTER). An admin of another
        # organisation is never a platform admin.
        is_platform_admin = administrator and admin_of == 1

        def can(self, permission):
            return administrator

        def is_admin(self):
            return False

    login.user_loader(lambda user_id: User())
    app.register_blueprint(getattr(module, blueprint_name))
    metrics = Mock()
    monkeypatch.setattr(module, "get_deprecation_metrics", metrics)
    monkeypatch.setattr(module, "render_template", lambda *args, **kwargs: "Monitoring")

    @app.route("/test-entry")
    def entry():
        login_user(User())
        return '<a href="/admin/deprecation/">Monitoring</a>'

    return app, metrics


@pytest.mark.parametrize("module_name,blueprint_name", MODULES)
@pytest.mark.parametrize("path", ["/", "/api/stats", "/api/alerts", "/api/velocity", "/api/export", "/api/webhook"])
def test_non_admin_is_denied_before_monitoring_handlers(monkeypatch, module_name, blueprint_name, path):
    app, metrics = _test_app(monkeypatch, module_name, blueprint_name)
    client = app.test_client()
    client.get("/test-entry")
    response = client.open("/admin/deprecation" + path,
                           method="POST" if path == "/api/webhook" else "GET")
    assert response.status_code == 403
    metrics.assert_not_called()


@pytest.mark.parametrize("module_name,blueprint_name", MODULES)
def test_admin_of_a_different_organisation_is_denied(monkeypatch, module_name, blueprint_name):
    """Active-org property: ADMINISTER plus org_admin of organisation 2 must not
    open organisation 1's monitoring while organisation 1 is the active one."""
    app, metrics = _test_app(monkeypatch, module_name, blueprint_name, administrator=True, admin_of=2)
    client = app.test_client()
    client.get("/test-entry")
    assert client.get("/admin/deprecation/").status_code == 403
    metrics.assert_not_called()


@pytest.mark.parametrize("module_name,blueprint_name", MODULES)
@pytest.mark.parametrize("administrator", [False, True])
def test_browser_monitoring_navigation_permissions(monkeypatch, module_name, blueprint_name, administrator):
    from threading import Thread
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    app, _ = _test_app(monkeypatch, module_name, blueprint_name, administrator)
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
                    page.get_by_role("link", name="Monitoring", exact=True).click()
                assert navigation.value.status == (200 if administrator else 403)
                assert page.locator("body").inner_text().strip().startswith(
                    "Monitoring" if administrator else "Forbidden"
                )
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
