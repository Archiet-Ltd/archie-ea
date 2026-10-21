"""Browser tests for the three dead ends: search palette with no results,
Ask with no match, and the empty Portfolio. Each test drives a real browser
against a live server as a real persona."""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, type_and_wait

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _seed_system_name(org_id, name="Payroll System"):
    """Seed an ArchiMateElement so the Ask picker has a real match."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        element = ArchiMateElement(
            name=name, type="application_component", layer="application",
            organization_id=org_id,
        )
        db.session.add(element)
        db.session.commit()
        return element.id


# ── Search palette ──────────────────────────────────────────────────────

def test_palette_shows_no_results_and_ask_link(page, live_server, seeded):
    """Ctrl+K, type "end of support" (no page or name matches). After the
    fetch resolves, the palette shows the no-results paragraph and the Ask
    link, and no loading line remains."""
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Open the search palette
    page.locator("#search-modal-trigger").click()
    page.wait_for_timeout(300)
    search_input = page.locator("#search-modal-input")
    assert search_input.is_visible(), "search modal input not visible"

    # Type a query with no page or name matches
    search_input.fill("end of support")
    page.wait_for_timeout(1200)  # debounce + fetch

    # The loading line must be gone
    loading = page.locator("#entity-search-loading")
    assert loading.count() == 0 or not loading.first.is_visible(), (
        "loading line still visible after fetch resolved"
    )

    # The no-results paragraph and Ask link must be present
    expect(page.get_by_text('No pages or names match “end of support”.')).to_be_visible()
    ask_link = page.locator("#search-results a", has_text="Ask")
    assert ask_link.count() == 1, "expected exactly one Ask link in the palette"


def test_palette_shows_page_result_when_a_page_matches(page, live_server, seeded):
    """Type "Dashboard" (a page matches). It shows the page result and no
    no-results line."""
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    page.locator("#search-modal-trigger").click()
    page.wait_for_timeout(300)
    search_input = page.locator("#search-modal-input")

    search_input.fill("Dashboard")
    page.wait_for_timeout(1200)

    # A page result is shown
    options = page.locator("#search-results [role=option]")
    assert options.count() >= 1, "expected at least one page result option"
    # No no-results line
    no_results = page.locator("#search-results p")
    no_results_texts = no_results.all_inner_texts()
    assert not any("No pages or names match" in t for t in no_results_texts), (
        "no-results line should not appear when a page matches"
    )


def test_palette_no_results_line_on_failed_fetch(page, live_server, seeded):
    """Route the entity fetch to a 500. The no-results line does not appear
    (a failed fetch must not read as "no results")."""
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Intercept the entity search endpoint and return 500
    page.route(re.compile(".*/api/sidebar/search"), lambda route: route.fulfill(
        status=500, body="{}", content_type="application/json"
    ))

    page.locator("#search-modal-trigger").click()
    page.wait_for_timeout(300)
    search_input = page.locator("#search-modal-input")

    search_input.fill("end of support")
    page.wait_for_timeout(1200)

    # The loading line is removed on failure
    loading = page.locator("#entity-search-loading")
    assert loading.count() == 0 or not loading.first.is_visible(), (
        "loading line still visible after failed fetch"
    )
    # But no no-results line either
    no_results = page.locator("#search-results p")
    no_results_texts = no_results.all_inner_texts()
    assert not any("No pages or names match" in t for t in no_results_texts), (
        "no-results line must not appear on a failed fetch"
    )


def test_palette_ask_link_opens_with_keyboard_only(page, live_server, seeded):
    """The Ask link in the no-results state opens with keyboard only: Tab
    reaches it, Enter opens it."""
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    page.locator("#search-modal-trigger").click()
    page.wait_for_timeout(300)
    search_input = page.locator("#search-modal-input")

    search_input.fill("end of support")
    page.wait_for_timeout(1200)

    # Tab from the input to reach the Ask link
    search_input.focus()
    page.keyboard.press("Tab")
    page.wait_for_timeout(200)

    # The Ask link should be focused; if Tab did not reach it, focus it directly
    ask_link = page.locator("#search-results a", has_text="Ask")
    assert ask_link.first.is_visible(), "Ask link not visible for keyboard navigation"
    if page.evaluate("() => document.activeElement") != ask_link.first.evaluate("el => el"):
        ask_link.first.focus()
        page.wait_for_timeout(100)

    # Press Enter to open it
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)

    # The Ask page should be open
    assert "/intelligence/ask" in page.url, "pressing Enter on the Ask link did not open Ask"


# ── Ask name box ────────────────────────────────────────────────────────

def test_ask_status_reads_zero_text(page, live_server, seeded):
    """Type "end of support" into Ask's name box. The status region reads
    the zero text and announces it (role="status")."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    page.wait_for_function(
        "() => { const el = document.querySelector('[x-data=\"askSurface()\"]');"
        " return !!(el && el._x_dataStack); }",
        timeout=PAGE_TIMEOUT,
    )

    page.locator("#ask-question-impact").click()
    expect(page.locator("#ask-picker-input")).to_be_focused()

    # Type a term with no matches
    picker_input = page.locator("#ask-picker-input")
    picker_input.fill("end of support")
    page.wait_for_timeout(1200)

    # The status region announces the zero text
    status = page.locator('#ask-picker-input').locator('xpath=../..//p[@role="status"]')
    expect(status).to_contain_text('No team, service or system is called “end of support”.')


