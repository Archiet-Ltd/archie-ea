"""A result footer adds evidence; it never repeats the page it sits on.

An enterprise architect has a plateau, a gap and a work package that closes the gap. On the work
package's page the footer must not name the package under the heading that prints it, nor list the gap
already linked above it. On the roadmap the plateau footer must not print the plateau's name again.
Each footer still states its source, confidence and date.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _evidence_still_stated(footer):
    expect(footer.get_by_test_id("result-footer-source")).to_be_visible()
    expect(footer.get_by_test_id("result-footer-confidence")).to_be_visible()
    expect(footer.get_by_test_id("result-footer-date")).to_be_visible()


def test_work_package_and_plateau_footers_do_not_repeat_the_page(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]
    gap_name = "Billing has no owner %s" % suffix
    wp_name = "Appoint billing owner %s" % suffix
    plateau_name = "Target state %s" % suffix

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/implementation/gap-analysis",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, plateau = api(page, "POST", "/capability-map/api/roadmap/plateaus",
                              {"name": plateau_name, "sequence_order": 1})
        assert status == 201, plateau
        status, gap = api(page, "POST", "/capability-map/api/roadmap/gaps",
                          {"name": gap_name, "gap_type": "coverage", "priority": "high"})
        assert status in (200, 201), gap
        status, wp = api(page, "POST", "/capability-map/api/roadmap/work-packages", {
            "name": wp_name, "plateau_id": plateau["plateau"]["id"],
            "start_date": "2026-11-01", "target_date": "2027-01-31"})
        assert status == 201, wp
        wp_id = wp["work_package"]["id"]
        status, linked = api(page, "POST", "/capability-map/api/roadmap/gaps/%d/link-work-package/%d"
                             % (gap["gap"]["id"], wp_id))
        assert status == 200, linked

        # The work package page: the heading and the gap list say the names; the footer says the evidence.
        page.goto(live_server + "/implementation/work-packages/%d" % wp_id,
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("wp-gaps")).to_contain_text(gap_name)
        footer = page.get_by_test_id("result-footer")
        expect(footer).to_have_count(1)
        expect(footer).not_to_contain_text(wp_name)
        expect(footer).not_to_contain_text(gap_name)
        expect(footer.get_by_role("link")).to_have_count(0)
        _evidence_still_stated(footer)

        # The roadmap: the plateau card prints the name; its footer does not.
        page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card = page.get_by_test_id("plateau-footer").first
        expect(card.get_by_test_id("result-footer")).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(card).not_to_contain_text(plateau_name)
        _evidence_still_stated(card)
    finally:
        context.close()
