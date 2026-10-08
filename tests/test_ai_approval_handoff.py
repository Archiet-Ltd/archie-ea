"""Two-person, tenant-scoped approval handoff for AI-originated writes."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest


pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org_id, label, *, can_approve=True):
    """Create a user with either GENERAL permission or an explicit Viewer role."""
    from app.models.user import Permission, Role, User

    suffix = uuid.uuid4().hex[:8]
    role_name = "AI Approval Reviewer" if can_approve else "AI Approval Viewer"
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
        email=f"ai-handoff-{label}-{suffix}@example.com",
        first_name=label,
        last_name="Reviewer",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="solution_architect",
    )
    user.role = role
    db_session.add(user)
    db_session.flush()
    return user


def _queue(service):
    result = service.create_pending_approval(
        operation_type="create",
        entity_type="capability",
        original_command="create capability Handoff",
        operation_payload={"name": "Handoff"},
        summary="Create capability Handoff",
        chat_session_id="handoff-test",
    )
    assert result["success"], result
    return result["approval_id"]


def _fake_execution(monkeypatch):
    import app.modules.ai_chat.services.ai_chat_approval_service as svc_mod

    calls = []

    class _FakeDataService:
        def __init__(self, user_id):
            self.user_id = user_id

        def create_capability(self, payload):
            calls.append((self.user_id, payload))
            return {"success": True, "id": 9001}

    monkeypatch.setattr(svc_mod, "AIDataInteractionService", _FakeDataService)
    return calls


def test_same_org_reviewer_executes_once_and_receives_allowlisted_queue(
    db_session, make_org, tenant_ctx, monkeypatch
):
    """A different authorized user reviews exactly one same-org queued write."""
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.models.ai_chat_crud_approval import AIChatApprovalAuditLog, AIChatCRUDApproval

    org = make_org("approval-handoff")
    requester = _make_user(db_session, org.id, "Requester")
    reviewer = _make_user(db_session, org.id, "Reviewer")

    with tenant_ctx(org.id):
        approval_id = _queue(AIChatApprovalService(requester.id))
        approval = db_session.get(AIChatCRUDApproval, approval_id)
        assert approval.organization_id == org.id

        queue = AIChatApprovalService(reviewer.id).get_approver_queue()
        assert queue["success"] is True
        assert queue["approvals"] == [
            {
                "id": approval_id,
                "operation_type": "create",
                "entity_type": "capability",
                "summary": "Create capability Handoff",
                "arguments": {"name": "Handoff"},
                "created_at": approval.created_at.isoformat(),
                "expires_at": approval.expires_at.isoformat(),
                "status": "pending",
                "source_table": None,
                "source_id": None,
                "requester": {"id": requester.id, "display_name": "Requester Reviewer"},
            }
        ]

        calls = _fake_execution(monkeypatch)
        mismatch = AIChatApprovalService(reviewer.id).approve_and_execute(approval_id, requester.id)
        assert mismatch["code"] == "FORBIDDEN"
        assert calls == []

        result = AIChatApprovalService(reviewer.id).approve_and_execute(approval_id, reviewer.id)
        assert result["success"] is True
        assert calls == [(reviewer.id, {"name": "Handoff"})]

        replay = AIChatApprovalService(reviewer.id).approve_and_execute(approval_id, reviewer.id)
        assert replay == {"success": False, "code": "CONFLICT", "error": "Approval is already approved"}
        assert len(calls) == 1

        events = AIChatApprovalAuditLog.query.filter_by(approval_id=approval_id).all()
        assert {event.event for event in events} >= {"created", "approved", "executed"}


def test_approval_model_uses_the_tenant_middleware_for_direct_reads(
    db_session, make_org, tenant_ctx
):
    """A direct model lookup must not bypass the tenant fence used by routes."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    org_a = make_org("approval-mixin-a")
    org_b = make_org("approval-mixin-b")
    requester_a = _make_user(db_session, org_a.id, "RequesterA")
    requester_b = _make_user(db_session, org_b.id, "RequesterB")
    expires_at = datetime.utcnow() + timedelta(minutes=5)
    foreign = AIChatCRUDApproval(
        user_id=requester_b.id,
        organization_id=org_b.id,
        operation_type="create",
        entity_type="capability",
        original_command="foreign approval",
        operation_payload='{"name": "Foreign"}',
        summary="Foreign approval",
        status=ApprovalStatus.PENDING,
        expires_at=expires_at,
    )
    db_session.add(foreign)
    db_session.flush()

    with tenant_ctx(org_a.id):
        assert AIChatCRUDApproval.query.filter_by(id=foreign.id).first() is None
        assert AIChatCRUDApproval.get_by_id_and_user(
            foreign.id, requester_a.id, org_a.id
        ) is None


