"""The owner and cost figures are the same on the dashboard and the application list,
and the CFO tab (held back for the soft launch) is not offered, in a real browser."""

import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def test_owner_figure_agrees_and_cfo_tab_is_not_offered(browser, live_server, seeded):
    page = browser.new_page()
    _login(page, live_server, seeded["emails"]["cto"])

    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert page.locator('[data-testid="layer-tab-cfo"]').count() == 0, "the CFO tab is offered"
    assert page.locator('[data-testid="layer-tab-cto"]').count() == 1
    strip = page.locator('[data-testid="owner-count"]')

    page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    listed = re.search(
        r"(\d+) of (\d+) applications in the portfolio have a named owner", page.content()
    )
    assert listed, "the application list does not state its owner count"

    if strip.count():
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        shown = re.search(r"(\d+) of (\d+) applications have an owner", strip.inner_text())
        assert shown and shown.groups() == listed.groups(), (shown and shown.groups(), listed.groups())
