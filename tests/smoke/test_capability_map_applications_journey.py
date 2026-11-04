"""A CTO opens the capability map, opens a capability with two mapped applications and sees both names.

Goal: "which applications run this capability?" The Applications count on the tile and the list in
the drawer must agree, and each application link must land on that application's page.
"""

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id, code, cap_name, app_names):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.application_layer import ApplicationComponent
        from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
        from app.models.unified_capability import UnifiedCapability

        cap = UnifiedCapability(
            name=cap_name, code=code, level=1, capability_type="strategic",
            organization_id=org_id, scope="tenant",
        )
        db.session.add(cap)
        db.session.flush()
        for name in app_names:
            comp = ApplicationComponent(name=name, lifecycle_status="active", organization_id=org_id)
            db.session.add(comp)
            db.session.flush()
            db.session.add(UnifiedApplicationCapabilityMapping(
                application_component_id=comp.id, unified_capability_id=cap.id,
                support_level="full", coverage_percentage=80,
            ))
        db.session.commit()
        db.session.remove()


@pytest.mark.parametrize("viewport,scheme", [({"width": 1440, "height": 900}, "light"),
                                             ({"width": 390, "height": 844}, "dark")])
def test_capability_drawer_lists_the_mapped_applications(browser, live_server, viewport, scheme):
    org = create_fresh_org("cto", org_admin=True)
    # Another organisation owns a capability with the same code and its own application.
    other = create_fresh_org("cto", org_admin=True)
    _seed(org["org_id"], "PAY-1", "Payments Processing", ["Billing Engine", "Ledger Service"])
    _seed(other["org_id"], "PAY-1", "Payments Processing", ["Other Org Secret App"])

    page = browser.new_page(viewport=viewport, color_scheme=scheme)
    try:
        sign_in(page, live_server, org["emails"]["cto"])
        page.goto(live_server + "/capability-map/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        tile = page.locator('[role="tabpanel"][aria-label="Map view"] [role="button"]',
                            has_text="Payments Processing")
        expect(tile).to_have_count(1, timeout=PAGE_TIMEOUT)
        tile.click()
        drawer = page.locator('[role="dialog"][aria-labelledby="drawer-title-capability-detail"]')
        expect(drawer).to_be_visible()
        expect(drawer).to_contain_text("Supporting applications (2)")
        expect(drawer).to_contain_text("Billing Engine")
        expect(drawer).to_contain_text("Ledger Service")
        expect(drawer).not_to_contain_text("Other Org Secret App")
        link = drawer.get_by_role("link", name="Billing Engine")
        href = link.first.get_attribute("href")
        landed = page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert landed.status == 200
        expect(page.locator("body")).to_contain_text("Billing Engine")
    finally:
        page.close()
