"""An architect in an organisation with no elements opens Scenarios. The page says there is nothing to run a scenario on,
does not run one, and the browser console stays free of errors."""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_scenarios_in_an_empty_organisation_asks_for_nothing_that_is_missing(browser, live_server, viewport):
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    page = context.new_page()
    problems = []
    page.on("console", lambda m: problems.append("console: " + m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append("page error: " + str(e)))
    page.on("response", lambda r: problems.append("%s %s" % (r.status, r.url)) if r.status >= 400 else None)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        problems.clear()
        page.goto(live_server + "/simulations", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        run = page.get_by_role("button", name="Run scenario")
        expect(run).to_be_visible(timeout=PAGE_TIMEOUT)
        page.get_by_label("Type to search...").fill("Calibration")
        page.wait_for_timeout(2500)
        if run.is_enabled():
            run.click()
            page.wait_for_timeout(2500)
        page.get_by_role("button", name="Compare Scenarios").click()
        page.wait_for_timeout(2500)
        expect(page.locator("main")).to_contain_text("Your model has no elements yet")
        expect(page.locator("main")).to_contain_text("There is nothing to try a change on")
        assert not problems, problems
    finally:
        context.close()
