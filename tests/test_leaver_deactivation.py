"""A user deactivated through SCIM is refused everywhere (R1-B26 PR 1,
acceptance criterion 3): next request, every sign-in path, sessions and
outstanding account tokens revoked, remember-me cookie, no session minted."""

from __future__ import annotations

import pytest

from tests._scim_test_helpers import call, issue_token, make_user
from tests.smoke.conftest import PASSWORD

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def org(make_org):
    return make_org("leaver-deact")


@pytest.fixture
def token(db_session, org):
    _row, raw = issue_token(db_session, org)
    return raw


def _clear_g():
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)


def _member(db_session, org, label="leaver"):
    user = make_user(db_session, org, label)
    user.password = PASSWORD
    db_session.flush()
    return user


def _login(client, user, remember=False):
    _clear_g()
    return client.post(
        "/account/login",
        data={"email": user.email, "password": PASSWORD, "remember_me": "y" if remember else ""},
        follow_redirects=False,
    )


def _sessions(user_id):
    from app.models.user_session import UserSession

    return UserSession.query.filter(UserSession.user_id == user_id).all()


def test_a_live_session_is_refused_on_the_very_next_request(app, client, db_session, org, token):
    user = _member(db_session, org)
    assert _login(client, user).status_code in (302, 303)
    _clear_g()
    assert client.get("/dashboard/overview").status_code == 200

    _clear_g()
    assert call(client.application.test_client(), "DELETE", f"/Users/{user.id}", token).status_code == 204
    _clear_g()

    json_resp = client.get("/dashboard/overview", headers={"X-Requested-With": "XMLHttpRequest"})
    assert json_resp.status_code == 401
    assert json_resp.get_json()["code"] in ("revoked", "deactivated")
    _clear_g()
    resp = client.get("/dashboard/overview", follow_redirects=False)
    assert resp.status_code == 302 and "login" in resp.headers["Location"]


def test_the_session_policy_alone_refuses_a_deactivated_user_with_a_live_session(client, db_session, org, login_as):
    """Even with the registry row left alive, ``is_active`` False is refused."""
    from app.services import provisioning_service

    user = _member(db_session, org, "policy")
    login_as(client, user)
    assert client.get("/dashboard/overview").status_code == 200
    user.deactivated_at = provisioning_service._now()
    db_session.flush()
    _clear_g()
    assert all(s.revoked_at is None for s in _sessions(user.id))
    resp = client.get("/dashboard/overview", follow_redirects=False)
    assert resp.status_code == 302 and "login" in resp.headers["Location"]
    _clear_g()
    again = client.get("/dashboard/overview", headers={"X-Requested-With": "XMLHttpRequest"})
    assert again.status_code == 401


def test_password_sign_in_is_refused_with_a_neutral_message_and_no_session(client, db_session, org, token):
    user = _member(db_session, org)
    call(client, "DELETE", f"/Users/{user.id}", token)
    before = len(_sessions(user.id))
    resp = _login(client, user)
    assert resp.status_code in (302, 303)
    assert "login" in resp.headers["Location"]
    assert "dashboard" not in resp.headers["Location"]
    page = client.get("/account/login")
    assert b"This account is not active. Contact your administrator." in page.data
    assert len(_sessions(user.id)) == before


def test_json_sign_in_is_refused(client, db_session, org, token):
    user = _member(db_session, org)
    call(client, "DELETE", f"/Users/{user.id}", token)
    _clear_g()
    resp = client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    assert resp.status_code == 403
    assert resp.get_json()["success"] is False
    assert _sessions(user.id) == []


def test_login_and_register_returns_false_and_mints_no_session(app, db_session, org):
    from app.services import provisioning_service, session_registry

    user = _member(db_session, org)
    with app.test_request_context("/"):
        assert session_registry.login_and_register(user) is True
        assert len(_sessions(user.id)) == 1
        provisioning_service.deactivate_user(user, reason="leaver_deprovisioned", actor="test")
        other = _member(db_session, org, "other")
        provisioning_service.deactivate_user(other, reason="leaver_deprovisioned", actor="test")
        assert session_registry.login_and_register(other) is False
        assert _sessions(other.id) == []


