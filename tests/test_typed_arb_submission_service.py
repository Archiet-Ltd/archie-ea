"""Database contracts for typed ARB subjects and immutable review cycles."""

from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
import uuid

import pytest
import psycopg2
from psycopg2 import sql
from sqlalchemy.orm import configure_mappers

from tests._raw_psycopg2 import raw_psycopg2_connection

from app.models.architecture_review_board import ARBReviewItem
from app.models.mixins import TenantMixin


SUBJECT_TYPES = {
    "decision_brief",
    "solution",
    "architecture_model",
    "adr",
}


def _typed_models():
    arb_models = importlib.import_module("app.models.architecture_review_board")
    decision_models = importlib.import_module("app.models.transformation_decision")
    assert hasattr(arb_models, "ARBReviewCycle"), "ARBReviewCycle is not implemented"
    assert hasattr(
        decision_models, "ARBSubjectEvidenceSnapshot"
    ), "ARBSubjectEvidenceSnapshot is not implemented"
    return arb_models.ARBReviewCycle, decision_models.ARBSubjectEvidenceSnapshot


def _check_sql(model) -> str:
    return " ".join(
        str(constraint.sqltext)
        for constraint in model.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    )


def test_typed_cycle_schema_has_real_subject_and_adapter_evidence_foreign_keys():
    """Removing a real FK would let a polymorphic integer lose its subject."""
    cycle, snapshot = _typed_models()

    assert {
        "organization_id",
        "subject_type",
        "subject_id",
        "decision_brief_id",
        "solution_id",
        "architecture_model_id",
        "adr_id",
        "decision_brief_version_id",
        "solution_evidence_snapshot_id",
        "subject_evidence_snapshot_id",
        "review_number",
        "cycle_number",
        "predecessor_cycle_id",
        "status",
        "migration_gap_reason",
        "legacy_source_type",
        "legacy_source_id",
        "opened_at",
        "closed_at",
        "terminal_outcome",
    } <= set(cycle.__table__.columns.keys())
    assert {
        "organization_id",
        "subject_type",
        "subject_id",
        "architecture_model_id",
        "adr_id",
        "schema_version",
        "policy_version",
        "captured_by_id",
        "captured_at",
        "payload",
        "citations",
        "content_hash",
    } <= set(snapshot.__table__.columns.keys())

    expected_targets = {
        "decision_brief_id": "decision_briefs.id",
        "solution_id": "solutions.id",
        "architecture_model_id": "architecture_models.id",
        "adr_id": "architecture_decisions.id",
        "decision_brief_version_id": "decision_brief_versions.id",
        "solution_evidence_snapshot_id": "arb_submission_evidence_snapshots.id",
        "subject_evidence_snapshot_id": "arb_subject_evidence_snapshots.id",
    }
    for column_name, target in expected_targets.items():
        foreign_keys = cycle.__table__.columns[column_name].foreign_keys
        assert {foreign_key.target_fullname for foreign_key in foreign_keys} == {target}


def test_cycle_shape_history_chain_and_open_uniqueness_are_database_constraints():
    """Shape, historical gaps, monotonic chains, and open-cycle races need DB guards."""
    cycle, snapshot = _typed_models()
    cycle_checks = _check_sql(cycle)
    snapshot_checks = _check_sql(snapshot)

    assert SUBJECT_TYPES <= {
        subject_type for subject_type in SUBJECT_TYPES if subject_type in cycle_checks
    }
    assert "historical_unverified" in cycle_checks
    assert "migration_gap_reason IS NOT NULL" in cycle_checks
    assert "legacy_source_type IS NOT NULL" in cycle_checks
    assert "legacy_source_id IS NOT NULL" in cycle_checks
    assert "cycle_number = 1" in cycle_checks
    assert "predecessor_cycle_id IS NULL" in cycle_checks
    assert {"architecture_model", "adr"} <= {
        subject_type for subject_type in SUBJECT_TYPES if subject_type in snapshot_checks
    }

    indexes = {index.name: index for index in cycle.__table__.indexes}
    open_index = indexes["uq_arb_review_cycle_open_subject"]
    assert open_index.unique is True
    assert tuple(column.name for column in open_index.columns) == (
        "organization_id",
        "subject_type",
        "subject_id",
    )
    assert open_index.dialect_options["postgresql"]["where"] is not None

    assert any(
        constraint.__class__.__name__ == "UniqueConstraint"
        and tuple(column.name for column in constraint.columns)
        == ("organization_id", "subject_type", "subject_id", "cycle_number")
        for constraint in cycle.__table__.constraints
    )


def test_review_item_is_a_nullable_additive_one_to_one_cycle_projection():
    """Existing Solution reviews remain valid while typed reviews have one owner."""
    _cycle, _snapshot = _typed_models()
    columns = ARBReviewItem.__table__.columns

    assert {
        "subject_type",
        "subject_id",
        "decision_brief_id",
        "decision_brief_version_id",
        "solution_evidence_snapshot_id",
        "subject_evidence_snapshot_id",
        "review_cycle_id",
    } <= set(columns.keys())
    assert all(
        columns[name].nullable
        for name in (
            "subject_type",
            "subject_id",
            "decision_brief_id",
            "decision_brief_version_id",
            "solution_evidence_snapshot_id",
            "subject_evidence_snapshot_id",
            "review_cycle_id",
        )
    )
    assert columns["review_cycle_id"].unique is True
    review_checks = _check_sql(ARBReviewItem)
    assert all(subject_type in review_checks for subject_type in SUBJECT_TYPES)
    assert "review_cycle_id IS NULL" in review_checks


def test_typed_arb_models_are_tenant_scoped_in_the_full_mapper_registry():
    """A new ARB table without TenantMixin would silently bypass tenant filtering."""
    cycle, snapshot = _typed_models()
    from app.models.archimate_core import ArchitectureModel as exported_model
    from app.models.models import ArchitectureModel as canonical_model

    configure_mappers()
    assert exported_model is canonical_model
    assert issubclass(canonical_model, TenantMixin)
    assert issubclass(cycle, TenantMixin)
    assert issubclass(snapshot, TenantMixin)


def test_subject_snapshot_hash_covers_typed_membership_and_evidence():
    """Changing either evidence or typed identity must change the content hash."""
    _cycle, snapshot_type = _typed_models()
    snapshot = snapshot_type(
        organization_id=41,
        subject_type="architecture_model",
        subject_id=73,
        architecture_model_id=73,
        schema_version=1,
        policy_version="architecture-model-arb-r1",
        captured_at=datetime.now(timezone.utc),
        payload={"name": "Governed model", "version": "1.0"},
        citations=[],
    )
    snapshot.content_hash = snapshot.recompute_content_hash()
    original_hash = snapshot.content_hash

    snapshot.payload = {"name": "tampered", "version": "1.0"}
    assert snapshot.recompute_content_hash() != original_hash

    snapshot.payload = {"name": "Governed model", "version": "1.0"}
    snapshot.subject_id = 74
    snapshot.architecture_model_id = 74
    assert snapshot.recompute_content_hash() != original_hash


def test_subject_snapshot_hash_normalizes_equal_instants_to_utc():
    _cycle, snapshot_type = _typed_models()
    utc_instant = datetime(2026, 8, 27, 13, 30, tzinfo=timezone.utc)
    offset_instant = utc_instant.astimezone(timezone(timedelta(hours=5, minutes=30)))

    def snapshot(captured_at):
        return snapshot_type(
            organization_id=41,
            subject_type="adr",
            subject_id=73,
            adr_id=73,
            schema_version=1,
            policy_version="adr-arb-r2",
            captured_at=captured_at,
            payload={"decision": "Use the canonical platform"},
            citations=[],
        )

    assert snapshot(utc_instant).recompute_content_hash() == snapshot(
        offset_instant
    ).recompute_content_hash()

