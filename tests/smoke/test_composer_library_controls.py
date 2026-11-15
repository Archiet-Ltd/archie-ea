"""The composer's dialogs and fields are the library's, and an architect can work them end to end.

An enterprise architect puts two elements on the canvas, reads the keyboard
shortcuts, finds one of the elements by name with Find and Replace, renames it
there, and renames the other from the detail panel. Each dialog must be a real
dialog with a heading, close from its own close button and from Escape, and open
again afterwards (the page's open/closed flag and the dialog stay in step).
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _quick_add(page, name):
    page.keyboard.press("Control+k")
    input_box = page.locator("#quick-add-input")
    expect(input_box).to_be_visible(timeout=PAGE_TIMEOUT)
    input_box.fill(name)
    create_btn = page.get_by_role("button", name=re.compile("^Create$"))
    expect(create_btn).to_be_visible(timeout=PAGE_TIMEOUT)
    create_btn.click()
    expect(page.locator("#quick-add-input")).to_be_hidden(timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
def test_dialogs_and_fields_work_end_to_end(browser, live_server, seeded):
    email = seeded["emails"]["enterprise_architect"]
    suffix = uuid.uuid4().hex[:6]
    first = "Ledger %s" % suffix
    second = "Gateway %s" % suffix

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _login(page, live_server, email)
    page.goto(live_server + "/archimate/composer", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("nav[aria-label='Composer toolbar']")).to_be_visible(timeout=PAGE_TIMEOUT)

    _quick_add(page, first)
    expect(page.locator(".joint-element")).to_have_count(1, timeout=PAGE_TIMEOUT)
    _quick_add(page, second)
    expect(page.locator(".joint-element")).to_have_count(2, timeout=PAGE_TIMEOUT)

    # Keyboard shortcuts: a dialog with a heading, listing the shortcuts, closed by its close button,
    # then by Escape, and still openable after each.
    for how in ("close button", "escape"):
        page.get_by_role("button", name="Keyboard Shortcuts").click()
        dialog = page.get_by_role("dialog", name="Keyboard Shortcuts")
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(dialog.get_by_role("heading", name="Keyboard Shortcuts")).to_be_visible()
        expect(dialog.get_by_text("Ctrl+Z")).to_be_visible()
        expect(dialog.get_by_text("Quick Add")).to_be_visible()
        if how == "close button":
            dialog.get_by_role("button", name="Close").click()
        else:
            page.keyboard.press("Escape")
        expect(dialog).to_be_hidden(timeout=PAGE_TIMEOUT)

    # Find and Replace: find the first element by name and rename it.
    page.locator("body").click(position={"x": 700, "y": 600})
    page.keyboard.press("Control+h")
    sr = page.get_by_role("dialog", name="Find and Replace")
    expect(sr).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(sr.get_by_role("heading", name="Find and Replace")).to_be_visible()
    sr.get_by_placeholder("Find").fill(first)
    sr.get_by_placeholder("Replace with").fill("Renamed %s" % suffix)
    sr.get_by_label("Case sensitive").check()
    sr.get_by_label("Regex").check()
    sr.get_by_label("Regex").uncheck()
    expect(sr.get_by_role("button", name=re.compile("Replace All \\(\\s*1\\s*\\)"))).to_be_enabled(timeout=PAGE_TIMEOUT)
    sr.get_by_role("button", name=re.compile("Replace All")).click()
    sr.get_by_role("button", name="Close").click()
    expect(sr).to_be_hidden(timeout=PAGE_TIMEOUT)
    expect(page.locator("#composer-canvas")).to_contain_text("Renamed %s" % suffix, timeout=PAGE_TIMEOUT)
    expect(page.locator("#composer-canvas")).to_contain_text(second)

    # The detail panel's name field renames the other element.
    page.locator(".joint-element", has_text=second).first.click()
    panel = page.locator(".detail-panel")
    expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)
    name_field = panel.get_by_label("Element name")
    expect(name_field).to_have_value(second, timeout=PAGE_TIMEOUT)
    name_field.fill("Edited %s" % suffix)
    name_field.press("Enter")
    expect(page.locator("#composer-canvas")).to_contain_text("Edited %s" % suffix, timeout=PAGE_TIMEOUT)
    context.close()
