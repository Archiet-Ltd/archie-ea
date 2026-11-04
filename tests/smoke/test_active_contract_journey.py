"""An Administrator checks that the Overview's monthly contract cost is the contracts the list calls active.

The organisation reports in pounds. Its contracts are recorded as the demonstration company records them: each
vendor-supplied system carries one active contract with no end date stating the system's own annual cost, and
one more pound contract with no end date sits on no system. A euro contract with no end date states €24,000 a
year. One contract marked active ended ten days ago and one was terminated; neither is active. A second
organisation's contract never appears.

The Administrator filters the contracts list to the active ones and finds four, the euro one under the euro
sign. The Vendors list gives the euro vendor its contract and says it is not added, never "No contract". The Overview's monthly figure is the pound contracts' annual cost over twelve, and its run-cost card names
the three pound contracts and states the euro one apart, so the two screens count the same four. The
Administrator is not offered an Edit link, because editing a contract belongs to procurement, and the edit page
refuses them; the procurement lead's Edit link opens the contract's form.
"""
import os
import re
import uuid
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("ACTIVE_CONTRACT_WALK_DIR")

SYSTEMS = (("Ledger", "Northwind", 36000.0), ("Payroll", "Harbour", 18000.0))
LOOSE_POUNDS = 12000.0
EUROS = 24000.0


