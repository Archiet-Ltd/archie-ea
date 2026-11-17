"""The activity record, as the people who use it see it.

* A security architect is refused a page that is not theirs, then opens the
  organisation's audit trail and finds that refusal recorded, with who, what
  and how it ended.
* The platform administrator opens Settings, finds the switch that controls
  only the page-use record, turns it off, saves, and sees it stay off after a
  reload.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _persona(email):
    from app import create_app
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.find_by_email(email)
        return user.enterprise_role, bool(user.is_platform_admin)


def test_a_refusal_is_found_in_the_audit_trail(browser, live_server, seeded):
    email = seeded["emails"]["security_architect"]
    role, is_platform_admin = _persona(email)
    assert role == "security_architect" and not is_platform_admin, "journey must run as the security architect"

    page = browser.new_page()
    try:
        _login(page, live_server, email)
        refused = page.goto(live_server + "/settings", timeout=PAGE_TIMEOUT)
        assert refused.status == 403

        response = page.goto(live_server + "/admin/audit-log", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        body = page.locator("main, body").first
        expect(body).to_contain_text("Permission refused (main.settings)", timeout=PAGE_TIMEOUT)
        expect(body).to_contain_text("GET main.settings, refused")
        expect(body).to_contain_text(email)
    finally:
        page.close()


def test_the_page_use_switch_says_what_it_controls_and_stays_off(browser, live_server, seeded):
    email = seeded["emails"]["platform_admin"]
    role, is_platform_admin = _persona(email)
    assert role == "platform_admin" and is_platform_admin, "journey must run as the platform administrator"

    page = browser.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/settings", timeout=PAGE_TIMEOUT)
        page.get_by_role("tab", name="Security").click()
        switch = page.locator("#audit-logging")
        expect(switch).to_be_visible()
        expect(page.get_by_text("Record page use")).to_be_visible()
        expect(page.get_by_text("are always recorded", exact=False)).to_be_visible()
        expect(switch).to_have_attribute("aria-checked", "true")

        switch.click()
        expect(switch).to_have_attribute("aria-checked", "false")
        page.get_by_role("button", name="Save all settings").click()
        expect(page.get_by_text("Settings saved.")).to_be_visible(timeout=PAGE_TIMEOUT)

        page.reload(timeout=PAGE_TIMEOUT)
        page.get_by_role("tab", name="Security").click()
        expect(page.locator("#audit-logging")).to_have_attribute("aria-checked", "false", timeout=PAGE_TIMEOUT)
    finally:
        # Leave the shared server recording page use, as it found it.
        try:
            page.goto(live_server + "/settings", timeout=PAGE_TIMEOUT)
            page.get_by_role("tab", name="Security").click()
            restore = page.locator("#audit-logging")
            if restore.get_attribute("aria-checked") == "false":
                restore.click()
                page.get_by_role("button", name="Save all settings").click()
                expect(page.get_by_text("Settings saved.")).to_be_visible(timeout=PAGE_TIMEOUT)
        finally:
            page.close()
