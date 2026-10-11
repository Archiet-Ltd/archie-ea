"""Regression tests: opening a page must never end an existing session.

Reproduces the whole-product audit's finding -- signing in, then merely
GETting one of four routes (the sensitive change-email entry point, the
sign-in page itself, an SSO callback reached with no parameters, and the
admin new-user form) must never leave the visitor signed out. A follow-up
request to /dashboard/overview after each GET is the same "still
authenticated" canary tests/test_session_invalidation.py uses.

Written against the shared fixtures in tests/conftest.py.
"""

import uuid

import pyotp
import pytest

pytestmark = pytest.mark.usefixtures("db_session")

from tests.smoke.conftest import PASSWORD as _PASSWORD


def _make_user(db_session, org, password=_PASSWORD, **kw):
    from app.models.user import User

    user = User(
        email=f"nosignout-{uuid.uuid4().hex[:10]}@example.com",
        organization_id=org.id,
        confirmed=True,
        **kw,
    )
    user.password = password
    db_session.add(user)
    db_session.flush()
    return user


def _make_admin_user(db_session, org, password=_PASSWORD):
    """A platform admin who is also an org_admin -- the persona
    /admin/new-user is actually gated on: @rbac_service.require_role
    ("org_admin") reads the separate OrgRole table (defaults to "viewer"
    with no row there), stacked with @admin_required's Permission.ADMINISTER
    check on the User's Role.
    """
    from app.models.org_role import OrgRole
    from app.models.user import Role

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()

    user = _make_user(
        db_session,
        org,
        password=password,
        is_org_admin=True,
        is_platform_admin=True,
        role=admin_role,
    )
    db_session.add(OrgRole(organization_id=org.id, user_id=user.id, role="org_admin"))
    db_session.flush()
    return user


def _clear_g_cache():
    """See the identical note on tests/test_session_invalidation.py's copy --
    a single app context spans every test-client request in a db_session
    test, so flask_login's g-level user cache must be cleared between
    requests or a stale cached user hides the thing under test."""
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _complete_admin_mfa_enrollment(client):
    """Finish the MFA step an administrator now hits on login (R1-B12 PR 2):
    GET the challenge page (which stashes a fresh TOTP secret in the
    session, exactly as a real authenticator app scanning it would expect),
    then POST the code that secret actually produces right now. Mirrors
    tests/test_mfa_login_gate.py::test_enrolling_with_the_right_code_completes_login
    -- this test asserts on a completed session, so it must complete MFA for
    real rather than bypass the gate."""
    _clear_g_cache()
    client.get("/account/mfa-challenge")
    with client.session_transaction() as sess:
        secret = sess["_mfa_enroll_secret"]
    code = pyotp.TOTP(secret).now()
    _clear_g_cache()
    resp = client.post("/account/mfa-challenge", data={"code": code}, follow_redirects=False)
    assert resp.status_code in (302, 303), resp.status_code
    _clear_g_cache()


