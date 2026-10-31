"""A platform administrator reads the deployment's security posture.

The page reports what this deployment really sends and counts; it has no setting to
change, so there is nothing to turn on here. What a person needs from it:

* the response headers are listed under column headings that name what each column is
  (name and value), not the same heading twice;
* the rate limiter's state is said in a sentence, not as a dash;
* the page is inside the shell, with the headings a screen reader needs;
* a security architect who is not a platform administrator is refused;
* nothing scrolls sideways on a phone.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_administrator_reads_the_headers_and_the_limiter_state_in_words(browser, live_server, seeded):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        assert page.goto(live_server + "/admin/security", timeout=PAGE_TIMEOUT).status == 200
        expect(page.get_by_text("Response headers", exact=True)).to_be_visible()
        expect(page.get_by_text("Rate limit", exact=True)).to_be_visible()

        heads = [t.strip() for t in page.locator("th").all_text_contents()]
        assert "Header" in heads and "Value" in heads, f"column headings are {heads}"
        assert len(heads) == len(set(heads)), f"a column heading repeats: {heads}"
        # A real header this deployment sends is listed with its value.
        expect(page.get_by_role("cell", name="X-Content-Type-Options")).to_be_visible()

        text = re.sub(r"\s+", " ", page.locator("main").inner_text())
        assert "— Not" not in text and "— Not" not in text, "a dash stands in for an explanation"
        assert re.search(r"rate limiter", text, re.I), "the limiter's state is not stated"
        assert page.locator("main h1").count() == 1
    finally:
        page.close()


def test_a_security_architect_who_is_not_a_platform_administrator_is_refused(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["security_architect"])
        assert page.goto(live_server + "/admin/security", timeout=PAGE_TIMEOUT).status == 403
    finally:
        page.close()


def test_the_page_holds_no_horizontal_scroll_on_a_phone(browser, live_server, seeded):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/admin/security", timeout=PAGE_TIMEOUT)
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, f"page scrolls sideways by {overflow}px"
    finally:
        page.close()
