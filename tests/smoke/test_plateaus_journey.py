"""An enterprise architect opens the plateaus page and sees their plateaus.

In a new organisation the architect records a plateau, then opens the sidebar
link to plateaus and sees the plateau by name. Every plateau link lands on the
plateau detail page with content.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_plateaus_page_shows_plateaus_and_links_land(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]
    plateau_name = "Target state %s" % suffix
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])

        # Create a plateau via API
        page.goto(live_server + "/enterprise/implementation/plateaus",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, plateau = api(page, "POST", "/capability-map/api/roadmap/plateaus",
                              {"name": plateau_name, "sequence_order": 1})
        assert status in (200, 201), plateau
        plateau_id = plateau["plateau"]["id"]

        # Reload and assert the plateau appears
        page.goto(live_server + "/enterprise/implementation/plateaus",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("plateau-link-%s" % plateau_id)).to_contain_text(plateau_name, timeout=PAGE_TIMEOUT)

        # Follow the plateau link and assert it lands on content
        page.get_by_test_id("plateau-link-%s" % plateau_id).click()
        page.wait_for_url(re.compile(r"/architecture/implementation/Plateau/\d+$"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=plateau_name).first).to_be_visible()
    finally:
        context.close()


def test_plateaus_empty_state(browser, live_server):
    """A fresh organisation with no plateaus sees the empty state."""
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/implementation/plateaus",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="No plateaus defined yet")).to_be_visible()
    finally:
        context.close()