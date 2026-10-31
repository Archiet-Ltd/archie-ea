"""One annual cost, whichever screen a person reads it on.

As the portfolio manager of the Lantern Quay demonstration company, a person reads the annual run cost
on the Overview, opens the applications list and adds up the cost column, then opens the most expensive
application and reads the same figure on its record, with the place it comes from. At 1440 and 390 wide.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_first_screen_one_message_journey import FOLD, PHONE, _page
from .test_overview_demo_organisations_journey import demo_org  # noqa: F401  (the seeded demonstration company)
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("COST_WALK_DIR")


def _shot(page, name, width, theme="light"):
    if WALK:
        page.wait_for_timeout(1200)
        page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, theme, width)), full_page=True)
        page.evaluate("document.documentElement.classList.remove('dark')")


def _pounds(text):
    return int(re.sub(r"[^0-9]", "", text))


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
def test_the_run_cost_the_list_and_the_record_state_one_figure_with_its_source(browser, live_server, demo_org, viewport):  # noqa: F811
    context, page = _page(browser, viewport)
    try:
        _login(page, live_server, demo_org["manager"])
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        run = page.locator('[data-testid="card-run-cost-value"]')
        expect(run).to_be_visible()
        overview_total = _pounds(run.get_attribute("title"))
        assert overview_total > 0
        assert "Appears here once" not in page.locator('[data-testid="overview-dashboard"]').inner_text()
        _shot(page, "overview", viewport["width"])
        _shot(page, "overview", viewport["width"], "dark")

        # the applications list: every application has a figure or says none is recorded, and the figures add up
        page.goto(live_server + "/applications/?page_size=100", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        rows = page.evaluate(
            """() => [...document.querySelectorAll('[data-testid^="app-row-"], [data-testid^="mobile-card-"]')]
                .filter(e => e.getBoundingClientRect().height > 0)
                .map(e => ({id: e.dataset.appId, text: e.innerText}))"""
        )
        assert len(rows) >= 15, len(rows)
        figures = {}
        for row in rows:
            match = re.search(r"£[\d,]+", row["text"])
            if match:
                figures[row["id"]] = _pounds(match.group(0))
            else:
                assert "Not recorded" in row["text"], row["text"]
        assert sum(figures.values()) == overview_total, (sum(figures.values()), overview_total)
        _shot(page, "applications", viewport["width"])
        _shot(page, "applications", viewport["width"], "dark")

        # the most expensive application's record states the same figure, and where it comes from
        top_id, top_cost = max(figures.items(), key=lambda kv: kv[1])
        page.locator('[data-app-id="%s"] a[href*="/applications/%s"]:visible' % (top_id, top_id)).first.click()
        page.wait_for_url(re.compile(r"/applications/%s(\?|$|#)" % top_id), timeout=PAGE_TIMEOUT)
        page.goto(live_server + "/applications/%s?tab=cost" % top_id, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        value = page.locator('[data-testid="annual-cost-value"]')
        expect(value).to_be_visible()
        assert _pounds(value.inner_text()) == top_cost
        source = page.locator('[data-testid="annual-cost-source"]').inner_text()
        assert source.startswith("From ") and len(source) > 10, source
        _shot(page, "record", viewport["width"])
        _shot(page, "record", viewport["width"], "dark")
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
def test_an_architect_who_may_not_see_spend_is_shown_no_cost_on_any_of_the_three_screens(browser, live_server, demo_org, viewport):  # noqa: F811
    context, page = _page(browser, viewport)
    money = re.compile(r"[£$€]\s?\d")
    try:
        _login(page, live_server, demo_org["architect"])
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="overview-dashboard"]')).to_be_visible()
        assert page.locator('[data-testid="card-run-cost-value"]').count() == 0
        assert not money.search(page.locator('[data-testid="overview-dashboard"]').inner_text())
        _shot(page, "architect-overview", viewport["width"])

        page.goto(live_server + "/applications/?page_size=100", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        link = page.locator('[data-testid^="app-row-"] a[href*="/applications/"]:visible, '
                            '[data-testid^="mobile-card-"] a[href*="/applications/"]:visible').first
        expect(link).to_be_visible()
        assert page.locator('[data-testid^="app-cost-"]').count() == 0
        assert not money.search(page.locator("main").inner_text())
        _shot(page, "architect-applications", viewport["width"])

        link.click()
        page.wait_for_url(re.compile(r"/applications/\d+"), timeout=PAGE_TIMEOUT)
        page.goto(page.url.split("?")[0] + "?tab=cost", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert page.locator('[data-testid="annual-cost-value"]').count() == 0
        assert not money.search(page.locator("main").inner_text())
        assert page.locator("main").inner_text().strip()
        _shot(page, "architect-record", viewport["width"])
    finally:
        context.close()
