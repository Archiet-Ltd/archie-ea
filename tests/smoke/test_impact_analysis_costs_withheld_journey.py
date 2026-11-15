"""Analyze Impact follows the one cost rule: a person whose costs are withheld reads no money figure there.

In the Lantern Quay Systems demonstration organisation:
- the demonstration architect, whom the rule withholds costs from, analyses the retirement of an application and
  reads "Withheld" with the reason in words under TCO at Risk, and no pound figure anywhere on the page;
- an architect of the same organisation who administers it, and so may read costs, analyses the same change and
  reads the running cost at risk with the reason it was worked out from.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_simulation_journey import DEMO_EMAIL, VIEWPORT_IDS, VIEWPORTS, _cost_reader, _pick, demo  # noqa: F401
from .test_simulation_screen_and_saved_scenarios_journey import _context, _shot, _sign_in_as

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WITHHELD = "withheld for your role"
MONEY = re.compile(r"[£$€]\s?\d")


def _analyse(page, live_server):
    page.goto(live_server + "/strategic/impact-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _pick(page)
    page.get_by_role("button", name="Analyze Impact").click()
    expect(page.get_by_test_id("tco-at-risk")).not_to_have_text("", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("tco-at-risk-reason")).not_to_have_text("", timeout=PAGE_TIMEOUT)
    return page.get_by_test_id("tco-at-risk"), page.get_by_test_id("tco-at-risk-reason")


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_the_architect_whose_costs_are_withheld_reads_no_cost_in_analyze_impact(browser, live_server, app, demo, viewport, scheme):
    context = _context(browser, viewport, scheme)
    try:
        page = _sign_in_as(context, live_server, DEMO_EMAIL)
        figure, reason = _analyse(page, live_server)
        expect(figure).to_have_text("Withheld")
        expect(reason).to_contain_text(WITHHELD)
        assert not MONEY.search(page.locator("main").inner_text()), page.locator("main").inner_text()
        figure.scroll_into_view_if_needed()
        _shot(page, "impact-cost-withheld", viewport, scheme)
    finally:
        context.close()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_an_architect_who_may_read_costs_reads_the_cost_at_risk_in_analyze_impact(browser, live_server, app, demo, viewport, scheme):
    reader = _cost_reader(app)
    context = _context(browser, viewport, scheme)
    try:
        page = _sign_in_as(context, live_server, reader)
        figure, reason = _analyse(page, live_server)
        expect(figure).to_contain_text(MONEY)
        expect(reason).to_contain_text("Yearly running cost")
        assert WITHHELD not in page.locator("main").inner_text()
        figure.scroll_into_view_if_needed()
        _shot(page, "impact-cost-admitted", viewport, scheme)
    finally:
        context.close()