def _install_typed_schema(app):
    from app import db
    from app.commands.reconcile_schema import _reconcile

    with app.app_context():
        _added, failed, missing, blocking = _reconcile(dry_run=False)
        assert failed == []
        assert "arb_review_cycles" not in missing
        assert "arb_subject_evidence_snapshots" not in missing
        assert not [item for item in blocking if item.startswith("arb_")]
        return raw_psycopg2_connection(db.engine)


def _seed_org_user_model(cursor, label):
    suffix = uuid.uuid4().hex[:12]
    safe_label = label[:20]
    cursor.execute(
        "INSERT INTO organizations (name, slug) VALUES (%s, %s) RETURNING id",
        (f"Typed ARB {label} {suffix}", f"typed-arb-{safe_label}-{suffix}"),
    )
    organization_id = cursor.fetchone()[0]
    cursor.execute(
        "INSERT INTO users (organization_id, email, enterprise_role) "
        "VALUES (%s, %s, 'enterprise_architect') RETURNING id",
        (organization_id, f"typed-arb-{safe_label}-{suffix}@example.test"),
    )
    user_id = cursor.fetchone()[0]
    cursor.execute(
        "INSERT INTO architecture_models (organization_id, name, version) "
        "VALUES (%s, %s, '1.0') RETURNING id",
        (organization_id, f"Typed model {suffix}"),
    )
    model_id = cursor.fetchone()[0]
    return organization_id, user_id, model_id


def _insert_model_snapshot(cursor, organization_id, model_id):
    cursor.execute(
        """
        INSERT INTO arb_subject_evidence_snapshots (
            organization_id, subject_type, subject_id, architecture_model_id,
            schema_version, policy_version, captured_at, payload, citations,
            content_hash
        ) VALUES (
            %s, 'architecture_model', %s, %s, 1,
            'architecture-model-arb-r1', clock_timestamp(), '{}'::json,
            '[]'::json, %s
        ) RETURNING id
        """,
        (organization_id, model_id, model_id, "a" * 64),
    )
    return cursor.fetchone()[0]


def _insert_model_cycle(
    cursor,
    *,
    organization_id,
    model_id,
    snapshot_id,
    cycle_number=1,
    predecessor_cycle_id=None,
    closed=False,
):
    cursor.execute(
        """
        INSERT INTO arb_review_cycles (
            organization_id, subject_type, subject_id, architecture_model_id,
            subject_evidence_snapshot_id, review_number, cycle_number,
            predecessor_cycle_id, status, opened_at, closed_at, terminal_outcome
        ) VALUES (
            %s, 'architecture_model', %s, %s, %s, %s, %s, %s,
            %s, clock_timestamp(),
            CASE WHEN %s THEN clock_timestamp() ELSE NULL END,
            CASE WHEN %s THEN 'returned_for_evidence' ELSE NULL END
        ) RETURNING id
        """,
        (
            organization_id,
            model_id,
            model_id,
            snapshot_id,
            f"CYCLE-{uuid.uuid4().hex[:20]}",
            cycle_number,
            predecessor_cycle_id,
            "returned_for_evidence" if closed else "submitted",
            closed,
            closed,
        ),
    )
    return cursor.fetchone()[0]


def _insert_model_review(
    cursor,
    *,
    organization_id,
    user_id,
    model_id,
    snapshot_id,
    cycle_id,
    status="submitted",
    decision=None,
):
    cursor.execute(
        "SELECT review_number FROM arb_review_cycles WHERE id = %s",
        (cycle_id,),
    )
    review_number = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO arb_review_items (
            organization_id, review_number, title, review_type, submitter_id,
            subject_type, subject_id, architecture_model_id,
            subject_evidence_snapshot_id, review_cycle_id, status, decision
        ) VALUES (
            %s, %s, 'Typed architecture review', 'architecture_change', %s,
            'architecture_model', %s, %s, %s, %s, %s, %s
        ) RETURNING id
        """,
        (
            organization_id,
            review_number,
            user_id,
            model_id,
            model_id,
            snapshot_id,
            cycle_id,
            status,
            decision,
        ),
    )
    return cursor.fetchone()[0]


def _seed_decision_brief(cursor, organization_id, user_id, label):
    suffix = uuid.uuid4().hex[:10]
    cursor.execute(
        "INSERT INTO strategic_initiatives (organization_id, name, record_kind) "
        "VALUES (%s, %s, 'transformation_programme') RETURNING id",
        (organization_id, f"Typed programme {label} {suffix}"),
    )
    programme_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO programme_workstreams (
            organization_id, programme_id, workstream_type, objective,
            scope_expression, lifecycle_stage, revision
        ) VALUES (
            %s, %s, 'application_rationalisation', %s, '{}'::json,
            'decision_ready', 1
        ) RETURNING id
        """,
        (organization_id, programme_id, f"Typed objective {suffix}"),
    )
    workstream_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO transformation_options (
            organization_id, workstream_id, title, action_type, description,
            assumptions, dependencies, impacts, risks,
            affected_capability_ids, affected_value_stream_ids, revision
        ) VALUES (
            %s, %s, %s, 'invest', 'Typed option', '[]'::json, '[]'::json,
            '[]'::json, '[]'::json, '[]'::json, '[]'::json, 1
        ) RETURNING id
        """,
        (organization_id, workstream_id, f"Typed option {suffix}"),
    )
    option_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO transformation_option_versions (
            organization_id, option_id, workstream_id, version, source_revision,
            content_json, cost_min, cost_max, benefit_min, benefit_max,
            risk_min, risk_max, currency, technology_required, captured_by_id,
            content_hash
        ) VALUES (
            %s, %s, %s, 1, 1, '{}'::json, 0, 0, 0, 0, 0, 0, 'GBP',
            false, %s, %s
        ) RETURNING id
        """,
        (organization_id, option_id, workstream_id, user_id, "b" * 64),
    )
    option_version_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO decision_briefs (
            organization_id, workstream_id, title, recommendation_option_id,
            decision_authority_id, unknown_codes, conflicts, expected_impacts,
            status, revision
        ) VALUES (
            %s, %s, %s, %s, %s, '[]'::json, '[]'::json, '[]'::json,
            'frozen', 1
        ) RETURNING id
        """,
        (
            organization_id,
            workstream_id,
            f"Typed brief {suffix}",
            option_id,
            user_id,
        ),
    )
    brief_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO decision_brief_versions (
            organization_id, brief_id, workstream_id, version, source_revision,
            frozen_payload, recommendation_option_version_id,
            option_version_ids, cited_evidence_ids, outcome_ids, measure_ids,
            policy_version, created_by_id, content_hash, canonical_document,
            submitted_by_id, submitter_authorized, decision_authority_id,
            human_reviewed_ai, blockers_cleared, unknowns_acknowledged
        ) VALUES (
            %s, %s, %s, 1, 1, '{}'::json, %s, %s::json, '[]'::json,
            '[]'::json, '[]'::json, 'typed-arb-r1', %s, %s, '{}', %s,
            true, %s, true, true, true
        ) RETURNING id
        """,
        (
            organization_id,
            brief_id,
            workstream_id,
            option_version_id,
            f"[{option_version_id}]",
            user_id,
            "c" * 64,
            user_id,
            user_id,
        ),
    )
    return {
        "subject_type": "decision_brief",
        "subject_id": brief_id,
        "organization_id": organization_id,
        "evidence_id": cursor.fetchone()[0],
    }


