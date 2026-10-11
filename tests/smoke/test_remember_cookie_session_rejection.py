"""Round-2 D1: a real browser cookie jar is the only thing that can actually
prove the remember-me redirect-loop regression is fixed.

A pytest-client test (tests/test_session_invalidation.py) covers the same
scenario at the WSGI layer, but that only proves the ``Set-Cookie`` header on
one response looks right. It cannot prove the browser actually drops the
cookie and stops silently re-authenticating on the NEXT navigation -- and
that silent re-authentication is the entire regression: Flask-Login reading
a still-live ``remember_token`` cookie and quietly logging the user back in
with no ``_sid``, which the revocation check then rejects again, forever.
This journey drives a real Chromium cookie jar through exactly that loop.
"""

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login_with_remember(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.check("#remember_me")
    except Exception:
        # Fall back to name-based lookup if the rendered id differs.
        page.check("input[name='remember_me']")
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url, (
        "could not sign in as %s with remember_me - still on the login page" % email
    )


def test_remember_me_session_rejection_does_not_loop(page, live_server, seeded):
    """Log in with 'Keep me logged in', revoke the session server-side the
    way another device/admin/idle-timeout would (NOT via this browser's own
    /account/logout, which already goes through flask-login's normal
    response-building and would not reproduce the bug), then navigate again.

    Pre-fix (D1): session_policy.py's before_request rejection branch called
    logout_user() then session.clear() in that order, which wiped Flask-
    Login's session["_remember"] = "clear" marker before the response was
    built -- so the remember_token cookie was never actually cleared. With a
    remember cookie still live, the browser's NEXT request silently
    re-authenticates via flask-login's remember-cookie path with no _sid,
    which the revocation check then rejects again -- an unbreakable loop.

    Post-fix: the remember cookie must actually be gone from the browser's
    jar after the rejection response, and the following navigation must land
    on the login page rather than silently re-authenticating.
    """
    email = seeded["emails"]["solution_architect"]
    _login_with_remember(page, live_server, email)

    cookies_before = {c["name"]: c for c in page.context.cookies()}
    remember_cookie_name = None
    for name in cookies_before:
        if "remember" in name.lower():
            remember_cookie_name = name
            break
    assert remember_cookie_name is not None, (
        "sanity: logging in with remember_me must set a remember cookie; "
        "cookies were: %s" % list(cookies_before)
    )

    # Revoke the session server-side out-of-band -- simulating logout from
    # another device, an idle timeout, or an admin/GDPR-triggered revocation
    # -- via a direct DB write, so this browser's own cookies are untouched
    # and the NEXT navigation is what exercises session_policy.py's
    # rejection branch under test, not the normal /account/logout route.
    from app import create_app, db as _db
    from app.models.user import User
    from app.models.user_session import UserSession

    revoke_app = create_app("testing")
    with revoke_app.app_context():
        user = User.query.filter_by(email=email).first()
        assert user is not None
        row = (
            UserSession.query.filter_by(user_id=user.id, revoked_at=None)
            .order_by(UserSession.created_at.desc())
            .first()
        )
        assert row is not None, "no active registry row found for the just-logged-in user"
        row.revoked_at = __import__("datetime").datetime.utcnow()
        row.revoked_reason = "logout"
        _db.session.commit()
        _db.session.remove()

    # First navigation after the out-of-band revoke: this is the request
    # that trips session_policy.py's rejection branch (is_active(sid) is now
    # False) and, post-fix, must clear the remember cookie in its response.
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(500)
    assert "/account/login" in page.url, (
        "sanity: navigating with a revoked session must land on the login page, got %s" % page.url
    )

    cookies_after_rejection = {c["name"]: c for c in page.context.cookies()}
    remember_cookie_after = cookies_after_rejection.get(remember_cookie_name)
    assert remember_cookie_after is None, (
        "SECURITY REGRESSION (D1): the %s cookie is still present in the "
        "browser's cookie jar after the session was rejected -- it was not "
        "cleared, which is the exact precondition for the redirect loop." % remember_cookie_name
    )

    # The decisive assertion: navigate AGAIN. A still-live remember cookie
    # would silently re-authenticate the browser here (with no _sid) and
    # land back inside the app, which the revocation check would then
    # reject again -- the unbreakable loop. The fix must send the user to
    # the login page every time, not loop.
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(500)
    assert "/account/login" in page.url, (
        "SECURITY REGRESSION (D1): navigating a second time after the "
        "session was rejected landed on %s instead of the login page -- "
        "the remember cookie silently re-authenticated the browser "
        "(the redirect-loop regression)." % page.url
    )
