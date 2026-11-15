"""A founder who has just read the home page opens Sign in and Create account.

The goal, in a person's words: "Is this the same product I just read about, and what am I agreeing to?"
The visitor is signed out, opens the real pages, and follows both links under Create account.
"""
import os
import re

import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PROMISE = "The company brain for your business"
AUDIENCE = "Built for founders, leadership teams and architects."
SCREENS = {
    "1440-light": ({"width": 1440, "height": 900}, "light"),
    "390-dark": ({"width": 390, "height": 844}, "dark"),
}


def _shot(page, name):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.screenshot(path=os.path.join(folder, name + ".png"))


def _text(where):
    where = where.locator("body") if hasattr(where, "viewport_size") else where
    return re.sub(r"\s+", " ", where.inner_text())


def _context(browser, screen):
    viewport, scheme = SCREENS[screen]
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    return context


def test_sign_in_makes_the_home_pages_promise(browser, live_server):
    context = _context(browser, "1440-light")
    page = context.new_page()
    try:
        page.goto(live_server + "/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        promise = page.get_by_text(PROMISE, exact=True)
        assert promise.count() == 1 and promise.first.is_visible()
        text = _text(page)
        assert AUDIENCE in text
        assert "TOGAF 9.2" not in text and "consulting bench" not in text and " -- " not in text
        _shot(page, "login-1440-light")
    finally:
        context.close()


@pytest.mark.parametrize("screen", sorted(SCREENS))
def test_create_account_makes_the_promise_and_both_links_have_content(browser, live_server, screen):
    context = _context(browser, screen)
    page = context.new_page()
    try:
        page.goto(live_server + "/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_text(PROMISE, exact=True).first.is_visible()
        assert AUDIENCE in _text(page)
        terms = page.locator('[data-testid="register-terms"]')
        assert "By creating an account you agree to the Terms and the Privacy Policy" in _text(terms)
        _shot(page, "register-" + screen)
        for name in ("Terms", "Privacy Policy"):
            page.goto(live_server + "/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.locator('[data-testid="register-terms"]').get_by_role("link", name=name).click()
            page.wait_for_load_state("domcontentloaded")
            assert "/register" not in page.url, name
            assert len(page.locator("main, body").first.inner_text().strip()) > 300, name
            assert page.locator("h1").count() >= 1, name
    finally:
        context.close()
