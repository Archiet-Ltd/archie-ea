"""Continue with Google / Microsoft: the two-factor step still applies."""
import uuid

import pyotp
import pytest

from tests.support.social_sign_in import *  # noqa: F401,F403
from tests.support.social_sign_in import Browser
from tests.test_social_sign_in_cross_org_linking import _account, _connect, _google, _identities
from tests.test_team_invite_acceptance import PASSWORD


@pytest.fixture
def browser(app, social_config, db_session):
    return Browser(app, social_config)


def _enrolled(db_session):
    user = _account(db_session)
    user.mfa_secret = pyotp.random_base32()
    user.mfa_enabled = True
    db_session.flush()
    return user


def test_identity_is_connected_only_after_the_code(browser, db_session):
    """For an account with two-factor on, no identity row after the password step, one after the code."""
    user = _enrolled(db_session)
    browser.sign_in_with("google", _google(browser, user.email))
    browser.password_sign_in(user.email, PASSWORD)
    assert not browser.is_signed_in()
    assert _identities(user.id) == []
    done = browser.post("/account/mfa-challenge", data={"code": pyotp.TOTP(user.mfa_secret).now()})
    assert done.status_code == 302
    assert browser.signed_in_user_id() == user.id
    assert [r.provider for r in _identities(user.id)] == ["google"]


def test_linked_identity_ends_on_the_challenge(browser, db_session):
    """A linked identity for a two-factor user ends on the challenge, and signed-in pages wait for the code."""
    user = _enrolled(db_session)
    _connect(db_session, user, subject="g-mfa")
    response = browser.sign_in_with("google", _google(browser, user.email, "g-mfa"))
    assert response.headers["Location"].endswith("/account/mfa-challenge")
    assert not browser.is_signed_in()
    dashboard = browser.get("/dashboard/")
    assert dashboard.status_code == 302 and "/account/login" in dashboard.headers["Location"]
    browser.post("/account/mfa-challenge", data={"code": pyotp.TOTP(user.mfa_secret).now()})
    assert browser.signed_in_user_id() == user.id


def test_audit_row_names_the_provider(browser, db_session):
    """After the code that follows a Google sign-in, the login audit row's method is oauth:google."""
    from app.models.audit_log import AuditLog

    user = _enrolled(db_session)
    _connect(db_session, user, subject="g-audit")
    browser.sign_in_with("google", _google(browser, user.email, "g-audit"))
    browser.post("/account/mfa-challenge", data={"code": pyotp.TOTP(user.mfa_secret).now()})
    row = AuditLog.query.filter_by(user_id=user.id, action="login", table_name="auth").order_by(AuditLog.id.desc()).first()
    assert row is not None
    assert row.new_value["method"] == "oauth:google"


def test_password_sign_in_still_says_password(browser, db_session):
    """Other side: a password sign-in records the method password."""
    from app.models.audit_log import AuditLog

    user = _account(db_session)
    browser.password_sign_in(user.email, PASSWORD)
    row = AuditLog.query.filter_by(user_id=user.id, action="login", table_name="auth").order_by(AuditLog.id.desc()).first()
    assert row.new_value["method"] == "password"
