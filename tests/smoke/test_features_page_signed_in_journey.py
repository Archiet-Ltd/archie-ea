"""A signed-in architect reads the public features page and finds Scenarios among what they can use.

In the Lantern Quay Systems demonstration organisation, as its architect:
- opens /features and reads Scenarios in an "Available now" list, with a link that opens the working
  Simulations screen (it has content, not the waiting list);
- finds Scenarios nowhere under "Coming soon", and the waiting-list sentence only above the items that
  are still not available;
- as a visitor who is not signed in, once the Scenarios screen is released, reads it among what is
  available and never under "Coming soon"; the page never calls it planned.
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
    """A visitor reads a released Scenarios as available, never under Coming soon: one release check for everyone.

    The name is kept from the version of this test that held a released server to the old rule, where a
    visitor was shown Scenarios under Coming soon; the rule is now the one the lead set: a released item
    leaves Coming soon for a visitor as it does for a signed-in person.
    """
    context = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        page.goto(live_server + "/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("h2#available-now")).to_have_text("Available now")
        lists = _sections(page)
        expect(lists["available"].get_by_role("link", name="Scenarios")).to_have_attribute("href", "/modules/scenario-modelling")
        expect(lists["waiting"].get_by_role("link", name="Scenarios")).to_have_count(0)
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


VIEWPORTS_BOTH = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]


def _scenarios_links(page):
    """Every link on /features whose text names Scenarios, as (text, href) read from the rendered page."""
    return page.locator("main a", has_text="Scenarios").evaluate_all(
        "els => els.map(e => [e.innerText.trim(), e.getAttribute('href')])")


@pytest.mark.parametrize("signed_in", [False, True], ids=["visitor", "signed-in"])
@pytest.mark.parametrize("viewport,scheme", VIEWPORTS_BOTH, ids=["1440-light", "390-dark"])
def test_every_scenarios_link_lands_on_a_page_that_agrees_with_the_section(browser, wave_server, demo, signed_in, viewport, scheme):
    """The Scenarios section and each page its links open say the same thing about the release wave."""
    server, wave = wave_server
    if signed_in:
        context, page = _sign_in(browser, server, viewport, scheme)
    else:
        context = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True)
        context.set_default_timeout(PAGE_TIMEOUT)
        page = context.new_page()
    try:
        page.goto(server + "/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        section = page.locator("h3#simulation-and-scenario-planning ~ p").first
        text = section.inner_text()
        opened = wave == "3"
        if opened:
            assert text.startswith("Available."), text
            assert "Early access" not in text and "waiting list" not in text and "not open yet" not in text
        else:
            assert text.startswith("Early access."), text
            for words in ("cost", "risk", "resilience", "capacity", "before money is committed", "join the waiting list"):
                assert words in text, (words, text)
            assert "Once you are signed in" not in text and "can use" not in text
        _shot(page, "features-scenarios-w%s-%s" % (wave, "in" if signed_in else "out"), viewport, scheme)

        links = _scenarios_links(page)
        assert links, "the page has no link to Scenarios"
        for label, href in links:
            page.goto(server + "/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.locator("main a[href='%s']" % href, has_text=label).first.click()
            page.wait_for_load_state("domcontentloaded")
            body = page.locator("main").inner_text()
            if opened and signed_in:
                assert page.url.endswith("/simulations"), (label, href, page.url)
                expect(page.get_by_role("heading", level=1)).to_have_text("Simulations")
                expect(page.get_by_role("button", name="Run simulation")).to_be_visible()
            elif opened:
                assert page.url.endswith("/modules/scenario-modelling"), (label, href, page.url)
                assert "Available now" in body and "Not built yet" not in body and "waiting list" not in body.lower(), body
                assert "What Scenarios does" in body
            else:
                # the section sends everyone to the waiting list, never to the working screen
                assert page.url.endswith("/modules/scenario-modelling"), (label, href, page.url)
                assert "/simulations" not in href
                assert "Not built yet" in body and "Join the waiting list" in body, body
                assert "Available now" not in body
    finally:
        context.close()


@pytest.mark.parametrize("signed_in", [False, True], ids=["visitor", "signed-in"])
def test_the_module_page_agrees_with_the_wave_when_opened_directly(browser, wave_server, demo, signed_in):
    server, wave = wave_server
    if signed_in:
        context, page = _sign_in(browser, server, VIEWPORTS_BOTH[0][0], "light")
    else:
        context = browser.new_context(viewport=VIEWPORTS_BOTH[0][0], ignore_https_errors=True)
        context.set_default_timeout(PAGE_TIMEOUT)
        page = context.new_page()
    try:
        page.goto(server + "/modules/scenario-modelling", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        if wave == "3" and signed_in:
            assert page.url.endswith("/simulations"), page.url
            expect(page.get_by_role("button", name="Run simulation")).to_be_visible()
        elif wave == "3":
            assert page.url.endswith("/modules/scenario-modelling")
            body = page.locator("main").inner_text()
            assert "Available now" in body and "Not built yet" not in body
        else:
            assert page.url.endswith("/modules/scenario-modelling")
            body = page.locator("main").inner_text()
            assert "Not built yet" in body and "Available now" not in body
    finally:
        context.close()


def _scenarios_as_listed(page, selector):
    """Each link named Scenarios inside *selector*, as (href, availability) read from the rendered page.

    Availability is the data-availability of the link or of the nearest ancestor that carries one (the footer
    sets it on its Coming soon column), so a released Scenarios reads "live" wherever it sits.
    """
    return page.locator(selector).evaluate_all(
        """els => els.filter(e => e.innerText.trim().startsWith('Scenarios')).map(e => {
            const holder = e.closest('[data-availability]');
            return [e.getAttribute('href'), holder ? holder.getAttribute('data-availability') : 'live', e.innerText.trim()];
        })""")


def _lands_on_what_it_says(page, server, href, released, signed_in, where):
    """Follow *href* and assert the landing page has the content the wave promises: working screen, available page, or waiting list."""
    page.goto(server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    if released and signed_in:
        assert page.url.endswith("/simulations"), (where, page.url)
        expect(page.get_by_role("button", name="Run simulation")).to_be_visible()
        return
    assert page.url.endswith("/modules/scenario-modelling"), (where, page.url)
    body = page.locator("main").inner_text()
    form = page.locator("[data-testid='feature-interest-form']")
    if released:
        assert "Available now" in body and "Not built yet" not in body and "What Scenarios does" in body, (where, body[:300])
        expect(form).to_have_count(0)
        expect(page.get_by_role("link", name="Start free").first).to_be_visible()
    else:
        assert "Not built yet" in body and "Available now" not in body, (where, body[:300])
        expect(form).to_have_count(1)


@pytest.mark.parametrize("signed_in", [False, True], ids=["visitor", "signed-in"])
@pytest.mark.parametrize("viewport,scheme", VIEWPORTS_BOTH, ids=["1440-light", "390-dark"])
def test_every_surface_says_the_same_about_scenarios_and_every_link_lands_on_what_it_promises(
        browser, wave_server, demo, signed_in, viewport, scheme):
    """A person asking "can I use Scenarios yet?" gets one answer from the features page, menu, footer, home page and module page.

    At waves 1 and 2 every surface says coming soon and every link lands on the waiting list; at wave 3 every
    surface says available, none offers a waiting list, and every link lands on the working screen (signed in)
    or on a page that says "Available now" with no waiting-list form (visitor).
    """
    server, wave = wave_server
    released = wave == "3"
    if signed_in:
        context, page = _sign_in(browser, server, viewport, scheme)
    else:
        context = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True)
        context.set_default_timeout(PAGE_TIMEOUT)
        page = context.new_page()
    target = "/simulations" if released and signed_in else "/modules/scenario-modelling"
    availability = "live" if released else "early_access"
    tag = "w%s-%s" % (wave, "in" if signed_in else "out")
    try:
        page.goto(server + "/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        lists = _sections(page)
        if released:
            expect(page.locator("h2#available-now")).to_have_text("Available now")
            expect(lists["available"].get_by_role("link", name="Scenarios")).to_have_attribute("href", target)
            expect(lists["waiting"].get_by_role("link", name="Scenarios")).to_have_count(0)
            # what is still waiting keeps the waiting-list sentence; Scenarios does not sit under it
            expect(page.locator("h2#coming-soon + p")).to_contain_text(WAITING)
            if signed_in:
                expect(page.locator("main")).not_to_contain_text("Sign in to open it")
            else:
                expect(page.locator("main")).to_contain_text("Sign in to open it")
        else:
            expect(page.locator("h2#available-now")).to_have_count(0)
            expect(lists["waiting"].get_by_role("link", name="Scenarios")).to_have_attribute("href", "/modules/scenario-modelling")
        _shot(page, "scenarios-features-%s" % tag, viewport, scheme)

        menu = _scenarios_as_listed(page, "header a[data-menu-item]")
        assert menu, "the menu has no Scenarios row"
        for href, state, text in menu:
            assert (href, state) == (target, availability), ("menu", menu)
        footer = _scenarios_as_listed(page, "footer a[href]")
        assert footer, "the footer has no Scenarios link"
        for href, state, text in footer:
            assert (href, state) == (target, availability), ("footer", footer)

        page.goto(server + "/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for where, hrefs in (("menu", menu), ("footer", footer)):
            _lands_on_what_it_says(page, server, hrefs[0][0], released, signed_in, where)

        if not signed_in:
            # the home page is the visitor's: it names Scenarios once, by the same entry as the menu and the footer
            page.goto(server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            card = page.locator(".public-card a[href='%s']" % target, has_text="Scenarios").first
            expect(card).to_be_visible()
            want = "Scenarios" if released else "Scenarios (coming soon)"
            assert card.inner_text().strip() == want, card.inner_text()
            _shot(page, "scenarios-home-%s" % tag, viewport, scheme)
            card.click()
            page.wait_for_load_state("domcontentloaded")
            _lands_on_what_it_says(page, server, target, released, signed_in, "home")
            _shot(page, "scenarios-module-%s" % tag, viewport, scheme)
    finally:
        context.close()
