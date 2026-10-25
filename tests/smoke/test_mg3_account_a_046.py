"""mg3-account-a-046: reset_password.html rebuilt onto card/button macros."""
import os
import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOT_DIR = "/home/ubuntu/verify/wt-mg3-account-a-046/reports/shots"
os.makedirs(SHOT_DIR, exist_ok=True)


def test_viewer_sees_the_mail_unavailable_state(browser, live_server):
    """This smoke environment has no mail server configured, so the route
    renders the mail_unavailable state (the card still built from the
    card/button macros) rather than the email-entry form -- a real state
    this template must handle, named in the brief's `states` list."""
    page = browser.new_page()
    try:
        page.goto(live_server + "/account/reset-password", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator("text=Reset your password").count() > 0
        assert page.locator("[data-reset-state='mail-unavailable']").count() > 0
        assert page.locator("text=Sign in").count() > 0
        page.screenshot(path=f"{SHOT_DIR}/reset_password-mail_unavailable-1440-light.png")
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=f"{SHOT_DIR}/reset_password-mail_unavailable-390-light.png")
    finally:
        page.close()
