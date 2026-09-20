"""Canonical execution contracts for approved Transformation Room decisions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import threading
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app import db
from app.models.archimate_core import ArchiMateElement
from app.models.architecture_review_board import ARBReviewCycle, ARBReviewItem
from app.models.arb_decision_event import ARBCondition
from app.models.benefit import Benefit
from app.models.implementation_migration import WorkPackage
from app.models.organization import Organization
from app.models.solution_models import Solution
from app.models.strategic import RoadmapItem, StrategicInitiative
from app.models.transformation_decision import (
    DecisionBrief,
    DecisionBriefVersion,
    TransformationOption,
    TransformationOptionVersion,
)
from app.models.transformation_db_guards import (
    _ISSUE_COMMAND_CLAIM_CHALLENGE_SQL,
    _render_guard_sql,
    ensure_transformation_db_guards,
)
from app.models.transformation_execution import (
    CommandIdempotencyRecord,
    DeliveryExportAttempt,
    OperationOutboxEvent,
    OutcomeMeasurement,
)
from app.models.transformation_programme import (
    MeasureDefinition,
    ProgrammeOutcomeCommitment,
    ProgrammeRoleAssignment,
    ProgrammeWorkstream,
)
from app.models.user import User
from app.modules.transformation_room.command_service import (
    CommandService,
    canonical_request_digest,
)
from app.modules.transformation_room.decision_service import (
    DecisionBriefService,
    TransformationOptionService,
    _canonical_json,
    _sha256_canonical,
)
from app.modules.transformation_room.domain import (
    ActorContext,
    ApprovedAction,
    BlockedByEvidence,
    CommandClaim,
    CommandConflict,
    CommandResult,
    GateBlocker,
    KnownPreCommitTransient,
    NotFound,
)
from app.modules.transformation_room.gate_service import TransformationGateService
from app.modules.transformation_room.execution_service import (
    TransformationExecutionService,
)
from app.modules.transformation_room.outcome_service import OutcomeMeasurementService


@dataclass(frozen=True)
class ExecutionScope:
    actor: ActorContext
    outcome_actor: ActorContext
    foreign_actor: ActorContext
    programme_id: int
    workstream_id: int
    decision_brief_version_id: int
    option_version_id: int
    action: ApprovedAction


def _user(session, organization_id, role):
    suffix = uuid4().hex
    row = User(
        organization_id=organization_id,
        email=f"task9-{suffix}@example.test",
        first_name="Task",
        last_name="Nine",
        enterprise_role=role,
        confirmed=True,
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def make_execution_scope(db_session, make_org):
    def make(
        *,
        technology_required=False,
        option_title="Controlled consolidation",
        outcome_statement="Reduce annual run cost",
    ):
        organization = make_org("execution")
        foreign_organization = make_org("execution-foreign")
        delivery_lead = _user(db_session, organization.id, "application_architect")
        outcome_owner = _user(db_session, organization.id, "business_architect")
        foreign_user = _user(
            db_session, foreign_organization.id, "application_architect"
        )
        programme = StrategicInitiative(
            organization_id=organization.id,
            name="Application estate transformation",
            description="Governed programme context",
            record_kind="transformation_programme",
            status="in_progress",
            owner_id=delivery_lead.id,
        )
        db_session.add(programme)
        db_session.flush()
        workstream = ProgrammeWorkstream(
            organization_id=organization.id,
            programme_id=programme.id,
            workstream_type="application_rationalisation",
            objective="Retire avoidable estate cost without service interruption",
            scope_expression={"application_ids": [41]},
            lifecycle_stage="approved",
            lead_id=delivery_lead.id,
        )
        db_session.add(workstream)
        db_session.flush()
        db_session.add(
            ProgrammeRoleAssignment(
                organization_id=organization.id,
                programme_id=programme.id,
                workstream_id=workstream.id,
                user_id=delivery_lead.id,
                role="delivery_lead",
                effective_from=date.today() - timedelta(days=1),
                assigned_by_id=delivery_lead.id,
            )
        )
        outcome = ProgrammeOutcomeCommitment(
            organization_id=organization.id,
            programme_id=programme.id,
            workstream_id=workstream.id,
            statement=outcome_statement,
            owner_id=outcome_owner.id,
            improvement_direction="decrease",
            target_date=date.today() + timedelta(days=180),
            lifecycle="committed",
        )
        db_session.add(outcome)
        db_session.flush()
        measure = MeasureDefinition(
            organization_id=organization.id,
            outcome_commitment_id=outcome.id,
            metric_name="Annual run cost",
            unit="GBP/year",
            currency="GBP",
            aggregation="latest",
            baseline_amount="1000.00",
            target_amount="750.00",
            baseline_date=date.today(),
            target_date=outcome.target_date,
            cadence="quarterly",
            source_adapter="finance-ledger",
            source_key="run-cost",
        )
        option = TransformationOption(
            organization_id=organization.id,
            workstream_id=workstream.id,
            title=option_title,
            action_type="consolidate",
            description="Consolidate the cited application estate",
            assumptions=["Service owners approve the cutover window"],
            dependencies=["Accepted service continuity evidence"],
            impacts=["Lower run cost"],
            risks=["Cutover interruption"],
            reversibility="Rollback during the controlled window",
            transition_approach="Phased consolidation",
            affected_capability_ids=[17],
            affected_value_stream_ids=[23],
            recommendation_rationale="Best evidence-backed value and risk balance",
            cost_min=Decimal("100.00"),
            cost_max=Decimal("200.00"),
            benefit_min=Decimal("250.00"),
            benefit_max=Decimal("300.00"),
            risk_min=Decimal("0.10"),
            risk_max=Decimal("0.20"),
            currency="GBP",
            technology_required=technology_required,
        )
        db_session.add_all([measure, option])
        db_session.flush()
        option_version = TransformationOptionVersion(
            organization_id=organization.id,
            option_id=option.id,
            workstream_id=workstream.id,
            version=1,
            source_revision=1,
            content_json={
                "title": option.title,
                "action_type": option.action_type,
                "description": option.description,
                "assumptions": list(option.assumptions),
                "dependencies": list(option.dependencies),
                "impacts": list(option.impacts),
                "risks": list(option.risks),
                "transition_approach": option.transition_approach,
            },
            cost_min=Decimal("100.00"),
            cost_max=Decimal("200.00"),
            benefit_min=Decimal("250.00"),
            benefit_max=Decimal("300.00"),
            risk_min=Decimal("0.10"),
            risk_max=Decimal("0.20"),
            currency="GBP",
            technology_required=technology_required,
            captured_by_id=delivery_lead.id,
            captured_at=datetime.now(timezone.utc),
            content_hash="0" * 64,
        )
        option_version.content_hash = _sha256_canonical(
            TransformationOptionService.reconstruct_canonical_version(option_version)
        )
        db_session.add(option_version)
        db_session.flush()
        brief = DecisionBrief(
            organization_id=organization.id,
            workstream_id=workstream.id,
            title="Consolidation decision",
            recommendation_option_id=option.id,
            decision_authority_id=delivery_lead.id,
            unknown_codes=[],
            conflicts=["Cutover window remains a governed constraint"],
            expected_impacts=["Lower run cost"],
            status="frozen",
        )
        db_session.add(brief)
        db_session.flush()
        frozen_payload = {
            "programme_id": programme.id,
            "workstream_id": workstream.id,
            "objective": workstream.objective,
            "scope_expression": workstream.scope_expression,
            "conflicts": list(brief.conflicts),
            "option_exception": None,
            "evidence": [{"id": 701, "claim_key": "service_continuity"}],
            "outcomes": [{"id": outcome.id, "statement": outcome.statement}],
            "measures": [{"id": measure.id, "metric_name": measure.metric_name}],
        }
        version = DecisionBriefVersion(
            organization_id=organization.id,
            brief_id=brief.id,
            workstream_id=workstream.id,
            version=1,
            source_revision=1,
            frozen_payload=frozen_payload,
            recommendation_option_version_id=option_version.id,
            option_version_ids=[option_version.id],
            cited_evidence_ids=[701],
            outcome_ids=[outcome.id],
            measure_ids=[measure.id],
            policy_version="transformation-r1.1",
            created_by_id=delivery_lead.id,
            created_at=datetime.now(timezone.utc),
            content_hash="0" * 64,
            canonical_document="pending",
            submitted_by_id=delivery_lead.id,
            submitter_authorized=True,
            decision_authority_id=delivery_lead.id,
            human_reviewed_ai=True,
            blockers_cleared=True,
            unknowns_acknowledged=True,
        )
        version.canonical_document = _canonical_json(
            DecisionBriefService.reconstruct_canonical_payload(version)
        )
        version.content_hash = hashlib.sha256(
            version.canonical_document.encode("utf-8")
        ).hexdigest()
        db_session.add(version)
        db_session.flush()
        review_number = f"TR-{uuid4().hex[:12]}"
        cycle = ARBReviewCycle(
            organization_id=organization.id,
            subject_type="decision_brief",
            subject_id=brief.id,
            decision_brief_id=brief.id,
            decision_brief_version_id=version.id,
            review_number=review_number,
            cycle_number=1,
            status="approved",
            closed_at=datetime.now(timezone.utc),
            terminal_outcome="approved",
        )
        db_session.add(cycle)
        db_session.flush()
        review = ARBReviewItem(
            organization_id=organization.id,
            review_number=review_number,
            title=brief.title,
            description="Frozen decision review",
            review_type="architecture_change",
            subject_type="decision_brief",
            subject_id=brief.id,
            decision_brief_id=brief.id,
            decision_brief_version_id=version.id,
            review_cycle_id=cycle.id,
            status="approved",
            submitter_id=outcome_owner.id,
            submitted_at=datetime.now(),
            decision="approved",
            decision_rationale="Approved from the frozen evidence",
            decision_date=datetime.now(),
            decided_by_id=delivery_lead.id,
        )
        db_session.add(review)
        db_session.flush()
        assert DecisionBriefService.verify_hash(version)
        assert TransformationOptionService.verify_version_hash(option_version)
        actor = ActorContext(
            delivery_lead.id, organization.id, frozenset(), f"req-{uuid4().hex}"
        )
        foreign_actor = ActorContext(
            foreign_user.id,
            foreign_organization.id,
            frozenset(),
            f"req-{uuid4().hex}",
        )
        action = ApprovedAction(
            action_key=f"approved-option:{option_version.id}",
            option_version_id=option_version.id,
            title=option.title,
            owner_id=delivery_lead.id,
            start_date=date.today() + timedelta(days=7),
            target_date=date.today() + timedelta(days=90),
            scheduling_applicable=True,
        )
        return ExecutionScope(
            actor,
            ActorContext(
                outcome_owner.id,
                organization.id,
                frozenset(),
                f"req-{uuid4().hex}",
            ),
            foreign_actor,
            programme.id,
            workstream.id,
            version.id,
            option_version.id,
            action,
        )

    return make


def _install_command_harness(monkeypatch, session):
    cache = {}

    def execute(**kwargs):
        key = (
            kwargs["operation"],
            kwargs["natural_key"],
            canonical_request_digest(kwargs["payload"]),
        )
        kwargs["authorizer"](
            session,
            kwargs["actor"],
            kwargs["operation"],
            kwargs["natural_key"],
        )
        if key in cache:
            prior = cache[key]
            return CommandResult(
                False,
                True,
                prior.operation_result_id,
                prior.object_ids,
                prior.response,
            )
        claim = CommandClaim(
            1, 1, "c" * 64, key[2], kwargs["natural_key"], "{}", "d" * 64
        )
        with session.begin_nested():
            mutation = kwargs["handler"](session, claim)
            session.flush()
        result = CommandResult(
            True,
            False,
            len(cache) + 1,
            mutation.object_ids,
            mutation.response,
        )
        cache[key] = result
        return result

    monkeypatch.setattr(
        "app.modules.transformation_room.execution_service.CommandService.execute",
        execute,
    )


@pytest.fixture
def committed_execution_scope(app):
    """A committed isolated schema visible to production CommandService sessions."""
    schema = f"test_task9_command_{uuid4().hex[:12]}"
    with app.app_context():
        public_engine = db.engine
        with public_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        isolated_engine = create_engine(
            public_engine.url,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        original_engine = db.engines[None]
        db.session.remove()
        db.engines[None] = isolated_engine
        try:
            table_names = (
                "organizations",
                "soc2_audit_log",
                "roles",
                "users",
                # Authenticated requests look their session up here, so a login
                # made against this schema needs the table.
                "user_sessions",
                "strategic_initiatives",
                "programme_workstreams",
                "programme_role_assignments",
                "programme_outcome_commitments",
                "measure_definitions",
                "application_components",
                "transformation_candidates",
                "candidate_overlap_dispositions",
                "candidate_signals",
                "evidence_records",
                "evidence_claim_heads",
                "evidence_requests",
                "transformation_options",
                "transformation_option_versions",
                "decision_briefs",
                "decision_brief_versions",
                "arb_review_cycles",
                "arb_review_items",
                "archimate_elements",
                "work_packages",
                "strategic_roadmap_items",
                "benefits",
                "solutions",
                "solution_analysis_sessions",
                "command_idempotency_records",
                "operation_results",
                "command_materialisations",
                "transformation_outbox_events",
                "delivery_export_attempts",
                "outcome_measurements",
                "arb_canonical_conditions",
            )
            # LIKE copies the canonical table shapes, checks, defaults and indexes
            # without copying rows or foreign keys to unrelated public relations.
            with isolated_engine.begin() as connection:
                for table_name in table_names:
                    connection.execute(
                        text(
                            f'CREATE TABLE "{table_name}" '
                            f'(LIKE public."{table_name}" INCLUDING ALL)'
                        )
                    )
            ensure_transformation_db_guards(db.session.connection())
            db.session.commit()
            session = db.session
            with session.begin():
                suffix = uuid4().hex
                organization = Organization(
                    name=f"Task 9 committed {suffix}",
                    slug=f"task9-committed-{suffix}",
                )
                foreign_organization = Organization(
                    name=f"Task 9 foreign {suffix}",
                    slug=f"task9-foreign-{suffix}",
                )
                session.add_all([organization, foreign_organization])
                session.flush()
                delivery_lead = _user(
                    session, organization.id, "application_architect"
                )
                outcome_owner = _user(
                    session, organization.id, "business_architect"
                )
                foreign_user = _user(
                    session, foreign_organization.id, "application_architect"
                )
                programme = StrategicInitiative(
                    organization_id=organization.id,
                    name="Committed execution programme",
                    record_kind="transformation_programme",
                    status="in_progress",
                    owner_id=delivery_lead.id,
                )
                session.add(programme)
                session.flush()
                workstream = ProgrammeWorkstream(
                    organization_id=organization.id,
                    programme_id=programme.id,
                    workstream_type="application_rationalisation",
                    objective="Execute the frozen option",
                    scope_expression={"application_ids": [41]},
                    lifecycle_stage="approved",
                    lead_id=delivery_lead.id,
                )
                session.add(workstream)
                session.flush()
                session.add(
                    ProgrammeRoleAssignment(
                        organization_id=organization.id,
                        programme_id=programme.id,
                        workstream_id=workstream.id,
                        user_id=delivery_lead.id,
                        role="delivery_lead",
                        effective_from=date.today() - timedelta(days=1),
                        assigned_by_id=delivery_lead.id,
                    )
                )
                outcome = ProgrammeOutcomeCommitment(
                    organization_id=organization.id,
                    programme_id=programme.id,
                    workstream_id=workstream.id,
                    statement="Reduce annual run cost",
                    owner_id=outcome_owner.id,
                    improvement_direction="decrease",
                    target_date=date.today() + timedelta(days=180),
                    lifecycle="committed",
                )
                session.add(outcome)
                session.flush()
                measure = MeasureDefinition(
                    organization_id=organization.id,
                    outcome_commitment_id=outcome.id,
                    metric_name="Annual run cost",
                    unit="GBP/year",
                    currency="GBP",
                    aggregation="latest",
                    baseline_amount=Decimal("1000.00"),
                    target_amount=Decimal("750.00"),
                    baseline_date=date.today(),
                    target_date=outcome.target_date,
                    cadence="quarterly",
                    source_adapter="finance-ledger",
                    source_key="run-cost",
                )
                option = TransformationOption(
                    organization_id=organization.id,
                    workstream_id=workstream.id,
                    title="Controlled consolidation",
                    action_type="consolidate",
                    description="Consolidate the cited application estate",
                    assumptions=["Service owners approve the cutover window"],
                    dependencies=["Accepted service continuity evidence"],
                    impacts=["Lower run cost"],
                    risks=["Cutover interruption"],
                    reversibility="Rollback during the controlled window",
                    transition_approach="Phased consolidation",
                    affected_capability_ids=[17],
                    affected_value_stream_ids=[23],
                    recommendation_rationale="Best evidence-backed balance",
                    cost_min=Decimal("100.00"),
                    cost_max=Decimal("200.00"),
                    benefit_min=Decimal("250.00"),
                    benefit_max=Decimal("300.00"),
                    risk_min=Decimal("0.10"),
                    risk_max=Decimal("0.20"),
                    currency="GBP",
                    technology_required=True,
                )
                session.add_all([measure, option])
                session.flush()
                captured_at = datetime.now(timezone.utc)
                option_version = TransformationOptionVersion(
                    organization_id=organization.id,
                    option_id=option.id,
                    workstream_id=workstream.id,
                    version=1,
                    source_revision=1,
                    content_json={
                        "title": option.title,
                        "action_type": option.action_type,
                        "description": option.description,
                        "assumptions": list(option.assumptions),
                        "dependencies": list(option.dependencies),
                        "impacts": list(option.impacts),
                        "risks": list(option.risks),
                        "transition_approach": option.transition_approach,
                    },
                    cost_min=Decimal("100.00"),
                    cost_max=Decimal("200.00"),
                    benefit_min=Decimal("250.00"),
                    benefit_max=Decimal("300.00"),
                    risk_min=Decimal("0.10"),
                    risk_max=Decimal("0.20"),
                    currency="GBP",
                    technology_required=True,
                    captured_by_id=delivery_lead.id,
                    captured_at=captured_at,
                    content_hash="0" * 64,
                )
                option_version.content_hash = _sha256_canonical(
                    TransformationOptionService.reconstruct_canonical_version(
                        option_version
                    )
                )
                session.add(option_version)
                session.flush()
                brief = DecisionBrief(
                    organization_id=organization.id,
                    workstream_id=workstream.id,
                    title="Committed consolidation decision",
                    recommendation_option_id=option.id,
                    decision_authority_id=delivery_lead.id,
                    unknown_codes=[],
                    conflicts=["Cutover window remains governed"],
                    expected_impacts=["Lower run cost"],
                    status="frozen",
                )
                session.add(brief)
                session.flush()
                created_at = datetime.now(timezone.utc)
                version = DecisionBriefVersion(
                    organization_id=organization.id,
                    brief_id=brief.id,
                    workstream_id=workstream.id,
                    version=1,
                    source_revision=1,
                    frozen_payload={
                        "programme_id": programme.id,
                        "workstream_id": workstream.id,
                        "objective": workstream.objective,
                        "scope_expression": workstream.scope_expression,
                        "conflicts": list(brief.conflicts),
                        "option_exception": None,
                        "evidence": [{"id": 701, "claim_key": "continuity"}],
                        "outcomes": [{"id": outcome.id, "statement": outcome.statement}],
                        "measures": [{"id": measure.id, "metric_name": measure.metric_name}],
                    },
                    recommendation_option_version_id=option_version.id,
                    option_version_ids=[option_version.id],
                    cited_evidence_ids=[701],
                    outcome_ids=[outcome.id],
                    measure_ids=[measure.id],
                    policy_version="transformation-r1.1",
                    created_by_id=delivery_lead.id,
                    created_at=created_at,
                    content_hash="0" * 64,
                    canonical_document="pending",
                    submitted_by_id=delivery_lead.id,
                    submitter_authorized=True,
                    decision_authority_id=delivery_lead.id,
                    human_reviewed_ai=True,
                    blockers_cleared=True,
                    unknowns_acknowledged=True,
                )
                version.canonical_document = _canonical_json(
                    DecisionBriefService.reconstruct_canonical_payload(version)
                )
                version.content_hash = hashlib.sha256(
                    version.canonical_document.encode("utf-8")
                ).hexdigest()
                session.add(version)
                session.flush()
                review_number = f"TR-{uuid4().hex[:12]}"
                cycle = ARBReviewCycle(
                    organization_id=organization.id,
                    subject_type="decision_brief",
                    subject_id=brief.id,
                    decision_brief_id=brief.id,
                    decision_brief_version_id=version.id,
                    review_number=review_number,
                    cycle_number=1,
                    status="approved",
                    closed_at=datetime.now(timezone.utc),
                    terminal_outcome="approved",
                )
                session.add(cycle)
                session.flush()
                session.add(
                    ARBReviewItem(
                        organization_id=organization.id,
                        review_number=review_number,
                        title=brief.title,
                        review_type="architecture_change",
                        subject_type="decision_brief",
                        subject_id=brief.id,
                        decision_brief_id=brief.id,
                        decision_brief_version_id=version.id,
                        review_cycle_id=cycle.id,
                        status="approved",
                        submitter_id=outcome_owner.id,
                        submitted_at=datetime.now(),
                        decision="approved",
                        decision_rationale="Approved from frozen evidence",
                        decision_date=datetime.now(),
                        decided_by_id=delivery_lead.id,
                    )
                )
                session.flush()
                review = session.scalar(
                    select(ARBReviewItem).where(
                        ARBReviewItem.organization_id == organization.id,
                        ARBReviewItem.review_cycle_id == cycle.id,
                    )
                )
                now = datetime.now(timezone.utc)
                session.add(
                    ARBCondition(
                        organization_id=organization.id,
                        decision_event_id=cycle.id,
                        review_cycle_id=cycle.id,
                        review_item_id=review.id,
                        condition_number="C-1",
                        description="Operate under the approved compensating control",
                        category="delivery",
                        status="waived",
                        waived_at=now - timedelta(days=1),
                        waived_by_id=delivery_lead.id,
                        waiver_reason="Approved time-bound execution waiver",
                        waiver_expires_at=now + timedelta(hours=1),
                        compensating_control="Daily delivery assurance review",
                        legacy_lifecycle_provenance={
                            "classification": "pre_c3_waiver"
                        },
                    )
                )
                session.flush()
                scope = ExecutionScope(
                    ActorContext(
                        delivery_lead.id,
                        organization.id,
                        frozenset(),
                        f"req-{uuid4().hex}",
                    ),
                    ActorContext(
                        outcome_owner.id,
                        organization.id,
                        frozenset(),
                        f"req-{uuid4().hex}",
                    ),
                    ActorContext(
                        foreign_user.id,
                        foreign_organization.id,
                        frozenset(),
                        f"req-{uuid4().hex}",
                    ),
                    programme.id,
                    workstream.id,
                    version.id,
                    option_version.id,
                    ApprovedAction(
                        action_key=f"approved-option:{option_version.id}",
                        option_version_id=option_version.id,
                        title=option.title,
                        owner_id=delivery_lead.id,
                        start_date=date.today() + timedelta(days=7),
                        target_date=date.today() + timedelta(days=90),
                        scheduling_applicable=True,
                    ),
                )
            db.session.remove()
            yield scope
        finally:
            db.session.remove()
            db.engines[None] = original_engine
            isolated_engine.dispose()
            with public_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def _run_two_session_race(app, first, second):
    barrier = threading.Barrier(2, timeout=20)
    results = []
    errors = []

    def run(call):
        try:
            barrier.wait()
            with app.app_context():
                results.append(call())
                db.session.remove()
        except BaseException as error:  # noqa: BLE001 — preserve thread failure
            errors.append(error)

    threads = [
        threading.Thread(target=run, args=(first,)),
        threading.Thread(target=run, args=(second,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    return results


def test_production_command_fixture_has_no_public_relation_fallback(
    app, committed_execution_scope
):
    """Catches production-path tests reading colliding rows from public."""
    with app.app_context(), Session(db.engine) as session:
        schema = session.scalar(text("SELECT current_schema()"))
        search_path = session.scalar(text("SELECT current_setting('search_path')"))
        condition_schema = session.scalar(
            text(
                "SELECT namespace.nspname FROM pg_class relation "
                "JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace "
                "WHERE relation.oid=to_regclass('arb_canonical_conditions')"
            )
        )
        uuid_schema = session.scalar(
            text(
                "SELECT namespace.nspname FROM pg_proc proc "
                "JOIN pg_namespace namespace ON namespace.oid=proc.pronamespace "
                "WHERE proc.proname='gen_random_uuid' AND proc.pronargs=0 "
                "AND proc.prorettype='uuid'::regtype "
                "ORDER BY (namespace.nspname='pg_catalog') DESC, "
                "(namespace.nspname='public') DESC, namespace.nspname LIMIT 1"
            )
        )
        challenge_body = session.scalar(
            text(
                "SELECT proc.prosrc FROM pg_proc proc "
                "JOIN pg_namespace namespace ON namespace.oid=proc.pronamespace "
                "WHERE namespace.nspname=:schema "
                "AND proc.proname='archie_issue_command_claim_challenge'"
            ),
            {"schema": schema},
        )
        issued_nonce = session.scalar(
            text(
                "SELECT claim_nonce FROM archie_issue_command_claim_challenge("
                ":organization_id, :actor_id, 1000)"
            ),
            {
                "organization_id": committed_execution_scope.actor.organization_id,
                "actor_id": committed_execution_scope.actor.user_id,
            },
        )

    assert search_path == schema
    assert condition_schema == schema
    assert uuid_schema is not None
    quoted_uuid_schema = db.engine.dialect.identifier_preparer.quote(uuid_schema)
    assert f"{quoted_uuid_schema}.gen_random_uuid()" in challenge_body
    assert str(UUID(issued_nonce)) == issued_nonce


def test_claim_challenge_qualifies_pg12_public_uuid_function_without_search_path():
    """PostgreSQL 12 pgcrypto installs gen_random_uuid outside pg_catalog."""

    class Result:
        @staticmethod
        def scalar_one_or_none():
            return "public"

    class Quoter:
        @staticmethod
        def quote(value):
            return f'"{value}"'

    class Connection:
        dialect = SimpleNamespace(identifier_preparer=Quoter())

        @staticmethod
        def exec_driver_sql(statement):
            assert "proc.proname = 'gen_random_uuid'" in statement
            assert "extension.extname = 'pgcrypto'" in statement
            return Result()

    rendered = _render_guard_sql(
        Connection(), _ISSUE_COMMAND_CLAIM_CHALLENGE_SQL, '"isolated"'
    )

    assert '"public"."gen_random_uuid"()' in rendered
    assert 'SET search_path = pg_catalog, "isolated"' in rendered
    assert 'SET search_path = pg_catalog, "isolated", public' not in rendered


def test_export_revalidates_provider_key_width_after_unicode_normalisation():
    actor = ActorContext(1, 1, frozenset(), "provider-key-width")

    with pytest.raises(ValueError, match="invalid provider_key"):
        TransformationExecutionService.export_work_package(
            actor=actor,
            work_package_id=1,
            provider_key="İ" * 120,
            request={},
            exporter=lambda *_args: {"external_key": "unused"},
            command_key="provider-key-width",
        )


def test_command_service_classifies_postgresql_deadlock_as_retryable(monkeypatch):
    """A known pre-commit PostgreSQL conflict must release the durable claim."""
    claim = CommandClaim(1, 1, "a" * 64, "b" * 64, "natural", "{}", "c" * 64)
    actor = ActorContext(1, 1, frozenset(), "request")
    marked = []

    class DeadlockDetected(Exception):
        pgcode = "40P01"

    monkeypatch.setattr(
        CommandService,
        "claim_or_reconcile",
        classmethod(lambda cls, **kwargs: claim),
    )

    def fail_claim(cls, **kwargs):
        raise DBAPIError("SELECT 1", {}, DeadlockDetected("deadlock"), False)

    monkeypatch.setattr(CommandService, "_execute_claim", classmethod(fail_claim))
    monkeypatch.setattr(
        CommandService,
        "mark_retryable",
        classmethod(lambda cls, **kwargs: marked.append(kwargs) or True),
    )

    with pytest.raises(KnownPreCommitTransient, match="database_transaction_retry"):
        CommandService.execute(
            actor=actor,
            operation="outcome.measure",
            idempotency_key="deadlock",
            payload={"fact": 1},
            natural_key="natural",
            authorizer=lambda *_args: None,
            handler=lambda *_args: None,
        )

    assert marked == [
        {
            "actor": actor,
            "claim": claim,
            "error_class": "PostgreSQLTransient:40P01",
        }
    ]


def test_exact_materialisation_replay_survives_later_terminal_and_expired_state(
    app, committed_execution_scope
):
    """Catches mutable lifecycle/waiver checks rejecting an immutable replay."""
    scope = committed_execution_scope
    with app.app_context():
        first = TransformationExecutionService.materialise(
            actor=scope.actor,
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(scope.action,),
            command_key=f"materialise-first-{uuid4().hex}",
        )
        db.session.remove()
        with Session(db.engine) as session, session.begin():
            programme = session.get(StrategicInitiative, scope.programme_id)
            workstream = session.get(ProgrammeWorkstream, scope.workstream_id)
            condition = session.scalar(
                select(ARBCondition).where(
                    ARBCondition.organization_id == scope.actor.organization_id
                )
            )
            programme.status = "archived"
            programme.archived_at = datetime.now()
            workstream.lifecycle_stage = "completed"
            workstream.archived_at = datetime.now()
            condition.waiver_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        replay = TransformationExecutionService.materialise(
            actor=replace(scope.actor, request_id=f"replay-{uuid4().hex}"),
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(scope.action,),
            command_key=f"materialise-replay-{uuid4().hex}",
        )
        assert replay.idempotent is True
        assert replay.object_ids == first.object_ids

        altered = replace(scope.action, target_date=scope.action.target_date + timedelta(days=1))
        with pytest.raises(CommandConflict, match="execution_aggregate_not_approved"):
            TransformationExecutionService.materialise(
                actor=replace(scope.actor, request_id=f"altered-{uuid4().hex}"),
                decision_brief_version_id=scope.decision_brief_version_id,
                actions=(altered,),
                command_key=f"materialise-altered-{uuid4().hex}",
            )


def test_production_command_service_two_session_races_replay_canonical_ids(
    app, monkeypatch, committed_execution_scope
):
    """Exercise real claims, fences, envelopes and natural-key reconciliation."""
    scope = committed_execution_scope
    original_materialise = TransformationExecutionService._materialise_locked
    handler_barrier = threading.Barrier(2, timeout=20)

    def racing_materialise(*args, **kwargs):
        handler_barrier.wait()
        return original_materialise(*args, **kwargs)

    monkeypatch.setattr(
        TransformationExecutionService, "_materialise_locked", racing_materialise
    )
    materialised = _run_two_session_race(
        app,
        lambda: TransformationExecutionService.materialise(
            actor=replace(scope.actor, request_id=f"race-a-{uuid4().hex}"),
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(scope.action,),
            command_key=f"materialise-a-{uuid4().hex}",
        ),
        lambda: TransformationExecutionService.materialise(
            actor=replace(scope.actor, request_id=f"race-b-{uuid4().hex}"),
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(scope.action,),
            command_key=f"materialise-b-{uuid4().hex}",
        ),
    )
    monkeypatch.setattr(
        TransformationExecutionService, "_materialise_locked", original_materialise
    )
    assert materialised[0].object_ids == materialised[1].object_ids
    assert sorted((row.created, row.idempotent) for row in materialised) == [
        (False, True),
        (True, False),
    ]

    original_solution = TransformationExecutionService._create_solution_if_required
    handler_barrier = threading.Barrier(2, timeout=20)

    def racing_solution(*args, **kwargs):
        handler_barrier.wait()
        return original_solution(*args, **kwargs)

    monkeypatch.setattr(
        TransformationExecutionService,
        "_create_solution_if_required",
        racing_solution,
    )
    solutions = _run_two_session_race(
        app,
        lambda: TransformationExecutionService.create_technology_solution(
            actor=replace(scope.actor, request_id=f"solution-a-{uuid4().hex}"),
            decision_brief_version_id=scope.decision_brief_version_id,
            option_version_id=scope.option_version_id,
            command_key=f"solution-a-{uuid4().hex}",
        ),
        lambda: TransformationExecutionService.create_technology_solution(
            actor=replace(scope.actor, request_id=f"solution-b-{uuid4().hex}"),
            decision_brief_version_id=scope.decision_brief_version_id,
            option_version_id=scope.option_version_id,
            command_key=f"solution-b-{uuid4().hex}",
        ),
    )
    monkeypatch.setattr(
        TransformationExecutionService,
        "_create_solution_if_required",
        original_solution,
    )
    assert solutions[0].object_ids == solutions[1].object_ids

    benefit_id = materialised[0].object_ids["benefit_ids"][0]
    observed_at = datetime.now(timezone.utc)
    original_measure = OutcomeMeasurementService._append_measurement_and_project
    handler_barrier = threading.Barrier(2, timeout=20)

    def racing_measure(*args, **kwargs):
        handler_barrier.wait()
        return original_measure(*args, **kwargs)

    monkeypatch.setattr(
        OutcomeMeasurementService, "_append_measurement_and_project", racing_measure
    )
    measurements = _run_two_session_race(
        app,
        lambda: OutcomeMeasurementService.record(
            actor=replace(scope.outcome_actor, request_id=f"measure-a-{uuid4().hex}"),
            benefit_id=benefit_id,
            value=Decimal("800.00"),
            unavailable_reason=None,
            observed_at=observed_at,
            source_identity="finance-ledger:q4-close",
            source_version="v4",
            command_key=f"measure-a-{uuid4().hex}",
        ),
        lambda: OutcomeMeasurementService.record(
            actor=replace(scope.outcome_actor, request_id=f"measure-b-{uuid4().hex}"),
            benefit_id=benefit_id,
            value=Decimal("800.00"),
            unavailable_reason=None,
            observed_at=observed_at,
            source_identity="finance-ledger:q4-close",
            source_version="v4",
            command_key=f"measure-b-{uuid4().hex}",
        ),
    )
    assert measurements[0].object_ids == measurements[1].object_ids
    with Session(db.engine) as session:
        assert session.scalar(
            select(func.count()).select_from(OutcomeMeasurement).where(
                OutcomeMeasurement.organization_id == scope.actor.organization_id,
                OutcomeMeasurement.benefit_id == benefit_id,
            )
        ) == 1

    work_package_id = materialised[0].object_ids["work_package_ids"][0]
    original_prepare = TransformationExecutionService._prepare_export_locked
    handler_barrier = threading.Barrier(2, timeout=20)

    def racing_prepare(*args, **kwargs):
        handler_barrier.wait()
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr(
        TransformationExecutionService, "_prepare_export_locked", racing_prepare
    )
    provider_keys = []

    def provider(_work_package, _request, provider_idempotency_key):
        provider_keys.append(provider_idempotency_key)
        return {"external_key": "ARCH-RACE"}

    exports = _run_two_session_race(
        app,
        lambda: TransformationExecutionService.export_work_package(
            actor=replace(scope.actor, request_id=f"export-a-{uuid4().hex}"),
            work_package_id=work_package_id,
            provider_key="delivery-provider",
            request={"project": "ARCH-RACE"},
            exporter=provider,
            command_key=f"export-a-{uuid4().hex}",
        ),
        lambda: TransformationExecutionService.export_work_package(
            actor=replace(scope.actor, request_id=f"export-b-{uuid4().hex}"),
            work_package_id=work_package_id,
            provider_key="delivery-provider",
            request={"project": "ARCH-RACE"},
            exporter=provider,
            command_key=f"export-b-{uuid4().hex}",
        ),
    )
    assert exports[0].object_ids == exports[1].object_ids
    assert len(provider_keys) == 1
    with Session(db.engine) as session:
        assert session.scalar(
            select(func.count()).select_from(DeliveryExportAttempt).where(
                DeliveryExportAttempt.organization_id
                == scope.actor.organization_id,
                DeliveryExportAttempt.work_package_id == work_package_id,
            )
        ) == 1


def test_sibling_benefit_measurements_lock_outcome_before_benefits_without_deadlock(
    app, monkeypatch, committed_execution_scope
):
    """Catches Benefit-first sibling locks deadlocking concurrent ingestion."""
    scope = committed_execution_scope
    with app.app_context():
        materialised = TransformationExecutionService.materialise(
            actor=scope.actor,
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(scope.action,),
            command_key=f"sibling-materialise-{uuid4().hex}",
        )
        first_id = materialised.object_ids["benefit_ids"][0]
        db.session.remove()
        with Session(db.engine) as session, session.begin():
            first = session.get(Benefit, first_id)
            second = Benefit(
                organization_id=first.organization_id,
                name="Service continuity",
                status="realising",
                measure="Unplanned outage minutes",
                unit="minutes/year",
                baseline_value=Decimal("100.00"),
                target_value=Decimal("50.00"),
                owner_id=first.owner_id,
                strategic_initiative_id=first.strategic_initiative_id,
                programme_workstream_id=first.programme_workstream_id,
                outcome_commitment_id=first.outcome_commitment_id,
                work_package_id=first.work_package_id,
                materialisation_key=uuid4().hex + uuid4().hex,
            )
            session.add(second)
            session.flush()
            second_id = second.id
            outcome_id = first.outcome_commitment_id

    original_load = OutcomeMeasurementService._load_benefit
    benefit_lock_barrier = threading.Barrier(2, timeout=20)

    def expose_old_lock_inversion(session, actor, benefit_id, *, lock):
        row = original_load(session, actor, benefit_id, lock=lock)
        if lock and benefit_id in {first_id, second_id}:
            benefit_lock_barrier.wait()
        return row

    monkeypatch.setattr(
        OutcomeMeasurementService, "_load_benefit", expose_old_lock_inversion
    )
    observed_at = datetime.now(timezone.utc)
    measured = _run_two_session_race(
        app,
        lambda: OutcomeMeasurementService.record(
            actor=replace(scope.outcome_actor, request_id=f"sibling-a-{uuid4().hex}"),
            benefit_id=first_id,
            value=Decimal("700.00"),
            unavailable_reason=None,
            observed_at=observed_at,
            source_identity="finance-ledger:sibling-a",
            source_version="v1",
            command_key=f"sibling-a-{uuid4().hex}",
        ),
        lambda: OutcomeMeasurementService.record(
            actor=replace(scope.outcome_actor, request_id=f"sibling-b-{uuid4().hex}"),
            benefit_id=second_id,
            value=Decimal("75.00"),
            unavailable_reason=None,
            observed_at=observed_at,
            source_identity="availability:sibling-b",
            source_version="v1",
            command_key=f"sibling-b-{uuid4().hex}",
        ),
    )

    assert len(measured) == 2
    with Session(db.engine) as session:
        assert session.get(ProgrammeOutcomeCommitment, outcome_id).lifecycle == "not_realised"
        assert session.scalar(
            select(func.count()).select_from(OutcomeMeasurement).where(
                OutcomeMeasurement.organization_id == scope.actor.organization_id,
                OutcomeMeasurement.benefit_id.in_((first_id, second_id)),
            )
        ) == 2


def test_export_attempt_and_outbox_commit_before_provider_and_crash_replays(
    app, monkeypatch, committed_execution_scope
):
    scope = committed_execution_scope
    app.config["TRANSFORMATION_EXPORT_DISPATCH_LEASE_SECONDS"] = 0.05
    with app.app_context():
        materialised = TransformationExecutionService.materialise(
            actor=scope.actor,
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(scope.action,),
            command_key=f"export-materialise-{uuid4().hex}",
        )
    work_package_id = materialised.object_ids["work_package_ids"][0]
    provider_keys = []

    def provider(_work_package, _request, provider_idempotency_key):
        with Session(db.engine) as session:
            attempt = session.scalar(
                select(DeliveryExportAttempt)
                .where(
                    DeliveryExportAttempt.organization_id
                    == scope.actor.organization_id,
                    DeliveryExportAttempt.work_package_id == work_package_id,
                )
                .order_by(DeliveryExportAttempt.id.desc())
            )
            assert attempt is not None and attempt.status == "in_progress"
            assert session.scalar(
                select(func.count()).select_from(OperationOutboxEvent).where(
                    OperationOutboxEvent.organization_id
                    == scope.actor.organization_id,
                    OperationOutboxEvent.event_type
                    == "transformation.delivery_export_requested",
                )
            ) == len(provider_keys) + 1
            prepare_receipt = session.scalar(
                select(CommandIdempotencyRecord).where(
                    CommandIdempotencyRecord.organization_id
                    == scope.actor.organization_id,
                    CommandIdempotencyRecord.operation
                    == TransformationExecutionService.EXPORT_OPERATION,
                )
            )
            assert prepare_receipt is not None
            assert prepare_receipt.status == "succeeded"
            assert prepare_receipt.lease_expires_at is None
        provider_keys.append(provider_idempotency_key)
        return {"external_key": "ARCH-42"}

    crashes = iter((True, False))

    def crash_after_provider(*_args, **_kwargs):
        if next(crashes):
            raise RuntimeError("crash after provider success")

    monkeypatch.setattr(
        TransformationExecutionService,
        "_after_provider_before_finalise",
        staticmethod(crash_after_provider),
        raising=False,
    )
    with app.app_context(), pytest.raises(
        RuntimeError, match="crash after provider success"
    ):
        TransformationExecutionService.export_work_package(
            actor=scope.actor,
            work_package_id=work_package_id,
            provider_key="delivery-provider",
            request={"project": "ARCH"},
            exporter=provider,
            command_key="durable-export-root",
        )
    with Session(db.engine) as session:
        pending = session.scalar(
            select(DeliveryExportAttempt).where(
                DeliveryExportAttempt.organization_id
                == scope.actor.organization_id,
                DeliveryExportAttempt.work_package_id == work_package_id,
            )
        )
        assert pending is not None and pending.status == "in_progress"

    with app.app_context():
        replay = TransformationExecutionService.export_work_package(
            actor=replace(scope.actor, request_id=f"export-retry-{uuid4().hex}"),
            work_package_id=work_package_id,
            provider_key="delivery-provider",
            request={"project": "ARCH"},
            exporter=provider,
            command_key="durable-export-root",
        )
    assert replay.response["exported"] is True
    assert len(provider_keys) == 2 and len(set(provider_keys)) == 1
    with Session(db.engine) as session:
        attempts = tuple(
            session.scalars(
                select(DeliveryExportAttempt).where(
                    DeliveryExportAttempt.organization_id
                    == scope.actor.organization_id,
                    DeliveryExportAttempt.work_package_id == work_package_id,
                )
            ).all()
        )
        assert len(attempts) == 2
        attempts = tuple(sorted(attempts, key=lambda row: row.id))
        assert [row.status for row in attempts] == ["indeterminate", "succeeded"]
        assert attempts[1].predecessor_attempt_id == attempts[0].id
        assert attempts[0].attempt_key != attempts[1].attempt_key
        assert {
            row.provider_idempotency_key for row in attempts
        } == {provider_keys[0]}


def test_premature_export_recovery_remains_retryable_after_database_lease_expiry(
    app, monkeypatch, committed_execution_scope
):
    scope = committed_execution_scope
    monkeypatch.setitem(
        app.config, "TRANSFORMATION_EXPORT_DISPATCH_LEASE_SECONDS", 30.0
    )
    with app.app_context():
        materialised = TransformationExecutionService.materialise(
            actor=scope.actor,
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(scope.action,),
            command_key=f"early-recovery-materialise-{uuid4().hex}",
        )
    work_package_id = materialised.object_ids["work_package_ids"][0]
    provider_keys = []

    def provider(_work_package, _request, provider_idempotency_key):
        provider_keys.append(provider_idempotency_key)
        return {"external_key": "ARCH-EARLY"}

    crashes = iter((True, False))

    def crash_once(*_args, **_kwargs):
        if next(crashes):
            raise RuntimeError("crash before early recovery")

    monkeypatch.setattr(
        TransformationExecutionService,
        "_after_provider_before_finalise",
        staticmethod(crash_once),
        raising=False,
    )
    with app.app_context(), pytest.raises(
        RuntimeError, match="crash before early recovery"
    ):
        TransformationExecutionService.export_work_package(
            actor=scope.actor,
            work_package_id=work_package_id,
            provider_key="delivery-provider",
            request={"project": "ARCH"},
            exporter=provider,
            command_key="early-recovery-root",
        )
    with Session(db.engine) as session:
        predecessor = session.scalar(
            select(DeliveryExportAttempt).where(
                DeliveryExportAttempt.organization_id == scope.actor.organization_id,
                DeliveryExportAttempt.work_package_id == work_package_id,
            )
        )
        assert predecessor is not None
        session.expunge(predecessor)

    with app.app_context(), pytest.raises(
        KnownPreCommitTransient, match="delivery_export_dispatch_still_owned"
    ):
        TransformationExecutionService._recover_export_attempt(
            actor=scope.actor, attempt=predecessor
        )
    with Session(db.engine) as session:
        recovery_receipt = session.scalar(
            select(CommandIdempotencyRecord).where(
                CommandIdempotencyRecord.organization_id
                == scope.actor.organization_id,
                CommandIdempotencyRecord.operation
                == TransformationExecutionService.EXPORT_RECOVER_OPERATION,
                CommandIdempotencyRecord.idempotency_key
                == f"delivery-export-recover:{predecessor.id}",
            )
        )
        assert recovery_receipt.status == "retryable_failure"

    # Move the durable lease across its boundary explicitly. Sleeping for a
    # 150ms lease made the preceding "still owned" assertion depend on CI host
    # speed: session setup alone could consume the entire lease.
    with db.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE delivery_export_attempts "
                "SET dispatch_lease_expires_at = clock_timestamp() - INTERVAL '1 second' "
                "WHERE id = :attempt_id AND organization_id = :organization_id"
            ),
            {
                "attempt_id": predecessor.id,
                "organization_id": scope.actor.organization_id,
            },
        )
    with app.app_context():
        recovered = TransformationExecutionService.export_work_package(
            actor=replace(scope.actor, request_id=f"recovered-{uuid4().hex}"),
            work_package_id=work_package_id,
            provider_key="delivery-provider",
            request={"project": "ARCH"},
            exporter=provider,
            command_key="early-recovery-root",
        )

    assert recovered.response["exported"] is True
    assert len(provider_keys) == 2 and len(set(provider_keys)) == 1
    with Session(db.engine) as session:
        attempts = tuple(
            session.scalars(
                select(DeliveryExportAttempt)
                .where(
                    DeliveryExportAttempt.organization_id
                    == scope.actor.organization_id,
                    DeliveryExportAttempt.work_package_id == work_package_id,
                )
                .order_by(DeliveryExportAttempt.id)
            ).all()
        )
        assert [attempt.status for attempt in attempts] == [
            "indeterminate",
            "succeeded",
        ]


def test_export_wait_uses_database_clock_when_application_clock_is_ahead(
    app, monkeypatch, committed_execution_scope
):
    scope = committed_execution_scope
    monkeypatch.setitem(
        app.config, "TRANSFORMATION_EXPORT_DISPATCH_LEASE_SECONDS", 0.12
    )
    with app.app_context():
        materialised = TransformationExecutionService.materialise(
            actor=scope.actor,
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(scope.action,),
            command_key=f"skew-materialise-{uuid4().hex}",
        )
    work_package_id = materialised.object_ids["work_package_ids"][0]
    provider_keys = []

    def provider(_work_package, _request, provider_idempotency_key):
        provider_keys.append(provider_idempotency_key)
        return {"external_key": "ARCH-SKEW"}

    crashes = iter((True, False))

    def crash_once(*_args, **_kwargs):
        if next(crashes):
            raise RuntimeError("crash before skewed replay")

    monkeypatch.setattr(
        TransformationExecutionService,
        "_after_provider_before_finalise",
        staticmethod(crash_once),
        raising=False,
    )
    with app.app_context(), pytest.raises(
        RuntimeError, match="crash before skewed replay"
    ):
        TransformationExecutionService.export_work_package(
            actor=scope.actor,
            work_package_id=work_package_id,
            provider_key="delivery-provider",
            request={"project": "ARCH"},
            exporter=provider,
            command_key="skewed-clock-root",
        )

    class FutureApplicationClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=1)

    monkeypatch.setattr(
        "app.modules.transformation_room.execution_service.datetime",
        FutureApplicationClock,
    )
    with app.app_context():
        recovered = TransformationExecutionService.export_work_package(
            actor=replace(scope.actor, request_id=f"skew-retry-{uuid4().hex}"),
            work_package_id=work_package_id,
            provider_key="delivery-provider",
            request={"project": "ARCH"},
            exporter=provider,
            command_key="skewed-clock-root",
        )

    assert recovered.response["exported"] is True
    assert len(provider_keys) == 2 and len(set(provider_keys)) == 1


def test_materialise_creates_one_canonical_set_replays_and_never_implies_solution(
    db_session, monkeypatch, make_execution_scope
):
    scope = make_execution_scope(technology_required=False)
    _install_command_harness(monkeypatch, db_session)

    first = TransformationExecutionService.materialise(
        actor=scope.actor,
        decision_brief_version_id=scope.decision_brief_version_id,
        actions=(scope.action,),
        command_key="materialise-1",
    )
    replay = TransformationExecutionService.materialise(
        actor=scope.actor,
        decision_brief_version_id=scope.decision_brief_version_id,
        actions=(scope.action,),
        command_key="materialise-2",
    )

    work_packages = db_session.scalars(
        select(WorkPackage).where(
            WorkPackage.organization_id == scope.actor.organization_id,
            WorkPackage.decision_brief_version_id == scope.decision_brief_version_id,
        )
    ).all()
    roadmap_items = db_session.scalars(
        select(RoadmapItem).where(
            RoadmapItem.organization_id == scope.actor.organization_id,
            RoadmapItem.decision_brief_version_id == scope.decision_brief_version_id,
        )
    ).all()
    benefits = db_session.scalars(
        select(Benefit).where(
            Benefit.organization_id == scope.actor.organization_id,
            Benefit.decision_brief_version_id == scope.decision_brief_version_id,
        )
    ).all()
    solutions = db_session.scalar(
        select(func.count()).select_from(Solution).where(
            Solution.organization_id == scope.actor.organization_id,
            Solution.workstream_id == scope.workstream_id,
        )
    )
    workstream = db_session.get(ProgrammeWorkstream, scope.workstream_id)

    assert first.created is True and replay.idempotent is True
    assert replay.object_ids == first.object_ids
    assert len(work_packages) == len(roadmap_items) == len(benefits) == 1
    assert solutions == 0
    assert work_packages[0].enterprise_initiative_id is None
    assert work_packages[0].strategic_initiative_id == scope.programme_id
    assert roadmap_items[0].work_package_id == work_packages[0].id
    assert benefits[0].work_package_id == work_packages[0].id
    assert benefits[0].legacy_enterprise_initiative_id is None
    assert work_packages[0].archimate_element_id is not None
    assert (
        db_session.get(ArchiMateElement, work_packages[0].archimate_element_id).type
        == "WorkPackage"
    )
    assert workstream.lifecycle_stage == "execute"

    workstream.lifecycle_stage = "completed"
    db_session.flush()
    assert db_session.scalar(
        select(func.count()).select_from(Solution).where(
            Solution.organization_id == scope.actor.organization_id,
            Solution.workstream_id == scope.workstream_id,
        )
    ) == 0


@pytest.mark.parametrize("title_length", (101, 255))
def test_materialisation_preserves_upstream_valid_option_title_width(
    db_session, monkeypatch, make_execution_scope, title_length
):
    title = "T" * title_length
    scope = make_execution_scope(option_title=title)
    _install_command_harness(monkeypatch, db_session)

    materialised = TransformationExecutionService.materialise(
        actor=scope.actor,
        decision_brief_version_id=scope.decision_brief_version_id,
        actions=(scope.action,),
        command_key=f"wide-option-title-{title_length}",
    )

    work_package = db_session.get(
        WorkPackage, materialised.object_ids["work_package_ids"][0]
    )
    roadmap_item = db_session.get(
        RoadmapItem, materialised.object_ids["roadmap_item_ids"][0]
    )
    archimate_element = db_session.get(
        ArchiMateElement, work_package.archimate_element_id
    )
    assert work_package.name == title
    assert roadmap_item.title == title
    assert archimate_element.name == f"{title[:99]}…"
    assert archimate_element.custom_properties["source_name"] == title


def test_materialisation_preserves_full_outcome_statement_with_bounded_display_name(
    db_session, monkeypatch, make_execution_scope
):
    statement = ("Reduce avoidable annual application run cost without service impact. " * 10)[
        :600
    ]
    assert len(statement) > 255
    scope = make_execution_scope(outcome_statement=statement)
    _install_command_harness(monkeypatch, db_session)

    materialised = TransformationExecutionService.materialise(
        actor=scope.actor,
        decision_brief_version_id=scope.decision_brief_version_id,
        actions=(scope.action,),
        command_key="long-outcome-statement",
    )

    benefit = db_session.get(Benefit, materialised.object_ids["benefit_ids"][0])
    assert benefit.name == f"{statement[:254]}…"
    assert len(benefit.name) == 255
    assert benefit.description == statement


def test_exact_measurement_truth_survives_incompatible_legacy_benefit_projection(
    db_session, monkeypatch, make_execution_scope
):
    """Catches six-decimal/range/unit narrowing in the compatibility Benefit."""
    scope = make_execution_scope()
    measure = db_session.scalar(
        select(MeasureDefinition).where(
            MeasureDefinition.organization_id == scope.actor.organization_id
        )
    )
    long_unit = "u" * 64
    maximum = Decimal("999999999999999999.999999")
    measure.currency = None
    measure.baseline_amount = None
    measure.target_amount = None
    measure.unit = long_unit
    measure.baseline_value = maximum
    measure.target_value = Decimal("999999999999999998.123456")
    db_session.flush()
    _install_command_harness(monkeypatch, db_session)

    materialised = TransformationExecutionService.materialise(
        actor=scope.actor,
        decision_brief_version_id=scope.decision_brief_version_id,
        actions=(scope.action,),
        command_key="wide-measure-materialise",
    )
    benefit = db_session.get(Benefit, materialised.object_ids["benefit_ids"][0])
    assert measure.unit == long_unit
    assert measure.baseline_value == maximum
    assert benefit.unit is None
    assert benefit.baseline_value is None
    assert benefit.target_value is None

    observed_at = datetime.now(timezone.utc)
    first = OutcomeMeasurementService.record(
        actor=scope.outcome_actor,
        benefit_id=benefit.id,
        value=maximum,
        unavailable_reason=None,
        observed_at=observed_at,
        source_identity="telemetry:wide-contract",
        source_version="v1",
        command_key="wide-measure-record",
    )
    replay = OutcomeMeasurementService.record(
        actor=scope.outcome_actor,
        benefit_id=benefit.id,
        value=maximum,
        unavailable_reason=None,
        observed_at=observed_at,
        source_identity="telemetry:wide-contract",
        source_version="v1",
        command_key="wide-measure-replay",
    )
    observation = db_session.get(
        OutcomeMeasurement, first.object_ids["outcome_measurement_id"]
    )

    assert observation.value == maximum
    assert benefit.actual_value is None
    assert first.response["actual_value"] == "999999999999999999.999999"
    assert replay.response == first.response


def test_governance_gate_blocks_conditional_and_expired_waiver_but_accepts_fulfilment():
    now = datetime.now(timezone.utc)
    review = SimpleNamespace(
        status="approved_with_conditions", decision="approved_with_conditions"
    )
    cycle = SimpleNamespace(
        status="approved_with_conditions",
        terminal_outcome="approved_with_conditions",
    )
    pending = SimpleNamespace(status="pending", waiver_expires_at=None)
    with pytest.raises(CommandConflict, match="execution_not_approved"):
        TransformationExecutionService._assert_governance_ready(
            cycle, review, (pending,), now
        )

    cycle.status = review.status = "approved"
    fulfilled = SimpleNamespace(status="fulfilled", waiver_expires_at=None)
    TransformationExecutionService._assert_governance_ready(
        cycle, review, (fulfilled,), now
    )

    expired = SimpleNamespace(
        status="waived", waiver_expires_at=now - timedelta(seconds=1)
    )
    with pytest.raises(CommandConflict, match="execution_conditions_unresolved"):
        TransformationExecutionService._assert_governance_ready(
            cycle, review, (expired,), now
        )


def test_solution_requires_true_technology_flag_and_explicit_command(
    db_session, monkeypatch, make_execution_scope
):
    non_technology = make_execution_scope(technology_required=False)
    _install_command_harness(monkeypatch, db_session)
    with pytest.raises(CommandConflict, match="technology_solution_not_required"):
        TransformationExecutionService.create_technology_solution(
            actor=non_technology.actor,
            decision_brief_version_id=non_technology.decision_brief_version_id,
            option_version_id=non_technology.option_version_id,
            command_key="no-technology-solution",
        )


def test_explicit_technology_solution_is_single_and_inherits_only_frozen_context(
    db_session, monkeypatch, make_execution_scope
):
    scope = make_execution_scope(technology_required=True)
    _install_command_harness(monkeypatch, db_session)

    first = TransformationExecutionService.create_technology_solution(
        actor=scope.actor,
        decision_brief_version_id=scope.decision_brief_version_id,
        option_version_id=scope.option_version_id,
        command_key="technology-solution-1",
    )
    replay = TransformationExecutionService.create_technology_solution(
        actor=scope.actor,
        decision_brief_version_id=scope.decision_brief_version_id,
        option_version_id=scope.option_version_id,
        command_key="technology-solution-2",
    )
    rows = db_session.scalars(
        select(Solution).where(
            Solution.organization_id == scope.actor.organization_id,
            Solution.workstream_id == scope.workstream_id,
        )
    ).all()

    assert first.object_ids == replay.object_ids and replay.idempotent is True
    assert len(rows) == 1
    solution = rows[0]
    assert solution.initiative_id == scope.programme_id
    assert solution.workstream_id == scope.workstream_id
    assert solution.journey_state == {
        "source": "transformation_room",
        "decision_brief_version_id": scope.decision_brief_version_id,
        "option_version_id": scope.option_version_id,
        "constraints": ["Cutover window remains a governed constraint"],
        "cited_evidence_ids": [701],
        "scope_expression": {"application_ids": [41]},
    }


def test_cross_tenant_decision_identifier_is_not_found(
    monkeypatch, db_session, make_execution_scope
):
    scope = make_execution_scope()
    _install_command_harness(monkeypatch, db_session)
    with pytest.raises(NotFound, match="decision_brief_version_not_found"):
        TransformationExecutionService.materialise(
            actor=scope.foreign_actor,
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(scope.action,),
            command_key="foreign-materialise",
        )


def test_materialisation_rejects_action_identity_and_content_not_in_frozen_option(
    db_session, monkeypatch, make_execution_scope
):
    """Catches callers inventing work after the immutable option was approved."""
    scope = make_execution_scope()
    _install_command_harness(monkeypatch, db_session)
    invented = replace(
        scope.action,
        action_key="caller-invented-action",
        title="Unapproved delivery scope",
    )
    graph = TransformationExecutionService._load_execution_graph(
        db_session, scope.actor, scope.decision_brief_version_id, lock=False
    )
    assert DecisionBriefService.verify_hash(graph.version)
    assert all(
        TransformationOptionService.verify_version_hash(row)
        for row in graph.options.values()
    )

    with pytest.raises(CommandConflict, match="approved_action_not_frozen"):
        TransformationExecutionService.materialise(
            actor=scope.actor,
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(invented,),
            command_key="invented-action",
        )


@pytest.mark.parametrize(
    "aggregate_change",
    ("workstream_completed", "workstream_archived", "programme_archived"),
)
def test_materialisation_requires_current_approved_non_archived_aggregate(
    db_session, monkeypatch, make_execution_scope, aggregate_change
):
    """Catches an old approval reopening a terminal or archived aggregate."""
    scope = make_execution_scope()
    _install_command_harness(monkeypatch, db_session)
    workstream = db_session.get(ProgrammeWorkstream, scope.workstream_id)
    programme = db_session.get(StrategicInitiative, scope.programme_id)
    if aggregate_change == "workstream_completed":
        workstream.lifecycle_stage = "completed"
    elif aggregate_change == "workstream_archived":
        workstream.archived_at = datetime.now(timezone.utc)
    else:
        programme.status = "archived"
        programme.archived_at = datetime.now(timezone.utc)
    db_session.flush()

    with pytest.raises(CommandConflict, match="execution_aggregate_not_approved"):
        TransformationExecutionService.materialise(
            actor=scope.actor,
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(scope.action,),
            command_key=f"invalid-aggregate-{aggregate_change}",
        )


@pytest.mark.parametrize(
    ("table_name", "row_id_field", "scope_id_field"),
    (
        (
            "decision_brief_versions",
            "decision_brief_version_id",
            "decision_brief_version_id",
        ),
        (
            "transformation_option_versions",
            "option_version_id",
            "option_version_id",
        ),
    ),
)
def test_materialisation_rejects_corrupt_frozen_hashes(
    db_session,
    monkeypatch,
    make_execution_scope,
    table_name,
    row_id_field,
    scope_id_field,
):
    """Catches execution trusting a modified frozen brief or option snapshot."""
    scope = make_execution_scope()
    _install_command_harness(monkeypatch, db_session)
    row_id = getattr(scope, scope_id_field)
    db_session.execute(text("SET LOCAL session_replication_role = replica"))
    db_session.execute(
        text(
            f'UPDATE "{table_name}" SET content_hash = :bad_hash '
            f'WHERE id = :row_id AND organization_id = :organization_id'
        ),
        {
            "bad_hash": "f" * 64,
            "row_id": row_id,
            "organization_id": scope.actor.organization_id,
        },
    )
    db_session.execute(text("SET LOCAL session_replication_role = origin"))
    db_session.expire_all()

    with pytest.raises(CommandConflict, match="decision_snapshot_hash_invalid"):
        TransformationExecutionService.materialise(
            actor=scope.actor,
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(scope.action,),
            command_key=f"corrupt-{row_id_field}",
        )


def test_materialisation_applies_the_locked_approved_to_execute_gate(
    db_session, monkeypatch, make_execution_scope
):
    """Catches direct lifecycle mutation that bypasses the canonical gate policy."""
    scope = make_execution_scope()
    _install_command_harness(monkeypatch, db_session)

    def blocked(_snapshot, _transition):
        return (
            [
                GateBlocker(
                    "review_test_blocker",
                    "The approved-to-execute policy blocked this transition.",
                    "workstream",
                    scope.workstream_id,
                    None,
                )
            ],
            [],
            set(),
        )

    monkeypatch.setattr(
        TransformationGateService, "evaluate_requirements", staticmethod(blocked)
    )

    with pytest.raises(BlockedByEvidence, match="gate_requirements_not_met"):
        TransformationExecutionService.materialise(
            actor=scope.actor,
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(scope.action,),
            command_key="blocked-approved-to-execute",
        )
    assert db_session.scalar(
        select(func.count()).select_from(WorkPackage).where(
            WorkPackage.organization_id == scope.actor.organization_id,
            WorkPackage.decision_brief_version_id
            == scope.decision_brief_version_id,
        )
    ) == 0


def test_text_dependencies_remain_provenance_not_canonical_work_package_ids(
    db_session, monkeypatch, make_execution_scope
):
    """Catches prose being written into the WorkPackage-ID dependency field."""
    scope = make_execution_scope()
    _install_command_harness(monkeypatch, db_session)
    result = TransformationExecutionService.materialise(
        actor=scope.actor,
        decision_brief_version_id=scope.decision_brief_version_id,
        actions=(scope.action,),
        command_key="canonical-dependencies",
    )
    work_package = db_session.get(WorkPackage, result.object_ids["work_package_ids"][0])
    element = db_session.get(ArchiMateElement, work_package.archimate_element_id)

    assert work_package.dependencies is None
    assert element.custom_properties["unresolved_dependency_claims"] == [
        "Accepted service continuity evidence"
    ]


def test_subordinate_write_failure_rolls_back_the_whole_materialisation(
    db_session, monkeypatch, make_execution_scope
):
    scope = make_execution_scope()
    _install_command_harness(monkeypatch, db_session)

    def fail_roadmap(*_args, **_kwargs):
        raise RuntimeError("forced subordinate write failure")

    monkeypatch.setattr(
        TransformationExecutionService, "_create_roadmap_item", fail_roadmap
    )
    with pytest.raises(RuntimeError, match="forced subordinate write failure"):
        TransformationExecutionService.materialise(
            actor=scope.actor,
            decision_brief_version_id=scope.decision_brief_version_id,
            actions=(scope.action,),
            command_key="rollback-materialise",
        )

    assert db_session.scalar(
        select(func.count()).select_from(WorkPackage).where(
            WorkPackage.organization_id == scope.actor.organization_id,
            WorkPackage.decision_brief_version_id == scope.decision_brief_version_id,
        )
    ) == 0
    assert db_session.scalar(
        select(func.count()).select_from(ArchiMateElement).where(
            ArchiMateElement.organization_id == scope.actor.organization_id,
            ArchiMateElement.type == "WorkPackage",
        )
    ) == 0


def test_partial_unique_key_rejects_a_concurrent_duplicate(
    db_session, monkeypatch, make_execution_scope
):
    scope = make_execution_scope()
    _install_command_harness(monkeypatch, db_session)
    result = TransformationExecutionService.materialise(
        actor=scope.actor,
        decision_brief_version_id=scope.decision_brief_version_id,
        actions=(scope.action,),
        command_key="race-winner",
    )
    winner = db_session.get(WorkPackage, result.object_ids["work_package_ids"][0])

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            WorkPackage(
                organization_id=scope.actor.organization_id,
                name="Concurrent duplicate",
                strategic_initiative_id=scope.programme_id,
                programme_workstream_id=scope.workstream_id,
                decision_brief_version_id=scope.decision_brief_version_id,
                materialisation_key=winner.materialisation_key,
            )
        )
        db_session.flush()


def test_export_failure_preserves_pending_work_and_completed_attempt_is_immutable(
    db_session, monkeypatch, make_execution_scope
):
    scope = make_execution_scope()
    _install_command_harness(monkeypatch, db_session)
    materialised = TransformationExecutionService.materialise(
        actor=scope.actor,
        decision_brief_version_id=scope.decision_brief_version_id,
        actions=(scope.action,),
        command_key="materialise-for-export",
    )
    work_package_id = materialised.object_ids["work_package_ids"][0]

    def unavailable(_work_package, _request, _provider_idempotency_key):
        raise ConnectionError("provider unavailable")

    exported = TransformationExecutionService.export_work_package(
        actor=scope.actor,
        work_package_id=work_package_id,
        provider_key="delivery-provider",
        request={"project": "ARCH"},
        exporter=unavailable,
        command_key="export-failure",
    )
    work_package = db_session.get(WorkPackage, work_package_id)
    attempt = db_session.get(
        DeliveryExportAttempt, exported.object_ids["delivery_export_attempt_id"]
    )

    assert exported.response["exported"] is False
    assert exported.response["status"] == "failed"
    assert work_package.status == "planned"
    assert attempt.status == "failed" and attempt.completed_at is not None
    with pytest.raises(
        DBAPIError, match="completed delivery export attempts are immutable"
    ), db_session.begin_nested():
        db_session.execute(
            text(
                "UPDATE delivery_export_attempts SET error_message = :message "
                "WHERE id = :attempt_id AND organization_id = :organization_id"
            ),
            {
                "message": "rewritten outside the service",
                "attempt_id": attempt.id,
                "organization_id": scope.actor.organization_id,
            },
        )
    db_session.expire(attempt)
    with pytest.raises(
        ValueError, match="completed delivery export attempts are immutable"
    ), db_session.begin_nested():
        attempt.error_message = "rewritten"
        db_session.flush()

    retried = TransformationExecutionService.export_work_package(
        actor=scope.actor,
        work_package_id=work_package_id,
        provider_key="delivery-provider",
        request={"project": "ARCH"},
        exporter=lambda _work_package, _request, _provider_idempotency_key: {
            "external_key": "ARCH-42"
        },
        command_key="export-successful-retry",
        predecessor_attempt_id=attempt.id,
    )
    retry = db_session.get(
        DeliveryExportAttempt,
        retried.object_ids["delivery_export_attempt_id"],
    )
    db_session.refresh(attempt)

    assert retried.response["exported"] is True
    assert retry.predecessor_attempt_id == attempt.id
    assert retry.status == "succeeded"
    assert retry.external_key == "ARCH-42"
    assert attempt.status == "failed"


def test_export_failure_bounds_provider_error_class_to_persisted_width(
    db_session, monkeypatch, make_execution_scope
):
    scope = make_execution_scope()
    _install_command_harness(monkeypatch, db_session)
    materialised = TransformationExecutionService.materialise(
        actor=scope.actor,
        decision_brief_version_id=scope.decision_brief_version_id,
        actions=(scope.action,),
        command_key="materialise-for-wide-provider-error",
    )
    work_package_id = materialised.object_ids["work_package_ids"][0]
    provider_error_name = "ProviderFailure" + "X" * 300
    provider_error = type(provider_error_name, (Exception,), {})

    def unavailable(_work_package, _request, _provider_idempotency_key):
        raise provider_error()

    exported = TransformationExecutionService.export_work_package(
        actor=scope.actor,
        work_package_id=work_package_id,
        provider_key="delivery-provider",
        request={"project": "ARCH"},
        exporter=unavailable,
        command_key="wide-provider-error",
    )
    attempt = db_session.get(
        DeliveryExportAttempt, exported.object_ids["delivery_export_attempt_id"]
    )

    assert attempt.status == "failed"
    assert attempt.error_class == f"{provider_error_name[:254]}…"
    assert provider_error_name in attempt.error_message
