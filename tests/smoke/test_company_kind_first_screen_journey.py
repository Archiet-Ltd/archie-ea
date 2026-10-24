"""A new workspace owner picks a company kind and loads the sample company with real clicks.

As an architect in a fresh organisation: opens the first screen, clicks Scale-up and is told
it is saved, reloads and finds it kept, opens Account settings and finds the same choice there
(the same component, the same store), changes it there and sees the first screen follow. A
second organisation keeps its own answer. "Load sample" is a button that posts and lands on a
populated answer page, never on a "405 Method Not Allowed" page. Neither page logs a script
error or shows a search error nobody asked for.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_try_sample_company_journey import _empty_organisation_user as _cto_organisation, _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _empty_organisation_user(live_server):
    """A fresh organisation whose one user is an enterprise architect (who may load the sample)."""
    email, org_id = _cto_organisation(live_server)
    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        user.enterprise_role = "enterprise_architect"
        db.session.commit()
    return email, org_id


def _stored_kind(org_id):
    from app import create_app, db
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        org = db.session.get(Organization, org_id)
        return ((org.settings or {}).get("company_context") or {}).get("kind")


def _new_page(browser, base, email, viewport):
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    _login(page, base, email)
    return context, page, errors


def _selected(page, kind):
    return page.locator('[data-testid="company-kind-%s"]' % kind).get_attribute("aria-pressed") == "true"


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}], ids=["1440", "390"])
def test_company_kind_saves_and_is_one_component(browser, live_server, seeded, viewport):
    email, org_id = _empty_organisation_user(live_server)
    other_email, other_org_id = _empty_organisation_user(live_server)
    context, page, errors = _new_page(browser, live_server, email, viewport)
    try:
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        block = page.locator('[data-testid="company-kind-selector"]')
        expect(block).to_be_visible()
        assert _selected(page, "startup")

        page.locator('[data-testid="company-kind-scaleup"]').click()
        expect(page.locator('[data-testid="company-kind-status"]')).to_contain_text("Saved", timeout=PAGE_TIMEOUT)
        assert _selected(page, "scaleup") and not _selected(page, "startup")
        assert _stored_kind(org_id) == "scaleup"

        page.reload(wait_until="networkidle")
        assert _selected(page, "scaleup")

        # Account settings shows the same choice and changes the same store
        page.goto(live_server + "/account/manage", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        card = page.locator('[data-testid="company-kind-card"]')
        expect(card).to_be_visible()
        assert _selected(page, "scaleup")
        page.locator('[data-testid="company-kind-enterprise"]').click()
        expect(page.locator('[data-testid="company-kind-status"]')).to_contain_text("Enterprise", timeout=PAGE_TIMEOUT)
        assert _stored_kind(org_id) == "enterprise"
        # nobody searched: no search error on screen
        expect(page.get_by_text("Search is unavailable")).to_be_hidden()

        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert _selected(page, "enterprise")

        # the other organisation keeps its own answer
        assert _stored_kind(other_org_id) in (None, "startup")

        assert not errors, errors
    finally:
        context.close()


def test_load_sample_is_a_real_click_and_lands_on_content(browser, live_server, seeded):
    email, org_id = _empty_organisation_user(live_server)
    context, page, errors = _new_page(browser, live_server, email, {"width": 1440, "height": 900})
    try:
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Load sample").click()
        page.wait_for_url("**/intelligence/ask**", timeout=PAGE_TIMEOUT * 2)
        expect(page.get_by_text("405", exact=False)).to_have_count(0)
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("You are looking at the sample company")).to_be_visible()
        # the link beside it lands on a page listing what was loaded
        page.get_by_role("link", name="Remove it when you are ready").click()
        page.wait_for_url("**/restore-points/**", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT * 3)
        expect(page.locator('[data-testid="restore-summary"]')).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Payments service", exact=False).first).to_be_visible()
    finally:
        context.close()
