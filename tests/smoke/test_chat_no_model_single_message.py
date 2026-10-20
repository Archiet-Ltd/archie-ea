"""With no model configured the chat says so once: the banner, not also an error card."""

import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_a_question_nothing_matches_gets_one_message_not_two(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/ai-chat/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("#user-input")
        page.fill("#user-input", "What is the weather today?")
        page.click("#send-btn")
        page.wait_for_timeout(4000)
        banner = page.locator("[data-without-model]")
        assert banner.count() == 1
        cards = page.locator("#messages-container [role=alert]")
        assert cards.count() == 0, cards.all_inner_texts()
    finally:
        page.close()
