"""Direct-driver proof for transformation command database guards."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import db
from app.models.organization import Organization
from app.models.transformation_db_guards import ensure_transformation_db_guards
from app.models.transformation_execution import (
    CommandIdempotencyRecord,
    OperationOutboxEvent,
    OperationResult,
)
from app.models.transformation_decision import (
    DecisionBriefEvidenceCitation,
    DecisionBriefOptionCitation,
    DecisionBriefVersion,
    DecisionEvent,
    TransformationOption,
    TransformationOptionVersion,
)
from app.models.transformation_evidence import (
    EvidenceClaimHead,
    EvidenceHeadEvent,
    EvidenceRecord,
)
from app.models.user import User
from app.modules.transformation_room.command_service import (
    CommandService,
    NATURAL_KEY_CONTENDER_REASON,
)
from app.modules.transformation_room.domain import ActorContext, DomainMutationResult, StaleClaim
from app.modules.transformation_room.evidence_service import TransformationEvidenceService

from tests.test_transformation_evidence_service import (
    EvidenceScope,
    _record_inventory,
    evidence_scope,
)
from tests.test_transformation_option_service import DecisionScope, decision_scope


@dataclass(frozen=True)
class GuardFixture:
    actor: ActorContext
    organization_id: int
    receipt_id: int
    result_id: int
    outbox_id: int
    idempotency_key: str
    payload: dict
    natural_key: str


def _allow_command(_session, _actor, _operation, _natural_key):
    return None


@pytest.fixture
def guard_fixture(app, _schema):
    suffix = uuid.uuid4().hex[:12]
    with app.app_context():
        with db.engine.begin() as connection:
            ensure_transformation_db_guards(connection)
            ensure_transformation_db_guards(connection)

        db.session.remove()
        organization = Organization(name=f"Guard Org {suffix}", slug=f"guard-org-{suffix}")
        db.session.add(organization)
        db.session.flush()
        user = User(
            email=f"guard-{suffix}@example.test",
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

        actor = ActorContext(
            user_id=user_id,
            organization_id=organization_id,
            roles=frozenset({"enterprise_architect"}),
            request_id=f"guard-request-{suffix}",
        )
        key = f"guard-{suffix}"
        payload = {"name": f"guard object {suffix}"}
        natural_key = f"guard:{suffix}"

        def mutation(_session, _claim):
            return DomainMutationResult(
                object_ids={"guard_id": organization_id},
                response={"guard_id": organization_id},
                outbox_events=(
                    {
                        "event_type": "guard.created",
                        "payload": {"guard_id": organization_id},
                    },
                ),
            )

        result = CommandService.execute(
            actor=actor,
            operation="guard.create",
            idempotency_key=key,
            payload=payload,
            natural_key=natural_key,
            authorizer=_allow_command,
            handler=mutation,
        )
        with Session(db.engine) as session:
            receipt = session.execute(
                select(CommandIdempotencyRecord).where(
                    CommandIdempotencyRecord.organization_id == organization_id,
                    CommandIdempotencyRecord.actor_id == user_id,
                    CommandIdempotencyRecord.operation == "guard.create",
                    CommandIdempotencyRecord.idempotency_key == key,
                )
            ).scalar_one()
            outbox = session.execute(
                select(OperationOutboxEvent).where(
                    OperationOutboxEvent.organization_id == organization_id,
                    OperationOutboxEvent.operation_result_id == result.operation_result_id,
                )
            ).scalar_one()
            fixture = GuardFixture(
                actor=actor,
                organization_id=organization_id,
                receipt_id=receipt.id,
                result_id=result.operation_result_id,
                outbox_id=outbox.id,
                idempotency_key=key,
                payload=payload,
                natural_key=natural_key,
            )
        try:
            yield fixture
        finally:
            db.session.remove()
            from app.services.org_deletion_service import OrganizationDeletionService

            if db.session.get(Organization, organization_id) is not None:
                OrganizationDeletionService.delete_organization(organization_id)
            db.session.remove()


def _direct_driver_execute(statement, parameters):
    connection = db.engine.raw_connection()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(statement, parameters)
            connection.commit()
        finally:
            cursor.close()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def test_guard_installation_is_idempotent_and_functions_fix_search_path(guard_fixture):
    """Catches duplicate triggers or SECURITY DEFINER search-path hijacking."""
    with db.engine.connect() as connection:
        triggers = connection.execute(
            text(
                """
                SELECT tg.tgname, cls.relname
                FROM pg_trigger tg
                JOIN pg_class cls ON cls.oid = tg.tgrelid
                JOIN pg_namespace ns ON ns.oid = cls.relnamespace
                WHERE NOT tg.tgisinternal
                  AND ns.nspname = current_schema()
                  AND tg.tgname IN (
                    'trg_candidate_overlap_disposition_immutable',
                    'trg_command_materialisation_immutable',
                    'trg_transformation_result_immutable',
                    'trg_transformation_outbox_immutable',
                    'trg_transformation_receipt_guard',
                    'trg_evidence_record_immutable',
                    'trg_evidence_event_immutable',
                    'trg_evidence_event_binding',
                    'trg_evidence_head_guard',
                    'trg_transformation_option_version_immutable',
                    'trg_decision_brief_version_immutable',
                    'trg_decision_brief_option_citation_immutable',
                    'trg_decision_brief_option_citation_membership',
                    'trg_decision_brief_evidence_citation_immutable',
                    'trg_decision_brief_evidence_citation_membership',
                    'trg_decision_event_immutable'
                  )
                ORDER BY tg.tgname
                """
            )
        ).all()
        functions = connection.execute(
            text(
                """
                SELECT proc.proname, proc.prosecdef, proc.proconfig
                FROM pg_proc proc
                JOIN pg_namespace ns ON ns.oid = proc.pronamespace
                WHERE ns.nspname = current_schema()
                  AND proc.proname IN (
                    'archie_reject_transformation_mutation',
                    'archie_guard_transformation_receipt',
                    'archie_guard_evidence_head',
                    'archie_guard_evidence_event_binding',
                    'archie_hmac_sha256',
                    'archie_verify_command_capability',
                    'archie_claim_transformation_command',
                    'archie_create_decision_brief',
                    'archie_freeze_decision_brief_version',
                    'archie_advance_evidence_head'
                )
                ORDER BY proc.proname
                """
            )
        ).all()

    assert triggers == [
        ("trg_candidate_overlap_disposition_immutable", "candidate_overlap_dispositions"),
        ("trg_command_materialisation_immutable", "command_materialisations"),
        ("trg_decision_brief_evidence_citation_immutable", "decision_brief_evidence_citations"),
        ("trg_decision_brief_evidence_citation_membership", "decision_brief_evidence_citations"),
        ("trg_decision_brief_option_citation_immutable", "decision_brief_option_citations"),
        ("trg_decision_brief_option_citation_membership", "decision_brief_option_citations"),
        ("trg_decision_brief_version_immutable", "decision_brief_versions"),
        ("trg_decision_event_immutable", "decision_events"),
        ("trg_evidence_event_binding", "evidence_head_events"),
        ("trg_evidence_event_immutable", "evidence_head_events"),
        ("trg_evidence_head_guard", "evidence_claim_heads"),
        ("trg_evidence_record_immutable", "evidence_records"),
        ("trg_transformation_option_version_immutable", "transformation_option_versions"),
        ("trg_transformation_outbox_immutable", "transformation_outbox_events"),
        ("trg_transformation_receipt_guard", "command_idempotency_records"),
        ("trg_transformation_result_immutable", "operation_results"),
    ]
    assert len(functions) == 10
    assert all(row.prosecdef is True for row in functions)
    assert all("search_path=pg_catalog, public" in row.proconfig for row in functions)


@pytest.mark.parametrize(
    ("statement", "id_field"),
    (
        (
            "/* comment-prefixed */ UPDATE public.operation_results "
            "SET response_json = '{}'::json WHERE id = %s AND organization_id = %s",
            "result_id",
        ),
        (
            "WITH target AS (SELECT id FROM public.operation_results "
            "WHERE id = %s AND organization_id = %s) "
            "DELETE FROM public.operation_results r USING target "
            "WHERE r.id = target.id",
            "result_id",
        ),
        (
            "UPDATE public.transformation_outbox_events "
            "SET payload_json = '{}'::json WHERE id = %s AND organization_id = %s",
            "outbox_id",
        ),
        (
            "WITH target AS (SELECT id FROM public.transformation_outbox_events "
            "WHERE id = %s AND organization_id = %s) "
            "DELETE FROM public.transformation_outbox_events e USING target "
            "WHERE e.id = target.id",
            "outbox_id",
        ),
    ),
)
def test_direct_driver_cannot_update_or_delete_results_and_outbox(
    guard_fixture, statement, id_field
):
    """Catches lexical SQL variants bypassing append-only enforcement."""
    with pytest.raises(Exception, match="append-only"):
        _direct_driver_execute(
            statement,
            (getattr(guard_fixture, id_field), guard_fixture.organization_id),
        )


@pytest.mark.parametrize(
    ("assignment", "value"),
    (
        ("actor_id = %s", 999999999),
        ("operation = %s", "changed.operation"),
        ("idempotency_key = %s", "changed-key"),
        ("request_digest = %s", "0" * 64),
        ("natural_key = %s", "changed-natural-key"),
        ("operation_result_id = %s", None),
    ),
)
def test_receipt_identity_digest_natural_key_and_terminal_result_are_immutable(
    guard_fixture, assignment, value
):
    """Catches a direct client rebinding or erasing a completed command."""
    statement = (
        f"/* direct mutation */ UPDATE public.command_idempotency_records SET {assignment} "
        "WHERE id = %s AND organization_id = %s"
    )
    with pytest.raises(Exception, match="immutable"):
        _direct_driver_execute(
            statement,
            (value, guard_fixture.receipt_id, guard_fixture.organization_id),
        )


def test_direct_driver_cannot_delete_receipt(guard_fixture):
    """Catches command identity and its terminal result pointer being erased."""
    with pytest.raises(Exception, match="immutable"):
        _direct_driver_execute(
            "DELETE FROM public.command_idempotency_records "
            "WHERE id = %s AND organization_id = %s",
            (guard_fixture.receipt_id, guard_fixture.organization_id),
        )


def test_outbox_delivery_metadata_advances_without_mutating_event(guard_fixture):
    """Catches append-only protection making at-least-once delivery impossible."""
    _direct_driver_execute(
        "UPDATE public.transformation_outbox_events "
        "SET delivery_attempts = delivery_attempts + 1, "
        "published_at = clock_timestamp() "
        "WHERE id = %s AND organization_id = %s",
        (guard_fixture.outbox_id, guard_fixture.organization_id),
    )

    with Session(db.engine) as session:
        event = session.execute(
            select(OperationOutboxEvent).where(
                OperationOutboxEvent.id == guard_fixture.outbox_id,
                OperationOutboxEvent.organization_id == guard_fixture.organization_id,
            )
        ).scalar_one()
    assert event.delivery_attempts == 1
    assert event.published_at is not None
    assert event.event_type == "guard.created"
    assert event.payload_json == {"guard_id": guard_fixture.organization_id}


def test_fencing_trigger_rejects_invalid_generation_and_old_worker(guard_fixture, app):
    """Catches token reuse, generation skipping, and post-reclaim heartbeat."""
    key = f"lease-{uuid.uuid4().hex[:8]}"
    payload = {"name": key}
    claim = CommandService.claim_or_reconcile(
        actor=guard_fixture.actor,
        operation="guard.lease",
        idempotency_key=key,
        request_digest=CommandService.request_digest(payload),
        natural_key=f"guard-lease:{key}",
        authorizer=_allow_command,
    )
    extended = CommandService.heartbeat(actor=guard_fixture.actor, claim=claim)
    assert extended.claim_token == claim.claim_token
    assert extended.generation == claim.generation

    with pytest.raises(Exception, match="invalid command receipt transition"):
        _direct_driver_execute(
            "UPDATE public.command_idempotency_records "
            "SET lease_generation = lease_generation + 2, claim_token = %s, "
            "lease_expires_at = clock_timestamp() + interval '1 minute' "
            "WHERE id = %s AND organization_id = %s",
            (str(uuid.uuid4()), claim.receipt_id, guard_fixture.organization_id),
        )

    with db.engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL session_replication_role = replica")  # session-replication-role-ok: backdates a lease to simulate real expiry without a real wait; the guard correctly refuses an UPDATE to this append-only row from any real caller
        connection.execute(
            text(
                "UPDATE command_idempotency_records "
                "SET lease_expires_at = clock_timestamp() - interval '1 second' "
                "WHERE id = :receipt_id AND organization_id = :organization_id"
            ),
            {
                "receipt_id": claim.receipt_id,
                "organization_id": guard_fixture.organization_id,
            },
        )

    reclaimed = CommandService.claim_or_reconcile(
        actor=guard_fixture.actor,
        operation="guard.lease",
        idempotency_key=key,
        request_digest=CommandService.request_digest(payload),
        natural_key=f"guard-lease:{key}",
        authorizer=_allow_command,
    )
    assert reclaimed.generation == claim.generation + 1
    assert reclaimed.claim_token != claim.claim_token
    with pytest.raises(StaleClaim):
        CommandService.heartbeat(actor=guard_fixture.actor, claim=claim)


def test_receipt_guard_rejects_result_owned_by_another_actor(guard_fixture):
    """Catches direct receipt repair binding actor B to actor A's result."""
    suffix = uuid.uuid4().hex[:12]
    second_user = User(
        email=f"guard-second-{suffix}@example.test",
        organization_id=guard_fixture.organization_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db.session.add(second_user)
    db.session.commit()
    second_actor = ActorContext(
        user_id=second_user.id,
        organization_id=guard_fixture.organization_id,
        roles=guard_fixture.actor.roles,
        request_id=f"guard-second-request-{suffix}",
    )
    claim = CommandService.claim_or_reconcile(
        actor=second_actor,
        operation="guard.create",
        idempotency_key=f"guard-second-{suffix}",
        request_digest=CommandService.request_digest(guard_fixture.payload),
        natural_key=guard_fixture.natural_key,
        authorizer=_allow_command,
    )

    with pytest.raises(Exception, match="invalid command receipt transition"):
        _direct_driver_execute(
            "UPDATE public.command_idempotency_records "
            "SET status = 'succeeded', operation_result_id = %s, "
            "lease_expires_at = NULL, completed_at = clock_timestamp() "
            "WHERE id = %s AND organization_id = %s AND actor_id = %s",
            (
                guard_fixture.result_id,
                claim.receipt_id,
                guard_fixture.organization_id,
                second_actor.user_id,
            ),
        )


def test_receipt_guard_rejects_unsigned_failed_contender_reconciliation(
    guard_fixture,
):
    """Catches direct SQL imitating the signed failed-contender repair."""
    suffix = uuid.uuid4().hex[:12]
    payload = {"name": f"failed contender {suffix}"}
    natural_key = f"guard-failed-contender:{suffix}"
    request_digest = CommandService.request_digest(payload)
    failed_claim = CommandService.claim_or_reconcile(
        actor=guard_fixture.actor,
        operation="guard.reconcile",
        idempotency_key=f"failed-{suffix}",
        request_digest=request_digest,
        natural_key=natural_key,
        authorizer=_allow_command,
    )
    assert CommandService.mark_non_retryable(
        actor=guard_fixture.actor,
        claim=failed_claim,
        error_class="_NaturalKeyContenderConflict",
        terminal_reason=NATURAL_KEY_CONTENDER_REASON,
    ) is True

    winner = CommandService.execute(
        actor=guard_fixture.actor,
        operation="guard.reconcile",
        idempotency_key=f"winner-{suffix}",
        payload=payload,
        natural_key=natural_key,
        authorizer=_allow_command,
        handler=lambda _session, _claim: DomainMutationResult(
            object_ids={"guard_id": guard_fixture.organization_id},
            response={"guard_id": guard_fixture.organization_id},
            outbox_events=(),
        ),
    )

    with pytest.raises(Exception, match="invalid command receipt transition"):
        _direct_driver_execute(
            "WITH forged_context AS ("
            "SELECT set_config('archie.receipt_reconcile_key_id', 'forged', true), "
            "set_config('archie.receipt_reconcile_fence', %s, true)) "
            "UPDATE public.command_idempotency_records "
            "SET status = 'succeeded', lease_generation = lease_generation + 1, "
            "claim_token = %s, operation_result_id = %s, "
            "attempt_count = attempt_count + 1, last_error_class = NULL, "
            "lease_expires_at = NULL, completed_at = clock_timestamp() "
            "FROM forged_context "
            "WHERE id = %s AND organization_id = %s AND actor_id = %s",
            (
                "0" * 64,
                "f" * 64,
                winner.operation_result_id,
                failed_claim.receipt_id,
                guard_fixture.organization_id,
                guard_fixture.actor.user_id,
            ),
        )

    with Session(db.engine) as session:
        failed_receipt = session.get(
            CommandIdempotencyRecord, failed_claim.receipt_id
        )
    assert failed_receipt.status == "failed_non_retryable"
    assert failed_receipt.operation_result_id is None
    assert failed_receipt.lease_generation == failed_claim.generation


def test_receipt_guard_rejects_unsigned_contender_failure_marker(guard_fixture):
    """Direct SQL cannot label an unrelated terminal failure as a contender."""
    suffix = uuid.uuid4().hex[:12]
    claim = CommandService.claim_or_reconcile(
        actor=guard_fixture.actor,
        operation="guard.reconcile",
        idempotency_key=f"forged-marker-{suffix}",
        request_digest=CommandService.request_digest({"marker": suffix}),
        natural_key=f"guard-forged-marker:{suffix}",
        authorizer=_allow_command,
    )

    with pytest.raises(Exception, match="invalid command receipt transition"):
        _direct_driver_execute(
            "UPDATE public.command_idempotency_records "
            "SET status = 'failed_non_retryable', "
            "last_error_class = '_NaturalKeyContenderConflict', "
            "terminal_reason = %s, lease_expires_at = NULL, "
            "completed_at = clock_timestamp() "
            "WHERE id = %s AND organization_id = %s AND actor_id = %s",
            (
                NATURAL_KEY_CONTENDER_REASON,
                claim.receipt_id,
                guard_fixture.organization_id,
                guard_fixture.actor.user_id,
            ),
        )

    with Session(db.engine) as session:
        receipt = session.get(CommandIdempotencyRecord, claim.receipt_id)
    assert receipt.status == "in_progress"
    assert receipt.terminal_reason is None


def test_reconciliation_guard_can_be_reinstalled_without_changing_success(
    guard_fixture,
):
    """Catches guard replacement corrupting an existing canonical replay."""
    with db.engine.begin() as connection:
        ensure_transformation_db_guards(connection)

    replay = CommandService.execute(
        actor=guard_fixture.actor,
        operation="guard.create",
        idempotency_key=guard_fixture.idempotency_key,
        payload=guard_fixture.payload,
        natural_key=guard_fixture.natural_key,
        authorizer=_allow_command,
        handler=lambda _session, _claim: pytest.fail("replay executed handler"),
    )

    assert replay.created is False and replay.idempotent is True
    assert replay.operation_result_id == guard_fixture.result_id


def test_schema_reconciliation_restores_missing_guard_idempotently(guard_fixture):
    """Catches long-lived databases missing Task 3 guards after deployment."""
    from app.commands.reconcile_schema import _reconcile

    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_transformation_result_immutable ON operation_results"
        )

    _added, first_failed, _missing, _blocking = _reconcile(dry_run=False)
    assert first_failed == []
    _added, second_failed, _missing, _blocking = _reconcile(dry_run=False)
    assert second_failed == []

    with db.engine.connect() as connection:
        trigger_count = connection.scalar(
            text(
                """
                SELECT count(*) FROM pg_trigger
                WHERE tgname = 'trg_transformation_result_immutable'
                  AND tgrelid = 'operation_results'::regclass
                  AND NOT tgisinternal
                """
            )
        )
    assert trigger_count == 1


