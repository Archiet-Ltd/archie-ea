"""The CTO opens Reports > CTO Scorecard and reads four headline figures in plain words.

As the CTO of a company holding linked records (applications on vendor products, one product near end of
support, an open exception, an active contract, a decommissioned application that carries a cost): opens the
scorecard from the sidebar, reads the four cards, finds no internal name on the page, sees the annual run cost
the Overview gives, and follows every card to a page with content. A company with no applications is told
what each empty card needs. At 390 wide every card fits the screen.
"""
import datetime as dt
import os
import re
import tempfile
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .test_first_screen_one_message_journey import FOLD, PHONE, _page

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("SCORECARD_WALK_DIR") or tempfile.gettempdir()
CARDS = ("card-supported", "card-exceptions", "card-exposure", "card-disagree")
INTERNAL = ("orm:", "[store-", "—", "ApplicationComponent", "CostFact", "ApplicationCost", "None")


def _seed(org_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.architecture_decision import ArchitectureChangeRequest
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    app = create_app("testing")
    with app.app_context():
        vendor = VendorOrganization(name="Quillon %s" % uuid.uuid4().hex[:6])
        db.session.add(vendor)
        db.session.flush()
        soon = VendorProduct(vendor_organization_id=vendor.id, name="Quill DB 9",
                             end_of_life_date=dt.datetime.utcnow() + dt.timedelta(days=-30))
        fine = VendorProduct(vendor_organization_id=vendor.id, name="Quill Mail 3",
                             end_of_life_date=dt.datetime.utcnow() + dt.timedelta(days=3000))
        db.session.add_all([soon, fine])
        db.session.flush()
        for name, product, status, cost in (("Ledger Core", soon, "operational", 120000),
                                            ("Mail Relay", fine, "operational", 80000),
                                            ("Old Billing", None, "5. decommissioned", 5000)):
            db.session.add(ApplicationComponent(
                name=name, organization_id=org_id, lifecycle_status=status, total_cost_of_ownership=cost,
                vendor_product_id=product.id if product else None))
        db.session.add(VendorContract(vendor_id=vendor.id, organization_id=org_id, status="active",
                                      contract_name="Quillon agreement", contract_number="CS-%s" % uuid.uuid4().hex[:6],
                                      annual_cost=200000, start_date=dt.date.today() - dt.timedelta(days=90)))
        db.session.add(ArchitectureChangeRequest(organization_id=org_id, title="Run Ledger on old database",
                                                 acr_reference="ACR-CTO-1", disposition="exception"))
        db.session.commit()
        db.session.remove()


def _purge(org_id):
    from sqlalchemy import text

    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        for table in ("architecture_change_requests", "vendor_contracts", "application_components"):
            db.session.execute(text("DELETE FROM %s WHERE organization_id = :o" % table), {"o": org_id})
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def cto_org(live_server):
    org = create_fresh_org("cto")
    _seed(org["org_id"])
    yield org
    _purge(org["org_id"])


def _shot(page, name):
    page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _overview_cost(page, live_server):
    page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    value = page.locator('[data-testid="card-run-cost-value"]')
    sentence = page.locator('[data-testid="card-run-cost-sentence"]').inner_text().strip()
    return (value.inner_text().strip() + ". " if value.count() else "") + sentence


def _open_from_sidebar(page, live_server):
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    if page.viewport_size["width"] < 768:
        page.get_by_test_id("sidebar-trigger").click()
    link = page.locator('#sidebar-nav a[href="/architecture/cto-scorecard/"], .sidebar-nav a[href="/architecture/cto-scorecard/"]').locator("visible=true")
    if link.count() == 0:
        page.locator("#sidebar-nav, .sidebar-nav").get_by_text("Reports", exact=True).locator("visible=true").first.click()
    link.first.click()
    page.wait_for_url("**/architecture/cto-scorecard/", timeout=PAGE_TIMEOUT)
    cards = page.locator('[data-testid="scorecard-cards"]')
    assert cards.count() == 1, page.locator("body").inner_text()[:600]


def _assert_plain(page):
    body = page.locator("main").first.inner_text()
    for word in INTERNAL:
        assert word not in body, (word, body)


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
def test_cto_reads_four_cards_in_plain_words_and_every_card_leads_somewhere(browser, live_server, cto_org, viewport):
    context, page = _page(browser, viewport)
    try:
        sign_in(page, live_server, cto_org["emails"]["cto"])
        overview_cost = _overview_cost(page, live_server)
        _open_from_sidebar(page, live_server)
        width = viewport["width"]
        _shot(page, "cto-scorecard-%d-light" % width)
        page.evaluate("document.documentElement.classList.add('dark')")
        _shot(page, "cto-scorecard-%d-dark" % width)
        page.evaluate("document.documentElement.classList.remove('dark')")

        for testid in CARDS:
            assert page.locator('[data-testid="%s"]' % testid).count() == 1, testid
        assert page.locator('[data-testid="card-supported-value"]').inner_text().strip() == "50.0%"
        assert page.locator('[data-testid="card-exceptions-value"]').inner_text().strip() == "1"
        assert page.locator('[data-testid="card-exposure-value"]').inner_text().strip() == "1"
        assert "Quill DB 9" in page.locator('[data-testid="card-exposure-sentence"]').inner_text()
        for testid in CARDS:
            text = page.locator('[data-testid="%s"]' % testid).inner_text()
            assert re.search(r"[A-Za-z]{4,}", text) and not re.search(r"(^|\s)(-|0)(\s|$)", text), (testid, text)
        _assert_plain(page)

        # the annual cost is the Overview's own answer
        statement = page.locator('[data-testid="annual-cost-text"]').inner_text().strip()
        assert statement == overview_cost, (statement, overview_cost)

        # no card or table runs past the screen
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        assert page.evaluate("""[...document.querySelectorAll('[data-testid^="card-"]')]
            .every(c => c.scrollWidth <= c.clientWidth)""")

        # the records behind the cards
        assert "ACR-CTO-1" in page.locator('[data-testid="exceptions-table"]').inner_text()
        assert "Ledger Core" in page.locator('[data-testid="exposure-table"]').inner_text()
        href = page.locator('[data-testid="card-supported"]').get_attribute("href")
        page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "Ledger Core" in page.locator("main").first.inner_text()
        _open_from_sidebar(page, live_server)
        page.locator('[data-testid="exposure-table"] a', has_text="Ledger Core").click()
        page.wait_for_url("**/fact-sheet", timeout=PAGE_TIMEOUT)
        assert "Ledger Core" in page.locator("main").first.inner_text()
        # a card's own anchor lands on the section it names, with its records
        _open_from_sidebar(page, live_server)
        page.locator('[data-testid="card-exceptions"]').click()
        assert page.url.endswith("#open-exceptions")
        assert page.locator('[data-testid="exceptions-table"]').is_visible()
    finally:
        context.close()


def test_company_without_applications_is_told_what_each_card_needs(browser, live_server):
    org = create_fresh_org("cto")
    context, page = _page(browser, FOLD)
    try:
        sign_in(page, live_server, org["emails"]["cto"])
        _open_from_sidebar(page, live_server)
        _shot(page, "cto-scorecard-empty-1440-light")
        for testid in CARDS:
            text = page.locator('[data-testid="%s"]' % testid).inner_text()
            assert len(text.split()) >= 8, (testid, text)
        assert "record its vendor product" in page.locator('[data-testid="card-supported"]').inner_text()
        assert "Record end-of-support dates" in page.locator('[data-testid="card-exposure"]').inner_text()
        assert "Appears here once a contract states its annual cost" in page.locator(
            '[data-testid="annual-cost-text"]').inner_text()
        _assert_plain(page)
    finally:
        context.close()
