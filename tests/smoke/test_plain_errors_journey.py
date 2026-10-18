"""R1-W9: a person who follows a dead link, or a sidebar link that needs no
parameters, lands on a plain page with the navigation intact, in a real browser."""

import pytest

from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def test_enterprise_architect_follows_a_dead_link_and_gets_back_to_work(page, live_server, seeded):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])

    page.goto(live_server + "/applications/987654321", wait_until="domcontentloaded")
    box = page.locator("[data-testid=plain-error]")
    expect(box).to_contain_text("We couldn't find that page")
    expect(page.locator("#sidebar-nav")).to_be_visible()
    for raw in ("Traceback", "Not Found", "Internal Server Error"):
        assert raw not in page.inner_text("body")

    # Survives a reload, and the one button takes the person back to work.
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("[data-testid=plain-error]")).to_be_visible()
    page.get_by_role("link", name="Go to the dashboard").click()
    page.wait_for_url("**/dashboard/overview")


def test_model_history_pages_open_from_the_sidebar_and_their_buttons_have_labels(page, live_server, seeded):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])

    page.goto(live_server + "/intelligence/history/changes", wait_until="domcontentloaded")
    expect(page.get_by_role("button", name="Show changes")).to_be_visible()
    assert "MISSING_PARAMETER" not in page.inner_text("body")

    page.goto(live_server + "/intelligence/history/as-of", wait_until="domcontentloaded")
    expect(page.get_by_role("button", name="Show model")).to_be_visible()
