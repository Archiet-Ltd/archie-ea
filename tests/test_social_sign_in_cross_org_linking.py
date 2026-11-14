"""Continue with Google / Microsoft: connecting to an existing account, and keeping organisations apart (M-28 to M-37, M-40, M-42)."""
import time
import uuid

import pytest

from tests.support.fake_oidc_provider import PERSONAL_TENANT
from tests.support.social_sign_in import *  # noqa: F401,F403
from tests.support.social_sign_in import Browser
from tests.test_team_invite_acceptance import PASSWORD, _make_org, _make_user


@pytest.fixture
def browser(app, social_config, db_session):
    return Browser(app, social_config)


@pytest.fixture
def outbox(app, monkeypatch):
    from app.extensions import mail

    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", "no-reply@example.com")
    with mail.record_messages() as messages:
        yield messages


def _email():
    return "linking-%s@example.com" % uuid.uuid4().hex[:10]


def _account(db_session, label="a", **kw):
    org = _make_org(db_session, label)
    return _make_user(db_session, org, email=kw.pop("email", _email()), **kw)


def _google(browser, email, sub=None):
    return browser.providers["google"].google_claims(sub or "g-" + uuid.uuid4().hex, email)


def _identities(user_id):
    from app.models.sign_in_identity import SignInIdentity

    return SignInIdentity.query.filter_by(user_id=user_id).all()


def _snapshot(user):
    from app import db

    db.session.refresh(user)
    return (user.email, user.password_hash, user.organization_id, user.confirmed, user.sso_provider, user.external_id)


