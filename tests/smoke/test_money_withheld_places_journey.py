"""Business cases, the capability map, the timeline and the application pickers hold back money from some roles.

Written from two goals: an architect checks what a business case says, where the capabilities stand, when work
lands and which applications to consolidate, and reads no amount of money and the reason once on each page; the
chief technology officer opens the same pages for what they cost and reads every figure. Another organisation's
figures are seeded so a leak across organisations shows as a number on a page.
"""

import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WITHHELD = "Cost figures are withheld for your role."
WALK = os.environ.get("SMOKE_SCREENSHOT_DIR")
VIEWS = (("1440-light", 1440, 900, False), ("390-dark", 390, 844, True))
FIGURES = ("731519", "731,519", "268431", "268,431", "905163", "905,163", "374821", "374,821", "482917", "482,917",
           "619247", "619,247", "558833", "884422", "620K", "619K")


def _shot(page, name):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


@pytest.fixture(scope="module")
def estate(seeded, live_server):
    """One business case, capability, work package, application and consolidation entry with money in the
    seeded organisation, and a business case and work package with other money in a second organisation."""
    from datetime import datetime

    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_case import BusinessCase
    from app.models.organization import Organization
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.services.application_cost_accessor import set_annual_cost

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    org_id = seeded["ids"]["org"]
    with app.app_context():
        other = Organization(name="Other Org %s" % suffix, slug="other-%s" % suffix)
        db.session.add(other)
        db.session.flush()
        case = BusinessCase(title="Data centre exit %s" % suffix, organization_id=org_id, tco_3yr=731519, capex=268431,
                            financial_benefit_annual=905163, roi_percentage=41, payback_months=19)
        foreign_case = BusinessCase(title="Foreign case %s" % suffix, organization_id=other.id, tco_3yr=558833)
        capability = UnifiedCapability(name="Payments %s" % suffix, organization_id=org_id, level=1,
                                       annual_cost=619247, investment_required=620000)
        package = UnifiedWorkPackage(name="Cutover %s" % suffix, organization_id=org_id, estimated_cost=374821,
                                     start_date=datetime(2026, 3, 1), end_date=datetime(2026, 9, 1), status="planned")
        foreign_package = UnifiedWorkPackage(name="Foreign cutover %s" % suffix, organization_id=other.id,
                                             estimated_cost=884422, start_date=datetime(2026, 3, 1),
                                             end_date=datetime(2026, 9, 1), status="planned")
        application = ApplicationComponent(name="Ledger %s" % suffix, organization_id=org_id)
        db.session.add_all([case, foreign_case, capability, package, foreign_package, application])
        db.session.flush()
        set_annual_cost(application, 482917)
        db.session.commit()
        return {"case": case.title, "case_id": case.id, "capability": capability.name, "package": package.name,
                "application": application.name, "foreign": ("Foreign case", "Foreign cutover")}


def _open(browser, live_server, email, width, height, dark):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    if dark:
        ctx.add_init_script("document.documentElement.classList.add('dark');")
    page = ctx.new_page()
    page.goto(live_server + "/account/login", wait_until="domcontentloaded")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.click("#submit", no_wait_after=True)
    page.wait_for_url(lambda url: "/account/login" not in url)
    return ctx, page


def _main_text(page):
    return page.locator("main").inner_text()


def _assert_no_figure(text, where):
    for figure in FIGURES:
        assert figure not in text, "%s printed %s to a person the rule withholds from" % (where, figure)
    found = re.search(r"[£$€]\s?\d", text)
    assert not found, "%s printed an amount: %s" % (where, found and text[found.start():found.start() + 12])


