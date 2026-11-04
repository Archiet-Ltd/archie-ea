"""A procurement lead whose only active contract is in euros opens Costs and is told once why it adds nothing.

The organisation reports in pounds and holds one active contract of EUR 1,200 a year. Costs has nothing to
total, so its empty card says one contract is not added, gives the reason once (stated in EUR, and the
organisation reports in GBP), and states the amount it left out. The card does not repeat the reason in a second
sentence, and its link opens the contracts page where the contract can be read.
"""
import os
import re
import uuid
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, _seed_standard_org
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("SPEND_OTHER_CURRENCY_WALK_DIR")


@pytest.fixture(scope="module")
def euro_only(request, ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models.application_portfolio import VendorContract
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.vendor.vendor_organization import VendorOrganization

    _seed_standard_org(request, ai_protocol_stub)
    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        org = Organization(name=f"Euro only {tag}", slug=f"euro-only-{tag}")
        db.session.add(org)
        db.session.flush()
        buyer = User(email=f"euro-only-{tag}@example.com", first_name="Euro", last_name="Buyer",
                     organization_id=org.id, enterprise_role="procurement", confirmed=True)
        buyer.password = PASSWORD
        vendor = VendorOrganization(name=f"Euro vendor {tag}")
        db.session.add_all([buyer, vendor])
        db.session.flush()
        db.session.add(VendorContract(
            contract_name=f"Euro deal {tag}", organization_id=org.id, vendor_id=vendor.id, annual_cost=1200.0,
            currency="EUR", status="active", start_date=date.today() - timedelta(days=30),
            end_date=date.today() + timedelta(days=300)))
        db.session.commit()
        return {"tag": tag, "email": buyer.email, "reporting": org.reporting_currency}


def _text(page):
    return re.sub(r"\s+", " ", page.locator("main, body").first.inner_text())


def test_costs_states_why_an_euro_only_organisation_has_nothing_to_total_once(browser, live_server, euro_only):
    tag = euro_only["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, euro_only["email"])
        assert page.goto(live_server + "/procurement/spend", timeout=PAGE_TIMEOUT).status == 200
        card = page.get_by_test_id("spend-empty")
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        if WALK_DIR:
            os.makedirs(WALK_DIR, exist_ok=True)
            page.screenshot(path=os.path.join(WALK_DIR, "costs-euro-only-1440-light.png"), full_page=True)
        text = re.sub(r"\s+", " ", card.inner_text())

        assert "1 active vendor contract not added" in text
        reason = "Stated in EUR, and this organisation reports in GBP."
        assert text.count(reason) == 1, text
        assert len(re.findall(r"Not added: 1 active contract states €1,200(?:\.00)? a year in EUR\.", text)) == 1, text
        assert "stated in a currency other than" not in text, "the old second sentence is still there"
        assert text.lower().count("in eur") == 2, "EUR is named by the reason and by the amount line, nowhere else: " + text

        card.get_by_role("link", name="Go to contracts").click()
        page.wait_for_url(re.compile(r"/procurement/contracts"), timeout=PAGE_TIMEOUT)
        assert f"Euro deal {tag}" in _text(page), "the link must land where the left-out contract can be read"
    finally:
        page.close()
