"""Continue with Google / Microsoft: what is stored, disconnecting, and the old route pair."""
import importlib
import importlib.util
import logging
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.support.social_sign_in import *  # noqa: F401,F403
from tests.support.social_sign_in import Browser
from tests.test_social_sign_in_cross_org_linking import _account, _connect, _google, _identities
from tests.test_team_invite_acceptance import PASSWORD

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "migrations" / "versions" / "20261114_sign_in_identities.py"


@pytest.fixture
def browser(app, social_config, db_session):
    return Browser(app, social_config)


def test_same_provider_and_subject_twice_is_refused(db_session):
    """A second row with the same provider and subject raises an integrity error."""
    one = _account(db_session, "one")
    two = _account(db_session, "two")
    _connect(db_session, one, subject="g-same")
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _connect(db_session, two, subject="g-same")


def test_same_user_and_provider_twice_is_refused(db_session):
    """A second row with the same user and provider raises an integrity error."""
    user = _account(db_session)
    _connect(db_session, user, subject="g-first")
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _connect(db_session, user, subject="g-second")
    _connect(db_session, user, provider="microsoft", subject="m-first")


def test_no_token_is_stored_or_logged(browser, db_session, caplog):
    """The access token and id_token strings appear in no users or sign_in_identities column and in no log line."""
    caplog.set_level(logging.DEBUG)
    email = "tokens-%s@example.com" % uuid.uuid4().hex[:8]
    browser.sign_in_with("google", _google(browser, email))
    assert browser.is_signed_in()
    tokens = browser.providers["google"].token_strings
    assert len(tokens) == 2 and all(len(t) > 20 for t in tokens)
    for table in ("users", "sign_in_identities"):
        for row in db_session.execute(text("SELECT t::text FROM %s t" % table)):
            for token in tokens:
                assert token not in row[0], table
    for record in caplog.records:
        line = record.getMessage() + " " + str(record.args or "") + " " + (record.exc_text or "")
        for token in tokens:
            assert token not in line
    assert "oauth_flow" not in browser.session()