def test_schema_dry_run_reports_disabled_guard_without_mutating_it(guard_fixture):
    """Catches a same-name disabled trigger being treated as installed."""
    from app.commands.reconcile_schema import _reconcile

    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE operation_results "
            "DISABLE TRIGGER trg_transformation_result_immutable"
        )
    try:
        _added, failed, _missing, _blocking = _reconcile(dry_run=True)
        assert any(
            "trg_transformation_result_immutable" in item and "disabled" in item
            for item in failed
        )
        with db.engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT tgenabled FROM pg_trigger "
                    "WHERE tgname = 'trg_transformation_result_immutable' "
                    "AND tgrelid = 'operation_results'::regclass"
                )
            ) == "D"

        _added, repaired_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert repaired_failed == []
        with db.engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT tgenabled FROM pg_trigger "
                    "WHERE tgname = 'trg_transformation_result_immutable' "
                    "AND tgrelid = 'operation_results'::regclass"
                )
            ) == "O"
    finally:
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE operation_results "
                "ENABLE TRIGGER trg_transformation_result_immutable"
            )


def test_guard_installation_replaces_miswired_same_name_trigger(guard_fixture):
    """Catches trigger-name-only reconciliation accepting the wrong function."""
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_transformation_result_immutable ON operation_results"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER trg_transformation_result_immutable "
            "BEFORE UPDATE OR DELETE ON operation_results FOR EACH ROW "
            "EXECUTE FUNCTION public.archie_guard_transformation_receipt()"
        )
    try:
        with db.engine.begin() as connection:
            ensure_transformation_db_guards(connection)
        with db.engine.connect() as connection:
            function_name = connection.scalar(
                text(
                    "SELECT proc.proname FROM pg_trigger trigger "
                    "JOIN pg_proc proc ON proc.oid = trigger.tgfoid "
                    "WHERE trigger.tgname = 'trg_transformation_result_immutable' "
                    "AND trigger.tgrelid = 'operation_results'::regclass"
                )
            )
        assert function_name == "archie_reject_transformation_mutation"
    finally:
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP TRIGGER IF EXISTS trg_transformation_result_immutable "
                "ON operation_results"
            )
            connection.exec_driver_sql(
                "CREATE TRIGGER trg_transformation_result_immutable "
                "BEFORE UPDATE OR DELETE ON operation_results FOR EACH ROW "
                "EXECUTE FUNCTION public.archie_reject_transformation_mutation()"
            )


