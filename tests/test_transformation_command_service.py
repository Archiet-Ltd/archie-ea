"""PostgreSQL crash, replay, and stale-worker proof for transformation commands."""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

import pytest
from flask import current_app
from sqlalchemy import select, text
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import db
from app.models.organization import Organization
from app.models.strategic import StrategicInitiative
from app.models.transformation_execution import (
    CommandMaterialisation,
    CommandIdempotencyRecord,
    OperationOutboxEvent,
    OperationResult,
)
from app.models.transformation_db_guards import ensure_transformation_db_guards
from app.models.user import User
from app.modules.transformation_room.command_service import (
    CommandService,
    NATURAL_KEY_CONTENDER_REASON,
    canonical_request_digest,
)
from app.modules.transformation_room.domain import (
    ActorContext,
    CommandConflict,
    CommandResult,
    DomainMutationResult,
    KnownPreCommitTransient,
    NotAuthorised,
    NotFound,
    StaleClaim,
)


@dataclass(frozen=True)
class CommandFixture:
    actor: ActorContext
    organization_id: int
    user_id: int
    domain_name: str


@pytest.fixture(scope="module", autouse=True)
def command_guard_schema(app, _schema):
    """Install the current signed-command boundary for focused Task 3 runs."""
    with app.app_context(), db.engine.begin() as connection:
        ensure_transformation_db_guards(connection)


def test_database_now_normalizes_session_timezone_to_utc(app):
    """Catches date-sensitive commands hashing a database-local calendar day."""
    with app.app_context(), Session(db.engine) as session, session.begin():
        session.execute(text("SET LOCAL TIME ZONE 'Asia/Tokyo'"))
        value = CommandService._database_now(session)

    assert value.utcoffset() == timedelta(0)


@pytest.fixture
def command_fixture(app, _schema):
    """Committed rows are required so independent workers see the same state."""
    suffix = uuid.uuid4().hex[:12]
    with app.app_context():
        db.session.remove()
        organization = Organization(
            name=f"Command Org {suffix}", slug=f"command-org-{suffix}"
        )
        db.session.add(organization)
        db.session.flush()
        user = User(
            email=f"command-{suffix}@example.test",
            organization_id=organization.id,
            confirmed=True,
            enterprise_role="enterprise_architect",
        )
        db.session.add(user)
        db.session.flush()
        organization_id = organization.id
        user_id = user.id
        db.session.commit()
        db.session.remove()

        original_lease = app.config.get("TRANSFORMATION_COMMAND_LEASE_SECONDS")
        # Ordinary command tests must not turn into expiry tests merely because
        # coverage instrumentation or a busy CI database takes over 120 ms.
        # Tests that exercise reclamation expire their receipt explicitly via
        # ``_wait_for_expiry`` below.
        app.config["TRANSFORMATION_COMMAND_LEASE_SECONDS"] = 5.0
        fixture = CommandFixture(
            actor=ActorContext(
                user_id=user_id,
                organization_id=organization_id,
                roles=frozenset({"enterprise_architect"}),
                request_id=f"request-{suffix}",
            ),
            organization_id=organization_id,
            user_id=user_id,
            domain_name=f"Programme {suffix}",
        )
        try:
            yield fixture
        finally:
            db.session.remove()
            if original_lease is None:
                app.config.pop("TRANSFORMATION_COMMAND_LEASE_SECONDS", None)
            else:
                app.config["TRANSFORMATION_COMMAND_LEASE_SECONDS"] = original_lease
            # Append-only guards admit the organisation's own rows when the
            # deletion service's transaction-local marker is set; no trigger is
            # disabled.
            from app.services.org_deletion_service import OrganizationDeletionService

            if db.session.get(Organization, organization_id) is not None:
                OrganizationDeletionService.delete_organization(organization_id)


def _mutation(fixture: CommandFixture, *, events: int = 1):
    def handler(session, _claim):
        programme = StrategicInitiative(
            organization_id=fixture.organization_id,
            name=fixture.domain_name,
            record_kind="transformation_programme",
            description="Created by a fenced command",
        )
        session.add(programme)
        session.flush()
        return DomainMutationResult(
            object_ids={"programme_id": programme.id},
            response={"programme_id": programme.id, "name": programme.name},
            outbox_events=tuple(
                {
                    "event_type": "programme.created",
                    "payload": {"programme_id": programme.id, "ordinal": ordinal},
                }
                for ordinal in range(events)
            ),
        )

    return handler


def _allow_command(_session, _actor, _operation, _natural_key):
    return None


def _execute(fixture: CommandFixture, *, key="same", payload=None, handler=None):
    return CommandService.execute(
        actor=fixture.actor,
        operation="programme.create",
        idempotency_key=key,
        payload=payload or {"name": fixture.domain_name, "scope": {"b": 2, "a": 1}},
        natural_key=f"programme-intake:{key}",
        authorizer=_allow_command,
        handler=handler or _mutation(fixture),
    )


def _counts(fixture: CommandFixture):
    with Session(db.engine) as session:
        domain = session.scalar(
            select(db.func.count())
            .select_from(StrategicInitiative)
            .where(
                StrategicInitiative.organization_id == fixture.organization_id,
                StrategicInitiative.name == fixture.domain_name,
            )
        )
        results = session.scalar(
            select(db.func.count())
            .select_from(OperationResult)
            .where(OperationResult.organization_id == fixture.organization_id)
        )
        events = session.scalar(
            select(db.func.count())
            .select_from(OperationOutboxEvent)
            .where(OperationOutboxEvent.organization_id == fixture.organization_id)
        )
    return domain, results, events


def _receipt(fixture: CommandFixture, key="same"):
    with Session(db.engine) as session:
        return session.execute(
            select(CommandIdempotencyRecord).where(
                CommandIdempotencyRecord.organization_id == fixture.organization_id,
                CommandIdempotencyRecord.actor_id == fixture.user_id,
                CommandIdempotencyRecord.operation == "programme.create",
                CommandIdempotencyRecord.idempotency_key == key,
            )
        ).scalar_one()


def _wait_for_expiry(claim):
    """Let a live lease actually expire, in real wall-clock time.

    archie_guard_transformation_receipt correctly refuses a direct write that
    shortens a live receipt's own lease_expires_at: every "heartbeat"/"reclaim"/
    "finalise" branch requires the lease to move only forward, or a distinct
    claim_token the test has no legitimate way to mint. The one thing a test may
    do instead is wait: TRANSFORMATION_COMMAND_LEASE_SECONDS is already cut to a
    few seconds for exactly this.
    """
    lease_seconds = current_app.config.get("TRANSFORMATION_COMMAND_LEASE_SECONDS", 5.0)
    time.sleep(lease_seconds + 0.25)
    with db.engine.begin() as connection:
        expired = connection.scalar(
            text(
                "SELECT lease_expires_at <= clock_timestamp() "
                "FROM command_idempotency_records WHERE id = :receipt_id"
            ),
            {"receipt_id": claim.receipt_id},
        )
    assert expired is True


