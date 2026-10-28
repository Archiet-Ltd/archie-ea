import pytest
from playwright.sync_api import Page, expect
from app.models.user import User
from app.models.application_portfolio import ApplicationComponent
from app.models.ai_chat_crud_approval import AIChatCRUDApproval
from app.connectors.github import OP_CLASSIFY
import json

@pytest.mark.journey
def test_github_owner_carried_journey(page: Page, test_user: User, test_client):
    # 1. Create a mock repository proposal with a GitHub code owner
    proposal_payload = {
        "repository": "test-org/test-repo",
        "name": "Test Application",
        "description": "A test application.",
        "classification": "application",
        "owners": ["testuser@example.com"],
        "commit": "abcdef123456",
        "evidence_file": "CODEOWNERS"
    }

    approval = AIChatCRUDApproval(
        organization_id=test_user.organization_id,
        operation_type=OP_CLASSIFY,
        operation_payload=json.dumps(proposal_payload),
        summary="Approve Test Application",
        status="pending",
        created_by_id=test_user.id,
    )
    with test_client.application.app_context():
        from app import db
        db.session.add(approval)
        db.session.commit()
        approval_id = approval.id

    # 2. Log in as an admin and approve the proposal
    page.goto(f"/login")
    page.fill("input[name=email]", test_user.email)
    page.fill("input[name=password]", "password")
    page.click("button[type=submit]")
    expect(page).to_have_url("/applications/")

    page.goto(f"/admin/approvals")
    page.click(f"#approve-btn-{approval_id}")

    # 3. Verify the application was created with the correct owner
    page.goto("/applications/")

    with test_client.application.app_context():
        app = ApplicationComponent.query.filter_by(name="Test Application").first()
        assert app is not None
        owner_text = page.locator(f"[data-testid=app-owner-{app.id}]").inner_text()
        assert owner_text == f"{test_user.first_name} {test_user.last_name}"

        # 4. Verify data quality banner does not show "no owner"
        no_owner_link = page.locator("[data-testid=dq-no-owner]")
        expect(no_owner_link).not_to_be_visible()

    # 5. Test unmatched owner
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
        organization_id=test_user.organization_id,
        operation_type=OP_CLASSIFY,
        operation_payload=json.dumps(unmatched_payload),
        summary="Approve Unmatched Application",
        status="pending",
        created_by_id=test_user.id,
    )
    with test_client.application.app_context():
        from app import db
        db.session.add(unmatched_approval)
        db.session.commit()
        unmatched_approval_id = unmatched_approval.id

    page.goto(f"/admin/approvals")
    page.click(f"#approve-btn-{unmatched_approval_id}")

    page.goto("/applications/")
    with test_client.application.app_context():
        unmatched_app = ApplicationComponent.query.filter_by(name="Unmatched Application").first()
        assert unmatched_app is not None
        unmatched_owner_text = page.locator(f"[data-testid=app-owner-{unmatched_app.id}]").inner_text()
        assert "Code owner on GitHub: @unmatcheduser" in unmatched_owner_text

        # 6. Assign the unmatched owner
        page.click(f"a[href='/applications/{unmatched_app.id}/assign-owner']")
        page.select_option("select[name=user_id]", str(test_user.id))
        page.click("button[type=submit]")

        page.goto("/applications/")
        assigned_owner_text = page.locator(f"[data-testid=app-owner-{unmatched_app.id}]").inner_text()
        assert assigned_owner_text == f"{test_user.first_name} {test_user.last_name}"