@pytest.mark.parametrize(
    ("trigger_clause", "drift_marker", "definition_marker"),
    (
        (
            "BEFORE UPDATE OR DELETE ON operation_results FOR EACH ROW "
            "WHEN (false)",
            "trigger_when",
            "WHEN (false)",
        ),
        (
            "BEFORE UPDATE OF created_at OR DELETE ON operation_results "
            "FOR EACH ROW",
            "trigger_columns",
            "UPDATE OF created_at",
        ),
    ),
)
def test_schema_reconciliation_repairs_conditional_or_column_limited_guard(
    guard_fixture,
    trigger_clause,
    drift_marker,
    definition_marker,
):
    """Catches a correct-name/function trigger whose predicate disables updates."""
    from app.commands.reconcile_schema import _reconcile

    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_transformation_result_immutable "
            "ON operation_results"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER trg_transformation_result_immutable "
            f"{trigger_clause} "
            "EXECUTE FUNCTION public.archie_reject_transformation_mutation()"
        )
    try:
        _added, failed, _missing, _blocking = _reconcile(dry_run=True)
        assert any(
            drift_marker in item
            and "trg_transformation_result_immutable" in item
            for item in failed
        )
        with db.engine.connect() as connection:
            tampered_definition = connection.scalar(
                text(
                    "SELECT pg_get_triggerdef(oid) FROM pg_trigger "
                    "WHERE tgname = 'trg_transformation_result_immutable' "
                    "AND tgrelid = 'operation_results'::regclass"
                )
            )
        assert definition_marker in tampered_definition

        _added, repaired_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert repaired_failed == []
        with db.engine.connect() as connection:
            repaired_definition = connection.scalar(
                text(
                    "SELECT pg_get_triggerdef(oid) FROM pg_trigger "
                    "WHERE tgname = 'trg_transformation_result_immutable' "
                    "AND tgrelid = 'operation_results'::regclass"
                )
            )
        assert definition_marker not in repaired_definition
        with pytest.raises(Exception, match="append-only"):
            _direct_driver_execute(
                "UPDATE operation_results SET request_digest = %s "
                "WHERE id = %s AND organization_id = %s",
                (
                    "e" * 64,
                    guard_fixture.result_id,
                    guard_fixture.organization_id,
                ),
            )
    finally:
        with db.engine.begin() as connection:
            ensure_transformation_db_guards(connection)


