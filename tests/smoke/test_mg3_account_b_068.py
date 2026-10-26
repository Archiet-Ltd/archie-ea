"""mg3-account-b-068: account/manage.html rebuilt onto card/button/input/switch/badge macros."""
import os
import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOT_DIR = "/home/ubuntu/verify/wt-mg3-account-b-068/reports/shots"
os.makedirs(SHOT_DIR, exist_ok=True)


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
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
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def test_solution_architect_views_account_settings_and_toggles_a_preference(browser, live_server, seeded):
    page = browser.new_page()
    try:
        email = seeded["emails"]["solution_architect"]
        _login(page, live_server, email)

        page.goto(live_server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator(f"dd:has-text('{email}')").count() > 0, (
            "signed-in user is not the solution_architect persona"
        )
        page.screenshot(path=f"{SHOT_DIR}/manage-1440-light.png")
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=f"{SHOT_DIR}/manage-390-light.png")
        page.set_viewport_size({"width": 1440, "height": 900})

        switch = page.locator("#pref_show_archimate_btn")
        before = switch.get_attribute("aria-checked")
        switch.click()
        page.wait_for_timeout(150)
        after = switch.get_attribute("aria-checked")
        assert before != after, "clicking the Show ArchiMate names switch did not change its state"

        page.click("form:has(#pref_show_archimate) button[type=submit]")
        page.wait_for_timeout(500)
        assert page.locator("text=Preferences saved").count() > 0, "saving display preferences did not confirm"
    finally:
        page.close()
