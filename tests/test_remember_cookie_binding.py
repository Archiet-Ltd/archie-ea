"""A remember cookie is tied to something that can be revoked.

A cookie copied before a password change, a reset or a sign-out-everywhere must
not sign anyone in afterwards; one issued afterwards must.
"""

import pytest

from tests.smoke.conftest import PASSWORD as _PASSWORD
from tests.test_session_invalidation import _clear_g_cache, _make_user

NEW_PASSWORD = "New-Correct-Horse-9!"


def _remember_cookie(app, email, password):
    client = app.test_client()
    _clear_g_cache()
    resp = client.post(
        "/account/login",
        data={"email": email, "password": password, "remember_me": "y"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    cookie = client.get_cookie(app.config.get("REMEMBER_COOKIE_NAME", "remember_token"))
    assert cookie is not None
    _clear_g_cache()
    return client, cookie.value


def _open_with_only(app, value):
    """A browser holding nothing but the copied remember cookie."""
    thief = app.test_client()
    thief.set_cookie(app.config.get("REMEMBER_COOKIE_NAME", "remember_token"), value)
    _clear_g_cache()
    return thief.get("/dashboard/overview", follow_redirects=False)


def _end(app, kind, user, owner):
    from app.services import session_registry

    if kind == "password_change":
        resp = owner.post(
            "/account/manage/change-password",
            data={"old_password": _PASSWORD, "new_password": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303), resp.status_code
    elif kind == "reset":
        from app.models.account_token import PURPOSE_PASSWORD_RESET, AccountToken
        from app.modules.account.services.account_service import AccountService

        _row, token = AccountToken.issue(user, PURPOSE_PASSWORD_RESET)
        ok, message = AccountService.reset_password(token, NEW_PASSWORD)
        assert ok, message
    else:
        session_registry.revoke_all_for_user(user.id, "user_all")
    _clear_g_cache()


@pytest.mark.parametrize("kind", ["password_change", "reset", "sign_out_everywhere"])
def test_cookie_copied_before_is_refused_after(app, db_session, make_org, kind):
    org = make_org("rememberbind" + kind.replace("_", "")[:8])
    user = _make_user(db_session, org)
    owner, cookie = _remember_cookie(app, user.email, _PASSWORD)

    assert _open_with_only(app, cookie).status_code == 200, "a fresh remembered browser should be signed in"

    _end(app, kind, user, owner)

    resp = _open_with_only(app, cookie)
    assert resp.status_code in (302, 401), "copied cookie still signed in after %s" % kind
    if resp.status_code == 302:
        assert "/account/login" in resp.headers["Location"]


def test_cookie_issued_after_the_change_still_works(app, db_session, make_org):
    org = make_org("rememberafter")
    user = _make_user(db_session, org)
    owner, _old = _remember_cookie(app, user.email, _PASSWORD)
    _end(app, "password_change", user, owner)

    _client, fresh = _remember_cookie(app, user.email, NEW_PASSWORD)
    assert _open_with_only(app, fresh).status_code == 200


def test_cookie_of_one_organisation_does_not_open_another_account(app, db_session, make_org):
    first = _make_user(db_session, make_org("rememberiso1"))
    second = _make_user(db_session, make_org("rememberiso2"))
    owner, cookie = _remember_cookie(app, first.email, _PASSWORD)
    _remember_cookie(app, second.email, _PASSWORD)
    _end(app, "sign_out_everywhere", second, owner)

    assert _open_with_only(app, cookie).status_code == 200


def _set_count(db_session, user, value):
    user.session_generation = value
    db_session.flush()
    db_session.commit()


@pytest.mark.parametrize("kind", ["password_change", "reset", "sign_out_everywhere"])
def test_person_with_no_count_keeps_cookie_then_loses_it_on_change(app, db_session, make_org, kind):
    """A person who existed before the count was added holds NULL, read as 0."""
    org = make_org("remembernull" + kind.replace("_", "")[:6])
    user = _make_user(db_session, org)
    _set_count(db_session, user, None)
    owner, cookie = _remember_cookie(app, user.email, _PASSWORD)

    assert _open_with_only(app, cookie).status_code == 200, "a cookie issued before the count existed must keep working"

    _end(app, kind, user, owner)

    resp = _open_with_only(app, cookie)
    assert resp.status_code in (302, 401), "cookie of a person with no count still signed in after %s" % kind
    if resp.status_code == 302:
        assert "/account/login" in resp.headers["Location"]
    db_session.refresh(user)
    assert user.session_generation == 1


def test_cookie_issued_before_the_column_existed_keeps_working(app, db_session, make_org):
    """Before the count, the cookie named the account by its bare id."""
    org = make_org("rememberlegacy")
    user = _make_user(db_session, org)
    _set_count(db_session, user, None)
    _client, cookie = _remember_cookie(app, user.email, _PASSWORD)
    assert cookie.split("|")[0] == str(user.id)
    assert _open_with_only(app, cookie).status_code == 200
