"""A person whose request fails is told so, and Enter in the change request form never wipes it.

- A solution architect picks a template on the solutions list; the create request fails: a plain
  error appears and they stay on the list.
- A solution architect types a title in the change request form and presses Enter: the page is not
  reloaded and the typed title is still there, with the form explaining what is missing.
"""

import json
import os

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("BROKEN_SURFACES_WALK_DIR", "")
TEMPLATE_NAME = "Claims platform pattern"


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _context(browser, width, dark):
    context = browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme="dark" if dark else "light")
    init = "localStorage.setItem('archie_onboarding_ts', '1');"
    if dark:
        init += " localStorage.setItem('theme', 'dark'); document.documentElement.classList.add('dark');"
    context.add_init_script(init)
    return context


def _shot(page, name, width, theme):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, "%s-%s-%d.png" % (name, theme, width)))


@pytest.mark.parametrize("width,dark", [(1440, False), (390, True)])
def test_failed_solution_create_tells_the_person(browser, live_server, seeded, width, dark):
    context = _context(browser, width, dark)
    page = context.new_page()
    theme = "dark" if dark else "light"
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])

        def templates(route):
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "templates": [{"id": 1, "name": TEMPLATE_NAME, "description": "A reusable pattern.",
                               "domain": "Application", "usage_count": 2}]}))

        page.route("**/solutions/templates", templates)
        page.route("**/architecture-journey/start-from-template", lambda route: route.abort())
        page.goto(live_server + "/solutions/", wait_until="networkidle", timeout=PAGE_TIMEOUT)

        page.locator('[data-testid="btn-new-from-template"]').first.dispatch_event("click")
        page.get_by_text(TEMPLATE_NAME).first.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Use template").first.click()

        error = page.get_by_text("The solution could not be created")
        error.first.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        _shot(page, "solution-create-failed", width, theme)
        assert "/solutions" in page.url
    finally:
        context.close()


@pytest.mark.parametrize("width,dark", [(1440, False), (390, True)])
def test_enter_in_change_request_form_keeps_the_typed_title(browser, live_server, seeded, width, dark):
    context = _context(browser, width, dark)
    page = context.new_page()
    theme = "dark" if dark else "light"
    title = "Move claims to the shared data store"
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/arb/change-requests/new", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.evaluate("window.__noReload = 'still the same page'")
        field = page.get_by_label("Title", exact=False).first
        field.fill(title)
        field.press("Enter")
        page.wait_for_timeout(1500)

        assert page.evaluate("window.__noReload") == "still the same page", "Enter reloaded the page"
        assert field.input_value() == title
        assert page.locator("body").inner_text().strip()
        _shot(page, "change-request-enter", width, theme)
    finally:
        context.close()
