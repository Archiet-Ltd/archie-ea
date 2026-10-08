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
# An administrator invited into a FOREIGN organisation (not their home
# organisation) must be gated on MFA exactly like a home-organisation
# administrator -- required_for() used to check only User.is_org_admin,
# which only ever answers for the user's own home organisation, so this
# class of administrator signed in on a password alone with no MFA step at
# all (second refuter pass, R1).
# ---------------------------------------------------------------------------


def _make_user_with_org_role(db_session, home_org, *, role_org=None, role="org_admin"):
    """A user whose HOME organisation membership carries no admin authority
    at all (plain default Role), but who optionally holds a real OrgRole
    grant in a different organisation they were invited into -- not a
    database shortcut on the User row."""
    from app.models.user import User

    user = User(
        email=f"invited-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=home_org.id,
        confirmed=True,
    )
    user.password = _PASSWORD
    db_session.add(user)
    db_session.commit()
    if role_org is not None:
        from app.models.org_role import OrgRole

        OrgRole.set_role(role_org.id, user.id, role, granted_by_id=user.id)
        db_session.commit()
    return user


def test_an_org_admin_of_an_invited_org_not_home_org_is_sent_to_mfa_on_password_login(
    app, db_session, make_org
):
    home_org = make_org("mfa-gate-invited-home")
    other_org = make_org("mfa-gate-invited-other")
    user = _make_user_with_org_role(db_session, home_org, role_org=other_org)

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": user.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" in resp.headers.get("Location", "")


def test_sso_callback_sends_an_org_admin_of_an_invited_org_to_the_mfa_challenge(
    app, db_session, make_org, monkeypatch
):
    home_org = make_org("sso-mfa-gate-invited-home")
    other_org = make_org("sso-mfa-gate-invited-other")
    user = _make_user_with_org_role(db_session, home_org, role_org=other_org)

    client = app.test_client()
    userinfo = {
        "sub": f"external-{uuid.uuid4().hex[:8]}",
        "email": user.email,
        "given_name": "Invited",
        "family_name": "Admin",
    }
    resp = _sso_callback(client, monkeypatch, db_session, userinfo)

    assert resp.status_code in (302, 303), resp.get_data(as_text=True)
    assert "/mfa-challenge" in resp.headers.get("Location", "")
    with client.session_transaction() as sess:
        assert sess.get("_mfa_pending_user_id") == user.id
        assert "_user_id" not in sess
    dash = client.get("/dashboard/overview")
    assert dash.status_code in (302, 401)


def test_a_user_with_no_org_role_admin_row_anywhere_is_not_sent_to_mfa_no_regression(
    app, db_session, make_org
):
    home_org = make_org("mfa-gate-no-admin-anywhere-home")
    user = _make_user_with_org_role(db_session, home_org, role_org=None)

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": user.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" not in resp.headers.get("Location", "")


def test_an_org_admin_of_only_a_deactivated_org_is_not_sent_to_mfa(
    app, db_session, make_org
):
    home_org = make_org("mfa-gate-deactivated-only-home")
    deactivated_org = make_org("mfa-gate-deactivated-only-other")
    deactivated_org.is_active = False
    db_session.commit()
    user = _make_user_with_org_role(db_session, home_org, role_org=deactivated_org)

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": user.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" not in resp.headers.get("Location", "")


