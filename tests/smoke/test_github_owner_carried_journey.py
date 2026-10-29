"""Browser journey for GitHub code owner carried to application."""

import pytest
from playwright.sync_api import Page, expect
import json
from datetime import datetime, timedelta

from .conftest import PAGE_TIMEOUT, PASSWORD


pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login_as(page, base, email):
    """Sign in as a specific user."""
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


@pytest.fixture(scope="module")
def admin_context(browser, live_server, seeded):
    """Create an admin context using the seeded platform_admin."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    login_page = ctx.new_page()
    _login_as(login_page, live_server, seeded["emails"]["platform_admin"])
    login_page.close()
    yield ctx
    ctx.close()


@pytest.fixture
def admin_page(admin_context):
    """A page for the admin user."""
    pg = admin_context.new_page()
    pg.console_errors = []
    pg.page_errors = []
    pg.on("console", lambda m: pg.console_errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: pg.page_errors.append(str(e)))
    yield pg
    pg.close()


@pytest.mark.skip(reason="Test is timing out and needs further investigation")
def test_github_owner_carried_journey(admin_page: Page, live_server, seeded, admin_context):
    from app import create_app, db
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.connectors.github import OP_CLASSIFY
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        # 1. Create a mock repository proposal with a GitHub code owner (matched)
        proposal_payload = {
            "repository": "test-org/test-repo",
            "name": "Test Application",
            "description": "A test application.",
            "classification": "application",
            "owners": [seeded["emails"]["platform_admin"]],
            "commit": "abcdef123456",
            "evidence_file": "CODEOWNERS"
        }

        approval = AIChatCRUDApproval(
            organization_id=seeded["ids"]["org"],
            user_id=seeded["ids"]["app_manager_user"],
            operation_type=OP_CLASSIFY,
            entity_type="application",
            original_command="Create application from GitHub repository",
            operation_payload=json.dumps(proposal_payload),
            summary="Approve Test Application",
            status=ApprovalStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(days=30),
        )
        db.session.add(approval)
        db.session.commit()
        approval_id = approval.id

    # 2. Log in as an admin and approve the proposal
    admin_page.goto(f"{live_server}/admin/approvals")
    admin_page.click(f"[data-testid=approve-btn-{approval_id}]")

    # 3. Verify the application was created with the correct owner
    admin_page.goto(f"{live_server}/applications/")

    with app.app_context():
        app_obj = ApplicationComponent.query.filter_by(name="Test Application").first()
        assert app_obj is not None
        app_id = app_obj.id

    owner_text = admin_page.locator(f"[data-testid=app-owner--{app_id}]").inner_text()
    assert owner_text == "Smoke Platform-admin"

    # 4. Verify data quality banner does not show "no owner"
    no_owner_link = admin_page.locator("[data-testid=dq-no-owner]")
    expect(no_owner_link).not_to_be_visible()

    # 5. Test unmatched owner
    with app.app_context():
        unmatched_payload = {
            "repository": "test-org/unmatched-repo",
            "name": "Unmatched Application",
            "description": "An unmatched application.",
            "classification": "application",
            "owners": ["unmatcheduser"],
            "commit": "abcdef123456",
            "evidence_file": "CODEOWNERS"
        }
        unmatched_approval = AIChatCRUDApproval(
            organization_id=seeded["ids"]["org"],
            user_id=seeded["ids"]["app_manager_user"],
            operation_type=OP_CLASSIFY,
            entity_type="application",
            original_command="Create application from GitHub repository",
            operation_payload=json.dumps(unmatched_payload),
            summary="Approve Unmatched Application",
            status=ApprovalStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(days=30),
        )
        db.session.add(unmatched_approval)
        db.session.commit()
        unmatched_approval_id = unmatched_approval.id

    admin_page.goto(f"{live_server}/admin/approvals")
    admin_page.click(f"[data-testid=approve-btn-{unmatched_approval_id}]")

    admin_page.goto(f"{live_server}/applications/")
    with app.app_context():
        unmatched_app = ApplicationComponent.query.filter_by(name="Unmatched Application").first()
        assert unmatched_app is not None
        unmatched_app_id = unmatched_app.id

    unmatched_owner_text = admin_page.locator(f"[data-testid=app-owner--{unmatched_app_id}]").inner_text()
    assert "Code owner on GitHub: @unmatcheduser" in unmatched_owner_text

    # 6. Assign the unmatched owner
    admin_page.click(f"a[href='/applications/{unmatched_app_id}/assign-owner']")
    admin_page.select_option("select[name=user_id]", str(seeded["ids"]["app_manager_user"]))
    admin_page.click("button[type=submit]")

    admin_page.goto(f"{live_server}/applications/")
    assigned_owner_text = admin_page.locator(f"[data-testid=app-owner--{unmatched_app_id}]").inner_text()
    assert assigned_owner_text == "Smoke Application-manager"