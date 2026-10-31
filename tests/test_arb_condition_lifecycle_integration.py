"""PostgreSQL integration for the typed ARB condition lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import threading
import uuid

import pytest
from sqlalchemy import func


os.environ["TRANSFORMATION_COMMAND_CAPABILITY_SECRET"] = "74" * 32


_C3_CLEANUP_TABLES = (
    "archie_command_claim_challenges",
    "arb_condition_events", "arb_canonical_conditions",
    "arb_condition_evidence_records", "arb_decision_events",
    "arb_submission_events", "operation_results",
    "command_materialisations", "command_idempotency_records",
    "arb_review_items", "arb_review_cycles",
    "arb_subject_evidence_snapshots",
    "architecture_decisions", "users",
)
_C3_CLEANUP_ASSERT_TABLES = _C3_CLEANUP_TABLES


@pytest.fixture
def db_session(app, _schema):
    """Committed setup visible to CommandService's independent sessions."""
    from app import db

    with app.app_context():
        db.session.remove()
        cleanup_org_ids = set()
        cleanup_checkpoint_keys = set()
        db.session.info["c3_cleanup_org_ids"] = cleanup_org_ids
        db.session.info["c3_cleanup_checkpoint_keys"] = cleanup_checkpoint_keys
        try:
            yield db.session
        finally:
            organization_ids = tuple(cleanup_org_ids)
            checkpoint_keys = tuple(cleanup_checkpoint_keys)
            db.session.remove()
            if organization_ids:
                from app.models.organization import Organization
                from app.services.org_deletion_service import OrganizationDeletionService

                raw = db.engine.raw_connection()
                try:
                    with raw.cursor() as cursor:
                        if checkpoint_keys:
                            cursor.execute(
                                "DELETE FROM arb_waiver_expiry_checkpoints "
                                "WHERE scope_key = ANY(%s)",
                                (list(checkpoint_keys),),
                            )
                        # archie_command_claim_challenges carries organization_id with no
                        # foreign key to organizations (a deliberate, trigger-free,
                        # TTL-cleaned table — see transformation_db_guards.py), so the
                        # deletion service's foreign-key-driven plan never reaches it and
                        # an un-expired nonce for this organisation outlives it. No guard
                        # sits on this table, so a plain delete needs no marker and no
                        # escape hatch.
                        cursor.execute(
                            "DELETE FROM archie_command_claim_challenges "
                            "WHERE organization_id = ANY(%s)",
                            (list(organization_ids),),
                        )
                        raw.commit()
                finally:
                    raw.close()
                for org_id in organization_ids:
                    if db.session.get(Organization, org_id) is not None:
                        OrganizationDeletionService.delete_organization(org_id)
                db.session.remove()

                raw = db.engine.raw_connection()
                try:
                    with raw.cursor() as cursor:
                        residual = {}
                        for table in _C3_CLEANUP_ASSERT_TABLES:
                            cursor.execute(
                                f'SELECT count(*) FROM "{table}" '
                                "WHERE organization_id = ANY(%s)",
                                (list(organization_ids),),
                            )
                            count = cursor.fetchone()[0]
                            if count:
                                residual[table] = count
                        cursor.execute(
                            "SELECT count(*) FROM organizations WHERE id = ANY(%s)",
                            (list(organization_ids),),
                        )
                        organization_count = cursor.fetchone()[0]
                        if organization_count:
                            residual["organizations"] = organization_count
                        if checkpoint_keys:
                            cursor.execute(
                                "SELECT count(*) FROM arb_waiver_expiry_checkpoints "
                                "WHERE scope_key = ANY(%s)",
                                (list(checkpoint_keys),),
                            )
                            checkpoint_count = cursor.fetchone()[0]
                            if checkpoint_count:
                                residual["arb_waiver_expiry_checkpoints"] = (
                                    checkpoint_count
                                )
                        if residual:
                            raise AssertionError(
                                f"C3 cleanup did not restore database state: residual={residual!r}"
                            )
                finally:
                    raw.close()