def test_every_session_is_revoked_with_the_leaver_reason(client, db_session, org, token, login_as):
    from app.services import session_registry

    user = _member(db_session, org)
    login_as(client, user)
    second = client.application.test_client()
    login_as(second, user)
    assert len(_sessions(user.id)) == 2
    call(client, "DELETE", f"/Users/{user.id}", token)
    rows = _sessions(user.id)
    assert len(rows) == 2
    assert all(r.revoked_at is not None and r.revoked_reason == "leaver_deprovisioned" for r in rows)
    assert not any(session_registry.is_active(r.sid) for r in rows)


def test_outstanding_account_tokens_are_revoked(client, db_session, org, token):
    from app.models.account_token import PURPOSE_CONFIRM_EMAIL, PURPOSE_PASSWORD_RESET, AccountToken

    user = _member(db_session, org)
    AccountToken.issue(user, PURPOSE_PASSWORD_RESET)
    AccountToken.issue(user, PURPOSE_CONFIRM_EMAIL)
    db_session.flush()
    live = AccountToken.query.filter(AccountToken.user_id == user.id, AccountToken.revoked_at.is_(None)).count()
    assert live == 2
    call(client, "DELETE", f"/Users/{user.id}", token)
    rows = AccountToken.query.filter(AccountToken.user_id == user.id).all()
    assert len(rows) == 2 and all(r.revoked_at is not None for r in rows)


def test_the_remember_me_cookie_does_not_bring_them_back(app, client, db_session, org, token):
    user = _member(db_session, org)
    assert _login(client, user, remember=True).status_code in (302, 303)
    name = app.config.get("REMEMBER_COOKIE_NAME", "remember_token")
    assert client.get_cookie(name) is not None
    call(client.application.test_client(), "DELETE", f"/Users/{user.id}", token)
    # drop the signed session cookie but keep the remember cookie: only it can authenticate now
    client.delete_cookie("session")
    _clear_g()
    resp = client.get("/dashboard/overview", follow_redirects=False)
    assert resp.status_code != 200
    assert resp.status_code in (302, 401)
    _clear_g()
    assert client.get("/dashboard/overview", follow_redirects=False).status_code != 200


def test_reactivation_restores_sign_in(client, db_session, org, token):
    user = _member(db_session, org)
    call(client, "DELETE", f"/Users/{user.id}", token)
    from tests._scim_test_helpers import patch_body

    resp = call(client, "PATCH", f"/Users/{user.id}", token, patch_body({"op": "replace", "path": "active", "value": "True"}))
    assert resp.status_code == 200
    assert _login(client, user).status_code in (302, 303)
    _clear_g()
    assert client.get("/dashboard/overview").status_code == 200


def test_is_active_is_a_real_sql_predicate_for_the_existing_filters(db_session, org):
    from app.models.user import User
    from app.services import provisioning_service

    gone = _member(db_session, org, "gone")
    kept = _member(db_session, org, "kept")
    provisioning_service.deactivate_user(gone, reason="leaver_deprovisioned", actor="test")
    ids = {u.id for u in User.query.filter(User.organization_id == org.id).filter(User.is_active == True).all()}  # noqa: E712
    assert kept.id in ids and gone.id not in ids
    assert gone.is_active is False and kept.is_active is True


def test_deactivation_is_idempotent_and_changes_no_ownership(db_session, org):
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.services import provisioning_service

    user = _member(db_session, org, "owner")
    app_row = ApplicationComponent(name="Keep me", organization_id=org.id)
    db_session.add(app_row)
    db_session.flush()
    owner = ApplicationOwner(application_id=app_row.id, user_id=user.id, organization_id=org.id, ownership_type="primary")
    db_session.add(owner)
    db_session.flush()
    provisioning_service.deactivate_user(user, reason="leaver_deprovisioned", actor="test")
    first = user.deactivated_at
    provisioning_service.deactivate_user(user, reason="leaver_deprovisioned", actor="test")
    assert user.deactivated_at == first
    db_session.refresh(owner)
    assert owner.user_id == user.id
    rows = ApplicationOwner.get_display_rows_for_application(app_row.id, org.id)
    assert rows[0]["owner_active"] is False