def test_schema_dry_run_reports_tampered_guard_function_then_apply_repairs_it(
    guard_fixture,
):
    """Catches a correct function name hiding a replaced permissive body."""
    from app.commands.reconcile_schema import _reconcile

    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE OR REPLACE FUNCTION public.archie_reject_transformation_mutation()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public AS $$
            BEGIN
                RETURN NEW;
            END;
            $$
            """
        )
    try:
        _added, failed, _missing, _blocking = _reconcile(dry_run=True)
        assert any(
            "archie_reject_transformation_mutation" in item
            and "function_body" in item
            for item in failed
        )
        with db.engine.connect() as connection:
            before_body = connection.scalar(
                text(
                    "SELECT prosrc FROM pg_proc proc "
                    "JOIN pg_namespace ns ON ns.oid = proc.pronamespace "
                    "WHERE ns.nspname = 'public' "
                    "AND proc.proname = 'archie_reject_transformation_mutation'"
                )
            )
        assert "RETURN NEW" in before_body

        _added, repaired_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert repaired_failed == []
        with pytest.raises(Exception, match="append-only"):
            _direct_driver_execute(
                "UPDATE public.operation_results SET request_digest = %s "
                "WHERE id = %s AND organization_id = %s",
                ("f" * 64, guard_fixture.result_id, guard_fixture.organization_id),
            )
    finally:
        with db.engine.begin() as connection:
            ensure_transformation_db_guards(connection)


def test_evidence_records_events_and_heads_reject_direct_mutation(
    evidence_scope: EvidenceScope,
):
    """Catches direct SQL rewriting evidence history or deleting its active pointer."""
    scope = evidence_scope
    result = _record_inventory(scope)
    record_id = result.object_ids["evidence_record_id"]
    head_id = result.object_ids["evidence_head_id"]
    event_id = result.object_ids["evidence_head_event_id"]

    for statement, parameters in (
        (
            "UPDATE public.evidence_records SET value_json = '{}'::json "
            "WHERE id = %s AND organization_id = %s",
            (record_id, scope.organization_id),
        ),
        (
            "DELETE FROM public.evidence_records WHERE id = %s AND organization_id = %s",
            (record_id, scope.organization_id),
        ),
        (
            "UPDATE public.evidence_head_events SET reason = 'forged' "
            "WHERE id = %s AND organization_id = %s",
            (event_id, scope.organization_id),
        ),
        (
            "DELETE FROM public.evidence_head_events WHERE id = %s AND organization_id = %s",
            (event_id, scope.organization_id),
        ),
        (
            "DELETE FROM public.evidence_claim_heads WHERE id = %s AND organization_id = %s",
            (head_id, scope.organization_id),
        ),
    ):
        with pytest.raises(Exception):
            _direct_driver_execute(statement, parameters)


def test_option_brief_versions_citations_and_decision_events_reject_direct_mutation(
    decision_scope: DecisionScope,
):
    """Catches direct SQL rewriting any immutable Task 7 decision artifact."""
    from app.modules.transformation_room.decision_service import (
        DecisionBriefService,
        TransformationOptionService,
    )
    from app.modules.transformation_room.domain import HumanAssertions

    scope = decision_scope
    option_version_ids = tuple(
        TransformationOptionService.freeze_version(
            actor=scope.actor,
            option_id=option_id,
            expected_revision=1,
            command_key=f"guard-option-{ordinal}",
        ).object_ids["option_version_id"]
        for ordinal, option_id in enumerate(scope.option_ids, start=1)
    )
    frozen = DecisionBriefService.freeze(
        actor=scope.actor,
        brief_id=scope.brief_id,
        option_version_ids=option_version_ids,
        evidence_ids=(scope.evidence_id,),
        assertions=HumanAssertions(
            True,
            ("cost_source_unknown",),
            (),
            "Human reviewed the complete guarded decision snapshot.",
        ),
        expected_revision=1,
        command_key="guard-brief",
    )
    version_id = frozen.object_ids["decision_brief_version_id"]
    with Session(db.engine) as session:
        option_citation_id = session.scalar(
            select(DecisionBriefOptionCitation.id).where(
                DecisionBriefOptionCitation.organization_id == scope.organization_id,
                DecisionBriefOptionCitation.brief_version_id == version_id,
            )
        )
        evidence_citation_id = session.scalar(
            select(DecisionBriefEvidenceCitation.id).where(
                DecisionBriefEvidenceCitation.organization_id == scope.organization_id,
                DecisionBriefEvidenceCitation.brief_version_id == version_id,
            )
        )
        event_id = session.scalar(
            select(DecisionEvent.id).where(
                DecisionEvent.organization_id == scope.organization_id,
                DecisionEvent.brief_version_id == version_id,
            )
        )

    attempts = (
        (
            "UPDATE public.transformation_option_versions SET content_hash = %s "
            "WHERE id = %s AND organization_id = %s",
            ("a" * 64, option_version_ids[0], scope.organization_id),
        ),
        (
            "DELETE FROM public.transformation_option_versions "
            "WHERE id = %s AND organization_id = %s",
            (option_version_ids[0], scope.organization_id),
        ),
        (
            "UPDATE public.decision_brief_versions SET content_hash = %s "
            "WHERE id = %s AND organization_id = %s",
            ("b" * 64, version_id, scope.organization_id),
        ),
        (
            "DELETE FROM public.decision_brief_option_citations "
            "WHERE id = %s AND organization_id = %s",
            (option_citation_id, scope.organization_id),
        ),
        (
            "UPDATE public.decision_brief_evidence_citations "
            "SET acknowledged = NOT acknowledged "
            "WHERE id = %s AND organization_id = %s",
            (evidence_citation_id, scope.organization_id),
        ),
        (
            "DELETE FROM public.decision_events "
            "WHERE id = %s AND organization_id = %s",
            (event_id, scope.organization_id),
        ),
    )
    for statement, parameters in attempts:
        with pytest.raises(Exception, match="append-only"):
            _direct_driver_execute(statement, parameters)


def test_frozen_brief_rejects_post_freeze_citation_membership_insert(
    decision_scope: DecisionScope,
):
    """Catches runtime appending a newly frozen option to an old brief version."""
    from app.modules.transformation_room.decision_service import (
        DecisionBriefService,
        TransformationOptionService,
    )
    from app.modules.transformation_room.domain import HumanAssertions

    scope = decision_scope
    option_version_ids = tuple(
        TransformationOptionService.freeze_version(
            actor=scope.actor,
            option_id=option_id,
            expected_revision=1,
            command_key=f"membership-option-{ordinal}",
        ).object_ids["option_version_id"]
        for ordinal, option_id in enumerate(scope.option_ids, start=1)
    )
    frozen = DecisionBriefService.freeze(
        actor=scope.actor,
        brief_id=scope.brief_id,
        option_version_ids=option_version_ids,
        evidence_ids=(scope.evidence_id,),
        assertions=HumanAssertions(
            True,
            ("cost_source_unknown",),
            (),
            "Human reviewed the frozen membership boundary.",
        ),
        expected_revision=1,
        command_key="membership-brief",
    )
    with Session(db.engine) as session, session.begin():
        option = session.get(TransformationOption, scope.option_ids[0])
        option.title = "A later option revision"
    later_version_id = TransformationOptionService.freeze_version(
        actor=scope.actor,
        option_id=scope.option_ids[0],
        expected_revision=3,
        command_key="membership-later-option",
    ).object_ids["option_version_id"]

    with pytest.raises(Exception, match="citation membership is frozen"):
        _direct_driver_execute(
            "INSERT INTO public.decision_brief_option_citations "
            "(organization_id, brief_version_id, option_version_id) "
            "VALUES (%s, %s, %s)",
            (
                scope.organization_id,
                frozen.object_ids["decision_brief_version_id"],
                later_version_id,
            ),
        )


def test_evidence_head_rejects_arbitrary_historical_cross_tenant_wrong_subject_and_jump(
    evidence_scope: EvidenceScope,
):
    """Catches every direct pointer/revision bypass named by the evidence contract."""
    scope = evidence_scope
    root = _record_inventory(scope)
    with Session(db.engine) as session, session.begin():
        application = session.get(
            __import__(
                "app.models.application_portfolio", fromlist=["ApplicationComponent"]
            ).ApplicationComponent,
            scope.application_id,
        )
        application.application_owner = "Guarded correction"
    correction = _record_inventory(
        scope, expected_revision=1, key="guarded-correction"
    )
    root_id = root.object_ids["evidence_record_id"]
    current_id = correction.object_ids["evidence_record_id"]
    head_id = correction.object_ids["evidence_head_id"]

    invalid_moves = (
        (
            "INSERT INTO public.evidence_claim_heads "
            "(organization_id, subject_type, subject_id, claim_key, source_identity, "
            "current_record_id, revision) VALUES (%s, 'application', %s, "
            "'forged_claim', 'forged:source', %s, 1)",
            (scope.organization_id, scope.application_id, root_id),
        ),
        (
            "UPDATE public.evidence_claim_heads SET current_record_id = %s, revision = 3 "
            "WHERE id = %s AND organization_id = %s",
            (root_id, head_id, scope.organization_id),
        ),
        (
            "UPDATE public.evidence_claim_heads SET revision = revision + 2 "
            "WHERE id = %s AND organization_id = %s",
            (head_id, scope.organization_id),
        ),
        (
            "UPDATE public.evidence_claim_heads SET organization_id = %s, "
            "current_record_id = %s, revision = revision + 1 WHERE id = %s",
            (scope.foreign_organization_id, current_id, head_id),
        ),
        (
            "UPDATE public.evidence_claim_heads SET subject_id = subject_id + 1, "
            "current_record_id = %s, revision = revision + 1 WHERE id = %s",
            (current_id, head_id),
        ),
    )
    for statement, parameters in invalid_moves:
        with pytest.raises(Exception):
            _direct_driver_execute(statement, parameters)


def test_evidence_head_rejects_wrong_predecessor_and_unaudited_function_move(
    evidence_scope: EvidenceScope,
):
    """Catches an inserted leaf moving a head without its exact event and live command."""
    scope = evidence_scope
    result = _record_inventory(scope)
    head_id = result.object_ids["evidence_head_id"]
    current_id = result.object_ids["evidence_record_id"]

    connection = db.engine.raw_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO public.evidence_records (
                organization_id, programme_id, workstream_id, candidate_id,
                subject_type, subject_id, claim_key, value_json, value_type,
                classification, collector_type, source_identity, source_type, source_version,
                source_checksum, source_system, collected_at, observed_at,
                freshness_status, freshness_rule_version, created_by_id,
                cited_evidence_ids, supersedes_id
            )
            SELECT organization_id, programme_id, workstream_id, candidate_id,
                   subject_type, subject_id, claim_key, value_json, value_type,
                   classification, 'system', source_identity, source_type,
                   'direct-wrong-predecessor',
                   source_checksum, source_system, clock_timestamp(), observed_at,
                   freshness_status, freshness_rule_version, created_by_id,
                   '[]'::json, NULL
            FROM public.evidence_records WHERE id = %s
            RETURNING id
            """,
            (current_id,),
        )
        wrong_predecessor_id = cursor.fetchone()[0]
        with pytest.raises(Exception):
            cursor.execute(
                "UPDATE public.evidence_claim_heads SET current_record_id = %s, revision = 2 "
                "WHERE id = %s AND organization_id = %s",
                (wrong_predecessor_id, head_id, scope.organization_id),
            )
        connection.rollback()
    finally:
        connection.close()

    with Session(db.engine) as session:
        event_count = session.scalar(
            select(db.func.count())
            .select_from(EvidenceHeadEvent)
            .where(
                EvidenceHeadEvent.organization_id == scope.organization_id,
                EvidenceHeadEvent.head_id == head_id,
            )
        )
        record_count = session.scalar(
            select(db.func.count())
            .select_from(EvidenceRecord)
            .where(
                EvidenceRecord.organization_id == scope.organization_id,
                EvidenceRecord.claim_key == "application_owner",
            )
        )
        head = session.get(EvidenceClaimHead, head_id)
    assert head.current_record_id == current_id and head.revision == 1
    assert event_count == 1 and record_count == 1


