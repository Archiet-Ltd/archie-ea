"""A new startup CTO's first minutes: one sample control, a chat in the CTO's lens, a phone toolbar on one line.

The person: a self-serve owner who has just signed up. The goal: look around a sample company and
ask the first question, on a laptop and on a phone, without meeting a control that is offered twice,
an assistant that is not the one chosen for them, a toolbar that eats the phone screen, or a message
about the server's mail settings.

Every journey starts at /account/register as a new person. The signed-in person is the startup CTO
(lens "cto", not a platform administrator), asserted in each journey.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_sign_up_first_screen_journey import SIZES, _skip_to_the_dashboard
from .test_signup_brings_the_company_in_journey import (  # noqa: F401  (people is a fixture)
    assert_is_a_startup_cto_not_an_administrator,
    people,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _new_page(browser, viewport):
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    return context, context.new_page()


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_first_screen_offers_the_sample_once_and_says_nothing_about_the_server(browser, live_server, people, app, name, viewport):
    context, page = _new_page(browser, viewport)
    try:
        email, _ = sign_up(page, live_server, people)
        assert_is_a_startup_cto_not_an_administrator(app, email)
        _skip_to_the_dashboard(page)
        # the page sign-up lands on carries the flash, so read it before navigating again
        page.wait_for_load_state("networkidle")
        landed = page.locator("body").inner_text()
        assert "server" not in landed.lower(), landed
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("chat-first-sample")).to_be_visible()
        body = page.locator("body").inner_text()
        # one sample control, inside the first steps, and no second "Load sample" further down
        assert page.locator('[data-testid="chat-first-sample"]').count() == 1
        assert page.locator('[data-testid="startup-first-steps"] [data-testid="chat-first-sample"]').count() == 1
        assert page.get_by_role("button", name=re.compile("sample", re.I)).count() == 1
        assert "Load sample" not in body
        # no flash or any text names the server's mail settings
        assert "server" not in body.lower().replace("serverless", ""), body
        assert "E-mail is not available" not in body
    finally:
        context.close()


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_chat_opens_in_the_ctos_lens_with_a_one_line_toolbar(browser, live_server, people, app, name, viewport):
    context, page = _new_page(browser, viewport)
    try:
        email, _ = sign_up(page, live_server, people)
        assert_is_a_startup_cto_not_an_administrator(app, email)
        _skip_to_the_dashboard(page)
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("chat-first-sample").click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)

        # the dashboard's question line is in the body font, not a serif italic
        line = page.locator(".question-line").first
        if line.count():
            style = line.evaluate("e => { const s = getComputedStyle(e); return [s.fontStyle, s.fontFamily]; }")
            assert style[0] == "normal", style
            assert "serif" not in style[1].replace("sans-serif", ""), style

        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        # the assistant and scope sit behind Chat options, two clicks from the question box
        selector = page.locator("#persona-selector")
        expect(selector).to_be_hidden()
        page.get_by_role("button", name="Chat options").click()
        expect(selector).to_be_visible()
        chosen = selector.evaluate("e => e.options[e.selectedIndex].text")
        assert "Enterprise Architect" not in chosen, chosen
        assert re.search(r"CTO|CIO|Executive", chosen), chosen
        expect(page.locator("#domain-selector")).to_be_visible()
        page.wait_for_timeout(600)  # the sheet has finished sliding in
        box = selector.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"]
        page.keyboard.press("Escape")
        expect(selector).to_be_hidden()

        # the whole header, not just its control row: nothing may sit on a line of its own above it
        header = page.locator('[data-testid="chat-header-row"]')
        height = header.evaluate("e => e.getBoundingClientRect().height")
        assert height <= 72, height
        # the composer is on screen with the toolbar
        expect(page.locator("#user-input")).to_be_in_viewport()
    finally:
        context.close()