@pytest.fixture(scope="module")
def estate(ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.services.application_cost_accessor import state_contract

    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        today = date.today()
        org = Organization(name=f"Lantern {tag}", slug=f"lantern-{tag}")
        rival = Organization(name=f"Rival {tag}", slug=f"rival-{tag}")
        db.session.add_all([org, rival])
        db.session.flush()

        def user(org_id, kind, role, admin=False):
            u = User(email=f"act-{kind}-{tag}@example.com", first_name="Act", last_name=kind.title(),
                     organization_id=org_id, enterprise_role=role, confirmed=True)
            u.password = PASSWORD
            db.session.add(u)
            db.session.commit()
            if admin:
                u.grant_org_admin()
                db.session.commit()
            return u.email

        emails = {"admin": user(org.id, "admin", "enterprise_architect", admin=True),
                  "buyer": user(org.id, "buyer", "procurement")}
        vendors = {}
        for name in ("Northwind", "Harbour", "Oracle", "Rival"):
            vendors[name] = VendorOrganization(name=f"{name} {tag}")
            db.session.add(vendors[name])
        db.session.flush()
        for name, vendor, cost in SYSTEMS:
            system = ApplicationComponent(name=f"{name} {tag}", organization_id=org.id,
                                          lifecycle_status="operational", total_cost_of_ownership=cost,
                                          vendor_organization_id=vendors[vendor].id)
            db.session.add(system)
            db.session.flush()
            # the demonstration seed's own call: an active contract, no end date, the system's cost
            state_contract(system, f"{vendor} {tag}", cost)

        def contract(name, org_id, vendor, cost, currency="GBP", status="active", end=None):
            db.session.add(VendorContract(contract_name=f"{name} {tag}", organization_id=org_id,
                                          vendor_id=vendors[vendor].id, annual_cost=cost, currency=currency,
                                          status=status, start_date=today - timedelta(days=200), end_date=end))

        contract("Support desk", org.id, "Northwind", LOOSE_POUNDS)
        contract("Oracle database", org.id, "Oracle", EUROS, currency="EUR")
        contract("Old hosting", org.id, "Harbour", 9000.0, end=today - timedelta(days=10))
        contract("Cancelled analytics", org.id, "Harbour", 7000.0, status="terminated")
        contract("Rival deal", rival.id, "Rival", 777000.0)
        db.session.commit()
    return {"tag": tag, **emails}


def _text(locator):
    return re.sub(r"\s+", " ", locator.inner_text())


def _shot(page, name):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def _active_rows(page, base):
    page.goto(base + "/procurement/contracts", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("contracts-status-filter").locator("select").select_option("active")
    page.wait_for_url(re.compile(r"status=active"), timeout=PAGE_TIMEOUT)
    rows = page.get_by_test_id("contract-row")
    expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
    return rows


def test_the_active_list_shows_four_contracts_with_the_euro_one_under_its_own_sign(browser, live_server, estate):
    tag = estate["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["admin"])
        rows = _active_rows(page, live_server)
        names = sorted(_text(r.get_by_test_id("contract-link")) for r in rows.all())
        assert names == sorted([f"Northwind {tag} agreement for Ledger {tag}", f"Harbour {tag} agreement for Payroll {tag}",
                                f"Support desk {tag}", f"Oracle database {tag}"]), names
        euro_row = _text(rows.filter(has_text=f"Oracle database {tag}"))
        assert "€24,000.00" in euro_row and "£24,000" not in euro_row, euro_row
        assert "£36,000.00" in _text(rows.filter(has_text=f"Ledger {tag}"))
        assert "Rival" not in _text(page.locator("main"))
        # the Administrator is not offered an edit they may not make, and the edit page agrees
        assert page.get_by_test_id("contract-edit").count() == 0
        _shot(page, "contracts-active-1440-light")
        rows.filter(has_text=f"Oracle database {tag}").get_by_test_id("contract-link").click()
        page.wait_for_url(re.compile(r"/procurement/contracts/\d+$"), timeout=PAGE_TIMEOUT)
        detail = _text(page.locator("main"))
        assert f"Oracle database {tag}" in detail and "€24,000.00" in detail
        assert page.get_by_role("link", name="Edit contract").count() == 0
        edit = page.request.get(page.url + "/edit")
        assert edit.status == 403, edit.status
    finally:
        page.close()


def test_the_overview_and_spend_count_the_same_four_contracts(browser, live_server, estate):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["admin"])
        assert page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT).status == 200
        monthly = page.get_by_test_id("monthly-cost-value")
        expect(monthly).to_be_visible(timeout=PAGE_TIMEOUT)
        pounds = sum(cost for _, _, cost in SYSTEMS) + LOOSE_POUNDS
        assert int(re.sub(r"[^0-9]", "", monthly.get_attribute("title"))) == round(pounds / 12), monthly.get_attribute("title")
        card = _text(page.get_by_test_id("card-run-cost-sentence"))
        counted = re.search(r"(\d+) active contracts?\b", card)
        apart = re.search(r"(\d+) active contracts? in EUR (?:is|are) not added: €24,000(?:\.00)? a year", card)
        assert counted and apart, card
        assert int(counted.group(1)) + int(apart.group(1)) == 4, card
        _shot(page, "overview-1440-light")
        # Spend states the euro contract apart as well, and its tile counts all four
        assert page.goto(live_server + "/procurement/spend", timeout=PAGE_TIMEOUT).status == 200
        assert re.search(r"Not added: 1 active contract states €24,000(?:\.00)? a year in EUR",
                         _text(page.locator("main")))
        tile = _text(page.get_by_test_id("spend-active-contracts"))
        assert re.match(r"^4 Active contracts\b", tile) and "1 stated in another currency and not added" in tile, tile
        _shot(page, "spend-1440-light")
    finally:
        page.close()


def test_the_vendors_list_never_reads_the_euro_vendor_as_having_no_contract(browser, live_server, estate):
    tag = estate["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["admin"])
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        row = page.get_by_test_id("vendor-row").filter(has_text=f"Oracle {tag}")
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        text = _text(row)
        assert "No contract" not in text, text
        assert "Not added: stated in EUR, and this organisation reports in GBP" in text, text
        assert re.search(r"\b1\b", text), text
        _shot(page, "vendors-1440-light")
        # the reason wraps inside its cell: the table does not scroll sideways and no cell clips its words
        clipped = page.evaluate("""() => {
            const table = document.querySelector('[data-testid="vendor-row"]').closest('table');
            let box = table.parentElement;
            while (box && box !== document.body && !/(auto|scroll)/.test(getComputedStyle(box).overflowX)) box = box.parentElement;
            const sideways = box && box !== document.body ? box.scrollWidth - box.clientWidth : 0;
            const cells = [...table.querySelectorAll('td')].filter(c => /Not added|£|No contract/.test(c.innerText) && !c.querySelector('a')).filter(c => c.scrollWidth > c.clientWidth + 1).map(c => c.innerText);
            const last = [...document.querySelectorAll('[data-testid="vendor-row"]')].map(r => r.lastElementChild.getBoundingClientRect().right);
            return {sideways, cells, page: document.documentElement.scrollWidth - window.innerWidth, off: Math.max(...last) - window.innerWidth};
        }""")
        assert clipped["sideways"] <= 1 and not clipped["cells"] and clipped["page"] <= 1 and clipped["off"] <= 1, clipped
        for vendor, cost in (("Northwind", "£48,000"), ("Harbour", "£18,000")):
            cell = _text(page.get_by_test_id("vendor-row").filter(has_text=f"{vendor} {tag}"))
            assert cost in cell, cell
        row.get_by_test_id("vendor-link").click()
        page.wait_for_url(re.compile(r"/vendors/\d+$"), timeout=PAGE_TIMEOUT)
        detail = _text(page.locator("main"))
        assert f"Oracle database {tag}" in detail and "€24,000.00 a year" in detail, detail
        assert "Not added: stated in EUR" in detail
        _shot(page, "vendor-1440-light")
        # the ended contract is dated the way its neighbour is
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        page.get_by_test_id("vendor-row").filter(has_text=f"Harbour {tag}").get_by_test_id("vendor-link").click()
        page.wait_for_url(re.compile(r"/vendors/\d+$"), timeout=PAGE_TIMEOUT)
        ended = (date.today() - timedelta(days=10))
        harbour = _text(page.locator("main"))
        assert f"the contract ended on {ended.strftime('%d %b %Y')}" in harbour, harbour
        assert ended.isoformat() not in harbour
    finally:
        page.close()


def test_the_buyer_edit_link_opens_the_euro_contract_form(browser, live_server, estate):
    tag = estate["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["buyer"])
        rows = _active_rows(page, live_server)
        row = rows.filter(has_text=f"Oracle database {tag}")
        row.get_by_role("button", name=f"Actions for Oracle database {tag}").click()
        edit = page.get_by_role("menuitem", name="Edit contract")
        expect(edit).to_be_visible(timeout=PAGE_TIMEOUT)
        edit.click()
        page.wait_for_url(re.compile(r"/procurement/contracts/\d+/edit$"), timeout=PAGE_TIMEOUT)
        expect(page.locator("#contract_name")).to_have_value(f"Oracle database {tag}")
        expect(page.locator("select#currency")).to_have_value("EUR")
    finally:
        page.close()


def test_the_narrow_dark_screens_hold_the_same_figures(browser, live_server, estate):
    tag = estate["tag"]
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        _login(page, live_server, estate["admin"])
        rows = _active_rows(page, live_server)
        assert rows.count() == 4
        assert "€24,000.00" in _text(rows.filter(has_text=f"Oracle database {tag}"))
        _shot(page, "contracts-active-390-dark")
        assert page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT).status == 200
        monthly = page.get_by_test_id("monthly-cost-value")
        expect(monthly).to_be_visible(timeout=PAGE_TIMEOUT)
        assert monthly.inner_text().strip() == "£5.5k"
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        _shot(page, "overview-390-dark")
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        row = page.get_by_test_id("vendor-row").filter(has_text=f"Oracle {tag}")
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "No contract" not in _text(row) and "Not added: stated in EUR" in _text(row)
        _shot(page, "vendors-390-dark")
        row.get_by_test_id("vendor-link").click()
        page.wait_for_url(re.compile(r"/vendors/\d+$"), timeout=PAGE_TIMEOUT)
        assert f"Oracle database {tag}" in _text(page.locator("main"))
        _shot(page, "vendor-390-dark")
        assert page.goto(live_server + "/procurement/spend", timeout=PAGE_TIMEOUT).status == 200
        assert "1 stated in another currency and not added" in _text(page.get_by_test_id("spend-active-contracts"))
        _shot(page, "spend-390-dark")
    finally:
        page.close()
