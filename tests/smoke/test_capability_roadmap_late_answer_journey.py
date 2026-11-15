"""A slow first answer on the capability roadmap never puts older figures or lists back after a save.

An architect with three plateaus and six work packages opens the capability roadmap while the first
read of the work packages, the gaps or the plateaus is slow. They add a work package (or a gap, or a
plateau): the tile, the list and, for a package, the timeline show it. The slow first answer then
arrives, and the tile, the list and the timeline still show it. Another organisation's packages are on none.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .test_capability_roadmap_figures_journey import PATH, _frame, _page, _plan

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.mark.parametrize("held", ["work-packages", "gaps", "plateaus"])
def test_a_late_first_answer_does_not_replace_the_figures_after_a_save(browser, live_server, held):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    _plan(browser, live_server, org["emails"]["enterprise_architect"], org["suffix"])
    _plan(browser, live_server, other["emails"]["enterprise_architect"], other["suffix"])

    for round_number, (width, theme) in enumerate(((1440, "light"), (390, "dark"))):
        before = 6 + (round_number if held == "work-packages" else 0)
        after = before + 1
        plateaus_after = 4 + (round_number if held == "plateaus" else 0)
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
            page.route(re.compile(r".*/capability-map/api/roadmap/%s(\?.*)?$" % held), slow_first_gaps)
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            tile = page.get_by_test_id("rdm-work-packages-tile")
            line = page.get_by_test_id("roadmap-summary-footer")
            expect(tile).to_have_text(str(before), timeout=PAGE_TIMEOUT)

            if held == "gaps":
                recorded = page.get_by_test_id("rdm-recorded-gaps")
                signals = page.get_by_test_id("rdm-gap-signals")
                page.get_by_role("button", name="Add Gap").click()
                page.fill("#rdm-gap-name", "Late gap " + org["suffix"])
                page.locator("#rdm-create-gap-modal").get_by_role("button", name="Create Gap").click()
                expect(recorded).to_have_text(re.compile(r"^[1-9][0-9]*$"), timeout=PAGE_TIMEOUT)
                recorded_after_save = recorded.inner_text()
                signals_after_save = signals.inner_text()
                assert state["arrived"] is False, "the first answer arrived before the save"
                state["release"] = True
                for _ in range(100):
                    if state["arrived"]:
                        break
                    page.wait_for_timeout(100)
                assert state["arrived"] is True
                page.wait_for_timeout(500)
                expect(recorded).to_have_text(recorded_after_save)
                assert signals.inner_text() == signals_after_save
                page.get_by_role("button", name="Add Work Package").click()
                page.fill("#rdm-wp-gap", "Late gap " + org["suffix"])
                expect(page.locator("#rdm-create-wp-modal").get_by_role("button", name="Late gap " + org["suffix"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
                _frame(page, "late-answer-gaps", width, theme)
                continue

            if held == "plateaus":
                page.get_by_role("button", name="Add Plateau").click()
                page.fill("#rdm-plateau-name", "Late plateau " + org["suffix"])
                page.fill("#rdm-plateau-order", str(plateaus_after))
                page.locator("#rdm-create-plateau-modal").get_by_role("button", name="Create Plateau").click()
                expect(page.get_by_test_id("rdm-plateaus-tile")).to_have_text(str(plateaus_after), timeout=PAGE_TIMEOUT)
                expect(page.get_by_test_id("rdm-plateau")).to_have_count(plateaus_after, timeout=PAGE_TIMEOUT)
                state["release"] = True
                for _ in range(100):
                    if state["arrived"]:
                        break
                    page.wait_for_timeout(100)
                assert state["arrived"] is True
                page.wait_for_timeout(500)
                expect(page.get_by_test_id("rdm-plateaus-tile")).to_have_text(str(plateaus_after))
                expect(page.get_by_test_id("rdm-plateau")).to_have_count(plateaus_after)
                _frame(page, "late-answer-plateaus", width, theme)
                continue

            page.get_by_role("button", name="Add Work Package").click()
            page.fill("#rdm-wp-name", "Late package " + org["suffix"])
            page.fill("#rdm-wp-start", "2026-11-01")
            page.fill("#rdm-wp-target", "2027-01-31")
            page.locator("#rdm-create-wp-modal").get_by_role("button", name="Create Work Package").click()
            expect(tile).to_have_text(str(after), timeout=PAGE_TIMEOUT)
            expect(line).to_contain_text("Work packages: %d" % after, timeout=PAGE_TIMEOUT)
            assert state["arrived"] is False, "the first answer arrived before the save"
            new_package = page.get_by_test_id("roadmap-wp-link").filter(has_text="Late package " + org["suffix"])
            expect(new_package.first).to_be_visible(timeout=PAGE_TIMEOUT)
            on_timeline = page.locator(".rdm-gantt-name").filter(has_text="Late package " + org["suffix"])
            expect(on_timeline.first).to_be_visible(timeout=PAGE_TIMEOUT)
            rows_after_save = page.get_by_test_id("roadmap-wp-link").count()
            shown_new = new_package.count()

            state["release"] = True
            for _ in range(100):
                if state["arrived"]:
                    break
                page.wait_for_timeout(100)
            assert state["arrived"] is True
            page.wait_for_timeout(500)

            expect(tile).to_have_text(str(after))
            expect(page.get_by_test_id("roadmap-wp-link")).to_have_count(rows_after_save)
            expect(new_package).to_have_count(shown_new)
            expect(on_timeline.first).to_be_visible()
            # Two of the six planned packages are parts of another, so the list holds the tile's figure less two.
            assert rows_after_save == after - 2, (rows_after_save, after)
            expect(line).to_contain_text("Work packages: %d" % after)
            assert "Work packages: %d" % before not in line.inner_text()
            assert other["suffix"] not in page.content()
            footers = page.get_by_test_id("roadmap-wp-footer")
            for index in range(footers.count()):
                box = footers.nth(index).bounding_box()
                scroller = footers.nth(index).evaluate(
                    "e => { let n = e.parentElement; while (n && n.scrollWidth <= n.clientWidth) n = n.parentElement;"
                    " return n ? n.getBoundingClientRect().right : 0 }")
                assert box["x"] + box["width"] <= min(width, scroller or width) + 1, (index, box, width)
            _frame(page, "late-answer", width, theme)
        finally:
            context.close()
