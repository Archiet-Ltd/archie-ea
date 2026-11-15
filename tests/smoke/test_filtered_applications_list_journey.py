"""A chief technology officer opens a coverage figure and the applications list says what it is filtered by.

"With a named owner: 0" opens an empty list, and the list shows the owner chip beside its empty state; the chip's
remove link and Clear both bring the applications back. The no-unit link shows its own chip. A refused work
package create keeps the status, priority and programme that were chosen.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .test_ownership_coverage_links_journey import _rows, _seed

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _signed_in(browser, live_server, org, width):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["cto"])
    return context, page


def test_a_coverage_link_to_an_empty_list_names_its_filters(live_server, browser):
    mine = "Mine%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("cto")
    _seed(org["org_id"], mine, "%s Finance" % mine)
    context, page = _signed_in(browser, live_server, org, 1440)
    try:
        page.goto(live_server + "/applications/ownership-coverage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        footer = page.get_by_test_id("result-footer").filter(has_text="Business unit: %s Retail" % mine).last
        footer.get_by_test_id("result-footer-evidence").get_by_role("link", name="With a named owner: 0").click()
        page.wait_for_url(lambda url: "/ownership-coverage" not in url, timeout=PAGE_TIMEOUT)
        chips = page.get_by_test_id("active-filters")
        expect(chips).to_contain_text("Named owner", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("No applications match your filters")).to_be_visible()
        expect(page.get_by_test_id("btn-clear-filters")).to_be_visible()
        assert _rows(page).count() == 0
        # Removing the owner chip keeps the unit and shows its one application.
        chips.get_by_role("link", name="Named owner").click()
        page.wait_for_load_state("domcontentloaded")
        expect(_rows(page).first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "has_owner" not in page.url and "domain=" in page.url
        assert _rows(page).count() == 1 and "%s Till" % mine in _rows(page).first.inner_text()
        # Clear drops every filter the link set.
        page.go_back()
        page.get_by_test_id("btn-clear-filters").click()
        page.wait_for_function("!location.search.includes('has_owner')", timeout=PAGE_TIMEOUT)
        expect(_rows(page).first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert _rows(page).count() == 6  # the active applications
    finally:
        context.close()


def test_the_no_unit_link_shows_its_chip_and_removes_it(live_server, browser):
    mine = "Mine%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("cto")
    _seed(org["org_id"], mine, "%s Finance" % mine)
    context, page = _signed_in(browser, live_server, org, 390)
    try:
        page.goto(live_server + "/applications/ownership-coverage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        footer = page.get_by_test_id("result-footer").filter(has_text="Business unit: No business unit").last
        footer.get_by_test_id("result-footer-evidence").get_by_role("link", name="Applications counted: 2").click()
        page.wait_for_url(lambda url: "/ownership-coverage" not in url, timeout=PAGE_TIMEOUT)
        chips = page.get_by_test_id("active-filters")
        expect(chips).to_contain_text("No business unit", timeout=PAGE_TIMEOUT)
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        chips.get_by_role("link", name="No business unit").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_test_id("active-filters")).to_have_count(0)
        assert "no_domain" not in page.url
    finally:
        context.close()


def test_a_refused_work_package_create_keeps_status_priority_and_programme(live_server, browser):
    from app import create_app, db
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    org = create_fresh_org("cto")
    app = create_app("testing")
    label = "Prog%s" % uuid.uuid4().hex[:6]
    with app.app_context():
        programme = EnterpriseInitiative(name=label, organization_id=org["org_id"])
        db.session.add(programme)
        db.session.commit()
        programme_id = str(programme.id)
        db.session.remove()
    context, page = _signed_in(browser, live_server, org, 1440)
    try:
        page.goto(live_server + "/implementation/work-packages/create", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_label("Work Package Name").fill("Keep%s" % uuid.uuid4().hex[:6])
        page.locator("#status").select_option("on_hold")
        page.locator("#priority").select_option("critical")
        page.locator("#programme_id").select_option(programme_id)
        page.locator("#start_date").fill("2026-12-10")
        page.locator("#end_date").fill("2026-12-01")
        page.get_by_role("button", name="Create Work Package").click()
        expect(page.locator("#end_date-error")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator("#status")).to_have_value("on_hold")
        expect(page.locator("#priority")).to_have_value("critical")
        expect(page.locator("#programme_id")).to_have_value(programme_id)
    finally:
        context.close()
