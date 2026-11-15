"""A person whose session ended is told why on the sign-in page, and signing out
needs a deliberate click.

Sam signs in, a page fetch of the Sign out address (what a crawler or prefetch
does) leaves Sam signed in, Sam's session is then revoked and the next page
shows the sign-in page with a plain sentence, and finally Sam signs out from the
account menu and reads the same sentence.
"""
import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_first_screen_one_message_journey import FOLD, PHONE, _page
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("LOGIN_REASON_WALK_DIR")
SENTENCE = "You were signed out."


def _new_account():
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Reason Org %s" % suffix, slug="reason-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        email = "sam.%s@example.com" % suffix
        user = User(
            email=email,
            first_name="Sam",
            last_name="Starter",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
            is_org_admin=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        db.session.remove()
    return email, user_id


def _revoke_sessions(user_id):
    from app import create_app, db
    from app.services import session_registry

    app = create_app("testing")
    with app.app_context():
        session_registry.revoke_all_for_user(user_id, "admin_revoked")
        db.session.commit()
        db.session.remove()


def _shot(page, name, width, theme):
    if WALK:
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, theme, width)))


def _theme(page, theme):
    page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)


@pytest.mark.parametrize("viewport,theme", [(FOLD, "light"), (PHONE, "dark")], ids=["1440-light", "390-dark"])
def test_revoked_session_is_explained_on_the_sign_in_page(browser, live_server, viewport, theme):
    email, user_id = _new_account()
    context, page = _page(browser, viewport)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        _revoke_sessions(user_id)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert "/account/login" in page.url and "timeout=revoked" in page.url
        _theme(page, theme)
        why = page.locator('[data-testid="login-why"]')
        expect(why).to_be_visible()
        expect(why).to_have_text(SENTENCE)
        expect(page.locator("#email")).to_be_visible()
        _shot(page, "loginwhy-revoked", viewport["width"], theme)
    finally:
        context.close()


@pytest.mark.parametrize("viewport,theme", [(FOLD, "light"), (PHONE, "dark")], ids=["1440-light", "390-dark"])
def test_fetching_the_sign_out_address_does_not_sign_out_but_the_button_does(browser, live_server, viewport, theme):
    email, _user_id = _new_account()
    context, page = _page(browser, viewport)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)

        # What a crawler or a prefetch does: fetch the address. The person stays signed in.
        fetched = page.request.get(live_server + "/account/logout")
        assert fetched.status == 200
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert "/account/login" not in page.url
        expect(page.locator('[data-testid="overview-dashboard"]')).to_be_visible()

        # A person following the link is asked, and can decline.
        page.goto(live_server + "/account/logout", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _theme(page, theme)
        expect(page.get_by_role("heading", name="Sign out")).to_be_visible()
        _shot(page, "loginwhy-confirm", viewport["width"], theme)
        page.get_by_role("link", name="Stay signed in").click()
        page.wait_for_load_state("domcontentloaded")
        assert "/account/logout" not in page.url and "/account/login" not in page.url
        expect(page.locator('[data-testid="overview-dashboard"]')).to_be_visible()

        # The account menu's Sign out is a form: one click signs out and says so.
        if viewport["width"] < 768:
            page.get_by_test_id("sidebar-trigger").click()
        page.locator('[data-testid="user-menu"]').first.click()
        page.locator('[data-testid="user-menu-sign-out"]').click()
        page.wait_for_url("**/account/login**", timeout=PAGE_TIMEOUT)
        _theme(page, theme)
        expect(page.locator('[data-testid="login-why"]')).to_have_text(SENTENCE)
        _shot(page, "loginwhy-signed-out", viewport["width"], theme)

        # The session is really over: the Overview sends the person back to sign in.
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/account/login" in page.url
    finally:
        context.close()
