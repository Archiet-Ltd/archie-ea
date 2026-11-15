"""A CTO opens the capability map while the server cannot build it, then again once it can.

Goal: "tell me what capabilities we have". When the map cannot load a person reads a plain
message and presses Try again; they are never told that nothing matches their filters, never see
exception text, and never see the empty state flash while the map is still loading. Once it loads,
the map states how many capabilities it holds, the same figure as the hierarchy page.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.test_capability_map_applications_journey import _seed

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

HIERARCHY_API = "**/capability-map/api/capabilities/hierarchy"


@pytest.mark.parametrize("viewport,scheme", [({"width": 1440, "height": 900}, "light"),
                                             ({"width": 390, "height": 844}, "dark")])
def test_failed_load_says_so_and_try_again_recovers(browser, live_server, viewport, scheme):
    org = create_fresh_org("cto", org_admin=True)
    _seed(org["org_id"], "PAY-1", "Payments Processing", ["Billing Engine"])
    _seed(org["org_id"], "LED-1", "General Ledger", [])

    page = browser.new_page(viewport=viewport, color_scheme=scheme)
    try:
        sign_in(page, live_server, org["emails"]["cto"])
        page.route(HIERARCHY_API, lambda route: route.fulfill(
            status=500, content_type="application/json",
            body='{"error": "secret internal detail", "domains": []}'))
        page.goto(live_server + "/capability-map/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

        error = page.locator('[data-testid="capability-map-error"]')
        expect(error).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(error).to_contain_text("The capability map could not load")
        body = page.locator("body")
        expect(body).not_to_contain_text("No capabilities match your current filters")
        expect(body).not_to_contain_text("No capabilities found")
        expect(body).not_to_contain_text("secret internal detail")
        expect(page.get_by_role("button", name="Clear filters")).to_have_count(0)

        page.unroute(HIERARCHY_API)
        error.get_by_role("button", name="Try again").click()
        tile = page.locator('[role="tabpanel"][aria-label="Map view"] [role="button"]',
                            has_text="Payments Processing")
        expect(tile).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(error).to_have_count(0)
    finally:
        page.close()


def test_empty_state_does_not_show_while_loading(browser, live_server):
    org = create_fresh_org("cto", org_admin=True)
    _seed(org["org_id"], "PAY-1", "Payments Processing", [])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        sign_in(page, live_server, org["emails"]["cto"])
        seen = []

        def slow(route):
            seen.append(page.locator("body").inner_text())
            route.continue_()

        page.route(HIERARCHY_API, slow)
        page.goto(live_server + "/capability-map/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert seen, "the map never asked for its capabilities"
        assert all("No capabilities found" not in text for text in seen), seen
    finally:
        page.close()


def test_map_total_equals_the_hierarchy_page_total(browser, live_server):
    org = create_fresh_org("cto", org_admin=True)
    _seed(org["org_id"], "PAY-1", "Payments Processing", ["Billing Engine"])
    _seed(org["org_id"], "LED-1", "General Ledger", [])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        sign_in(page, live_server, org["emails"]["cto"])
        page.goto(live_server + "/capability-map/hierarchy", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        match = re.search(r"(\d+) capabilities", page.locator("body").inner_text())
        assert match, "the hierarchy page shows no capability total"
        expected = match.group(1)
        page.goto(live_server + "/capability-map/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        total = page.locator('[data-testid="capability-total"]')
        expect(total).to_have_text("%s capabilities" % expected, timeout=PAGE_TIMEOUT)
        assert int(expected) >= 2
    finally:
        page.close()