def test_evidence_event_rejects_orphan_insert_that_would_poison_next_revision(
    evidence_scope: EvidenceScope,
    guard_fixture,
):
    """Catches an arbitrary event reserving a head revision/new-record pair."""
    scope = evidence_scope
    result = _record_inventory(scope)
    with pytest.raises(Exception, match="evidence event is not bound to its head movement"):
        _direct_driver_execute(
            """
            WITH leaf AS (
                INSERT INTO public.evidence_records (
                    organization_id, programme_id, workstream_id, candidate_id,
                    subject_type, subject_id, claim_key, value_json, value_type,
                    classification, collector_type, source_identity, source_type,
                    source_version, source_checksum, source_system, collected_at,
                    observed_at, freshness_status, freshness_rule_version,
                    created_by_id, cited_evidence_ids, supersedes_id
                )
                SELECT organization_id, programme_id, workstream_id, candidate_id,
                       subject_type, subject_id, claim_key, value_json, value_type,
                       classification, collector_type, source_identity, source_type,
                       'poisoned-event', source_checksum, source_system,
                       clock_timestamp(), observed_at, freshness_status,
                       freshness_rule_version, created_by_id, '[]'::json, id
                FROM public.evidence_records WHERE id = %s
                RETURNING id, organization_id, created_by_id
            )
            INSERT INTO public.evidence_head_events (
                organization_id, head_id, old_record_id, new_record_id, actor_id,
                command_receipt_id, command_generation, reason, revision, created_txid
            )
            SELECT leaf.organization_id, %s, %s, leaf.id, leaf.created_by_id,
                   prior.command_receipt_id, prior.command_generation,
                   'poisoned revision', 2, txid_current()
            FROM leaf
            JOIN public.evidence_head_events AS prior ON prior.id = %s
            """,
            (
                result.object_ids["evidence_record_id"],
                result.object_ids["evidence_head_id"],
                result.object_ids["evidence_record_id"],
                result.object_ids["evidence_head_event_id"],
            ),
        )


