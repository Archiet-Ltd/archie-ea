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
        planned = ApplicationComponent(
            name="Ledger Core %s" % tag, organization_id=org["org_id"],
            lifecycle_status="operational", description="General ledger.",
        )
        db.session.add_all([component, planned, kept])
        db.session.flush()
        retired = UnifiedCapability(
            name="Invoice handling (old) %s" % tag, level=1, code="IO%s" % tag,
            organization_id=org["org_id"], retired_into_id=kept.id,
        )
        db.session.add(retired)
        db.session.commit()
        org.update(app_id=component.id, planned_app_id=planned.id, kept_id=kept.id, kept_name=kept.name, retired_name=retired.name,
                   retired_id=retired.id)
        db.session.remove()
    return org


@pytest.mark.parametrize("label,width,height", WIDTHS)
def test_roadmap_and_vendor_pickers_offer_only_the_kept_capability(
    browser, live_server, world, label, width, height, tmp_path
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
        page.screenshot(path=str(tmp_path / ("roadmap-%s.png" % label)), full_page=True)

        # Vendor analysis: the first-level capability list.
        page.goto(live_server + "/dashboard/vendor-analysis/new",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        panel = page.get_by_role("group", name="L1 capabilities")
        expect(panel).to_contain_text(world["kept_name"], timeout=PAGE_TIMEOUT)
        assert world["retired_name"] not in panel.inner_text()
        page.screenshot(path=str(tmp_path / ("vendor-analysis-%s.png" % label)), full_page=True)

        # The retired capability is not offered, but its record still opens.
        status, body = api(page, "GET", "/api/v1/capabilities/%d" % world["retired_id"])
        assert status == 200, body
        assert (body.get("data") or body)["name"] == world["retired_name"]
    finally:
        context.close()


@pytest.mark.parametrize("label,width,height", WIDTHS)
def test_governance_dashboard_and_roadmap_list_show_only_the_kept_capability(
    browser, live_server, world, label, width, height, tmp_path
):
    """The governance dashboard lists the kept capability once; the retired copy is gone."""
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, world["emails"]["application_manager"])
        page.goto(live_server + "/capability-governance/governance-dashboard",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        main = page.locator("main")
        expect(main).to_contain_text(world["kept_name"], timeout=PAGE_TIMEOUT)
        assert world["retired_name"] not in main.inner_text()
        page.screenshot(path=str(tmp_path / ("governance-%s.png" % label)), full_page=True)

        # The empty roadmap explains itself and offers the next step.
        page.goto(live_server + "/applications/%d/roadmap" % world["app_id"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("tab", name="List").click()
        expect(page.get_by_text("No work packages yet", exact=False)).to_be_visible(timeout=PAGE_TIMEOUT)
        page.screenshot(path=str(tmp_path / ("roadmap-list-%s.png" % label)), full_page=True)
    finally:
        context.close()


@pytest.mark.parametrize("label,width,height", WIDTHS)
def test_roadmap_list_shows_a_work_package_with_its_kept_capability(
    browser, live_server, world, label, width, height, tmp_path
):
    """A planned work package is listed with the kept capability; Edit opens it filled in."""
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    name = "Close the ledger %s" % label
    try:
        sign_in(page, live_server, world["emails"]["application_manager"])
        page.goto(live_server + "/applications/%d/roadmap" % world["planned_app_id"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, body = api(page, "POST", "/api/applications/%d/work-packages" % world["planned_app_id"], {
            "name": name, "transformation_type": "Modernization",
            "start_date": "2026-11-01", "target_date": "2027-03-01",
            "capability_id": world["kept_id"],
        })
        assert status < 400, body
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("tab", name="List").click()
        row = page.locator("tr", has_text=name)
        expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(row).to_contain_text("Cap: " + world["kept_name"])
        assert world["retired_name"] not in page.locator("main").inner_text()
        expect(row).to_contain_text("2026-11-01")
        page.screenshot(path=str(tmp_path / ("roadmap-list-data-%s.png" % label)), full_page=True)
        row.get_by_role("button", name="Edit").click()
        expect(page.locator("#wp-name")).to_have_value(name, timeout=PAGE_TIMEOUT)
        expect(page.locator("#wp-capability")).to_have_value(str(world["kept_id"]))
    finally:
        context.close()