def test_two_independent_sessions_cannot_claim_the_same_pending_approval(app):
    """The conditional claim permits exactly one reviewer before dispatch starts."""
    from sqlalchemy.orm import sessionmaker

    from app import db
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.models.organization import Organization
    from app.models.user import User
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    marker = uuid.uuid4().hex[:10]
    with app.app_context():
        Session = sessionmaker(bind=db.engine)
        setup = Session()
        first = second = None
        approval_id = requester_id = first_reviewer_id = second_reviewer_id = org_id = None
        try:
            org = Organization(name=f"Claim {marker}", slug=f"claim-{marker}")
            setup.add(org)
            setup.flush()
            requester = User(email=f"claim-requester-{marker}@example.com", organization_id=org.id)
            first_reviewer = User(email=f"claim-first-{marker}@example.com", organization_id=org.id)
            second_reviewer = User(email=f"claim-second-{marker}@example.com", organization_id=org.id)
            # User.__init__ attaches the default Role from Flask's scoped
            # session. These are persistence-only identities for the private
            # conditional-claim primitive, so detach that cross-session object.
            requester.role = first_reviewer.role = second_reviewer.role = None
            setup.add_all([requester, first_reviewer, second_reviewer])
            setup.flush()
            approval = AIChatCRUDApproval(
                user_id=requester.id,
                organization=org,
                operation_type="create",
                entity_type="capability",
                original_command="claim approval",
                operation_payload='{"name": "Claim"}',
                summary="Claim approval",
                status=ApprovalStatus.PENDING,
                expires_at=datetime.utcnow() + timedelta(minutes=5),
            )
            setup.add(approval)
            setup.commit()
            approval_id = approval.id
            requester_id = requester.id
            first_reviewer_id = first_reviewer.id
            second_reviewer_id = second_reviewer.id
            org_id = org.id

            first, second = Session(), Session()
            assert AIChatApprovalService._claim_pending_approval(
                approval_id, org_id, first_reviewer_id, session=first
            ) is True
            first.commit()
            assert AIChatApprovalService._claim_pending_approval(
                approval_id, org_id, second_reviewer_id, session=second
            ) is False
            first.expire_all()
            claimed = first.get(AIChatCRUDApproval, approval_id)
            assert claimed.status == ApprovalStatus.APPROVED
            assert claimed.approved_by_id == first_reviewer_id
        finally:
            if first is not None:
                first.rollback()
                first.close()
            if second is not None:
                second.rollback()
                second.close()
            setup.close()
            cleanup = Session()
            try:
                if approval_id is not None:
                    cleanup.query(AIChatCRUDApproval).filter_by(id=approval_id).delete()
                if requester_id is not None:
                    cleanup.query(User).filter(
                        User.id.in_([requester_id, first_reviewer_id, second_reviewer_id])
                    ).delete(synchronize_session=False)
                if org_id is not None:
                    cleanup.query(Organization).filter_by(id=org_id).delete()
                cleanup.commit()
            finally:
                cleanup.close()


def test_audit_failure_after_claim_cannot_reopen_or_dispatch_an_approval(
    db_session, make_org, tenant_ctx, monkeypatch
):
    """An optional audit failure happens after the irreversible claim boundary."""
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org = make_org("approval-audit-boundary")
    requester = _make_user(db_session, org.id, "Requester")
    reviewer = _make_user(db_session, org.id, "Reviewer")
    second_reviewer = _make_user(db_session, org.id, "SecondReviewer")

    with tenant_ctx(org.id):
        approval_id = _queue(AIChatApprovalService(requester.id))
        calls = _fake_execution(monkeypatch)
        original_audit = AIChatApprovalService._audit
        failed_claim_audit = False

        def fail_only_the_post_claim_audit(self, approval, event, *args, **kwargs):
            nonlocal failed_claim_audit
            if event == "approved" and not failed_claim_audit:
                failed_claim_audit = True
                raise RuntimeError("approval audit storage is temporarily unavailable")
            return original_audit(self, approval, event, *args, **kwargs)

        monkeypatch.setattr(AIChatApprovalService, "_audit", fail_only_the_post_claim_audit)

        first = AIChatApprovalService(reviewer.id).approve_and_execute(approval_id, reviewer.id)
        replay = AIChatApprovalService(second_reviewer.id).approve_and_execute(
            approval_id, second_reviewer.id
        )

        assert first["success"] is True
        assert replay["code"] == "CONFLICT"
        assert calls == [(reviewer.id, {"name": "Handoff"})]


