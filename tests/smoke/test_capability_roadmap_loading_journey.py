"""The capability roadmap asks for each source once and shows each part as its own data arrives.

An architect with three plateaus and six work packages opens the capability roadmap while the
work-package answer is slow. The plateau timeline names every plateau before the work packages
have arrived, then the work-package tile reads the same number as the roadmap board. The page
asked for gaps, plateaus and work packages once each. Another organisation's plateaus never show.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .test_capability_roadmap_figures_journey import BASE, PATH, _frame, _page, _plan

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SOURCES = re.compile(r"/capability-map/api/roadmap/(gaps|plateaus|work-packages)(\?.*)?$")


def test_each_source_is_asked_once_and_plateaus_show_before_work_packages(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    names = _plan(browser, live_server, org["emails"]["enterprise_architect"], org["suffix"])
    _plan(browser, live_server, other["emails"]["enterprise_architect"], other["suffix"])

    for width, theme in ((1440, "light"), (390, "dark")):
        context, page = _page(browser, width, theme)
        asked = {"gaps": 0, "plateaus": 0, "work-packages": 0}
        state = {"release": False, "packages_arrived": False}

        def on_request(request):
            found = SOURCES.search(request.url.split("#")[0])
            if found and request.method == "GET":
                asked[found.group(1)] += 1

        def slow_packages(route):
            for _ in range(300):
                if state["release"]:
                    break
                page.wait_for_timeout(100)
            route.continue_()
            state["packages_arrived"] = True

        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])
            page.on("request", on_request)
            page.route(re.compile(r".*/capability-map/api/roadmap/work-packages(\?.*)?$"), slow_packages)
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

            for name in names:
                expect(page.get_by_test_id("rdm-plateau-name").filter(has_text=name)).to_be_visible(timeout=15000)
            assert state["packages_arrived"] is False, "plateaus showed only after the work packages arrived"
            assert other["suffix"] not in page.content()
            _frame(page, "loading-early", width, theme)

            state["release"] = True
            expect(page.get_by_test_id("rdm-work-packages-tile")).to_have_text("6", timeout=PAGE_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id("rdm-plateau")).to_have_count(len(names))
            line = page.get_by_test_id("roadmap-summary-footer").inner_text()
            assert "Work packages: 6" in line, line
            assert asked == {"gaps": 1, "plateaus": 1, "work-packages": 1}, asked
            _frame(page, "loading-done", width, theme)
        finally:
            state["release"] = True
            context.close()
