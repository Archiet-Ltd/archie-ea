"""The list's Edit dialog and the AI-generate dialog, walked as the architect who uses them.

From a layer's list the architect clicks Edit on a Goal: the dialog offers "Full Edit Page", which opens the
Goal's own Edit page with its name in the form. That page offers no "Full Edit Page" link to itself, and
Cancel returns to the Goal's page. The "Generate Element with AI" window has one Close button, and it closes
the window. A second organisation's Goal is never listed to the first.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]


def _frame(page, name, viewport, theme):
    out = os.environ.get("EDIT_DIALOG_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    page.screenshot(path="%s/%s-%d-%s.png" % (out, name, viewport["width"], theme), full_page=True)


def _goal(org, name):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    with create_app("testing").app_context():
        element = ArchiMateElement(organization_id=org["org_id"], name=name, type="Goal", layer="Motivation")
        db.session.add(element)
        db.session.commit()
        element_id = element.id
        db.session.remove()
    return element_id


def _signed_in(browser, live_server, org, viewport, theme):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])
    page.evaluate("localStorage.setItem('theme', '%s')" % theme)
    return context, page


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_full_edit_page_opens_the_edit_page_and_that_page_does_not_link_to_itself(
        browser, live_server, viewport, theme):
    mine, theirs = create_fresh_org("enterprise_architect"), create_fresh_org("enterprise_architect")
    tag = uuid.uuid4().hex[:6]
    name, foreign = "Listed goal %s" % tag, "Foreign goal %s" % tag
    goal_id = _goal(mine, name)
    _goal(theirs, foreign)
    context, page = _signed_in(browser, live_server, mine, viewport, theme)
    try:
        page.goto(live_server + "/architecture/motivation/Goal", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Edit " + name).first.click()
        dialog = page.locator("#archimate-form-modal")
        expect(dialog.get_by_role("textbox", name="Element name")).to_have_value(name, timeout=PAGE_TIMEOUT)
        full = dialog.get_by_role("link", name="Full Edit Page")
        expect(full).to_have_attribute("href", "/architecture/motivation/Goal/%d/edit?ae=1" % goal_id)
        assert foreign not in page.locator("body").inner_text()
        _frame(page, "list-edit-dialog", viewport, theme)

        full.click()
        page.wait_for_url(re.compile(r"/architecture/motivation/Goal/%d/edit\?ae=1$" % goal_id), timeout=PAGE_TIMEOUT)
        dialog = page.locator("#archimate-form-modal")
        expect(dialog.get_by_role("textbox", name="Element name")).to_have_value(name, timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("link", name="Full Edit Page")).to_have_count(0)
        _frame(page, "edit-page", viewport, theme)

        dialog.get_by_role("button", name="Cancel").click()
        page.wait_for_url(re.compile(r"/architecture/motivation/Goal/%d(\?ae=1)?$" % goal_id), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=name).first).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_the_ai_generate_window_has_one_close_button_and_it_closes_the_window(browser, live_server, viewport, theme):
    org = create_fresh_org("enterprise_architect")
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        page.goto(live_server + "/architecture/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="More actions").click()
        page.get_by_test_id("dropdown-ai-generate-element").click()
        window = page.get_by_test_id("ai-generate-modal")
        expect(window.get_by_role("heading", name="Generate Element with AI")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(window.get_by_role("button", name="Close")).to_have_count(1)
        _frame(page, "ai-generate-window", viewport, theme)
        window.get_by_role("button", name="Close").click()
        expect(window).to_be_hidden(timeout=PAGE_TIMEOUT)
    finally:
        context.close()
