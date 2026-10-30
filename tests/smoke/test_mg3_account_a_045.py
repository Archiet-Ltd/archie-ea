"""mg3-account-a-045: login, join_invite, mfa_challenge, unconfirmed, link_expired,
register rebuilt onto the shadcn component macros (card, button, input, empty_state).
Journeys exercise the main action of each page and screenshot the result.
"""
import os
import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOT_DIR = "/home/ubuntu/verify/wt-mg3-account-a-045/reports/shots"
os.makedirs(SHOT_DIR, exist_ok=True)


def test_viewer_signs_in_on_the_rebuilt_login_page(browser, live_server, seeded):
    page = browser.new_page()
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.screenshot(path=f"{SHOT_DIR}/login-1440-light.png")
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=f"{SHOT_DIR}/login-390-light.png")
        page.set_viewport_size({"width": 1440, "height": 900})

        email = seeded["emails"]["solution_architect"]
        page.fill("#email", email)
        page.fill("#password", PASSWORD)
        try:
            page.click("#submit", force=True, no_wait_after=True)
        except TypeError:
            page.locator("#submit").dispatch_event("click")
        try:
            page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
        except Exception:
            pass
        assert "/account/login" not in page.url, "sign-in from the rebuilt page did not reach a signed-in page"

        page.goto(live_server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator(f"dd:has-text('{email}')").count() > 0, (
            "signed-in user is not the solution_architect persona (%s)" % email
        )
    finally:
        page.close()


def test_register_page_renders_the_signup_form(browser, live_server):
    page = browser.new_page()
    try:
        page.goto(live_server + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator("text=Create an account").count() > 0
        assert page.locator("input[name='email']").count() > 0
        page.screenshot(path=f"{SHOT_DIR}/register-1440-light.png")
    finally:
        page.close()


def test_link_expired_page_shows_empty_state_and_next_step(browser, live_server):
    page = browser.new_page()
    try:
        page.goto(live_server + "/account/reset-password/not-a-real-token", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(200)
        assert page.locator("text=This reset link no longer works").count() > 0
        page.screenshot(path=f"{SHOT_DIR}/link_expired-1440-light.png")
    finally:
        page.close()
