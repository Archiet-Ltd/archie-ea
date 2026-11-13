"""While the gaps answer is held, the gaps tiles show the loading mark, never 0 or blank.

An architect records a capability gap. While /api/roadmap/gaps is held, the
"Capability Gaps" and "AI Generated" tiles show the loading mark ("—"), never
"0" or a blank. When the answer arrives, both tiles show the recorded figure
and agree with the evidence line.
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


def test_gaps_held_show_loading_mark_never_zero(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]

    for width, theme in ((1440, "light"), (390, "dark")):
        context, page = _page(browser, width, theme)
        state = {"release_gaps": False}

        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])

            # Record a gap through the product's own API, then read the register's
            # recorded_gaps count so the assertion matches the source of truth.
            status, gap = api(page, "POST", BASE + "/gaps", {
                "name": "Gap " + suffix, "gap_type": "coverage", "priority": "high"})
            assert status == 201, gap
            status, gaps_body = api(page, "GET", BASE + "/gaps")
            assert status == 200, gaps_body
            expected_gaps = gaps_body.get("recorded_gaps")
            assert expected_gaps is not None and expected_gaps > 0, gaps_body

            def hold_gaps(route):
                try:
                    for _ in range(300):
                        if state["release_gaps"]:
                            break
                        page.wait_for_timeout(100)
                    route.continue_()
                except Exception:
                    pass

            page.route(GAPS_RE, hold_gaps)
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

            # While the gaps answer is held, both gaps tiles show the loading mark,
            # never "0" or blank.
            recorded = page.get_by_test_id("rdm-recorded-gaps")
            ai = page.get_by_test_id("rdm-ai-generated")
            expect(recorded).to_have_text("—", timeout=PAGE_TIMEOUT)
            expect(ai).to_have_text("—", timeout=PAGE_TIMEOUT)

            # Release the gaps answer
            state["release_gaps"] = True

            # Both tiles now show their figures: recorded gaps = expected_gaps, AI generated = 0
            # (the gap was created manually, not via AI generation)
            expect(recorded).to_have_text(str(expected_gaps), timeout=PAGE_TIMEOUT)
            expect(ai).to_have_text("0", timeout=PAGE_TIMEOUT)

            # Evidence line agrees
            line = page.get_by_test_id("roadmap-summary-footer").inner_text()
            assert "Gaps: %d" % expected_gaps in line, line
        finally:
            state["release_gaps"] = True
            context.close()