def test_two_sessions_rejection_cannot_overwrite_a_durable_claim(app):
    """A stale reject loses the same conditional database race as a second approver."""
    from sqlalchemy.orm import sessionmaker

    from app import db
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.models.organization import Organization
    from app.models.user import User
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    marker = uuid.uuid4().hex[:10]
    with app.app_context():
        Session = sessionmaker(bind=db.engine)
        setup = Session()
        claimer = rejecter = None
        approval_id = requester_id = reviewer_id = org_id = None
        try:
            org = Organization(name=f"Reject {marker}", slug=f"reject-{marker}")
            setup.add(org)
            setup.flush()
            requester = User(email=f"reject-requester-{marker}@example.com", organization_id=org.id)
            reviewer = User(email=f"reject-reviewer-{marker}@example.com", organization_id=org.id)
            requester.role = reviewer.role = None
            setup.add_all([requester, reviewer])
            setup.flush()
            approval = AIChatCRUDApproval(
                user_id=requester.id,
                organization=org,
                operation_type="create",
                entity_type="capability",
                original_command="reject race approval",
                operation_payload='{"name": "Reject race"}',
                summary="Reject race",
                status=ApprovalStatus.PENDING,
                expires_at=datetime.utcnow() + timedelta(minutes=5),
            )
            setup.add(approval)
            setup.commit()
            approval_id, requester_id, reviewer_id, org_id = (
                approval.id,
                requester.id,
                reviewer.id,
                org.id,
            )

            claimer, rejecter = Session(), Session()
            assert AIChatApprovalService._claim_pending_approval(
                approval_id, org_id, reviewer_id, session=claimer
            ) is True
            claimer.commit()
            assert AIChatApprovalService._reject_pending_approval(
                approval_id, org_id, reason="stale cancellation", session=rejecter
            ) is False
            rejecter.rollback()
            claimer.expire_all()
            assert claimer.get(AIChatCRUDApproval, approval_id).status == ApprovalStatus.APPROVED
        finally:
            if claimer is not None:
                claimer.rollback()
                claimer.close()
            if rejecter is not None:
                rejecter.rollback()
                rejecter.close()
            setup.close()
            cleanup = Session()
            try:
                if approval_id is not None:
                    cleanup.query(AIChatCRUDApproval).filter_by(id=approval_id).delete()
                if requester_id is not None:
                    cleanup.query(User).filter(User.id.in_([requester_id, reviewer_id])).delete(
                        synchronize_session=False
                    )
                if org_id is not None:
                    cleanup.query(Organization).filter_by(id=org_id).delete()
                cleanup.commit()
            finally:
                cleanup.close()


def test_foreign_org_cannot_see_or_decide_known_approval_id(
    db_session, make_org, tenant_ctx, monkeypatch
):
    """Known numeric IDs never reveal or execute another organization's request."""
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org_a = make_org("approval-a")
    org_b = make_org("approval-b")
    requester = _make_user(db_session, org_a.id, "RequesterA")
    foreign_reviewer = _make_user(db_session, org_b.id, "ReviewerB")

    with tenant_ctx(org_a.id):
        approval_id = _queue(AIChatApprovalService(requester.id))

    calls = _fake_execution(monkeypatch)
    with tenant_ctx(org_b.id):
        service = AIChatApprovalService(foreign_reviewer.id)
        assert service.get_approver_queue() == {"success": True, "approvals": []}
        assert service.approve_and_execute(approval_id, foreign_reviewer.id)["code"] == "NOT_FOUND"
        assert service.reject_approval(approval_id)["code"] == "NOT_FOUND"
    assert calls == []


def test_requester_can_cancel_and_same_org_reviewer_can_reject(db_session, make_org, tenant_ctx):
    """Cancellation is requester-owned; review rejection is a same-org write permission."""
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.models.ai_chat_crud_approval import AIChatApprovalAuditLog, AIChatCRUDApproval, ApprovalStatus

    org = make_org("approval-reject")
    requester = _make_user(db_session, org.id, "Requester")
    reviewer = _make_user(db_session, org.id, "Reviewer")

    with tenant_ctx(org.id):
        requester_service = AIChatApprovalService(requester.id)
        cancelled_id = _queue(requester_service)
        cancelled = requester_service.reject_approval(cancelled_id, "cancelled")
        assert cancelled["success"] is True
        assert "cancelled" in cancelled["message"]
        assert db_session.get(AIChatCRUDApproval, cancelled_id).status == ApprovalStatus.REJECTED
        assert AIChatApprovalAuditLog.query.filter_by(
            approval_id=cancelled_id, event="cancelled"
        ).one()

        reviewer_id = _queue(requester_service)
        rejected = AIChatApprovalService(reviewer.id).reject_approval(reviewer_id, "needs evidence")
        assert rejected["success"] is True
        assert db_session.get(AIChatCRUDApproval, reviewer_id).status == ApprovalStatus.REJECTED


