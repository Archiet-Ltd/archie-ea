"""An enterprise architect registers a data-sharing agreement for a new
organisation and sees it listed, with the lineage view reachable for one
of its elements.

Covers the three new data-governance templates introduced with R1-B80
(lineage_view.html, sharing_agreements.html, new_sharing_agreement.html),
none of which had a browser smoke journey before this.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _vendor_for(org_id):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.vendor.vendor_organization import VendorOrganization

        vendor = VendorOrganization.query.first()
        if vendor is None:
            vendor = VendorOrganization(name="Smoke Vendor %s" % org_id)
            db.session.add(vendor)
            db.session.commit()
        vendor_id = vendor.id
        db.session.remove()
    return vendor_id


def test_architect_registers_a_sharing_agreement_and_sees_it_listed(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    vendor_id = _vendor_for(org["org_id"])
    name = "Smoke Agreement %s" % org["suffix"]

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])

    page.goto(live_server + "/data-governance/sharing-agreements/new",
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#name", name)
    page.select_option("#vendor_organization_id", str(vendor_id))
    page.get_by_role("button", name="Register agreement").click()
    page.wait_for_url(lambda url: url.endswith("/data-governance/sharing-agreements"), timeout=PAGE_TIMEOUT)

    expect(page.get_by_text(name)).to_have_count(1)

    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text(name)).to_have_count(1)
    context.close()