@contextmanager
def _deferred_result_failure(organization_id: int):
    suffix = uuid.uuid4().hex[:10]
    function = f"test_fail_result_commit_{suffix}"
    trigger = f"trg_test_fail_result_commit_{suffix}"
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            f"""
            CREATE FUNCTION {function}() RETURNS trigger
            LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
            BEGIN
                IF NEW.organization_id = {int(organization_id)} THEN
                    RAISE EXCEPTION 'simulated failure after operation result insert';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        connection.exec_driver_sql(
            f"""
            CREATE CONSTRAINT TRIGGER {trigger}
            AFTER INSERT ON operation_results
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION {function}()
            """
        )
    try:
        yield
    finally:
        db.session.remove()
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS {trigger} ON operation_results"
            )
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}()")


def test_canonical_digest_is_stable_for_equivalent_nested_json(command_fixture):
    """Catches request-key ordering being bound as a different command identity."""
    left = {"z": [3, {"b": False, "a": None}], "name": "é"}
    right = {"name": "é", "z": [3, {"a": None, "b": False}]}

    assert canonical_request_digest(left) == canonical_request_digest(right)
    assert len(canonical_request_digest(left)) == 64


def test_execute_owns_domain_transaction_after_request_identity_read(command_fixture):
    """Catches request user loading making the command transaction impossible to begin."""
    loaded_user_id = db.session.scalar(
        select(User.id).where(
            User.id == command_fixture.user_id,
            User.organization_id == command_fixture.organization_id,
        )
    )
    assert loaded_user_id == command_fixture.user_id
    assert db.session().in_transaction() is True

    result = _execute(command_fixture, key="request-read")

    assert result.created is True
    assert db.session().in_transaction() is True
    assert _counts(command_fixture) == (1, 1, 1)


def test_preflushed_orm_write_is_not_rolled_back_by_command(command_fixture):
    """Catches a flushed caller row disappearing at the command boundary."""
    pending = StrategicInitiative(
        organization_id=command_fixture.organization_id,
        name=f"Caller pending {uuid.uuid4().hex[:8]}",
        record_kind="transformation_programme",
    )
    db.session.add(pending)
    db.session.flush()
    pending_id = pending.id

    result = _execute(command_fixture, key="caller-orm-write")

    assert result.created is True
    assert db.session().in_transaction() is True
    assert db.session.scalar(
        select(StrategicInitiative.id).where(
            StrategicInitiative.id == pending_id,
            StrategicInitiative.organization_id == command_fixture.organization_id,
        )
    ) == pending_id
    db.session.rollback()
    with Session(db.engine) as session:
        assert session.scalar(
            select(StrategicInitiative.id).where(
                StrategicInitiative.id == pending_id,
                StrategicInitiative.organization_id == command_fixture.organization_id,
            )
        ) is None
    assert _counts(command_fixture) == (1, 1, 1)


def test_raw_sql_write_is_not_rolled_back_by_command(command_fixture):
    """Catches raw SQL work being invisible to new/dirty/deleted heuristics."""
    original_name = db.session.scalar(
        select(Organization.name).where(
            Organization.id == command_fixture.organization_id
        )
    )
    pending_name = f"{original_name} pending"
    db.session.execute(
        text(
            "UPDATE organizations SET name = :name "
            "WHERE id = :organization_id"
        ),
        {"name": pending_name, "organization_id": command_fixture.organization_id},
    )

    result = _execute(command_fixture, key="caller-raw-write")

    assert result.created is True
    assert db.session().in_transaction() is True
    assert db.session.scalar(
        select(Organization.name).where(
            Organization.id == command_fixture.organization_id
        )
    ) == pending_name
    db.session.rollback()
    with Session(db.engine) as session:
        assert session.scalar(
            select(Organization.name).where(
                Organization.id == command_fixture.organization_id
            )
        ) == original_name
    assert _counts(command_fixture) == (1, 1, 1)


def test_claim_then_crash_reclaims_only_after_expiry(command_fixture):
    """Catches an independent claim transaction becoming a permanent orphan."""
    digest = canonical_request_digest({"name": command_fixture.domain_name})
    claim = CommandService.claim_or_reconcile(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="claim-crash",
        request_digest=digest,
        natural_key="programme-intake:claim-crash",
        authorizer=_allow_command,
    )

    assert claim.generation == 1
    assert _receipt(command_fixture, "claim-crash").status == "in_progress"
    _wait_for_expiry(claim)

    result = _execute(
        command_fixture,
        key="claim-crash",
        payload={"name": command_fixture.domain_name},
    )

    assert result.created is True
    assert _receipt(command_fixture, "claim-crash").lease_generation == 2
    assert _counts(command_fixture) == (1, 1, 1)


def test_domain_insert_then_known_failure_rolls_back_and_recovers(command_fixture):
    """Catches domain writes escaping before a retryable failure is recorded."""
    attempts = 0

    def fail_after_insert(session, claim):
        nonlocal attempts
        attempts += 1
        _mutation(command_fixture)(session, claim)
        raise KnownPreCommitTransient("database_temporarily_unavailable")

    with pytest.raises(KnownPreCommitTransient):
        _execute(command_fixture, key="retryable", handler=fail_after_insert)

    receipt = _receipt(command_fixture, "retryable")
    assert receipt.status == "retryable_failure"
    assert receipt.operation_result_id is None
    assert receipt.attempt_count == 1
    assert receipt.last_error_class == "KnownPreCommitTransient"
    assert _counts(command_fixture) == (0, 0, 0)

    recovered = _execute(command_fixture, key="retryable")

    assert recovered.created is True
    assert attempts == 1
    assert _receipt(command_fixture, "retryable").lease_generation == 2
    assert _counts(command_fixture) == (1, 1, 1)


def test_authorisation_failure_is_terminal_and_never_replayed_as_success(
    command_fixture,
):
    """Catches known non-retryable denials being reclaimed and re-executed."""
    calls = 0

    def deny(_session, _claim):
        nonlocal calls
        calls += 1
        raise NotAuthorised("programme_create_not_authorised")

    with pytest.raises(NotAuthorised):
        _execute(command_fixture, key="denied", handler=deny)

    receipt = _receipt(command_fixture, "denied")
    assert receipt.status == "failed_non_retryable"
    assert receipt.operation_result_id is None
    assert receipt.lease_expires_at is None
    assert receipt.completed_at is not None
    assert receipt.last_error_class == "NotAuthorised"
    assert _counts(command_fixture) == (0, 0, 0)

    with pytest.raises(CommandConflict) as replay:
        _execute(command_fixture, key="denied", handler=deny)
    assert replay.value.reason == "failed_non_retryable"
    assert calls == 1
    assert _counts(command_fixture) == (0, 0, 0)


@pytest.mark.parametrize(
    ("error_type", "reason"),
    (
        (NotAuthorised, "programme_create_not_authorised"),
        (NotFound, "programme_owner_not_found"),
        (CommandConflict, "programme_business_rule_conflict"),
    ),
)
def test_terminal_business_failure_does_not_reconcile_a_later_exact_result(
    command_fixture,
    error_type,
    reason,
):
    """Only a proven natural-key contender may leave terminal failure."""
    payload = {"name": command_fixture.domain_name, "scope": {"a": 1}}
    natural_key = f"programme-intake:terminal-{error_type.__name__.lower()}"
    failed_key = f"terminal-{error_type.__name__.lower()}"

    def execute(key, handler):
        return CommandService.execute(
            actor=command_fixture.actor,
            operation="programme.create",
            idempotency_key=key,
            payload=payload,
            natural_key=natural_key,
            authorizer=_allow_command,
            handler=handler,
        )

    with pytest.raises(error_type, match=reason):
        execute(
            failed_key,
            lambda _session, _claim: (_ for _ in ()).throw(error_type(reason)),
        )

    winner = execute(f"winner-{failed_key}", _mutation(command_fixture))

    with pytest.raises(CommandConflict) as replay:
        execute(
            failed_key,
            lambda _session, _claim: pytest.fail(
                "a business-terminal receipt reran the immutable operation"
            ),
        )

    failed_receipt = _receipt(command_fixture, failed_key)
    assert replay.value.reason == "failed_non_retryable"
    assert failed_receipt.status == "failed_non_retryable"
    assert failed_receipt.operation_result_id is None
    assert winner.operation_result_id is not None


def test_result_then_deferred_commit_failure_is_atomic(command_fixture):
    """Catches a result/outbox/domain row surviving a failure at COMMIT."""
    with _deferred_result_failure(command_fixture.organization_id):
        with pytest.raises(Exception, match="simulated failure after operation result insert"):
            _execute(command_fixture, key="commit-failure", handler=_mutation(command_fixture, events=2))

    assert _counts(command_fixture) == (0, 0, 0)
    receipt = _receipt(command_fixture, "commit-failure")
    assert receipt.status == "in_progress"
    assert receipt.operation_result_id is None

    _wait_for_expiry(
        CommandService.claim_from_record(receipt)
    )
    recovered = _execute(command_fixture, key="commit-failure", handler=_mutation(command_fixture, events=2))

    assert recovered.created is True
    assert _counts(command_fixture) == (1, 1, 2)


def test_committed_result_replays_after_simulated_lost_response(command_fixture):
    """Catches a lost HTTP response causing duplicate committed effects."""
    committed = _execute(command_fixture, key="lost-response", handler=_mutation(command_fixture, events=2))
    # The transport loses ``committed`` here; the client repeats the same request.
    replayed = _execute(command_fixture, key="lost-response", handler=_mutation(command_fixture, events=2))

    assert committed.created is True and committed.idempotent is False
    assert replayed.created is False and replayed.idempotent is True
    assert replayed.operation_result_id == committed.operation_result_id
    assert replayed.object_ids == committed.object_ids
    assert replayed.response == committed.response
    assert _counts(command_fixture) == (1, 1, 2)
    with Session(db.engine) as session:
        event_ids = session.scalars(
            select(OperationOutboxEvent.event_id)
            .where(OperationOutboxEvent.organization_id == command_fixture.organization_id)
            .order_by(OperationOutboxEvent.ordinal)
        ).all()
    assert len(event_ids) == len(set(event_ids)) == 2
    assert all(uuid.UUID(event_id).version == 5 for event_id in event_ids)


def test_new_idempotency_key_replays_one_natural_result_with_original_provenance(
    command_fixture,
):
    """Transport-key rotation must not rewrite the effect's receipt identity."""
    payload = {"name": command_fixture.domain_name, "scope": {"a": 1}}
    natural_key = "programme-intake:stable-natural-operation"

    def execute(key, handler):
        return CommandService.execute(
            actor=command_fixture.actor,
            operation="programme.create",
            idempotency_key=key,
            payload=payload,
            natural_key=natural_key,
            authorizer=_allow_command,
            handler=handler,
        )

    first = execute("stable-natural-first", _mutation(command_fixture))
    replay = execute(
        "stable-natural-second",
        lambda _session, _claim: pytest.fail(
            "a new transport key reran an immutable natural operation"
        ),
    )

    assert replay.created is False and replay.idempotent is True
    assert replay.operation_result_id == first.operation_result_id
    assert replay.object_ids == first.object_ids
    assert replay.response == first.response
    assert _counts(command_fixture) == (1, 1, 1)
    with Session(db.engine) as session:
        result = session.get(OperationResult, first.operation_result_id)
        materialisation = session.scalar(
            select(CommandMaterialisation).where(
                CommandMaterialisation.organization_id
                == command_fixture.organization_id,
                CommandMaterialisation.operation == "programme.create",
                CommandMaterialisation.natural_key == natural_key,
            )
        )
        receipts = session.scalars(
            select(CommandIdempotencyRecord)
            .where(
                CommandIdempotencyRecord.organization_id
                == command_fixture.organization_id,
                CommandIdempotencyRecord.operation == "programme.create",
                CommandIdempotencyRecord.natural_key == natural_key,
            )
            .order_by(CommandIdempotencyRecord.id)
        ).all()
    assert len(receipts) == 2
    assert all(receipt.status == "succeeded" for receipt in receipts)
    assert {receipt.operation_result_id for receipt in receipts} == {result.id}
    assert result.receipt_id == materialisation.receipt_id == receipts[0].id
    assert result.receipt_generation == materialisation.receipt_generation == 1


