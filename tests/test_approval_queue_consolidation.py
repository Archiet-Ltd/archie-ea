"""Consolidation: one approval queue for every proposed change (consolidation, PR 1).

Covers: two-organisation isolation for backfilled and newly-created rows,
backfill idempotency, overdue items staying actionable (overdue-not-expired), and the
repointed constructor sites pairing a source row with an approval row.

Uses the shared fixtures (tests/conftest.py) per CLAUDE.md's convention.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org_id, email, *, enterprise_role=None, is_org_admin=False):
    from app.models.user import User

    unique_email = email.replace("@", f"+{uuid.uuid4().hex[:8]}@")
    user = User(
        email=unique_email,
        first_name="Test",
        last_name="User",
        organization_id=org_id,
        confirmed=True,
        is_org_admin=is_org_admin,
    )
    if enterprise_role:
        user.enterprise_role = enterprise_role
    if hasattr(user, "set_password"):
        user.set_password("password123!")
    from app.models.user import Role
    role = Role.query.filter_by(name="Architect").first() or Role.query.filter_by(name="Administrator").first()
    if role is not None:
        user.role_id = role.id
    db_session.add(user)
    db_session.flush()
    return user


# --------------------------------------------------------------------- #
# Two organisations
# --------------------------------------------------------------------- #


def test_approver_queue_never_shows_another_organisations_item(db_session, make_org, tenant_ctx):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org_a = make_org("a")
    org_b = make_org("b")
    requester_a = _make_user(db_session, org_a.id, "req-a@example.com")
    approver_b = _make_user(db_session, org_b.id, "appr-b@example.com")

    with tenant_ctx(org_a.id):
        svc = AIChatApprovalService(user_id=requester_a.id)
        result = svc.create_pending_approval(
            operation_type="create",
            entity_type="capability",
            original_command="create capability Org A Thing",
            operation_payload={"name": "Org A Thing"},
            summary="Create capability 'Org A Thing'",
            chat_session_id="s-a",
        )
    assert result["success"] is True

    with tenant_ctx(org_b.id):
        queue = AIChatApprovalService(user_id=approver_b.id).get_approver_queue()

    assert queue["success"] is True
    assert all(a["summary"] != "Create capability 'Org A Thing'" for a in queue["approvals"])


def test_backfilled_row_keeps_its_source_organisation(db_session, make_org, tenant_ctx):
    from app.models.confidence_review import ReviewQueueItem, ReviewStatus
    from app.commands.backfill_review_queue_approvals import run_backfill
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    org_a = make_org("a")
    org_b = make_org("b")
    org_a_id, org_b_id = org_a.id, org_b.id

    item_a = ReviewQueueItem(
        organization_id=org_a.id, item_type="archimate_element", item_id=1,
        item_name="A item", confidence_score="0.50", status=ReviewStatus.PENDING,
    )
    item_b = ReviewQueueItem(
        organization_id=org_b.id, item_type="archimate_element", item_id=2,
        item_name="B item", confidence_score="0.50", status=ReviewStatus.PENDING,
    )
    db_session.add_all([item_a, item_b])
    db_session.commit()
    item_a_id, item_b_id = item_a.id, item_b.id

    # run_backfill calls db.session.remove() per organisation (CLAUDE.md/ADR
    # 0003), which detaches every object loaded through this session -- work
    # with plain ids and re-query afterwards rather than the now-detached
    # instances.
    run_backfill(dry_run=False, organization_id=None)

    from app.models.confidence_review import ReviewQueueItem as RQI
    item_a = RQI.query.get(item_a_id)
    item_b = RQI.query.get(item_b_id)
    assert item_a.retired_into_id is not None
    assert item_b.retired_into_id is not None

    approval_a = AIChatCRUDApproval.query.get(item_a.retired_into_id)
    approval_b = AIChatCRUDApproval.query.get(item_b.retired_into_id)
    assert approval_a.organization_id == org_a_id
    assert approval_b.organization_id == org_b_id
    assert approval_a.source_table == "review_queue_items"
    assert approval_a.source_id == item_a_id
    # Regression: entity_type must be the row's own item_type
    # ("archimate_element"), not the literal string "item_type" that named
    # the column to read it from.
    assert approval_a.entity_type == "archimate_element"


# --------------------------------------------------------------------- #
# Backfill idempotency
# --------------------------------------------------------------------- #


def test_backfill_run_twice_creates_no_duplicate_approvals(db_session, make_org):
    from app.models.confidence_review import ReviewQueueItem, ReviewStatus
    from app.commands.backfill_review_queue_approvals import run_backfill
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    org = make_org("idem")
    item = ReviewQueueItem(
        organization_id=org.id, item_type="archimate_element", item_id=99,
        item_name="Idempotency item", confidence_score="0.50", status=ReviewStatus.PENDING,
    )
    db_session.add(item)
    db_session.commit()
    item_id = item.id

    run_backfill(dry_run=False, organization_id=None)
    run_backfill(dry_run=False, organization_id=None)

    count = AIChatCRUDApproval.query.filter_by(
        source_table="review_queue_items", source_id=item_id
    ).count()
    assert count == 1


def test_backfilled_approval_never_shows_another_organisations_item(db_session, make_org, tenant_ctx):
    """The approver queue, not just the model row, must scope a backfilled
    (source_table/source_id-carrying, user_id NULL) approval to its own
    organisation -- reviews/pr302-review-v1.md's MEDIUM asked for a test that
    actually exercises the consolidation path rather than a plain
    create_pending_approval call already safe on main.
    """
    from app.models.confidence_review import ReviewQueueItem, ReviewStatus
    from app.commands.backfill_review_queue_approvals import run_backfill
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org_a = make_org("bq-a")
    org_b = make_org("bq-b")
    approver_b = _make_user(db_session, org_b.id, "appr-bq-b@example.com")
    org_b_id, approver_b_id = org_b.id, approver_b.id

    item_a = ReviewQueueItem(
        organization_id=org_a.id, item_type="archimate_element", item_id=1,
        item_name="Org A backfilled item", confidence_score="0.50", status=ReviewStatus.PENDING,
    )
    db_session.add(item_a)
    db_session.commit()

    # run_backfill calls db.session.remove() per organisation (CLAUDE.md/ADR
    # 0003), detaching every object loaded through this session -- use the
    # plain ids captured above, not org_b/approver_b themselves.
    run_backfill(dry_run=False, organization_id=None)

    with tenant_ctx(org_b_id):
        queue = AIChatApprovalService(user_id=approver_b_id).get_approver_queue()

    assert queue["success"] is True
    assert all(a["summary"] != "Confidence review: Org A backfilled item" for a in queue["approvals"])


def test_backfill_quarantines_an_unattributable_row_and_fails_non_zero(db_session, make_org):
    """reviews/pr302-final-check-v1.md HIGH: a source row with no organisation
    must not be silently dropped -- it is recorded where a platform admin can
    see it, and the command's own unreconciled count (which the CLI raises
    ClickException on) must not stay 0 while it sits outside the canonical
    queue.
    """
    from app.models.confidence_review import ReviewQueueItem, ReviewStatus
    from app.commands.backfill_review_queue_approvals import run_backfill
    from app.models.error_event import ErrorEvent

    # A real, seedable "organisation cannot be determined" row: organization_id
    # is nullable on ReviewQueueItem for exactly this reason.
    orphan = ReviewQueueItem(
        organization_id=None, item_type="archimate_element", item_id=999,
        item_name="Orphaned item", confidence_score="0.50", status=ReviewStatus.PENDING,
    )
    db_session.add(orphan)
    db_session.commit()
    orphan_id = orphan.id

    stats = run_backfill(dry_run=False, organization_id=None)

    assert stats["quarantined"] >= 1
    assert stats["unreconciled"] >= 1

    event = ErrorEvent.query.filter_by(
        fingerprint=f"backfill-quarantine:review_queue_items:{orphan_id}"
    ).first()
    assert event is not None
    assert event.resolved is False
    assert event.organization_id is None


# --------------------------------------------------------------------- #
# Overdue, not expired
# --------------------------------------------------------------------- #


def test_overdue_item_still_approvable_and_marked_overdue(db_session, make_org, tenant_ctx):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.modules.ai_chat.services.ai_chat_approval_service import (
        AIChatApprovalService,
        create_approval_record,
    )

    org = make_org("overdue")
    requester = _make_user(db_session, org.id, "req-overdue@example.com")
    approver = _make_user(db_session, org.id, "appr-overdue@example.com")

    with tenant_ctx(org.id):
        approval = create_approval_record(
            organization_id=org.id,
            operation_type="create",
            entity_type="capability",
            summary="Overdue thing",
            operation_payload={"name": "Overdue thing"},
            user_id=requester.id,
            expiry_minutes=15,
        )
        db_session.commit()

    # Force it overdue, as a real one would become after 15 real minutes.
    approval.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db_session.commit()

    assert approval.is_overdue() is True

    with tenant_ctx(org.id):
        result = AIChatApprovalService(user_id=approver.id).approve_and_execute(approval.id)

    # It must not be refused as expired (overdue-not-expired) -- whatever the execution
    # outcome, the approval was not blocked by is_expired()/status flip.
    refreshed = AIChatCRUDApproval.query.get(approval.id)
    assert refreshed.status != ApprovalStatus.EXPIRED
    assert result.get("code") != "CONFLICT" or "expired" not in str(result.get("error", "")).lower()


def test_overdue_item_still_appears_in_approver_queue(db_session, make_org, tenant_ctx):
    from app.modules.ai_chat.services.ai_chat_approval_service import (
        AIChatApprovalService,
        create_approval_record,
    )

    org = make_org("overdue-queue")
    approver = _make_user(db_session, org.id, "appr-oq@example.com")

    with tenant_ctx(org.id):
        approval = create_approval_record(
            organization_id=org.id,
            operation_type="create",
            entity_type="capability",
            summary="Overdue queue thing",
            operation_payload={"name": "Overdue queue thing"},
            user_id=None,
        )
        db_session.commit()

    approval.expires_at = datetime.utcnow() - timedelta(hours=1)
    db_session.commit()

    with tenant_ctx(org.id):
        queue = AIChatApprovalService(user_id=approver.id).get_approver_queue()

    assert any(a["id"] == approval.id for a in queue["approvals"])


def test_escalation_notifies_only_the_overdue_items_organisation(db_session, make_org, monkeypatch):
    from app.modules.ai_chat.services.ai_chat_approval_service import (
        create_approval_record,
        escalate_overdue_approvals,
    )
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from flask import current_app

    org_a = make_org("esc-a")
    org_b = make_org("esc-b")
    admin_a = _make_user(db_session, org_a.id, "admin-a@example.com", enterprise_role="platform_admin")
    _make_user(db_session, org_b.id, "admin-b@example.com", enterprise_role="platform_admin")

    approval_a = create_approval_record(
        organization_id=org_a.id, operation_type="create", entity_type="capability",
        summary="Org A overdue", operation_payload={}, user_id=None,
    )
    db_session.commit()
    approval_a.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db_session.commit()

    sent = {}

    def _fake_send(app, subject, recipients, html_body):
        sent["recipients"] = recipients
        return True

    # escalate_overdue_approvals imports _safe_send_email locally from its
    # source module at call time, so the patch target is that module, not
    # ai_chat_approval_service's own namespace.
    monkeypatch.setattr(
        "app._bootstrap._digest_emails._safe_send_email", _fake_send
    )

    stats = escalate_overdue_approvals(current_app._get_current_object())

    assert stats["organisations_notified"] == 1
    assert sent["recipients"] == [admin_a.email]


def test_escalation_reaches_a_real_org_admin_without_platform_admin_role(db_session, make_org, monkeypatch):
    """reviews/pr302-final-check-v1.md HIGH: an organisation can have a real
    administrator (is_org_admin=True) with no user holding the separate,
    enterprise-wide platform_admin role -- escalation must still reach them,
    not silently notify nobody.
    """
    from app.modules.ai_chat.services.ai_chat_approval_service import (
        create_approval_record,
        escalate_overdue_approvals,
    )
    from flask import current_app

    org_a = make_org("esc-orgadmin-a")
    org_b = make_org("esc-orgadmin-b")
    org_admin_a = _make_user(db_session, org_a.id, "orgadmin-a@example.com", is_org_admin=True)
    # org B has neither a platform_admin nor an org_admin: must not be notified.
    _make_user(db_session, org_b.id, "plain-b@example.com")

    approval_a = create_approval_record(
        organization_id=org_a.id, operation_type="create", entity_type="capability",
        summary="Org A overdue, org-admin recipient", operation_payload={}, user_id=None,
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
    assert sent["recipients"] == [org_admin_a.email]


# --------------------------------------------------------------------- #
# Constructor-site repointing
# --------------------------------------------------------------------- #


def test_review_queue_item_creation_also_creates_an_approval(db_session, make_org, tenant_ctx):
    from app.services.confidence_review_service import ConfidenceReviewService, ReviewQueueItemData
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.models.archimate_core import ArchiMateElement

    org = make_org("rqi")
    element = ArchiMateElement(name="Test element", type="ApplicationComponent", organization_id=org.id)
    db_session.add(element)
    db_session.flush()

    with tenant_ctx(org.id):
        svc = ConfidenceReviewService()
        item_data = ReviewQueueItemData(
            item_type="archimate_element", item_id=element.id, item_name="Test element",
            item_data={}, confidence_score=0.4, confidence_factors={},
            ai_model_used="test", generation_timestamp=datetime.utcnow(),
            threshold_name="test-threshold",
        )
        result = svc.add_to_review_queue(item_data, {"action": {"priority": 5, "estimated_review_time": 1}})

    assert result["success"] is True
    approval = AIChatCRUDApproval.query.filter_by(
        source_table="review_queue_items", source_id=result["review_item_id"]
    ).first()
    assert approval is not None
    assert approval.organization_id == org.id
    # DEFECT-002 regression: the source row must be marked superseded
    # immediately, or the backfill command would re-copy it and create a
    # second approval for the same source row the next time it runs.
    from app.models.confidence_review import ReviewQueueItem
    review_item = ReviewQueueItem.query.get(result["review_item_id"])
    assert review_item.retired_into_id == approval.id


def test_blueprint_proposal_creation_also_creates_an_approval(db_session, make_org, tenant_ctx):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
    from app.services.solution_blueprint_service import create_solution_blueprint_proposal
    from app.models.solution_models import Solution

    org = make_org("blueprint")

    with tenant_ctx(org.id):
        solution = Solution(name="Test Solution", organization_id=org.id)
        db_session.add(solution)
        db_session.flush()

        proposal = create_solution_blueprint_proposal(
            solution_id=solution.id,
            archimate_type="ApplicationComponent",
            name="Proposed Component",
            organization_id=org.id,
        )
        db_session.commit()

    approval = AIChatCRUDApproval.query.filter_by(
        source_table="solution_blueprint_proposals", source_id=proposal.id
    ).first()
    assert approval is not None
    assert approval.organization_id == org.id
    assert approval.entity_type == "solution_blueprint_element"

    # DEFECT-001 regression: the source row must be marked superseded
    # immediately, or the backfill command would re-copy it and create a
    # second approval for the same source row the next time it runs.
    reloaded = SolutionBlueprintProposal.query.filter_by(id=proposal.id).first()
    assert reloaded.retired_into_id == approval.id


def test_approval_source_pair_is_unique(db_session, make_org, tenant_ctx):
    """DEFECT-003 regression: at most one canonical approval per source row."""
    from sqlalchemy.exc import IntegrityError

    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    org = make_org("uniq")

    with tenant_ctx(org.id):
        db_session.add(AIChatCRUDApproval(
            organization_id=org.id, operation_type="review", entity_type="x",
            original_command="", operation_payload="{}", summary="first",
            expires_at=datetime.utcnow() + timedelta(minutes=15),
            source_table="review_queue_items", source_id=999,
        ))
        db_session.flush()

        db_session.add(AIChatCRUDApproval(
            organization_id=org.id, operation_type="review", entity_type="x",
            original_command="", operation_payload="{}", summary="duplicate",
            expires_at=datetime.utcnow() + timedelta(minutes=15),
            source_table="review_queue_items", source_id=999,
        ))
        with pytest.raises(IntegrityError):
            db_session.flush()


def test_agent_runner_queued_approval_carries_the_actors_organisation(db_session, make_org, tenant_ctx):
    """Regression: AgentRunner._queue_approval constructed AIChatCRUDApproval
    directly, leaving organization_id NULL -- invisible to every
    organisation-scoped query and skipped by escalate_overdue_approvals.
    """
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.tools.executor import ToolCall

    org = make_org("agentqueue")

    with tenant_ctx(org.id):
        user = _make_user(db_session, org.id, "agent-caller@example.com")
        db_session.commit()

        runner = AgentRunner(user_id=user.id, chat_session_id="sess-1")
        runner._turn_id = "turn-1"
        approval_id = runner._queue_approval(
            ToolCall(id="tc-1", name="create_capability", arguments={"name": "Test Capability"})
        )

    approval = AIChatCRUDApproval.query.filter_by(id=approval_id).first()
    assert approval is not None
    assert approval.organization_id == org.id
    assert approval.user_id == user.id
    assert approval.operation_type == "tool_use"
    assert approval.chat_session_id == "sess-1"


def test_archimate_mapping_agents_relationship_suggestion_is_not_the_orm_model():
    """Two unrelated classes share this name -- confirms the docstring in
    app/models/archimate_core.py isn't claiming something false. The ORM
    model (app.models.archimate_core.RelationshipSuggestion) has no live
    constructor site anywhere in this codebase; the plain @dataclass in
    archimate_mapping_agent.py (same name, never imported from the ORM
    module, never added to a session) is what suggest_relationships()
    actually constructs, and it never reaches this table.
    """
    from dataclasses import is_dataclass

    from app.models.archimate_core import RelationshipSuggestion as ORMModel
    from app.modules.architecture.services.archimate_mapping_agent import (
        RelationshipSuggestion as LocalSuggestion,
    )

    assert LocalSuggestion is not ORMModel
    assert is_dataclass(LocalSuggestion)
    assert not hasattr(LocalSuggestion, "__tablename__")
    assert hasattr(ORMModel, "__tablename__")

    # The constructor at archimate_mapping_agent.py:185 uses exactly the
    # local dataclass's fields -- this would TypeError against the ORM
    # model's actual columns (source_element_id, target_element_id,
    # confidence, reason), which don't match.
    suggestion = LocalSuggestion(
        source_id=1, target_id=2, relationship_type="serving",
        confidence_score=0.9, reasoning="test",
    )
    assert suggestion.source_id == 1
