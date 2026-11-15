"""Signing out is a deliberate POST with the form's token, and the sign-in page
says why a person is there."""

import uuid

import pytest

from app.models.user import User

_PASSWORD = "Sup3r-secret-pass!"


def _user(db_session, make_org, tag):
    org = make_org(tag)
    user = User(
        email=f"{tag}-{uuid.uuid4().hex[:8]}@example.com",
        confirmed=True,
        organization_id=org.id,
    )
    user.password = _PASSWORD
    db_session.add(user)
    db_session.flush()
    return user


def _forget_cached_user():
    # db_session keeps one app context open, so flask_login's cached user on g
    # would otherwise survive a sign-out made by another request in the test.
    from flask import g

    for cached in ("_login_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _sign_in(app, db_session, make_org, tag):
    user = _user(db_session, make_org, tag)
    client = app.test_client()
    resp = client.post(
        "/account/login",
        data={"email": user.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    _forget_cached_user()
    assert client.get("/dashboard/overview").status_code == 200
    return client


def test_get_logout_asks_and_keeps_the_session(app, db_session, make_org):
    client = _sign_in(app, db_session, make_org, "logoutget")
    _forget_cached_user()
    resp = client.get("/account/logout")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'data-testid="logout-confirm"' in html and "Sign out" in html
    _forget_cached_user()
    assert client.get("/dashboard/overview").status_code == 200


def test_post_logout_signs_out_and_says_so(app, db_session, make_org):
    client = _sign_in(app, db_session, make_org, "logoutpost")
    _forget_cached_user()
    resp = client.post("/account/logout", follow_redirects=True)
    assert resp.status_code == 200
    assert resp.get_data(as_text=True).count("You were signed out.") == 1
    _forget_cached_user()
    assert client.get("/dashboard/overview", follow_redirects=False).status_code in (302, 401)


def test_post_logout_without_the_token_is_refused(app, db_session, make_org):
    client = _sign_in(app, db_session, make_org, "logoutcsrf")
    try:
        app.config["WTF_CSRF_ENABLED"] = True
        _forget_cached_user()
        resp = client.post("/account/logout")
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert resp.status_code == 400
    _forget_cached_user()
    assert client.get("/dashboard/overview").status_code == 200


@pytest.mark.parametrize(
    "reason,sentence",
    [
        ("revoked", "You were signed out."),
        ("signed_out", "You were signed out."),
        ("idle", "Your session ended after a period of inactivity. Sign in again to carry on."),
    ],
)
def test_sign_in_page_gives_the_reason_it_was_sent_for(app, reason, sentence):
    html = app.test_client().get(f"/account/login?timeout={reason}").get_data(as_text=True)
    assert sentence in html


def test_sign_in_page_invents_no_reason(app):
    client = app.test_client()
    for url in ("/account/login", "/account/login?timeout=nonsense"):
        html = client.get(url).get_data(as_text=True)
        assert 'data-testid="login-why"' not in html