def _seed_subject_material(cursor, subject_type, organization_id, user_id, label):
    suffix = uuid.uuid4().hex[:10]
    if subject_type == "decision_brief":
        return _seed_decision_brief(cursor, organization_id, user_id, label)
    if subject_type == "solution":
        cursor.execute(
            "INSERT INTO solutions (organization_id, name) VALUES (%s, %s) RETURNING id",
            (organization_id, f"Typed solution {label} {suffix}"),
        )
    elif subject_type == "architecture_model":
        cursor.execute(
            "INSERT INTO architecture_models (organization_id, name, version) "
            "VALUES (%s, %s, '1.0') RETURNING id",
            (organization_id, f"Typed model {label} {suffix}"),
        )
    else:
        cursor.execute(
            """
            INSERT INTO architecture_decisions (
                organization_id, decision_id, title, status, context,
                decision, rationale, consequences, enterprise_level
            ) VALUES (
                %s, %s, %s, 'proposed', 'Typed context', 'Typed decision',
                'Typed rationale', 'Typed consequences', true
            ) RETURNING id
            """,
            (organization_id, f"AD-{uuid.uuid4().hex[:10]}", f"Typed ADR {label} {suffix}"),
        )
    return {
        "subject_type": subject_type,
        "subject_id": cursor.fetchone()[0],
        "organization_id": organization_id,
        "evidence_id": None,
    }


def _insert_typed_graph(
    cursor,
    *,
    organization_id,
    user_id,
    subject,
    evidence_subject=None,
    cycle_number=1,
    predecessor_cycle_id=None,
    cycle_status="submitted",
    closed_at=None,
    terminal_outcome=None,
    review_status=None,
    review_decision=None,
    cycle_review_number=None,
    item_review_number=None,
):
    evidence_subject = evidence_subject or subject
    review_status = review_status or cycle_status
    cycle_review_number = cycle_review_number or f"CYCLE-{uuid.uuid4().hex[:20]}"
    item_review_number = item_review_number or cycle_review_number
    subject_type = subject["subject_type"]
    subject_id = subject["subject_id"]
    subject_columns = {
        "decision_brief": "decision_brief_id",
        "solution": "solution_id",
        "architecture_model": "architecture_model_id",
        "adr": "adr_id",
    }
    subject_column = subject_columns[subject_type]
    evidence_column = {
        "decision_brief": "decision_brief_version_id",
        "solution": "solution_evidence_snapshot_id",
        "architecture_model": "subject_evidence_snapshot_id",
        "adr": "subject_evidence_snapshot_id",
    }[subject_type]

    if subject_type == "solution":
        cursor.execute(
            f"""
            INSERT INTO arb_review_items (
                organization_id, review_number, title, review_type, submitter_id,
                {subject_column}, status
            ) VALUES (%s, %s, 'Typed review', 'solution_design', %s, %s, %s)
            RETURNING id
            """,
            (organization_id, item_review_number, user_id, subject_id, review_status),
        )
        review_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO arb_submission_evidence_snapshots (
                organization_id, review_item_id, solution_id, schema_version,
                captured_at, checks, artifacts, governance_result,
                request_assertions, content_hash
            ) VALUES (
                %s, %s, %s, 1, clock_timestamp(), '{}'::json, '{}'::json,
                '{}'::json, '{}'::json, %s
            ) RETURNING id
            """,
            (
                evidence_subject["organization_id"],
                review_id,
                evidence_subject["subject_id"],
                "d" * 64,
            ),
        )
        evidence_id = cursor.fetchone()[0]
    elif subject_type == "decision_brief":
        evidence_id = evidence_subject["evidence_id"]
        review_id = None
    else:
        evidence_id = _insert_subject_snapshot(cursor, evidence_subject)
        review_id = None

    cursor.execute(
        f"""
        INSERT INTO arb_review_cycles (
            organization_id, subject_type, subject_id, {subject_column},
            {evidence_column}, review_number, cycle_number,
            predecessor_cycle_id, status, opened_at, closed_at, terminal_outcome
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp(),
            %s, %s
        ) RETURNING id
        """,
        (
            organization_id,
            subject_type,
            subject_id,
            subject_id,
            evidence_id,
            cycle_review_number,
            cycle_number,
            predecessor_cycle_id,
            cycle_status,
            closed_at,
            terminal_outcome,
        ),
    )
    cycle_id = cursor.fetchone()[0]
    if review_id is None:
        cursor.execute(
            f"""
            INSERT INTO arb_review_items (
                organization_id, review_number, title, review_type, submitter_id,
                subject_type, subject_id, {subject_column}, {evidence_column},
                review_cycle_id, status, decision
            ) VALUES (
                %s, %s, 'Typed review', 'architecture_change', %s, %s, %s,
                %s, %s, %s, %s, %s
            ) RETURNING id
            """,
            (
                organization_id,
                item_review_number,
                user_id,
                subject_type,
                subject_id,
                subject_id,
                evidence_id,
                cycle_id,
                review_status,
                review_decision,
            ),
        )
        review_id = cursor.fetchone()[0]
    else:
        cursor.execute(
            f"""
            UPDATE arb_review_items
            SET subject_type = %s, subject_id = %s, {evidence_column} = %s,
                review_cycle_id = %s, decision = %s
            WHERE id = %s
            """,
            (subject_type, subject_id, evidence_id, cycle_id, review_decision, review_id),
        )
    return cycle_id, review_id, evidence_id


def _insert_subject_snapshot(cursor, subject):
    subject_type = subject["subject_type"]
    subject_column = (
        "architecture_model_id" if subject_type == "architecture_model" else "adr_id"
    )
    cursor.execute(
        f"""
        INSERT INTO arb_subject_evidence_snapshots (
            organization_id, subject_type, subject_id, {subject_column},
            schema_version, policy_version, captured_at, payload, citations,
            content_hash
        ) VALUES (
            %s, %s, %s, %s, 1, 'typed-arb-r1', clock_timestamp(),
            '{{}}'::json, '[]'::json, %s
        ) RETURNING id
        """,
        (
            subject["organization_id"],
            subject_type,
            subject["subject_id"],
            subject["subject_id"],
            "e" * 64,
        ),
    )
    return cursor.fetchone()[0]


def test_reconcile_installs_typed_arb_constraints_idempotently(app, _schema):
    """Existing databases receive tables, checks, indexes and enabled triggers."""
    arb_models = importlib.import_module("app.models.architecture_review_board")
    assert hasattr(arb_models, "ensure_arb_cycle_constraints")
    assert hasattr(arb_models, "inspect_arb_cycle_constraints")

    raw = _install_typed_schema(app)
    raw.close()
    raw = _install_typed_schema(app)
    raw.close()
    from app import db

    with app.app_context(), db.engine.connect() as connection:
        assert arb_models.inspect_arb_cycle_constraints(connection) == []


def test_direct_snapshot_commit_rejects_cross_tenant_subject(app, _schema):
    """A forged FK from another tenant must fail at the database commit boundary."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_a, _user_a, _model_a = _seed_org_user_model(cursor, "snapshot-a")
        _org_b, _user_b, model_b = _seed_org_user_model(cursor, "snapshot-b")
        _insert_model_snapshot(cursor, org_a, model_b)
        with pytest.raises(psycopg2.Error, match="snapshot subject is outside its tenant"):
            raw.commit()
    finally:
        raw.rollback()
        raw.close()


