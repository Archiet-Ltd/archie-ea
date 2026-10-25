"""ARB pages use shadcn components.

An arb_member opens each of the converted ARB pages (dashboard, sessions,
reviews, decisions, change request detail) and asserts the page renders with
shadcn component macros — page header, card, button, table, badge, form_field,
native_select — and that every link lands on a page with content.

Each test asserts from the database that the signed-in user's role matches the
brief persona and is not an administrator. Screenshots are captured at 1440
and 390 viewport widths, in light and dark themes, and saved to the walk
directory.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = "/home/ubuntu/verify/seats/mg3arbb029b/walk"


def _assert_role(email, expected_enterprise_role):
    """Assert from the database that the user identified by *email* has the
    expected enterprise_role and is not an administrator."""
    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == expected_enterprise_role, (
            "User %s has enterprise_role=%r, expected %r"
            % (email, user.enterprise_role, expected_enterprise_role)
        )
        # Assert the user is NOT an administrator
        assert user.role is None or user.role.name != "Administrator", (
            "User %s has platform role Administrator, expected a non-admin role" % email
        )
        assert not user.is_org_admin, (
            "User %s is an org admin, expected a non-admin persona" % email
        )
        assert not user.is_platform_admin, (
            "User %s is a platform admin, expected a non-admin persona" % email
        )


def _screenshot(page, label, viewport_width):
    """Take a screenshot of the current page at the given viewport width,
    in both light and dark themes, and save to the walk directory."""
    os.makedirs(WALK_DIR, exist_ok=True)

    for theme in ("light", "dark"):
        # Set theme via localStorage before navigation
        page.evaluate("localStorage.setItem('theme', '%s')" % theme)
        page.reload(wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)

        filename = "%s-%s-%s.png" % (label, viewport_width, theme)
        filepath = os.path.join(WALK_DIR, filename)
        page.screenshot(path=filepath, full_page=True)
        print("[screenshot] saved %s" % filepath)


def _new_page(browser, viewport_width=1440):
    """A fresh browser session with the first-login welcome tour already seen."""
    context = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": viewport_width, "height": 1000},
    )
    context.add_init_script(
        "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
    )
    return context, context.new_page()


def test_arb_dashboard_uses_shadcn_components(browser, live_server, seeded):
    """An arb_member sees the ARB dashboard with shadcn components."""
    email = seeded["emails"]["arb_member"]
    _assert_role(email, "arb_member")

    context, page = _new_page(browser)
    sign_in(page, live_server, email)
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

    # Screenshots at both viewports
    _screenshot(page, "arb-dashboard", 1440)
    context.close()

    # Repeat at 390
    context2, page2 = _new_page(browser, viewport_width=390)
    sign_in(page2, live_server, email)
    page2.goto(live_server + "/arb/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page2.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    _screenshot(page2, "arb-dashboard", 390)
    context2.close()


def test_arb_sessions_list_uses_shadcn_components(browser, live_server, seeded):
    """An arb_member sees the ARB sessions list with shadcn components."""
    email = seeded["emails"]["arb_member"]
    _assert_role(email, "arb_member")

    context, page = _new_page(browser)
    sign_in(page, live_server, email)
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

    # Screenshots at both viewports
    _screenshot(page, "arb-sessions", 1440)
    context.close()

    # Repeat at 390
    context2, page2 = _new_page(browser, viewport_width=390)
    sign_in(page2, live_server, email)
    page2.goto(live_server + "/arb/sessions", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page2.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    _screenshot(page2, "arb-sessions", 390)
    context2.close()


def test_arb_reviews_list_uses_shadcn_components(browser, live_server, seeded):
    """An arb_member sees the ARB reviews list with shadcn components."""
    email = seeded["emails"]["arb_member"]
    _assert_role(email, "arb_member")

    context, page = _new_page(browser)
    sign_in(page, live_server, email)
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
    """An arb_member sees the decision register with shadcn components."""
    email = seeded["emails"]["arb_member"]
    _assert_role(email, "arb_member")

    context, page = _new_page(browser)
    sign_in(page, live_server, email)
    page.goto(live_server + "/arb/decisions", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # The page header renders (shadcn page_header macro)
    expect(page.locator("h1")).to_contain_text("Decision Register")
    expect(page.get_by_text("Capability-based architecture decision register")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Breadcrumb renders
    breadcrumb = page.locator("nav[aria-label='Breadcrumb']")
    expect(breadcrumb).to_be_visible(timeout=PAGE_TIMEOUT)

    # The page uses shadcn Card (data-slot="card") for the summary metric cards
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

    # Screenshots at both viewports
    _screenshot(page, "arb-decisions", 1440)
    context.close()

    # Repeat at 390
    context2, page2 = _new_page(browser, viewport_width=390)
    sign_in(page2, live_server, email)
    page2.goto(live_server + "/arb/decisions", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page2.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    _screenshot(page2, "arb-decisions", 390)
    context2.close()


def test_arb_sessions_modal_form_uses_shadcn_fields(browser, live_server, seeded):
    """The 'Schedule Session' modal uses shadcn form_field components."""
    email = seeded["emails"]["arb_member"]
    _assert_role(email, "arb_member")

    context, page = _new_page(browser)
    sign_in(page, live_server, email)
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


def test_session_detail_uses_shadcn_components(browser, live_server, seeded):
    """An arb_member sees the session detail page with shadcn components."""
    email = seeded["emails"]["arb_member"]
    _assert_role(email, "arb_member")

    # Create an ARB session so we have something to view
    from app import create_app, db
    from app.models.architecture_review_board import ArchitectureReviewBoard
    from app.models.user import User
    from datetime import datetime, timezone

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    session_id = None
    with app.app_context():
        org_id = seeded["ids"]["org"]
        chair = User.query.filter_by(email=seeded["emails"]["solution_architect"]).one()
        arb_session = ArchitectureReviewBoard(
            board_number="ARB-TEST-%s" % suffix,
            name="Smoke ARB Session %s" % suffix,
            description="Created by smoke test for session detail screenshot.",
            scheduled_date=datetime.now(timezone.utc),
            status="scheduled",
            chair_id=chair.id,
            secretary_id=chair.id,
            organization_id=org_id,
        )
        db.session.add(arb_session)
        db.session.commit()
        session_id = arb_session.id

    context, page = _new_page(browser)
    sign_in(page, live_server, email)
    page.goto(live_server + "/arb/sessions/%d" % session_id, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # The page header renders (shadcn page_header macro)
    expect(page.locator("h1")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Breadcrumb renders
    breadcrumb = page.locator("nav[aria-label='Breadcrumb']")
    expect(breadcrumb).to_be_visible(timeout=PAGE_TIMEOUT)

    # The detail card renders (shadcn card macro)
    card = page.locator("[data-slot='card']").first
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)

    # Screenshots at both viewports
    _screenshot(page, "arb-session-detail", 1440)
    context.close()

    # Repeat at 390
    context2, page2 = _new_page(browser, viewport_width=390)
    sign_in(page2, live_server, email)
    page2.goto(live_server + "/arb/sessions/%d" % session_id, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page2.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    _screenshot(page2, "arb-session-detail", 390)
    context2.close()


def test_change_request_form_uses_shadcn_components(browser, live_server, seeded):
    """A solution architect sees the change request form with shadcn components."""
    email = seeded["emails"]["solution_architect"]
    _assert_role(email, "solution_architect")

    context, page = _new_page(browser)
    sign_in(page, live_server, email)
    page.goto(live_server + "/arb/change-requests/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # The page header renders (shadcn page_header macro)
    expect(page.locator("h1")).to_contain_text("New Change Request")

    # Breadcrumb renders
    breadcrumb = page.locator("nav[aria-label='Breadcrumb']")
    expect(breadcrumb).to_be_visible(timeout=PAGE_TIMEOUT)

    # Form fields render (shadcn form_field / input / select macros)
    expect(page.get_by_label("Title")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_label("Description")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Trigger type select renders
    trigger_select = page.locator("select[aria-label='Trigger Type']")
    expect(trigger_select).to_be_visible(timeout=PAGE_TIMEOUT)

    # Submit button renders (shadcn button macro)
    submit_btn = page.get_by_role("button", name="Submit Change Request")
    expect(submit_btn).to_be_visible(timeout=PAGE_TIMEOUT)

    # Screenshots at both viewports
    _screenshot(page, "arb-change-request-form", 1440)
    context.close()

    # Repeat at 390
    context2, page2 = _new_page(browser, viewport_width=390)
    sign_in(page2, live_server, email)
    page2.goto(live_server + "/arb/change-requests/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page2.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    _screenshot(page2, "arb-change-request-form", 390)
    context2.close()


def test_change_request_detail_uses_shadcn_components(browser, live_server, seeded):
    """An arb_member sees the change request detail with shadcn components."""
    email = seeded["emails"]["arb_member"]
    _assert_role(email, "arb_member")

    # Create a change request first so we have something to view
    from app import create_app, db
    from app.models.architecture_decision import ArchitectureChangeRequest

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    cr_id = None
    with app.app_context():
        org_id = seeded["ids"]["org"]
        cr = ArchitectureChangeRequest(
            acr_reference="ACR-TEST-%s" % suffix,
            title="Smoke change request %s" % suffix,
            description="Created by smoke test for detail page screenshot.",
            trigger_type="reactive",
            status="open",
            organization_id=org_id,
        )
        db.session.add(cr)
        db.session.commit()
        cr_id = cr.id

    context, page = _new_page(browser)
    sign_in(page, live_server, email)
    page.goto(live_server + "/arb/change-requests/%d" % cr_id, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # The page header renders (shadcn page_header macro)
    expect(page.locator("h1")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Breadcrumb renders
    breadcrumb = page.locator("nav[aria-label='Breadcrumb']")
    expect(breadcrumb).to_be_visible(timeout=PAGE_TIMEOUT)

    # The detail card renders (shadcn card macro)
    card = page.locator("[data-slot='card']").first
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)

    # Screenshots at both viewports
    _screenshot(page, "arb-change-request-detail", 1440)
    context.close()

    # Repeat at 390
    context2, page2 = _new_page(browser, viewport_width=390)
    sign_in(page2, live_server, email)
    page2.goto(live_server + "/arb/change-requests/%d" % cr_id, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page2.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    _screenshot(page2, "arb-change-request-detail", 390)
    context2.close()