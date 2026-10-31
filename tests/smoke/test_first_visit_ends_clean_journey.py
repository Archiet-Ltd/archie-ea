"""Journey: with the first release switched on, a new owner's first visit never ends in a waiting list or an error.

Written from the goal "I signed up, tried the sample company, took a suggestion and removed the sample, and
nothing broke or sent me to a page that is not there yet":

* sign up, load the sample company from the chat, open an application, accept a suggested capability;
* remove the sample company: the Overview says "Sample company removed." and no error page appears;
* every link on the Overview and in the chat lands on a page with content, none on a waiting list;
* an early-access address opened while signed in is a page inside the app, with the person's email filled
  in and a way back to the Overview.
"""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_sign_up_first_screen_journey import SIZES, _skip_to_the_dashboard
from .test_signup_brings_the_company_in_journey import (  # noqa: F401  (people is a fixture)
    assert_is_a_startup_cto_not_an_administrator,
    people,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ONB_WALK_DIR")


@pytest.fixture(scope="module")
def live_server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": "1"})


def _shot(page, name, width):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, os.environ.get("ONB_THEME", "light"), width)), full_page=True)


def _links(page):
    return page.eval_on_selector_all("a[href]", "els => els.map(e => [e.getAttribute('href'), (e.innerText || '').trim()])")


def _accept_a_suggested_capability(page, base):
    """Open the application the portfolio offers a capability for, and press Accept on the first suggestion."""
    page.goto(base + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    paths = sorted({h for h, _ in _links(page) if h and h.startswith("/applications/") and h.rstrip("/").split("/")[-1].isdigit()})
    assert paths, "the sample applications are not listed"
    for path in paths:
        offered = page.request.get(base + path.rstrip("/") + "/suggest-capabilities").json()
        if offered.get("suggestions"):
            break
    else:
        raise AssertionError("no sample application is offered a capability")
    page.goto(base + path, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_role("tab", name="Capabilities").click()
    expect(page.get_by_text("Suggested Capabilities")).to_be_visible(timeout=PAGE_TIMEOUT)
    count = page.locator("[x-text*='suggestions.length']")
    before = count.inner_text()
    page.get_by_role("button", name="Accept").first.click()
    expect(count).not_to_have_text(before, timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_the_first_visit_ends_clean(browser, live_server, people, app, name, viewport):
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    if os.environ.get("ONB_THEME") == "dark":
        context.add_init_script(
            "try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
            "document.documentElement.classList.add('dark');"
        )
    page = context.new_page()
    try:
        email, _ = sign_up(page, live_server, people)
        assert_is_a_startup_cto_not_an_administrator(app, email)
        _skip_to_the_dashboard(page)

        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("chat-first-sample").click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)

        _accept_a_suggested_capability(page, live_server)

        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Remove it", exact=True).click()
        page.get_by_test_id("sample-company-remove-confirm").click()
        page.wait_for_url("**/dashboard/overview")
        expect(page.get_by_text("Sample company removed.")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "Server Error" not in page.content() and "Internal Server Error" not in page.content()
        expect(page.get_by_test_id("sample-company-indicator")).to_have_count(0)
        _shot(page, "overview-after-removal", viewport["width"])

        # nothing on the home or the chat leads to a waiting list; every link lands on a page with content
        for start in ("/dashboard/overview", "/ai-chat"):
            page.goto(live_server + start, wait_until="networkidle", timeout=PAGE_TIMEOUT)
            hrefs = sorted({h.split("#")[0] for h, _ in _links(page) if h and h.startswith("/")})
            assert hrefs, start
            assert not [h for h in hrefs if h.startswith("/early-access")], (start, hrefs)
            for href in hrefs:
                if href.startswith(("/static/", "/account/logout", "/auth/logout")) or "logout" in href:
                    continue
                response = page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                assert response and response.status < 400, (start, href, response and response.status)
                assert "/early-access" not in page.url, (start, href)
                assert page.locator("main, [role=main], body").first.inner_text().strip(), (start, href)

        # an early-access address opened directly while signed in is a page inside the app
        page.goto(live_server + "/early-access/pg-ans", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("early-access-page")).to_be_visible()
        expect(page.locator("#early-access-email")).to_have_value(email)
        # the signed-in app's sidebar is there (a drawer on a phone); the public navbar is not
        assert page.locator('[data-sidebar="sidebar"]').count() >= 1
        if viewport["width"] >= 768:
            expect(page.locator('[data-sidebar="sidebar"]').first).to_be_visible()
        expect(page.get_by_test_id("public-navbar")).to_have_count(0)
        _shot(page, "early-access-signed-in", viewport["width"])
        page.get_by_test_id("early-access-back").click()
        page.wait_for_url("**/dashboard/overview")
        expect(page.get_by_test_id("overview-dashboard")).to_be_visible()
    finally:
        context.close()