def test_direct_cycle_commit_requires_adapter_snapshot_membership_and_review(app, _schema):
    """A cycle cannot pin another subject's evidence even with valid foreign keys."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, model_a = _seed_org_user_model(cursor, "cycle-membership")
        cursor.execute(
            "INSERT INTO architecture_models (organization_id, name) "
            "VALUES (%s, 'Other governed model') RETURNING id",
            (org_id,),
        )
        model_b = cursor.fetchone()[0]
        snapshot_b = _insert_model_snapshot(cursor, org_id, model_b)
        cycle_id = _insert_model_cycle(
            cursor,
            organization_id=org_id,
            model_id=model_a,
            snapshot_id=snapshot_b,
        )
        _insert_model_review(
            cursor,
            organization_id=org_id,
            user_id=user_id,
            model_id=model_a,
            snapshot_id=snapshot_b,
            cycle_id=cycle_id,
        )
        with pytest.raises(psycopg2.Error, match="cycle snapshot does not belong"):
            raw.commit()
    finally:
        raw.rollback()
        raw.close()


def test_direct_cycle_commit_requires_exactly_one_review_projection(app, _schema):
    """A cycle without its sole canonical ARBReviewItem cannot commit."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, _user_id, model_id = _seed_org_user_model(cursor, "cycle-review")
        snapshot_id = _insert_model_snapshot(cursor, org_id, model_id)
        _insert_model_cycle(
            cursor,
            organization_id=org_id,
            model_id=model_id,
            snapshot_id=snapshot_id,
        )
        with pytest.raises(psycopg2.Error, match="cycle review projection is missing"):
            raw.commit()
    finally:
        raw.rollback()
        raw.close()


def test_direct_cycle_commit_requires_review_status_projection(app, _schema):
    """The cycle cannot become a parallel workflow-status authority."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, model_id = _seed_org_user_model(cursor, "cycle-status")
        snapshot_id = _insert_model_snapshot(cursor, org_id, model_id)
        cycle_id = _insert_model_cycle(
            cursor,
            organization_id=org_id,
            model_id=model_id,
            snapshot_id=snapshot_id,
        )
        review_id = _insert_model_review(
            cursor,
            organization_id=org_id,
            user_id=user_id,
            model_id=model_id,
            snapshot_id=snapshot_id,
            cycle_id=cycle_id,
        )
        cursor.execute(
            "UPDATE arb_review_items SET status = 'under_review' WHERE id = %s",
            (review_id,),
        )
        with pytest.raises(psycopg2.Error, match="cycle review projection"):
            raw.commit()
    finally:
        raw.rollback()
        raw.close()


def test_direct_cycle_commit_rejects_non_monotonic_predecessor(app, _schema):
    """A successor must be the next cycle for the same closed typed subject."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, model_id = _seed_org_user_model(cursor, "cycle-chain")
        snapshot_id = _insert_model_snapshot(cursor, org_id, model_id)
        first_cycle = _insert_model_cycle(
            cursor,
            organization_id=org_id,
            model_id=model_id,
            snapshot_id=snapshot_id,
            closed=True,
        )
        _insert_model_review(
            cursor,
            organization_id=org_id,
            user_id=user_id,
            model_id=model_id,
            snapshot_id=snapshot_id,
            cycle_id=first_cycle,
            status="returned_for_evidence",
            decision="returned_for_evidence",
        )
        third_cycle = _insert_model_cycle(
            cursor,
            organization_id=org_id,
            model_id=model_id,
            snapshot_id=snapshot_id,
            cycle_number=3,
            predecessor_cycle_id=first_cycle,
        )
        _insert_model_review(
            cursor,
            organization_id=org_id,
            user_id=user_id,
            model_id=model_id,
            snapshot_id=snapshot_id,
            cycle_id=third_cycle,
        )
        with pytest.raises(psycopg2.Error, match="cycle predecessor is not monotonic"):
            raw.commit()
    finally:
        raw.rollback()
        raw.close()


def test_database_rejects_two_open_cycles_for_one_typed_subject(app, _schema):
    """The partial unique index closes the concurrent-open race."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, model_id = _seed_org_user_model(cursor, "cycle-open")
        snapshot_id = _insert_model_snapshot(cursor, org_id, model_id)
        first_cycle = _insert_model_cycle(
            cursor,
            organization_id=org_id,
            model_id=model_id,
            snapshot_id=snapshot_id,
        )
        _insert_model_review(
            cursor,
            organization_id=org_id,
            user_id=user_id,
            model_id=model_id,
            snapshot_id=snapshot_id,
            cycle_id=first_cycle,
        )
        with pytest.raises(psycopg2.Error):
            _insert_model_cycle(
                cursor,
                organization_id=org_id,
                model_id=model_id,
                snapshot_id=snapshot_id,
                cycle_number=2,
                predecessor_cycle_id=first_cycle,
            )
    finally:
        raw.rollback()
        raw.close()


def test_historical_unverified_cycle_validates_without_fabricated_snapshot(app, _schema):
    """Legacy model/ADR/Solution history records its evidence gap, never fake evidence."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, model_id = _seed_org_user_model(cursor, "historical-gap")
        review_number = f"HIST-{uuid.uuid4().hex[:20]}"
        cursor.execute(
            """
            INSERT INTO arb_review_cycles (
                organization_id, subject_type, subject_id, architecture_model_id,
                review_number, cycle_number, status, migration_gap_reason,
                legacy_source_type, legacy_source_id, opened_at, closed_at,
                terminal_outcome
            ) VALUES (
                %s, 'architecture_model', %s, %s, %s, 1,
                'historical_unverified', 'legacy review has no provable snapshot',
                'arb_review_item', 9127, clock_timestamp(), clock_timestamp(),
                'historical_unverified'
            ) RETURNING id
            """,
            (org_id, model_id, model_id, review_number),
        )
        cycle_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO arb_review_items (
                organization_id, review_number, title, review_type, submitter_id,
                subject_type, subject_id, architecture_model_id, review_cycle_id,
                status
            ) VALUES (
                %s, %s, 'Historical architecture review', 'architecture_change',
                %s, 'architecture_model', %s, %s, %s,
                'historical_unverified'
            )
            """,
            (
                org_id,
                review_number,
                user_id,
                model_id,
                model_id,
                cycle_id,
            ),
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        raw.rollback()
        raw.close()


def test_historical_solution_cycle_also_validates_without_fabricated_snapshot(app, _schema):
    """The legacy Solution adapter receives the same honest gap treatment."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, _model_id = _seed_org_user_model(cursor, "historical-solution")
        cursor.execute(
            "INSERT INTO solutions (organization_id, name) VALUES (%s, %s) RETURNING id",
            (org_id, f"Historical solution {uuid.uuid4().hex[:10]}"),
        )
        solution_id = cursor.fetchone()[0]
        review_number = f"HIST-{uuid.uuid4().hex[:20]}"
        cursor.execute(
            """
            INSERT INTO arb_review_cycles (
                organization_id, subject_type, subject_id, solution_id,
                review_number, cycle_number, status, migration_gap_reason,
                legacy_source_type, legacy_source_id, opened_at, closed_at,
                terminal_outcome
            ) VALUES (
                %s, 'solution', %s, %s, %s, 1, 'historical_unverified',
                'legacy review has no provable snapshot', 'arb_review_item',
                9128, clock_timestamp(), clock_timestamp(),
                'historical_unverified'
            ) RETURNING id
            """,
            (org_id, solution_id, solution_id, review_number),
        )
        cycle_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO arb_review_items (
                organization_id, review_number, title, review_type, submitter_id,
                subject_type, subject_id, solution_id, review_cycle_id, status
            ) VALUES (
                %s, %s, 'Historical Solution review', 'solution_design', %s,
                'solution', %s, %s, %s, 'historical_unverified'
            )
            """,
            (
                org_id,
                review_number,
                user_id,
                solution_id,
                solution_id,
                cycle_id,
            ),
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        raw.rollback()
        raw.close()


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE arb_subject_evidence_snapshots SET payload = '{}'::json WHERE id = %s",
        "DELETE FROM arb_subject_evidence_snapshots WHERE id = %s",
        "UPDATE arb_review_cycles SET subject_id = subject_id + 1 WHERE id = %s",
        "DELETE FROM arb_review_cycles WHERE id = %s",
        "UPDATE arb_review_items SET subject_id = subject_id + 1 WHERE id = %s",
        "DELETE FROM arb_review_items WHERE id = %s",
    ),
)
def test_direct_sql_cannot_rewrite_typed_snapshot_or_history(app, _schema, statement):
    """Snapshot and typed identity/history are append-only outside the ORM too."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, model_id = _seed_org_user_model(cursor, "cycle-history")
        snapshot_id = _insert_model_snapshot(cursor, org_id, model_id)
        cycle_id = _insert_model_cycle(
            cursor,
            organization_id=org_id,
            model_id=model_id,
            snapshot_id=snapshot_id,
        )
        review_id = _insert_model_review(
            cursor,
            organization_id=org_id,
            user_id=user_id,
            model_id=model_id,
            snapshot_id=snapshot_id,
            cycle_id=cycle_id,
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        target_id = (
            snapshot_id
            if "arb_subject_evidence_snapshots" in statement
            else cycle_id
            if "arb_review_cycles" in statement
            else review_id
        )
        with pytest.raises(psycopg2.Error, match="append-only|immutable"):
            cursor.execute(statement, (target_id,))
    finally:
        raw.rollback()
        raw.close()


@pytest.mark.parametrize("subject_type", sorted(SUBJECT_TYPES))
def test_direct_commit_accepts_each_verified_typed_subject(app, _schema, subject_type):
    """Every adapter shape has one valid direct-commit proof."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, _model_id = _seed_org_user_model(cursor, f"valid-{subject_type}")
        subject = _seed_subject_material(cursor, subject_type, org_id, user_id, "valid")
        _insert_typed_graph(
            cursor,
            organization_id=org_id,
            user_id=user_id,
            subject=subject,
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        raw.rollback()
        raw.close()


@pytest.mark.parametrize("subject_type", sorted(SUBJECT_TYPES))
@pytest.mark.parametrize("invalidity", ("cross_tenant", "wrong_evidence"))
def test_direct_commit_rejects_all_typed_membership_mismatches(
    app, _schema, subject_type, invalidity
):
    """All adapters repeat tenant and pinned-evidence membership at commit."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_a, user_a, _model_a = _seed_org_user_model(
            cursor, f"matrix-a-{subject_type}-{invalidity}"
        )
        if invalidity == "cross_tenant":
            org_b, user_b, _model_b = _seed_org_user_model(
                cursor, f"matrix-b-{subject_type}"
            )
            subject = _seed_subject_material(cursor, subject_type, org_b, user_b, "foreign")
            evidence_subject = subject
        else:
            subject = _seed_subject_material(cursor, subject_type, org_a, user_a, "subject")
            evidence_subject = _seed_subject_material(
                cursor, subject_type, org_a, user_a, "wrong-evidence"
            )
        _insert_typed_graph(
            cursor,
            organization_id=org_a,
            user_id=user_a,
            subject=subject,
            evidence_subject=evidence_subject,
        )
        with pytest.raises(psycopg2.Error, match="outside its tenant|does not belong"):
            raw.commit()
    finally:
        raw.rollback()
        raw.close()


