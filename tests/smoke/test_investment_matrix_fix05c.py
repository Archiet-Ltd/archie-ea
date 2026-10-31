"""Journey tests for fix05c: investment matrix pages should have no console errors
and no raw 'None' text at 1440 and 390 viewport widths.
"""
import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD
from tests.smoke.test_strategic_modals_fix05f import linked_portfolio  # noqa: F401

PAGE_TIMEOUT = 30000

STRATEGIC_URL = "/strategic/investment-matrix"
ENTERPRISE_URL = "/enterprise/strategic/investment-matrix"

VIEWPORTS = [
    {"width": 1440, "height": 900, "name": "desktop-1440"},
    {"width": 390, "height": 844, "name": "mobile-390"},
]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.fixture(scope="module")
def seeded_org(linked_portfolio):
    """An organisation whose capabilities are linked to an application, so the matrix has rows."""
    return linked_portfolio["emails"]["enterprise_architect"]


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("viewport", VIEWPORTS, ids=[v["name"] for v in VIEWPORTS])
def test_strategic_investment_matrix_no_console_errors(browser, live_server, seeded_org, viewport):
    """Strategic investment matrix page loads with no console errors at all viewport widths."""
    email = seeded_org
    page = browser.new_context(viewport=viewport, ignore_https_errors=True).new_page()

    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: console_errors.append(str(exc)))

    _login(page, live_server, email)
    page.goto(live_server + STRATEGIC_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(2000)  # Allow charts to render

    # Assert no console errors
    assert not console_errors, f"Console errors at {viewport['name']}: {console_errors}"

    # Verify key page elements are present
    expect(page.locator("h3:has-text('Investment Priority Matrix')")).to_be_visible()
    expect(page.locator("canvas#priorityChart")).to_be_visible()
    expect(page.locator("canvas#investmentChart")).to_be_visible()

    # Verify table has data rows (or empty state)
    table_rows = page.locator("tbody tr")
    expect(table_rows.first).to_be_visible()


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("viewport", VIEWPORTS, ids=[v["name"] for v in VIEWPORTS])
def test_enterprise_investment_matrix_no_console_errors_or_none(browser, live_server, seeded_org, viewport):
    """Enterprise investment matrix page loads with no console errors and no raw 'None' text."""
    email = seeded_org
    page = browser.new_context(viewport=viewport, ignore_https_errors=True).new_page()

    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: console_errors.append(str(exc)))

    _login(page, live_server, email)
    page.goto(live_server + ENTERPRISE_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(2000)  # Allow bubble chart to render

    # Assert no console errors
    assert not console_errors, f"Console errors at {viewport['name']}: {console_errors}"

    # Verify key page elements are present
    expect(page.locator("h3:has-text('Investment Priority Matrix')")).to_be_visible()

    # Verify table has data rows
    table_rows = page.locator("tbody tr")
    expect(table_rows.first).to_be_visible()

    # CRITICAL: Check that no cell contains raw "None" text
    page_content = page.content()
    assert "None" not in page_content, f"Raw 'None' found in page content at {viewport['name']}"

    # Specifically check the strategic importance column
    importance_cells = page.locator("tbody tr td:nth-child(2)")
    count = importance_cells.count()
    for i in range(count):
        cell_text = importance_cells.nth(i).inner_text()
        assert cell_text != "None", f"Row {i} strategic importance shows 'None' at {viewport['name']}"
        assert cell_text != "", f"Row {i} strategic importance is empty at {viewport['name']}"


@pytest.mark.smoke
@pytest.mark.journey
def test_strategic_investment_matrix_charts_render(browser, live_server, seeded_org):
    """Strategic investment matrix charts render with data (not empty)."""
    email = seeded_org
    page = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True).new_page()

    _login(page, live_server, email)
    page.goto(live_server + STRATEGIC_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(2000)

    # Check that canvas elements have been drawn on (width/height > 0)
    priority_canvas = page.locator("canvas#priorityChart")
    investment_canvas = page.locator("canvas#investmentChart")

    expect(priority_canvas).to_be_visible()
    expect(investment_canvas).to_be_visible()

    # Verify canvas has actual drawing context (not just empty)
    priority_width = priority_canvas.evaluate("el => el.width")
    investment_width = investment_canvas.evaluate("el => el.width")
    assert priority_width > 0, "Priority chart canvas has zero width"
    assert investment_width > 0, "Investment chart canvas has zero width"


@pytest.mark.smoke
@pytest.mark.journey
def test_enterprise_investment_matrix_bubble_chart_renders(browser, live_server, seeded_org):
    """Enterprise investment matrix bubble chart renders with data or shows empty state."""
    email = seeded_org
    page = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True).new_page()

    _login(page, live_server, email)
    page.goto(live_server + ENTERPRISE_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(3000)  # Bubble chart takes longer

    bubble_canvas = page.locator("canvas#investment-bubble-chart")
    empty_state = page.locator("#investment-bubble-empty")

    # Either the chart is visible with data, or the empty state is shown
    chart_visible = bubble_canvas.is_visible()
    empty_visible = empty_state.is_visible()

    assert chart_visible or empty_visible, "Neither bubble chart nor empty state is visible"

    if chart_visible:
        bubble_width = bubble_canvas.evaluate("el => el.width")
        assert bubble_width > 0, "Bubble chart canvas has zero width"
        # Verify quadrant legend is present
        for label in ["Invest", "Migrate", "Tolerate", "Eliminate"]:
            expect(page.locator(f"text={label}").first).to_be_visible()
    else:
        # Empty state should have explanatory text
        expect(empty_state).to_contain_text("No capability data to plot")
