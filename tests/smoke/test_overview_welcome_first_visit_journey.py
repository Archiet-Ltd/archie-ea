"""The Overview greets a first visit differently from a return (round welcome01).

A brand-new account's first signed-in visit to the Overview reads "Welcome, <name>"; after
signing out and signing back in, the same account reads "Welcome back, <name>". Decided from
the signed-in user's own ``user_sessions`` rows -- no new column, no new migration.
"""
import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_first_screen_one_message_journey import FOLD, PHONE, _page
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("WELCOME01_WALK_DIR")


def _new_account():
    """A brand-new organisation with one person, Sam, who has never signed in."""
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Welcome Org %s" % suffix, slug="welcome-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        org_id = org.id
        email = "sam.%s@example.com" % suffix
        user = User(
            email=email,
            first_name="Sam",
            last_name="Starter",
            organization_id=org_id,
            enterprise_role="enterprise_architect",
            confirmed=True,
            is_org_admin=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        db.session.remove()
    return email, org_id


def _shot(page, name, width, theme):
    if WALK:
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, theme, width)))


@pytest.mark.parametrize("viewport,theme", [(FOLD, "light"), (PHONE, "dark")], ids=["1440-light", "390-dark"])
def test_overview_greets_a_first_visit_and_a_return_differently(browser, live_server, viewport, theme):
    email, _org_id = _new_account()
    context, page = _page(browser, viewport)
    try:
        # first ever sign-in: the welcome card reads "Welcome, Sam", never "Welcome back"
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        dash = page.locator('[data-testid="overview-dashboard"]')
        expect(dash).to_be_visible()
        expect(dash.get_by_text("Welcome, Sam")).to_be_visible()
        assert dash.get_by_text("Welcome back, Sam").count() == 0
        _shot(page, "welcome01-first-visit", viewport["width"], theme)

        # sign out, sign back in: the same account now reads "Welcome back, Sam"
        page.goto(live_server + "/account/logout", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        dash = page.locator('[data-testid="overview-dashboard"]')
        expect(dash).to_be_visible()
        expect(dash.get_by_text("Welcome back, Sam")).to_be_visible()
        assert dash.get_by_text("Welcome, Sam").count() == 0
        _shot(page, "welcome01-return-visit", viewport["width"], theme)
    finally:
        context.close()