def test_failed_contender_reconciles_exact_natural_result_on_retry(
    command_fixture,
):
    """A failed race receipt may converge only through a fresh signed claim."""
    payload = {"name": command_fixture.domain_name, "scope": {"a": 1}}
    natural_key = "programme-intake:failed-contender-reconcile"
    request_digest = canonical_request_digest(payload)

    failed_claim = CommandService.claim_or_reconcile(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="failed-contender",
        request_digest=request_digest,
        natural_key=natural_key,
        authorizer=_allow_command,
    )
    original_failed_token = failed_claim.claim_token
    assert CommandService.mark_non_retryable(
        actor=command_fixture.actor,
        claim=failed_claim,
        error_class="_NaturalKeyContenderConflict",
        terminal_reason=NATURAL_KEY_CONTENDER_REASON,
    ) is True

    def execute(key, handler):
        return CommandService.execute(
            actor=command_fixture.actor,
            operation="programme.create",
            idempotency_key=key,
            payload=payload,
            natural_key=natural_key,
            authorizer=_allow_command,
            handler=handler,
        )

    winner = execute("successful-contender", _mutation(command_fixture))
    replay = execute(
        "failed-contender",
        lambda _session, _claim: pytest.fail(
            "the failed contender reran the immutable natural operation"
        ),
    )
    second_replay = execute(
        "failed-contender",
        lambda _session, _claim: pytest.fail(
            "the reconciled contender reran the immutable natural operation"
        ),
    )

    assert replay.created is False and replay.idempotent is True
    assert second_replay.created is False and second_replay.idempotent is True
    assert (
        replay.operation_result_id
        == second_replay.operation_result_id
        == winner.operation_result_id
    )
    assert replay.object_ids == second_replay.object_ids == winner.object_ids
    assert replay.response == second_replay.response == winner.response
    assert _counts(command_fixture) == (1, 1, 1)

    with Session(db.engine) as session:
        result = session.get(OperationResult, winner.operation_result_id)
        materialisation = session.scalar(
            select(CommandMaterialisation).where(
                CommandMaterialisation.organization_id
                == command_fixture.organization_id,
                CommandMaterialisation.operation == "programme.create",
                CommandMaterialisation.natural_key == natural_key,
            )
        )
        receipts = {
            receipt.idempotency_key: receipt
            for receipt in session.scalars(
                select(CommandIdempotencyRecord).where(
                    CommandIdempotencyRecord.organization_id
                    == command_fixture.organization_id,
                    CommandIdempotencyRecord.operation == "programme.create",
                    CommandIdempotencyRecord.natural_key == natural_key,
                )
            ).all()
        }

    failed_receipt = receipts["failed-contender"]
    winner_receipt = receipts["successful-contender"]
    assert failed_receipt.status == winner_receipt.status == "succeeded"
    assert (
        failed_receipt.operation_result_id
        == winner_receipt.operation_result_id
        == result.id
    )
    assert failed_receipt.lease_generation == failed_claim.generation + 1
    assert failed_receipt.attempt_count == 2
    assert failed_receipt.claim_token != original_failed_token
    assert failed_receipt.last_error_class is None
    assert failed_receipt.terminal_reason == NATURAL_KEY_CONTENDER_REASON
    assert result.receipt_id == materialisation.receipt_id == winner_receipt.id
    assert result.receipt_generation == materialisation.receipt_generation == 1


def test_rejected_signed_claim_cannot_be_replayed_after_winner_appears(
    command_fixture,
    monkeypatch,
):
    """A rejected claim bearer is consumed before an exact result can appear."""
    payload = {"name": command_fixture.domain_name, "scope": {"a": 1}}
    natural_key = "programme-intake:consumed-contender-claim"
    request_digest = canonical_request_digest(payload)
    failed_claim = CommandService.claim_or_reconcile(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="consumed-contender",
        request_digest=request_digest,
        natural_key=natural_key,
        authorizer=_allow_command,
    )
    assert CommandService.mark_non_retryable(
        actor=command_fixture.actor,
        claim=failed_claim,
        error_class="_NaturalKeyContenderConflict",
        terminal_reason=NATURAL_KEY_CONTENDER_REASON,
    ) is True

    captured = {}
    real_signed_capability = CommandService._signed_capability

    def capture_claim(payload_document):
        document, capability = real_signed_capability(payload_document)
        if (
            payload_document.get("schema_version")
            == "transformation-command-claim-r1"
            and not captured
        ):
            captured.update(document=document, capability=capability)
        return document, capability

    monkeypatch.setattr(
        CommandService, "_signed_capability", staticmethod(capture_claim)
    )

    with pytest.raises(CommandConflict, match="failed_non_retryable"):
        CommandService.claim_or_reconcile(
            actor=command_fixture.actor,
            operation="programme.create",
            idempotency_key="consumed-contender",
            request_digest=request_digest,
            natural_key=natural_key,
            authorizer=_allow_command,
        )

    assert json.loads(captured["document"])["contender_reason"] == (
        NATURAL_KEY_CONTENDER_REASON
    )

    def replay_captured_claim():
        with Session(db.engine) as session, session.begin():
            session.execute(
                text(
                    "SELECT * FROM public.archie_claim_transformation_command("
                    "CAST(:document AS text), CAST(:capability AS text))"
                ),
                captured,
            ).all()

    with pytest.raises(DBAPIError, match="command capability is invalid"):
        replay_captured_claim()

    winner = CommandService.execute(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="consumed-winner",
        payload=payload,
        natural_key=natural_key,
        authorizer=_allow_command,
        handler=_mutation(command_fixture),
    )

    with pytest.raises(DBAPIError, match="command capability is invalid"):
        replay_captured_claim()

    fresh = CommandService.execute(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="consumed-contender",
        payload=payload,
        natural_key=natural_key,
        authorizer=_allow_command,
        handler=lambda _session, _claim: pytest.fail(
            "fresh contender reconciliation reran the immutable operation"
        ),
    )
    assert fresh.operation_result_id == winner.operation_result_id
    assert fresh.created is False and fresh.idempotent is True


