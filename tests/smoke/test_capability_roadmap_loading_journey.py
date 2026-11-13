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
from .test_capability_roadmap_figures_journey import BASE, PATH, _frame, _page, _plan, SOURCES

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


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

            # While work-packages answer is held, the Work Packages tile must show the
            # evidence line's figure (6) or the loading mark (—), never "0".
            # Read once without retrying; the assertion must fail at the base commit
            # where the tile starts at 0.
            wp_tile = page.get_by_test_id("rdm-work-packages-tile")
            tile_text = wp_tile.inner_text()
            assert tile_text != "0", f"Work Packages tile showed '0' while answer held"
            assert tile_text in ("6", "—"), f"Work Packages tile showed '{tile_text}' while answer held"

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


def test_plateaus_tile_shows_evidence_figure_while_plateaus_answer_held(browser, live_server):
    """While the plateaus answer is held, the Plateaus tile must show the evidence
    line's figure or the loading mark, never "0"."""
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    names = _plan(browser, live_server, org["emails"]["enterprise_architect"], org["suffix"])
    _plan(browser, live_server, other["emails"]["enterprise_architect"], other["suffix"])

    for width, theme in ((1440, "light"), (390, "dark")):
        context, page = _page(browser, width, theme)
        asked = {"gaps": 0, "plateaus": 0, "work-packages": 0}
        state = {"release": False, "plateaus_arrived": False}

        def on_request(request):
            found = SOURCES.search(request.url.split("#")[0])
            if found and request.method == "GET":
                asked[found.group(1)] += 1

        def slow_plateaus(route):
            for _ in range(300):
                if state["release"]:
                    break
                page.wait_for_timeout(100)
            route.continue_()
            state["plateaus_arrived"] = True

        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])
            page.on("request", on_request)
            page.route(re.compile(r".*/capability-map/api/roadmap/plateaus(\?.*)?$"), slow_plateaus)
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

            # Wait for gaps to load (they arrive first)
            expect(page.get_by_test_id("rdm-recorded-gaps")).not_to_have_text("—", timeout=15000)
            assert state["plateaus_arrived"] is False, "plateaus answer arrived too early"
            assert other["suffix"] not in page.content()

            # While plateaus answer is held, the Plateaus tile must show the
            # evidence line's figure (3) or the loading mark (—), never "0".
            # Read once without retrying; the assertion must fail at the base commit
            # where the tile starts at 0.
            plateau_tile = page.get_by_test_id("rdm-plateaus-tile")
            tile_text = plateau_tile.inner_text()
            assert tile_text != "0", f"Plateaus tile showed '0' while answer held"
            assert tile_text in ("3", "—"), f"Plateaus tile showed '{tile_text}' while answer held"

            _frame(page, "loading-plateaus-held", width, theme)

            state["release"] = True
            expect(page.get_by_test_id("rdm-plateaus-tile")).to_have_text("3", timeout=PAGE_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id("rdm-plateau")).to_have_count(len(names))
            line = page.get_by_test_id("roadmap-summary-footer").inner_text()
            assert "Plateaus: 3" in line, line
            assert asked == {"gaps": 1, "plateaus": 1, "work-packages": 1}, asked
            _frame(page, "loading-plateaus-done", width, theme)
        finally:
            state["release"] = True
            context.close()


def test_work_packages_query_failure_shows_error_state(browser, live_server):
    """When the work-packages query fails, the route returns an error status and
    the page shows the work-packages error state while gaps and plateaus remain usable."""
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    names = _plan(browser, live_server, org["emails"]["enterprise_architect"], org["suffix"])
    _plan(browser, live_server, other["emails"]["enterprise_architect"], other["suffix"])

    for width, theme in ((1440, "light"), (390, "dark")):
        context, page = _page(browser, width, theme)
        asked = {"gaps": 0, "plateaus": 0, "work-packages": 0}

        def on_request(request):
            found = SOURCES.search(request.url.split("#")[0])
            if found and request.method == "GET":
                asked[found.group(1)] += 1

        def fail_packages(route):
            # Intercept and force a 500 response
            route.fulfill(status=500, json={"success": False, "error": "Database error"})

        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])
            page.on("request", on_request)
            page.route(re.compile(r".*/capability-map/api/roadmap/work-packages(\?.*)?$"), fail_packages)
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

            # Gaps and plateaus should load normally
            expect(page.get_by_test_id("rdm-recorded-gaps")).not_to_have_text("—", timeout=15000)
            for name in names:
                expect(page.get_by_test_id("rdm-plateau-name").filter(has_text=name)).to_be_visible(timeout=15000)

            # Work packages table should show error state (not "No work packages yet")
            wp_error = page.locator("text=Work packages could not be read")
            expect(wp_error).to_be_visible(timeout=PAGE_TIMEOUT)

            # Work packages tile should show loading mark or evidence figure, not "0"
            wp_tile = page.get_by_test_id("rdm-work-packages-tile")
            tile_text = wp_tile.inner_text()
            assert tile_text != "0", f"Work Packages tile showed '0' on query failure"
            # The tile shows the server-side count (6) since the API failed before updating it
            assert tile_text in ("6", "—"), f"Work Packages tile showed '{tile_text}' on query failure"

            # Evidence line should still show the correct work packages count
            line = page.get_by_test_id("roadmap-summary-footer").inner_text()
            assert "Work packages: 6" in line, line

            # Gaps and plateaus tiles should show correct figures
            expect(page.get_by_test_id("rdm-plateaus-tile")).to_have_text("3", timeout=PAGE_TIMEOUT)

            _frame(page, "wp-query-failure", width, theme)
        finally:
            context.close()
