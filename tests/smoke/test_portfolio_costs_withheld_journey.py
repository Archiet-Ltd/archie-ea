"""The portfolio, strategic initiatives and business cases print a money value only to a person the cost rule allows.

Written from two goals: an architect scans the initiatives to see what is running and how healthy it is,
and the chief technology officer opens the same pages to see what they cost. The architect reads no amount
and is told once, on each page, why; the chief technology officer reads every figure. Another
organisation's figures are seeded so a leak across organisations shows as a number on the page.
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
# the budgets seeded below, as a page would print them
FIGURES = ("250,000", "250000", "90,000", "123,456", "777,000", "999,999", "555,555")


def _shot(page, name):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


@pytest.fixture(scope="module")
def estate(seeded, live_server):
    """One initiative, one strategic initiative and one business case with money, in the seeded organisation,
    and the same three with other money in a second organisation."""
    from app import create_app, db
    from app.models.business_case import BusinessCase
    from app.models.organization import Organization
    from app.models.strategic import StrategicInitiative
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    org_id = seeded["ids"]["org"]
    with app.app_context():
        other = Organization(name="Other Org %s" % suffix, slug="other-%s" % suffix)
        db.session.add(other)
        db.session.flush()
        mine = EnterpriseInitiative(name="Cloud move %s" % suffix, organization_id=org_id,
                                    approved_budget=250000, spent_to_date=90000, status="active")
        theirs = EnterpriseInitiative(name="Foreign move %s" % suffix, organization_id=other.id,
                                      approved_budget=999999, spent_to_date=555555, status="active")
        strategic = StrategicInitiative(name="Platform bet %s" % suffix, organization_id=org_id,
                                        status="in_progress", budget_allocated=123456, budget_spent=1000)
        case = BusinessCase(title="Data centre exit %s" % suffix, organization_id=org_id, tco_3yr=777000)
        db.session.add_all([mine, theirs, strategic, case])
        db.session.commit()
        return {"initiative": mine.id, "initiative_name": mine.name, "strategic_name": strategic.name,
                "case_name": case.title}


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
    assert not re.search(r"[£$€]\s?\d", text), "%s printed an amount: %s" % (where, re.search(r"[£$€]\s?\d.{0,12}", text).group(0))


@pytest.mark.parametrize("label,width,height,dark", VIEWS)
def test_an_architect_reads_the_portfolio_pages_without_an_amount(browser, live_server, seeded, estate, label, width, height, dark):
    ctx, page = _open(browser, live_server, seeded["emails"]["enterprise_architect"], width, height, dark)
    try:
        page.goto(live_server + "/portfolio/", wait_until="domcontentloaded")
        expect(page.get_by_role("link", name=estate["initiative_name"])).to_be_visible()
        text = _main_text(page)
        assert text.count(WITHHELD) == 1, "list: the sentence is said once, not %d times" % text.count(WITHHELD)
        _assert_no_figure(text, "the portfolio list")
        assert "Foreign move" not in text
        _shot(page, "portfolio-list-withheld-" + label)

        with page.expect_navigation():
            page.get_by_role("link", name=estate["initiative_name"]).click()
        expect(page.get_by_role("heading", name=estate["initiative_name"]).first).to_be_visible()
        text = _main_text(page)
        assert text.count(WITHHELD) == 1, "detail: the sentence is said once, not %d times" % text.count(WITHHELD)
        _assert_no_figure(text, "the initiative page")
        _shot(page, "portfolio-detail-withheld-" + label)

        page.goto(live_server + "/enterprise/strategic/initiatives", wait_until="domcontentloaded")
        expect(page.get_by_text(estate["strategic_name"])).to_be_visible()
        text = _main_text(page)
        assert text.count(WITHHELD) == 1, "initiatives: the sentence is said once, not %d times" % text.count(WITHHELD)
        _assert_no_figure(text, "the strategic initiatives")
        _shot(page, "strategic-initiatives-withheld-" + label)

        page.goto(live_server + "/business-case/", wait_until="domcontentloaded")
        expect(page.get_by_role("link", name=estate["case_name"])).to_be_visible()
        text = _main_text(page)
        assert text.count(WITHHELD) == 1, "business cases: the sentence is said once, not %d times" % text.count(WITHHELD)
        _assert_no_figure(text, "the business case list")
        _shot(page, "business-cases-withheld-" + label)
    finally:
        ctx.close()


@pytest.mark.parametrize("label,width,height,dark", VIEWS)
def test_the_chief_technology_officer_reads_the_same_pages_with_their_figures(browser, live_server, seeded, estate, label, width, height, dark):
    ctx, page = _open(browser, live_server, seeded["emails"]["cto"], width, height, dark)
    try:
        page.goto(live_server + "/portfolio/", wait_until="domcontentloaded")
        row = page.locator("tr", has_text=estate["initiative_name"])
        expect(row).to_contain_text("250,000")
        expect(row).to_contain_text("90,000")
        assert WITHHELD not in _main_text(page)
        assert "999,999" not in _main_text(page)
        _shot(page, "portfolio-list-allowed-" + label)

        with page.expect_navigation():
            page.get_by_role("link", name=estate["initiative_name"]).click()
        expect(page.get_by_text("250,000").first).to_be_visible()
        assert WITHHELD not in _main_text(page)

        page.goto(live_server + "/enterprise/strategic/initiatives", wait_until="domcontentloaded")
        expect(page.get_by_text("123,456").first).to_be_visible()
        assert WITHHELD not in _main_text(page)

        page.goto(live_server + "/business-case/", wait_until="domcontentloaded")
        expect(page.locator("tr", has_text=estate["case_name"])).to_contain_text("777,000")
        assert WITHHELD not in _main_text(page)
    finally:
        ctx.close()
