"""F-01: ARB decisions must mirror into AuditLog (soc2_audit_log).

/admin/audit-log (app/modules/admin/v2/routes/admin_routes.py,
audit_log_viewer) queries only AuditLog. ARBAuditService recorded governance
decisions into ARBAuditLog, a table that page never reads, so "who approved
this change" was unanswerable there for ARB decisions even though the data
existed. Every ARBAuditLog row is now copied onto AuditLog (the model's
insert hook), decisions included.

Uses the shared fixtures (tests/conftest.py) per CLAUDE.md's convention.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_org(db_session):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Test org {suffix}", slug=f"test-org-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _make_user(db_session, org_id, email):
    from app.models.user import User

    unique_email = email.replace("@", f"+{uuid.uuid4().hex[:8]}@")
    user = User(
        email=unique_email,
        first_name="Test",
        last_name="User",
        organization_id=org_id,
    )
    if hasattr(user, "set_password"):
        user.set_password("password123!")
    db_session.add(user)
    db_session.flush()
    return user


def _make_review_item(db_session, org_id, submitter_id):
    from app.models.architecture_review_board import ARBReviewItem

    suffix = uuid.uuid4().hex[:8]
    item = ARBReviewItem(
        organization_id=org_id,
        review_number=f"REV-TEST-{suffix}",
        title="Test solution review",
        review_type="solution_design",
        status="under_review",
        submitter_id=submitter_id,
    )
    db_session.add(item)
    db_session.flush()
    return item


def test_arb_decision_mirrors_into_compliance_audit_log(db_session, tenant_ctx):
    """RED before the fix: AuditLog had no row for this decision even though
    ARBAuditLog did — the compliance page could not surface it.
    """
    from app.services.arb_audit_service import ARBAuditService
    from app.models.architecture_review_board import ARBAuditLog
    from app.models.audit_log import AuditLog

    org = _make_org(db_session)
    reviewer = _make_user(db_session, org.id, "arb-reviewer@example.com")

    with tenant_ctx(org.id):
        item = _make_review_item(db_session, org.id, reviewer.id)

        service = ARBAuditService()
        service.log_decision(
            review_item=item,
            decision="approved_with_conditions",
            user_id=reviewer.id,
            rationale="Meets requirements with follow-up",
        )

        # Detail still lands in the specialised table.
        specialised = ARBAuditLog.query.filter_by(
            entity_type="review_item", entity_id=item.id, action="decision"
        ).all()
        assert len(specialised) == 1

        # And it is now mirrored where the compliance viewer actually looks.
        mirrored = AuditLog.query.filter_by(
            organization_id=org.id, user_id=reviewer.id, action="decision"
        ).filter(AuditLog.table_name == "arb:review_item", AuditLog.record_id == item.id).all()
        assert len(mirrored) == 1
        assert mirrored[0].user_id == reviewer.id
        assert mirrored[0].new_value["new_value"]["decision"] == "approved_with_conditions"


def test_arb_routine_action_is_copied_with_provenance(db_session, tenant_ctx):
    """Every ARB action reaches the one audit store, not only decisions.

    This used to assert the opposite (routine updates stayed in ARBAuditLog
    only). The audit trail must be complete, so every ARBAuditLog row is now
    copied into AuditLog with source_table/source_id, and the source row
    points at its copy through retired_into_id.
    """
    from app.services.arb_audit_service import ARBAuditService
    from app.models.audit_log import AuditLog

    org = _make_org(db_session)
    reviewer = _make_user(db_session, org.id, "arb-editor@example.com")

    with tenant_ctx(org.id):
        item = _make_review_item(db_session, org.id, reviewer.id)

        service = ARBAuditService()
        source = service.log_action(
            entity_type="review_item",
            entity_id=item.id,
            action="update",
            user_id=reviewer.id,
            entity_reference=item.review_number,
            old_value={"title": "old"},
            new_value={"title": "new"},
            changed_fields=["title"],
            description="Title updated",
        )

        mirrored = AuditLog.query.filter_by(
            organization_id=org.id, user_id=reviewer.id, action="update"
        ).filter(AuditLog.table_name == "arb:review_item", AuditLog.record_id == item.id).all()
        assert len(mirrored) == 1
        copy = mirrored[0]
        assert copy.source_table == "arb_audit_logs"
        assert copy.source_id == source.id
        assert copy.old_value == {"title": "old"}
        assert copy.row_hash is not None
        assert source.retired_into_id == copy.id