def test_an_org_admin_of_a_deactivated_org_is_still_gated_if_also_admin_elsewhere(
    app, db_session, make_org
):
    """The deactivated organisation is excluded from the id set, but it must
    not poison the whole lookup: a user who is ALSO an admin of a separate,
    active organisation is still gated on MFA."""
    from app.models.org_role import OrgRole

    home_org = make_org("mfa-gate-deactivated-plus-home")
    deactivated_org = make_org("mfa-gate-deactivated-plus-deactivated")
    deactivated_org.is_active = False
    active_org = make_org("mfa-gate-deactivated-plus-active")
    db_session.commit()
    user = _make_user_with_org_role(db_session, home_org, role_org=deactivated_org)
    OrgRole.set_role(active_org.id, user.id, "org_admin", granted_by_id=user.id)
    db_session.commit()

    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": user.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/mfa-challenge" in resp.headers.get("Location", "")


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
# /account/sso/callback/<provider>: the same MFA gate, applied to the SSO
# callback -- hot-fix alongside the /api/auth/login bypass above.
#
# Both the v1 (app.modules.account.routes.account_routes) and v2
# (app.modules.account.v2.routes.account_routes) blueprints define a
# sso_callback() of their own, and only one is ever registered on a given
# running app -- chosen by USE_ACCOUNT_GUARDRAILS, which
# app/_bootstrap/blueprints.py's _init_blueprints() defaults to "true"
# (os.environ.setdefault) before _register_account() ever runs. That means
# v2 is what a default clone -- and production -- actually serves, which is
# exactly what this module's own ``app``/``client`` fixtures boot by
# default. The tests below patch v2's own _get_sso_oauth() seam and drive
# the shared ``app`` fixture's client, so they exercise the route that is
# actually live rather than one that happens to share a URL.
#
# The v1 route carries the identical MFA gate (same code, same session
# keys) and is kept under test too, forced via an explicit
# USE_ACCOUNT_GUARDRAILS=false override on a second, independently-built
# app instance (the ``_v1_forced_app`` helper below) -- the same
# second-create_app("testing") pattern already used by
# tests/test_platform_slos.py's early-failure test and
# tests/smoke/test_remember_cookie_session_rejection.py's out-of-band
# revoke. That second app gets its own SQLAlchemy engine/connection against
# the same physical TEST_DATABASE_URL, so this module's db_session
# savepoint-rollback fixture (scoped to the primary ``app``'s engine only)
# does not wrap it -- data for these specific tests is seeded with a real,
# directly-committed Organization/User via the forced app's own context,
# matching the convention those two existing call sites already use for a
# second app.
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
    """Drive the v2 (guardrail-enabled) sso_callback() -- the module that is
    actually registered under this module's default ``app``/``client``
    fixtures, same as a default clone and production."""
    from app.modules.account.v2.routes import account_routes as account_routes_v2

    _enable_sso_flag(db_session)
    monkeypatch.setattr(
        account_routes_v2, "_get_sso_oauth", lambda: _FakeSSOOAuth(userinfo)
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


# --- v1 (legacy, non-guardrail) sso_callback(): forced explicitly --------


def _v1_forced_app(monkeypatch):
    """Build a second, independent app instance with
    USE_ACCOUNT_GUARDRAILS explicitly off, so app.modules.account.routes
    .account_routes (v1) registers instead of v2. _is_flag() re-reads
    os.environ on every call and _init_blueprints()'s own
    os.environ.setdefault(..., "true") is a no-op once the variable is
    already set, so this override holds for the app built inside this
    monkeypatch's scope regardless of what the primary ``app`` fixture
    already set process-wide."""
    from app import create_app

    monkeypatch.setenv("USE_ACCOUNT_GUARDRAILS", "false")
    v1_app = create_app("testing")
    v1_app.config["TESTING"] = True
    v1_app.config["WTF_CSRF_ENABLED"] = False
    return v1_app


def _v1_seed_user(v1_app, label, *, mfa_enabled=False, mfa_secret=None, plain=False):
    """Create an Organization + User directly against the forced v1 app's
    own engine/connection and commit for real (see the module docstring
    above on why this bypasses the db_session savepoint fixture)."""
    from app import db
    from app.models import Role
    from app.models.organization import Organization
    from app.models.user import User

    with v1_app.app_context():
        suffix = uuid.uuid4().hex[:10]
        org = Organization(name=f"Test {label} {suffix}", slug=f"test-{label}-{suffix}")
        db.session.add(org)
        db.session.flush()

        kwargs = dict(
            email=f"{label}-{suffix}@example.com",
            organization_id=org.id,
            confirmed=True,
        )
        if not plain:
            role = Role.query.filter_by(name="Administrator").first()
            if role is None:
                pytest.skip("no Administrator role seeded in this database")
            kwargs["role"] = role
        user = User(**kwargs)
        user.password = _PASSWORD
        if not plain:
            user.mfa_enabled = mfa_enabled
            user.mfa_secret = mfa_secret
        db.session.add(user)
        db.session.commit()
        user_id, user_email = user.id, user.email
        db.session.remove()
    return user_id, user_email


def _sso_callback_v1(v1_app, monkeypatch, userinfo):
    """Drive v1's sso_callback() on the forced app. The SSO feature flag is
    enabled with a real, directly-committed write (same engine as the rest
    of this helper's seeding, see module docstring above) and always turned
    back off afterwards -- left on, it would leak into any other test in
    this session that asserts SSO-disabled behaviour against the shared
    physical database (e.g. test_rbac_and_sso_posture.py's
    test_oidc_sign_in_routes_404_when_disabled), since this real commit is
    not covered by the db_session savepoint-rollback fixture."""
    from app import db
    from app.modules.account.routes import account_routes

    with v1_app.app_context():
        _enable_sso_flag(db.session)
        db.session.remove()

    monkeypatch.setattr(
        account_routes, "_get_sso_oauth", lambda: _FakeSSOOAuth(userinfo)
    )

    client = v1_app.test_client()
    with client.session_transaction() as sess:
        sess["sso_state"] = "state-abc"

    try:
        resp = client.get(
            "/account/sso/callback/azure?state=state-abc", follow_redirects=False
        )
    finally:
        with v1_app.app_context():
            from app.models.feature_flags import FeatureFlag

            flag = FeatureFlag.query.filter_by(key="sso_authentication").first()
            if flag is not None:
                flag.enabled = False
                db.session.commit()
            db.session.remove()
    return client, resp


def test_v1_sso_callback_sends_an_mfa_enrolled_administrator_to_the_challenge(
    monkeypatch,
):
    """Explicit USE_ACCOUNT_GUARDRAILS=false override: proves the legacy v1
    sso_callback() carries the same MFA gate, for the rollback path where
    v1 is what is actually registered."""
    v1_app = _v1_forced_app(monkeypatch)
    secret = pyotp.random_base32()
    admin_id, admin_email = _v1_seed_user(
        v1_app, "v1-sso-mfa-gate-enrolled", mfa_enabled=True, mfa_secret=secret
    )

    userinfo = {
        "sub": f"external-{uuid.uuid4().hex[:8]}",
        "email": admin_email,
        "given_name": "Ada",
        "family_name": "Lovelace",
    }
    client, resp = _sso_callback_v1(v1_app, monkeypatch, userinfo)

    assert resp.status_code in (302, 303), resp.get_data(as_text=True)
    assert "/mfa-challenge" in resp.headers.get("Location", "")

    with client.session_transaction() as sess:
        assert sess.get("_mfa_pending_user_id") == admin_id
        assert "_user_id" not in sess
    dash = client.get("/dashboard/overview")
    assert dash.status_code in (302, 401)


def test_v1_sso_callback_still_logs_in_a_plain_user_no_regression(monkeypatch):
    v1_app = _v1_forced_app(monkeypatch)
    _, user_email = _v1_seed_user(v1_app, "v1-sso-mfa-gate-plain", plain=True)

    userinfo = {
        "sub": f"external-{uuid.uuid4().hex[:8]}",
        "email": user_email,
        "given_name": "Grace",
        "family_name": "Hopper",
    }
    client, resp = _sso_callback_v1(v1_app, monkeypatch, userinfo)

    assert resp.status_code in (302, 303), resp.get_data(as_text=True)

    with client.session_transaction() as sess:
        assert sess.get("_user_id") is not None