def _connect(db_session, user, provider="google", subject=None, email=None):
    from app.models.sign_in_identity import SignInIdentity
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    row = SignInIdentity(
        user_id=user.id, provider=provider, issuer="https://accounts.google.com", subject=subject or "g-" + uuid.uuid4().hex,
        email_at_link=email or user.email, created_at=now, last_used_at=now,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_m28_matching_email_signs_nobody_in_and_changes_nothing(browser, db_session):
    """M-28: a verified email matching a password account signs nobody in and leaves the user and identity rows unchanged."""
    user = _account(db_session)
    before = _snapshot(user)
    response = browser.sign_in_with("google", _google(browser, user.email))
    assert response.status_code == 302 and "/account/login" in response.headers["Location"]
    assert not browser.is_signed_in()
    assert _snapshot(user) == before
    assert _identities(user.id) == []
    page = browser.get("/account/login").get_data(as_text=True)
    assert "An Entelim account already uses %s. Sign in to it once to connect Google." % user.email in page
    assert 'value="%s"' % user.email in page


def test_m29_password_sign_in_in_the_same_browser_connects_and_tells_the_owner(browser, db_session, outbox):
    """M-29: signing in with the password in the same browser within 10 minutes connects Google and sends one notice."""
    user = _account(db_session)
    sub = "g-" + uuid.uuid4().hex
    browser.sign_in_with("google", _google(browser, user.email, sub))
    browser.password_sign_in(user.email, PASSWORD)
    assert browser.is_signed_in()
    rows = _identities(user.id)
    assert [(r.provider, r.subject, r.email_at_link) for r in rows] == [("google", sub, user.email)]
    notices = [m for m in outbox if user.email in m.recipients]
    assert len(notices) == 1
    assert "Google" in notices[0].body and "reset your password" in notices[0].body
    assert "oauth_pending_link" not in browser.session()


def test_m29_after_connecting_google_signs_straight_in(browser, db_session):
    """M-29 (next time): once connected, Continue with Google signs that person in with no password."""
    user = _account(db_session)
    claims = _google(browser, user.email)
    browser.sign_in_with("google", claims)
    browser.password_sign_in(user.email, PASSWORD)
    browser.get("/account/logout")
    assert not browser.is_signed_in()
    again = Browser(browser.app, browser.providers)
    again.sign_in_with("google", claims)
    assert again.signed_in_user_id() == user.id


def test_m30_different_browser_creates_no_row(browser, db_session):
    """M-30: a password sign-in from another browser connects nothing."""
    user = _account(db_session)
    browser.sign_in_with("google", _google(browser, user.email))
    elsewhere = Browser(browser.app, browser.providers)
    elsewhere.password_sign_in(user.email, PASSWORD)
    assert elsewhere.is_signed_in()
    assert _identities(user.id) == []


def test_m31_after_ten_minutes_creates_no_row(browser, db_session):
    """M-31: a password sign-in after the 10 minutes connects nothing."""
    user = _account(db_session)
    browser.sign_in_with("google", _google(browser, user.email))
    pending = dict(browser.session()["oauth_pending_link"])
    pending["expires_at"] = time.time() - 1
    browser.set_session(oauth_pending_link=pending)
    browser.password_sign_in(user.email, PASSWORD)
    assert browser.is_signed_in()
    assert _identities(user.id) == []


def test_m32_a_different_person_signing_in_connects_nothing(browser, db_session):
    """M-32: after a pending link for U, a sign-in by V in the same browser connects nothing for U or V."""
    user_u = _account(db_session, "u")
    user_v = _account(db_session, "v")
    browser.sign_in_with("google", _google(browser, user_u.email))
    browser.password_sign_in(user_v.email, PASSWORD)
    assert browser.signed_in_user_id() == user_v.id
    assert _identities(user_u.id) == [] and _identities(user_v.id) == []
    assert "oauth_pending_link" not in browser.session()


def test_m34_returning_identity_with_new_email_signs_the_same_user_in(browser, db_session):
    """M-34: a returning identity whose email claim changed signs in the same user and leaves the stored email alone."""
    user = _account(db_session)
    row = _connect(db_session, user, subject="g-returning")
    original = user.email
    browser.sign_in_with("google", _google(browser, "renamed-%s@example.com" % uuid.uuid4().hex[:6], "g-returning"))
    assert browser.signed_in_user_id() == user.id
    assert _snapshot(user)[0] == original
    assert row.last_used_at is not None


def test_m35_platform_administrator_is_never_signed_in_by_email(browser, db_session, outbox):
    """M-35: a platform administrator matched by email is not signed in, and nothing is stored or sent."""
    admin = _account(db_session)
    admin.is_platform_admin = True
    db_session.flush()
    browser.sign_in_with("google", _google(browser, admin.email))
    assert not browser.is_signed_in()
    assert _identities(admin.id) == []
    assert "oauth_pending_link" not in browser.session()
    assert outbox == []


def test_m35_platform_administrator_with_an_identity_row_is_refused(browser, db_session):
    """M-35 (identity row): a platform administrator with an existing identity row is not signed in either."""
    admin = _account(db_session)
    admin.is_platform_admin = True
    _connect(db_session, admin, subject="g-admin")
    browser.sign_in_with("google", _google(browser, admin.email, "g-admin"))
    assert not browser.is_signed_in()


def test_m36_unactivated_invitee_is_not_signed_in(browser, db_session):
    """M-36: a person with an invitation they have not taken up is not signed in and their row is unchanged."""
    from app.models.user import User

    org = _make_org(db_session, "invite")
    email = _email()
    invitee = User(first_name="In", last_name="Vited", email=email, confirmed=False, organization_id=org.id)
    db_session.add(invitee)
    db_session.flush()
    before = _snapshot(invitee)
    browser.sign_in_with("google", _google(browser, email))
    assert not browser.is_signed_in()
    assert _snapshot(invitee) == before
    assert _identities(invitee.id) == []
    assert "You have an invitation waiting" in browser.get("/account/login").get_data(as_text=True)


def test_m37_a_second_google_subject_is_not_linked(browser, db_session):
    """M-37: an account connected to Google subject S1 is not linked to S2, and the S1 row stays."""
    user = _account(db_session)
    first = _connect(db_session, user, subject="g-s1")
    browser.sign_in_with("google", _google(browser, user.email, "g-s2"))
    assert not browser.is_signed_in()
    rows = _identities(user.id)
    assert [(r.id, r.subject) for r in rows] == [(first.id, "g-s1")]
    assert "oauth_pending_link" not in browser.session()


def test_m40_signing_in_with_organisation_b_identity_stays_in_b(browser, db_session):
    """M-40: with identities in organisations A and B, B's identity gives a session in B, which cannot open A's records."""
    from app.models.application_portfolio import ApplicationComponent

    user_a = _account(db_session, "org-a")
    user_b = _account(db_session, "org-b")
    _connect(db_session, user_a, subject="g-a")
    _connect(db_session, user_b, subject="g-b")
    app_a = ApplicationComponent(name="Org A ledger", organization_id=user_a.organization_id, lifecycle_status="operational")
    app_b = ApplicationComponent(name="Org B billing", organization_id=user_b.organization_id, lifecycle_status="operational")
    db_session.add_all([app_a, app_b])
    db_session.commit()
    ids = {"user_b": user_b.id, "email_b": user_b.email, "app_a": app_a.id, "app_b": app_b.id}
    # A request is its own session in production; here one session serves the whole test, and an object it
    # already holds is handed back by id without asking the database, so start the request with none held.
    db_session.expunge_all()
    browser.sign_in_with("google", _google(browser, ids["email_b"], "g-b"))
    assert browser.signed_in_user_id() == ids["user_b"]
    db_session.expunge_all()
    assert browser.get("/applications/%d" % ids["app_a"]).status_code == 404
    assert browser.get("/applications/%d" % ids["app_b"]).status_code == 200


def test_m42_returning_identity_of_an_organisation_that_enabled_sso_is_refused(browser, db_session):
    """M-42: a returning identity whose organisation has since enabled its own SSO is refused."""
    from app.models.sso_config import SSOConfig

    user = _account(db_session)
    _connect(db_session, user, subject="g-sso")
    db_session.add(SSOConfig(
        organization_id=user.organization_id, protocol="oidc", enabled=True, client_id="x",
        idp_metadata_url="https://idp.example.com/.well-known/openid-configuration", email_domain="sso-%s.example.com" % uuid.uuid4().hex[:6],
    ))
    db_session.flush()
    browser.sign_in_with("google", _google(browser, user.email, "g-sso"))
    assert not browser.is_signed_in()