def test_migration_cycles_and_there_is_one_head(app):
    """The revision upgrades, downgrades and upgrades again, and the chain has one head."""
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect

    from app import db

    config = Config()
    config.set_main_option("script_location", str(REPO / "migrations"))
    assert len(ScriptDirectory.from_config(config).get_heads()) == 1

    spec = importlib.util.spec_from_file_location("sign_in_identities_revision", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def run(step):
        with app.app_context(), db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                step()

    def present():
        with app.app_context():
            return "sign_in_identities" in inspect(db.engine).get_table_names()

    run(module.downgrade)
    assert not present()
    run(module.upgrade)
    assert present()
    run(module.downgrade)
    assert not present()
    run(module.upgrade)
    assert present()
    with app.app_context():
        columns = {c["name"] for c in inspect(db.engine).get_columns("sign_in_identities")}
    assert {"user_id", "provider", "issuer", "subject", "email_at_link", "created_at", "last_used_at"} <= columns


def test_disconnect_removes_the_row_and_the_next_sign_in_asks_for_the_password(browser, db_session):
    """A person with a password disconnects Google; the next Google sign-in for that subject goes to the connect step."""
    user = _account(db_session)
    claims = _google(browser, user.email, "g-gone")
    _connect(db_session, user, subject="g-gone")
    browser.password_sign_in(user.email, PASSWORD)
    assert browser.is_signed_in()
    result = browser.post("/account/oauth/google/disconnect")
    assert result.status_code == 302
    assert _identities(user.id) == []
    other = Browser(browser.app, browser.providers)
    response = other.sign_in_with("google", claims)
    assert not other.is_signed_in()
    assert "/account/login" in response.headers["Location"]
    assert "oauth_pending_link" in other.session()


def test_google_only_person_cannot_disconnect(browser, db_session):
    """A person with Google only (no password, no other provider) is refused and the row remains."""
    from app.models.user import User

    email = "only-%s@example.com" % uuid.uuid4().hex[:8]
    browser.sign_in_with("google", _google(browser, email, "g-only"))
    user = User.find_by_email(email)
    assert browser.signed_in_user_id() == user.id
    result = browser.post("/account/oauth/google/disconnect", follow_redirects=True)
    assert [r.provider for r in _identities(user.id)] == ["google"]
    assert "Set a password first: use Forgot password on the sign-in page." in result.get_data(as_text=True)


def test_second_provider_lets_google_be_disconnected(browser, db_session):
    """Other side: with Microsoft also connected and no password, Google can be disconnected."""
    from app.models.user import User

    email = "both-%s@example.com" % uuid.uuid4().hex[:8]
    browser.sign_in_with("google", _google(browser, email, "g-both"))
    user = User.find_by_email(email)
    _connect(db_session, user, provider="microsoft", subject="tid:oid")
    browser.post("/account/oauth/google/disconnect")
    assert [r.provider for r in _identities(user.id)] == ["microsoft"]


def test_sign_in_methods_card_lists_the_connected_provider(browser, db_session):
    """Settings card: the account page lists each connected provider with its address and a Disconnect button."""
    user = _account(db_session)
    _connect(db_session, user, subject="g-card", email=user.email)
    browser.password_sign_in(user.email, PASSWORD)
    page = browser.get("/account/manage").get_data(as_text=True)
    assert "Sign-in methods" in page
    assert "Google" in page and user.email in page
    assert "/account/oauth/google/disconnect" in page


def test_sign_in_methods_card_empty_state(browser, db_session):
    """Settings card (empty): says why nothing is listed and what to do next."""
    user = _account(db_session)
    browser.password_sign_in(user.email, PASSWORD)
    page = browser.get("/account/manage").get_data(as_text=True)
    assert "No Google or Microsoft sign-in is connected" in page
    assert "we connect it after you confirm your password" in page


def test_the_old_platform_route_pair_is_gone(app, browser):
    """/account/sso/azure is 404 and neither account blueprint registers a /sso/ route."""
    from flask import Flask

    assert browser.get("/account/sso/azure").status_code == 404
    assert browser.get("/account/sso/okta").status_code == 404
    for module_path in ("app.modules.account", "app.modules.account.v2"):
        probe = Flask(__name__)
        module = importlib.import_module(module_path)
        module.register(probe)
        rules = [r.rule for r in probe.url_map.iter_rules() if "/sso" in r.rule]
        assert rules == [], (module_path, rules)


PROVIDER_SETTINGS = (
    "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
    "MICROSOFT_OAUTH_CLIENT_ID", "MICROSOFT_OAUTH_CLIENT_SECRET",
)


def _switch_off(app, monkeypatch, *names):
    for name in names:
        monkeypatch.setitem(app.config, name, "")


def test_with_no_client_settings_the_sign_up_and_sign_in_pages_offer_no_provider(app, browser, monkeypatch):
    """No provider button, no terms line for it and no divider appears on either page."""
    _switch_off(app, monkeypatch, *PROVIDER_SETTINGS)
    for path in ("/account/register", "/account/login"):
        page = browser.get(path)
        html = page.get_data(as_text=True)
        assert page.status_code == 200, path
        for absent in ("Continue with", "social-sign-in", "By continuing, you agree"):
            assert absent not in html, (path, absent)


def test_with_no_client_settings_the_sign_in_methods_card_is_not_drawn(app, browser, db_session, monkeypatch):
    """A person who had connected Google still sees no card while the provider is switched off."""
    user = _account(db_session)
    _connect(db_session, user, subject="g-off", email=user.email)
    browser.password_sign_in(user.email, PASSWORD)
    _switch_off(app, monkeypatch, *PROVIDER_SETTINGS)
    page = browser.get("/account/manage")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Sign-in methods" not in html and "sign-in-methods-card" not in html
    assert "/account/oauth/google/disconnect" not in html


def test_the_card_lists_only_the_provider_that_is_switched_on(app, browser, db_session, monkeypatch):
    """With only Google configured, a Microsoft connection is not offered for removal."""
    user = _account(db_session)
    _connect(db_session, user, subject="g-on", email=user.email)
    _connect(db_session, user, provider="microsoft", subject="tid:oid")
    browser.password_sign_in(user.email, PASSWORD)
    _switch_off(app, monkeypatch, "MICROSOFT_OAUTH_CLIENT_ID", "MICROSOFT_OAUTH_CLIENT_SECRET")
    html = browser.get("/account/manage").get_data(as_text=True)
    assert "/account/oauth/google/disconnect" in html
    assert "/account/oauth/microsoft/disconnect" not in html


@pytest.mark.parametrize("key", ["google", "microsoft"])
def test_with_no_client_settings_every_provider_route_answers_a_plain_not_found(app, browser, db_session, monkeypatch, key):
    """Start, return and disconnect each answer 404 with no server error, and a signed-in person is not changed."""
    user = _account(db_session)
    _connect(db_session, user, provider=key, subject="s-off")
    browser.password_sign_in(user.email, PASSWORD)
    _switch_off(app, monkeypatch, *PROVIDER_SETTINGS)
    assert browser.start(key).status_code == 404
    assert browser.get("/account/oauth/%s/callback?code=x&state=y" % key).status_code == 404
    assert browser.post("/account/oauth/%s/disconnect" % key).status_code == 404
    assert [r.provider for r in _identities(user.id)] == [key]
