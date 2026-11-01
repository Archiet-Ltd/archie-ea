"""A person is never offered a capability that was retired into another.

The goal: an application manager adds a work package to an application roadmap
and, separately, starts a vendor analysis. In both places the capability choices
list the kept capability and not the copy that was retired into it, while the
retired capability's own record still opens. Checked at desktop and phone width.
"""


import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = "/home/ubuntu/verify/seats/providx01d/walk"
WIDTHS = [("1440", 1440, 1000), ("390", 390, 844)]


@pytest.fixture(scope="module")
def world():
    """One organisation holding a kept capability and a copy retired into it."""
    org = create_fresh_org("application_manager", org_admin=True)
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_capability import UnifiedCapability

    tag = org["suffix"]
    app = create_app("testing")
    with app.app_context():
        component = ApplicationComponent(
            name="Billing Hub %s" % tag, organization_id=org["org_id"],
            lifecycle_status="operational", description="Invoicing.",
        )
        kept = UnifiedCapability(
            name="Invoice handling %s" % tag, level=1, code="IH%s" % tag,
            organization_id=org["org_id"],
        )
        db.session.add_all([component, kept])
        db.session.flush()
        retired = UnifiedCapability(
            name="Invoice handling (old) %s" % tag, level=1, code="IO%s" % tag,
            organization_id=org["org_id"], retired_into_id=kept.id,
        )
        db.session.add(retired)
        db.session.commit()
        org.update(app_id=component.id, kept_name=kept.name, retired_name=retired.name,
                   retired_id=retired.id)
        db.session.remove()
    return org


@pytest.mark.parametrize("label,width,height", WIDTHS)
def test_roadmap_and_vendor_pickers_offer_only_the_kept_capability(
    browser, live_server, world, label, width, height
):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, world["emails"]["application_manager"])

        # Roadmap: the work package form's linked-capability choice.
        page.goto(live_server + "/applications/%d/roadmap" % world["app_id"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Add Work Package").first.click()
        select = page.locator("#wp-capability")
        expect(select).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(select.locator("option", has_text=world["kept_name"])).to_have_count(1, timeout=PAGE_TIMEOUT)
        options = select.locator("option").all_inner_texts()
        assert not [o for o in options if world["retired_name"] in o], options
        page.screenshot(path="%s/roadmap-%s.png" % (WALK, label), full_page=True)

        # Vendor analysis: the first-level capability list.
        page.goto(live_server + "/dashboard/vendor-analysis/new",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        panel = page.get_by_role("group", name="L1 capabilities")
        expect(panel).to_contain_text(world["kept_name"], timeout=PAGE_TIMEOUT)
        assert world["retired_name"] not in panel.inner_text()
        page.screenshot(path="%s/vendor-analysis-%s.png" % (WALK, label), full_page=True)

        # The retired capability is not offered, but its record still opens.
        status, body = api(page, "GET", "/api/v1/capabilities/%d" % world["retired_id"])
        assert status == 200, body
        assert (body.get("data") or body)["name"] == world["retired_name"]
    finally:
        context.close()
