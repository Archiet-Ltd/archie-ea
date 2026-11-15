"""A slow first answer on the capability roadmap never puts older figures back after a save.

An architect with three plateaus and six work packages opens the capability roadmap while the first
read of the gaps is slow. They add a work package: the Work Packages tile and the evidence line read
seven. The slow first answer then arrives, and both still read seven. Another organisation's packages
are on neither.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .test_capability_roadmap_figures_journey import PATH, _frame, _page, _plan

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_a_late_first_answer_does_not_replace_the_figures_after_a_save(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    _plan(browser, live_server, org["emails"]["enterprise_architect"], org["suffix"])
    _plan(browser, live_server, other["emails"]["enterprise_architect"], other["suffix"])

    for before, (width, theme) in ((6, (1440, "light")), (7, (390, "dark"))):
        after = before + 1
        context, page = _page(browser, width, theme)
        state = {"release": False, "held": False, "arrived": False}

        def slow_first_gaps(route):
            try:
                if state["held"]:
                    route.continue_()
                    return
                state["held"] = True
                answer = route.fetch()
                for _ in range(300):
                    if state["release"]:
                        break
                    page.wait_for_timeout(100)
                route.fulfill(response=answer)
                state["arrived"] = True
            except Exception as e:
                if "closed" not in str(e).lower():
                    raise

        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])
            page.route(re.compile(r".*/capability-map/api/roadmap/gaps(\?.*)?$"), slow_first_gaps)
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            tile = page.get_by_test_id("rdm-work-packages-tile")
            line = page.get_by_test_id("roadmap-summary-footer")
            expect(tile).to_have_text(str(before), timeout=PAGE_TIMEOUT)

            page.get_by_role("button", name="Add Work Package").click()
            page.fill("#rdm-wp-name", "Late package " + org["suffix"])
            page.fill("#rdm-wp-start", "2026-11-01")
            page.fill("#rdm-wp-target", "2027-01-31")
            page.locator("#rdm-create-wp-modal").get_by_role("button", name="Create Work Package").click()
            expect(tile).to_have_text(str(after), timeout=PAGE_TIMEOUT)
            expect(line).to_contain_text("Work packages: %d" % after, timeout=PAGE_TIMEOUT)
            assert state["arrived"] is False, "the first answer arrived before the save"

            state["release"] = True
            for _ in range(100):
                if state["arrived"]:
                    break
                page.wait_for_timeout(100)
            assert state["arrived"] is True
            page.wait_for_timeout(500)

            expect(tile).to_have_text(str(after))
            expect(line).to_contain_text("Work packages: %d" % after)
            assert "Work packages: %d" % before not in line.inner_text()
            assert other["suffix"] not in page.content()
            _frame(page, "late-answer", width, theme)
        finally:
            context.close()
