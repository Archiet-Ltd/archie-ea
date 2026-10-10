"""PostgreSQL integration proof for the typed ARB submission command."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from flask import g
import psycopg2
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from tests._raw_psycopg2 import raw_psycopg2_connection

from app import db
from app.models.architecture_decision import ArchitectureDecision
from app.models.architecture_review_board import ARBReviewCycle, ARBReviewItem
from app.models.arb_submission_event import ARBSubmissionEvent
from app.models.arb_decision_event import (
    ARBCondition,
    ARBDecisionEvent,
    ensure_arb_decision_guards,
)
from app.models.transformation_db_guards import ensure_transformation_db_guards
from app.models.transformation_decision import ARBSubjectEvidenceSnapshot
from app.models.transformation_execution import CommandIdempotencyRecord, OperationResult
from app.models.organization import Organization
from app.models.arb_submission_evidence import (
    ARBSubmissionEvidenceSnapshot,
    WorkbenchArtifactEvidence,
    ensure_evidence_immutability_triggers,
)
from app.models.solution_architect_models import (
    DriverType,
    SolutionAnalysisSession,
    SolutionDriver,
    SolutionGoal,
    SolutionProblemDefinition,
)
from app.models.solution_lifecycle_models import SolutionRisk
from app.models.solution_models import Solution
from app.models.user import User
from app.modules.transformation_room.arb_submission_service import (
    TypedARBSubmissionService,
)
from app.modules.transformation_room.arb_decision_service import (
    TypedARBDecisionService,
)
from app.modules.transformation_room.domain import (
    ActorContext,
    CommandConflict,
    NotAuthorised,
    NotFound,
)


@dataclass(frozen=True)
class _ADRScope:
    actor: ActorContext
    organization_id: int
    user_id: int
    adr_id: int
    foreign_actor: ActorContext


@dataclass(frozen=True)
class _SolutionScope:
    actor: ActorContext
    organization_id: int
    user_id: int
    workspace_id: int
    solution_id: int


@pytest.fixture(scope="module", autouse=True)
def _command_guards(app, _schema):
    with app.app_context(), db.engine.begin() as connection:
        ensure_transformation_db_guards(connection)
        ensure_evidence_immutability_triggers(connection)
        ensure_arb_decision_guards(connection)


@pytest.fixture
def adr_scope(app, _schema):
    suffix = uuid.uuid4().hex[:12]
    with app.app_context():
        db.session.remove()
        organization = Organization(
            name=f"Typed ARB integration {suffix}",
            slug=f"typed-arb-integration-{suffix}",
        )
        foreign = Organization(
            name=f"Typed ARB foreign {suffix}",
            slug=f"typed-arb-foreign-{suffix}",
        )
        db.session.add_all((organization, foreign))
        db.session.flush()
        user = User(
            organization_id=organization.id,
            email=f"typed-arb-{suffix}@example.test",
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        foreign_user = User(
            organization_id=foreign.id,
            email=f"typed-arb-foreign-{suffix}@example.test",
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        db.session.add_all((user, foreign_user))
        db.session.flush()
        adr = ArchitectureDecision(
            organization_id=organization.id,
            decision_id=f"AD-{suffix}",
            title=f"Adopt governed integration {suffix}",
            status="proposed",
            context="Services require reliable asynchronous integration.",
            decision="Use durable domain events through the enterprise broker.",
            rationale="This isolates producers and consumers.",
            consequences="Teams own schema compatibility.",
            created_by_id=user.id,
        )
        db.session.add(adr)
        db.session.commit()
        scope = _ADRScope(
            actor=ActorContext(
                user.id,
                organization.id,
                frozenset({"enterprise_architect"}),
                f"typed-arb-{suffix}",
            ),
            organization_id=organization.id,
            user_id=user.id,
            adr_id=adr.id,
            foreign_actor=ActorContext(
                foreign_user.id,
                foreign.id,
                frozenset({"enterprise_architect"}),
                f"typed-arb-foreign-{suffix}",
            ),
        )
        db.session.remove()
        try:
            yield scope
        finally:
            db.session.remove()
            with db.engine.begin() as connection:
                connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
                for table_name in (
                    "arb_canonical_conditions",
                    "arb_decision_events",
                    "arb_submission_events",
                    "transformation_outbox_events",
                    "operation_results",
                    "command_materialisations",
                    "command_idempotency_records",
                    "arb_review_items",
                    "arb_review_cycles",
                    "arb_subject_evidence_snapshots",
                    "architecture_decisions",
                    "users",
                ):
                    connection.execute(
                        text(
                            f'DELETE FROM "{table_name}" '
                            "WHERE organization_id IN (:organization_id, :foreign_id)"
                        ),
                        {
                            "organization_id": scope.organization_id,
                            "foreign_id": scope.foreign_actor.organization_id,
                        },
                    )
                connection.execute(
                    text("DELETE FROM organizations WHERE id IN (:own, :foreign)"),
                    {
                        "own": scope.organization_id,
                        "foreign": scope.foreign_actor.organization_id,
                    },
                )


@pytest.fixture
def solution_scope(app, _schema):
    suffix = uuid.uuid4().hex[:12]
    with app.app_context():
        db.session.remove()
        organization = Organization(
            name=f"Typed ARB solution {suffix}",
            slug=f"typed-arb-solution-{suffix}",
        )
        db.session.add(organization)
        db.session.flush()
        user = User(
            organization_id=organization.id,
            email=f"typed-arb-solution-{suffix}@example.test",
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        db.session.add(user)
        db.session.flush()
        workspace = SolutionAnalysisSession(
            organization_id=organization.id,
            name=f"Governed workspace {suffix}",
            created_by_id=user.id,
        )
        db.session.add(workspace)
        db.session.flush()
        problem = SolutionProblemDefinition(
            organization_id=organization.id,
            session_id=workspace.id,
            problem_description="Replace brittle synchronous integration.",
        )
        db.session.add(problem)
        db.session.flush()
        driver = SolutionDriver(
            organization_id=organization.id,
            problem_id=problem.id,
            name="Resilience",
            driver_type=DriverType.TECHNOLOGY,
        )
        goal = SolutionGoal(
            organization_id=organization.id,
            problem_id=problem.id,
            name="Reliable event delivery",
        )
        solution = Solution(
            organization_id=organization.id,
            name=f"Governed solution {suffix}",
            description="A fully evidenced integration solution.",
            created_by_id=user.id,
            analysis_session_id=workspace.id,
            governance_status="draft",
        )
        db.session.add_all((driver, goal, solution))
        db.session.flush()
        workspace.custom_metadata = {
            "workspace_type": "greenfield",
            "solution_id": solution.id,
        }
        for name in ("brief", "scope", "recommendation"):
            WorkbenchArtifactEvidence.capture(
                organization_id=organization.id,
                workspace_id=workspace.id,
                solution_id=solution.id,
                name=name,
                state="persisted",
                payload={"name": name, "source": "integration-fixture"},
                actor_id=user.id,
            )
        db.session.add(
            SolutionRisk(
                organization_id=organization.id,
                solution_id=solution.id,
                risk_name="Schema drift",
                risk_description="Consumers may lag schema changes.",
                impact="medium",
                probability="medium",
                mitigation="Use versioned schemas and compatibility checks.",
                created_by_id=user.id,
            )
        )
        db.session.commit()
        scope = _SolutionScope(
            actor=ActorContext(
                user.id,
                organization.id,
                frozenset({"enterprise_architect"}),
                f"typed-arb-solution-{suffix}",
            ),
            organization_id=organization.id,
            user_id=user.id,
            workspace_id=workspace.id,
            solution_id=solution.id,
        )
        db.session.remove()
        try:
            yield scope
        finally:
            db.session.remove()
            raw = raw_psycopg2_connection(db.engine)
            try:
                with raw.cursor() as cursor:
                    cursor.execute("SET LOCAL session_replication_role = replica")
                    cursor.execute(
                        "DELETE FROM workbench_artifact_evidence "
                        "WHERE organization_id = %s",
                        (scope.organization_id,),
                    )
                    cursor.execute(
                        "DELETE FROM arb_submission_evidence_snapshots "
                        "WHERE organization_id = %s",
                        (scope.organization_id,),
                    )
                raw.commit()
            finally:
                raw.close()
            with db.engine.begin() as connection:
                connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
                for table_name in (
                    "arb_submission_events",
                    "transformation_outbox_events",
                    "operation_results",
                    "command_materialisations",
                    "command_idempotency_records",
                    "arb_review_items",
                    "arb_review_cycles",
                    "solution_risks",
                    "solution_goals",
                    "solution_drivers",
                    "solutions",
                    "solution_problem_definitions",
                    "solution_analysis_sessions",
                    "users",
                ):
                    connection.execute(
                        text(
                            f'DELETE FROM "{table_name}" '
                            "WHERE organization_id = :organization_id"
                        ),
                        {"organization_id": scope.organization_id},
                    )
                connection.execute(
                    text("DELETE FROM organizations WHERE id = :organization_id"),
                    {"organization_id": scope.organization_id},
                )


def _submit(scope, command_key):
    return TypedARBSubmissionService.submit(
        actor=scope.actor,
        command_key=command_key,
        subject_type="adr",
        subject_id=scope.adr_id,
        assertions={"human_reviewed": True},
    )


def _counts(scope):
    with Session(db.engine) as session:
        predicates = (
            ARBReviewCycle.organization_id == scope.organization_id,
            ARBReviewCycle.subject_type == "adr",
            ARBReviewCycle.subject_id == scope.adr_id,
        )
        return {
            "snapshots": session.scalar(
                select(db.func.count())
                .select_from(ARBSubjectEvidenceSnapshot)
                .where(
                    ARBSubjectEvidenceSnapshot.organization_id == scope.organization_id,
                    ARBSubjectEvidenceSnapshot.adr_id == scope.adr_id,
                )
            ),
            "cycles": session.scalar(
                select(db.func.count()).select_from(ARBReviewCycle).where(*predicates)
            ),
            "items": session.scalar(
                select(db.func.count())
                .select_from(ARBReviewItem)
                .where(
                    ARBReviewItem.organization_id == scope.organization_id,
                    ARBReviewItem.subject_type == "adr",
                    ARBReviewItem.subject_id == scope.adr_id,
                )
            ),
            "results": session.scalar(
                select(db.func.count())
                .select_from(OperationResult)
                .where(
                    OperationResult.organization_id == scope.organization_id,
                    OperationResult.operation == "arb.submit",
                )
            ),
            "events": session.scalar(
                select(db.func.count())
                .select_from(ARBSubmissionEvent)
                .where(ARBSubmissionEvent.organization_id == scope.organization_id)
            ),
        }


def test_real_adr_submission_is_atomic_and_same_key_replay_is_stable(app, adr_scope):
    with app.app_context():
        first = _submit(adr_scope, "adr-submit")
        replay = _submit(adr_scope, "adr-submit")

    assert first.created is True
    assert replay.idempotent is True
    assert replay.object_ids == first.object_ids
    assert _counts(adr_scope) == {
        "snapshots": 1,
        "cycles": 1,
        "items": 1,
        "results": 1,
        "events": 1,
    }
    with Session(db.engine) as session:
        cycle = session.get(ARBReviewCycle, first.object_ids["review_cycle_id"])
        item = session.get(ARBReviewItem, first.object_ids["review_item_id"])
        snapshot = session.get(
            ARBSubjectEvidenceSnapshot, first.object_ids["evidence_id"]
        )
        assert cycle.subject_evidence_snapshot_id == snapshot.id
        assert item.review_cycle_id == cycle.id
        assert item.subject_evidence_snapshot_id == snapshot.id
        assert cycle.review_number == item.review_number
        assert snapshot.content_hash == snapshot.recompute_content_hash()
        submission_event = session.execute(
            select(ARBSubmissionEvent).where(
                ARBSubmissionEvent.review_cycle_id == cycle.id
            )
        ).scalar_one()
        receipt = session.get(
            CommandIdempotencyRecord, submission_event.command_receipt_id
        )
        assert submission_event.review_item_id == item.id
        assert submission_event.subject_type == "adr"
        assert submission_event.subject_id == submission_event.adr_id == adr_scope.adr_id
        assert submission_event.subject_evidence_snapshot_id == snapshot.id
        assert submission_event.actor_id == adr_scope.user_id
        assert submission_event.event_type == "submitted"
        assert submission_event.command_generation == receipt.lease_generation
        assert receipt.operation == "arb.submit"


def test_same_key_replay_survives_terminal_cycle_transition(app, adr_scope):
    with app.app_context():
        first = _submit(adr_scope, "terminal-replay")
        cycle = db.session.get(
            ARBReviewCycle, first.object_ids["review_cycle_id"]
        )
        item = db.session.get(ARBReviewItem, first.object_ids["review_item_id"])
        now = datetime.now(timezone.utc)
        cycle.status = cycle.terminal_outcome = "approved"
        cycle.closed_at = now
        item.status = "approved"
        item.decision = "approved"
        db.session.commit()
        db.session.remove()

        replay = _submit(adr_scope, "terminal-replay")

    assert replay.idempotent is True
    assert replay.object_ids == first.object_ids
    assert _counts(adr_scope) == {
        "snapshots": 1,
        "cycles": 1,
        "items": 1,
        "results": 1,
        "events": 1,
    }


@pytest.mark.parametrize(
    ("outcome", "conditions"),
    (
        ("approved", []),
        (
            "approved_with_conditions",
            [{"code": "SEC-1", "text": "Complete the threat model"}],
        ),
        ("returned_for_evidence", []),
    ),
)
def test_real_typed_terminal_decision_projects_event_and_conditions(
    app, adr_scope, outcome, conditions
):
    canonical_conditions = TypedARBDecisionService._canonical_conditions(conditions)
    with app.app_context():
        submission = _submit(adr_scope, f"submit-for-{outcome}")
        decider = User(
            organization_id=adr_scope.organization_id,
            email=f"decider-{outcome}-{uuid.uuid4().hex[:8]}@example.test",
            enterprise_role="chief_architect",
            confirmed=True,
        )
        db.session.add(decider)
        db.session.commit()
        actor = ActorContext(
            decider.id,
            adr_scope.organization_id,
            frozenset({"viewer"}),
            f"decide-{outcome}",
        )
        result = TypedARBDecisionService.decide(
            actor=actor,
            command_key=f"decision-{outcome}",
            cycle_id=submission.object_ids["review_cycle_id"],
            outcome=outcome,
            rationale=f"Board recorded {outcome}",
            conditions=conditions,
        )
        replay = TypedARBDecisionService.decide(
            actor=actor,
            command_key=f"decision-{outcome}",
            cycle_id=submission.object_ids["review_cycle_id"],
            outcome=outcome,
            rationale=f"Board recorded {outcome}",
            conditions=conditions,
        )
        with pytest.raises(CommandConflict):
            TypedARBDecisionService.decide(
                actor=actor,
                command_key=f"conflicting-decision-{outcome}",
                cycle_id=submission.object_ids["review_cycle_id"],
                outcome="rejected" if outcome != "rejected" else "approved",
                rationale="A conflicting terminal outcome",
                conditions=[],
            )

    assert replay.idempotent is True
    assert replay.object_ids == result.object_ids
    with Session(db.engine) as session:
        cycle = session.get(ARBReviewCycle, result.object_ids["review_cycle_id"])
        review = session.get(ARBReviewItem, result.object_ids["review_item_id"])
        decision = session.get(ARBDecisionEvent, result.object_ids["decision_event_id"])
        assert cycle.status == cycle.terminal_outcome == outcome
        assert review.status == review.decision == outcome
        assert review.decision_rationale == decision.rationale
        assert decision.from_state == "submitted"
        assert decision.conditions_json == canonical_conditions
        condition_rows = session.scalars(
            select(ARBCondition).where(
                ARBCondition.decision_event_id == decision.id
            )
        ).all()
        assert len(condition_rows) == len(conditions)
        assert [
            {
                "condition_number": row.condition_number,
                "description": row.description,
                "category": row.category,
                "due_date": row.due_date.isoformat() if row.due_date else None,
                "blocks_execution": row.blocks_execution,
            }
            for row in condition_rows
        ] == canonical_conditions


def test_decision_rejects_submitter_and_forged_roles(app, adr_scope):
    with app.app_context():
        submission = _submit(adr_scope, "submit-authz")
        with pytest.raises(NotAuthorised, match="separation_of_duties"):
            TypedARBDecisionService.decide(
                actor=adr_scope.actor,
                command_key="self-decision",
                cycle_id=submission.object_ids["review_cycle_id"],
                outcome="approved",
                rationale="Self approval is forbidden",
            )
        viewer = User(
            organization_id=adr_scope.organization_id,
            email=f"decision-viewer-{uuid.uuid4().hex[:8]}@example.test",
            enterprise_role="viewer",
            confirmed=True,
        )
        db.session.add(viewer)
        db.session.commit()
        forged = ActorContext(
            viewer.id, adr_scope.organization_id,
            frozenset({"chief_architect"}), "forged-decision-role",
        )
        with pytest.raises(NotAuthorised, match="not_authorised"):
            TypedARBDecisionService.decide(
                actor=forged,
                command_key="forged-decision",
                cycle_id=submission.object_ids["review_cycle_id"],
                outcome="approved",
                rationale="Caller roles cannot grant authority",
            )


def test_decision_replay_rechecks_revoked_server_role(app, adr_scope):
    with app.app_context():
        submission = _submit(adr_scope, "submit-revocation")
        decider = User(
            organization_id=adr_scope.organization_id,
            email=f"decision-revoke-{uuid.uuid4().hex[:8]}@example.test",
            enterprise_role="chief_architect",
            confirmed=True,
        )
        db.session.add(decider)
        db.session.commit()
        actor = ActorContext(
            decider.id, adr_scope.organization_id,
            frozenset({"chief_architect"}), "decision-revocation",
        )
        result = TypedARBDecisionService.decide(
            actor=actor, command_key="revoke-after-win",
            cycle_id=submission.object_ids["review_cycle_id"], outcome="approved",
            rationale="Approved before authority changed",
        )
        decider.enterprise_role = "viewer"
        db.session.commit()
        db.session.remove()
        with pytest.raises(NotAuthorised, match="not_authorised"):
            TypedARBDecisionService.decide(
                actor=actor, command_key="revoke-after-win",
                cycle_id=submission.object_ids["review_cycle_id"], outcome="approved",
                rationale="Approved before authority changed",
            )
    assert result.created is True


def test_decision_event_failure_rolls_back_terminal_projection(app, adr_scope):
    with app.app_context():
        submission = _submit(adr_scope, "submit-rollback-decision")
        decider = User(
            organization_id=adr_scope.organization_id,
            email=f"decision-rollback-{uuid.uuid4().hex[:8]}@example.test",
            enterprise_role="chief_architect",
            confirmed=True,
        )
        db.session.add(decider)
        db.session.commit()
        actor = ActorContext(
            decider.id, adr_scope.organization_id,
            frozenset(), "decision-rollback",
        )

        def fail_event_insert(*_args, **_kwargs):
            raise RuntimeError("forced decision event failure")

        event.listen(ARBDecisionEvent, "before_insert", fail_event_insert)
        try:
            with pytest.raises(RuntimeError, match="forced decision event failure"):
                TypedARBDecisionService.decide(
                    actor=actor, command_key="decision-rollback",
                    cycle_id=submission.object_ids["review_cycle_id"],
                    outcome="rejected", rationale="Rollback this decision",
                )
        finally:
            event.remove(ARBDecisionEvent, "before_insert", fail_event_insert)
    with Session(db.engine) as session:
        cycle = session.get(ARBReviewCycle, submission.object_ids["review_cycle_id"])
        review = session.get(ARBReviewItem, submission.object_ids["review_item_id"])
        assert cycle.status == review.status == "submitted"
        assert session.scalar(
            select(db.func.count()).select_from(ARBDecisionEvent).where(
                ARBDecisionEvent.review_cycle_id == cycle.id
            )
        ) == 0


def test_real_solution_submission_pins_legacy_evidence_into_typed_graph(
    app, solution_scope, tenant_ctx
):
    assertions = {
        "human_reviewed": True,
        "direct_route_evidence": {
            name: {"passed": True, "evidence": f"{name} checked"}
            for name in (
                "design_reviewed",
                "security_impact_reviewed",
                "data_impact_reviewed",
            )
        },
    }
    with app.app_context(), tenant_ctx(solution_scope.organization_id):
        result = TypedARBSubmissionService.submit_legacy_solution(
            actor=solution_scope.actor,
            command_key="solution-submit",
            solution_id=solution_scope.solution_id,
            assertions=assertions,
        )

    assert result.created is True
    with Session(db.engine) as session:
        snapshot = session.get(
            ARBSubmissionEvidenceSnapshot, result.object_ids["evidence_id"]
        )
        cycle = session.get(ARBReviewCycle, result.object_ids["review_cycle_id"])
        item = session.get(ARBReviewItem, result.object_ids["review_item_id"])
        assert snapshot.solution_id == solution_scope.solution_id
        assert snapshot.content_hash == snapshot.recompute_content_hash()
        assert cycle.solution_id == item.solution_id == solution_scope.solution_id
        assert cycle.solution_evidence_snapshot_id == snapshot.id
        assert item.solution_evidence_snapshot_id == snapshot.id
        assert item.review_cycle_id == cycle.id
        assert snapshot.review_item_id == item.id
        submission_event = session.execute(
            select(ARBSubmissionEvent).where(
                ARBSubmissionEvent.review_cycle_id == cycle.id
            )
        ).scalar_one()
        assert submission_event.review_item_id == item.id
        assert submission_event.solution_id == solution_scope.solution_id
        assert submission_event.solution_evidence_snapshot_id == snapshot.id
        assert submission_event.actor_id == solution_scope.actor.user_id
        assert submission_event.event_type == "submitted"
        assert session.scalar(
            select(db.func.count())
            .select_from(ARBSubmissionEvidenceSnapshot)
            .where(
                ARBSubmissionEvidenceSnapshot.organization_id
                == solution_scope.organization_id,
                ARBSubmissionEvidenceSnapshot.solution_id
                == solution_scope.solution_id,
            )
        ) == 1
        constraint = session.execute(
            text(
                """
                SELECT c.condeferrable, c.condeferred
                FROM pg_constraint AS c
                JOIN pg_class AS t ON t.oid = c.conrelid
                JOIN pg_namespace AS n ON n.oid = t.relnamespace
                WHERE n.nspname = current_schema()
                  AND t.relname = 'arb_submission_evidence_snapshots'
                  AND c.conname = 'fk_arb_submission_snapshot_review_item'
                """
            )
        ).one()
        assert constraint == (True, True)


def test_solution_replay_rejects_workspace_bound_to_another_solution(
    app, solution_scope, tenant_ctx
):
    with app.app_context(), tenant_ctx(solution_scope.organization_id):
        other_workspace = SolutionAnalysisSession(
            organization_id=solution_scope.organization_id,
            name="Foreign solution workspace",
            created_by_id=solution_scope.user_id,
        )
        db.session.add(other_workspace)
        db.session.flush()
        other_solution = Solution(
            organization_id=solution_scope.organization_id,
            name="Other governed solution",
            description="Workspace must not replay another solution's receipt.",
            created_by_id=solution_scope.user_id,
            analysis_session_id=other_workspace.id,
            governance_status="draft",
        )
        db.session.add(other_solution)
        db.session.flush()
        other_workspace.custom_metadata = {
            "workspace_type": "greenfield",
            "solution_id": other_solution.id,
        }
        db.session.commit()

        first = TypedARBSubmissionService.submit_legacy_solution(
            actor=solution_scope.actor,
            command_key="solution-workspace-replay",
            solution_id=solution_scope.solution_id,
            workspace_id=solution_scope.workspace_id,
            assertions={"human_reviewed": True},
        )
        with pytest.raises(NotFound, match="arb_submission_workspace_not_found"):
            TypedARBSubmissionService.submit_legacy_solution(
                actor=solution_scope.actor,
                command_key="solution-workspace-replay",
                solution_id=solution_scope.solution_id,
                workspace_id=other_workspace.id,
                assertions={"human_reviewed": True},
            )

    assert first.created is True
    with Session(db.engine) as session:
        assert session.scalar(
            select(db.func.count())
            .select_from(ARBReviewCycle)
            .where(
                ARBReviewCycle.organization_id == solution_scope.organization_id,
                ARBReviewCycle.solution_id == solution_scope.solution_id,
            )
        ) == 1


def test_concurrent_solution_submissions_converge_on_one_real_cycle(
    app, solution_scope
):
    def submit(command_key):
        with app.test_request_context("/"):
            g.current_org_id = solution_scope.organization_id
            result = TypedARBSubmissionService.submit_legacy_solution(
                actor=solution_scope.actor,
                command_key=command_key,
                solution_id=solution_scope.solution_id,
                workspace_id=solution_scope.workspace_id,
                assertions={"human_reviewed": True},
            )
            db.session.remove()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(submit, ("solution-race-one", "solution-race-two"))
        )

    assert {result.object_ids["review_cycle_id"] for result in results} == {
        results[0].object_ids["review_cycle_id"]
    }
    assert sorted(result.idempotent for result in results) == [False, True]
    with Session(db.engine) as session:
        assert session.scalar(
            select(db.func.count())
            .select_from(ARBReviewCycle)
            .where(
                ARBReviewCycle.organization_id == solution_scope.organization_id,
                ARBReviewCycle.solution_id == solution_scope.solution_id,
            )
        ) == 1
        assert session.scalar(
            select(db.func.count())
            .select_from(ARBSubmissionEvidenceSnapshot)
            .where(
                ARBSubmissionEvidenceSnapshot.organization_id
                == solution_scope.organization_id,
                ARBSubmissionEvidenceSnapshot.solution_id
                == solution_scope.solution_id,
            )
        ) == 1


def test_solution_snapshot_failure_rolls_back_cycle_review_and_event(
    app, solution_scope, tenant_ctx
):
    def fail_snapshot(_mapper, _connection, _target):
        raise RuntimeError("forced solution snapshot failure")

    event.listen(ARBSubmissionEvidenceSnapshot, "before_insert", fail_snapshot)
    try:
        with app.app_context(), tenant_ctx(solution_scope.organization_id), pytest.raises(
            RuntimeError, match="forced solution snapshot failure"
        ):
            TypedARBSubmissionService.submit_legacy_solution(
                actor=solution_scope.actor,
                command_key="solution-snapshot-rollback",
                solution_id=solution_scope.solution_id,
                workspace_id=solution_scope.workspace_id,
                assertions={"human_reviewed": True},
            )
    finally:
        event.remove(ARBSubmissionEvidenceSnapshot, "before_insert", fail_snapshot)

    with Session(db.engine) as session:
        for model in (
            ARBSubmissionEvidenceSnapshot,
            ARBReviewCycle,
            ARBReviewItem,
            ARBSubmissionEvent,
        ):
            assert session.scalar(
                select(db.func.count())
                .select_from(model)
                .where(model.organization_id == solution_scope.organization_id)
            ) == 0
        solution = session.get(Solution, solution_scope.solution_id)
        assert solution.governance_status == "draft"
        assert solution.arb_review_item_id is None


def test_real_solution_snapshot_is_database_immutable(
    app, solution_scope, tenant_ctx
):
    with app.app_context(), tenant_ctx(solution_scope.organization_id):
        result = TypedARBSubmissionService.submit_legacy_solution(
            actor=solution_scope.actor,
            command_key="solution-snapshot-immutable",
            solution_id=solution_scope.solution_id,
            workspace_id=solution_scope.workspace_id,
            assertions={"human_reviewed": True},
        )

    raw = raw_psycopg2_connection(db.engine)
    try:
        with pytest.raises(psycopg2.Error, match="append-only"):
            with raw.cursor() as cursor:
                cursor.execute(
                    "UPDATE arb_submission_evidence_snapshots "
                    "SET workflow_type='tampered' WHERE id=%s",
                    (result.object_ids["evidence_id"],),
                )
    finally:
        raw.rollback()
        raw.close()


def test_different_command_key_reconciles_to_the_same_open_cycle(app, adr_scope):
    with app.app_context():
        first = _submit(adr_scope, "first-command")
        contender = _submit(adr_scope, "second-command")

    assert first.created is True
    assert contender.idempotent is True
    assert contender.object_ids == first.object_ids
    assert _counts(adr_scope) == {
        "snapshots": 1,
        "cycles": 1,
        "items": 1,
        "results": 1,
        "events": 1,
    }


def test_cross_tenant_subject_is_uniform_not_found_and_creates_no_receipt(app, adr_scope):
    with app.app_context(), pytest.raises(NotFound, match="arb_subject_not_found"):
        TypedARBSubmissionService.submit(
            actor=adr_scope.foreign_actor,
            command_key="foreign-command",
            subject_type="adr",
            subject_id=adr_scope.adr_id,
            assertions={"human_reviewed": True},
        )

    assert _counts(adr_scope) == {
        "snapshots": 0,
        "cycles": 0,
        "items": 0,
        "results": 0,
        "events": 0,
    }


def test_same_tenant_non_architect_cannot_submit_even_with_forged_actor_roles(
    app, adr_scope
):
    with app.app_context():
        user = db.session.get(User, adr_scope.user_id)
        user.enterprise_role = "viewer"
        user.is_org_admin = False
        user.is_platform_admin = False
        db.session.commit()
        forged = ActorContext(
            user.id,
            adr_scope.organization_id,
            frozenset({"enterprise_architect", "platform_admin"}),
            "forged-submit-role",
        )
        with pytest.raises(NotAuthorised, match="arb_submission_not_authorised"):
            TypedARBSubmissionService.submit(
                actor=forged,
                command_key="unauthorised-command",
                subject_type="adr",
                subject_id=adr_scope.adr_id,
                assertions={"human_reviewed": True},
            )

    assert _counts(adr_scope) == {
        "snapshots": 0,
        "cycles": 0,
        "items": 0,
        "results": 0,
        "events": 0,
    }


def test_replay_revalidates_authority_after_role_revocation(app, adr_scope):
    with app.app_context():
        first = _submit(adr_scope, "revoked-replay")
        user = db.session.get(User, adr_scope.user_id)
        user.enterprise_role = "viewer"
        user.is_org_admin = False
        user.is_platform_admin = False
        db.session.commit()
        with pytest.raises(NotAuthorised, match="arb_submission_not_authorised"):
            _submit(adr_scope, "revoked-replay")

    assert first.created is True
    assert _counts(adr_scope) == {
        "snapshots": 1,
        "cycles": 1,
        "items": 1,
        "results": 1,
        "events": 1,
    }


def test_review_item_insert_failure_rolls_back_snapshot_cycle_and_result(app, adr_scope):
    def fail_review_insert(_mapper, _connection, _target):
        raise RuntimeError("forced typed ARB review insert failure")

    event.listen(ARBReviewItem, "before_insert", fail_review_insert)
    try:
        with app.app_context(), pytest.raises(
            RuntimeError, match="forced typed ARB review insert failure"
        ):
            _submit(adr_scope, "forced-rollback")
    finally:
        event.remove(ARBReviewItem, "before_insert", fail_review_insert)

    assert _counts(adr_scope) == {
        "snapshots": 0,
        "cycles": 0,
        "items": 0,
        "results": 0,
        "events": 0,
    }


def test_submission_event_insert_failure_rolls_back_entire_graph(app, adr_scope):
    def fail_event_insert(_mapper, _connection, _target):
        raise RuntimeError("forced typed ARB event insert failure")

    event.listen(ARBSubmissionEvent, "before_insert", fail_event_insert)
    try:
        with app.app_context(), pytest.raises(
            RuntimeError, match="forced typed ARB event insert failure"
        ):
            _submit(adr_scope, "forced-event-rollback")
    finally:
        event.remove(ARBSubmissionEvent, "before_insert", fail_event_insert)

    assert _counts(adr_scope) == {
        "snapshots": 0,
        "cycles": 0,
        "items": 0,
        "results": 0,
        "events": 0,
    }
