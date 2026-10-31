"""Immutable Transformation Room option-version contracts."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app import db
from app.models.application_capability import ApplicationCapabilityMapping
from app.models.business_capabilities import BusinessCapability
from app.models.transformation_decision import (
    DecisionBrief,
    TransformationOption,
    TransformationOptionVersion,
)
from app.models.transformation_evidence import EvidenceRequest
from app.models.transformation_programme import (
    MeasureDefinition,
    ProgrammeOutcomeCommitment,
    ProgrammeWorkstream,
)
from app.models.unified_capability import ValueStream
from app.modules.transformation_room.decision_service import TransformationOptionService
from app.modules.transformation_room.domain import ActorContext, CommandConflict, NotFound

from tests.test_transformation_evidence_service import (
    EvidenceScope,
    _record_inventory,
    evidence_scope,
)


@dataclass(frozen=True)
class DecisionScope:
    organization_id: int
    actor_id: int
    workstream_id: int
    candidate_id: int
    application_id: int
    outcome_id: int
    measure_id: int
    value_stream_id: int
    option_ids: tuple[int, int]
    brief_id: int
    evidence_id: int
    actor: ActorContext


def _option_values(scope: EvidenceScope, *, title: str, action_type: str, ordinal: int):
    with Session(db.engine) as session:
        capability_id = session.scalar(
            select(ApplicationCapabilityMapping.business_capability_id).where(
                ApplicationCapabilityMapping.organization_id == scope.organization_id,
                ApplicationCapabilityMapping.application_component_id == scope.application_id,
            )
        )
        value_stream_id = session.scalar(
            select(ValueStream.id).where(
                ValueStream.organization_id == scope.organization_id,
            )
        )
    return {
        "organization_id": scope.organization_id,
        "workstream_id": scope.workstream_id,
        "candidate_id": scope.candidate_id,
        "title": title,
        "action_type": action_type,
        "description": f"Governed {action_type.lower()} alternative",
        "assumptions": [f"Assumption {ordinal} is explicitly test-owned"],
        "dependencies": [f"Dependency {ordinal} is resolved before execution"],
        "impacts": [
            {
                "impact_type": "capability",
                "subject_id": capability_id,
                "description": f"Capability impact {ordinal}",
            },
            {
                "impact_type": "value_stream",
                "subject_id": value_stream_id,
                "description": f"Value-stream impact {ordinal}",
            },
        ],
        "risks": [f"Risk {ordinal} has an owned mitigation"],
        "reversibility": "Reversible until the governed cutover",
        "transition_approach": f"Transition wave {ordinal}",
        "affected_capability_ids": [capability_id],
        "affected_value_stream_ids": [value_stream_id],
        "recommendation_rationale": f"Alternative {ordinal} rationale",
        "cost_min": Decimal(10000 * ordinal),
        "cost_max": Decimal(15000 * ordinal),
        "benefit_min": Decimal(20000 * ordinal),
        "benefit_max": Decimal(30000 * ordinal),
        "risk_min": Decimal("0.10") * ordinal,
        "risk_max": Decimal("0.20") * ordinal,
        "currency": "GBP",
        "technology_required": ordinal % 2 == 0,
        "revision": 1,
    }


@pytest.fixture
def decision_scope(app, evidence_scope: EvidenceScope):
    """Persist real option/brief prerequisites visible to fenced sessions."""
    scope = evidence_scope
    suffix = uuid.uuid4().hex[:10]
    with app.app_context():
        with Session(db.engine) as session, session.begin():
            workstream = session.scalar(
                select(ProgrammeWorkstream).where(
                    ProgrammeWorkstream.organization_id == scope.organization_id,
                    ProgrammeWorkstream.id == scope.workstream_id,
                )
            )
            workstream.lifecycle_stage = "options"
            value_stream = ValueStream(
                organization_id=scope.organization_id,
                name=f"Customer service {suffix}",
                code=f"TR-{suffix}",
                value_stream_type="customer_facing",
            )
            outcome = ProgrammeOutcomeCommitment(
                organization_id=scope.organization_id,
                programme_id=workstream.programme_id,
                workstream_id=workstream.id,
                statement="Reduce avoidable run cost without service disruption",
                owner_id=scope.actor_id,
                improvement_direction="decrease",
                lifecycle="committed",
            )
            session.add_all((value_stream, outcome))
            session.flush()
            measure = MeasureDefinition(
                organization_id=scope.organization_id,
                outcome_commitment_id=outcome.id,
                metric_name="Annual run cost",
                unit="GBP",
                currency="GBP",
                aggregation="sum",
                baseline_amount=Decimal("125000.00"),
                target_amount=Decimal("95000.00"),
            )
            session.add(measure)
            session.flush()
            outcome_id = outcome.id
            measure_id = measure.id
            value_stream_id = value_stream.id
        evidence = _record_inventory(scope, key=f"decision-evidence-{suffix}")
        with Session(db.engine) as session, session.begin():
            request = session.scalar(
                select(EvidenceRequest).where(
                    EvidenceRequest.organization_id == scope.organization_id,
                    EvidenceRequest.id == scope.request_id,
                )
            )
            request.status = "accepted"
            request.submitted_evidence_id = evidence.object_ids["evidence_record_id"]
            request.accepted_evidence_id = evidence.object_ids["evidence_record_id"]
            request.submitted_at = datetime.now(timezone.utc)
            request.accepted_at = datetime.now(timezone.utc)
            option_one = TransformationOption(
                **_option_values(scope, title="Tolerate", action_type="tolerate", ordinal=1)
            )
            option_two = TransformationOption(
                **_option_values(scope, title="Migrate", action_type="migrate", ordinal=2)
            )
            session.add_all((option_one, option_two))
            session.flush()
            brief = DecisionBrief(
                organization_id=scope.organization_id,
                workstream_id=scope.workstream_id,
                candidate_id=scope.candidate_id,
                title="Application rationalisation decision",
                recommendation_option_id=option_two.id,
                decision_authority_id=scope.actor_id,
                unknown_codes=["cost_source_unknown"],
                conflicts=["Operational cutover window requires confirmation"],
                expected_impacts=["Lower run cost after controlled migration"],
                status="draft",
                revision=1,
            )
            session.add(brief)
            session.flush()
            created = DecisionScope(
                organization_id=scope.organization_id,
                actor_id=scope.actor_id,
                workstream_id=scope.workstream_id,
                candidate_id=scope.candidate_id,
                application_id=scope.application_id,
                outcome_id=outcome_id,
                measure_id=measure_id,
                value_stream_id=value_stream_id,
                option_ids=(option_one.id, option_two.id),
                brief_id=brief.id,
                evidence_id=evidence.object_ids["evidence_record_id"],
                actor=scope.actor,
            )
        try:
            yield created
        finally:
            db.session.remove()
            from app.models.organization import Organization
            from app.services.org_deletion_service import OrganizationDeletionService

            own_id = scope.organization_id
            foreign_id = scope.foreign_organization_id
            raw = db.engine.raw_connection()
            try:
                with raw.cursor() as cursor:
                    # archie_command_claim_challenges carries organization_id with no
                    # foreign key to organizations, so the deletion service never
                    # reaches it; no guard sits on it, so a plain delete needs no marker.
                    cursor.execute(
                        "DELETE FROM archie_command_claim_challenges "
                        "WHERE organization_id IN (%s, %s)",
                        (own_id, foreign_id),
                    )
                    raw.commit()
            finally:
                raw.close()
            # The foreign organisation first: it may deliberately cite the primary
            # organisation's rows, so the citing side must be gone before the cited
            # organisation is deleted.
            for org_id in (foreign_id, own_id):
                if db.session.get(Organization, org_id) is not None:
                    OrganizationDeletionService.delete_organization(org_id)
            db.session.remove()


def _freeze(scope: DecisionScope, option_id: int, *, key: str, revision: int = 1):
    return TransformationOptionService.freeze_version(
        actor=scope.actor,
        option_id=option_id,
        expected_revision=revision,
        command_key=key,
    )


def test_public_option_draft_creation_is_tenant_authorised_and_replay_safe(
    decision_scope,
):
    scope = decision_scope
    values = _option_values(
        scope, title="Retire", action_type="retire", ordinal=3
    )
    draft = {
        key: value
        for key, value in values.items()
        if key
        not in {
            "organization_id",
            "workstream_id",
            "candidate_id",
            "revision",
        }
    }
    created = TransformationOptionService.create_draft(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        candidate_id=scope.candidate_id,
        draft=draft,
        command_key="public-retire-draft",
    )
    replayed = TransformationOptionService.create_draft(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        candidate_id=scope.candidate_id,
        draft=draft,
        command_key="public-retire-draft",
    )

    with Session(db.engine) as session:
        option = session.get(
            TransformationOption, created.object_ids["option_id"]
        )
    assert option.organization_id == scope.organization_id
    assert option.candidate_id == scope.candidate_id
    assert option.title == "Retire"
    assert created.created is True
    assert replayed.created is False and replayed.idempotent is True
    assert replayed.object_ids == created.object_ids


def test_decision_brief_scope_uses_separate_partial_unique_indexes():
    """Catches PostgreSQL NULL semantics allowing duplicate workstream briefs."""
    indexes = {index.name: index for index in DecisionBrief.__table__.indexes}
    workstream = indexes["uq_decision_brief_workstream_scope"]
    candidate = indexes["uq_decision_brief_candidate_scope"]

    assert workstream.unique is True
    assert [column.name for column in workstream.columns] == [
        "organization_id",
        "workstream_id",
    ]
    assert "candidate_id IS NULL" in str(
        workstream.dialect_options["postgresql"]["where"]
    )
    assert candidate.unique is True
    assert [column.name for column in candidate.columns] == [
        "organization_id",
        "workstream_id",
        "candidate_id",
    ]
    assert "candidate_id IS NOT NULL" in str(
        candidate.dialect_options["postgresql"]["where"]
    )


def test_freeze_persists_complete_decimal_canonical_snapshot_and_replays(decision_scope):
    """Catches lossy numbers, omitted option contract fields, or duplicate replay."""
    scope = decision_scope
    result = _freeze(scope, scope.option_ids[0], key="option-freeze-complete")
    replay = _freeze(scope, scope.option_ids[0], key="option-freeze-complete")

    with Session(db.engine) as session:
        version = session.get(
            TransformationOptionVersion,
            result.object_ids["option_version_id"],
        )
        root = session.get(TransformationOption, scope.option_ids[0])
        count = session.scalar(
            select(func.count())
            .select_from(TransformationOptionVersion)
            .where(
                TransformationOptionVersion.organization_id == scope.organization_id,
                TransformationOptionVersion.option_id == scope.option_ids[0],
            )
        )

    assert replay.operation_result_id == result.operation_result_id
    assert replay.created is False and replay.idempotent is True
    assert count == 1 and version.version == 1 and root.revision == 2
    assert version.cost_min == Decimal("10000.00")
    assert version.cost_max == Decimal("15000.00")
    assert version.benefit_min == Decimal("20000.00")
    assert version.risk_max == Decimal("0.2000")
    assert version.currency == "GBP"
    assert version.technology_required is False
    assert version.content_json["assumptions"]
    assert version.content_json["dependencies"]
    assert version.content_json["impacts"]
    assert version.content_json["reversibility"]
    assert version.content_json["cost_min"] == "10000"
    assert len(version.content_hash) == 64
    assert version.captured_by_id == scope.actor_id
    assert version.captured_at.tzinfo is not None


def test_option_fact_validation_rejects_binary_float(decision_scope):
    """Catches IEEE-754 rounding entering facts that require Decimal fidelity."""
    scope = decision_scope
    with Session(db.engine) as session:
        option = session.get(TransformationOption, scope.option_ids[0])
        session.expunge(option)
    option.cost_min = 0.1

    with pytest.raises(ValueError, match="cost_min must use Decimal"):
        TransformationOptionService.canonical_option_payload(option, 1)


def _altered_version(version, field, replacement):
    values = {
        column.name: getattr(version, column.name)
        for column in TransformationOptionVersion.__table__.columns
    }
    values[field] = replacement
    return TransformationOptionVersion(**values)


def test_option_hash_binds_scope_and_every_comparison_column(decision_scope):
    """Catches duplicated numeric/scope columns escaping the integrity digest."""
    scope = decision_scope
    frozen = _freeze(scope, scope.option_ids[0], key="hash-all-option-facts")
    with Session(db.engine) as session:
        version = session.get(
            TransformationOptionVersion,
            frozen.object_ids["option_version_id"],
        )
        session.expunge(version)

    assert TransformationOptionService.verify_version_hash(version)
    for field, replacement in (
        ("workstream_id", version.workstream_id + 1),
        ("candidate_id", None),
        ("cost_min", version.cost_min + Decimal("1.00")),
        ("cost_max", version.cost_max + Decimal("1.00")),
        ("benefit_min", version.benefit_min + Decimal("1.00")),
        ("benefit_max", version.benefit_max + Decimal("1.00")),
        ("risk_min", version.risk_min + Decimal("0.01")),
        ("risk_max", version.risk_max + Decimal("0.01")),
        ("currency", "USD"),
        ("technology_required", not version.technology_required),
    ):
        assert not TransformationOptionService.verify_version_hash(
            _altered_version(version, field, replacement)
        ), field


def test_compare_rejects_a_database_corrupt_comparison_column(decision_scope):
    """Catches comparison trusting duplicated columns without verifying the hash."""
    scope = decision_scope
    frozen = _freeze(scope, scope.option_ids[0], key="compare-integrity")
    version_id = frozen.object_ids["option_version_id"]
    with db.engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL session_replication_role = replica")  # session-replication-role-ok: mutates a frozen transformation_option_versions row; the append-only guard correctly refuses every UPDATE on this table
        connection.execute(
            text(
                "UPDATE transformation_option_versions "
                "SET cost_min = cost_min + 1 "
                "WHERE id = :version_id AND organization_id = :organization_id"
            ),
            {"version_id": version_id, "organization_id": scope.organization_id},
        )

    with pytest.raises(CommandConflict, match="option_version_hash_invalid"):
        TransformationOptionService.compare(
            actor=scope.actor,
            option_version_ids=(version_id,),
        )


@pytest.mark.parametrize("reference_kind", ("missing", "foreign"))
def test_freeze_rejects_non_tenant_capability_and_value_stream_ids(
    decision_scope, evidence_scope, reference_kind
):
    """Catches syntax-only reference IDs entering a governed option snapshot."""
    scope = decision_scope
    if reference_kind == "missing":
        capability_id = value_stream_id = 2_000_000_000
    else:
        suffix = uuid.uuid4().hex[:8]
        with Session(db.engine) as session, session.begin():
            capability = BusinessCapability(
                organization_id=evidence_scope.foreign_organization_id,
                name=f"Foreign capability {suffix}",
                code=f"FC-{suffix}",
                level=1,
            )
            value_stream = ValueStream(
                organization_id=evidence_scope.foreign_organization_id,
                name=f"Foreign value stream {suffix}",
                code=f"FV-{suffix}",
                value_stream_type="internal",
            )
            session.add_all((capability, value_stream))
            session.flush()
            capability_id, value_stream_id = capability.id, value_stream.id
    with Session(db.engine) as session, session.begin():
        option = session.get(TransformationOption, scope.option_ids[0])
        option.affected_capability_ids = [capability_id]
        option.affected_value_stream_ids = [value_stream_id]

    with pytest.raises(NotFound, match="affected_(capabilities|value_streams)_not_found"):
        _freeze(
            scope,
            scope.option_ids[0],
            key=f"reject-{reference_kind}-references",
            revision=2,
        )


@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    (
        ("assumptions", [], "assumptions"),
        ("dependencies", [], "dependencies"),
        ("impacts", [], "impacts"),
        ("reversibility", "", "reversibility"),
        ("technology_required", None, "technology_required"),
        ("cost_min", Decimal("NaN"), "cost_min"),
        ("currency", "ZZZ", "currency"),
    ),
)
def test_freeze_rejects_incomplete_or_non_decimal_option_contract(
    decision_scope, field, bad_value, message
):
    """Catches an immutable version preserving an incomplete or invented contract."""
    scope = decision_scope
    if field == "currency":
        with pytest.raises(ValueError, match=message):
            with Session(db.engine) as session, session.begin():
                option = session.get(TransformationOption, scope.option_ids[0])
                setattr(option, field, bad_value)
        return
    with Session(db.engine) as session, session.begin():
        option = session.get(TransformationOption, scope.option_ids[0])
        setattr(option, field, bad_value)

    with pytest.raises(ValueError, match=message):
        _freeze(scope, scope.option_ids[0], key=f"invalid-{field}")
    with Session(db.engine) as session:
        assert session.scalar(
            select(func.count())
            .select_from(TransformationOptionVersion)
            .where(
                TransformationOptionVersion.organization_id == scope.organization_id,
                TransformationOptionVersion.option_id == scope.option_ids[0],
            )
        ) == 0


def test_compare_uses_exact_stored_versions_and_rejects_client_totals(decision_scope):
    """Catches client-authored totals, missing IDs, duplicates, or draft-value leakage."""
    scope = decision_scope
    first = _freeze(scope, scope.option_ids[0], key="compare-first")
    second = _freeze(scope, scope.option_ids[1], key="compare-second")
    version_ids = (
        first.object_ids["option_version_id"],
        second.object_ids["option_version_id"],
    )
    with Session(db.engine) as session, session.begin():
        draft = session.get(TransformationOption, scope.option_ids[0])
        draft.cost_min = Decimal("999999.00")
        draft.cost_max = Decimal("999999.00")

    comparison = TransformationOptionService.compare(
        actor=scope.actor,
        option_version_ids=version_ids,
    )

    assert tuple(comparison.option_version_ids) == version_ids
    assert comparison.comparable_currency == "GBP"
    assert comparison.cost_range == (Decimal("10000.00"), Decimal("30000.00"))
    assert comparison.benefit_range == (Decimal("20000.00"), Decimal("60000.00"))
    assert comparison.conflicts == ()
    with pytest.raises(TypeError):
        TransformationOptionService.compare(
            actor=scope.actor,
            option_version_ids=version_ids,
            client_totals={"cost_min": "0"},
        )
    with pytest.raises(ValueError, match="duplicate"):
        TransformationOptionService.compare(
            actor=scope.actor,
            option_version_ids=(version_ids[0], version_ids[0]),
        )
    with pytest.raises(NotFound, match="option_versions_not_found"):
        TransformationOptionService.compare(
            actor=scope.actor,
            option_version_ids=(version_ids[0], 999999999),
        )


def test_compare_reports_noncomparable_currency_without_fabricating_range(decision_scope):
    """Catches arithmetic across currencies or a fabricated zero comparison."""
    scope = decision_scope
    with Session(db.engine) as session, session.begin():
        second = session.get(TransformationOption, scope.option_ids[1])
        second.currency = "USD"
    first_result = _freeze(scope, scope.option_ids[0], key="currency-first")
    second_result = _freeze(
        scope, scope.option_ids[1], key="currency-second", revision=2
    )

    comparison = TransformationOptionService.compare(
        actor=scope.actor,
        option_version_ids=(
            first_result.object_ids["option_version_id"],
            second_result.object_ids["option_version_id"],
        ),
    )

    assert comparison.comparable_currency is None
    assert comparison.cost_range is None and comparison.benefit_range is None
    assert comparison.conflicts == ("currency_mismatch",)


def test_concurrent_same_revision_freeze_serializes_and_leaves_one_version(
    app, decision_scope
):
    """Catches two workers freezing one draft revision or leaving an orphan loser."""
    scope = decision_scope
    engine = db.engine
    first_has_lock = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    results = []
    errors = []

    def pause_first(_conn, _cursor, statement, _params, _context, _many):
        if (
            threading.current_thread().name == "option-version-first"
            and statement.lstrip().upper().startswith("SELECT")
            and "transformation_options" in statement.lower()
            and "for update" in statement.lower()
        ):
            first_has_lock.set()
            if not release_first.wait(timeout=10):
                raise TimeoutError("option version race pause was not released")

    def freeze(key, entered=None):
        with app.app_context():
            if entered is not None:
                entered.set()
            actor = replace(scope.actor, request_id=f"{scope.actor.request_id}-{key}")
            try:
                results.append(
                    TransformationOptionService.freeze_version(
                        actor=actor,
                        option_id=scope.option_ids[0],
                        expected_revision=1,
                        command_key=key,
                    )
                )
            except Exception as error:  # asserted after both real workers finish
                errors.append(error)
            finally:
                db.session.remove()

    event.listen(engine, "after_cursor_execute", pause_first)
    first = threading.Thread(
        target=freeze,
        args=("option-race-first",),
        name="option-version-first",
        daemon=True,
    )
    second = threading.Thread(
        target=freeze,
        args=("option-race-second", second_entered),
        name="option-version-second",
        daemon=True,
    )
    try:
        first.start()
        assert first_has_lock.wait(timeout=10)
        second.start()
        assert second_entered.wait(timeout=5)
        time.sleep(0.2)
        assert second.is_alive() is True
    finally:
        release_first.set()
        first.join(timeout=10)
        second.join(timeout=10)
        event.remove(engine, "after_cursor_execute", pause_first)

    assert first.is_alive() is False and second.is_alive() is False
    # Two interleavings are legitimate and the outcome depends on timing. If the
    # second worker is blocked on the row lock when the first commits, it finds
    # the revision moved and fails with stale_revision. If it only claims after
    # the first result exists, the database claim reconciles its rotated
    # transport key to that result and it is a replay. Either way exactly one
    # worker created the version and nobody else created a second.
    assert len(errors) <= 1
    for error in errors:
        assert isinstance(error, CommandConflict) and error.reason == "stale_revision"
    assert len(results) + len(errors) == 2
    assert sum(1 for result in results if result.created) == 1
    assert {result.object_ids["option_version_id"] for result in results} != set() and len(
        {result.object_ids["option_version_id"] for result in results}
    ) == 1
    with Session(engine) as session:
        root = session.get(TransformationOption, scope.option_ids[0])
        count = session.scalar(
            select(func.count())
            .select_from(TransformationOptionVersion)
            .where(
                TransformationOptionVersion.organization_id == scope.organization_id,
                TransformationOptionVersion.option_id == scope.option_ids[0],
            )
        )
    assert root.revision == 2 and count == 1
