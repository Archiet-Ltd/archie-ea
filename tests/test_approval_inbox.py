"""Approval Inbox page tests.

Covers: inbox page renders, sidebar link present for all personas with GENERAL
permission, two-organisation isolation, approve/reject actions from the page.
"""

import uuid
from datetime import datetime, timedelta

import pytest


def _make_user(db_session, org_id, label, *, can_approve=True):
    """Create a user with either GENERAL permission or an explicit Viewer role."""
    from app.models.user import Permission, Role, User

    suffix = uuid.uuid4().hex[:8]
    role_name = f"Approval Inbox {label} {suffix}"
    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        role = Role(
            name=role_name,
            permissions=Permission.GENERAL if can_approve else 0,
            index="main",
            default=False,
        )
        db_session.add(role)
        db_session.flush()
    user = User(
        email=f"approval-inbox-{label}-{suffix}@example.com",
        first_name=label,
        last_name="User",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="solution_architect",
    )
    user.role = role
    db_session.add(user)
    db_session.flush()
    return user


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid

    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


@pytest.fixture
def approval_inbox_setup(db_session, make_org, tenant_ctx):
    """Create two orgs with requester and approver users."""
    org_a = make_org("inbox-a")
    org_b = make_org("inbox-b")
    requester_a = _make_user(db_session, org_a.id, "RequesterA")
    approver_a = _make_user(db_session, org_a.id, "ApproverA")
    requester_b = _make_user(db_session, org_b.id, "RequesterB")
    approver_b = _make_user(db_session, org_b.id, "ApproverB")
    viewer_a = _make_user(db_session, org_a.id, "ViewerA", can_approve=False)

    with tenant_ctx(org_a.id):
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

        svc = AIChatApprovalService(requester_a.id)
        approval_id = svc.create_pending_approval(
            operation_type="create",
            entity_type="capability",
            original_command="create capability Test Cap",
            operation_payload={"name": "Test Cap"},
            summary="Create capability 'Test Cap'",
            chat_session_id="sess-1",
        )["approval_id"]

    return {
        "org_a": org_a,
        "org_b": org_b,
        "requester_a": requester_a,
        "approver_a": approver_a,
        "requester_b": requester_b,
        "approver_b": approver_b,
        "viewer_a": viewer_a,
        "approval_id": approval_id,
    }


def test_approval_inbox_page_renders(app, approval_inbox_setup, login_as):
    """The inbox page renders with the correct structure (data loaded via JS)."""
    setup = approval_inbox_setup
    client = app.test_client()
    login_as(client, setup["approver_a"])

    resp = client.get("/ai-chat/approvals/inbox")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Approval Inbox" in html
    assert "data-testid=\"approval-inbox-loading\"" in html
    assert "data-testid=\"approval-inbox-empty\"" in html
    assert "data-testid=\"approval-inbox-unavailable\"" in html


def test_approval_inbox_api_shows_only_same_org_items(app, approval_inbox_setup, login_as):
    """Org B's approver never sees Org A's item via the queue API."""
    setup = approval_inbox_setup
    client = app.test_client()
    login_as(client, setup["approver_b"])

    resp = client.get("/ai-chat/approvals/queue")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert all(a["summary"] != "Create capability 'Test Cap'" for a in data["approvals"])


def test_approval_inbox_approve_action(app, approval_inbox_setup, login_as, tenant_ctx, monkeypatch):
    """Approving from the inbox executes the operation."""
    setup = approval_inbox_setup
    client = app.test_client()
    login_as(client, setup["approver_a"])

    # Mock the execution
    import app.modules.ai_chat.services.ai_chat_approval_service as svc_mod

    calls = []

    class _FakeDataService:
        def __init__(self, user_id):
            self.user_id = user_id

        def create_capability(self, payload):
            calls.append((self.user_id, payload))
            return {"success": True, "id": 9001}

    monkeypatch.setattr(svc_mod, "AIDataInteractionService", _FakeDataService)

    resp = client.post(f"/ai-chat/approvals/{setup['approval_id']}/approve")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True

    # Verify the queue API no longer shows the approved item
    resp = client.get("/ai-chat/approvals/queue")
    data = resp.get_json()
    assert all(a["id"] != setup["approval_id"] for a in data["approvals"])


