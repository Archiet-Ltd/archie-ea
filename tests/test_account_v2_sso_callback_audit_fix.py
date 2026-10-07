"""Regression test for the v2 SSO callback crash (hot-fix alongside the
/api/auth/login MFA bypass in tests/test_mfa_login_gate.py).

app/modules/account/v2/routes/account_routes.py's sso_callback() called
audit_logger.log("sso_login", ...) -- AuditLogger has no .log() method, only
log_event(...) and convenience wrappers such as log_authentication(...) --
so every successful SSO sign-in through this route raised AttributeError
*after* session_registry.login_and_register(user) had already run: the
visitor ended up logged in but the request itself 500'd.

No existing test in this repo drives this route's full OAuth exchange (the
nearby SSO tests cover a different blueprint, app.modules.auth.sso_routes,
via a different service seam). This monkeypatches the same seam
sso_callback() itself uses -- _get_sso_oauth() -- to stand in a fake OAuth
client, the smallest substitution that exercises the real route body.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


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


def test_sso_callback_logs_in_a_new_user_without_crashing(app, db_session, monkeypatch):
    """The uncovered path the bug report calls out: a non-admin user whose
    email isn't registered yet, so sso_callback() hits its 'create new
    user' branch. Before the fix this raised AttributeError right after the
    session was established; after the fix it must complete cleanly and the
    session must actually be the new user's."""
    from app.modules.account.v2.routes import account_routes

    _enable_sso_flag(db_session)

    email = f"sso-new-{uuid.uuid4().hex[:10]}@example.com"
    userinfo = {"email": email, "given_name": "Ada", "family_name": "Lovelace"}
    monkeypatch.setattr(
        account_routes, "_get_sso_oauth", lambda: _FakeSSOOAuth(userinfo)
    )

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["sso_state"] = "state-abc"

    resp = client.get(
        "/account/sso/callback/azure?state=state-abc", follow_redirects=False
    )

    # Before the fix this was a 500 (AttributeError) even though the user
    # had already been logged in by the time it was raised.
    assert resp.status_code in (302, 303), resp.get_data(as_text=True)

    from app.models.user import User

    created = User.query.filter_by(email=email).first()
    assert created is not None
    assert created.confirmed is True

    # The real assertion: session_registry.login_and_register(user) ran and
    # the request completed, so the signed session cookie names this user.
    with client.session_transaction() as sess:
        assert sess.get("_user_id") == str(created.id)
