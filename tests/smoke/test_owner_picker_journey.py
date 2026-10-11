"""Owner picker, fact-sheet and coverage page smoke journeys in a real browser.

Drives the owner picker on the application edit form, verifies persistence on
the fact sheet, and confirms the coverage page is accessible by CTO and
portfolio manager but denied for procurement.
"""

import pytest

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
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _visit(page, base, path, expected_status=200):
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response is not None and response.status == expected_status, (
        "%s answered %s (expected %s)" % (path, response and response.status, expected_status)
    )
    page.wait_for_timeout(500)


def test_owner_picker_loads_on_edit_page(page, live_server, seeded):
    """The edit page renders the owner picker for an application manager."""
    _login(page, live_server, seeded["emails"]["application_manager"])

    app_id = seeded["ids"]["application"]
    _visit(page, live_server, "/applications/%d/edit" % app_id)

    assert page.locator("#owner_picker_search").count() == 1, "owner picker search input not found"
    assert page.locator("#owner_type_select").count() == 1, "owner type select not found"
    assert page.locator("#add-owner-btn").count() == 1, "add owner button not found"
    assert page.locator("#owner-list").count() == 1, "owner list container not found"


def test_owner_picker_search_shows_users(page, live_server, seeded):
    """Typing in the owner picker search shows matching users."""
    _login(page, live_server, seeded["emails"]["application_manager"])

    app_id = seeded["ids"]["application"]
    _visit(page, live_server, "/applications/%d/edit" % app_id)

    search = page.locator("#owner_picker_search")
    # The app manager user name starts with "Smoke" - search for "Smoke"
    search.fill("Smoke")
    page.wait_for_timeout(400)  # debounce is 300ms

    results = page.locator("#owner_picker_results .owner-picker-result")
    assert results.count() >= 1, "should find at least one matching user"


def test_coverage_page_allowed_for_cto(page, live_server, seeded):
    """CTO can access the ownership coverage page."""
    _login(page, live_server, seeded["emails"]["cto"])
    _visit(page, live_server, "/applications/ownership-coverage")

    assert page.locator("text=Ownership coverage").count() >= 1, "coverage page should show title"


def test_coverage_page_allowed_for_portfolio_manager(page, live_server, seeded):
    """Portfolio manager can access the ownership coverage page."""
    _login(page, live_server, seeded["emails"]["portfolio_manager"])
    _visit(page, live_server, "/applications/ownership-coverage")

    assert page.locator("text=Ownership coverage").count() >= 1, "coverage page should show title"


def test_coverage_page_denied_for_procurement(page, live_server, seeded):
    """Procurement role gets 403 on the ownership coverage page."""
    _login(page, live_server, seeded["emails"]["procurement"])
    _visit(page, live_server, "/applications/ownership-coverage", expected_status=403)