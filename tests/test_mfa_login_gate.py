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


# ---------------------------------------------------------------------------
# /api/auth/login: the same MFA gate, applied to the JSON API endpoint
# (hot-fix for a complete MFA bypass -- this endpoint used to call
# session_registry.login_and_register unconditionally after a correct
# password, with no check at all for whether the user has MFA enabled).
# ---------------------------------------------------------------------------


def _api_login(client, email, password):
    return client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )


def test_api_login_refuses_an_mfa_enrolled_administrator(app, db_session, make_org):
    secret = pyotp.random_base32()
    org = make_org("api-mfa-gate-enrolled")
    admin = _make_admin(db_session, org, mfa_enabled=True, mfa_secret=secret)

    client = app.test_client()
    resp = _api_login(client, admin.email, _PASSWORD)

    assert resp.status_code == 401, resp.get_json()
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"] == "mfa_required"

    # No real session was established: the session has no logged-in user id,
    # and an authenticated-only route still refuses the follow-up request.
    with client.session_transaction() as sess:
        assert "_user_id" not in sess
    dash = client.get("/dashboard/overview")
    assert dash.status_code in (302, 401)


def test_api_login_refuses_an_administrator_who_has_not_enrolled_mfa_either(
    app, db_session, make_org
):
    """mfa_service.required_for() treats an unenrolled administrator the same
    as an enrolled one -- MFA is required either way, and this API endpoint
    cannot complete enrolment, so it must refuse rather than ever let an
    unenrolled administrator through on a password alone."""
    org = make_org("api-mfa-gate-unenrolled")
    admin = _make_admin(db_session, org, mfa_enabled=False)

    client = app.test_client()
    resp = _api_login(client, admin.email, _PASSWORD)

    assert resp.status_code == 401, resp.get_json()
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"] == "mfa_required"

    with client.session_transaction() as sess:
        assert "_user_id" not in sess
    dash = client.get("/dashboard/overview")
    assert dash.status_code in (302, 401)


def test_api_login_still_succeeds_for_a_plain_user_no_regression(app, db_session, make_org):
    org = make_org("api-mfa-gate-plain")
    user = _make_plain_user(db_session, org)

    client = app.test_client()
    resp = _api_login(client, user.email, _PASSWORD)

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["success"] is True
    assert body["user"]["email"] == user.email

    with client.session_transaction() as sess:
        assert sess.get("_user_id") == str(user.id)


# ---------------------------------------------------------------------------
# /account/sso/callback/<provider> (v1 account blueprint): the same MFA gate,
# applied to the SSO callback -- hot-fix alongside the /api/auth/login bypass
# above and the v2 SSO callback crash fix in
# tests/test_account_v2_sso_callback_audit_fix.py. This route's own
# sso_callback() called session_registry.login_and_register(user,
# remember=True) unconditionally after resolving/creating the user, with no
# check at all for whether the user needs to complete MFA first -- the same
# bug class as the API-login bypass, for the IdP-driven sign-in path.
#
# _get_sso_oauth() is this file's own helper (app.modules.account.routes
# .account_routes), separate from the v2 blueprint's helper of the same
# name -- monkeypatched the same way the v2 crash-fix test does, the
# smallest substitution that exercises the real route body.
# ---------------------------------------------------------------------------


def _enable_sso_flag(db_session):
    from app.models.feature_flags import FeatureFlag

    flag = FeatureFlag.query.filter_by(key="sso_authentication").first()
    if flag is None:
        flag = FeatureFlag(key="sso_authentication", name="SSO authentication", enabled=True)
        db_session.add(flag)
    else:
        flag.enabled = True
    db_session.commit()
    return flag


class _FakeSSOClient:
    """Stands in for the authlib client sso_callback() calls -- only the two
    methods the route actually uses."""

    def __init__(self, userinfo):
        self._userinfo = userinfo

    def authorize_access_token(self):
        # Mirrors the real shape: token.get("userinfo") is tried first.
        return {"userinfo": self._userinfo}

    def userinfo(self):  # pragma: no cover - not reached, token already has it
        return self._userinfo


class _FakeSSOOAuth:
    def __init__(self, userinfo):
        self._userinfo = userinfo

    def create_client(self, provider):
        return _FakeSSOClient(self._userinfo)


def _sso_callback(client, monkeypatch, db_session, userinfo):
    from app.modules.account.routes import account_routes

    _enable_sso_flag(db_session)
    monkeypatch.setattr(
        account_routes, "_get_sso_oauth", lambda: _FakeSSOOAuth(userinfo)
    )

    with client.session_transaction() as sess:
        sess["sso_state"] = "state-abc"

    return client.get(
        "/account/sso/callback/azure?state=state-abc", follow_redirects=False
    )


def test_sso_callback_sends_an_mfa_enrolled_administrator_to_the_challenge(
    app, db_session, make_org, monkeypatch
):
    secret = pyotp.random_base32()
    org = make_org("sso-mfa-gate-enrolled")
    admin = _make_admin(db_session, org, mfa_enabled=True, mfa_secret=secret)

    client = app.test_client()
    userinfo = {
        "sub": f"external-{uuid.uuid4().hex[:8]}",
        "email": admin.email,
        "given_name": "Ada",
        "family_name": "Lovelace",
    }
    resp = _sso_callback(client, monkeypatch, db_session, userinfo)

    assert resp.status_code in (302, 303), resp.get_data(as_text=True)
    assert "/mfa-challenge" in resp.headers.get("Location", "")

    # No real session was established: the pending-MFA key is set, there is
    # no logged-in user id, and an authenticated-only route still refuses
    # the follow-up request.
    with client.session_transaction() as sess:
        assert sess.get("_mfa_pending_user_id") == admin.id
        assert "_user_id" not in sess
    dash = client.get("/dashboard/overview")
    assert dash.status_code in (302, 401)


def test_sso_callback_still_logs_in_a_plain_user_no_regression(
    app, db_session, make_org, monkeypatch
):
    org = make_org("sso-mfa-gate-plain")
    user = _make_plain_user(db_session, org)

    client = app.test_client()
    userinfo = {
        "sub": f"external-{uuid.uuid4().hex[:8]}",
        "email": user.email,
        "given_name": "Grace",
        "family_name": "Hopper",
    }
    resp = _sso_callback(client, monkeypatch, db_session, userinfo)

    assert resp.status_code in (302, 303), resp.get_data(as_text=True)

    with client.session_transaction() as sess:
        assert sess.get("_user_id") == str(user.id)
