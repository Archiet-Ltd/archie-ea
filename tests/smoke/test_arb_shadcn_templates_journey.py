"""Journey tests for the shadcn-converted ARB templates.

The two Tier B templates at arb/change_requests.html and arb/standard_detail.html
are rebuilt from the component library. Each journey signs in as arb_member
(the persona the page serves), navigates to the page, and asserts the content
a person doing their job would need to see.

Screenshots are captured at 1440 and 390, light and dark, for both pages.
"""

import os
import pathlib
import uuid
from datetime import date

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# Walk directory for screenshots
WALK_DIR = os.environ.get(
    "SMOKE_SCREENSHOT_DIR",
    "/home/ubuntu/verify/seats/mg3arbb030e/walk",
)


def _shot(page, name, theme, width):
    """Save a screenshot to the walk directory."""
    pathlib.Path(WALK_DIR).mkdir(parents=True, exist_ok=True)
    path = pathlib.Path(WALK_DIR) / f"{name}-{theme}-{width}.png"
    page.screenshot(path=str(path), full_page=True)
    assert path.exists() and path.stat().st_size > 0
    print(f"[screenshot] {path}")
    return path


def _assert_role(live_server, email):
    """Assert the signed-in user is arb_member and NOT an administrator."""
    from app import create_app, db
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "arb_member", (
            f"Expected enterprise_role=arb_member, got {user.enterprise_role!r}"
        )
        assert not user.is_admin(), (
            "Signed-in user is an administrator, not the expected persona arb_member"
        )
        assert user.role.name != "Administrator", (
            f"Expected platform role != Administrator, got {user.role.name}"
        )


def _enable_dark_mode(page):
    """Add the 'dark' class after page navigation (init scripts are stripped by the app's JS)."""
    page.evaluate("document.documentElement.classList.add('dark');")


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.fixture(scope="module")
def arb_standards(seeded):
    """Create one governance standard linked to the seed org."""
    from app import create_app, db
    from app.models.architecture_review_board import ARBGovernanceStandard

    app = create_app("testing")
    org_id = seeded["ids"]["org"]
    standard_id = None
    try:
        with app.app_context():
            standard = ARBGovernanceStandard(
                code=f"STD-TEST-{uuid.uuid4().hex[:6]}",
                name="Security Architecture Standard",
                description="Defines security architecture requirements for all solutions.",
                category="security",
                togaf_phase="D",
                archimate_layer="application",
                status="active",
                mandatory=True,
                requirements=[
                    "All solutions must use OAuth 2.0",
                    "Data at rest must be encrypted using AES-256",
                ],
                checklist_items=[
                    {"item": "OAuth 2.0 implementation reviewed", "required": True},
                    {"item": "Encryption standards confirmed", "required": True},
                    {"item": "Penetration test schedule agreed", "required": False},
                ],
                exceptions_allowed=True,
                exception_process="Submit a waiver request to the ARB for review.",
                applies_to_review_types=["security_review", "architecture_review"],
                effective_date=date.today(),
                review_date=date(2027, 1, 1),
                organization_id=org_id,
            )
            db.session.add(standard)
            db.session.commit()
            standard_id = standard.id
            yield standard_id
    finally:
        if standard_id is not None:
            with app.app_context():
                db.session.delete(db.session.get(ARBGovernanceStandard, standard_id))
                db.session.commit()


@pytest.fixture(scope="module")
def arb_change_requests(seeded):
    """Create change requests linked to the seed org."""
    from app import create_app, db
    from app.models.architecture_decision import ArchitectureChangeRequest
    from app.models.user import User

    app = create_app("testing")
    org_id = seeded["ids"]["org"]
    created_ids = []
    try:
        with app.app_context():
            user = User.query.filter_by(email=seeded["emails"]["arb_member"]).one()
            for title, trigger, status in [
                ("Upgrade firewall infrastructure", "business_event", "open"),
                ("Migrate legacy CRM to cloud", "regulatory", "assessing"),
                ("Decommission old data centre", "cost_optimisation", "approved"),
            ]:
                cr = ArchitectureChangeRequest(
                    title=title,
                    acr_reference=f"ACR-2026-{uuid.uuid4().hex[:4].upper()}",
                    trigger_type=trigger,
                    status=status,
                    description=f"Change request for: {title}",
                    raised_by_id=user.id,
                    organization_id=org_id,
                )
                db.session.add(cr)
                db.session.flush()
                created_ids.append(cr.id)
            db.session.commit()
            yield created_ids
    finally:
        if created_ids:
            with app.app_context():
                for cid in created_ids:
                    cr = db.session.get(ArchitectureChangeRequest, cid)
                    if cr is not None:
                        db.session.delete(cr)
                db.session.commit()