@pytest.mark.parametrize("subject_type", sorted(SUBJECT_TYPES))
def test_parent_subject_cannot_be_retenanted_away_from_committed_cycle(
    app, _schema, subject_type
):
    """Changing a parent tenant must not silently invalidate committed typed history."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_a, user_a, _model_a = _seed_org_user_model(cursor, f"retenant-{subject_type}")
        org_b, _user_b, _model_b = _seed_org_user_model(cursor, f"retenant-target-{subject_type}")
        subject = _seed_subject_material(cursor, subject_type, org_a, user_a, "retenant")
        _insert_typed_graph(
            cursor,
            organization_id=org_a,
            user_id=user_a,
            subject=subject,
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        table = {
            "decision_brief": "decision_briefs",
            "solution": "solutions",
            "architecture_model": "architecture_models",
            "adr": "architecture_decisions",
        }[subject_type]
        with pytest.raises(psycopg2.Error, match="tenant.*typed ARB|typed ARB.*tenant"):
            cursor.execute(
                f"UPDATE {table} SET organization_id = %s WHERE id = %s",
                (org_b, subject["subject_id"]),
            )
    finally:
        raw.rollback()
        raw.close()


@pytest.mark.parametrize(
    ("terminal_outcome", "review_decision"),
    (("approved", None), ("historical_unverified", "approved")),
)
def test_historical_unverified_cannot_carry_canonical_decision(
    app, _schema, terminal_outcome, review_decision
):
    """Unverified imports cannot project an approval or other verified decision."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, _model_id = _seed_org_user_model(cursor, "historical-decision")
        subject = _seed_subject_material(cursor, "adr", org_id, user_id, "historical")
        with pytest.raises(psycopg2.Error):
            _insert_historical_graph(
                cursor,
                organization_id=org_id,
                user_id=user_id,
                subject=subject,
                terminal_outcome=terminal_outcome,
                review_decision=review_decision,
            )
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        raw.rollback()
        raw.close()


def _insert_historical_graph(
    cursor,
    *,
    organization_id,
    user_id,
    subject,
    terminal_outcome="historical_unverified",
    review_decision=None,
):
    subject_type = subject["subject_type"]
    subject_column = {
        "solution": "solution_id",
        "architecture_model": "architecture_model_id",
        "adr": "adr_id",
    }[subject_type]
    review_number = f"HIST-{uuid.uuid4().hex[:20]}"
    cursor.execute(
        f"""
        INSERT INTO arb_review_cycles (
            organization_id, subject_type, subject_id, {subject_column},
            review_number, cycle_number, status, migration_gap_reason,
            legacy_source_type, legacy_source_id, opened_at, closed_at,
            terminal_outcome
        ) VALUES (
            %s, %s, %s, %s, %s, 1, 'historical_unverified',
            'legacy evidence unavailable', 'arb_review_item', 9911,
            clock_timestamp(), clock_timestamp(), %s
        ) RETURNING id
        """,
        (
            organization_id,
            subject_type,
            subject["subject_id"],
            subject["subject_id"],
            review_number,
            terminal_outcome,
        ),
    )
    cycle_id = cursor.fetchone()[0]
    cursor.execute(
        f"""
        INSERT INTO arb_review_items (
            organization_id, review_number, title, review_type, submitter_id,
            subject_type, subject_id, {subject_column}, review_cycle_id,
            status, decision
        ) VALUES (
            %s, %s, 'Historical typed review', 'architecture_change', %s,
            %s, %s, %s, %s, 'historical_unverified', %s
        ) RETURNING id
        """,
        (
            organization_id,
            review_number,
            user_id,
            subject_type,
            subject["subject_id"],
            subject["subject_id"],
            cycle_id,
            review_decision,
        ),
    )
    return cycle_id, cursor.fetchone()[0]