def _login_via_form(client, email, password):
    """Log in through the real /account/login route, exactly as a browser
    would -- not the login_as fixture's direct session write, because the
    routes under test here are about that real cookie surviving."""
    _clear_g_cache()
    resp = client.post(
        "/account/login",
        data={"email": email, "password": password, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    _clear_g_cache()


def _assert_still_signed_in(client):
    """The canary: an unrelated authenticated page must still answer 200."""
    _clear_g_cache()
    resp = client.get("/dashboard/overview", follow_redirects=False)
    assert resp.status_code == 200, (
        "opening the page under test ended the session: /dashboard/overview "
        f"now returns {resp.status_code}"
    )


class TestOpeningAPageNeverSignsOut:
    """Log in once, then open each of the four routes in turn -- the
    session must survive every one of them."""

    def test_change_email_page_keeps_the_session(self, app, db_session, make_org, client):
        org = make_org("nosignout1")
        user = _make_user(db_session, org)
        _login_via_form(client, user.email, _PASSWORD)

        _clear_g_cache()
        resp = client.get("/account/manage/change-email", follow_redirects=False)
        assert resp.status_code == 200, resp.status_code
        _assert_still_signed_in(client)

    def test_login_page_redirects_to_dashboard_instead_of_signing_out(
        self, app, db_session, make_org, client
    ):
        org = make_org("nosignout2")
        user = _make_user(db_session, org)
        _login_via_form(client, user.email, _PASSWORD)

        _clear_g_cache()
        resp = client.get("/account/login", follow_redirects=False)
        assert resp.status_code in (302, 303), resp.status_code
        assert "/dashboard" in resp.headers["Location"]
        _assert_still_signed_in(client)

    def test_bare_login_convenience_path_reaches_dashboard_not_the_form(
        self, app, db_session, make_org, client
    ):
        """The bare /login convenience redirect (distinct from /account/login)
        is what the audit actually walked -- it forwards to /account/login,
        which before this fix re-rendered the sign-in form for an already
        authenticated visitor. Follow the whole chain and land on the
        dashboard, not a login form."""
        org = make_org("nosignout2b")
        user = _make_user(db_session, org)
        _login_via_form(client, user.email, _PASSWORD)

        _clear_g_cache()
        resp = client.get("/login", follow_redirects=True)
        assert resp.status_code == 200, resp.status_code
        body = resp.get_data(as_text=True)
        assert 'id="password"' not in body, "landed back on the sign-in form"
        _assert_still_signed_in(client)

    def test_login_page_still_renders_the_form_when_signed_out(self, client):
        """Guard against overcorrecting: an anonymous visitor must still see
        the sign-in form, not get redirected anywhere."""
        resp = client.get("/account/login", follow_redirects=False)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "email" in body.lower()

    def test_sso_callback_without_parameters_answers_400_and_keeps_the_session(
        self, app, db_session, make_org, client
    ):
        org = make_org("nosignout3")
        user = _make_user(db_session, org)
        _login_via_form(client, user.email, _PASSWORD)

        _clear_g_cache()
        resp = client.get("/auth/sso/callback/oidc", follow_redirects=False)
        assert resp.status_code == 400, resp.status_code
        _assert_still_signed_in(client)

    def test_admin_new_user_page_keeps_the_session_and_shows_the_form(
        self, app, db_session, make_org, client
    ):
        org = make_org("nosignout4")
        admin = _make_admin_user(db_session, org)
        _login_via_form(client, admin.email, _PASSWORD)
        # R1-B12 PR 2: _login_via_form's POST now only parks this admin
        # mid-login pending MFA (still a 302, which is all that helper
        # asserts) -- complete the real TOTP step before the session this
        # test checks is actually a signed-in one.
        _complete_admin_mfa_enrollment(client)

        _clear_g_cache()
        resp = client.get("/admin/new-user", follow_redirects=False)
        assert resp.status_code == 200, resp.status_code
        body = resp.get_data(as_text=True)
        assert 'name="first_name"' in body
        _assert_still_signed_in(client)


class TestLoginRedirectHonoursSafeNext:
    """Landing on /account/login already signed in (e.g. a deep link, or a
    bookmarked page opened in a tab whose session outlived it) must return
    the visitor to where they were headed, not always the dashboard -- but
    only when that destination is actually on this site."""

    def test_safe_relative_next_is_honoured(self, app, db_session, make_org, client):
        org = make_org("nosignoutnext1")
        user = _make_user(db_session, org)
        _login_via_form(client, user.email, _PASSWORD)

        _clear_g_cache()
        resp = client.get(
            "/account/login?next=/capability-map/hierarchy", follow_redirects=False
        )
        assert resp.status_code in (302, 303), resp.status_code
        assert resp.headers["Location"] == "/capability-map/hierarchy"
        _assert_still_signed_in(client)

    def test_absolute_off_site_next_falls_back_to_dashboard(
        self, app, db_session, make_org, client
    ):
        org = make_org("nosignoutnext2")
        user = _make_user(db_session, org)
        _login_via_form(client, user.email, _PASSWORD)

        _clear_g_cache()
        resp = client.get(
            "/account/login?next=https://evil.example/x", follow_redirects=False
        )
        assert resp.status_code in (302, 303), resp.status_code
        assert "/dashboard" in resp.headers["Location"]
        assert "evil.example" not in resp.headers["Location"]
        _assert_still_signed_in(client)

    def test_protocol_relative_off_site_next_falls_back_to_dashboard(
        self, app, db_session, make_org, client
    ):
        org = make_org("nosignoutnext3")
        user = _make_user(db_session, org)
        _login_via_form(client, user.email, _PASSWORD)

        _clear_g_cache()
        resp = client.get(
            "/account/login?next=//evil.example", follow_redirects=False
        )
        assert resp.status_code in (302, 303), resp.status_code
        assert "/dashboard" in resp.headers["Location"]
        assert "evil.example" not in resp.headers["Location"]
        _assert_still_signed_in(client)


class TestChangeEmailReauthenticationReturnsToTheForm:
    """The POST side of the sensitive change keeps requiring the password
    (Constraints: that control must not weaken) -- a wrong password sends
    the visitor back to the same form rather than anywhere else, and never
    ends the session either."""

    def test_wrong_password_returns_to_the_change_email_form(
        self, app, db_session, make_org, client
    ):
        org = make_org("nosignout5")
        user = _make_user(db_session, org)
        _login_via_form(client, user.email, _PASSWORD)

        _clear_g_cache()
        resp = client.post(
            "/account/manage/change-email",
            data={
                "email": f"new-{uuid.uuid4().hex[:8]}@example.com",
                "password": "definitely-the-wrong-password",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 200, resp.status_code
        body = resp.get_data(as_text=True)
        assert "invalid email or password" in body.lower()
        _assert_still_signed_in(client)

    def test_blank_password_is_rejected_and_email_is_unchanged(
        self, app, db_session, make_org, client
    ):
        """Regression guard for the constraint itself: opening the page (and
        any reauthentication convenience added for it) must not loosen the
        POST's existing password requirement."""
        from app.extensions import db as _db
        from app.models.user import User

        org = make_org("nosignout6")
        user = _make_user(db_session, org)
        original_email = user.email
        _login_via_form(client, user.email, _PASSWORD)

        _clear_g_cache()
        resp = client.post(
            "/account/manage/change-email",
            data={"email": f"new-{uuid.uuid4().hex[:8]}@example.com", "password": ""},
            follow_redirects=False,
        )
        # WTForms InputRequired rejects the blank password before the
        # service layer runs -- still a re-render of the same form, not a
        # change and not a sign-out.
        assert resp.status_code == 200
        unchanged = _db.session.get(User, user.id)
        assert unchanged.email == original_email
        _assert_still_signed_in(client)