def test_arb_member_role_lookup(live_server, seeded):
    """Verify the seeded arb_member user has the correct role before any page tests.

    This test stands apart from the page journeys so the role assertion runs
    whether or not Playwright is available.
    """
    _assert_role(live_server, seeded["emails"]["arb_member"])


def test_arb_member_sees_change_requests(browser, live_server, seeded, arb_change_requests):
    """An ARB member lands on the change requests page and sees the requests."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["arb_member"])

        page.goto(live_server + "/arb/change-requests", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

        # The page title confirms we are on the right page
        body_text = page.text_content("body")
        assert "change request" in body_text.lower(), "Expected change request in body text"

        # The stat cards show summary counts
        stat_cards = page.locator("[data-slot=card]").all()
        assert len(stat_cards) >= 4, f"Expected at least 4 stat cards, got {len(stat_cards)}"

        # Assert the Raised By column shows the raiser's name for seeded requests
        page_text = page.text_content("body")
        assert "Smoke arb_member" in page_text, (
            "Expected seeded change request to show raiser name 'Smoke arb_member' "
            "in the Raised By column"
        )

        # Light mode screenshot at 1440
        page.wait_for_timeout(600)
        _shot(page, "change-requests", "light", 1440)
    finally:
        page.close()
        context.close()


def test_arb_member_sees_change_requests_mobile_light(browser, live_server, seeded, arb_change_requests):
    """Light mode screenshot of change requests at 390 viewport."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["arb_member"])
        page.goto(live_server + "/arb/change-requests", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(600)
        _shot(page, "change-requests", "light", 390)
    finally:
        page.close()
        context.close()


def test_arb_member_sees_change_requests_dark(browser, live_server, seeded, arb_change_requests):
    """Dark mode screenshot of change requests at 1440."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["arb_member"])
        page.goto(live_server + "/arb/change-requests", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        _enable_dark_mode(page)
        page.wait_for_timeout(600)
        _shot(page, "change-requests", "dark", 1440)
    finally:
        page.close()
        context.close()


def test_arb_member_sees_change_requests_mobile_dark(browser, live_server, seeded, arb_change_requests):
    """Dark mode screenshot of change requests at 390."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["arb_member"])
        page.goto(live_server + "/arb/change-requests", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        _enable_dark_mode(page)
        page.wait_for_timeout(600)
        _shot(page, "change-requests", "dark", 390)
    finally:
        page.close()
        context.close()


def test_arb_member_sees_standards_and_detail(browser, live_server, seeded, arb_standards):
    """An ARB member navigates to a standard detail page and sees its content."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["arb_member"])

        # Navigate directly to the standard detail page
        standard_id = arb_standards
        page.goto(live_server + f"/arb/standards/{standard_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

        # The standard detail page shows the standard name
        body_text = page.text_content("body")
        assert "Security Architecture Standard" in body_text
        assert "Mandatory" in body_text

        # Breadcrumb in the page header links the user back to ARB and Standards
        breadcrumb = page.locator('nav[aria-label="Breadcrumb"]').first
        assert breadcrumb.is_visible(), "Breadcrumb trail not rendered in page header"
        breadcrumb_texts = [t.strip() for t in breadcrumb.inner_text().splitlines() if t.strip()]
        assert any("ARB" in t for t in breadcrumb_texts), f"Expected ARB breadcrumb, got {breadcrumb_texts}"
        assert any("Standard" in t for t in breadcrumb_texts), f"Expected Standards breadcrumb, got {breadcrumb_texts}"

        # Light mode screenshot at 1440
        page.wait_for_timeout(600)
        _shot(page, "standard-detail", "light", 1440)
    finally:
        page.close()
        context.close()


def test_arb_member_sees_standards_and_detail_mobile_light(browser, live_server, seeded, arb_standards):
    """Light mode screenshot of standard detail at 390 viewport."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["arb_member"])
        standard_id = arb_standards
        page.goto(live_server + f"/arb/standards/{standard_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(600)
        _shot(page, "standard-detail", "light", 390)
    finally:
        page.close()
        context.close()


def test_arb_member_sees_standards_and_detail_dark(browser, live_server, seeded, arb_standards):
    """Dark mode screenshot of standard detail at 1440."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["arb_member"])
        standard_id = arb_standards
        page.goto(live_server + f"/arb/standards/{standard_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        _enable_dark_mode(page)
        page.wait_for_timeout(600)
        _shot(page, "standard-detail", "dark", 1440)
    finally:
        page.close()
        context.close()


def test_arb_member_sees_standards_and_detail_mobile_dark(browser, live_server, seeded, arb_standards):
    """Dark mode screenshot of standard detail at 390."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["arb_member"])
        standard_id = arb_standards
        page.goto(live_server + f"/arb/standards/{standard_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        _enable_dark_mode(page)
        page.wait_for_timeout(600)
        _shot(page, "standard-detail", "dark", 390)
    finally:
        page.close()
        context.close()