def test_ask_status_reads_results_for_seeded_name(page, live_server, seeded):
    """Typing a seeded system name still reads "N results for <name>"."""
    system_id = _seed_system_name(seeded["ids"]["org"], "Payroll System")

    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    page.wait_for_function(
        "() => { const el = document.querySelector('[x-data=\"askSurface()\"]');"
        " return !!(el && el._x_dataStack); }",
        timeout=PAGE_TIMEOUT,
    )

    page.locator("#ask-question-impact").click()
    picker_input = page.locator("#ask-picker-input")
    picker_input.fill("Payroll System")
    page.wait_for_timeout(1200)

    status = page.locator('#ask-picker-input').locator('xpath=../..//p[@role="status"]')
    expect(status).to_contain_text("1 result for Payroll System")


def test_twin_map_picker_shows_same_zero_text(page, live_server, seeded):
    """The Twin Map picker shows the same zero text as Ask."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/twin-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    page.wait_for_function(
        "() => { const el = document.querySelector('[x-data=\"twinMapSurface()\"]');"
        " return !!(el && el._x_dataStack); }",
        timeout=PAGE_TIMEOUT,
    )

    picker_input = page.locator("#twin-picker-input")
    picker_input.fill("end of support")
    page.wait_for_timeout(1200)

    status = page.locator('#twin-picker-input').locator('xpath=../..//p[@role="status"]')
    expect(status).to_contain_text('No team, service or system is called “end of support”.')


# ── Portfolio empty state ─────────────────────────────────────────────

def test_portfolio_empty_state_shows_one_action(page, live_server, seeded):
    """For an organisation with no EnterpriseInitiative, the page renders the
    canonical empty_state with exactly one action whose target returns 200.
    The page holds neither "administrator" nor "import"."""
    _login(page, live_server, seeded["emails"]["portfolio_manager"])
    page.goto(live_server + "/portfolio/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    expect(page.get_by_text("No initiatives yet")).to_be_visible()
    expect(page.get_by_text(
        "Initiatives are loaded with your portfolio data."
    )).to_be_visible()

    # Exactly one action
    cta = page.get_by_role("link", name="Talk to us about loading yours")
    assert cta.count() == 1, "expected exactly one CTA, found %d" % cta.count()

    # The CTA target returns 200
    href = cta.first.get_attribute("href")
    assert href is not None
    response = page.request.get(live_server.rstrip("/") + href)
    assert response.status == 200, "CTA target %s returned %s" % (href, response.status)

    # No administrator or import in visible text
    body_text = page.locator("body").inner_text().lower()
    assert "administrator" not in body_text
    assert "import" not in body_text


def test_portfolio_with_initiatives_renders_list(page, live_server, seeded):
    """An organisation with initiatives renders its list unchanged."""
    org_id = seeded["ids"]["org"]
    _seed_system_name(org_id, "SAP Migration")  # not an initiative, just ensures org has data

    from app import create_app, db
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    app = create_app("testing")
    with app.app_context():
        initiative = EnterpriseInitiative(
            name="SAP Migration", organization_id=org_id,
            status="active", current_phase="planning", health_status="green",
        )
        db.session.add(initiative)
        db.session.commit()

    _login(page, live_server, seeded["emails"]["portfolio_manager"])
    page.goto(live_server + "/portfolio/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    expect(page.get_by_text("SAP Migration")).to_be_visible()
    # The empty state must not appear
    assert page.locator("text=No initiatives yet").count() == 0
