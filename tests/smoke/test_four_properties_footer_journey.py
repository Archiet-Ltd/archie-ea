"""An enterprise architect reads the four properties under the gap list.

In a brand-new organisation the architect records a gap. The gap analysis page
carries a footer saying where the figure comes from, when it was worked out, how
sure it is and where to follow it. After a reload the footer still counts the
one recorded gap, and its link opens the roadmap. A second new organisation's
footer does not count that gap.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

GAPS = "/enterprise/implementation/gap-analysis"


def _new_page(browser):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def test_architect_sees_the_four_properties_under_the_gap_list(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")

    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + GAPS, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        footer = page.locator('[data-four-properties="gaps"]')
        expect(footer).to_contain_text("no recorded gaps")

        status, gap = api(page, "POST", "/capability-map/api/roadmap/gaps",
                          {"name": "Billing has no owner %s" % org["suffix"], "gap_type": "coverage",
                           "priority": "high"})
        assert status in (200, 201), gap

        page.reload(wait_until="domcontentloaded")
        footer = page.locator('[data-four-properties="gaps"]')
        expect(footer.locator('[data-fp="source"]')).to_contain_text("1 recorded gap")
        expect(footer.locator('[data-fp="computed"] time')).to_have_count(1)
        expect(footer.locator('[data-fp="confidence"]')).to_contain_text("Counted from recorded data")
        footer.locator('[data-fp="trail"] a').click()
        page.wait_for_url(re.compile(r"/capability-roadmap"), timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-four-properties="roadmap"] [data-fp="source"]')).to_contain_text(
            "1 recorded gap")
    finally:
        context.close()

    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, other["emails"]["enterprise_architect"])
        page.goto(live_server + GAPS, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-four-properties="gaps"] [data-fp="source"]')).to_contain_text(
            "no recorded gaps")
    finally:
        context.close()


ROADMAP = "/capability-roadmap"


def test_roadmap_footer_follows_a_deleted_work_package_without_a_reload(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + ROADMAP, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, gap = api(page, "POST", "/capability-map/api/roadmap/gaps",
                          {"name": "Billing has no owner %s" % org["suffix"], "gap_type": "coverage",
                           "priority": "high"})
        assert status in (200, 201), gap
        status, wp = api(page, "POST", "/capability-map/api/roadmap/work-packages",
                         {"name": "Assign billing owner %s" % org["suffix"], "status": "planned",
                          "priority": "high", "business_capability": "Enterprise"})
        assert status in (200, 201), wp

        page.goto(live_server + ROADMAP, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        source = page.locator('[data-four-properties="roadmap"] [data-fp="source"]')
        expect(source).to_contain_text("1 recorded gap and 1 work package")

        page.get_by_role("button", name="Delete", exact=True).first.click()
        page.get_by_role("alertdialog").get_by_role("button", name="Delete").click()
        expect(source).to_contain_text("1 recorded gap and no work packages", timeout=PAGE_TIMEOUT)

        page.reload(wait_until="domcontentloaded")
        expect(page.locator('[data-four-properties="roadmap"] [data-fp="source"]')).to_contain_text(
            "1 recorded gap and no work packages")
    finally:
        context.close()
