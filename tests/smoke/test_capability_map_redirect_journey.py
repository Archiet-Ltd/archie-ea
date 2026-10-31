"""A business architect opens the old heat map addresses and lands on the one capability map.

Goal: "I bookmarked the maturity heat map and the dashboard heat map; both should
open the capability map, on the right measure, with the same figures as my
capabilities list."

Runs in the demonstration organisation as a business architect.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_capability_map_first_screen_journey import (  # noqa: F401
    LIST_URL,
    MAP,
    _login,
    _open,
    _shot,
    business_architect,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

OLD_ADDRESSES = [
    ("/capability-maturity/heatmap", "Maturity gap", "gap"),
    ("/dashboard/capability-heatmap", "Maturity gap", None),
]


def _stat(page, key):
    return page.locator("#capmap-stat-" + key).inner_text().strip()


@pytest.mark.parametrize("old, measure, heat", OLD_ADDRESSES)
def test_old_address_lands_on_the_map_with_the_right_measure(browser, live_server, business_architect, old, measure, heat):  # noqa: F811
    context, page = _open(browser, live_server, business_architect, 1440, 900)
    try:
        response = page.request.get(live_server + old, max_redirects=0)
        assert response.status == 302, "%s answered %s" % (old, response.status)
        location = response.headers["location"]
        assert location.split("?")[0].endswith("/capability-map/dashboard"), location
        if heat:
            assert location.endswith("?heat=" + heat), location
        else:
            assert "?" not in location, location

        page.goto(live_server + old, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.url.split("?")[0].endswith("/capability-map/dashboard"), page.url
        region = page.locator(MAP)
        expect(region.locator('[data-testid="cap-box"]').first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="capability-legend"]')).to_contain_text("Heat by: " + measure)
        expect(page.locator("#capmap-measure")).to_have_value("gap")
        assert region.locator('[data-testid="cap-box"][data-assessed="true"]').count() > 0

        # The four figures agree with the capabilities list.
        total = region.locator('[data-testid="cap-box"]').count()
        assert int(_stat(page, "total")) == total
        assert int(_stat(page, "mapped")) + int(_stat(page, "unmapped")) == total
        assert re.fullmatch(r"\d+%", _stat(page, "coverage")), _stat(page, "coverage")
        _shot(page, "c-1440-light-" + re.sub(r"\W+", "-", old.strip("/")))
        page.goto(live_server + LIST_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator(".cap-card").count() == total
    finally:
        context.close()


def test_old_address_on_a_phone_in_the_dark_keeps_the_map(browser, live_server, business_architect):  # noqa: F811
    context, page = _open(browser, live_server, business_architect, 390, 844, scheme="dark")
    try:
        for old in ("/capability-maturity/heatmap", "/dashboard/capability-heatmap"):
            page.goto(live_server + old, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.locator(MAP).locator('[data-testid="cap-box"]').first).to_be_visible(timeout=PAGE_TIMEOUT)
            expect(page.locator('[data-testid="capability-legend"]')).to_contain_text("Heat by: Maturity gap")
            _shot(page, "c-390-dark-" + re.sub(r"\W+", "-", old.strip("/")))
    finally:
        context.close()
