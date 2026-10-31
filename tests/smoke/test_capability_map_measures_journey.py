"""A business architect colours the capability map by maturity gap; annual cost states it is not recorded.

Goal: "Show me which capabilities are furthest from the
maturity they should have, on the one capability map."

Runs in the demonstration organisation as a business architect. Each measure must
colour the map: the key names the measure and its steps, and at least two boxes
sit at different steps.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_capability_map_first_screen_journey import MAP, _open, _shot, business_architect  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

MAP_URL = "/capability-map/dashboard"

MEASURES = {
    "gap": ("Maturity gap", ["On target", "1 level short", "2 levels short", "3+ levels short"]),
}


@pytest.mark.parametrize("measure", ["gap"])
def test_the_map_is_coloured_by_the_measure_and_the_key_names_it(browser, live_server, business_architect, measure):  # noqa: F811
    name, steps = MEASURES[measure]
    context, page = _open(browser, live_server, business_architect, 1440, 900)
    try:
        page.goto(live_server + MAP_URL + "?heat=" + measure, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        region = page.locator(MAP)
        expect(region.locator('[data-testid="cap-box"]').first).to_be_visible(timeout=PAGE_TIMEOUT)
        legend = page.locator('[data-testid="capability-legend"]')
        expect(legend).to_contain_text("Heat by: " + name, timeout=PAGE_TIMEOUT)
        for step in steps:
            expect(legend).to_contain_text(step)

        boxes = region.locator('[data-testid="cap-box"]')
        assessed = region.locator('[data-testid="cap-box"][data-assessed="true"]').count()
        total = boxes.count()
        assert assessed > 0, "%s: all %d boxes read Not assessed, so nothing is coloured" % (measure, total)
        steps_seen = set(
            region.locator('[data-testid="cap-box"][data-assessed="true"] > button').evaluate_all(
                "els => els.map(e => (e.className.match(/bg-heat-\\d/) || ['none'])[0])"
            )
        )
        assert len(steps_seen) >= 2, "%s: boxes differ in step %s" % (measure, sorted(steps_seen))
    finally:
        context.close()


def test_cost_measure_says_it_is_not_recorded_and_colours_nothing(browser, live_server, business_architect):  # noqa: F811
    context, page = _open(browser, live_server, business_architect, 1440, 900)
    try:
        option = page.locator('#capmap-measure option[value="cost"]')
        assert option.inner_text().strip() == "Annual cost: not recorded yet"
        page.goto(live_server + "/capability-map/dashboard?heat=cost", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        region = page.locator(MAP)
        expect(region.locator('[data-testid="cap-box"]').first).to_be_visible(timeout=PAGE_TIMEOUT)
        legend = page.locator('[data-testid="capability-legend"]')
        expect(legend).to_contain_text("not recorded for capabilities yet")
        assert region.locator('[data-testid="cap-box"][data-assessed="true"]').count() == 0
        _shot(page, "c-1440-light-cost")
    finally:
        context.close()