def test_submit_verify_and_projection_commit_under_real_guards(
    app, db_session, make_org, monkeypatch
):
    from sqlalchemy.exc import DBAPIError

    from app import db
    from app.models.arb_condition_event import ensure_arb_condition_event_guards
    from app.models.arb_condition_evidence import ensure_arb_condition_evidence_guards
    from app.models.arb_decision_event import (
        ARBCondition,
        ARBDecisionEvent,
        ensure_arb_decision_guards,
    )
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.architecture_review_board import (
        ARBReviewCycle,
        ARBReviewItem,
        ensure_arb_cycle_constraints,
    )
    from app.models.transformation_db_guards import ensure_transformation_db_guards
    from app.models.user import User
    from app.modules.transformation_room.arb_condition_evidence_service import (
        TypedARBConditionEvidenceService,
    )
    from app.modules.transformation_room.arb_condition_lifecycle_service import (
        TypedARBConditionLifecycleService,
    )
    from app.modules.transformation_room.arb_decision_service import (
        TypedARBDecisionService,
    )
    from app.modules.transformation_room.arb_submission_service import (
        TypedARBSubmissionService,
    )
    from app.modules.transformation_room.command_service import CommandService
    from app.modules.transformation_room.domain import ActorContext

    monkeypatch.setenv(
        "TRANSFORMATION_COMMAND_CAPABILITY_SECRET",
        "74" * 32,
    )
    connection = db_session.connection()
    ensure_transformation_db_guards(
        connection, capability_secrets=("74" * 32,)
    )
    ensure_arb_cycle_constraints(connection)
    ensure_arb_decision_guards(connection)
    ensure_arb_condition_evidence_guards(connection)
    ensure_arb_condition_event_guards(connection)

    org = make_org("c3-lifecycle")
    db_session.info.setdefault("c3_cleanup_org_ids", set()).add(org.id)
    suffix = uuid.uuid4().hex[:10]
    submitter = User(
        organization_id=org.id, email=f"c3-submit-{suffix}@example.test",
        enterprise_role="enterprise_architect", confirmed=True,
    )
    verifier = User(
        organization_id=org.id, email=f"c3-verify-{suffix}@example.test",
        enterprise_role="enterprise_architect", confirmed=True,
    )
    db_session.add_all((submitter, verifier))
    db_session.flush()
    adr = ArchitectureDecision(
        organization_id=org.id, decision_id=f"AD-{suffix[:7]}",
        title=f"C3 lifecycle {suffix}", status="proposed",
        context="A governed choice needs evidence.",
        decision="Adopt the governed option.", rationale="It is testable.",
        consequences="Conditions must be verified.", created_by_id=submitter.id,
    )
    db_session.add(adr)
    db_session.commit()
    submitter_actor = ActorContext(
        submitter.id, org.id, frozenset(), f"c3-submit-{suffix}"
    )
    verifier_actor = ActorContext(
        verifier.id, org.id, frozenset(), f"c3-verify-{suffix}"
    )
    submission = TypedARBSubmissionService.submit(
        actor=submitter_actor, command_key=f"submit-{suffix}",
        subject_type="adr", subject_id=adr.id,
        assertions={"human_reviewed": True},
    )
    decision = TypedARBDecisionService.decide(
        actor=verifier_actor, command_key=f"decision-{suffix}",
        cycle_id=submission.object_ids["review_cycle_id"],
        outcome="approved_with_conditions", rationale="Approved with proof.",
        conditions=[
            {"code": "C-1", "text": "Provide deployment proof."},
            {"code": "C-2", "text": "Time-bound operational control."},
        ],
    )
    condition_id = decision.object_ids["condition_ids"][0]
    waiver_condition_id = decision.object_ids["condition_ids"][1]
    legacy_insert = db.text("""
        INSERT INTO arb_canonical_conditions (
            organization_id, decision_event_id, review_cycle_id, review_item_id,
            condition_number, description, blocks_execution, status, revision,
            fulfilled_at, fulfilled_by_id, fulfilment_evidence_id,
            verified_at, verified_by_id, legacy_lifecycle_provenance
        )
        SELECT organization_id, decision_event_id, review_cycle_id, review_item_id,
               :condition_number, :description, TRUE, 'fulfilled', 1,
               clock_timestamp(), :actor_id, NULL,
               clock_timestamp(), :actor_id, CAST(:provenance AS json)
        FROM arb_canonical_conditions
        WHERE id=:source_condition_id AND organization_id=:organization_id
        RETURNING id
    """)
    legacy_provenance = {
        "classification": "pre_c3_fulfilment",
        "legacy_fulfilment_evidence_id": None,
    }
    legacy_parameters = {
        "condition_number": f"C-LEGACY-{suffix}",
        "description": "Pre-C3 fulfilled condition",
        "actor_id": verifier.id,
        "provenance": json.dumps(legacy_provenance),
        "source_condition_id": condition_id,
        "organization_id": org.id,
    }
    with pytest.raises(
        DBAPIError, match="legacy ARB condition provenance is reconcile-only"
    ):
        db_session.execute(legacy_insert, legacy_parameters)
        db_session.commit()
    db_session.rollback()

    db_session.execute(db.text("SET LOCAL session_replication_role = replica"))  # session-replication-role-ok: constructs a row shaped like data written before this validation existed, to prove reconciliation of genuinely pre-existing legacy rows; no real caller can write this shape today
    legacy_condition_id = db_session.execute(
        legacy_insert, legacy_parameters
    ).scalar_one()
    db_session.commit()
    assert db_session.execute(
        db.text("SHOW session_replication_role")  # session-replication-role-ok: read-only check that the replica mode set above was scoped to its own transaction and already reverted
    ).scalar_one() == "origin"
    mutated_provenance = {
        "classification": "pre_c3_fulfilment",
        "legacy_fulfilment_evidence_id": 999,
    }
    with pytest.raises(DBAPIError, match="legacy ARB condition provenance is immutable"):
        db_session.execute(
            db.text(
                "UPDATE arb_canonical_conditions "
                "SET legacy_lifecycle_provenance=CAST(:provenance AS json) "
                "WHERE id=:condition_id AND organization_id=:organization_id"
            ),
            {
                "provenance": json.dumps(mutated_provenance),
                "condition_id": legacy_condition_id,
                "organization_id": org.id,
            },
        )
    db_session.rollback()
    assert db_session.execute(
        db.text(
            "SELECT legacy_lifecycle_provenance::jsonb "
            "FROM arb_canonical_conditions "
            "WHERE id=:condition_id AND organization_id=:organization_id"
        ),
        {"condition_id": legacy_condition_id, "organization_id": org.id},
    ).scalar_one() == legacy_provenance

    now = datetime.now(timezone.utc)
    evidence = TypedARBConditionEvidenceService.capture(
        actor=submitter_actor, command_key=f"capture-{suffix}",
        condition_id=condition_id,
        evidence={
            "source_identity": f"cmdb:{suffix}", "source_type": "cmdb",
            "source_version": "1", "source_checksum": "a" * 64,
            "value_json": {"verified": True},
            "observed_at": (now - timedelta(minutes=1)).isoformat(),
            "freshness_rule_version": "arb-condition-v1",
            "freshness_expires_at": (now + timedelta(days=1)).isoformat(),
        },
    )
    with pytest.raises(
        DBAPIError, match="ARB condition lifecycle mutation lacks exact event provenance"
    ):
        db_session.execute(
            db.text(
                "UPDATE arb_canonical_conditions "
                "SET status='evidence_submitted', revision=revision + 1, "
                "submitted_evidence_id=:evidence_id, "
                "evidence_submitted_by_id=:actor_id, "
                "evidence_submitted_at=clock_timestamp() "
                "WHERE id=:condition_id AND organization_id=:organization_id"
            ),
            {
                "evidence_id": evidence.object_ids["condition_evidence_id"],
                "actor_id": submitter.id,
                "condition_id": condition_id,
                "organization_id": org.id,
            },
        )
    db_session.rollback()
    assert db_session.execute(
        db.text(
            "SELECT status FROM arb_canonical_conditions "
            "WHERE id=:condition_id AND organization_id=:organization_id"
        ),
        {"condition_id": condition_id, "organization_id": org.id},
    ).scalar_one() == "pending"

    submitted = TypedARBConditionLifecycleService.submit_evidence(
        actor=submitter_actor, command_key=f"evidence-submit-{suffix}",
        condition_id=condition_id,
        condition_evidence_id=evidence.object_ids["condition_evidence_id"],
    )
    submitted_replay = TypedARBConditionLifecycleService.submit_evidence(
        actor=submitter_actor, command_key=f"evidence-submit-{suffix}",
        condition_id=condition_id,
        condition_evidence_id=evidence.object_ids["condition_evidence_id"],
    )
    db_session.expire_all()
    cycle = db_session.get(ARBReviewCycle, submission.object_ids["review_cycle_id"])
    review = db_session.get(ARBReviewItem, submission.object_ids["review_item_id"])
    assert submitted.response["projection_status"] == "approved_with_conditions"
    assert submitted_replay.object_ids == submitted.object_ids
    assert cycle.status == review.status == "approved_with_conditions"
    assert cycle.terminal_outcome == review.decision == "approved_with_conditions"

    submitted_condition = db_session.get(ARBCondition, condition_id)
    submitted_evidence_state = (
        submitted_condition.submitted_evidence_id,
        submitted_condition.evidence_submitted_by_id,
        submitted_condition.evidence_submitted_at,
    )
    database_now = CommandService._database_now
    waiver_now = database_now(db_session) - timedelta(days=2)
    monkeypatch.setattr(
        CommandService, "_database_now", staticmethod(lambda session: waiver_now),
    )
    TypedARBConditionLifecycleService.waive(
        actor=verifier_actor, command_key=f"evidence-waive-{suffix}",
        condition_id=condition_id,
        reason="Temporary evidence review exception",
        expires_at=waiver_now + timedelta(days=1),
        scope={"evidence": "deployment-proof"},
        compensating_control="Daily evidence review",
    )
    db_session.expire_all()
    monkeypatch.setitem(app.config, "ARB_CONDITION_EXPIRY_PRINCIPAL_ID", verifier.id)
    monkeypatch.setitem(app.config, "ARB_CONDITION_EXPIRY_ORGANIZATION_ID", org.id)
    monkeypatch.setitem(
        app.config, "ARB_CONDITION_EXPIRY_CAPABILITY", "c3-expiry-capability"
    )
    monkeypatch.setattr(CommandService, "_database_now", staticmethod(database_now))
    restored = TypedARBConditionLifecycleService.expire_waivers(
        capability="c3-expiry-capability",
        command_key=f"evidence-expiry-{suffix}",
        condition_id=condition_id,
    )
    db_session.expire_all()
    restored_condition = db_session.get(ARBCondition, condition_id)
    assert restored.response["status"] == "evidence_submitted"
    assert restored_condition.status == "evidence_submitted"
    assert restored_condition.revision == 4
    assert (
        restored_condition.submitted_evidence_id,
        restored_condition.evidence_submitted_by_id,
        restored_condition.evidence_submitted_at,
    ) == submitted_evidence_state
    assert restored_condition.waived_at is None
    assert restored_condition.waived_by_id is None
    assert restored_condition.waiver_reason is None
    assert restored_condition.waiver_expires_at is None
    assert restored_condition.compensating_control is None
    assert restored_condition.waiver_prior_status is None
    assert restored_condition.waiver_scope_json is None

    verified = TypedARBConditionLifecycleService.verify(
        actor=verifier_actor, command_key=f"verify-{suffix}",
        condition_id=condition_id,
        condition_evidence_id=evidence.object_ids["condition_evidence_id"],
    )
    replay = TypedARBConditionLifecycleService.verify(
        actor=verifier_actor, command_key=f"verify-{suffix}",
        condition_id=condition_id,
        condition_evidence_id=evidence.object_ids["condition_evidence_id"],
    )
    db_session.expire_all()
    condition = db_session.get(ARBCondition, condition_id)
    cycle = db_session.get(ARBReviewCycle, cycle.id)
    review = db_session.get(ARBReviewItem, review.id)
    assert verified.object_ids == replay.object_ids
    assert condition.status == "fulfilled"
    assert cycle.status == review.status == "approved_with_conditions"
    assert cycle.terminal_outcome == review.decision == "approved_with_conditions"
    assert db_session.query(ARBDecisionEvent).filter_by(
        organization_id=org.id
    ).count() == 1

    monkeypatch.setattr(
        CommandService, "_database_now", staticmethod(lambda session: waiver_now),
    )
    TypedARBConditionLifecycleService.waive(
        actor=verifier_actor, command_key=f"waive-{suffix}",
        condition_id=waiver_condition_id,
        reason="Temporary operational acceptance",
        expires_at=waiver_now + timedelta(days=1),
        scope={"release": "R1"},
        compensating_control="Daily operational review",
    )
    db_session.expire_all()
    cycle = db_session.get(ARBReviewCycle, cycle.id)
    assert cycle.status == "approved"
    assert cycle.terminal_outcome == "approved_with_conditions"

    monkeypatch.setattr(CommandService, "_database_now", staticmethod(database_now))
    expired = TypedARBConditionLifecycleService.expire_waivers(
        capability="c3-expiry-capability", command_key=f"expiry-{suffix}",
        condition_id=waiver_condition_id,
    )
    db_session.expire_all()
    cycle = db_session.get(ARBReviewCycle, cycle.id)
    review = db_session.get(ARBReviewItem, review.id)
    assert expired.response["status"] == "pending"
    assert cycle.status == review.status == "approved_with_conditions"
    assert cycle.terminal_outcome == review.decision == "approved_with_conditions"


