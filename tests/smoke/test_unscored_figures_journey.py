"""A procurement lead reads a vendor whose readiness was never scored, and is not told it is 0%.

The organisation holds two vendors through the systems that name them: one carries an enterprise
readiness score, the other has never been scored. The vendor register opens each vendor's record. The
scored vendor reads its score and a bar; the unscored vendor says "Not scored" and never shows a
"0%" that a measured zero would also show. The solutions list and the vendor's own system links are
followed to pages that hold the thing clicked.
"""
import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, _seed_standard_org
from .test_archetype_journeys import _login

SHOTS = os.environ.get("UNSCORED_SHOTS")

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def vendor_world(request, ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.vendor.vendor_organization import VendorOrganization

    seeded = _seed_standard_org(request, ai_protocol_stub)
    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        for name, vendor in ((f"Ledger {tag}", f"Scored Vendor {tag}"), (f"Rota {tag}", f"Unscored Vendor {tag}")):
            db.session.add(ApplicationComponent(name=name, organization_id=org_id, vendor_name=vendor,
                                                total_cost_of_ownership=1000, lifecycle_status="operational"))
        db.session.commit()
        for vendor, score in ((f"Scored Vendor {tag}", 72), (f"Unscored Vendor {tag}", None)):
            row = VendorOrganization.query.filter_by(name=vendor).one()
            row.enterprise_readiness_score = score
        db.session.commit()
    return {"seeded": seeded, "tag": tag}


def _readiness(page):
    block = page.locator("dt", has_text="Enterprise Readiness").locator("xpath=following-sibling::dd[1]")
    return re.sub(r"\s+", " ", block.inner_text()).strip()


def _open_vendor(page, base, name):
    page.goto(base + "/applications/vendors", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name=name).first.click()
    expect(page).to_have_url(re.compile(r"/applications/vendors/\d+"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name=name).first).to_be_visible(timeout=PAGE_TIMEOUT)


def _shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.locator("dt", has_text="Enterprise Readiness").scroll_into_view_if_needed()
        page.screenshot(path=str(Path(SHOTS) / (name + ".png")))


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_unscored_vendor_is_not_drawn_as_zero(browser, live_server, vendor_world, viewport):
    tag, email = vendor_world["tag"], vendor_world["seeded"]["emails"]["procurement"]
    page = browser.new_page(viewport=viewport)
    try:
        _login(page, live_server, email)

        _open_vendor(page, live_server, "Scored Vendor %s" % tag)
        assert _readiness(page) == "72%"
        _shot(page, "scored-%d" % viewport["width"])

        _open_vendor(page, live_server, "Unscored Vendor %s" % tag)
        reading = _readiness(page)
        assert reading == "Not scored", reading
        assert "0%" not in reading
        _shot(page, "unscored-%d" % viewport["width"])
        # The vendor's system link still lands on the system's own record.
        page.locator("#vendor-systems").get_by_role("link", name="Rota %s" % tag).click()
        expect(page.get_by_role("heading", name="Rota %s" % tag).first).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        page.close()
