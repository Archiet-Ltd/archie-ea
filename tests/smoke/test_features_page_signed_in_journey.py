"""A signed-in architect reads the public features page and finds Scenarios among what they can use.

In the Lantern Quay Systems demonstration organisation, as its architect:
- opens /features and reads Scenarios in an "Available now" list, with a link that opens the working
  Simulations screen (it has content, not the waiting list);
- finds Scenarios nowhere under "Coming soon", and the waiting-list sentence only above the items that
  are still not available;
- as a visitor who is not signed in, still reads Scenarios under "Coming soon" and the page never
  calls it planned.
"""
import os

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_simulation_journey import VIEWPORT_IDS, VIEWPORTS, _shot, _sign_in, demo  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WAITING = "These are not available yet. Open one to join its waiting list"


def _sections(page):
    """The Available now and Coming soon lists of the page, as text, from the rendered page."""
    return {
        "available": page.locator("h2#available-now + p + ul"),
        "waiting": page.locator("h2#coming-soon + p + ul"),
    }


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_a_signed_in_person_finds_scenarios_among_what_is_available(browser, live_server, demo, viewport, scheme):
    context, page = _sign_in(browser, live_server, viewport, scheme)
    try:
        page.goto(live_server + "/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        lists = _sections(page)
        expect(page.locator("h2#available-now")).to_have_text("Available now")
        link = lists["available"].get_by_role("link", name="Scenarios")
        expect(link).to_have_attribute("href", "/simulations")
        expect(lists["waiting"].get_by_role("link", name="Scenarios")).to_have_count(0)
        # the waiting-list sentence sits above what is still waiting, and that list is not empty
        expect(page.locator("h2#coming-soon + p")).to_contain_text(WAITING)
        expect(lists["waiting"].locator("li")).not_to_have_count(0)
        assert "planned" not in page.locator("h3#simulation-and-scenario-planning ~ p").first.inner_text().lower()
        _shot(page, "features-signed-in", viewport, scheme)

        link.click()
        page.wait_for_url("**/simulations", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("early-access-page").count() == 0
        expect(page.get_by_role("heading", level=1)).to_have_text("Simulations")
        expect(page.get_by_role("button", name="Run simulation")).to_be_visible()
    finally:
        context.close()


def test_a_visitor_who_is_not_signed_in_still_reads_scenarios_as_coming_soon(browser, live_server):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        page.goto(live_server + "/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("h2#available-now")).to_have_count(0)
        waiting = _sections(page)["waiting"]
        expect(waiting.get_by_role("link", name="Scenarios")).to_have_attribute("href", "/modules/scenario-modelling")
    finally:
        context.close()


SENTENCE = "lets you run a change on a copy of the"


@pytest.fixture(scope="module", params=["1", "2", "3"], ids=["wave-1", "wave-2", "wave-3"])
def wave_server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": request.param}), request.param


def test_the_features_page_promotes_scenarios_only_in_the_release_wave_that_opens_them(browser, wave_server, demo):
    """A signed-in architect is told Scenarios is available only where the menu offers it and its screen opens."""
    server, wave = wave_server
    context, page = _sign_in(browser, server, VIEWPORTS[0][0], VIEWPORTS[0][1])
    try:
        page.goto(server + "/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        body = page.locator("main")
        if wave == "3":
            expect(page.locator("h2#available-now")).to_have_text("Available now")
            expect(body).to_contain_text(SENTENCE)
            page.locator("h2#available-now + p + ul").get_by_role("link", name="Scenarios").click()
            page.wait_for_url("**/simulations", timeout=PAGE_TIMEOUT)
            expect(page.get_by_role("heading", level=1)).to_have_text("Simulations")
            expect(page.get_by_role("button", name="Run simulation")).to_be_visible()
        else:
            expect(page.locator("h2#available-now")).to_have_count(0)
            expect(page.get_by_text("You are signed in, so these open on the working screen.")).to_have_count(0)
            expect(body).not_to_contain_text(SENTENCE)
            # every link to Scenarios on the page still goes to where it opened before, never to the working screen
            hrefs = page.locator("main a", has_text="Scenarios").evaluate_all("els => els.map(e => e.getAttribute('href'))")
            assert hrefs and "/simulations" not in hrefs, hrefs
    finally:
        context.close()
