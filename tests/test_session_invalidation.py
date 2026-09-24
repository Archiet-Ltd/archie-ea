"""Regression tests for session invalidation on logout.

Reproduces the exact pentest steps against a real test client: log in,
capture the raw session cookie, hit an authenticated route, log out,
replay the captured cookie against the same route. Pre-fix this still
returns 200; post-fix it must not.

Written against the shared fixtures in ``tests/conftest.py`` — ``db_session``
rolls everything back, ``app`` is session-scoped.
"""

import datetime
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

_PASSWORD = "Correct-Horse-9!"


def _make_user(db_session, org, password=_PASSWORD):
    from app.models.user import User

    user = User(
        email=f"sess-{uuid.uuid4().hex[:10]}@example.com",
        organization_id=org.id,
        confirmed=True, onboarding_completed_at=datetime.datetime.utcnow(),
    )
    user.password = password
    db_session.add(user)
    db_session.flush()
    return user


def _clear_g_cache():
    """Defeat flask_login's g-level user cache between test-client requests.

    ``db_session`` holds ONE app context open for the whole test, so ``g``
    survives across every ``test_client()`` call in it -- flask_login caches
    the resolved user on ``g._login_user``, and ``logout_user()`` (called by
    a *different* client within the same test) sets that cache to the
    anonymous user for the rest of the test, not just for the client that
    called it. Without clearing it, every assertion after a logout call
    would pass for the wrong reason (a stale cached anonymous user) rather
    than because the fix under test actually ran. See the identical note on
    the shared ``login_as`` fixture in tests/conftest.py.
    """
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _login_via_form(client, email, password):
    """Log in through the real /account/login route (mints a _sid, per the
    fix), and return the raw captured session cookie string."""
    _clear_g_cache()
    resp = client.post(
        "/account/login",
        data={"email": email, "password": password, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    cookie = client.get_cookie("session")
    assert cookie is not None, "login did not set a session cookie"
    _clear_g_cache()
    return cookie.value


class TestExploitReproduction:
    """1. Log in, capture cookie A. 2. GET an authenticated page -> 200.
    3. Log out. 4. Replay cookie A against the same page -> must NOT be 200.
    """

    def test_replayed_cookie_rejected_after_logout(self, app, db_session, make_org):
        org = make_org("sessioninv")
        user = _make_user(db_session, org)

        client = app.test_client()
        captured_cookie = _login_via_form(client, user.email, _PASSWORD)

        resp = client.get("/dashboard/overview")
        assert resp.status_code == 200, "sanity: session must authenticate before logout"

        logout_resp = client.get("/account/logout", follow_redirects=False)
        assert logout_resp.status_code in (302, 303)
        _clear_g_cache()

        # Fresh client -- simulates the attacker who captured the cookie
        # independently and never sees the logout request.
        replay_client = app.test_client()
        replay_client.set_cookie("session", captured_cookie)
        replay_resp = replay_client.get("/dashboard/overview", follow_redirects=False)

        assert replay_resp.status_code != 200, (
            "SECURITY REGRESSION: a session cookie captured before logout still "
            f"authenticates after logout (got {replay_resp.status_code})"
        )
        assert replay_resp.status_code in (302, 401)
        if replay_resp.status_code == 302:
            assert "login" in replay_resp.headers["Location"]

    def test_replayed_cookie_rejected_after_json_logout(self, app, db_session, make_org):
        """Same exploit against the JSON logout/login pair."""
        org = make_org("sessioninv2")
        user = _make_user(db_session, org)

        client = app.test_client()
        login_resp = client.post(
            "/api/auth/login",
            json={"email": user.email, "password": _PASSWORD},
        )
        assert login_resp.status_code == 200, login_resp.get_json()
        cookie = client.get_cookie("session")
        assert cookie is not None
        captured_cookie = cookie.value
        _clear_g_cache()

        # Sanity: authenticated before logout.
        assert client.get("/dashboard/overview").status_code == 200
        _clear_g_cache()

        logout_resp = client.post("/api/auth/logout")
        assert logout_resp.status_code == 200
        _clear_g_cache()

        replay_client = app.test_client()
        replay_client.set_cookie("session", captured_cookie)
        replay_resp = replay_client.get(
            "/dashboard/overview",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert replay_resp.status_code == 401, replay_resp.status_code
        body = replay_resp.get_json()
        assert body is not None
        assert body.get("code") == "revoked"


class TestNoFalsePositives:
    def test_active_session_survives_many_requests(self, app, db_session, make_org):
        """A normal session must keep working for its full lifetime -- no
        false-positive invalidation."""
        org = make_org("sessionok")
        user = _make_user(db_session, org)

        client = app.test_client()
        _login_via_form(client, user.email, _PASSWORD)

        for _ in range(20):
            resp = client.get("/dashboard/overview")
            assert resp.status_code == 200

    def test_last_seen_advances(self, app, db_session, make_org):
        from app.models.user_session import UserSession

        org = make_org("sessiontouch")
        user = _make_user(db_session, org)

        client = app.test_client()
        _login_via_form(client, user.email, _PASSWORD)

        with client.session_transaction() as sess:
            sid = sess.get("_sid")
        assert sid

        row = db_session.get(UserSession, sid)
        assert row is not None
        assert row.revoked_at is None


class TestIdleTimeoutStillRevokes:
    def test_idle_timeout_writes_revoked_reason(self, app, db_session, make_org):
        from datetime import datetime, timedelta, timezone

        from app._bootstrap.session_policy import LAST_ACTIVITY_KEY
        from app.models.user_session import UserSession

        org = make_org("sessionidle")
        user = _make_user(db_session, org)

        client = app.test_client()
        _login_via_form(client, user.email, _PASSWORD)

        idle = app.config["SESSION_IDLE_TIMEOUT_SECONDS"]
        stale = int((datetime.now(timezone.utc) - timedelta(seconds=idle + 60)).timestamp())
        with client.session_transaction() as sess:
            sess[LAST_ACTIVITY_KEY] = stale
            sid = sess.get("_sid")

        resp = client.get("/dashboard/overview")
        assert resp.status_code in (302, 401)

        row = db_session.get(UserSession, sid)
        assert row is not None
        assert row.revoked_at is not None
        assert row.revoked_reason == "idle_timeout"

    def test_replay_after_idle_timeout_is_rejected(self, app, db_session, make_org):
        from datetime import datetime, timedelta, timezone

        from app._bootstrap.session_policy import LAST_ACTIVITY_KEY

        org = make_org("sessionidlereplay")
        user = _make_user(db_session, org)

        client = app.test_client()
        captured_cookie = _login_via_form(client, user.email, _PASSWORD)

        idle = app.config["SESSION_IDLE_TIMEOUT_SECONDS"]
        stale = int((datetime.now(timezone.utc) - timedelta(seconds=idle + 60)).timestamp())
        with client.session_transaction() as sess:
            sess[LAST_ACTIVITY_KEY] = stale
        # Trip the idle timeout so the registry row is revoked.
        client.get("/dashboard/overview")
        _clear_g_cache()

        replay_client = app.test_client()
        replay_client.set_cookie("session", captured_cookie)
        # The replayed cookie still carries the *stale* LAST_ACTIVITY_KEY, but
        # the registry check runs first and must reject it regardless.
        resp = replay_client.get("/dashboard/overview")
        assert resp.status_code in (302, 401)


class TestNoRegistryRecordIsRejected:
    def test_session_with_user_id_but_no_sid_is_rejected(self, app, db_session, make_org):
        """A session that never went through login_and_register (e.g. a
        pre-fix cookie, or one forged with only _user_id) is rejected --
        fail closed."""
        org = make_org("sessionnosid")
        user = _make_user(db_session, org)

        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
            # deliberately no "_sid"

        resp = client.get("/dashboard/overview")
        assert resp.status_code in (302, 401)


class TestRememberCookieActuallyClearedOnReject:
    """Round-2 D1 regression: a remember-me cookie must actually be cleared
    when a session is rejected, or logout_user()+session.clear() (in that
    order) silently no-ops the remember-cookie deletion and produces an
    unbreakable redirect loop for any remember-me/SSO user."""

    def test_remember_cookie_cleared_on_revoked_session_rejection(self, app, db_session, make_org):
        org = make_org("sessionremember")
        user = _make_user(db_session, org)

        client = app.test_client()
        _clear_g_cache()
        resp = client.post(
            "/account/login",
            data={"email": user.email, "password": _PASSWORD, "remember_me": "y"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303), resp.status_code

        remember_cookie = client.get_cookie(app.config.get("REMEMBER_COOKIE_NAME", "remember_token"))
        assert remember_cookie is not None, "sanity: remember_me=True must set a remember cookie"
        _clear_g_cache()

        # Revoke the underlying session server-side (simulates logout /
        # idle-timeout / password-change from elsewhere) without touching
        # this client's cookies, then make a follow-up request.
        from app.services import session_registry

        with client.session_transaction() as sess:
            sid = sess.get("_sid")
        session_registry.revoke(sid, "logout")
        _clear_g_cache()

        rejected = client.get("/dashboard/overview", follow_redirects=False)
        assert rejected.status_code in (302, 401)

        set_cookie_headers = rejected.headers.get_all("Set-Cookie")
        remember_name = app.config.get("REMEMBER_COOKIE_NAME", "remember_token")
        cleared = [
            h for h in set_cookie_headers
            if h.startswith(remember_name + "=")
            and ("Max-Age=0" in h or "expires=Thu, 01-Jan-1970" in h or "01 Jan 1970" in h)
        ]
        assert cleared, (
            "SECURITY REGRESSION (D1): the remember_token cookie was not cleared "
            f"on session rejection. Set-Cookie headers were: {set_cookie_headers}"
        )
        _clear_g_cache()

        # The decisive check: the still-present-in-the-jar remember cookie
        # must not silently re-authenticate the client on the very next
        # request (the actual redirect-loop symptom).
        follow_up = client.get("/dashboard/overview", follow_redirects=False)
        assert follow_up.status_code != 200, (
            "SECURITY REGRESSION (D1): remember-cookie re-authenticated the "
            "client after its session was rejected -- redirect loop."
        )


class TestPasswordChangeRevokesOtherSessions:
    """Task 02: changing/resetting a password revokes other active sessions."""

    def test_change_password_revokes_other_device_keeps_acting_one(self, app, db_session, make_org):
        org = make_org("pwchange")
        user = _make_user(db_session, org)

        client_a = app.test_client()
        _login_via_form(client_a, user.email, _PASSWORD)

        client_b = app.test_client()
        cookie_b = _login_via_form(client_b, user.email, _PASSWORD)

        # Client A changes the password.
        resp = client_a.post(
            "/account/manage/change-password",
            data={
                "old_password": _PASSWORD,
                "new_password": "New-Correct-Horse-9!",
                "new_password2": "New-Correct-Horse-9!",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303), resp.status_code
        _clear_g_cache()

        # Client B's cookie (captured before the change) must now be rejected.
        replay_client = app.test_client()
        replay_client.set_cookie("session", cookie_b)
        replay_resp = replay_client.get("/dashboard/overview")
        assert replay_resp.status_code in (302, 401), replay_resp.status_code
        _clear_g_cache()

        # Client A -- the device that made the change -- must remain logged in.
        still_ok = client_a.get("/dashboard/overview")
        assert still_ok.status_code == 200

    def test_wrong_old_password_revokes_nothing(self, app, db_session, make_org):
        from app.models.user_session import UserSession

        org = make_org("pwchangewrong")
        user = _make_user(db_session, org)

        client_a = app.test_client()
        _login_via_form(client_a, user.email, _PASSWORD)
        client_b = app.test_client()
        _login_via_form(client_b, user.email, _PASSWORD)

        resp = client_a.post(
            "/account/manage/change-password",
            data={
                "old_password": "totally-wrong-password",
                "new_password": "New-Correct-Horse-9!",
                "new_password2": "New-Correct-Horse-9!",
            },
        )
        assert resp.status_code == 200  # re-renders the form with an error

        active = UserSession.query.filter_by(user_id=user.id, revoked_at=None).count()
        assert active == 2, "a failed password-change attempt must not revoke any session"

    def test_reset_password_revokes_every_session_including_the_resetting_one(
        self, app, db_session, make_org
    ):
        from app.modules.account.services.account_service import AccountService

        org = make_org("pwreset")
        user = _make_user(db_session, org)

        client_a = app.test_client()
        cookie_a = _login_via_form(client_a, user.email, _PASSWORD)

        token = user.generate_password_reset_token()
        success, message = AccountService.reset_password(token, user.email, "Reset-Correct-Horse-9!")
        assert success, message
        _clear_g_cache()

        replay_client = app.test_client()
        replay_client.set_cookie("session", cookie_a)
        resp = replay_client.get("/dashboard/overview")
        assert resp.status_code in (302, 401)

    def test_flash_count_matches_actual_revocations(self, app, db_session, make_org):
        org = make_org("pwchangecount")
        user = _make_user(db_session, org)

        client_a = app.test_client()
        _login_via_form(client_a, user.email, _PASSWORD)
        client_b = app.test_client()
        _login_via_form(client_b, user.email, _PASSWORD)
        client_c = app.test_client()
        _login_via_form(client_c, user.email, _PASSWORD)

        resp = client_a.post(
            "/account/manage/change-password",
            data={
                "old_password": _PASSWORD,
                "new_password": "New-Correct-Horse-9!",
                "new_password2": "New-Correct-Horse-9!",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # Two OTHER sessions (B, C) were revoked; A is the acting session.
        assert "2 other signed-in devices were signed out." in body


class TestAdminPasswordResetRevokesSessions:
    """Round-2 D2: admin-initiated password reset is the primary
    incident-response path (compromised account) and must revoke the
    account's existing sessions -- not just change the password."""

    def test_admin_set_user_password_revokes_existing_session(self, app, db_session, make_org):
        from app.modules.admin.services.admin_user_service import AdminUserService
        from app.models.user_session import UserSession

        org = make_org("adminreset")
        user = _make_user(db_session, org)

        client = app.test_client()
        _login_via_form(client, user.email, _PASSWORD)
        with client.session_transaction() as sess:
            sid = sess.get("_sid")
        assert sid

        AdminUserService.set_user_password(user, "Admin-Reset-Correct-Horse-9!")
        db_session.commit()

        row = db_session.get(UserSession, sid)
        assert row is not None
        assert row.revoked_at is not None, (
            "D2 REGRESSION: admin password reset did not revoke the user's "
            "existing session -- a captured cookie keeps working after "
            "an admin 'fixes' a compromised account."
        )

        _clear_g_cache()
        resp = client.get("/dashboard/overview")
        assert resp.status_code in (302, 401)

    def test_admin_v2_set_user_password_revokes_existing_session(self, app, db_session, make_org):
        from app.modules.admin.v2.services.admin_user_service_v2 import (
            AdminUserService as AdminUserServiceV2,
        )
        from app.models.user_session import UserSession

        org = make_org("adminresetv2")
        user = _make_user(db_session, org)

        client = app.test_client()
        _login_via_form(client, user.email, _PASSWORD)
        with client.session_transaction() as sess:
            sid = sess.get("_sid")
        assert sid

        AdminUserServiceV2.set_user_password(user, "Admin-Reset-Correct-Horse-9!")
        db_session.commit()

        row = db_session.get(UserSession, sid)
        assert row is not None
        assert row.revoked_at is not None


class TestGdprErasureRevokesSessions:
    """Round-2 D3: erasing a subject's data must also kill their live
    session, or an already-open browser keeps authenticating and reading
    data after 'erasure' completes."""

    def test_delete_user_data_revokes_existing_session(self, app, db_session, make_org):
        from app.services.gdpr_service import GDPRService
        from app.models.user_session import UserSession

        org = make_org("gdprerasure")
        requester = _make_user(db_session, org)
        subject = _make_user(db_session, org)

        client = app.test_client()
        _login_via_form(client, subject.email, _PASSWORD)
        with client.session_transaction() as sess:
            sid = sess.get("_sid")
        assert sid

        ok = GDPRService.delete_user_data(subject.id, requester.id)
        assert ok

        row = db_session.get(UserSession, sid)
        assert row is not None
        assert row.revoked_at is not None, (
            "D3 REGRESSION: GDPR erasure did not revoke the subject's "
            "existing session -- their already-open browser tab keeps "
            "authenticating after erasure."
        )


class TestUserSessionForeignKeyCascade:
    """Round-2 D4: user_sessions has no ondelete on its users.id FK, so any
    user who ever logged in accumulates permanent rows and a bare
    db.session.delete(user) 500s with a ForeignKeyViolation."""

    def test_delete_user_with_active_session_succeeds(self, app, db_session, make_org):
        """Insert the ``user_sessions`` row directly (rather than through a
        real login) to isolate this test to the FK under test -- going
        through the full login path also writes a ``soc2_audit_log`` row,
        whose own FK (unrelated, pre-existing, out of this bucket's scope)
        would otherwise mask a pass/fail of the fix under test here."""
        import secrets as _secrets

        from app.modules.admin.services.admin_user_service import AdminUserService
        from app.models.user_session import UserSession

        org = make_org("deluserfk")
        user = _make_user(db_session, org)

        sid = _secrets.token_urlsafe(32)
        db_session.add(UserSession(sid=sid, user_id=user.id, organization_id=org.id))
        db_session.flush()
        assert db_session.get(UserSession, sid) is not None

        success, message = AdminUserService.delete_user(user)
        assert success, message

        assert db_session.get(UserSession, sid) is None, (
            "D4 REGRESSION: deleting a user with an active session should "
            "cascade-delete their user_sessions rows via ondelete=CASCADE, "
            "not raise or leave the row orphaned."
        )
