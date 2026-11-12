"""A visitor reads what Entelim does on the home page and finds what is not open yet, with no timetable promised.

Goal: "Which of these can I use, and which are still to come?" The home page's capability summary names no quarter or
roadmap; it points to Coming soon on the features page, and that link lands on the list of capabilities that are not
open yet, each of which opens a page with a heading.
"""
import pytest
from playwright.sync_api import expect

from tests.test_public_timeline_words import TIMETABLE

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_the_home_page_sends_a_visitor_to_coming_soon_without_a_timetable(browser, live_server):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        sentence = page.get_by_text("Capabilities that are not open to everyone yet").first
        expect(sentence).to_be_visible(timeout=PAGE_TIMEOUT)
        assert not TIMETABLE.search(sentence.inner_text()), sentence.inner_text()
        sentence.get_by_role("link", name="Coming soon").click()
        page.wait_for_url(lambda url: "/features" in url, timeout=PAGE_TIMEOUT)
        heading = page.locator("#coming-soon")
        expect(heading).to_have_text("Coming soon", timeout=PAGE_TIMEOUT)
        items = page.locator("#coming-soon + p + ul a")
        assert items.count() >= 1, "Coming soon lists nothing"
        assert not TIMETABLE.search(page.inner_text("main")), TIMETABLE.search(page.inner_text("main")).group(0)
        first = items.first.get_attribute("href")
        items.first.click()
        page.wait_for_url(lambda url: url.rstrip("/").endswith(first.rstrip("/")), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", level=1)).not_to_be_empty(timeout=PAGE_TIMEOUT)
    finally:
        context.close()