def test_automatic_expiry_is_bounded_tenant_explicit_concurrent_and_retry_safe(
    app, db_session, make_org, monkeypatch
):
    from app import db
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.arb_condition_event import (
        ARBConditionEvent,
        ensure_arb_condition_event_guards,
    )
    from app.models.arb_condition_evidence import ensure_arb_condition_evidence_guards
    from app.models.arb_decision_event import ARBCondition, ensure_arb_decision_guards
    from app.models.architecture_review_board import (
        ARBReviewCycle,
        ensure_arb_cycle_constraints,
    )
    from app.models.transformation_db_guards import ensure_transformation_db_guards
    from app.models.user import User
    from app.modules.transformation_room.arb_condition_lifecycle_service import (
        TypedARBConditionLifecycleService,
    )
    from app.modules.transformation_room.arb_decision_service import (
        TypedARBDecisionService,
    )
    from app.modules.transformation_room.arb_submission_service import (
        TypedARBSubmissionService,
    )
    from app.modules.transformation_room.arb_waiver_expiry_batch_service import (
        ARBWaiverExpiryBatchService,
    )
    from app.modules.transformation_room.command_service import CommandService
    from app.modules.transformation_room.domain import ActorContext

    connection = db_session.connection()
    ensure_transformation_db_guards(connection, capability_secrets=("74" * 32,))
    ensure_arb_cycle_constraints(connection)
    ensure_arb_decision_guards(connection)
    ensure_arb_condition_evidence_guards(connection)
    ensure_arb_condition_event_guards(connection)

    suffix = uuid.uuid4().hex[:10]
    organizations = (make_org(f"expiry-a-{suffix}"), make_org(f"expiry-b-{suffix}"))
    organization_ids = tuple(organization.id for organization in organizations)
    for organization in organizations:
        db_session.info.setdefault("c3_cleanup_org_ids", set()).add(organization.id)
    users = {}
    for organization in organizations:
        submitter = User(
            organization_id=organization.id,
            email=f"expiry-submit-{organization.id}-{suffix}@example.test",
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        principal = User(
            organization_id=organization.id,
            email=f"expiry-principal-{organization.id}-{suffix}@example.test",
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        db_session.add_all((submitter, principal))
        db_session.flush()
        users[organization.id] = (submitter.id, principal.id)
    db_session.commit()

    database_now = CommandService._database_now
    real_now = database_now(db_session)
    serial = 0

    def create_waived(organization_id, *, due):
        nonlocal serial
        serial += 1
        submitter_id, principal_id = users[organization_id]
        adr = ArchitectureDecision(
            organization_id=organization_id,
            decision_id=f"AD-{suffix[:6]}{serial}",
            title=f"Automatic expiry {suffix} {serial}",
            status="proposed",
            context="A time-bound waiver needs automatic expiry.",
            decision="Use the governed expiry worker.",
            rationale="The lifecycle must remain auditable.",
            consequences="Expired conditions block execution again.",
            created_by_id=submitter_id,
        )
        db_session.add(adr)
        db_session.commit()
        submitter = ActorContext(
            submitter_id, organization_id, frozenset(), f"expiry-submit-{serial}"
        )
        principal = ActorContext(
            principal_id, organization_id, frozenset(), f"expiry-principal-{serial}"
        )
        submission = TypedARBSubmissionService.submit(
            actor=submitter,
            command_key=f"expiry-submission-{suffix}-{serial}",
            subject_type="adr",
            subject_id=adr.id,
            assertions={"human_reviewed": True},
        )
        decision = TypedARBDecisionService.decide(
            actor=principal,
            command_key=f"expiry-decision-{suffix}-{serial}",
            cycle_id=submission.object_ids["review_cycle_id"],
            outcome="approved_with_conditions",
            rationale="Approved with a time-bound control.",
            conditions=[{"code": "C-1", "text": "Retain the compensating control."}],
        )
        condition_id = decision.object_ids["condition_ids"][0]
        waiver_now = real_now - timedelta(days=2) if due else real_now
        monkeypatch.setattr(
            CommandService,
            "_database_now",
            staticmethod(lambda session, value=waiver_now: value),
        )
        TypedARBConditionLifecycleService.waive(
            actor=principal,
            command_key=f"expiry-waive-{suffix}-{serial}",
            condition_id=condition_id,
            reason="Temporary automatic-expiry test waiver",
            expires_at=waiver_now + timedelta(days=1),
            scope={"test": suffix, "serial": serial},
            compensating_control="Daily automated control review",
        )
        monkeypatch.setattr(
            CommandService, "_database_now", staticmethod(database_now)
        )
        db.session.remove()
        condition = db.session.execute(
            db.select(ARBCondition).where(
                ARBCondition.id == condition_id,
                ARBCondition.organization_id == organization_id,
            )
        ).scalar_one()
        return condition_id, condition.revision, submission.object_ids["review_cycle_id"]

    due = [
        create_waived(organization_ids[0], due=True),
        create_waived(organization_ids[0], due=True),
        create_waived(organization_ids[1], due=True),
    ]
    not_due = create_waived(organization_ids[1], due=False)
    monkeypatch.setitem(app.config, "ARB_CONDITION_EXPIRY_CAPABILITY", "batch-secret")
    monkeypatch.setitem(
        app.config,
        "ARB_CONDITION_EXPIRY_PRINCIPALS",
        {str(org_id): users[org_id][1] for org_id in organization_ids},
    )
    db_session.info.setdefault("c3_cleanup_checkpoint_keys", set()).add(
        ARBWaiverExpiryBatchService._scope_key(organization_ids)
    )

    first = ARBWaiverExpiryBatchService.run(
        organization_ids=reversed(organization_ids), batch_size=2
    )
    second = ARBWaiverExpiryBatchService.run(
        organization_ids=organization_ids, batch_size=2
    )
    assert (first.selected_count, first.expired_count, first.failed_count) == (2, 2, 0)
    assert (second.selected_count, second.expired_count, second.failed_count) == (1, 1, 0)

    db.session.remove()
    due_rows = db.session.execute(
        db.select(ARBCondition).where(
            ARBCondition.organization_id.in_(organization_ids),
            ARBCondition.id.in_([item[0] for item in due]),
        )
    ).scalars().all()
    future_row = db.session.execute(
        db.select(ARBCondition).where(
            ARBCondition.id == not_due[0],
            ARBCondition.organization_id == organization_ids[1],
        )
    ).scalar_one()
    assert {row.status for row in due_rows} == {"pending"}
    assert future_row.status == "waived"
    cycles = db.session.execute(
        db.select(ARBReviewCycle).where(
            ARBReviewCycle.organization_id.in_(organization_ids),
            ARBReviewCycle.id.in_([item[2] for item in due] + [not_due[2]]),
        )
    ).scalars().all()
    cycle_status = {cycle.id: cycle.status for cycle in cycles}
    assert all(cycle_status[item[2]] == "approved_with_conditions" for item in due)
    assert cycle_status[not_due[2]] == "approved"

    first_condition_id, first_waived_revision, _ = due[0]
    replay = TypedARBConditionLifecycleService.expire_waivers(
        capability="batch-secret",
        command_key=(
            f"arb-waiver-expiry:{organization_ids[0]}:"
            f"{first_condition_id}:{first_waived_revision + 1}"
        ),
        condition_id=first_condition_id,
        organization_id=organization_ids[0],
    )
    assert replay.idempotent is True
    assert db.session.execute(
        db.select(func.count(ARBConditionEvent.id)).where(
            ARBConditionEvent.organization_id == organization_ids[0],
            ARBConditionEvent.condition_id == first_condition_id,
            ARBConditionEvent.event_type == "waiver_expired",
        )
    ).scalar_one() == 1

    held_due = create_waived(organization_ids[0], due=True)
    free_due = create_waived(organization_ids[1], due=True)
    lock_connection = db.engine.connect()
    lock_transaction = lock_connection.begin()
    try:
        lock_connection.execute(
            db.text(
                "SELECT id FROM arb_canonical_conditions "
                "WHERE id=:condition_id AND organization_id=:organization_id "
                "FOR UPDATE"
            ),
            {
                "condition_id": held_due[0],
                "organization_id": organization_ids[0],
            },
        )
        skip_locked = ARBWaiverExpiryBatchService.run(
            organization_ids=organization_ids, batch_size=1
        )
        assert skip_locked.expired_count == 1
        assert skip_locked.errors == ()
    finally:
        lock_transaction.rollback()
        lock_connection.close()
    db.session.remove()
    assert db.session.execute(
        db.select(ARBCondition.status).where(
            ARBCondition.id == held_due[0],
            ARBCondition.organization_id == organization_ids[0],
        )
    ).scalar_one() == "waived"
    assert db.session.execute(
        db.select(ARBCondition.status).where(
            ARBCondition.id == free_due[0],
            ARBCondition.organization_id == organization_ids[1],
        )
    ).scalar_one() == "pending"

    monkeypatch.setitem(
        app.config,
        "ARB_CONDITION_EXPIRY_PRINCIPALS",
        {str(organization_ids[0]): users[organization_ids[0]][1]},
    )
    partial = ARBWaiverExpiryBatchService.run(
        organization_ids=organization_ids, batch_size=10
    )
    assert partial.selected_count == 1
    assert partial.expired_count == 1
    assert partial.failed_count == 0

    failure_due = create_waived(organization_ids[1], due=True)
    success_due = create_waived(organization_ids[0], due=True)
    partial = ARBWaiverExpiryBatchService.run(
        organization_ids=organization_ids, batch_size=10
    )
    assert partial.selected_count == 2
    assert partial.expired_count == 1
    assert partial.failed_count == 1
    assert partial.errors[0]["organization_id"] == organization_ids[1]
    assert partial.errors[0]["condition_id"] == failure_due[0]
    db.session.remove()
    assert db.session.execute(
        db.select(ARBCondition.status).where(
            ARBCondition.id == success_due[0],
            ARBCondition.organization_id == organization_ids[0],
        )
    ).scalar_one() == "pending"
    assert db.session.execute(
        db.select(ARBCondition.status).where(
            ARBCondition.id == failure_due[0],
            ARBCondition.organization_id == organization_ids[1],
        )
    ).scalar_one() == "waived"

    monkeypatch.setitem(
        app.config,
        "ARB_CONDITION_EXPIRY_PRINCIPALS",
        {str(org_id): users[org_id][1] for org_id in organization_ids},
    )
    retried = ARBWaiverExpiryBatchService.run(
        organization_ids=organization_ids, batch_size=10
    )
    assert (retried.selected_count, retried.expired_count, retried.failed_count) == (1, 1, 0)

    overlap_due = create_waived(organization_ids[0], due=True)
    entered = threading.Event()
    release = threading.Event()
    worker_result = {}
    original_process = ARBWaiverExpiryBatchService._process_candidates

    def pause_while_locked(cls, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return original_process(**kwargs)

    monkeypatch.setattr(
        ARBWaiverExpiryBatchService,
        "_process_candidates",
        classmethod(pause_while_locked),
    )

    def run_worker():
        with app.app_context():
            worker_result["value"] = ARBWaiverExpiryBatchService.run(
                organization_ids=organization_ids, batch_size=10
            )

    thread = threading.Thread(target=run_worker)
    thread.start()
    assert entered.wait(timeout=10)
    overlapping = ARBWaiverExpiryBatchService.run(
        organization_ids=organization_ids, batch_size=10
    )
    assert overlapping.lock_acquired is False
    assert overlapping.selected_count == 0
    release.set()
    thread.join(timeout=20)
    assert not thread.is_alive()
    assert worker_result["value"].expired_count == 1
    assert worker_result["value"].failed_count == 0
    db.session.remove()
    assert db.session.execute(
        db.select(ARBCondition.status).where(
            ARBCondition.id == overlap_due[0],
            ARBCondition.organization_id == organization_ids[0],
        )
    ).scalar_one() == "pending"

    monkeypatch.setattr(
        ARBWaiverExpiryBatchService,
        "_process_candidates",
        classmethod(original_process.__func__),
    )
    poison = create_waived(organization_ids[0], due=True)
    second_poison = create_waived(organization_ids[0], due=True)
    healthy_after_poison = create_waived(organization_ids[0], due=True)
    real_expire = TypedARBConditionLifecycleService.expire_waivers

    def fail_one_condition(**kwargs):
        if kwargs["condition_id"] in {poison[0], second_poison[0]}:
            raise RuntimeError("persistent condition failure")
        return real_expire(**kwargs)

    monkeypatch.setattr(
        TypedARBConditionLifecycleService,
        "expire_waivers",
        fail_one_condition,
    )
    poison_run = ARBWaiverExpiryBatchService.run(
        organization_ids=organization_ids, batch_size=2
    )
    continuation_run = ARBWaiverExpiryBatchService.run(
        organization_ids=organization_ids, batch_size=2
    )
    assert (poison_run.failed_count, poison_run.expired_count) == (2, 0)
    assert (continuation_run.failed_count, continuation_run.expired_count) == (0, 1)
    db.session.rollback()
    assert db.session.execute(
        db.select(ARBCondition.status).where(
            ARBCondition.id == poison[0],
            ARBCondition.organization_id == organization_ids[0],
        )
    ).scalar_one() == "waived"
    assert db.session.execute(
        db.select(ARBCondition.status).where(
            ARBCondition.id == second_poison[0],
            ARBCondition.organization_id == organization_ids[0],
        )
    ).scalar_one() == "waived"
    assert db.session.execute(
        db.select(ARBCondition.status).where(
            ARBCondition.id == healthy_after_poison[0],
            ARBCondition.organization_id == organization_ids[0],
        )
    ).scalar_one() == "pending"

    monkeypatch.setattr(
        TypedARBConditionLifecycleService,
        "expire_waivers",
        real_expire,
    )
    unconfirmed_due = create_waived(organization_ids[1], due=True)
    db.session.execute(
        db.update(User).where(
            User.id == users[organization_ids[1]][1],
            User.organization_id == organization_ids[1],
        ).values(confirmed=False)
    )
    db.session.commit()
    unconfirmed_run = ARBWaiverExpiryBatchService.run(
        organization_ids=organization_ids, batch_size=1
    )
    assert unconfirmed_run.failed_count == 1
    assert unconfirmed_run.errors[0]["condition_id"] == unconfirmed_due[0]
    assert unconfirmed_run.errors[0]["reason"] == (
        "arb_condition_expiry_principal_invalid"
    )
    db.session.rollback()
    assert db.session.execute(
        db.select(ARBCondition.status).where(
            ARBCondition.id == unconfirmed_due[0],
            ARBCondition.organization_id == organization_ids[1],
        )
    ).scalar_one() == "waived"
    db.session.execute(
        db.update(User).where(
            User.id == users[organization_ids[1]][1],
            User.organization_id == organization_ids[1],
        ).values(confirmed=True)
    )
    db.session.commit()


def test_legacy_condition_reconcile_is_real_and_idempotent(app, _schema):
    from app import db
    from app.models.arb_decision_event import _condition_reconcile_sql

    schema = f"c3_legacy_{uuid.uuid4().hex}"
    with app.app_context():
        engine = db.engine
    quote = engine.dialect.identifier_preparer.quote
    quoted_schema = quote(schema)

    def snapshot(connection):
        return connection.exec_driver_sql(f"""
            SELECT to_jsonb(condition)
            FROM {quoted_schema}.arb_canonical_conditions AS condition
            ORDER BY condition.id
        """).scalars().all()

    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {quoted_schema}")
            connection.exec_driver_sql(f"""
                CREATE TABLE {quoted_schema}.evidence_records (
                    id integer PRIMARY KEY
                );
                CREATE TABLE {quoted_schema}.arb_condition_evidence_records (
                    id integer PRIMARY KEY,
                    organization_id integer NOT NULL,
                    condition_id integer NOT NULL,
                    decision_event_id integer NOT NULL,
                    review_cycle_id integer NOT NULL,
                    review_item_id integer NOT NULL
                );
                CREATE TABLE {quoted_schema}.arb_decision_events (
                    id integer PRIMARY KEY,
                    organization_id integer NOT NULL,
                    review_cycle_id integer NOT NULL,
                    review_item_id integer NOT NULL,
                    outcome varchar(40) NOT NULL
                );
                CREATE TABLE {quoted_schema}.arb_condition_membership_audit (
                    condition_id integer NOT NULL
                );
                CREATE TABLE {quoted_schema}.arb_canonical_conditions (
                    id integer PRIMARY KEY,
                    organization_id integer NOT NULL,
                    decision_event_id integer NOT NULL,
                    review_cycle_id integer NOT NULL,
                    review_item_id integer NOT NULL,
                    status varchar(30) NOT NULL,
                    revision integer,
                    submitted_evidence_id integer,
                    evidence_submitted_by_id integer,
                    evidence_submitted_at timestamptz,
                    fulfilled_at timestamptz,
                    fulfilled_by_id integer,
                    fulfilment_evidence_id integer,
                    verified_at timestamptz,
                    verified_by_id integer,
                    waived_at timestamptz,
                    waived_by_id integer,
                    waiver_reason text,
                    waiver_expires_at timestamptz,
                    compensating_control text,
                    waiver_prior_status varchar(30),
                    waiver_scope_json json,
                    CONSTRAINT ck_arb_condition_status
                        CHECK (status IN ('pending','fulfilled','waived')),
                    CONSTRAINT arb_canonical_conditions_fulfilment_evidence_id_fkey
                        FOREIGN KEY (fulfilment_evidence_id)
                        REFERENCES {quoted_schema}.evidence_records(id)
                );
                INSERT INTO {quoted_schema}.evidence_records (id)
                VALUES (701), (702);
                INSERT INTO {quoted_schema}.arb_condition_evidence_records (
                    id, organization_id, condition_id, decision_event_id,
                    review_cycle_id, review_item_id
                ) VALUES (702, 1, 2, 12, 22, 32);
                INSERT INTO {quoted_schema}.arb_decision_events (
                    id, organization_id, review_cycle_id, review_item_id, outcome
                ) VALUES
                    (11, 1, 21, 31, 'approved_with_conditions'),
                    (12, 1, 22, 32, 'approved_with_conditions'),
                    (13, 1, 23, 33, 'approved_with_conditions');
                INSERT INTO {quoted_schema}.arb_canonical_conditions (
                    id, organization_id, decision_event_id, review_cycle_id,
                    review_item_id, status, revision, fulfilled_at,
                    fulfilled_by_id, fulfilment_evidence_id, verified_at,
                    verified_by_id, waived_at, waived_by_id, waiver_reason,
                    waiver_expires_at, compensating_control
                ) VALUES
                    (1, 1, 11, 21, 31, 'fulfilled', NULL,
                     TIMESTAMPTZ '2026-01-01 10:00:00+00', 101, 701,
                     NULL, NULL, NULL, NULL, NULL, NULL, NULL),
                    (2, 1, 12, 22, 32, 'fulfilled', 3,
                     TIMESTAMPTZ '2026-01-02 10:00:00+00', 102, 702,
                     TIMESTAMPTZ '2026-01-02 10:00:00+00', 103,
                     NULL, NULL, NULL, NULL, NULL),
                    (3, 1, 13, 23, 33, 'waived', 5,
                     NULL, NULL, NULL, NULL, NULL,
                     TIMESTAMPTZ '2026-01-03 10:00:00+00', 104,
                     'Legacy waiver', TIMESTAMPTZ '2026-01-04 10:00:00+00',
                     'Legacy control');
                CREATE OR REPLACE FUNCTION {quoted_schema}.validate_legacy_condition_membership()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM {quoted_schema}.arb_decision_events AS decision
                        WHERE decision.id=NEW.decision_event_id
                          AND decision.outcome='approved_with_conditions'
                          AND decision.review_cycle_id=NEW.review_cycle_id
                          AND decision.review_item_id=NEW.review_item_id
                          AND decision.organization_id=NEW.organization_id
                    ) THEN
                        RAISE EXCEPTION 'legacy ARB condition membership is invalid';
                    END IF;
                    INSERT INTO {quoted_schema}.arb_condition_membership_audit (condition_id)
                    VALUES (NEW.id);
                    RETURN NEW;
                END $$;
                CREATE CONSTRAINT TRIGGER trg_arb_condition_membership
                AFTER UPDATE ON {quoted_schema}.arb_canonical_conditions
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                EXECUTE FUNCTION {quoted_schema}.validate_legacy_condition_membership();
            """)

        with engine.begin() as connection:
            connection.exec_driver_sql(_condition_reconcile_sql(quoted_schema))
            first = snapshot(connection)
            membership_audit = connection.exec_driver_sql(f"""
                SELECT condition_id, count(*)
                FROM {quoted_schema}.arb_condition_membership_audit
                GROUP BY condition_id
                ORDER BY condition_id
            """).fetchall()
            foreign_keys = connection.exec_driver_sql("""
                SELECT constraint_row.conname, target.relname
                FROM pg_constraint AS constraint_row
                JOIN pg_class AS source ON source.oid=constraint_row.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid=source.relnamespace
                JOIN pg_class AS target ON target.oid=constraint_row.confrelid
                WHERE namespace.nspname=%s
                  AND source.relname='arb_canonical_conditions'
                  AND constraint_row.contype='f'
                ORDER BY constraint_row.conname
            """, (schema,)).fetchall()

        first_by_id = {row["id"]: row for row in first}
        assert set(first_by_id) == {1, 2, 3}
        assert first_by_id[1]["status"] == "fulfilled"
        assert first_by_id[1]["revision"] == 1
        assert first_by_id[1]["fulfilment_evidence_id"] is None
        assert first_by_id[1]["verified_at"] == first_by_id[1]["fulfilled_at"]
        assert first_by_id[1]["verified_by_id"] == 101
        assert first_by_id[1]["legacy_lifecycle_provenance"] == {
            "classification": "pre_c3_fulfilment",
            "legacy_fulfilment_evidence_id": 701,
        }
        assert first_by_id[2]["status"] == "fulfilled"
        assert first_by_id[2]["revision"] == 3
        assert first_by_id[2]["fulfilment_evidence_id"] == 702
        assert first_by_id[2]["verified_by_id"] == 103
        assert first_by_id[2]["legacy_lifecycle_provenance"] is None
        assert first_by_id[3]["status"] == "waived"
        assert first_by_id[3]["revision"] == 5
        assert first_by_id[3]["legacy_lifecycle_provenance"] == {
            "classification": "pre_c3_waiver"
        }
        assert membership_audit == [(1, 3), (3, 1)]
        assert foreign_keys == [
            (
                "fk_arb_condition_fulfilment_evidence",
                "arb_condition_evidence_records",
            ),
            (
                "fk_arb_condition_submitted_evidence",
                "arb_condition_evidence_records",
            ),
        ]

        with engine.begin() as connection:
            connection.exec_driver_sql(_condition_reconcile_sql(quoted_schema))
            second = snapshot(connection)
            second_membership_audit = connection.exec_driver_sql(f"""
                SELECT condition_id, count(*)
                FROM {quoted_schema}.arb_condition_membership_audit
                GROUP BY condition_id
                ORDER BY condition_id
            """).fetchall()
        assert second == first
        # Reconciliation is data-idempotent, though its defensive NULL reset
        # deliberately updates the already-normalised legacy row and must still
        # drain the long-lived deferred membership trigger before its DDL.
        assert second_membership_audit == [(1, 4), (3, 1)]
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE")