def test_database_rejects_an_expired_signed_claim(command_fixture, monkeypatch):
    """Claim expiry is checked against database time, not caller wall time."""
    original_seconds = current_app.config.get(
        "TRANSFORMATION_COMMAND_CLAIM_CAPABILITY_SECONDS"
    )
    current_app.config["TRANSFORMATION_COMMAND_CLAIM_CAPABILITY_SECONDS"] = 0.1
    real_signed_capability = CommandService._signed_capability

    def delay_claim(payload_document):
        document, capability = real_signed_capability(payload_document)
        if payload_document.get("schema_version") == "transformation-command-claim-r1":
            time.sleep(0.2)
        return document, capability

    monkeypatch.setattr(
        CommandService, "_signed_capability", staticmethod(delay_claim)
    )
    try:
        with pytest.raises(DBAPIError, match="command capability is invalid"):
            CommandService.claim_or_reconcile(
                actor=command_fixture.actor,
                operation="programme.create",
                idempotency_key="expired-signed-claim",
                request_digest=canonical_request_digest(
                    {"name": command_fixture.domain_name}
                ),
                natural_key="programme-intake:expired-signed-claim",
                authorizer=_allow_command,
            )
    finally:
        if original_seconds is None:
            current_app.config.pop(
                "TRANSFORMATION_COMMAND_CLAIM_CAPABILITY_SECONDS", None
            )
        else:
            current_app.config[
                "TRANSFORMATION_COMMAND_CLAIM_CAPABILITY_SECONDS"
            ] = original_seconds

    with Session(db.engine) as session:
        receipt = session.scalar(
            select(CommandIdempotencyRecord).where(
                CommandIdempotencyRecord.organization_id
                == command_fixture.organization_id,
                CommandIdempotencyRecord.idempotency_key
                == "expired-signed-claim",
            )
        )
    assert receipt is None


@pytest.mark.parametrize("deleted_ordinal", (0, None))
def test_replay_restores_exact_missing_outbox_documents(
    command_fixture, deleted_ordinal
):
    """A surviving result repairs one missing event or the complete ordered set."""
    key = f"outbox-repair-{deleted_ordinal}"
    first = _execute(
        command_fixture,
        key=key,
        handler=_mutation(command_fixture, events=2),
    )
    with Session(db.engine) as session:
        before = session.execute(
            select(
                OperationOutboxEvent.ordinal,
                OperationOutboxEvent.event_id,
                OperationOutboxEvent.event_type,
                OperationOutboxEvent.payload_json,
            )
            .where(OperationOutboxEvent.operation_result_id == first.operation_result_id)
            .order_by(OperationOutboxEvent.ordinal)
        ).all()
    assert len(before) == 2
    with db.engine.begin() as connection:
        # The append-only guard admits a DELETE on this organisation's own rows
        # while the deletion service's transaction-local marker is set; no
        # trigger is disabled.
        connection.execute(
            text("INSERT INTO organization_deletions_in_progress (target_organization_id) "
                 "VALUES (:organization_id) ON CONFLICT DO NOTHING"),
            {"organization_id": command_fixture.organization_id},
        )
        connection.execute(
            text("SELECT set_config('archie.deleting_organisation', :organization_id, true)"),
            {"organization_id": str(command_fixture.organization_id)},
        )
        statement = (
            "DELETE FROM transformation_outbox_events "
            "WHERE operation_result_id = :result_id"
        )
        parameters = {"result_id": first.operation_result_id}
        if deleted_ordinal is not None:
            statement += " AND ordinal = :ordinal"
            parameters["ordinal"] = deleted_ordinal
        connection.execute(text(statement), parameters)
        connection.execute(
            text("DELETE FROM organization_deletions_in_progress WHERE target_organization_id = :organization_id"),
            {"organization_id": command_fixture.organization_id},
        )

    replay = _execute(
        command_fixture,
        key=key,
        handler=lambda _session, _claim: pytest.fail(
            "an outbox-only recovery reran domain work"
        ),
    )

    with Session(db.engine) as session:
        after = session.execute(
            select(
                OperationOutboxEvent.ordinal,
                OperationOutboxEvent.event_id,
                OperationOutboxEvent.event_type,
                OperationOutboxEvent.payload_json,
            )
            .where(OperationOutboxEvent.operation_result_id == first.operation_result_id)
            .order_by(OperationOutboxEvent.ordinal)
        ).all()
    assert replay.idempotent is True
    assert after == before


def test_replay_fails_closed_on_conflicting_extra_outbox_event(command_fixture):
    first = _execute(command_fixture, key="outbox-extra")
    with db.engine.begin() as connection:
        # The insert guard's own repair escape hatch (already used by
        # archie_persist_command_envelope/archie_repair_command_envelope)
        # admits this forged envelope outside its live fence; no trigger is
        # disabled.
        connection.execute(
            text("SELECT set_config('archie.signed_envelope_repair', 'on', true)")
        )
        connection.execute(
            text(
                "INSERT INTO transformation_outbox_events "
                "(organization_id, operation_result_id, event_id, ordinal, "
                "event_type, payload_json) VALUES "
                "(:organization_id, :result_id, :event_id, 99, "
                "'forged.extra', '{}'::jsonb)"
            ),
            {
                "organization_id": command_fixture.organization_id,
                "result_id": first.operation_result_id,
                "event_id": str(uuid.uuid4()),
            },
        )

    with pytest.raises(
        CommandConflict, match="operation_outbox_materialisation_mismatch"
    ):
        _execute(command_fixture, key="outbox-extra")


@pytest.mark.parametrize("forgery", ("cross_tenant", "stale_generation"))
def test_command_envelope_insert_requires_signed_live_fence(
    command_fixture, forgery
):
    """Direct SQL cannot bind another tenant or stale generation to a receipt."""
    payload = {"name": command_fixture.domain_name}
    claim = CommandService.claim_or_reconcile(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="forged-envelope",
        request_digest=canonical_request_digest(payload),
        natural_key="programme-intake:forged-envelope",
        authorizer=_allow_command,
    )

    with Session(db.engine) as session:
        transaction = session.begin()
        try:
            organization_id = command_fixture.organization_id
            generation = claim.generation
            if forgery == "cross_tenant":
                foreign_org = Organization(
                    name=f"Forged command org {uuid.uuid4().hex[:8]}",
                    slug=f"forged-command-{uuid.uuid4().hex[:8]}",
                )
                session.add(foreign_org)
                session.flush()
                organization_id = foreign_org.id
            else:
                generation += 1
            session.add(
                CommandMaterialisation(
                    organization_id=organization_id,
                    # Every foreign key is individually valid; the forged
                    # organization/actor/receipt combination is not.
                    actor_id=command_fixture.user_id,
                    operation="programme.create",
                    natural_key=claim.natural_key,
                    request_digest=claim.request_digest,
                    receipt_id=claim.receipt_id,
                    receipt_generation=generation,
                    object_ids={"programme_id": 999999},
                    response_json={"programme_id": 999999},
                    outbox_events=[],
                )
            )
            with pytest.raises(DBAPIError):
                session.flush()
        finally:
            transaction.rollback()


def test_expired_lease_reconciles_existing_result_without_handler(command_fixture):
    """Catches reclaim re-executing after result commit but before receipt repair."""
    payload = {"name": command_fixture.domain_name}
    digest = canonical_request_digest(payload)
    claim = CommandService.claim_or_reconcile(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="orphan-result",
        request_digest=digest,
        natural_key="programme-intake:orphan-result",
        authorizer=_allow_command,
    )
    with Session(db.engine) as session, session.begin():
        programme = StrategicInitiative(
            organization_id=command_fixture.organization_id,
            name=command_fixture.domain_name,
            record_kind="transformation_programme",
        )
        session.add(programme)
        session.flush()
        persisted = OperationResult(
            organization_id=command_fixture.organization_id,
            actor_id=command_fixture.user_id,
            operation="programme.create",
            natural_key="programme-intake:orphan-result",
            request_digest=digest,
            receipt_id=claim.receipt_id,
            receipt_generation=claim.generation,
            object_ids={"programme_id": programme.id},
            response_json={"programme_id": programme.id, "name": programme.name},
        )
        session.add(persisted)
        session.flush()
        persisted_id = persisted.id
        session.add(
            OperationOutboxEvent(
                organization_id=command_fixture.organization_id,
                operation_result_id=persisted.id,
                event_id=str(uuid.uuid4()),
                ordinal=0,
                event_type="programme.created",
                payload_json={"programme_id": programme.id},
            )
        )
    _wait_for_expiry(claim)

    called = False

    def must_not_run(_session, _claim):
        nonlocal called
        called = True
        raise AssertionError("handler reran despite canonical operation result")

    result = _execute(
        command_fixture,
        key="orphan-result",
        payload=payload,
        handler=must_not_run,
    )

    assert isinstance(result, CommandResult)
    assert result.operation_result_id == persisted_id
    assert result.created is False and result.idempotent is True
    assert called is False
    assert _receipt(command_fixture, "orphan-result").status == "succeeded"
    assert _counts(command_fixture) == (1, 1, 1)


