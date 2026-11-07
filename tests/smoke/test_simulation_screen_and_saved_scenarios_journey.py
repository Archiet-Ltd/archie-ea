"""A signed-in architect reaches the simulation screen, and a scenario they save is kept for the organisation.

In the Lantern Quay Systems demonstration organisation, with the first three waves of the product released:
- as the architect, opens /simulations and gets the simulation screen, not the waiting list; the service
  status page then lists Scenarios as available and that link lands on the same screen, while a visitor
  to the same page still reads it under Coming soon;
- names a scenario in the save dialog (the button is announced by its full name), saves it, and a colleague
  of the same organisation, signing in on another browser, finds it under Compare Scenarios with the
  words the architect picked; a person in another organisation finds the list empty and told why.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server
from .fresh_org import create_fresh_org, sign_in
from .test_acc7sim_journey import DEMO_EMAIL, ELEMENT, VIEWPORT_IDS, VIEWPORTS, _pick, demo  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

COLLEAGUE_EMAIL = "second.architect@lantern-quay.example.com"
SCENARIO = "Retire the ledger for the shared review"


@pytest.fixture(scope="module")
def server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": "3"})


def _shot(page, name, viewport, scheme):
    walk = os.environ.get("SMOKE_WALK_DIR")
    if walk:
        os.makedirs(walk, exist_ok=True)
        page.screenshot(path=os.path.join(walk, "%s-%d-%s.png" % (name, viewport["width"], scheme)), full_page=True)


def _context(browser, viewport, scheme):
    context = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context


def _sign_in_as(context, server, email):
    page = context.new_page()
    page.on("dialog", lambda dialog: pytest.fail("a browser dialog opened: " + dialog.message))
    page.goto(server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    return page


def _inside_viewport(page, locator, viewport):
    box = locator.bounding_box()
    assert box is not None, "not rendered"
    assert box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"] + 1, box
    assert page.evaluate("document.documentElement.scrollWidth") <= viewport["width"] + 1


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_the_status_page_lists_scenarios_as_available_and_the_link_opens_the_screen(browser, server, demo, viewport, scheme):
    context = _context(browser, viewport, scheme)
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("early-access-page").count() == 0, "the waiting list is showing to a signed-in person"
        assert page.get_by_label("Email").count() == 0 and page.get_by_test_id("early-access-submit").count() == 0
        expect(page.get_by_role("heading", level=1)).to_have_text("Simulations")
        _shot(page, "simulations-screen", viewport, scheme)

        page.goto(server + "/status", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        footer = page.locator("footer")
        soon = footer.locator('[data-footer-column="coming-soon"]')
        assert soon.get_by_text("Scenarios").count() == 0, "Scenarios is still listed as coming soon to a signed-in person"
        link = footer.locator('[data-footer-column="product"]').get_by_role("link", name=re.compile("^Scenarios"))
        expect(link).to_have_attribute("href", "/simulations")
        link.scroll_into_view_if_needed()
        _inside_viewport(page, link, viewport)
        _shot(page, "status-scenarios", viewport, scheme)
        link.click()
        page.wait_for_url(re.compile(r"/simulations$"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", level=1)).to_have_text("Simulations")
        expect(page.get_by_role("button", name="Run simulation")).to_be_visible()
    finally:
        context.close()

    visitor = _context(browser, viewport, scheme)
    try:
        page = visitor.new_page()
        page.goto(server + "/status", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        soon = page.locator('footer [data-footer-column="coming-soon"]')
        expect(soon.get_by_text("Scenarios")).to_be_visible()
        page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("early-access-page")).to_be_visible()
    finally:
        visitor.close()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_a_saved_scenario_is_listed_for_a_colleague_and_not_for_another_organisation(browser, server, app, demo, viewport, scheme):
    from app import db
    from app.models.saved_scenario import SavedScenario
    from app.models.user import User

    with app.app_context():
        from app.models.user import Role

        org_id = db.session.execute(db.text("select id from organizations where slug = 'lantern-quay'")).scalar_one()
        colleague = User.query.filter_by(email=COLLEAGUE_EMAIL).first()
        if colleague is None:
            colleague = User(email=COLLEAGUE_EMAIL, first_name="Second", last_name="Architect", organization_id=org_id,
                             confirmed=True, enterprise_role="enterprise_architect")
            colleague.role = Role.query.filter_by(name="Architect").one()
            db.session.add(colleague)
        colleague.password = PASSWORD
        db.session.commit()
        db.session.remove()

    architect_ctx = _context(browser, viewport, scheme)
    try:
        page = _sign_in_as(architect_ctx, server, DEMO_EMAIL)
        page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _pick(page, change="Decommission / Retirement")
        page.get_by_role("button", name="Run simulation").click()
        expect(page.get_by_test_id("simulation-panel")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Save Scenario for Comparison", exact=True).click()
        dialog = page.get_by_role("dialog", name="Save scenario")
        dialog.get_by_label("Scenario name").fill(SCENARIO)
        dialog.get_by_role("button", name="Save scenario").click()
        expect(dialog).to_be_hidden()

        with app.app_context():
            rows = SavedScenario.query.filter_by(name=SCENARIO).all()
            org_id = db.session.execute(db.text("select id from organizations where slug = 'lantern-quay'")).scalar_one()
            assert [(r.organization_id, r.element_name, r.change_label) for r in rows] == \
                [(org_id, ELEMENT, "Decommission / Retirement")]
            db.session.remove()

        # a colleague signing in on another browser finds it, with the words the architect picked
        colleague_ctx = _context(browser, viewport, scheme)
        try:
            colleague = _sign_in_as(colleague_ctx, server, COLLEAGUE_EMAIL)
            colleague.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            colleague.get_by_role("button", name="Compare Scenarios").click()
            compare = colleague.get_by_role("dialog", name="Compare Impact Scenarios")
            expect(compare).to_be_visible()
            expect(compare.get_by_text(SCENARIO)).to_be_visible()
            compare.get_by_text(SCENARIO).click()
            expect(compare.get_by_text("Decommission / Retirement").first).to_be_visible()
            expect(compare.get_by_text(ELEMENT).first).to_be_visible()
            _shot(colleague, "colleague-compare", viewport, scheme)
        finally:
            colleague_ctx.close()

        # a person in another organisation finds none of it, and is told why
        other = create_fresh_org("enterprise_architect")
        other_ctx = _context(browser, viewport, scheme)
        try:
            outsider = other_ctx.new_page()
            sign_in(outsider, server, other["emails"]["enterprise_architect"])
            outsider.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            outsider.get_by_role("button", name="Compare Scenarios").click()
            compare = outsider.get_by_role("dialog", name="Compare Impact Scenarios")
            expect(compare.get_by_text("No Saved Scenarios")).to_be_visible()
            assert compare.get_by_text(SCENARIO).count() == 0
            _shot(outsider, "other-organisation-compare", viewport, scheme)
        finally:
            other_ctx.close()
    finally:
        architect_ctx.close()
        with app.app_context():
            SavedScenario.query.filter_by(name=SCENARIO).delete()
            db.session.commit()
            db.session.remove()