@pytest.mark.parametrize(
    ("operation", "natural_key"),
    (
        ("programme.archive", "programme:unrelated"),
        ("evidence.observe", "evidence:unrelated"),
    ),
)
def test_evidence_advance_rejects_unrelated_operation_or_natural_key(
    evidence_scope: EvidenceScope,
    guard_fixture,
    operation,
    natural_key,
):
    """Catches a live fenced receipt being reused for an unrelated head action."""
    scope = evidence_scope
    result = _record_inventory(scope)
    claim = CommandService.claim_or_reconcile(
        actor=scope.actor,
        operation=operation,
        idempotency_key=f"unrelated-{operation}-{uuid.uuid4().hex}",
        request_digest=CommandService.request_digest({"unrelated": True}),
        natural_key=natural_key,
        authorizer=_allow_command,
    )

    with pytest.raises(
        Exception,
        match="receipt operation or natural key does not authorize evidence head",
    ):
        _direct_driver_execute(
            """
            WITH leaf AS (
                INSERT INTO public.evidence_records (
                    organization_id, programme_id, workstream_id, candidate_id,
                    subject_type, subject_id, claim_key, value_json, value_type,
                    classification, collector_type, source_identity, source_type,
                    source_version, source_checksum, source_system, collected_at,
                    observed_at, freshness_status, freshness_rule_version,
                    created_by_id, cited_evidence_ids, supersedes_id
                )
                SELECT organization_id, programme_id, workstream_id, candidate_id,
                       subject_type, subject_id, claim_key, value_json, value_type,
                       classification, collector_type, source_identity, source_type,
                       'unrelated-receipt', source_checksum, source_system,
                       clock_timestamp(), observed_at, freshness_status,
                       freshness_rule_version, created_by_id, '[]'::json, id
                FROM public.evidence_records WHERE id = %s
                RETURNING id
            )
            SELECT public.archie_advance_evidence_head(
                %s, leaf.id, 1, %s, %s, %s, %s
            ) FROM leaf
            """,
            (
                result.object_ids["evidence_record_id"],
                result.object_ids["evidence_head_id"],
                scope.actor.user_id,
                claim.receipt_id,
                claim.generation,
                claim.claim_token,
            ),
        )