def test_domain_row_only_crash_is_reconciled_by_operation_resolver(command_fixture):
    """Catches a committed natural-key row being duplicated when its result is absent."""
    payload = {"name": command_fixture.domain_name}
    digest = canonical_request_digest(payload)
    claim = CommandService.claim_or_reconcile(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="domain-row-only",
        request_digest=digest,
        natural_key="programme-intake:domain-row-only",
        authorizer=_allow_command,
    )
    with Session(db.engine) as session, session.begin():
        programme = StrategicInitiative(
            organization_id=command_fixture.organization_id,
            name=command_fixture.domain_name,
            record_kind="transformation_programme",
        )
        session.add(programme)
        session.flush()
        programme_id = programme.id
    _wait_for_expiry(claim)
    handler_called = False

    def resolve_programme(session, actor, natural_key, _claim):
        assert natural_key == "programme-intake:domain-row-only"
        recovered = session.execute(
            select(StrategicInitiative).where(
                StrategicInitiative.id == programme_id,
                StrategicInitiative.organization_id == actor.organization_id,
                StrategicInitiative.record_kind == "transformation_programme",
            )
        ).scalar_one_or_none()
        if recovered is None:
            return None
        return DomainMutationResult(
            object_ids={"programme_id": recovered.id},
            response={"programme_id": recovered.id, "name": recovered.name},
            outbox_events=(
                {
                    "event_type": "programme.created",
                    "payload": {"programme_id": recovered.id},
                },
            ),
        )

    def must_not_run(_session, _claim):
        nonlocal handler_called
        handler_called = True
        raise AssertionError("domain mutation reran after natural-key recovery")

    result = CommandService.execute(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="domain-row-only",
        payload=payload,
        natural_key="programme-intake:domain-row-only",
        authorizer=_allow_command,
        natural_key_resolver=resolve_programme,
        handler=must_not_run,
    )

    assert result.created is False and result.idempotent is True
    assert result.object_ids == {"programme_id": programme_id}
    assert handler_called is False
    assert _receipt(command_fixture, "domain-row-only").status == "succeeded"
    assert _counts(command_fixture) == (1, 1, 1)
    with Session(db.engine) as session:
        recovered_envelope = session.scalar(
            select(CommandMaterialisation).where(
                CommandMaterialisation.organization_id
                == command_fixture.organization_id,
                CommandMaterialisation.actor_id == command_fixture.user_id,
                CommandMaterialisation.operation == "programme.create",
                CommandMaterialisation.natural_key
                == "programme-intake:domain-row-only",
            )
        )
        assert recovered_envelope is not None
        assert recovered_envelope.object_ids == {"programme_id": programme_id}


def test_reclaimed_pre_envelope_effect_fails_closed_without_provable_resolver(
    command_fixture,
):
    """Legacy domain-only effects are never guessed or silently re-executed."""
    payload = {"name": command_fixture.domain_name}
    claim = CommandService.claim_or_reconcile(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="unprovable-domain-only",
        request_digest=canonical_request_digest(payload),
        natural_key="programme-intake:unprovable-domain-only",
        authorizer=_allow_command,
    )
    with Session(db.engine) as session, session.begin():
        session.add(
            StrategicInitiative(
                organization_id=command_fixture.organization_id,
                name=command_fixture.domain_name,
                record_kind="transformation_programme",
            )
        )
    _wait_for_expiry(claim)

    with pytest.raises(CommandConflict, match="pre_envelope_domain_effect_unprovable"):
        CommandService.execute(
            actor=command_fixture.actor,
            operation="programme.create",
            idempotency_key="unprovable-domain-only",
            payload=payload,
            natural_key="programme-intake:unprovable-domain-only",
            authorizer=_allow_command,
            natural_key_resolver=(
                CommandService.fail_closed_pre_envelope_recovery
            ),
            handler=lambda _session, _claim: pytest.fail(
                "an unprovable legacy effect reran domain work"
            ),
        )

    assert _counts(command_fixture) == (1, 0, 0)


def test_builtin_materialisation_recovers_exact_result_after_receipt_result_damage(
    command_fixture,
):
    """Damaged-result recovery preserves the immutable command provenance exactly."""
    first = _execute(command_fixture, key="builtin-materialisation")
    with Session(db.engine) as session:
        materialisation = session.scalar(
            select(CommandMaterialisation).where(
                CommandMaterialisation.organization_id
                == command_fixture.organization_id,
                CommandMaterialisation.actor_id == command_fixture.user_id,
                CommandMaterialisation.operation == "programme.create",
                CommandMaterialisation.natural_key
                == "programme-intake:builtin-materialisation",
            )
        )
        assert materialisation is not None
        assert materialisation.request_digest == canonical_request_digest(
            {"name": command_fixture.domain_name, "scope": {"b": 2, "a": 1}}
        )
        assert materialisation.object_ids == first.object_ids
        assert materialisation.response_json == first.response
        original_receipt_id = materialisation.receipt_id
        original_generation = materialisation.receipt_generation
        original_outbox = tuple(
            session.execute(
                select(
                    OperationOutboxEvent.event_id,
                    OperationOutboxEvent.ordinal,
                    OperationOutboxEvent.event_type,
                    OperationOutboxEvent.payload_json,
                )
                .where(
                    OperationOutboxEvent.organization_id
                    == command_fixture.organization_id,
                    OperationOutboxEvent.operation_result_id
                    == first.operation_result_id,
                )
                .order_by(OperationOutboxEvent.ordinal)
            ).all()
        )

    with db.engine.begin() as connection:
        # The delete is admitted by the deletion service's own transaction-local
        # marker, scoped to this organisation; no trigger is disabled.
        connection.execute(
            text("INSERT INTO organization_deletions_in_progress (target_organization_id) "
                 "VALUES (:organization_id) ON CONFLICT DO NOTHING"),
            {"organization_id": command_fixture.organization_id},
        )
        connection.execute(
            text("SELECT set_config('archie.deleting_organisation', :organization_id, true)"),
            {"organization_id": str(command_fixture.organization_id)},
        )
        connection.execute(
            text(
                "DELETE FROM transformation_outbox_events "
                "WHERE operation_result_id = :result_id"
            ),
            {"result_id": first.operation_result_id},
        )
        # Flagged exception: this receipt has already reached
        # 'succeeded' with a real operation_result_id, and
        # archie_guard_transformation_receipt correctly refuses to clear a
        # succeeded result's object link from any state — that refusal is the
        # guard doing its job, not a gap the deletion marker should paper over.
        # Simulating "receipt damage" after a real success needs a new, named
        # admission branch on a production concurrency-control guard — out of
        # scope here. Left for the lead/founder to decide whether that branch
        # should exist.
        connection.exec_driver_sql("SET LOCAL session_replication_role = replica")  # session-replication-role-ok: clears a succeeded receipt's result link, a transition the guard correctly never admits for any real caller
        connection.execute(
            text(
                "UPDATE command_idempotency_records SET status = 'retryable_failure', "
                "operation_result_id = NULL, last_error_class = 'envelope_lost', "
                "lease_expires_at = clock_timestamp() - interval '1 second' "
                "WHERE organization_id = :organization_id AND actor_id = :actor_id "
                "AND operation = 'programme.create' AND idempotency_key = :command_key"
            ),
            {
                "organization_id": command_fixture.organization_id,
                "actor_id": command_fixture.user_id,
                "command_key": "builtin-materialisation",
            },
        )
        connection.exec_driver_sql("SET LOCAL session_replication_role = DEFAULT")  # session-replication-role-ok: restores the switch flipped above
        connection.execute(
            text("DELETE FROM operation_results WHERE id = :result_id"),
            {"result_id": first.operation_result_id},
        )
        connection.execute(
            text("DELETE FROM organization_deletions_in_progress WHERE target_organization_id = :organization_id"),
            {"organization_id": command_fixture.organization_id},
        )

    replay = _execute(
        command_fixture,
        key="builtin-materialisation",
        handler=lambda _session, _claim: pytest.fail(
            "a damaged receipt reran a materialised domain mutation"
        ),
    )

    assert replay.created is False and replay.idempotent is True
    assert replay.object_ids == first.object_ids
    assert replay.response == first.response
    assert _counts(command_fixture) == (1, 1, 1)

    with Session(db.engine) as session:
        result = session.get(OperationResult, replay.operation_result_id)
        receipt = session.get(CommandIdempotencyRecord, original_receipt_id)
        repaired_outbox = tuple(
            session.execute(
                select(
                    OperationOutboxEvent.event_id,
                    OperationOutboxEvent.ordinal,
                    OperationOutboxEvent.event_type,
                    OperationOutboxEvent.payload_json,
                )
                .where(
                    OperationOutboxEvent.organization_id
                    == command_fixture.organization_id,
                    OperationOutboxEvent.operation_result_id
                    == replay.operation_result_id,
                )
                .order_by(OperationOutboxEvent.ordinal)
            ).all()
        )
    assert result.receipt_id == original_receipt_id
    assert result.receipt_generation == original_generation == 1
    assert repaired_outbox == original_outbox
    assert receipt.status == "succeeded"
    assert receipt.lease_generation == original_generation + 1
    assert receipt.operation_result_id == result.id

    second_replay = _execute(
        command_fixture,
        key="builtin-materialisation",
        handler=lambda _session, _claim: pytest.fail(
            "a second damaged-result replay reran the domain mutation"
        ),
    )

    assert second_replay.operation_result_id == replay.operation_result_id
    assert second_replay.object_ids == first.object_ids
    assert second_replay.response == first.response
    assert second_replay.created is False and second_replay.idempotent is True
    assert _counts(command_fixture) == (1, 1, 1)
    with Session(db.engine) as session:
        stable_outbox = tuple(
            session.execute(
                select(
                    OperationOutboxEvent.event_id,
                    OperationOutboxEvent.ordinal,
                    OperationOutboxEvent.event_type,
                    OperationOutboxEvent.payload_json,
                )
                .where(
                    OperationOutboxEvent.organization_id
                    == command_fixture.organization_id,
                    OperationOutboxEvent.operation_result_id
                    == second_replay.operation_result_id,
                )
                .order_by(OperationOutboxEvent.ordinal)
            ).all()
        )
    assert stable_outbox == original_outbox