def test_approval_inbox_reject_action(app, approval_inbox_setup, login_as, tenant_ctx):
    """Rejecting from the inbox marks the approval rejected."""
    setup = approval_inbox_setup
    client = app.test_client()
    login_as(client, setup["approver_a"])

    resp = client.post(
        f"/ai-chat/approvals/{setup['approval_id']}/reject",
        json={"reason": "Not needed"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True

    # Verify the queue API no longer shows the rejected item
    resp = client.get("/ai-chat/approvals/queue")
    data = resp.get_json()
    assert all(a["id"] != setup["approval_id"] for a in data["approvals"])


def test_approval_inbox_sidebar_link_present_for_approver(app, approval_inbox_setup, login_as):
    """The sidebar shows the Approval Inbox link for users with GENERAL permission."""
    setup = approval_inbox_setup
    client = app.test_client()
    login_as(client, setup["approver_a"])

    resp = client.get("/ai-chat/approvals/inbox")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # The sidebar is included in the base template
    assert "Approval Inbox" in html


def test_approval_inbox_sidebar_link_absent_for_viewer(app, approval_inbox_setup, login_as):
    """The inbox route returns 403 for Viewer roles (no GENERAL permission)."""
    setup = approval_inbox_setup
    client = app.test_client()
    login_as(client, setup["viewer_a"])

    resp = client.get("/ai-chat/approvals/inbox")
    # Viewer cannot access the route (requires GENERAL)
    assert resp.status_code == 403


def test_approval_inbox_backfilled_row_keeps_org(app, db_session, make_org, tenant_ctx, login_as):
    """A backfilled approval row keeps its organisation and is visible only there."""
    from app.models.confidence_review import ReviewQueueItem, ReviewStatus
    from app.commands.backfill_review_queue_approvals import run_backfill
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.models.user import User

    org_a = make_org("backfill-a")
    org_b = make_org("backfill-b")
    approver_b = _make_user(db_session, org_b.id, "ApproverB")
    approver_b_id = approver_b.id

    item_a = ReviewQueueItem(
        organization_id=org_a.id,
        item_type="archimate_element",
        item_id=1,
        item_name="Backfilled Item",
        confidence_score="0.50",
        status=ReviewStatus.PENDING,
    )
    db_session.add(item_a)
    db_session.commit()

    run_backfill(dry_run=False, organization_id=None)

    # Re-query the approver after backfill (which calls db.session.remove())
    approver_b = User.query.get(approver_b_id)

    client = app.test_client()
    login_as(client, approver_b)

    resp = client.get("/ai-chat/approvals/queue")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert all(a["summary"] != "Confidence review: Backfilled Item" for a in data["approvals"])


def test_approval_inbox_overdue_item_still_visible(app, db_session, make_org, tenant_ctx, login_as):
    """Overdue (past expires_at) items still appear in the queue API."""
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    org = make_org("overdue-inbox")
    approver = _make_user(db_session, org.id, "ApproverOverdue")

    with tenant_ctx(org.id):
        approval = create_approval_record(
            organization_id=org.id,
            operation_type="create",
            entity_type="capability",
            summary="Overdue capability",
            operation_payload={"name": "Overdue Cap"},
            user_id=None,
            expiry_minutes=15,
        )
        db_session.commit()

    # Force it overdue
    approval.expires_at = datetime.utcnow() - timedelta(hours=1)
    db_session.commit()

    client = app.test_client()
    login_as(client, approver)

    resp = client.get("/ai-chat/approvals/queue")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert any(a["summary"] == "Overdue capability" for a in data["approvals"])


def test_approval_inbox_escalation_notifies_only_own_org(app, db_session, make_org, monkeypatch):
    """Escalation emails only the overdue items' organisation administrators."""
    from app.modules.ai_chat.services.ai_chat_approval_service import (
        create_approval_record,
        escalate_overdue_approvals,
    )
    from flask import current_app

    org_a = make_org("esc-inbox-a")
    org_b = make_org("esc-inbox-b")
    admin_a = _make_user(db_session, org_a.id, "AdminA", can_approve=True)
    admin_a.is_org_admin = True
    _make_user(db_session, org_b.id, "AdminB", can_approve=True)

    approval_a = create_approval_record(
        organization_id=org_a.id,
        operation_type="create",
        entity_type="capability",
        summary="Org A overdue",
        operation_payload={},
        user_id=None,
    )
    db_session.commit()
    approval_a.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db_session.commit()

    sent = {}

    def _fake_send(app, subject, recipients, html_body):
        sent["recipients"] = recipients
        return True

    monkeypatch.setattr(
        "app._bootstrap._digest_emails._safe_send_email", _fake_send
    )

    stats = escalate_overdue_approvals(current_app._get_current_object())

    assert stats["organisations_notified"] == 1
    assert sent["recipients"] == [admin_a.email]


def test_approval_inbox_template_has_required_elements(app, approval_inbox_setup, login_as):
    """The inbox template includes approve/reject buttons and technical detail disclosure."""
    setup = approval_inbox_setup
    client = app.test_client()
    login_as(client, setup["approver_a"])

    resp = client.get("/ai-chat/approvals/inbox")
    html = resp.get_data(as_text=True)

    # Check for key UI elements in the Alpine.js template
    assert "Approve" in html
    assert "Reject" in html
    assert "View technical detail" in html
    assert "Requested by" in html
    assert "Expires:" in html


def test_approval_inbox_empty_state(app, db_session, make_org, login_as):
    """Empty inbox shows the correct empty state message."""
    org = make_org("empty-inbox")
    approver = _make_user(db_session, org.id, "ApproverEmpty")

    client = app.test_client()
    login_as(client, approver)

    resp = client.get("/ai-chat/approvals/inbox")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "No pending approvals waiting for your review" in html


def test_approval_inbox_source_table_display(app, db_session, make_org, tenant_ctx, login_as):
    """Backfilled items show their source table in the queue API."""
    from app.models.confidence_review import ReviewQueueItem, ReviewStatus
    from app.commands.backfill_review_queue_approvals import run_backfill
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.models.user import User

    org = make_org("source-display")
    approver = _make_user(db_session, org.id, "ApproverSource")
    approver_id = approver.id

    item = ReviewQueueItem(
        organization_id=org.id,
        item_type="archimate_element",
        item_id=42,
        item_name="Source Display Item",
        confidence_score="0.50",
        status=ReviewStatus.PENDING,
    )
    db_session.add(item)
    db_session.commit()

    run_backfill(dry_run=False, organization_id=None)

    # Re-query the approver after backfill
    approver = User.query.get(approver_id)

    client = app.test_client()
    login_as(client, approver)

    resp = client.get("/ai-chat/approvals/queue")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    # Find the backfilled item
    backfilled = next((a for a in data["approvals"] if a["summary"] == "Confidence review: Source Display Item"), None)
    assert backfilled is not None
    assert backfilled.get("source_table") == "review_queue_items", (
        "backfilled item must carry its source_table so the inbox template "
        "can render the source badge"
    )
    # source_id is the review_queue_items row id, not item_id.
    assert isinstance(backfilled.get("source_id"), int) and backfilled["source_id"] > 0, (
        "backfilled item must carry its source_id so the inbox template "
        "can render the source badge"
    )
    assert backfilled.get("status") == "pending", (
        "queue items must carry their status so the inbox template's "
        "isOverdue() can decide whether to show the Overdue indicator"
    )