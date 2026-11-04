"""A CTO reads the scorecard, trusts each figure, follows every link, then places a
technology on the radar and sees it saved."""

import os
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _shot(page, name):
    folder = os.environ.get("CTO01_SHOTS")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.screenshot(path=os.path.join(folder, name + ".png"), full_page=True)


def _seed_estate(org_id):
    """Two applications on technology products, one past end of support and one
    ending inside the window, through the product's own models."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.application_portfolio import ApplicationComponent
        from app.models.architecture_decision import ArchitectureChangeRequest
        from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

        vendor = VendorOrganization(name="Scorecard Vendor %s" % org_id)
        db.session.add(vendor)
        db.session.flush()
        names = {}
        for key, days in (("expired", -30), ("soon", 120)):
            product = VendorProduct(vendor_organization_id=vendor.id, name="Runtime %s" % key,
                                    end_of_life_date=datetime.utcnow() + timedelta(days=days))
            db.session.add(product)
            db.session.flush()
            app_row = ApplicationComponent(name="App on %s runtime" % key, organization_id=org_id,
                                           vendor_product_id=product.id)
            db.session.add(app_row)
            names[key] = app_row.name
        exc = ArchitectureChangeRequest(organization_id=org_id, title="Scorecard exception",
                                        acr_reference="ACR-C-%d" % org_id, disposition="exception",
                                        raised_at=datetime.utcnow() - timedelta(days=12))
        db.session.add(exc)
        db.session.commit()
        out = {"names": names, "exception": exc.id}
        db.session.remove()
    return out


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_scorecard_figures_are_explained_and_the_exception_escalates(width, scheme, browser, live_server):
    org = create_fresh_org("cto")
    seed = _seed_estate(org["org_id"])
    ctx = browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=scheme)
    ctx.add_init_script("try { localStorage.setItem('archie_onboarding_ts', '1'); } catch (e) {}")
    page = ctx.new_page()
    try:
        sign_in(page, live_server, org["emails"]["cto"])
        page.goto(live_server + "/architecture/cto-scorecard/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        share = page.get_by_test_id("supported-version-share")
        expect(share).to_contain_text("50.0%")
        expect(share).to_contain_text("1 of 2 applications on a supported technology version")
        expect(page.get_by_test_id("end-of-support-exposure")).to_contain_text("Runtime soon")
        expect(page.get_by_test_id("end-of-support-exposure")).to_contain_text(seed["names"]["soon"])
        row = page.get_by_test_id("exception-%d" % seed["exception"])
        expect(row).to_contain_text("12 days open")
        # Every panel sits in the library card, and the escalate control is the library button.
        assert page.locator('main [data-slot="card"]').count() >= 4
        escalate = row.get_by_role("button", name="Escalate")
        expect(escalate).to_have_attribute("data-slot", "button")
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"), "overflow"
        _shot(page, "C-scorecard-%d-%s" % (width, scheme))
        escalate.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("escalated-badge")).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        ctx.close()


def test_empty_scorecard_explains_every_figure_and_offers_the_next_step(browser, live_server):
    org = create_fresh_org("cto")
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("try { localStorage.setItem('archie_onboarding_ts', '1'); } catch (e) {}")
    page = ctx.new_page()
    try:
        sign_in(page, live_server, org["emails"]["cto"])
        page.goto(live_server + "/architecture/cto-scorecard/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        share = page.get_by_test_id("supported-version-share")
        text = share.inner_text()
        assert "No application has a vendor product recorded yet" in text
        assert "—" not in text and "&mdash;" not in text, "a bare dash stands where the figure should be explained"
        step = share.get_by_test_id("supported-version-next-step")
        assert step.count() == 1
        step.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/cto-scorecard" not in page.url
        assert page.locator("main").inner_text().strip(), "the next step lands on an empty page"
    finally:
        ctx.close()


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_cto_places_a_technology_and_sees_it_saved(width, scheme, browser, live_server, seeded):
    element_id = seeded["ids"]["radar_element"]
    ctx = browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=scheme)
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["cto"])
        page.goto(live_server + "/technology/radar/%d/place" % element_id, wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        form = page.get_by_test_id("radar-classify-form-%d" % element_id)
        expect(form).to_be_visible()
        assert form.locator('input[type="hidden"][data-slot="input"]').count() >= 2, "hidden fields are library inputs"
        fonts = page.evaluate("() => ['radar-review-input', 'radar-initiative'].map(id => getComputedStyle(document.querySelector('label[for=' + id + ']')).fontFamily)")
        assert fonts[0] == fonts[1], "the initiative field is set in a different face from its neighbours: %r" % fonts
        form.locator('select[name="ring"]').select_option("trial")
        form.locator('textarea[name="rationale"]').fill("Two teams already run it in production")
        _shot(page, "C-classify-%d-%s" % (width, scheme))
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("radar-classify-%d" % element_id).click()
        expect(page.get_by_test_id("radar-ring-%d" % element_id)).to_have_text("Trial", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("radar-rationale-%d" % element_id)).to_contain_text(
            "Two teams already run it in production")
        page.goto(live_server + "/technology/radar/%d/place" % element_id, wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        expect(page.locator('select[name="ring"]')).to_have_value("trial")
        expect(page.locator('textarea[name="rationale"]')).to_have_value("Two teams already run it in production")
        expect(page.get_by_test_id("radar-same-type-%d" % element_id)).to_be_visible()
    finally:
        ctx.close()