def test_viewer_is_denied_reviewer_queue_and_rejection(db_session, make_org, tenant_ctx):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org = make_org("approval-viewer")
    requester = _make_user(db_session, org.id, "Requester")
    viewer = _make_user(db_session, org.id, "Viewer", can_approve=False)

    with tenant_ctx(org.id):
        approval_id = _queue(AIChatApprovalService(requester.id))
        service = AIChatApprovalService(viewer.id)
        assert service.get_approver_queue()["code"] == "FORBIDDEN"
        assert service.reject_approval(approval_id)["code"] == "FORBIDDEN"


def test_approval_routes_hide_foreign_rows_and_forbid_viewer_queue(
    app, db_session, make_org, tenant_ctx, login_as
):
    """HTTP contracts preserve 404 for foreign IDs and 403 for capability denial."""
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org_a = make_org("approval-route-a")
    org_b = make_org("approval-route-b")
    requester = _make_user(db_session, org_a.id, "RequesterA")
    foreign_reviewer = _make_user(db_session, org_b.id, "ReviewerB")
    viewer = _make_user(db_session, org_b.id, "ViewerB", can_approve=False)

    with tenant_ctx(org_a.id):
        approval_id = _queue(AIChatApprovalService(requester.id))

    client = app.test_client()
    login_as(client, foreign_reviewer)
    assert client.get("/ai-chat/approvals/queue").get_json() == {"success": True, "approvals": []}
    assert client.post(f"/ai-chat/approvals/{approval_id}/approve").status_code == 404
    assert client.post(f"/ai-chat/approvals/{approval_id}/reject").status_code == 404

    login_as(client, viewer)
    assert client.get("/ai-chat/approvals/queue").status_code == 403


def test_backfill_is_idempotent_and_reports_no_remaining_nulls(db_session, make_org):
    """Legacy NULL rows derive their owner exactly from their requester."""
    from app.commands.backfill_ai_chat_approval_org import run_backfill
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    org = make_org("approval-backfill")
    requester = _make_user(db_session, org.id, "Requester")
    approval = AIChatCRUDApproval(
        user_id=requester.id,
        organization_id=None,
        operation_type="create",
        entity_type="capability",
        original_command="legacy approval",
        operation_payload='{"name": "Legacy"}',
        summary="Legacy approval",
        status=ApprovalStatus.PENDING,
        expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    db_session.add(approval)
    db_session.flush()

    first = run_backfill()
    second = run_backfill()
    db_session.refresh(approval)
    assert approval.organization_id == org.id
    assert first["backfilled"] >= 1
    assert first["remaining_nulls"] == 0
    assert second == {"backfilled": 0, "remaining_nulls": 0}


def test_approval_modal_is_a_reviewer_queue_with_requester_attribution():
    """The shared modal cannot present a requester's own approval as actionable."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    js = (root / "app/static/js/ai_chat/approval_modal.js").read_text(encoding="utf-8")
    template = (root / "app/templates/ai_chat/index.html").read_text(encoding="utf-8")

    assert 'fetch("/ai-chat/approvals/queue"' in js
    assert "Approvals for review" in template
    assert "Requested by" in template
    assert 'item.arguments' in template
    assert "hasLoaded: false" in js
    assert "this.loading = true" in js
    assert "Approval status is unavailable" in js
    assert "finally" in js and "this.loading = false" in js
    assert 'data-testid="approval-queue-unavailable"' in template
    assert 'data-testid="approval-queue-loading"' in template
    assert 'data-testid="approval-queue-empty"' in template
    assert 'aria-label="Retry approval queue"' in template
    assert (
        "$store.approvals.hasLoaded && !$store.approvals.loading"
        " && !$store.approvals.error && $store.approvals.approvals.length === 0"
        in template
    )
    assert "queued for another authorized user" in (
        root / "app/modules/ai_chat/services/ai_chat_approval_service.py"
    ).read_text(encoding="utf-8")
