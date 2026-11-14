"""A procurement lead finds the contract on a retired system that is still active, on every screen that counts renewals.

One vendor holds a contract on a live system and one on a decommissioned system, both due in 20 days; a second
vendor holds one more. Renewals reads 3, Vendors reads "3 contracts with 2 vendors renew within 90 days", the
vendor page lists the retired-system contract and says the system is retired, the Renewals row says the same, and
its link opens the contract page, which says it too. Checked at 1440 light and 390 dark with no sideways scroll.
"""
import os
import re
import uuid
from datetime import date, timedelta

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD, _seed_standard_org
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("RETIRED_RENEWAL_WALK_DIR")
NOTE = "The system is retired; the contract is still active. Renew or end it."


@pytest.fixture(scope="module")
def estate(request, ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.user import User
    from app.models.vendor.vendor_organization import VendorOrganization

    seeded = _seed_standard_org(request, ai_protocol_stub)
    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        today = date.today()
        email = f"ret-{tag}@example.com"
        buyer = User(email=email, first_name="Retired", last_name="Buyer",
                     organization_id=org_id, enterprise_role="procurement", confirmed=True)
        buyer.password = PASSWORD
        db.session.add(buyer)
        db.session.commit()
        buyer.grant_org_admin()
        db.session.commit()
        first = VendorOrganization(name=f"Holder {tag}")
        second = VendorOrganization(name=f"Single {tag}")
        db.session.add_all([first, second])
        db.session.flush()
        for key, vendor, state in (("live", first, "operational"), ("retired", first, "5. decommissioned"),
                                   ("single", second, "operational")):
            system = ApplicationComponent(name=f"System {key} {tag}", organization_id=org_id,
                                          lifecycle_status=state, vendor_organization_id=vendor.id)
            db.session.add(system)
            db.session.flush()
            db.session.add(VendorContract(
                contract_name=f"Contract {key} {tag}", organization_id=org_id, vendor_id=vendor.id,
                application_id=system.id, annual_cost=1000.0, currency="GBP", status="active",
                start_date=today - timedelta(days=300), renewal_date=today + timedelta(days=20),
                end_date=today + timedelta(days=200)))
        db.session.commit()
        holder_id = first.id
    return {"tag": tag, "email": email, "holder_id": holder_id}


def _shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name + ".png"), full_page=True)


def _words(locator):
    return re.sub(r"\s+", " ", locator.inner_text())


@pytest.mark.parametrize("width,height,scheme", [(1440, 900, "light"), (390, 844, "dark")])
def test_the_contract_on_a_retired_system_is_counted_and_explained_from_vendors_to_its_contract(
        browser, live_server, estate, width, height, scheme):
    tag = estate["tag"]
    page = browser.new_page(viewport={"width": width, "height": height}, color_scheme=scheme)
    try:
        _login(page, live_server, estate["email"])
        suffix = "%d-%s" % (width, scheme)

        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        answer = _words(page.get_by_test_id("vendors-answer"))
        assert "3 contracts with 2 vendors renew within 90 days" in answer, answer
        assert "An active contract on one still renews: 1 of the renewals above." in answer, answer
        _shot(page, "vendors-" + suffix)
        page.get_by_test_id("vendor-row").filter(has_text="Holder " + tag).get_by_test_id("vendor-link").click()
        page.wait_for_url(re.compile(r"/vendors?/\d+"), timeout=PAGE_TIMEOUT)
        vendor = _words(page.locator("main"))
        assert "Contract retired " + tag in vendor and "Contract live " + tag in vendor, vendor
        assert NOTE in vendor and "next renewal" in vendor, vendor
        _shot(page, "vendor-" + suffix)

        assert page.goto(live_server + "/procurement/renewals?days=90", timeout=PAGE_TIMEOUT).status == 200
        rows = page.get_by_test_id("renewal-row")
        assert rows.count() == 3, _words(page.locator("main"))
        retired = rows.filter(has_text="Contract retired " + tag)
        assert NOTE in _words(retired), _words(retired)
        assert page.get_by_test_id("renewal-system-retired").count() == 1
        assert page.get_by_test_id("renewal-system-retired").evaluate(
            "(el) => getComputedStyle(el).maxWidth") != "none", "the retired note is not width-limited"
        _shot(page, "renewals-" + suffix)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

        retired.get_by_test_id("renewal-contract-link").click()
        page.wait_for_url(re.compile(r"/procurement/contracts/\d+"), timeout=PAGE_TIMEOUT)
        contract = _words(page.locator("main"))
        assert "Contract retired " + tag in contract and "20 days until renewal" in contract, contract
        assert NOTE in contract, contract
        _shot(page, "contract-" + suffix)
    finally:
        page.close()