@pytest.mark.parametrize("label,width,height,dark", VIEWS)
def test_an_architect_reads_the_places_without_an_amount(browser, live_server, seeded, estate, label, width, height, dark):
    ctx, page = _open(browser, live_server, seeded["emails"]["enterprise_architect"], width, height, dark)
    try:
        # the business case: from the list to the document
        page.goto(live_server + "/business-case/", wait_until="domcontentloaded")
        with page.expect_navigation():
            page.get_by_role("link", name=estate["case"]).click()
        expect(page.get_by_role("heading", name=estate["case"]).first).to_be_visible()
        text = _main_text(page)
        assert text.count(WITHHELD) == 1, "business case: the sentence is said once, not %d times" % text.count(WITHHELD)
        _assert_no_figure(text, "the business case")
        assert page.locator("#bc-capex, #bc-tco, #bc-benefit, #bc-roi, #bc-npv").count() == 0
        _assert_no_figure(page.content(), "the business case source")
        _shot(page, "business-case-withheld-" + label)

        feed = page.request.get(live_server + "/business-case/api/%d" % estate["case_id"])
        assert feed.ok
        body = feed.json()["data"]
        assert body["title"] == estate["case"]
        assert all(body[key] is None for key in ("tco_3yr", "capex", "opex_annual", "financial_benefit_annual", "npv"))

        # the capability map: no investment lens, no investment column, no amount in the detail panel
        page.goto(live_server + "/capability-map/?lens=investment", wait_until="domcontentloaded")
        expect(page.get_by_text(estate["capability"]).first).to_be_visible()
        text = _main_text(page)
        assert text.count(WITHHELD) == 1, "capability map: the sentence is said once, not %d times" % text.count(WITHHELD)
        assert page.get_by_role("button", name="Investment", exact=True).count() == 0
        assert page.get_by_role("button", name="Maturity", exact=True).get_attribute("aria-pressed") == "true"
        _assert_no_figure(text, "the capability map")
        _shot(page, "capability-map-withheld-" + label)
        page.goto(live_server + "/capability-map/?view=list", wait_until="domcontentloaded")
        expect(page.get_by_text(estate["capability"]).first).to_be_visible()
        assert page.get_by_role("columnheader", name="Investment").count() == 0
        _assert_no_figure(_main_text(page), "the capability list")

        # the work-package timeline
        page.goto(live_server + "/gantt-demo", wait_until="domcontentloaded")
        expect(page.get_by_text(estate["package"]).first).to_be_visible()
        text = _main_text(page)
        assert text.count(WITHHELD) == 1, "timeline: the sentence is said once, not %d times" % text.count(WITHHELD)
        _assert_no_figure(text, "the timeline")
        assert estate["foreign"][1] not in page.content()
        _shot(page, "timeline-withheld-" + label)

        # the applications list
        page.goto(live_server + "/applications/", wait_until="domcontentloaded")
        expect(page.get_by_role("link", name=estate["application"]).first).to_be_visible()
        text = _main_text(page)
        assert text.count(WITHHELD) == 1, "applications: the sentence is said once, not %d times" % text.count(WITHHELD)
        _assert_no_figure(text, "the applications list")
        _shot(page, "applications-withheld-" + label)

        # the Add Applications picker on the consolidation list
        page.goto(live_server + "/consolidation-list/", wait_until="domcontentloaded")
        page.get_by_role("button", name="Add Applications").first.click()
        modal = page.locator("#add-applications-modal")
        expect(modal.get_by_text(estate["application"]).first).to_be_visible()
        text = modal.inner_text()
        assert text.count(WITHHELD) == 1, "picker: the sentence is said once, not %d times" % text.count(WITHHELD)
        assert "Cost Range" not in text
        _assert_no_figure(text, "the Add Applications picker")
        _shot(page, "add-applications-withheld-" + label)
    finally:
        ctx.close()


@pytest.mark.parametrize("label,width,height,dark", VIEWS)
def test_the_chief_technology_officer_reads_the_same_places_with_their_figures(browser, live_server, seeded, estate, label, width, height, dark):
    ctx, page = _open(browser, live_server, seeded["emails"]["cto"], width, height, dark)
    try:
        page.goto(live_server + "/business-case/", wait_until="domcontentloaded")
        with page.expect_navigation():
            page.get_by_role("link", name=estate["case"]).click()
        expect(page.locator("#bc-tco")).to_have_value("731519.00")
        expect(page.locator("#bc-capex")).to_have_value("268431.00")
        assert WITHHELD not in _main_text(page)
        assert "Foreign case" not in page.content()
        _shot(page, "business-case-allowed-" + label)

        page.goto(live_server + "/capability-map/?lens=investment", wait_until="domcontentloaded")
        expect(page.get_by_text(estate["capability"]).first).to_be_visible()
        expect(page.get_by_text("£619K").first).to_be_visible()
        assert WITHHELD not in _main_text(page)
        _shot(page, "capability-map-allowed-" + label)

        page.goto(live_server + "/gantt-demo", wait_until="domcontentloaded")
        expect(page.get_by_text(estate["package"]).first).to_be_visible()
        assert WITHHELD not in _main_text(page)
        with page.expect_download() as download:
            page.get_by_role("button", name="Export", exact=False).first.click()
            page.get_by_role("button", name="Export to CSV").click()
        csv_text = Path(download.value.path()).read_text()
        assert csv_text.splitlines()[0].endswith('"Cost"') and "374821" in csv_text
        assert "884422" not in csv_text

        page.goto(live_server + "/consolidation-list/", wait_until="domcontentloaded")
        page.get_by_role("button", name="Add Applications").first.click()
        modal = page.locator("#add-applications-modal")
        expect(modal.get_by_text("TCO: £482,917")).to_be_visible()
        expect(modal.get_by_text("Cost Range")).to_be_visible()
        assert WITHHELD not in modal.inner_text()
        _shot(page, "add-applications-allowed-" + label)
    finally:
        ctx.close()
