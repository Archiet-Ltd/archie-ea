"""QA-034: Add Work Package opens a dialog a person can see and read; Escape and Cancel close it."""
import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD
from tests.smoke.test_strategic_modals_fix05f import linked_portfolio  # noqa: F401

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("viewport,scheme", [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")],
                         ids=["1440-light", "390-dark"])
def test_add_work_package_opens_a_readable_dialog_that_closes(browser, live_server, linked_portfolio, viewport, scheme):
    ctx = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True)
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    _login(page, live_server, linked_portfolio["emails"]["enterprise_architect"])
    page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(2500)

    trigger = page.locator("button", has_text="Add Work Package").first
    panel = page.locator("#rdm-create-wp-modal .modal-panel")
    heading = page.locator("#rdm-wp-modal-title")
    backdrop = page.locator("#rdm-create-wp-modal .modal-backdrop")

    trigger.scroll_into_view_if_needed()
    trigger.click()
    expect(panel).to_be_visible()
    expect(heading).to_have_text("Create Work Package")
    expect(page.locator("#rdm-create-wp-modal").get_by_role("button", name="Create Work Package")).to_be_visible()
    box = backdrop.bounding_box()
    assert box["width"] >= viewport["width"] - 1 and box["height"] >= viewport["height"] - 1, box

    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()

    trigger.click()
    expect(panel).to_be_visible()
    page.locator("#rdm-create-wp-modal").get_by_role("button", name="Cancel").click()
    expect(panel).to_be_hidden()
    assert not errors, errors
    ctx.close()
