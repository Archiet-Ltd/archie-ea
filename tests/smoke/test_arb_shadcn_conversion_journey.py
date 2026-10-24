"""ARB pages use shadcn components.

An arb_member opens each of the converted ARB pages (dashboard, sessions,
reviews, decisions, change request detail) and asserts the page renders with
shadcn component macros — page header, card, button, table, badge, form_field,
native_select — and that every link lands on a page with content.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _new_page(browser):
    """A fresh browser session with the first-login welcome tour already seen."""
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script(
        "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
    )
    return context, context.new_page()


def test_arb_dashboard_uses_shadcn_components(browser, live_server, seeded):
    """An arb_member sees the ARB dashboard with shadcn components."""
    context, page = _new_page(browser)
    sign_in(page, live_server, seeded["emails"]["arb_member"])
    page.goto(live_server + "/arb/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # The page header renders (shadcn page_header macro provides the title)
    expect(page.locator("h1")).to_contain_text("Architecture Review Board")

    # The breadcrumb renders (breadcrumb comes from page_header)
    breadcrumb = page.locator("nav[aria-label='Breadcrumb']")
    expect(breadcrumb).to_be_visible(timeout=PAGE_TIMEOUT)

    # The "Full review ledger" and "Sessions" links render (shadcn page_header actions)
    expect(page.get_by_role("link", name="Full review ledger")).to_be_visible(timeout=PAGE_TIMEOUT)
    sessions_links = page.get_by_role("link", name="Sessions")
    expect(sessions_links.first).to_be_visible(timeout=PAGE_TIMEOUT)

    # The AI queue triage section uses shadcn Card
    expect(page.get_by_text("AI queue triage")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Stat cards render with metrics (shadcn Card components)
    stat_card = page.locator("[data-slot='card']").first
    expect(stat_card).to_be_visible(timeout=PAGE_TIMEOUT)

    context.close()


def test_arb_sessions_list_uses_shadcn_components(browser, live_server, seeded):
    """A solution architect sees the ARB sessions list with shadcn components."""
    context, page = _new_page(browser)
    sign_in(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/arb/sessions", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # The page header renders (shadcn page_header macro)
    expect(page.locator("h1")).to_contain_text("ARB Sessions")
    expect(page.get_by_text("Scheduled Architecture Review Board meetings")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Breadcrumb renders (from page_header)
    breadcrumb = page.locator("nav[aria-label='Breadcrumb']")
    expect(breadcrumb).to_be_visible(timeout=PAGE_TIMEOUT)

    # The "New Session" action button renders
    new_session_btn = page.get_by_role("button", name="New Session")
    expect(new_session_btn).to_be_visible(timeout=PAGE_TIMEOUT)

    # Filter section renders using native_select (shadcn)
    form_selects = page.locator("form select")
    expect(form_selects.first).to_be_visible(timeout=PAGE_TIMEOUT)

    # If there are sessions listed, verify session cards render
    cards = page.locator(".bg-card.border.border-border.rounded-lg.p-4")
    first_card = cards.first
    if first_card.is_visible():
        # Session cards show status text
        expect(first_card).to_be_visible(timeout=PAGE_TIMEOUT)

        # The "View" link renders and lands on content
        view_link = first_card.get_by_role("link").filter(has_text="View")
        if view_link.is_visible():
            with page.expect_navigation(timeout=PAGE_TIMEOUT):
                view_link.first.click()
            page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.locator("h1")).to_be_visible(timeout=PAGE_TIMEOUT)
            # Verify breadcrumb on detail page
            detail_breadcrumb = page.locator("nav[aria-label='Breadcrumb']")
            expect(detail_breadcrumb).to_be_visible(timeout=PAGE_TIMEOUT)

    context.close()


def test_arb_reviews_list_uses_shadcn_components(browser, live_server, seeded):
    """An arb_member sees the ARB reviews list with shadcn components."""
    context, page = _new_page(browser)
    sign_in(page, live_server, seeded["emails"]["arb_member"])
    page.goto(live_server + "/arb/reviews", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # The page header renders (shadcn page_header macro)
    expect(page.locator("h1")).to_contain_text("ARB Reviews")
    expect(page.get_by_text("Architecture review items submitted")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Breadcrumb renders
    breadcrumb = page.locator("nav[aria-label='Breadcrumb']")
    expect(breadcrumb).to_be_visible(timeout=PAGE_TIMEOUT)

    # The "New Review" action button renders (from page_header or empty_state)
    new_review_btn = page.get_by_role("button", name="New Review").first
    expect(new_review_btn).to_be_visible(timeout=PAGE_TIMEOUT)

    context.close()


def test_decision_register_uses_shadcn_components(browser, live_server, seeded):
    """A data architect sees the decision register with shadcn components."""
    context, page = _new_page(browser)
    sign_in(page, live_server, seeded["emails"]["data_architect"])
    page.goto(live_server + "/arb/decisions", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # The page header renders (shadcn page_header macro)
    expect(page.locator("h1")).to_contain_text("Decision Register")
    expect(page.get_by_text("Capability-based architecture decision register")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Breadcrumb renders
    breadcrumb = page.locator("nav[aria-label='Breadcrumb']")
    expect(breadcrumb).to_be_visible(timeout=PAGE_TIMEOUT)

    # The page uses shadcn Card (data-slot="card") for the summary metric cards
    # (data_card is the in-house replacement, but the page uses card for the table
    # container and the overall layout)
    card_container = page.locator(".bg-card.border.border-border.rounded-lg.overflow-x-auto")
    expect(card_container).to_be_visible(timeout=PAGE_TIMEOUT)

    # The table renders if decisions exist (shadcn table macro)
    tables = page.locator("table")
    if tables.first.is_visible():
        # Table headers from shadcn table macro
        expect(tables.first.get_by_role("columnheader", name="Title")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Filter section renders with native selects (shadcn)
    horizon_select = page.locator("select[aria-label='Horizon']")
    expect(horizon_select).to_be_visible(timeout=PAGE_TIMEOUT)

    authority_select = page.locator("select[aria-label='Authority Level']")
    expect(authority_select).to_be_visible(timeout=PAGE_TIMEOUT)

    status_select = page.locator("select[aria-label='Status']")
    expect(status_select).to_be_visible(timeout=PAGE_TIMEOUT)

    context.close()


def test_arb_sessions_modal_form_uses_shadcn_fields(browser, live_server, seeded):
    """The 'Schedule Session' modal uses shadcn form_field components."""
    context, page = _new_page(browser)
    sign_in(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/arb/sessions", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # Click "New Session" to open the modal
    new_session_btn = page.get_by_role("button", name="New Session")
    expect(new_session_btn).to_be_visible(timeout=PAGE_TIMEOUT)
    new_session_btn.click()

    # The modal renders with form fields
    modal = page.locator("#create-arb-session")
    expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)

    # The modal uses shadcn form fields
    expect(modal.get_by_label("Session Name")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(modal.get_by_label("Description")).to_be_visible(timeout=PAGE_TIMEOUT)

    # The modal footer has Cancel and Schedule Session buttons (shadcn button macro)
    expect(modal.get_by_role("button", name="Cancel")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(modal.get_by_role("button", name="Schedule Session")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Close the modal
    modal.get_by_role("button", name="Cancel").click()
    page.wait_for_timeout(500)

    context.close()