def test_locked_generation_fence_precedes_first_real_domain_write(command_fixture):
    """The receipt generation is locked before the handler's first database mutation."""
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        lowered = " ".join(statement.lower().split())
        if "command_idempotency_records" in lowered or "strategic_initiatives" in lowered:
            statements.append(lowered)

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        _execute(command_fixture, key="fence-before-first-write")
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)

    first_domain_insert = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("insert into strategic_initiatives")
    )
    locked_fences = [
        index
        for index, statement in enumerate(statements)
        if "from command_idempotency_records" in statement
        and "for update" in statement
    ]
    assert locked_fences
    assert min(locked_fences) < first_domain_insert, "\n".join(statements)


def test_stale_generation_is_rejected_before_first_real_domain_write(
    command_fixture,
):
    """A reclaimed receipt must stop its stale handler before any domain INSERT."""
    domain_inserts = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("insert into strategic_initiatives"):
            domain_inserts.append(normalized)

    def reclaim_then_write(session, claim):
        with db.engine.begin() as connection:
            # Flagged exception: bumps lease_generation out
            # of band to simulate a concurrent reclaim mid-handler.
            # archie_guard_transformation_receipt's "reclaim" branch requires a
            # fresh, distinct claim_token and advancing attempt_count together
            # with the generation bump; a test has no legitimate way to mint
            # that claim_token. Needs a new, named admission branch on a
            # production concurrency-control guard — out of scope here. Left
            # for the lead/founder to decide whether that branch should exist.
            connection.exec_driver_sql("SET LOCAL session_replication_role = replica")  # session-replication-role-ok: bumps lease_generation out of band; the guard's reclaim branch needs a claim_token a test cannot legitimately mint
            connection.execute(
                text(
                    "UPDATE command_idempotency_records "
                    "SET lease_generation = lease_generation + 1 "
                    "WHERE id = :receipt_id AND organization_id = :organization_id"
                ),
                {
                    "receipt_id": claim.receipt_id,
                    "organization_id": command_fixture.organization_id,
                },
            )
        return _mutation(command_fixture)(session, claim)

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(StaleClaim, match="stale_or_expired_claim"):
            _execute(
                command_fixture,
                key="stale-before-first-write",
                handler=reclaim_then_write,
            )
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)

    assert domain_inserts == []
    assert _counts(command_fixture) == (0, 0, 0)


def test_result_replay_runs_authorizer_before_returning_persisted_result(
    command_fixture,
):
    """Catches immutable-result replay bypassing current authorization."""
    payload = {"name": command_fixture.domain_name}
    first = _execute(command_fixture, key="result-reauthorize", payload=payload)
    authorizer_calls = 0

    def deny_replay(_session, actor, operation, natural_key):
        nonlocal authorizer_calls
        authorizer_calls += 1
        assert actor == command_fixture.actor
        assert operation == "programme.create"
        assert natural_key == "programme-intake:result-reauthorize"
        raise NotAuthorised("programme_replay_not_authorised")

    with pytest.raises(NotAuthorised) as denied:
        CommandService.execute(
            actor=command_fixture.actor,
            operation="programme.create",
            idempotency_key="result-reauthorize",
            payload=payload,
            natural_key="programme-intake:result-reauthorize",
            authorizer=deny_replay,
            handler=lambda _session, _claim: pytest.fail(
                "result replay executed handler"
            ),
        )

    assert denied.value.reason == "programme_replay_not_authorised"
    assert denied.value.details.get("operation_result_id") is None
    assert authorizer_calls == 1
    assert first.response["name"] == command_fixture.domain_name
    assert _counts(command_fixture) == (1, 1, 1)


@pytest.mark.parametrize(
    "invalid_authorizer",
    (None, object()),
    ids=("none", "non-callable"),
)
def test_execute_rejects_invalid_authorizer_before_claim_or_domain_work(
    command_fixture,
    invalid_authorizer,
):
    """Catches the high-level command entry accepting an auth bypass value."""
    resolver_called = False
    handler_called = False

    def resolver(_session, _actor, _natural_key, _claim):
        nonlocal resolver_called
        resolver_called = True
        return None

    def handler(_session, _claim):
        nonlocal handler_called
        handler_called = True
        return DomainMutationResult({}, {}, ())

    with pytest.raises(TypeError, match="authorizer must be callable"):
        CommandService.execute(
            actor=command_fixture.actor,
            operation="programme.create",
            idempotency_key="invalid-execute-authorizer",
            payload={"name": command_fixture.domain_name},
            natural_key="programme-intake:invalid-execute-authorizer",
            authorizer=invalid_authorizer,
            natural_key_resolver=resolver,
            handler=handler,
        )

    assert resolver_called is False
    assert handler_called is False
    with Session(db.engine) as session:
        receipt_count = session.scalar(
            select(db.func.count())
            .select_from(CommandIdempotencyRecord)
            .where(
                CommandIdempotencyRecord.organization_id
                == command_fixture.organization_id,
                CommandIdempotencyRecord.idempotency_key
                == "invalid-execute-authorizer",
            )
        )
    assert receipt_count == 0
    assert _counts(command_fixture) == (0, 0, 0)


