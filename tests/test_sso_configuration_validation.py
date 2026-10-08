"""Federation configuration must not save a login protocol it cannot execute.

Exercises the registered route and real form with a storage boundary double;
this is not a PostgreSQL persistence or external identity-provider test.
"""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import CSRFProtect
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader


def _configuration_app(monkeypatch, *, existing=True, csrf=False):
    from app import db
    from app.models import sso_config
    from app.modules.auth import sso_routes

    initial = dict(organization_id=7, protocol="oidc", enabled=True,
                   email_domain="original.example", client_id="original-client",
                   idp_metadata_url="https://idp.example/discovery",
                   client_secret="retained-secret", _client_secret_encrypted=None,
                   email_domains=["original.example"])
    state = SimpleNamespace(config=SimpleNamespace(**initial) if existing else None,
                            persisted=deepcopy(initial) if existing else None)

    class Query:
        def filter_by(self, **values):
            assert values == {"organization_id": 7}
            return self

        def first(self):
            return state.config

    class Config(SimpleNamespace):
        query = Query()

        def __init__(self, **values):
            super().__init__(**{**initial, **values})

    def commit():
        state.persisted = deepcopy(vars(state.config))

    monkeypatch.setattr(sso_config, "SSOConfig", Config)
    monkeypatch.setattr(db, "session", SimpleNamespace(
        add=lambda config: setattr(state, "config", config),
        commit=commit, rollback=lambda: None,
    ))

    class User(UserMixin):
        id = "sso-validation"
        organization_id = 7

        def can(self, permission):
            return True

    application = Flask(__name__)
    application.config.update(SECRET_KEY="isolated-sso-validation", TESTING=True,
                              WTF_CSRF_ENABLED=csrf)
    CSRFProtect(application)
    manager = LoginManager(application)
    manager.user_loader(lambda user_id: User())
    application.jinja_loader = ChoiceLoader([
        DictLoader({"layouts/admin_base.html":
                    '<!doctype html><main>{% block content %}{% endblock %}</main>'
                    '{% block extra_js %}{% endblock %}'}),
        FileSystemLoader(str(Path(__file__).resolve().parents[1] / "app/templates")),
    ])
    application.add_url_rule("/admin", "admin.index", lambda: "Admin")
    application.register_blueprint(sso_routes.sso_bp)

    @application.route("/test-entry")
    def entry():
        login_user(User())
        return '<a href="/admin/sso">SSO Configuration</a>'

    return application, state


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("protocol,enabled", [("bogus", "on"), ("", "")])
def test_rejected_protocol_preserves_configuration(monkeypatch, existing, protocol, enabled):
    application, state = _configuration_app(monkeypatch, existing=existing)
    before = deepcopy(state.persisted)
    client = application.test_client()
    client.get("/test-entry")
    response = client.post("/admin/sso", data={
        "protocol": protocol, "enabled": enabled, "email_domain": "replacement.example",
        "client_secret": "replacement-secret",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"SSO configuration saved." not in response.data
    assert b"not supported" in response.data
    assert state.persisted == before
    assert (vars(state.config) if state.config else None) == before


@pytest.mark.parametrize("existing", [False, True])
def test_rejected_saml_enabled_without_idp_fields_preserves_configuration(monkeypatch, existing):
    """SAML can now be enabled (R1-B12 PR 2), but only once the IdP SSO URL
    and signing certificate it would verify assertions against are present
    -- there is no protocol to run without them, so this is still a
    rejection, with a different, field-specific message than the generic
    "protocol not supported" case above."""
    application, state = _configuration_app(monkeypatch, existing=existing)
    before = deepcopy(state.persisted)
    client = application.test_client()
    client.get("/test-entry")
    response = client.post("/admin/sso", data={
        "protocol": "saml", "enabled": "on", "email_domain": "replacement.example",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"SSO configuration saved." not in response.data
    assert b"needs an IdP SSO URL and signing certificate" in response.data
    assert state.persisted == before
    assert (vars(state.config) if state.config else None) == before


@pytest.mark.parametrize("protocol,enabled", [("oidc", "on"), ("saml", "")])
def test_supported_oidc_and_disabled_saml_draft_remain_saveable(monkeypatch, protocol, enabled):
    application, state = _configuration_app(monkeypatch)
    client = application.test_client()
    client.get("/test-entry")
    response = client.post("/admin/sso", data={
        "protocol": protocol, "enabled": enabled, "email_domain": "updated.example",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"SSO configuration saved." in response.data
    assert state.persisted["protocol"] == protocol
    assert state.persisted["enabled"] == (enabled == "on")
    assert state.persisted["email_domain"] == "updated.example"
    assert state.persisted["client_secret"] == "retained-secret"


def test_saml_with_idp_fields_can_be_enabled_and_saved(monkeypatch):
    """The positive case this PR adds: SAML with its required IdP fields
    present saves and enables cleanly, the same as OIDC always could."""
    application, state = _configuration_app(monkeypatch)
    client = application.test_client()
    client.get("/test-entry")
    response = client.post("/admin/sso", data={
        "protocol": "saml", "enabled": "on", "email_domain": "updated.example",
        "idp_sso_url": "https://idp.example.com/saml/sso",
        "idp_x509_cert": "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"SSO configuration saved." in response.data
    assert state.persisted["protocol"] == "saml"
    assert state.persisted["enabled"] is True
    assert state.persisted["idp_sso_url"] == "https://idp.example.com/saml/sso"


def test_browser_rejects_enabled_saml_without_idp_fields_and_reloads_prior_oidc_settings(monkeypatch):
    """SAML can be enabled now (R1-B12 PR 2), but still not without the IdP
    SSO URL and certificate it would verify assertions against -- this
    browser-level test is updated to fill neither, which still rejects, with
    the field-specific message rather than the old blanket "coming soon" one."""
    from threading import Thread
    from playwright.sync_api import sync_playwright, expect
    from werkzeug.serving import make_server

    application, state = _configuration_app(monkeypatch, csrf=True)
    before = deepcopy(state.persisted)
    server = make_server("127.0.0.1", 0, application, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/test-entry")
                page.get_by_role("link", name="SSO Configuration").click()
                page.get_by_label("Protocol", exact=True).select_option("saml")
                page.get_by_label("Email Domain(s)", exact=True).fill("replacement.example")
                page.get_by_label("Enable SSO for this organisation").check()
                with page.expect_navigation() as navigation:
                    page.get_by_role("button", name="Save Configuration").click()
                assert navigation.value.status == 200
                expect(page.get_by_text(
                    "SAML SSO needs an IdP SSO URL and signing certificate before it can be enabled.",
                    exact=True,
                )).to_be_visible()
                expect(page.get_by_text("SSO configuration saved.", exact=True)).to_have_count(0)
                page.reload()
                expect(page.get_by_label("Protocol", exact=True)).to_have_value("oidc")
                expect(page.get_by_label("Email Domain(s)", exact=True)).to_have_value("original.example")
                expect(page.get_by_label("Enable SSO for this organisation")).to_be_checked()
                assert state.persisted == before
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
