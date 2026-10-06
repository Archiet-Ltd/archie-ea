"""The password login route gates an administrator on MFA (R1-B12 PR 2,
TB-0144/PB-0100): /account/login never mints a real session for an
administrator until a TOTP code is verified, whether that is a first-time
enrolment or an already-enrolled sign-in.
"""

from __future__ import annotations

import uuid

import pyotp
import pytest

pytestmark = pytest.mark.usefixtures("db_session")

_PASSWORD = "Str0ng!Passw0rd"


def _make_admin(db_session, org, *, mfa_enabled=False, mfa_secret=None):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"mfa-login-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=role,
    )
    user.password = _PASSWORD
    user.mfa_enabled = mfa_enabled
    user.mfa_secret = mfa_secret
    db_session.add(user)
    db_session.commit()
    return user


def _make_plain_user(db_session, org):
    from app.models.user import User

    user = User(
        email=f"plain-login-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True,
    )
    user.password = _PASSWORD
    db_session.add(user)
    db_session.commit()
    return user


def test_a_plain_user_logs_in_without_any_mfa_step(app, db_session, make_org):
    org = make_org("mfa-gate-plain")
    user = _make_plain_user(db_session, org)

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": user.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" not in resp.headers.get("Location", "")


def test_an_unenrolled_administrator_is_sent_to_enrol_not_logged_in(app, db_session, make_org):
    org = make_org("mfa-gate-enrol")
    admin = _make_admin(db_session, org, mfa_enabled=False)

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" in resp.headers.get("Location", "")

    challenge = client.get("/account/mfa-challenge")
    assert challenge.status_code == 200
    assert b"Set up two-factor authentication" in challenge.data or b"setup key" in challenge.data.lower()


def test_an_unenrolled_administrator_cannot_reach_dashboard_without_mfa(app, db_session, make_org):
    org = make_org("mfa-gate-noaccess")
    admin = _make_admin(db_session, org, mfa_enabled=False)

    client = app.test_client()
    client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    resp = client.get("/dashboard/overview")
    # Not authenticated yet -- MFA was never completed.
    assert resp.status_code in (302, 401)


def test_enrolling_with_the_right_code_completes_login(app, db_session, make_org):
    org = make_org("mfa-gate-enrol-complete")
    admin = _make_admin(db_session, org, mfa_enabled=False)

    client = app.test_client()
    client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    # GET the challenge page first -- that's where a fresh secret is
    # generated and stashed in the session (mirroring a real authenticator
    # app scanning the page's QR code / setup key before producing a code).
    client.get("/account/mfa-challenge")
    with client.session_transaction() as sess:
        secret = sess["_mfa_enroll_secret"]
    code = pyotp.TOTP(secret).now()

    resp = client.post("/account/mfa-challenge", data={"code": code}, follow_redirects=False)
    assert resp.status_code in (302, 303)

    from app.models.user import User

    db_session.refresh(admin)
    reloaded = db_session.get(User, admin.id)
    assert reloaded.mfa_enabled is True
    assert reloaded.mfa_secret == secret

    dash = client.get("/dashboard/overview")
    assert dash.status_code == 200


def test_enrolling_with_the_wrong_code_does_not_complete_login(app, db_session, make_org):
    org = make_org("mfa-gate-enrol-wrong")
    admin = _make_admin(db_session, org, mfa_enabled=False)

    client = app.test_client()
    client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )

    resp = client.post("/account/mfa-challenge", data={"code": "000000"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"did not match" in resp.data

    dash = client.get("/dashboard/overview")
    assert dash.status_code in (302, 401)


def test_an_already_enrolled_administrator_must_enter_a_valid_code(app, db_session, make_org):
    secret = pyotp.random_base32()
    org = make_org("mfa-gate-enrolled")
    admin = _make_admin(db_session, org, mfa_enabled=True, mfa_secret=secret)

    client = app.test_client()
    client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )

    # Wrong code: still not logged in.
    client.post("/account/mfa-challenge", data={"code": "000000"})
    dash = client.get("/dashboard/overview")
    assert dash.status_code in (302, 401)

    # Right code: now logged in.
    code = pyotp.TOTP(secret).now()
    resp = client.post("/account/mfa-challenge", data={"code": code}, follow_redirects=False)
    assert resp.status_code in (302, 303)

    dash = client.get("/dashboard/overview")
    assert dash.status_code == 200


def test_mfa_challenge_with_no_pending_login_redirects_to_login(app):
    client = app.test_client()
    resp = client.get("/account/mfa-challenge", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("Location", "")
