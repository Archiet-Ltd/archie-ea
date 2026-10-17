"""An enterprise architect links a gap to a work package and finds it everywhere.

In a brand-new organisation the architect has a gap, a plateau and a work package
that delivers the plateau. On the work package page they search for the gap in the
picker and pick it. After a reload the gap is listed on the work package page,
beside its owner field and progress, and the same link shows on the gap analysis
page, in the work packages list (whose row opens the page), in the roadmap's Gap
column and on the plateau. A second new organisation sees none of it.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _new_page(browser):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def test_architect_links_a_gap_to_a_work_package_and_sees_it_everywhere(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]
    gap_name = "Billing has no owner %s" % suffix
    wp_name = "Appoint billing owner %s" % suffix
    plateau_name = "Target state %s" % suffix

    context, page = _new_page(browser)
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
            "start_date": "2026-11-01", "target_date": "2027-01-31",
        })
        assert status == 201, wp
        wp_id = wp["work_package"]["id"]

        # Before the link the gap is on the "not addressed" list.
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_test_id("not-addressed-gap-link-%s" % gap["gap"]["id"])).to_have_count(1)

        # Open the work package from the work packages list (the row link).
        page.goto(live_server + "/enterprise/implementation/work-packages",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("work-package-link").filter(has_text=wp_name).click()
        page.wait_for_url(re.compile(r"/implementation/work-packages/%d$" % wp_id), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("wp-gaps")).not_to_contain_text(gap_name)

        # Link the gap through the picker.
        page.get_by_test_id("gap-picker-input").fill("Billing has no")
        option = page.get_by_test_id("gap-picker-option").filter(has_text=gap_name)
        expect(option).to_have_count(1, timeout=PAGE_TIMEOUT)
        with page.expect_response(
            lambda r: "/link-work-package/" in r.url and r.request.method == "POST", timeout=PAGE_TIMEOUT
        ) as posted:
            option.click()
        assert posted.value.status == 200
        # The page reloads itself once the link is saved.
        expect(page.get_by_test_id("wp-gaps")).to_contain_text(gap_name, timeout=PAGE_TIMEOUT)

        # After a reload of our own the work package page lists the gap, with a dash for the
        # owner it does not have.
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_test_id("wp-gaps")).to_contain_text(gap_name)
        expect(page.get_by_test_id("wp-owner")).to_have_text("—")
        expect(page.get_by_test_id("wp-plateaus")).to_contain_text(plateau_name)

        # The gap analysis page shows the package against the gap, and the gap is
        # no longer on the "not addressed" list.
        page.goto(live_server + "/enterprise/implementation/gap-analysis",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        gap_row = page.locator("tr").filter(has_text=gap_name)
        expect(gap_row.first).to_contain_text(wp_name)
        expect(page.get_by_test_id("not-addressed-gap-link-%s" % gap["gap"]["id"])).to_have_count(0)

        # The work packages list shows the gap on the package's row.
        page.goto(live_server + "/enterprise/implementation/work-packages",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("tr").filter(has_text=wp_name)).to_contain_text(gap_name, timeout=PAGE_TIMEOUT)

        # The roadmap's Gap column is filled.
        page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        roadmap_row = page.locator("tr").filter(has_text=wp_name)
        expect(roadmap_row.get_by_test_id("roadmap-wp-gaps")).to_contain_text(gap_name, timeout=PAGE_TIMEOUT)

        # The plateau lists the gap.
        page.goto(live_server + "/enterprise/implementation/plateaus",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("plateau-gaps-%s" % plateau["plateau"]["id"])).to_contain_text(gap_name)
    finally:
        context.close()

    other_context, other_page = _new_page(browser)
    try:
        sign_in(other_page, live_server, other["emails"]["enterprise_architect"])
        response = other_page.goto(live_server + "/implementation/work-packages/%d" % wp_id,
                                   wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response is not None and response.status == 404
        other_page.goto(live_server + "/enterprise/implementation/gap-analysis",
                        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert gap_name not in other_page.content()
        status, found = api(other_page, "GET", "/capability-map/api/roadmap/gaps/search?q=Billing", None)
        assert status == 200 and found["gaps"] == []
    finally:
        other_context.close()