@pytest.mark.parametrize(
    "invalid_authorizer",
    (None, object()),
    ids=("none", "non-callable"),
)
def test_claim_or_reconcile_rejects_invalid_authorizer_before_receipt(
    command_fixture,
    invalid_authorizer,
):
    """Catches direct claim/reconciliation accepting an auth bypass value."""
    payload = {"name": command_fixture.domain_name}
    with pytest.raises(TypeError, match="authorizer must be callable"):
        CommandService.claim_or_reconcile(
            actor=command_fixture.actor,
            operation="programme.create",
            idempotency_key="invalid-claim-authorizer",
            request_digest=canonical_request_digest(payload),
            natural_key="programme-intake:invalid-claim-authorizer",
            authorizer=invalid_authorizer,
        )

    with Session(db.engine) as session:
        receipt_count = session.scalar(
            select(db.func.count())
            .select_from(CommandIdempotencyRecord)
            .where(
                CommandIdempotencyRecord.organization_id
                == command_fixture.organization_id,
                CommandIdempotencyRecord.idempotency_key
                == "invalid-claim-authorizer",
            )
        )
    assert receipt_count == 0
    assert _counts(command_fixture) == (0, 0, 0)


@pytest.mark.parametrize(
    "invalid_authorizer",
    (None, object()),
    ids=("none", "non-callable"),
)
def test_execute_claim_rejects_invalid_authorizer_before_resolver_or_handler(
    command_fixture,
    invalid_authorizer,
):
    """Catches the low-level execution entry treating None as already authorized."""
    payload = {"name": command_fixture.domain_name}
    claim = CommandService.claim_or_reconcile(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="invalid-execute-claim-authorizer",
        request_digest=canonical_request_digest(payload),
        natural_key="programme-intake:invalid-execute-claim-authorizer",
        authorizer=_allow_command,
    )
    resolver_called = False
    handler_called = False

    def resolver(_session, _actor, _natural_key, _claim):
        nonlocal resolver_called
        resolver_called = True
        return DomainMutationResult({}, {}, ())

    def handler(_session, _claim):
        nonlocal handler_called
        handler_called = True
        return DomainMutationResult({}, {}, ())

    with pytest.raises(TypeError, match="authorizer must be callable"):
        CommandService.execute_claim(
            actor=command_fixture.actor,
            operation="programme.create",
            claim=claim,
            authorizer=invalid_authorizer,
            natural_key_resolver=resolver,
            handler=handler,
        )

    assert resolver_called is False
    assert handler_called is False
    assert _counts(command_fixture) == (0, 0, 0)


