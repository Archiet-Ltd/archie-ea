"""Business-first Transformation Programme service contracts."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app import db
from app.models.organization import Organization
from app.models.solution_models import Solution
from app.models.strategic import StrategicInitiative
from app.models.transformation_execution import OperationOutboxEvent
from app.models.transformation_programme import (
    MeasureDefinition,
    ProgrammeOutcomeCommitment,
    ProgrammeRoleAssignment,
    ProgrammeWorkstream,
)
from app.models.user import User
from app.modules.transformation_room.domain import (
    ActorContext,
    CommandConflict,
    NotAuthorised,
    NotFound,
    ProgrammeIntake,
)
from app.modules.transformation_room.command_service import CommandService
from app.modules.transformation_room.gate_service import TransformationGateService
from app.modules.transformation_room.programme_service import TransformationProgrammeService


@dataclass(frozen=True)
class ProgrammeFixture:
    organization_id: int
    foreign_organization_id: int
    owner_id: int
    foreign_owner_id: int
    actor: ActorContext


@pytest.fixture(scope="module", autouse=True)
def transformation_schema(app, _schema):
    from app.commands.reconcile_schema import _reconcile

    with app.app_context():
        _added, failed, _missing, _blocking = _reconcile(dry_run=False)
        assert failed == []


@pytest.fixture
def programme_fixture(app, _schema):
    suffix = uuid.uuid4().hex[:12]
    with app.app_context():
        db.session.remove()
        organization = Organization(name=f"Programme Org {suffix}", slug=f"programme-{suffix}")
        foreign = Organization(name=f"Foreign Org {suffix}", slug=f"foreign-{suffix}")
        db.session.add_all([organization, foreign])
        db.session.flush()
        owner = User(
            email=f"architect-{suffix}@example.test",
            organization_id=organization.id,
            confirmed=True,
            enterprise_role="enterprise_architect",
        )
        foreign_owner = User(
            email=f"foreign-{suffix}@example.test",
            organization_id=foreign.id,
            confirmed=True,
            enterprise_role="enterprise_architect",
        )
        db.session.add_all([owner, foreign_owner])
        db.session.flush()
        fixture = ProgrammeFixture(
            organization.id,
            foreign.id,
            owner.id,
            foreign_owner.id,
            ActorContext(owner.id, organization.id, frozenset({"forged_role"}), f"request-{suffix}"),
        )
        db.session.commit()
        db.session.remove()
        try:
            yield fixture
        finally:
            db.session.remove()
            with db.engine.begin() as connection:
                connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
                for table_name in (
                    "transformation_outbox_events",
                    "operation_results",
                    "command_materialisations",
                    "command_idempotency_records",
                    "measure_definitions",
                    "programme_outcome_commitments",
                    "programme_role_assignments",
                    "programme_workstreams",
                    "solutions",
                    "strategic_initiatives",
                    "users",
                ):
                    connection.execute(
                        text(f'DELETE FROM "{table_name}" WHERE organization_id IN (:org, :foreign)'),
                        {"org": fixture.organization_id, "foreign": fixture.foreign_organization_id},
                    )
                connection.execute(
                    text("DELETE FROM organizations WHERE id IN (:org, :foreign)"),
                    {"org": fixture.organization_id, "foreign": fixture.foreign_organization_id},
                )


def _intake(owner_id: int, **changes) -> ProgrammeIntake:
    values = {
        "name": "Simplify the application estate",
        "objective": "Reduce duplicated capability cost without service loss",
        "owner_id": owner_id,
        "target_date": date(2027, 6, 30),
        "target_date_unavailable_reason": None,
        "workstream_type": "application_rationalisation",
        "scope_expression": {"business_units": ["Retail"]},
        "outcome": {
            "statement": "Reduce annual run cost",
            "owner_id": owner_id,
            "direction": "decrease",
            "measure": {
                "metric_name": "Annual run cost",
                "unit": "GBP",
                "currency": "GBP",
                "aggregation": "sum",
                "baseline_value": None,
                "unavailable_reason": "Finance baseline requested",
                "target_value": "900000.00",
            },
        },
    }
    values.update(changes)
    return ProgrammeIntake(**values)


def _rows(fixture: ProgrammeFixture):
    with Session(db.engine) as session:
        programme = session.execute(
            select(StrategicInitiative).where(
                StrategicInitiative.organization_id == fixture.organization_id,
                StrategicInitiative.record_kind == "transformation_programme",
            )
        ).scalar_one_or_none()
        return {
            "programme": programme,
            "workstreams": session.scalars(
                select(ProgrammeWorkstream).where(
                    ProgrammeWorkstream.organization_id == fixture.organization_id
                )
            ).all(),
            "roles": session.scalars(
                select(ProgrammeRoleAssignment).where(
                    ProgrammeRoleAssignment.organization_id == fixture.organization_id
                )
            ).all(),
            "outcomes": session.scalars(
                select(ProgrammeOutcomeCommitment).where(
                    ProgrammeOutcomeCommitment.organization_id == fixture.organization_id
                )
            ).all(),
            "measures": session.scalars(
                select(MeasureDefinition).where(
                    MeasureDefinition.organization_id == fixture.organization_id
                )
            ).all(),
            "solutions": session.scalar(
                select(db.func.count()).select_from(Solution).where(
                    Solution.organization_id == fixture.organization_id
                )
            ),
            "events": session.scalars(
                select(OperationOutboxEvent).where(
                    OperationOutboxEvent.organization_id == fixture.organization_id
                )
            ).all(),
        }


def _claim_task4_mutation(kind: str, fixture: ProgrammeFixture):
    """Claim one Task 4 command without entering its locked handler yet."""
    if kind == "create":
        validated = TransformationProgrammeService.validate_intake(
            actor=fixture.actor, request=_intake(fixture.owner_id)
        )
        natural_key = "programme-intake:task4-create-race"
        claim = CommandService.claim_or_reconcile(
            actor=fixture.actor,
            operation="programme.create",
            idempotency_key="task4-create-race",
            request_digest=CommandService.request_digest(asdict(validated)),
            natural_key=natural_key,
            authorizer=TransformationProgrammeService.authorise_create_programme(
                validated, natural_key
            ),
        )
        return (
            "programme.create",
            claim,
            lambda session, fenced_claim: TransformationProgrammeService._insert_intake_graph(
                session=session,
                actor=fixture.actor,
                request=validated,
                claim=fenced_claim,
            ),
            None,
        )

    created = TransformationProgrammeService.create_programme(
        actor=fixture.actor,
        command_key=f"task4-{kind}-programme",
        request=_intake(fixture.owner_id),
    )
    programme_id = created.object_ids["programme_id"]
    workstream_id = created.object_ids["workstream_id"]
    with Session(db.engine) as session, session.begin():
        user = session.scalar(
            select(User).where(
                User.organization_id == fixture.organization_id,
                User.id == fixture.owner_id,
            )
        )
        user.enterprise_role = "application_manager"
        assignment_id = session.scalar(
            select(ProgrammeRoleAssignment.id).where(
                ProgrammeRoleAssignment.organization_id == fixture.organization_id,
                ProgrammeRoleAssignment.programme_id == programme_id,
                ProgrammeRoleAssignment.user_id == fixture.owner_id,
                ProgrammeRoleAssignment.role == "programme_owner",
            )
        )
        assignment = session.get(ProgrammeRoleAssignment, assignment_id)
        assignment.effective_from = date.today() - timedelta(days=2)

    if kind == "objective":
        operation = "workstream.update_objective"
        payload = {
            "workstream_id": workstream_id,
            "objective": "The locked objective update",
            "scope_expression": {"business_units": ["Retail"]},
            "expected_revision": 1,
        }
        natural_key = f"objective:{workstream_id}:1"
        authorizer = TransformationProgrammeService.authorise_objective_update(
            workstream_id, 1
        )
        def handler(session, fenced_claim):
            return TransformationProgrammeService._update_objective_locked(
                session, fixture.actor, payload, fenced_claim
            )
    elif kind == "archive":
        operation = "programme.archive"
        payload = {
            "programme_id": programme_id,
            "rationale": "Close the programme after its governed test decision.",
            "expected_revision": 1,
        }
        natural_key = f"programme-archive:{programme_id}:1"
        authorizer = TransformationProgrammeService.authorise_programme_archive(
            programme_id, 1
        )
        def handler(session, fenced_claim):
            return TransformationProgrammeService._archive_locked(
                session, fixture.actor, payload, fenced_claim
            )
    else:
        operation = "workstream.transition"
        payload = {
            "workstream_id": workstream_id,
            "target_stage": "discover",
            "expected_revision": 1,
        }
        natural_key = f"transition:{workstream_id}:1:discover"
        authorizer = TransformationGateService.authorise_transition(
            workstream_id, "discover", 1
        )
        def handler(session, fenced_claim):
            return TransformationGateService._locked_transition(
                session, fixture.actor, payload, fenced_claim
            )
    claim = CommandService.claim_or_reconcile(
        actor=fixture.actor,
        operation=operation,
        idempotency_key=f"task4-{kind}-race",
        request_digest=CommandService.request_digest(payload),
        natural_key=natural_key,
        authorizer=authorizer,
    )
    return operation, claim, handler, assignment_id


def test_create_programme_persists_one_business_graph_and_replays_exactly(programme_fixture):
    """Catches business intake creating a Solution, duplicate graph, or lossy result envelope."""
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="business-intake",
        request=_intake(programme_fixture.owner_id),
    )
    replayed = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="business-intake",
        request=_intake(programme_fixture.owner_id),
    )

    rows = _rows(programme_fixture)
    assert created.created is True and created.idempotent is False
    assert replayed.created is False and replayed.idempotent is True
    assert replayed.operation_result_id == created.operation_result_id
    assert replayed.object_ids == created.object_ids
    assert set(created.object_ids) >= {
        "programme_id", "workstream_id", "outcome_commitment_id", "measure_definition_id"
    }
    assert rows["programme"].record_kind == "transformation_programme"
    assert rows["programme"].owner_id == programme_fixture.owner_id
    assert len(rows["workstreams"]) == len(rows["roles"]) == len(rows["outcomes"]) == len(rows["measures"]) == 1
    assert rows["workstreams"][0].lead_id == programme_fixture.owner_id
    assert rows["roles"][0].role == "programme_owner"
    assert rows["measures"][0].baseline_amount is None
    assert str(rows["measures"][0].target_amount) == "900000.00"
    assert rows["solutions"] == 0
    assert len(rows["events"]) == 1
    assert rows["events"][0].event_type == "programme.created"


def test_create_programme_requires_persisted_server_role_not_actor_claim(programme_fixture):
    """Catches forged ActorContext roles authorising a user whose persisted role was removed."""
    with Session(db.engine) as session, session.begin():
        user = session.get(User, programme_fixture.owner_id)
        user.enterprise_role = "application_manager"

    forged = ActorContext(
        programme_fixture.owner_id,
        programme_fixture.organization_id,
        frozenset({"enterprise_architect", "chief_architect", "platform_admin"}),
        "forged-request",
    )
    with pytest.raises(NotAuthorised, match="programme_create_not_authorised"):
        TransformationProgrammeService.create_programme(
            actor=forged,
            command_key="forged-role",
            request=_intake(programme_fixture.owner_id),
        )
    assert _rows(programme_fixture)["programme"] is None


@pytest.mark.parametrize("kind", ["create", "objective", "archive", "transition"])
def test_task4_mutation_commit_serializes_with_authority_revocation(
    app, programme_fixture, kind
):
    """Catches a Task 4 write committing after its persisted authority was revoked."""
    operation, claim, handler, assignment_id = _claim_task4_mutation(
        kind, programme_fixture
    )
    engine = db.engine
    authority_locked = threading.Event()
    release_mutation = threading.Event()
    revocation_started = threading.Event()
    revocation_pid_ready = threading.Event()
    results = []
    mutation_errors = []
    revocation_errors = []
    revocation_pid = []
    mutation_thread_name = f"task4-{kind}-mutation"

    def pause_after_locked_authority(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        lowered = statement.lower()
        expected_table = "users" if kind == "create" else "programme_role_assignments"
        if (
            threading.current_thread().name == mutation_thread_name
            and statement.lstrip().upper().startswith("SELECT")
            and expected_table in lowered
            and "for update" in lowered
        ):
            authority_locked.set()
            if not release_mutation.wait(timeout=10):
                raise TimeoutError("Task 4 mutation authority pause was not released")

    def mutate():
        with app.app_context():
            try:
                results.append(
                    CommandService._execute_claim(
                        actor=programme_fixture.actor,
                        operation=operation,
                        claim=claim,
                        authorizer=None,
                        handler=handler,
                    )
                )
            except Exception as error:  # asserted after both workers finish
                mutation_errors.append(error)
            finally:
                db.session.remove()

    def revoke():
        with app.app_context():
            try:
                with Session(engine) as session, session.begin():
                    revocation_pid.append(
                        session.scalar(text("SELECT pg_backend_pid()"))
                    )
                    revocation_pid_ready.set()
                    revocation_started.set()
                    if kind == "create":
                        user = session.scalar(
                            select(User)
                            .where(
                                User.organization_id
                                == programme_fixture.organization_id,
                                User.id == programme_fixture.owner_id,
                            )
                            .with_for_update()
                        )
                        user.enterprise_role = "application_manager"
                    else:
                        assignment = session.scalar(
                            select(ProgrammeRoleAssignment)
                            .where(
                                ProgrammeRoleAssignment.organization_id
                                == programme_fixture.organization_id,
                                ProgrammeRoleAssignment.id == assignment_id,
                            )
                            .with_for_update()
                        )
                        assignment.effective_to = date.today() - timedelta(days=1)
            except Exception as error:  # asserted after both workers finish
                revocation_errors.append(error)
            finally:
                db.session.remove()

    event.listen(engine, "after_cursor_execute", pause_after_locked_authority)
    mutation_thread = threading.Thread(
        target=mutate, name=mutation_thread_name, daemon=True
    )
    revocation_thread = threading.Thread(
        target=revoke, name=f"task4-{kind}-revocation", daemon=True
    )
    revocation_waited_on_lock = False
    try:
        mutation_thread.start()
        assert authority_locked.wait(timeout=10)
        revocation_thread.start()
        assert revocation_started.wait(timeout=5)
        assert revocation_pid_ready.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and revocation_thread.is_alive():
            with engine.connect() as connection:
                revocation_waited_on_lock = connection.scalar(
                    text(
                        "SELECT wait_event_type = 'Lock' FROM pg_stat_activity "
                        "WHERE pid = :pid"
                    ),
                    {"pid": revocation_pid[0]},
                ) is True
            if revocation_waited_on_lock:
                break
            time.sleep(0.01)
    finally:
        release_mutation.set()
        mutation_thread.join(timeout=10)
        if revocation_thread.ident is not None:
            revocation_thread.join(timeout=10)
        event.remove(engine, "after_cursor_execute", pause_after_locked_authority)

    assert revocation_waited_on_lock is True
    assert mutation_thread.is_alive() is False
    assert revocation_thread.is_alive() is False
    assert mutation_errors == []
    assert revocation_errors == []
    assert len(results) == 1


@pytest.mark.parametrize("kind", ["create", "objective", "archive", "transition"])
def test_task4_locked_handler_denies_revocation_that_commits_first(
    programme_fixture, kind
):
    """Catches a claimed Task 4 command trusting authority read before its handler."""
    operation, claim, handler, assignment_id = _claim_task4_mutation(
        kind, programme_fixture
    )
    with Session(db.engine) as session, session.begin():
        if kind == "create":
            user = session.scalar(
                select(User).where(
                    User.organization_id == programme_fixture.organization_id,
                    User.id == programme_fixture.owner_id,
                )
            )
            user.enterprise_role = "application_manager"
        else:
            assignment = session.scalar(
                select(ProgrammeRoleAssignment).where(
                    ProgrammeRoleAssignment.organization_id
                    == programme_fixture.organization_id,
                    ProgrammeRoleAssignment.id == assignment_id,
                )
            )
            assignment.effective_to = date.today() - timedelta(days=1)

    with pytest.raises(NotAuthorised):
        CommandService._execute_claim(
            actor=programme_fixture.actor,
            operation=operation,
            claim=claim,
            authorizer=None,
            handler=handler,
        )


def test_transition_denies_revocation_committed_between_snapshot_and_locked_authority(
    app, programme_fixture
):
    """Catches the locked authority query reusing a stale snapshot identity."""
    operation, claim, handler, assignment_id = _claim_task4_mutation(
        "transition", programme_fixture
    )
    engine = db.engine
    aggregate_locked = threading.Event()
    snapshot_authority_read = threading.Event()
    revocation_committed = threading.Event()
    results = []
    errors = []
    mutation_thread_name = "transition-stale-authority"

    def pause_after_snapshot_authority_and_aggregate_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        lowered = statement.lower()
        if (
            threading.current_thread().name == mutation_thread_name
            and statement.lstrip().upper().startswith("SELECT")
            and "programme_workstreams" in lowered
            and "for update" in lowered
        ):
            aggregate_locked.set()
        elif (
            threading.current_thread().name == mutation_thread_name
            and aggregate_locked.is_set()
            and not snapshot_authority_read.is_set()
            and statement.lstrip().upper().startswith("SELECT")
            and "users." in lowered
            and "for update" not in lowered
        ):
            snapshot_authority_read.set()
            if not revocation_committed.wait(timeout=10):
                raise TimeoutError("authority revocation did not commit")

    def transition():
        with app.app_context():
            try:
                results.append(
                    CommandService._execute_claim(
                        actor=programme_fixture.actor,
                        operation=operation,
                        claim=claim,
                        authorizer=None,
                        handler=handler,
                    )
                )
            except Exception as error:  # asserted after the worker finishes
                errors.append(error)
            finally:
                db.session.remove()

    event.listen(
        engine,
        "after_cursor_execute",
        pause_after_snapshot_authority_and_aggregate_lock,
    )
    worker = threading.Thread(
        target=transition, name=mutation_thread_name, daemon=True
    )
    try:
        worker.start()
        assert snapshot_authority_read.wait(timeout=10)
        with Session(engine) as session, session.begin():
            assignment = session.scalar(
                select(ProgrammeRoleAssignment)
                .where(
                    ProgrammeRoleAssignment.organization_id
                    == programme_fixture.organization_id,
                    ProgrammeRoleAssignment.id == assignment_id,
                )
                .with_for_update()
            )
            assignment.effective_to = date.today() - timedelta(days=1)
        revocation_committed.set()
        worker.join(timeout=10)
    finally:
        revocation_committed.set()
        worker.join(timeout=10)
        event.remove(
            engine,
            "after_cursor_execute",
            pause_after_snapshot_authority_and_aggregate_lock,
        )

    assert worker.is_alive() is False
    assert results == []
    assert len(errors) == 1
    assert isinstance(errors[0], NotAuthorised)
    assert errors[0].reason == "transition_not_authorised"


def test_create_programme_hides_cross_tenant_owner(programme_fixture):
    """Catches a globally valid foreign user ID being accepted or disclosed."""
    with pytest.raises(NotFound, match="owner_not_found"):
        TransformationProgrammeService.create_programme(
            actor=programme_fixture.actor,
            command_key="foreign-owner",
            request=_intake(programme_fixture.foreign_owner_id),
        )


def test_nullable_target_date_requires_persisted_reason(programme_fixture):
    """Catches an unknown programme horizon being silently stored as blank."""
    with pytest.raises(ValueError, match="target_date_unavailable_reason"):
        TransformationProgrammeService.create_programme(
            actor=programme_fixture.actor,
            command_key="missing-date-reason",
            request=_intake(programme_fixture.owner_id, target_date=None),
        )


def test_role_assignment_requires_an_explicit_effective_from(programme_fixture):
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="role-date-programme",
        request=_intake(programme_fixture.owner_id),
    )

    with pytest.raises(ValueError, match="effective_from is required"):
        TransformationProgrammeService.assign_role(
            actor=programme_fixture.actor,
            programme_id=created.object_ids["programme_id"],
            workstream_id=created.object_ids["workstream_id"],
            user_id=programme_fixture.owner_id,
            role="contributor",
            effective_from=None,
            effective_to=None,
            expected_revision=1,
            command_key="role-without-effective-from",
        )

    with Session(db.engine) as session:
        assert session.get(
            ProgrammeWorkstream, created.object_ids["workstream_id"]
        ).revision == 1


def test_assign_role_reauthorises_replay_from_current_server_state(programme_fixture):
    """Catches persisted role-command success replaying after the caller loses authority."""
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="role-programme",
        request=_intake(programme_fixture.owner_id),
    )
    result = TransformationProgrammeService.assign_role(
        actor=programme_fixture.actor,
        programme_id=created.object_ids["programme_id"],
        workstream_id=created.object_ids["workstream_id"],
        user_id=programme_fixture.owner_id,
        role="contributor",
        effective_from=date.today(),
        effective_to=None,
        expected_revision=1,
        command_key="assign-contributor",
    )
    assert result.object_ids["role_assignment_id"] > 0

    with Session(db.engine) as session, session.begin():
        user = session.get(User, programme_fixture.owner_id)
        user.enterprise_role = "application_manager"
        owner_assignment = session.execute(
            select(ProgrammeRoleAssignment).where(
                ProgrammeRoleAssignment.organization_id == programme_fixture.organization_id,
                ProgrammeRoleAssignment.programme_id == created.object_ids["programme_id"],
                ProgrammeRoleAssignment.role == "programme_owner",
            )
        ).scalar_one()
        owner_assignment.effective_from = date.today() - timedelta(days=2)
        owner_assignment.effective_to = date.today() - timedelta(days=1)

    with pytest.raises(NotAuthorised, match="role_assignment_not_authorised"):
        TransformationProgrammeService.assign_role(
            actor=programme_fixture.actor,
            programme_id=created.object_ids["programme_id"],
            workstream_id=created.object_ids["workstream_id"],
            user_id=programme_fixture.owner_id,
            role="contributor",
            effective_from=date.today(),
            effective_to=None,
            expected_revision=1,
            command_key="assign-contributor",
        )


def test_programme_role_assignment_locks_and_advances_programme_revision(programme_fixture):
    """Catches programme-scoped role writes ignoring their expected aggregate revision."""
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="programme-role-revision",
        request=_intake(programme_fixture.owner_id),
    )
    programme_id = created.object_ids["programme_id"]
    assigned = TransformationProgrammeService.assign_role(
        actor=programme_fixture.actor,
        programme_id=programme_id,
        workstream_id=None,
        user_id=programme_fixture.owner_id,
        role="contributor",
        effective_from=date.today(),
        effective_to=None,
        expected_revision=1,
        command_key="programme-role-write",
    )
    assert assigned.response["revision"] == 2
    with Session(db.engine) as session:
        assert session.get(StrategicInitiative, programme_id).revision == 2

    with pytest.raises(CommandConflict, match="stale_revision"):
        TransformationProgrammeService.assign_role(
            actor=programme_fixture.actor,
            programme_id=programme_id,
            workstream_id=None,
            user_id=programme_fixture.owner_id,
            role="evidence_owner",
            effective_from=date.today(),
            effective_to=None,
            expected_revision=1,
            command_key="stale-programme-role-write",
        )


def test_workstream_role_assignment_locks_and_advances_workstream_revision(programme_fixture):
    """Catches workstream-scoped role writes leaving the aggregate revision unchanged."""
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="workstream-role-revision",
        request=_intake(programme_fixture.owner_id),
    )
    assigned = TransformationProgrammeService.assign_role(
        actor=programme_fixture.actor,
        programme_id=created.object_ids["programme_id"],
        workstream_id=created.object_ids["workstream_id"],
        user_id=programme_fixture.owner_id,
        role="contributor",
        effective_from=date.today(),
        effective_to=None,
        expected_revision=1,
        command_key="workstream-role-write",
    )
    assert assigned.response["revision"] == 2
    with Session(db.engine) as session:
        assert session.get(ProgrammeWorkstream, created.object_ids["workstream_id"]).revision == 2
    with pytest.raises(CommandConflict, match="stale_revision"):
        TransformationProgrammeService.assign_role(
            actor=programme_fixture.actor,
            programme_id=created.object_ids["programme_id"],
            workstream_id=created.object_ids["workstream_id"],
            user_id=programme_fixture.owner_id,
            role="evidence_owner",
            effective_from=date.today(),
            effective_to=None,
            expected_revision=1,
            command_key="stale-workstream-role-write",
        )


def test_concurrent_programme_role_assignments_allow_one_revision_winner(app, programme_fixture):
    """Catches two writers both committing against the same programme revision."""
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="concurrent-role-programme",
        request=_intake(programme_fixture.owner_id),
    )
    start = threading.Barrier(3)
    results = []
    errors = []

    def assign(role):
        with app.app_context():
            start.wait(timeout=5)
            try:
                results.append(
                    TransformationProgrammeService.assign_role(
                        actor=programme_fixture.actor,
                        programme_id=created.object_ids["programme_id"],
                        workstream_id=None,
                        user_id=programme_fixture.owner_id,
                        role=role,
                        effective_from=date.today(),
                        effective_to=None,
                        expected_revision=1,
                        command_key=f"concurrent-role-{role}",
                    )
                )
            except Exception as error:  # asserted after both workers finish
                errors.append(error)
            finally:
                db.session.remove()

    threads = [
        threading.Thread(target=assign, args=(role,), daemon=True)
        for role in ("contributor", "evidence_owner")
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 1
    assert results[0].response["revision"] == 2
    assert len(errors) == 1
    assert isinstance(errors[0], CommandConflict)
    assert errors[0].reason == "stale_revision"


def test_update_objective_checks_revision_and_changes_only_owned_fields(programme_fixture):
    """Catches stale or broad workstream updates bypassing the locked command handler."""
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="objective-programme",
        request=_intake(programme_fixture.owner_id),
    )
    result = TransformationProgrammeService.update_objective(
        actor=programme_fixture.actor,
        workstream_id=created.object_ids["workstream_id"],
        objective="Remove duplicate services while protecting customer journeys",
        scope_expression={"business_units": ["Retail", "Commercial"]},
        expected_revision=1,
        command_key="objective-update",
    )
    assert result.response["revision"] == 2
    with Session(db.engine) as session:
        stream = session.get(ProgrammeWorkstream, created.object_ids["workstream_id"])
        assert stream.objective == "Remove duplicate services while protecting customer journeys"
        assert stream.scope_expression == {"business_units": ["Retail", "Commercial"]}
        assert stream.lifecycle_stage == "objective"
        assert stream.workstream_type == "application_rationalisation"

    with pytest.raises(CommandConflict, match="stale_revision"):
        TransformationProgrammeService.update_objective(
            actor=programme_fixture.actor,
            workstream_id=created.object_ids["workstream_id"],
            objective="Stale overwrite",
            scope_expression={"business_units": ["Retail"]},
            expected_revision=3,
            command_key="stale-objective-update",
        )


def test_archive_is_soft_versioned_and_retains_children(programme_fixture):
    """Catches programme archive deleting history or accepting a stale aggregate revision."""
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="archive-programme",
        request=_intake(programme_fixture.owner_id),
    )
    with pytest.raises(ValueError, match="archive rationale is required"):
        TransformationProgrammeService.archive(
            actor=programme_fixture.actor,
            programme_id=created.object_ids["programme_id"],
            rationale="   ",
            expected_revision=1,
            command_key="archive-without-rationale",
        )
    archived = TransformationProgrammeService.archive(
        actor=programme_fixture.actor,
        programme_id=created.object_ids["programme_id"],
        rationale="The programme was superseded by an approved portfolio decision.",
        expected_revision=1,
        command_key="archive-command",
    )
    assert archived.response["status"] == "archived"
    assert archived.response["revision"] == 2
    with Session(db.engine) as session:
        programme = session.get(StrategicInitiative, created.object_ids["programme_id"])
        assert programme.archived_at is not None
        assert programme.revision == 2
        assert session.scalar(
            select(db.func.count()).select_from(ProgrammeWorkstream).where(
                ProgrammeWorkstream.programme_id == programme.id,
                ProgrammeWorkstream.organization_id == programme_fixture.organization_id,
            )
        ) == 1
        event = session.scalar(
            select(OperationOutboxEvent).where(
                OperationOutboxEvent.organization_id
                == programme_fixture.organization_id,
                OperationOutboxEvent.operation_result_id
                == archived.operation_result_id,
                OperationOutboxEvent.event_type == "programme.archived",
            )
        )
        assert event.payload_json["rationale"] == (
            "The programme was superseded by an approved portfolio decision."
        )


def test_archived_programme_rejects_role_and_objective_mutations(programme_fixture):
    """Catches archived history remaining writable through aggregate child commands."""
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="archived-mutation-programme",
        request=_intake(programme_fixture.owner_id),
    )
    TransformationProgrammeService.archive(
        actor=programme_fixture.actor,
        programme_id=created.object_ids["programme_id"],
        rationale="This history is retained after closure.",
        expected_revision=1,
        command_key="archive-before-mutation",
    )
    with pytest.raises(CommandConflict, match="programme_archived"):
        TransformationProgrammeService.assign_role(
            actor=programme_fixture.actor,
            programme_id=created.object_ids["programme_id"],
            workstream_id=created.object_ids["workstream_id"],
            user_id=programme_fixture.owner_id,
            role="contributor",
            effective_from=date.today(),
            effective_to=None,
            expected_revision=1,
            command_key="archived-role-write",
        )
    with pytest.raises(CommandConflict, match="programme_archived"):
        TransformationProgrammeService.update_objective(
            actor=programme_fixture.actor,
            workstream_id=created.object_ids["workstream_id"],
            objective="Attempted archived change",
            scope_expression={"business_units": ["Retail"]},
            expected_revision=1,
            command_key="archived-objective-write",
        )
    view = TransformationProgrammeService.get_programme(
        actor=programme_fixture.actor,
        programme_id=created.object_ids["programme_id"],
    )
    assert view.lifecycle == "archived"
    assert view.next_action is None


def test_active_programme_read_prioritises_earliest_stage_across_detached_workstreams(
    programme_fixture,
):
    """Catches next action using ID rather than lifecycle risk after session detach."""
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="active-multiple-workstreams",
        request=_intake(programme_fixture.owner_id),
    )
    with Session(db.engine) as session, session.begin():
        first = session.get(ProgrammeWorkstream, created.object_ids["workstream_id"])
        first.lifecycle_stage = "approved"
        second = ProgrammeWorkstream(
            organization_id=programme_fixture.organization_id,
            programme_id=created.object_ids["programme_id"],
            workstream_type="process",
            objective="Simplify fulfilment hand-offs",
            scope_expression={"business_units": ["Retail"]},
            lifecycle_stage="objective",
            lead_id=programme_fixture.owner_id,
            target_date=date(2027, 9, 30),
        )
        session.add(second)
        session.flush()
        second_id = second.id

    view = TransformationProgrammeService.get_programme(
        actor=programme_fixture.actor,
        programme_id=created.object_ids["programme_id"],
    )

    assert view.workstream_ids == (created.object_ids["workstream_id"], second_id)
    assert view.next_action is not None
    assert view.next_action.resource_id == second_id


@pytest.mark.parametrize("non_finite", ["NaN", "Infinity", "-Infinity", Decimal("NaN")])
def test_intake_rejects_non_finite_measure_values(programme_fixture, non_finite):
    """Catches PostgreSQL Numeric special values entering governed outcome measures."""
    intake = _intake(programme_fixture.owner_id)
    measure = {**intake.outcome["measure"], "target_value": non_finite}
    outcome = {**intake.outcome, "measure": measure}
    with pytest.raises(ValueError, match="finite"):
        TransformationProgrammeService.create_programme(
            actor=programme_fixture.actor,
            command_key=f"non-finite-{str(non_finite)}",
            request=_intake(programme_fixture.owner_id, outcome=outcome),
        )


@pytest.mark.parametrize(
    "failing_type",
    [ProgrammeRoleAssignment, ProgrammeWorkstream, ProgrammeOutcomeCommitment, MeasureDefinition],
)
def test_subordinate_failure_rolls_back_entire_programme_graph(programme_fixture, failing_type):
    """Catches any subordinate insert leaving a partial programme aggregate committed."""
    def fail_selected_insert(session, _flush_context, _instances):
        if any(isinstance(row, failing_type) for row in session.new):
            raise RuntimeError(f"forced {failing_type.__name__} failure")

    event.listen(Session, "before_flush", fail_selected_insert)
    try:
        with pytest.raises(RuntimeError, match="forced"):
            TransformationProgrammeService.create_programme(
                actor=programme_fixture.actor,
                command_key=f"rollback-{failing_type.__name__}",
                request=_intake(programme_fixture.owner_id),
            )
    finally:
        event.remove(Session, "before_flush", fail_selected_insert)

    rows = _rows(programme_fixture)
    assert rows["programme"] is None
    assert rows["workstreams"] == rows["roles"] == rows["outcomes"] == rows["measures"] == []
    assert rows["solutions"] == 0
    assert rows["events"] == []


# --------------------------------------------------------------------- #
# R1-01: programme write authority is persona- or assignment-based only #
# (security.md 3.2, findings S1 and S2)                                 #
# --------------------------------------------------------------------- #


def test_programme_write_roles_exclude_admin_derived():
    """T-A1 (S1): no write role set admits an admin-derived or legacy-Role
    string; ARCHIVE_ROLES keeps only the owner and the org-governance
    escape hatch."""
    from app.modules.transformation_room.programme_service import (
        ARCHIVE_ROLES,
        CREATE_ROLES,
        OBJECTIVE_ROLES,
        ROLE_ASSIGNMENT_ROLES,
    )

    admin_derived = {"platform_admin", "organization_admin", "administrator"}
    assert not (CREATE_ROLES & admin_derived)
    assert not (OBJECTIVE_ROLES & admin_derived)
    assert not (ROLE_ASSIGNMENT_ROLES & admin_derived)
    assert ARCHIVE_ROLES == {"programme_owner", "organization_admin"}


def test_admin_flags_alone_do_not_authorise_write_but_do_authorise_read(programme_fixture):
    """T-A2 (S1): a user left on the enterprise_role column default, with Role
    Administrator, is_org_admin and is_platform_admin all set, cannot create a
    programme -- but organization_admin (from is_org_admin) still reaches
    READ_ROLES, so list_programmes succeeds."""
    from app.models.user import Permission, Role

    with Session(db.engine) as session, session.begin():
        admin_role = session.scalar(select(Role).where(Role.name == "Administrator"))
        if admin_role is None:
            admin_role = Role(name="Administrator", permissions=Permission.ADMINISTER, index="admin", default=False)
            session.add(admin_role)
            session.flush()
        user = User(
            email=f"admin-default-{uuid.uuid4().hex[:10]}@example.test",
            organization_id=programme_fixture.organization_id,
            confirmed=True,
            is_org_admin=True,
            is_platform_admin=True,
            role=admin_role,
        )
        # enterprise_role left at the column server_default ("platform_admin").
        session.add(user)
        session.flush()
        user_id = user.id

    try:
        actor = ActorContext(
            user_id, programme_fixture.organization_id, frozenset({"platform_admin"}), "t-a2"
        )
        with pytest.raises(NotAuthorised, match="programme_create_not_authorised"):
            TransformationProgrammeService.create_programme(
                actor=actor,
                command_key=f"t-a2-{uuid.uuid4().hex[:8]}",
                request=_intake(user_id),
            )
        # Read succeeds: organization_admin (from is_org_admin) is in READ_ROLES.
        TransformationProgrammeService.list_programmes(actor=actor)
    finally:
        with db.engine.begin() as connection:
            connection.execute(text('DELETE FROM "users" WHERE id = :id'), {"id": user_id})


def test_viewer_permission_blocks_write_but_not_read(programme_fixture):
    """T-A3 (S2): a Viewer-permissioned user (Role.permissions == 0) whose
    enterprise_role string is a write role is still refused every write --
    Permission.GENERAL, not the role string, gates programme writes. Read
    stays allowed because authorise_read / list_programmes do not call
    _require_write_permission."""
    from app.models.user import Role

    with Session(db.engine) as session, session.begin():
        viewer_role = session.scalar(select(Role).where(Role.name == "Viewer"))
        if viewer_role is None:
            viewer_role = Role(name="Viewer", permissions=0, index="main", default=False)
            session.add(viewer_role)
            session.flush()
        user = User(
            email=f"viewer-ea-{uuid.uuid4().hex[:10]}@example.test",
            organization_id=programme_fixture.organization_id,
            confirmed=True,
            enterprise_role="enterprise_architect",
            role=viewer_role,
        )
        session.add(user)
        session.flush()
        user_id = user.id

    try:
        actor = ActorContext(
            user_id, programme_fixture.organization_id, frozenset({"enterprise_architect"}), "t-a3"
        )
        with pytest.raises(NotAuthorised, match="write_permission_required"):
            TransformationProgrammeService.create_programme(
                actor=actor,
                command_key=f"t-a3-{uuid.uuid4().hex[:8]}",
                request=_intake(user_id),
            )
        # Read is unaffected by Permission.GENERAL: enterprise_architect is in
        # READ_ROLES regardless of the user's Role bitfield.
        TransformationProgrammeService.list_programmes(actor=actor)
    finally:
        with db.engine.begin() as connection:
            connection.execute(text('DELETE FROM "users" WHERE id = :id'), {"id": user_id})
