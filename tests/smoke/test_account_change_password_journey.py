"""Account settings: change-password is a real write path with no prior coverage.

Login/logout are already exercised implicitly by every persona fixture in
conftest.py's `_login` helper, and account creation/confirmation is a
separate onboarding flow out of scope here. But /account/manage/change-password
is a genuine, previously-untested write: this proves the new password is the
one that actually works by logging out and back in with it, not merely that
the form round-tripped with a flashed message.

This test rotates its subject's password by design (that is the whole point
of the assertion), so it must never run against a persona any other smoke
test relies on: `seeded` is session-scoped, and there is no cleanup step that
could safely restore a password without depending on ordering. Instead this
test creates its own disposable user, following the same
`User(...); user.password = PASSWORD` construction `seeded` uses in
conftest.py, so rotating this user's password can never poison another
test's sign-in.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _create_disposable_user(organization_id):
    """A user that belongs to no other test - safe to rotate the password of.

    Mirrors tests/smoke/conftest.py's `seeded` per-archetype user creation
    (same app-context/create_app("testing") pattern, same
    `User(...); user.password = PASSWORD` construction) but is never added to
    `seeded['emails']`, so no other test file's `_login` helper can ever be
    handed this user's (about to be rotated) credentials.

    Returns (email, user_id) so the caller can register a teardown that
    removes this user from the shared smoke organisation.
    """
    from app import create_app, db
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        architect_role = Role.query.filter_by(name="Architect").one()
        suffix = uuid.uuid4().hex[:8]
        email = "smoke.password-change.%s@example.com" % suffix
        user = User(
            email=email, first_name="Smoke", last_name="PasswordChange",
            organization_id=organization_id, enterprise_role="business_architect",
            confirmed=True,
        )
        user.role = architect_role
        user.is_platform_admin = False
        user.is_org_admin = False
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        return email, user.id


def _remove_disposable_user(user_id, email):
    """Deletes the user `_create_disposable_user` added to the shared smoke org.

    This journey logs the disposable user in twice and out once, and
    authentication auditing records each event in soc2_audit_log with a
    NOT-NULLable-in-practice foreign key to users.id -- deleting the user
    first raises IntegrityError. Those rows are this disposable user's own
    login/logout events and nothing else's audit trail, so removing them
    ahead of the user, scoped strictly by this user_id, is safe.

    Otherwise follows the same query-count-assert-delete-assert pattern as
    the `remove_protocol_provider` finalizer in
    tests/smoke/conftest.py:335-347, so the shared smoke organisation
    doesn't accumulate an extra member per run.
    """
    from app import create_app, db
    from app.models.audit_log import AuditLog
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        db.session.remove()
        AuditLog.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        db.session.commit()

        query = User.query.filter_by(id=user_id, email=email)
        assert query.count() == 1, "Disposable password-change user was unexpectedly changed"
        assert query.delete(synchronize_session=False) == 1
        db.session.commit()
        assert User.query.filter_by(id=user_id).count() == 0
        db.session.remove()


def test_change_password_and_relogin_with_new_password(browser, live_server, seeded, request):
    new_password = "SmokeJourneyRotated!2026"
    email, disposable_user_id = _create_disposable_user(seeded['ids']['org'])
    request.addfinalizer(lambda: _remove_disposable_user(disposable_user_id, email))

    page = browser.new_page()
    try:
        _login(page, live_server, email)

        response = page.goto(live_server + '/account/manage/change-password', timeout=PAGE_TIMEOUT)
        assert response.status == 200

        page.get_by_label('Old password', exact=True).fill(PASSWORD)
        page.get_by_label('New password', exact=True).fill(new_password)
        page.get_by_label('Confirm new password', exact=True).fill(new_password)
        page.get_by_role('button', name='Update password', exact=True).click()

        # A successful change redirects to main.index with a flashed message;
        # a validation/business-rule failure re-renders the same form.
        page.wait_for_load_state('domcontentloaded', timeout=PAGE_TIMEOUT)
        assert '/account/manage/change-password' not in page.url, (
            'Password change did not redirect away -- the form likely failed validation'
        )

        # ---- Log out, then prove the NEW password (not the old one) works ----
        page.goto(live_server + '/account/logout', timeout=PAGE_TIMEOUT)
        page.locator('[data-testid="logout-confirm"]').click()
        page.wait_for_url('**/account/login**', timeout=PAGE_TIMEOUT)
        page.goto(live_server + '/account/login', wait_until='domcontentloaded', timeout=PAGE_TIMEOUT)
        page.fill('#email', email)
        page.fill('#password', new_password)
        page.locator('#submit').dispatch_event('click')
        page.wait_for_url(lambda url: '/account/login' not in url, timeout=PAGE_TIMEOUT)
        assert '/account/login' not in page.url
    finally:
        page.close()
