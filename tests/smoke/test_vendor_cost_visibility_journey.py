"""Who reads what a vendor costs is one rule, the Costs page's, on every surface of the Vendors page.

A finance lead and a compliance lead of the same seeded organisation open Vendors. Finance reads
the annual total, a cost in both cost columns of the costed vendor, a contract cost that agrees with
the Costs page, and nothing from a second organisation that names one of the same vendors.
Compliance reads no total, "Not shown for your role" in every row, and no cost from the register API.
"""
import os
import re
import uuid
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, _seed_standard_org
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("VENDOR_COST_WALK_DIR")


@pytest.fixture(scope="module")
def cost_world(request, ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.organization import Organization
    from app.models.vendor.vendor_organization import VendorOrganization

    seeded = _seed_standard_org(request, ai_protocol_stub)
    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]

        def system(name, vendor=None, cost=None, org=org_id):
            row = ApplicationComponent(name=name, organization_id=org, vendor_name=vendor,
                                       total_cost_of_ownership=cost, lifecycle_status="operational")
            db.session.add(row)
            db.session.commit()
            return row.id

        ids = {
            "sf1": system(f"Sales Cloud {tag}", f"Salesforce {tag}", 12000),
            "sf2": system(f"Service Cloud {tag}", f"Salesforce {tag}", 3000),
            "okta": system(f"Workforce Identity {tag}", f"Okta {tag}"),
            "dd": system(f"Telemetry {tag}", f"Datadog {tag}", 2000),
        }
        datadog = VendorOrganization.query.filter_by(name=f"Datadog {tag}").one()
        db.session.add(VendorContract(
            contract_name=f"Datadog monitoring {tag}", organization_id=org_id, vendor_id=datadog.id,
            application_id=ids["dd"], start_date=date.today() - timedelta(days=330),
            end_date=date.today() + timedelta(days=25), annual_cost=2000.0, status="active"))
        other = Organization(name=f"Other {tag}", slug=f"other-{tag}")
        db.session.add(other)
        db.session.commit()
        system(f"Their CRM {tag}", f"Okta {tag}", 999, org=other.id)
        system(f"Their Secret {tag}", f"OnlyTheirs {tag}", 4444, org=other.id)
    return {"seeded": seeded, "tag": tag, "ids": ids, "app": app, "org_id": org_id}


def _text(page):
    return re.sub(r"\s+", " ", page.locator("main, body").first.inner_text())


def _amount(text):
    """The number in a money string, whichever form the page uses (2,000.00 or 2k)."""
    found = re.search(r"([\d,]+(?:\.\d+)?)\s*([kKmM])?\b", text)
    assert found, text
    value = float(found.group(1).replace(",", ""))
    return value * {"k": 1e3, "m": 1e6}.get((found.group(2) or "").lower(), 1)


def _shot(page, name):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def test_finance_reads_every_vendor_cost_and_the_contract_figure_agrees_with_the_costs_page(
        browser, live_server, cost_world):
    tag = cost_world["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, cost_world["seeded"]["emails"]["finance"])
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200

        # The total across this organisation's vendors, and no figure from the other organisation.
        answer = page.get_by_test_id("vendors-answer")
        expect(answer).to_contain_text("Annual cost across vendors:", timeout=PAGE_TIMEOUT)
        assert _amount(answer.inner_text().split("Annual cost across vendors:")[1]) == 17000.0
        rows = page.get_by_test_id("vendor-row")
        expect(rows).to_have_count(3)
        body = _text(page)
        assert "Not shown for your role" not in body
        assert "OnlyTheirs" not in body and "999" not in body and "4,444" not in body
        expect(rows.filter(has_text="Salesforce %s" % tag)).to_contain_text("15,000")
        expect(rows.filter(has_text="Okta %s" % tag)).to_contain_text("No cost recorded")
        _shot(page, "finance-vendors-1440-light")

        # Both cost columns of the costed vendor carry its figure: the systems' cost and the contract's.
        datadog = rows.filter(has_text="Datadog %s" % tag)
        cells = [re.sub(r"\s+", " ", c).strip() for c in datadog.locator("td").all_inner_texts()]
        money_cells = [c for c in cells if "2,000" in c]
        assert len(money_cells) == 2, cells
        contract_cost = _amount(money_cells[1])
        assert contract_cost == 2000.0

        # The Costs page states the same contract figure for the same vendor.
        assert page.goto(live_server + "/procurement/spend", timeout=PAGE_TIMEOUT).status == 200
        spend_row = page.get_by_test_id("spend-vendor-row").filter(has_text="Datadog %s" % tag)
        expect(spend_row).to_be_visible(timeout=PAGE_TIMEOUT)
        stated = spend_row.inner_text().split(tag, 1)[1]
        assert "a year" in stated
        assert _amount(stated) == contract_cost
        _shot(page, "finance-costs-1440-light")
    finally:
        page.close()


def test_compliance_reads_no_vendor_cost_on_the_page_or_from_the_api(browser, live_server, cost_world):
    tag = cost_world["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, cost_world["seeded"]["emails"]["compliance"])
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        answer = page.get_by_test_id("vendors-answer")
        expect(answer).to_contain_text("3 vendors supply", timeout=PAGE_TIMEOUT)
        assert "Annual cost across vendors" not in answer.inner_text()
        rows = page.get_by_test_id("vendor-row")
        expect(rows).to_have_count(3)
        for i in range(3):
            expect(rows.nth(i)).to_contain_text("Not shown for your role")
        body = _text(page)
        for figure in ("15,000", "12,000", "3,000", "2,000", "999", "4,444"):
            assert figure not in body, figure
        _shot(page, "compliance-vendors-1440-light")

        # The register API the page reads says no more than the page does.
        reply = page.request.get(live_server + "/api/vendors/register?per_page=100")
        assert reply.status == 200
        mine = [v for v in reply.json()["vendors"] if tag in v["name"]]
        assert len(mine) == 3
        for vendor in mine:
            assert vendor["annual_cost"] is None and vendor["cost_visible"] is False, vendor
    finally:
        page.close()


def test_the_refused_view_fits_a_phone(browser, live_server, cost_world):
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        _login(page, live_server, cost_world["seeded"]["emails"]["compliance"])
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        expect(page.get_by_test_id("vendor-row").first).to_contain_text(
            "Not shown for your role", timeout=PAGE_TIMEOUT)
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        _shot(page, "compliance-vendors-390-dark")
    finally:
        page.close()