def test_cross_actor_domain_row_recovery_is_authorized_before_resolver(
    command_fixture,
):
    """Catches a domain-only recovery adapter exposing another actor's row."""
    with Session(db.engine) as session, session.begin():
        programme = StrategicInitiative(
            organization_id=command_fixture.organization_id,
            name=command_fixture.domain_name,
            record_kind="transformation_programme",
        )
        session.add(programme)
        session.flush()
        programme_id = programme.id

    suffix = uuid.uuid4().hex[:12]
    second_user = User(
        email=f"domain-recovery-second-{suffix}@example.test",
        organization_id=command_fixture.organization_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db.session.add(second_user)
    db.session.commit()
    second_actor = ActorContext(
        user_id=second_user.id,
        organization_id=command_fixture.organization_id,
        roles=command_fixture.actor.roles,
        request_id=f"domain-recovery-request-{suffix}",
    )
    resolver_called = False
    handler_called = False

    def authorize_owner(_session, actor, operation, natural_key):
        assert operation == "programme.create"
        assert natural_key == "programme-intake:cross-actor-domain-row"
        if actor.user_id != command_fixture.user_id:
            raise NotAuthorised("programme_recovery_not_authorised")

    def resolver(_session, _actor, _natural_key, _claim):
        nonlocal resolver_called
        resolver_called = True
        return DomainMutationResult(
            object_ids={"programme_id": programme_id},
            response={"programme_id": programme_id},
            outbox_events=(),
        )

    def handler(_session, _claim):
        nonlocal handler_called
        handler_called = True
        raise AssertionError("unauthorized domain recovery executed handler")

    with pytest.raises(NotAuthorised) as denied:
        CommandService.execute(
            actor=second_actor,
            operation="programme.create",
            idempotency_key="cross-actor-domain-row",
            payload={"name": command_fixture.domain_name},
            natural_key="programme-intake:cross-actor-domain-row",
            authorizer=authorize_owner,
            natural_key_resolver=resolver,
            handler=handler,
        )

    assert denied.value.reason == "programme_recovery_not_authorised"
    assert denied.value.details.get("programme_id") is None
    assert resolver_called is False
    assert handler_called is False
    with Session(db.engine) as session:
        second_receipts = session.scalar(
            select(db.func.count())
            .select_from(CommandIdempotencyRecord)
            .where(
                CommandIdempotencyRecord.organization_id
                == command_fixture.organization_id,
                CommandIdempotencyRecord.actor_id == second_actor.user_id,
            )
        )
    assert second_receipts == 0
    assert _counts(command_fixture) == (1, 0, 0)


def test_cross_actor_same_natural_key_cannot_replay_persisted_result(
    command_fixture,
):
    """Catches actor B receiving actor A's immutable result during repair."""
    payload = {"name": command_fixture.domain_name}
    first = _execute(command_fixture, key="cross-actor", payload=payload)
    suffix = uuid.uuid4().hex[:12]
    second_user = User(
        email=f"command-second-{suffix}@example.test",
        organization_id=command_fixture.organization_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db.session.add(second_user)
    db.session.commit()
    second_actor = ActorContext(
        user_id=second_user.id,
        organization_id=command_fixture.organization_id,
        roles=command_fixture.actor.roles,
        request_id=f"second-request-{suffix}",
    )
    second_claim = CommandService.claim_or_reconcile(
        actor=second_actor,
        operation="programme.create",
        idempotency_key="cross-actor",
        request_digest=canonical_request_digest(payload),
        natural_key="programme-intake:cross-actor",
        authorizer=_allow_command,
    )
    _wait_for_expiry(second_claim)
    called = False

    def must_not_run(_session, _claim):
        nonlocal called
        called = True
        raise AssertionError("cross-actor natural-key collision ran the handler")

    with pytest.raises(CommandConflict) as denied:
        CommandService.execute(
            actor=second_actor,
            operation="programme.create",
            idempotency_key="cross-actor",
            payload=payload,
            natural_key="programme-intake:cross-actor",
            authorizer=_allow_command,
            handler=must_not_run,
        )

    assert denied.value.reason == "natural_key_owned_by_another_actor"
    assert called is False
    assert denied.value.details.get("operation_result_id") is None
    assert first.response["name"] == command_fixture.domain_name
    assert _counts(command_fixture) == (1, 1, 1)


def test_damaged_receipt_reconciles_natural_key_and_exact_response(command_fixture):
    """Catches receipt damage causing a natural-key domain row to be duplicated."""
    first = _execute(command_fixture, key="damaged")
    receipt = _receipt(command_fixture, "damaged")
    with db.engine.begin() as connection:
        # Flagged exception: resets a terminal
        # ('succeeded') receipt back to 'in_progress' with an expired lease,
        # simulating external damage. No branch of
        # archie_guard_transformation_receipt admits a terminal-to-in_progress
        # transition (by design: a succeeded result is immutable). Needs a
        # new, named admission branch on a production concurrency-control
        # guard — out of scope here. Left for the lead/founder to decide
        # whether that branch should exist.
        connection.exec_driver_sql("SET LOCAL session_replication_role = replica")  # session-replication-role-ok: resets a succeeded receipt to in_progress; the guard correctly never admits a terminal-to-in_progress transition
        connection.execute(
            text(
                """
                UPDATE command_idempotency_records
                SET status = 'in_progress', operation_result_id = NULL,
                    completed_at = NULL,
                    lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE id = :receipt_id AND organization_id = :organization_id
                """
            ),
            {
                "receipt_id": receipt.id,
                "organization_id": command_fixture.organization_id,
            },
        )

    def must_not_run(_session, _claim):
        raise AssertionError("damaged receipt reran the natural-key mutation")

    replay = _execute(command_fixture, key="damaged", handler=must_not_run)

    assert replay.operation_result_id == first.operation_result_id
    assert replay.object_ids == first.object_ids
    assert replay.response == first.response
    assert replay.created is False and replay.idempotent is True
    assert _counts(command_fixture) == (1, 1, 1)


def test_same_key_changed_digest_conflicts_and_active_lease_has_retry_metadata(
    command_fixture, app,
):
    """Catches key rebinding and blind retries while another worker owns a lease."""
    # This assertion exercises an *active* lease, not expiry/reclaim.  The
    # fixture's intentionally tiny 120 ms lease supports the separate expiry
    # tests but can elapse between two PostgreSQL transactions under CI
    # coverage instrumentation, turning this into a timing lottery.
    app.config["TRANSFORMATION_COMMAND_LEASE_SECONDS"] = 1.0
    payload = {"name": command_fixture.domain_name}
    digest = canonical_request_digest(payload)
    claim = CommandService.claim_or_reconcile(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="active",
        request_digest=digest,
        natural_key="programme-intake:active",
        authorizer=_allow_command,
    )

    with pytest.raises(CommandConflict) as active:
        CommandService.claim_or_reconcile(
            actor=command_fixture.actor,
            operation="programme.create",
            idempotency_key="active",
            request_digest=digest,
            natural_key="programme-intake:active",
            authorizer=_allow_command,
        )
    assert active.value.reason == "active_lease"
    assert active.value.retry_after_seconds > 0
    assert active.value.receipt_id == claim.receipt_id
    assert active.value.generation == 1

    with pytest.raises(CommandConflict) as changed:
        CommandService.claim_or_reconcile(
            actor=command_fixture.actor,
            operation="programme.create",
            idempotency_key="active",
            request_digest=canonical_request_digest({"name": "different"}),
            natural_key="programme-intake:active",
            authorizer=_allow_command,
        )
    assert changed.value.http_status == 409
    assert changed.value.reason == "idempotency_digest_mismatch"


def test_paused_stale_worker_cannot_write_after_reclaim(command_fixture, app):
    """Catches generation-one work committing after generation two succeeds."""
    payload = {"name": command_fixture.domain_name}
    digest = canonical_request_digest(payload)
    worker_a = CommandService.claim_or_reconcile(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="race",
        request_digest=digest,
        natural_key="programme-intake:race",
        authorizer=_allow_command,
    )
    paused = threading.Barrier(2)
    resume_worker_a = threading.Event()
    outcome = {}

    def stale_worker():
        with app.app_context():
            paused.wait(timeout=3)
            resume_worker_a.wait(timeout=3)
            try:
                CommandService.execute_claim(
                    actor=command_fixture.actor,
                    operation="programme.create",
                    claim=worker_a,
                    authorizer=_allow_command,
                    handler=_mutation(command_fixture),
                )
            except Exception as error:  # captured for assertion in the test thread
                outcome["error"] = error
            finally:
                db.session.remove()

    thread = threading.Thread(target=stale_worker, daemon=True)
    thread.start()
    paused.wait(timeout=3)
    _wait_for_expiry(worker_a)

    worker_b = _execute(command_fixture, key="race", payload=payload)
    resume_worker_a.set()
    thread.join(timeout=3)

    assert thread.is_alive() is False
    assert worker_b.created is True
    assert isinstance(outcome.get("error"), StaleClaim)
    assert _receipt(command_fixture, "race").lease_generation == 2
    assert _counts(command_fixture) == (1, 1, 1)


def test_heartbeat_completes_while_handler_is_paused(command_fixture, app):
    """Catches execution holding the receipt row lock for the handler duration."""
    app.config["TRANSFORMATION_COMMAND_LEASE_SECONDS"] = 1.0
    payload = {"name": command_fixture.domain_name}
    claim = CommandService.claim_or_reconcile(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="paused-heartbeat",
        request_digest=canonical_request_digest(payload),
        natural_key="programme-intake:paused-heartbeat",
        authorizer=_allow_command,
    )
    handler_entered = threading.Event()
    release_handler = threading.Event()
    heartbeat_done = threading.Event()
    execution_outcome = {}
    heartbeat_outcome = {}

    def paused_handler(session, active_claim):
        handler_entered.set()
        assert release_handler.wait(timeout=3)
        return _mutation(command_fixture)(session, active_claim)

    def execute_worker():
        with app.app_context():
            try:
                execution_outcome["result"] = CommandService.execute_claim(
                    actor=command_fixture.actor,
                    operation="programme.create",
                    claim=claim,
                    authorizer=_allow_command,
                    handler=paused_handler,
                )
            except Exception as error:
                execution_outcome["error"] = error
            finally:
                db.session.remove()

    def heartbeat_worker():
        with app.app_context():
            try:
                heartbeat_outcome["claim"] = CommandService.heartbeat(
                    actor=command_fixture.actor, claim=claim
                )
            except Exception as error:
                heartbeat_outcome["error"] = error
            finally:
                heartbeat_done.set()
                db.session.remove()

    execution_thread = threading.Thread(target=execute_worker, daemon=True)
    execution_thread.start()
    assert handler_entered.wait(timeout=3)
    heartbeat_thread = threading.Thread(target=heartbeat_worker, daemon=True)
    heartbeat_thread.start()
    completed_while_paused = heartbeat_done.wait(timeout=0.5)
    release_handler.set()
    execution_thread.join(timeout=3)
    heartbeat_thread.join(timeout=3)

    assert completed_while_paused is True
    assert heartbeat_outcome.get("error") is None
    assert heartbeat_outcome["claim"].generation == claim.generation
    assert execution_outcome.get("error") is None
    assert execution_outcome["result"].created is True
    assert _counts(command_fixture) == (1, 1, 1)


def test_reclaim_completes_while_expired_handler_is_paused(command_fixture, app):
    """Catches an expired handler lock preventing the next generation from claiming."""
    payload = {"name": command_fixture.domain_name}
    claim = CommandService.claim_or_reconcile(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="paused-reclaim",
        request_digest=canonical_request_digest(payload),
        natural_key="programme-intake:paused-reclaim",
        authorizer=_allow_command,
    )
    handler_entered = threading.Event()
    release_handler = threading.Event()
    reclaim_done = threading.Event()
    execution_outcome = {}
    reclaim_outcome = {}

    def paused_handler(session, active_claim):
        handler_entered.set()
        assert release_handler.wait(timeout=3)
        return _mutation(command_fixture)(session, active_claim)

    def execute_worker():
        with app.app_context():
            try:
                CommandService.execute_claim(
                    actor=command_fixture.actor,
                    operation="programme.create",
                    claim=claim,
                    authorizer=_allow_command,
                    handler=paused_handler,
                )
            except Exception as error:
                execution_outcome["error"] = error
            finally:
                db.session.remove()

    def reclaim_worker():
        with app.app_context():
            try:
                reclaim_outcome["claim"] = CommandService.claim_or_reconcile(
                    actor=command_fixture.actor,
                    operation="programme.create",
                    idempotency_key="paused-reclaim",
                    request_digest=canonical_request_digest(payload),
                    natural_key="programme-intake:paused-reclaim",
                    authorizer=_allow_command,
                )
            except Exception as error:
                reclaim_outcome["error"] = error
            finally:
                reclaim_done.set()
                db.session.remove()

    execution_thread = threading.Thread(target=execute_worker, daemon=True)
    execution_thread.start()
    assert handler_entered.wait(timeout=3)
    _wait_for_expiry(claim)
    reclaim_thread = threading.Thread(target=reclaim_worker, daemon=True)
    reclaim_thread.start()
    completed_while_paused = reclaim_done.wait(timeout=0.5)
    release_handler.set()
    execution_thread.join(timeout=3)
    reclaim_thread.join(timeout=3)

    assert completed_while_paused is True
    assert reclaim_outcome.get("error") is None
    assert reclaim_outcome["claim"].generation == claim.generation + 1
    assert isinstance(execution_outcome.get("error"), StaleClaim)
    assert _counts(command_fixture) == (0, 0, 0)


def test_supplied_receipt_id_is_still_tenant_and_actor_scoped(command_fixture):
    """Catches a warm/supplied receipt ID bypassing explicit tenant ownership."""
    payload = {"name": command_fixture.domain_name}
    claim = CommandService.claim_or_reconcile(
        actor=command_fixture.actor,
        operation="programme.create",
        idempotency_key="foreign-actor",
        request_digest=canonical_request_digest(payload),
        natural_key="programme-intake:foreign-actor",
        authorizer=_allow_command,
    )
    forged = ActorContext(
        user_id=command_fixture.user_id,
        organization_id=command_fixture.organization_id + 1,
        roles=command_fixture.actor.roles,
        request_id="forged-request",
    )

    with pytest.raises(StaleClaim):
        CommandService.execute_claim(
            actor=forged,
            operation="programme.create",
            claim=claim,
            authorizer=_allow_command,
            handler=_mutation(command_fixture),
        )
    assert _counts(command_fixture) == (0, 0, 0)
