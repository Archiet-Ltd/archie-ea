"""A business architect colours the capability map by annual cost and by maturity gap.

Goal: "Show me which capabilities cost the most, and which are furthest from the
maturity they should have, on the one capability map."

Runs in the demonstration organisation as a business architect. Each measure must
colour the map: the key names the measure and its steps, and at least two boxes
sit at different steps.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_capability_map_first_screen_journey import MAP, _open, business_architect  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

MAP_URL = "/capability-map/dashboard"

MEASURES = {
    "cost": ("Annual cost", ["Under 25%", "25% to 50%", "50% to 75%", "75% and over"]),
    "gap": ("Maturity gap", ["On target", "1 level short", "2 levels short", "3+ levels short"]),
}


@pytest.mark.parametrize("measure", ["gap", "cost"])
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
