"""An architect with real gaps opens gap analysis from every link that points at it.

In a new organisation the architect records a gap, then opens the sidebar link to
gap analysis and sees the gap by name. The old /portfolio/gap-analysis address and
the header search entry "Gap Discovery" land on the same list. An address under
/portfolio that does not exist is a plain 404 page, not a redirect into one under
/enterprise.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _assert_gap_list(page, gap_name):
    assert page.url.rstrip("/").endswith("/implementation/gap-analysis"), page.url
    expect(page.get_by_test_id("gap-link-%s" % GAP_IDS[gap_name])).to_contain_text(gap_name, timeout=PAGE_TIMEOUT)


GAP_IDS = {}


def test_every_link_to_gap_analysis_lands_on_the_gap_list(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    gap_name = "Billing has no owner %s" % org["suffix"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/implementation/gap-analysis",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, gap = api(page, "POST", "/capability-map/api/roadmap/gaps",
                          {"name": gap_name, "gap_type": "coverage", "priority": "high"})
        assert status in (200, 201), gap
        GAP_IDS[gap_name] = gap["gap"]["id"]

        # The sidebar link on another page.
        page.goto(live_server + "/enterprise/implementation/work-packages",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        link = page.locator('a[href$="/implementation/gap-analysis"]').first
        link.click()
        page.wait_for_url(re.compile(r"/implementation/gap-analysis$"), timeout=PAGE_TIMEOUT)
        _assert_gap_list(page, gap_name)

        # The old address that the audit found answering 404.
        page.goto(live_server + "/portfolio/gap-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _assert_gap_list(page, gap_name)

        # The header search entry "Gap Discovery" points at a page that exists.
        href = page.evaluate(
            "() => { const m = document.documentElement.innerHTML.match("
            "/label:\s*'Gap Discovery',\s*href:\s*'([^']*)'/); return m && m[1]; }")
        assert href, "the header search no longer offers Gap Discovery"
        page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _assert_gap_list(page, gap_name)

        # A path that is not a page is a plain 404, not a redirect into one under /enterprise.
        response = page.goto(live_server + "/portfolio/xyz", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response is not None and response.status == 404
        assert "/enterprise/" not in page.url, page.url
    finally:
        context.close()
