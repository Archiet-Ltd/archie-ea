"""When the gaps answer fails with 500, the gaps tiles show the error mark, never 0.

An architect records a capability gap. When /api/roadmap/gaps returns 500, the
"Capability Gaps" and "AI Generated" tiles show the error mark ("!"), never "0"
or blank, and the error banner names the failure.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/capability-roadmap"
BASE = "/capability-map/api/roadmap"
GAPS_RE = re.compile(r".*/capability-map/api/roadmap/gaps(\?.*)?$")


def _page(browser, width, theme):
    context = browser.new_context(
        ignore_https_errors=True, viewport={"width": width, "height": 1000}, color_scheme=theme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def test_gaps_failing_show_error_mark_never_zero(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]

    for width, theme in ((1440, "light"), (390, "dark")):
        context, page = _page(browser, width, theme)

        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])

            # Record a gap through the product's own API
            status, gap = api(page, "POST", BASE + "/gaps", {
                "name": "Gap " + suffix, "gap_type": "coverage", "priority": "high"})
            assert status == 201, gap

            # The gaps answer fails with 500
            def fail_gaps(route):
                try:
                    route.fulfill(
                        status=500,
                        body='{"success": false, "error": "gaps service unavailable"}',
                        headers={"Content-Type": "application/json"})
                except Exception:
                    pass

            page.route(GAPS_RE, fail_gaps)
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

            # Both gaps tiles show the error mark, never "0" or blank
            recorded = page.get_by_test_id("rdm-recorded-gaps")
            ai = page.get_by_test_id("rdm-ai-generated")
            expect(recorded).to_have_text("!", timeout=PAGE_TIMEOUT)
            expect(ai).to_have_text("!", timeout=PAGE_TIMEOUT)

            # The error banner names the failure
            assert "gaps service unavailable" in page.content(), page.content()
        finally:
            context.close()