def test_historical_unverified_adr_validates_without_fabricated_snapshot(app, _schema):
    """ADR migration receives the same explicit, non-decision evidence-gap shape."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, _model_id = _seed_org_user_model(cursor, "historical-adr")
        subject = _seed_subject_material(cursor, "adr", org_id, user_id, "historical")
        _insert_historical_graph(
            cursor, organization_id=org_id, user_id=user_id, subject=subject
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        raw.rollback()
        raw.close()


@pytest.mark.parametrize(
    "mutation",
    (
        "title = 'Forged approval dossier'",
        "decision = 'approved'",
        "governance_checklist = '{}'::json",
    ),
)
def test_linked_historical_review_rejects_every_post_insert_mutation(
    app, _schema, mutation
):
    """Even non-projection edits cannot turn unverified history into governance truth."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, _model_id = _seed_org_user_model(cursor, "historical-mutation")
        subject = _seed_subject_material(cursor, "adr", org_id, user_id, "historical")
        _cycle_id, review_id = _insert_historical_graph(
            cursor, organization_id=org_id, user_id=user_id, subject=subject
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with pytest.raises(psycopg2.Error, match="historical.*immutable"):
            cursor.execute(
                f"UPDATE arb_review_items SET {mutation} WHERE id = %s",
                (review_id,),
            )
    finally:
        raw.rollback()
        raw.close()


def test_submitted_cycle_cannot_be_falsely_closed_to_open_successor(app, _schema):
    """A closed timestamp alone cannot make an open-state cycle a valid predecessor."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, _model_id = _seed_org_user_model(cursor, "false-close")
        subject = _seed_subject_material(cursor, "adr", org_id, user_id, "false-close")
        with pytest.raises(psycopg2.Error, match="ck_arb_review_cycle_shape"):
            _insert_typed_graph(
                cursor,
                organization_id=org_id,
                user_id=user_id,
                subject=subject,
                closed_at=datetime.now(timezone.utc),
                terminal_outcome="submitted",
                review_decision="submitted",
            )
    finally:
        raw.rollback()
        raw.close()


def test_terminal_cycle_and_verified_successor_commit_with_exact_projection(app, _schema):
    """A real terminal decision, unlike false closure, is a valid successor boundary."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, _model_id = _seed_org_user_model(cursor, "terminal-successor")
        subject = _seed_subject_material(cursor, "adr", org_id, user_id, "terminal")
        first_cycle, _review_id, _snapshot_id = _insert_typed_graph(
            cursor,
            organization_id=org_id,
            user_id=user_id,
            subject=subject,
            cycle_status="rejected",
            closed_at=datetime.now(timezone.utc),
            terminal_outcome="rejected",
            review_decision="rejected",
        )
        _insert_typed_graph(
            cursor,
            organization_id=org_id,
            user_id=user_id,
            subject=subject,
            cycle_number=2,
            predecessor_cycle_id=first_cycle,
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        raw.rollback()
        raw.close()


def test_cycle_and_review_numbers_must_match(app, _schema):
    """The cycle cannot carry a second review identity."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, _model_id = _seed_org_user_model(cursor, "review-number")
        subject = _seed_subject_material(cursor, "adr", org_id, user_id, "number")
        _insert_typed_graph(
            cursor,
            organization_id=org_id,
            user_id=user_id,
            subject=subject,
            cycle_review_number=f"CYCLE-{uuid.uuid4().hex[:16]}",
            item_review_number=f"REVIEW-{uuid.uuid4().hex[:16]}",
        )
        with pytest.raises(psycopg2.Error, match="review projection"):
            raw.commit()
    finally:
        raw.rollback()
        raw.close()


@pytest.mark.parametrize(
    "damage_sql",
    (
        "ALTER TABLE arb_review_cycles DROP CONSTRAINT ck_arb_review_cycle_shape; "
        "ALTER TABLE arb_review_cycles ADD CONSTRAINT ck_arb_review_cycle_shape CHECK (true)",
        # A fresh create_all schema exposes this guard as a UNIQUE constraint, while
        # the reconciliation repair exposes it as a standalone unique index. Remove
        # either catalog form before installing the malformed name-squatting index.
        "ALTER TABLE arb_review_cycles DROP CONSTRAINT IF EXISTS uq_arb_review_cycle_review_number; "
        "DROP INDEX IF EXISTS uq_arb_review_cycle_review_number; "
        "CREATE INDEX uq_arb_review_cycle_review_number ON arb_review_cycles (status)",
        "DROP TRIGGER trg_arb_cycle_membership ON arb_review_cycles; "
        "CREATE TRIGGER trg_arb_cycle_membership BEFORE INSERT ON arb_review_cycles "
        "FOR EACH ROW EXECUTE FUNCTION archie_guard_arb_cycle_history()",
        "ALTER TABLE arb_review_items DROP CONSTRAINT fk_arb_review_item_decision_brief_version; "
        "ALTER TABLE arb_review_items ADD CONSTRAINT fk_arb_review_item_decision_brief_version "
        "FOREIGN KEY (decision_brief_version_id) REFERENCES decision_briefs(id) NOT VALID",
        "ALTER TABLE arb_review_cycles DISABLE TRIGGER trg_arb_cycle_membership",
        "DROP TRIGGER trg_arb_review_cycle_membership ON arb_review_items",
        "CREATE OR REPLACE FUNCTION archie_validate_arb_cycle_membership() "
        "RETURNS trigger LANGUAGE plpgsql AS 'BEGIN\nRETURN NEW;\nEND'",
    ),
)
def test_reconcile_repairs_missing_disabled_and_malformed_typed_guards(
    app, _schema, damage_sql
):
    """A guard's name alone is not proof that existing-database enforcement works."""
    _raw = _install_typed_schema(app)
    _raw.close()
    from app import db
    from app.models.architecture_review_board import (
        ensure_arb_cycle_constraints,
        inspect_arb_cycle_constraints,
    )

    with app.app_context(), db.engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in damage_sql.split("; "):
                connection.exec_driver_sql(statement)
            assert inspect_arb_cycle_constraints(connection)
            ensure_arb_cycle_constraints(connection)
            assert inspect_arb_cycle_constraints(connection) == []
        finally:
            transaction.rollback()


def test_historical_unverified_requires_non_null_terminal_outcome(app, _schema):
    """SQL NULL must not bypass the explicit unverified terminal marker."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, _model_id = _seed_org_user_model(cursor, "historical-null")
        subject = _seed_subject_material(cursor, "adr", org_id, user_id, "historical")
        with pytest.raises(psycopg2.Error, match="ck_arb_review_cycle_shape"):
            _insert_historical_graph(
                cursor,
                organization_id=org_id,
                user_id=user_id,
                subject=subject,
                terminal_outcome=None,
            )
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        raw.rollback()
        raw.close()


def test_terminal_cycle_requires_non_null_canonical_outcome(app, _schema):
    """A terminal status without an outcome must fail instead of evaluating UNKNOWN."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, _model_id = _seed_org_user_model(cursor, "terminal-null-outcome")
        subject = _seed_subject_material(cursor, "adr", org_id, user_id, "terminal")
        with pytest.raises(psycopg2.Error, match="ck_arb_review_cycle_shape"):
            _insert_typed_graph(
                cursor,
                organization_id=org_id,
                user_id=user_id,
                subject=subject,
                cycle_status="rejected",
                closed_at=datetime.now(timezone.utc),
                terminal_outcome=None,
                review_decision="rejected",
            )
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        raw.rollback()
        raw.close()


def test_terminal_review_requires_non_null_canonical_decision(app, _schema):
    """A terminal review without its canonical decision must fail SQL validation."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, _model_id = _seed_org_user_model(cursor, "terminal-null-decision")
        subject = _seed_subject_material(cursor, "adr", org_id, user_id, "terminal")
        with pytest.raises(psycopg2.Error, match="ck_arb_review_item_typed_shape"):
            _insert_typed_graph(
                cursor,
                organization_id=org_id,
                user_id=user_id,
                subject=subject,
                cycle_status="rejected",
                closed_at=datetime.now(timezone.utc),
                terminal_outcome="rejected",
                review_decision=None,
            )
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        raw.rollback()
        raw.close()


def test_successor_rejects_predecessor_without_complete_terminal_projection(app, _schema):
    """The successor trigger independently rejects a damaged terminal predecessor."""
    raw = _install_typed_schema(app)
    try:
        cursor = raw.cursor()
        org_id, user_id, _model_id = _seed_org_user_model(cursor, "null-predecessor")
        subject = _seed_subject_material(cursor, "adr", org_id, user_id, "predecessor")
        cursor.execute(
            "ALTER TABLE arb_review_cycles DROP CONSTRAINT ck_arb_review_cycle_shape"
        )
        first_cycle, _review_id, _snapshot_id = _insert_typed_graph(
            cursor,
            organization_id=org_id,
            user_id=user_id,
            subject=subject,
            cycle_status="rejected",
            closed_at=datetime.now(timezone.utc),
            terminal_outcome=None,
            review_decision="rejected",
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("SET CONSTRAINTS ALL DEFERRED")
        with pytest.raises(
            psycopg2.Error, match="cycle predecessor is not monotonic"
        ):
            _insert_typed_graph(
                cursor,
                organization_id=org_id,
                user_id=user_id,
                subject=subject,
                cycle_number=2,
                predecessor_cycle_id=first_cycle,
            )
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        raw.rollback()
        raw.close()


def _purge_concurrency_test_organizations(connection, organization_ids):
    """Remove only committed fixtures created for a multi-transaction race."""
    connection.rollback()
    cursor = connection.cursor()
    try:
        cursor.execute("SET session_replication_role = replica")
        cursor.execute(
            """
            SELECT column_row.table_name
            FROM information_schema.columns column_row
            JOIN information_schema.tables table_row
              ON table_row.table_schema = column_row.table_schema
             AND table_row.table_name = column_row.table_name
            WHERE column_row.table_schema = current_schema()
              AND column_row.column_name = 'organization_id'
              AND table_row.table_type = 'BASE TABLE'
            """
        )
        for (table_name,) in cursor.fetchall():
            cursor.execute(
                sql.SQL("DELETE FROM {} WHERE organization_id = ANY(%s)").format(
                    sql.Identifier(table_name)
                ),
                (list(organization_ids),),
            )
        cursor.execute(
            "DELETE FROM organizations WHERE id = ANY(%s)",
            (list(organization_ids),),
        )
        cursor.execute("SET session_replication_role = origin")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


@pytest.mark.parametrize("subject_type", sorted(SUBJECT_TYPES))
def test_parent_retenant_and_child_insert_share_subject_concurrency_fence(
    app, _schema, subject_type
):
    """A child commit racing an uncommitted re-tenant must block, then fail."""
    setup = _install_typed_schema(app)
    parent = None
    child = None
    organization_ids = ()
    try:
        setup_cursor = setup.cursor()
        org_a, user_a, _model_a = _seed_org_user_model(
            setup_cursor, f"race-source-{subject_type}"
        )
        org_b, _user_b, _model_b = _seed_org_user_model(
            setup_cursor, f"race-target-{subject_type}"
        )
        organization_ids = (org_a, org_b)
        subject = _seed_subject_material(
            setup_cursor, subject_type, org_a, user_a, "race"
        )
        setup.commit()

        from app import db

        with app.app_context():
            parent = raw_psycopg2_connection(db.engine)
            child = raw_psycopg2_connection(db.engine)
        parent_cursor = parent.cursor()
        child_cursor = child.cursor()
        table = {
            "decision_brief": "decision_briefs",
            "solution": "solutions",
            "architecture_model": "architecture_models",
            "adr": "architecture_decisions",
        }[subject_type]
        parent_cursor.execute(
            f"UPDATE {table} SET organization_id = %s WHERE id = %s",
            (org_b, subject["subject_id"]),
        )
        _insert_typed_graph(
            child_cursor,
            organization_id=org_a,
            user_id=user_a,
            subject=subject,
        )

        def commit_child():
            try:
                child.commit()
            except psycopg2.Error as error:
                child.rollback()
                return error
            return None

        with ThreadPoolExecutor(max_workers=1) as executor:
            pending_commit = executor.submit(commit_child)
            try:
                early_result = pending_commit.result(timeout=0.5)
            except FutureTimeout:
                early_result = "blocked"
            if early_result != "blocked":
                parent.rollback()
                assert isinstance(early_result, psycopg2.Error), (
                    "child committed while the parent re-tenant was uncommitted"
                )
            else:
                parent.commit()
                child_error = pending_commit.result(timeout=10)
                assert isinstance(child_error, psycopg2.Error)

        verification = setup.cursor()
        verification.execute(
            "SELECT count(*) FROM arb_review_cycles "
            "WHERE subject_type = %s AND subject_id = %s",
            (subject_type, subject["subject_id"]),
        )
        assert verification.fetchone()[0] == 0
    finally:
        if parent is not None:
            parent.rollback()
            parent.close()
        if child is not None:
            child.rollback()
            child.close()
        if organization_ids:
            _purge_concurrency_test_organizations(setup, organization_ids)
        else:
            setup.rollback()
        setup.close()


@pytest.mark.parametrize(
    "damage_kind",
    (
        "check_true_or_canonical",
        "index_include",
        "function_noop_with_messages",
        "fk_shadow_schema",
        "fk_action_deferrability",
        "trigger_wrong_update_column",
        "check_regrouped_same_tokens",
        "trigger_when_false",
        "trigger_with_argument",
    ),
)
def test_reconcile_rejects_semantically_ineffective_same_named_guards(
    app, _schema, damage_kind
):
    """Catalog equality must cover complete semantics, not selected signature tokens."""
    raw = _install_typed_schema(app)
    raw.close()
    from app import db
    from app.models.architecture_review_board import (
        ensure_arb_cycle_constraints,
        inspect_arb_cycle_constraints,
    )

    with app.app_context(), db.engine.connect() as connection:
        transaction = connection.begin()
        try:
            if damage_kind == "check_true_or_canonical":
                definition = connection.exec_driver_sql(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'ck_arb_review_cycle_shape' "
                    "AND conrelid = 'arb_review_cycles'::regclass"
                ).scalar_one()
                expression = definition.removeprefix("CHECK (").removesuffix(")")
                connection.exec_driver_sql(
                    "ALTER TABLE arb_review_cycles DROP CONSTRAINT "
                    "ck_arb_review_cycle_shape"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE arb_review_cycles ADD CONSTRAINT "
                    f"ck_arb_review_cycle_shape CHECK (TRUE OR ({expression}))"
                )
            elif damage_kind == "check_regrouped_same_tokens":
                definition = connection.exec_driver_sql(
                    "SELECT pg_get_constraintdef(oid, true) FROM pg_constraint "
                    "WHERE conname = 'ck_arb_review_cycle_shape' "
                    "AND conrelid = 'arb_review_cycles'::regclass"
                ).scalar_one()
                expression = definition.removeprefix("CHECK (").removesuffix(")")
                regrouped = expression.replace(
                    "opened_at IS NOT NULL AND (cycle_number = 1",
                    "(opened_at IS NOT NULL AND cycle_number = 1",
                    1,
                )
                assert regrouped != expression
                connection.exec_driver_sql(
                    "ALTER TABLE arb_review_cycles DROP CONSTRAINT "
                    "ck_arb_review_cycle_shape"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE arb_review_cycles ADD CONSTRAINT "
                    f"ck_arb_review_cycle_shape CHECK ({regrouped})"
                )
            elif damage_kind == "index_include":
                connection.exec_driver_sql(
                    "DROP INDEX uq_arb_review_cycle_open_subject"
                )
                connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX uq_arb_review_cycle_open_subject "
                    "ON arb_review_cycles (organization_id, subject_type, subject_id) "
                    "INCLUDE (status) WHERE closed_at IS NULL"
                )
            elif damage_kind == "function_noop_with_messages":
                connection.exec_driver_sql(
                    "CREATE OR REPLACE FUNCTION archie_validate_arb_cycle_membership() "
                    "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                    "PERFORM 'cycle review projection is missing or disagrees'; "
                    "PERFORM 'cycle predecessor is not monotonic'; "
                    "PERFORM 'version does not belong to its brief and tenant'; "
                    "RETURN NEW; END; $$"
                )
            elif damage_kind == "fk_shadow_schema":
                connection.exec_driver_sql("CREATE SCHEMA arb_guard_shadow")
                connection.exec_driver_sql(
                    "CREATE TABLE arb_guard_shadow.decision_brief_versions "
                    "(id integer PRIMARY KEY)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO arb_guard_shadow.decision_brief_versions (id) "
                    "SELECT id FROM decision_brief_versions"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE arb_review_items DROP CONSTRAINT "
                    "fk_arb_review_item_decision_brief_version"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE arb_review_items ADD CONSTRAINT "
                    "fk_arb_review_item_decision_brief_version "
                    "FOREIGN KEY (decision_brief_version_id) REFERENCES "
                    "arb_guard_shadow.decision_brief_versions(id)"
                )
            elif damage_kind == "fk_action_deferrability":
                connection.exec_driver_sql(
                    "ALTER TABLE arb_review_items DROP CONSTRAINT "
                    "fk_arb_review_item_decision_brief_version"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE arb_review_items ADD CONSTRAINT "
                    "fk_arb_review_item_decision_brief_version "
                    "FOREIGN KEY (decision_brief_version_id) REFERENCES "
                    "decision_brief_versions(id) ON DELETE CASCADE "
                    "DEFERRABLE INITIALLY DEFERRED"
                )
            elif damage_kind == "trigger_wrong_update_column":
                connection.exec_driver_sql(
                    "DROP TRIGGER trg_arb_decision_brief_tenant_history "
                    "ON decision_briefs"
                )
                connection.exec_driver_sql(
                    "CREATE TRIGGER trg_arb_decision_brief_tenant_history "
                    "BEFORE UPDATE OF title ON decision_briefs FOR EACH ROW "
                    "EXECUTE FUNCTION archie_guard_arb_subject_tenant()"
                )
            elif damage_kind == "trigger_when_false":
                connection.exec_driver_sql(
                    "DROP TRIGGER trg_arb_decision_brief_tenant_history "
                    "ON decision_briefs"
                )
                connection.exec_driver_sql(
                    "CREATE TRIGGER trg_arb_decision_brief_tenant_history "
                    "BEFORE UPDATE OF organization_id ON decision_briefs "
                    "FOR EACH ROW WHEN (false) "
                    "EXECUTE FUNCTION archie_guard_arb_subject_tenant()"
                )
            else:
                connection.exec_driver_sql(
                    "DROP TRIGGER trg_arb_decision_brief_tenant_history "
                    "ON decision_briefs"
                )
                connection.exec_driver_sql(
                    "CREATE TRIGGER trg_arb_decision_brief_tenant_history "
                    "BEFORE UPDATE OF organization_id ON decision_briefs "
                    "FOR EACH ROW EXECUTE FUNCTION "
                    "archie_guard_arb_subject_tenant('ignored')"
                )

            drift = inspect_arb_cycle_constraints(connection)
            assert drift, f"{damage_kind} was accepted as canonical"
            ensure_arb_cycle_constraints(connection)
            assert inspect_arb_cycle_constraints(connection) == []
        finally:
            transaction.rollback()


def test_subject_tenant_guard_fails_closed_on_unsupported_table(app, _schema):
    """An unlisted table must be refused, not silently waved through."""
    raw = _install_typed_schema(app)
    raw.close()
    from app import db

    with app.app_context(), db.engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql(
                "CREATE TABLE arb_guard_unlisted "
                "(id integer PRIMARY KEY, organization_id integer)"
            )
            connection.exec_driver_sql(
                "CREATE TRIGGER trg_arb_guard_unlisted_tenant_history "
                "BEFORE UPDATE OF organization_id ON arb_guard_unlisted "
                "FOR EACH ROW EXECUTE FUNCTION archie_guard_arb_subject_tenant()"
            )
            connection.exec_driver_sql(
                "INSERT INTO arb_guard_unlisted (id, organization_id) VALUES (1, 1)"
            )
            with pytest.raises(Exception, match="unsupported table"):
                connection.exec_driver_sql(
                    "UPDATE arb_guard_unlisted SET organization_id = 2 WHERE id = 1"
                )
        finally:
            transaction.rollback()


def test_check_tokenizer_refuses_characters_it_cannot_compare(app, _schema):
    """Silently dropped operators would equate a weaker predicate to the canon."""
    from app.models.architecture_review_board import (
        _arb_check_structure,
        _arb_check_tokens,
        _arb_model_check_definitions,
    )

    with app.app_context():
        for definition in _arb_model_check_definitions().values():
            assert _arb_check_tokens(definition), definition

    for unreadable in (
        "CHECK (cycle_number + 1 > 0)",
        "CHECK (subject_type ~ 'adr')",
        "CHECK (payload.subject_id IS NOT NULL)",
    ):
        with pytest.raises(ValueError, match="unrecognized token"):
            _arb_check_tokens(unreadable)

    # The pair below differs only in characters the old tokenizer discarded, so
    # it normalized to one token stream and the weaker predicate was accepted.
    with pytest.raises(ValueError, match="unrecognized token"):
        _arb_check_structure("CHECK (cycle_number > 0)") == _arb_check_structure(
            "CHECK (cycle_number > -0)"
        )


def test_constraint_state_is_keyed_per_table_not_per_name(app, _schema):
    """conname is unique per table: a decoy must not evict the real constraint."""
    raw = _install_typed_schema(app)
    raw.close()
    from app import db
    from app.models.architecture_review_board import (
        _arb_check_state,
        _arb_fk_state,
        ensure_arb_cycle_constraints,
        inspect_arb_cycle_constraints,
    )

    with app.app_context(), db.engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql(
                "CREATE TABLE arb_guard_decoy ("
                "id integer PRIMARY KEY, "
                "solution_id integer, "
                "cycle_number integer, "
                "CONSTRAINT ck_arb_review_cycle_shape CHECK (cycle_number > 0))"
            )
            connection.exec_driver_sql(
                "ALTER TABLE arb_guard_decoy ADD CONSTRAINT "
                "fk_arb_review_cycle_solution FOREIGN KEY (solution_id) "
                "REFERENCES solutions(id)"
            )

            check_state = _arb_check_state(connection, "public")
            assert ("arb_guard_decoy", "ck_arb_review_cycle_shape") in check_state
            assert ("arb_review_cycles", "ck_arb_review_cycle_shape") in check_state
            fk_state = _arb_fk_state(connection, "public")
            assert ("arb_guard_decoy", "fk_arb_review_cycle_solution") in fk_state
            assert ("arb_review_cycles", "fk_arb_review_cycle_solution") in fk_state

            assert inspect_arb_cycle_constraints(connection) == []
            ensure_arb_cycle_constraints(connection)
            assert inspect_arb_cycle_constraints(connection) == []
        finally:
            transaction.rollback()
