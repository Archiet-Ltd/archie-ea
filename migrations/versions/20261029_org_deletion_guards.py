"""Deleting an organisation admits its own append-only rows.

A platform administrator's organisation delete sets a transaction-local marker,
``archie.deleting_organisation``, before any row is removed. The twelve
delete-refusing trigger functions on this schema (re-listed from ``pg_trigger``:
``archie_guard_arb_cycle_history``, ``archie_guard_arb_decision_immutable``,
``archie_guard_delivery_export_attempt_mutation``, ``archie_guard_evidence_head``,
``archie_guard_transformation_receipt``, ``archie_reject_arb_condition_event_mutation``,
``archie_reject_arb_condition_evidence_mutation``, ``archie_reject_arb_subject_snapshot_mutation``,
``archie_reject_arb_submission_event_mutation``, ``archie_reject_outcome_measurement_mutation``,
``archie_reject_transformation_mutation``, ``reject_archie_evidence_mutation``) now admit a
DELETE, never an UPDATE, when a shared SQL helper (``archie_org_deletion_admits``) says the
marker is set and, where the table carries ``organization_id``, that it matches. No trigger is
disabled and ``session_replication_role`` is never used. Every other delete is refused exactly
as before.

The twelve functions, the helper, and the triggers that use them are otherwise unchanged; this
revision applies the exact same Python-defined SQL that ``flask init-db`` and
``flask reconcile-schema`` already install, so the two paths cannot drift apart.

Revision ID: 20261029_org_deletion_guards
Revises: 20261029_deliv_wp_nullable
Create Date: 2026-10-29
"""
from __future__ import annotations

from alembic import op

revision = "20261029_org_deletion_guards"
down_revision = "20261029_deliv_wp_nullable"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    from app.models.architecture_review_board import _install_arb_cycle_constraints
    from app.models.arb_condition_event import ensure_arb_condition_event_guards
    from app.models.arb_condition_evidence import ensure_arb_condition_evidence_guards
    from app.models.arb_decision_event import ensure_arb_decision_guards
    from app.models.arb_submission_event import ensure_arb_submission_event_guards
    from app.models.arb_submission_evidence import ensure_evidence_immutability_triggers
    from app.models.transformation_db_guards import ensure_transformation_db_guards
    from app.models.transformation_decision import ensure_arb_subject_snapshot_immutability
    from app.models.transformation_execution import ensure_execution_history_immutability

    # The shared helper plus archie_reject_transformation_mutation,
    # archie_guard_transformation_receipt and archie_guard_evidence_head.
    ensure_transformation_db_guards(connection)
    # archie_guard_arb_cycle_history (also installs the helper defensively).
    _install_arb_cycle_constraints(None, connection)
    # archie_guard_arb_decision_immutable.
    ensure_arb_decision_guards(connection)
    # archie_reject_arb_condition_event_mutation.
    ensure_arb_condition_event_guards(connection)
    # archie_reject_arb_condition_evidence_mutation.
    ensure_arb_condition_evidence_guards(connection)
    # archie_reject_arb_submission_event_mutation.
    ensure_arb_submission_event_guards(connection)
    # reject_archie_evidence_mutation.
    ensure_evidence_immutability_triggers(connection)
    # archie_reject_arb_subject_snapshot_mutation.
    ensure_arb_subject_snapshot_immutability(connection)
    # archie_guard_delivery_export_attempt_mutation, archie_reject_outcome_measurement_mutation.
    ensure_execution_history_immutability(connection)


_DOWNGRADE_STATEMENTS = (
    # archie_reject_transformation_mutation — drops the DELETE admission, restoring the
    # unconditional refusal save for the one existing UPDATE carve-out.
    r"""
CREATE OR REPLACE FUNCTION public.archie_reject_transformation_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF current_setting('archie.signed_envelope_repair', TRUE) = 'on' THEN
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'transformation_outbox_events' AND TG_OP = 'UPDATE' THEN
        IF NEW.id = OLD.id
           AND NEW.organization_id = OLD.organization_id
           AND NEW.operation_result_id IS NOT DISTINCT FROM OLD.operation_result_id
           AND NEW.entity_type IS NOT DISTINCT FROM OLD.entity_type
           AND NEW.entity_id IS NOT DISTINCT FROM OLD.entity_id
           AND NEW.event_id = OLD.event_id
           AND NEW.ordinal = OLD.ordinal
           AND NEW.event_type = OLD.event_type
           AND NEW.payload_json::jsonb = OLD.payload_json::jsonb
           AND NEW.created_at = OLD.created_at
           AND NEW.delivery_attempts >= OLD.delivery_attempts
           AND (
               NEW.published_at IS NOT DISTINCT FROM OLD.published_at
               OR (
                   NEW.published_at IS NOT NULL
                   AND (OLD.published_at IS NULL OR NEW.published_at >= OLD.published_at)
               )
           ) THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'transformation immutable rows are append-only'
        USING ERRCODE = '55000';
END;
$$
""",
    # archie_guard_transformation_receipt.
    r"""
CREATE OR REPLACE FUNCTION public.archie_guard_transformation_receipt()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    now_at_database timestamptz := clock_timestamp();
    reconciliation_secret bytea;
    reconciliation_challenge record;
    contender_failure_document jsonb;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'command receipt identity and terminal result are immutable'
            USING ERRCODE = '55000';
    END IF;

    IF NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.operation IS DISTINCT FROM OLD.operation
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.request_digest IS DISTINCT FROM OLD.request_digest
       OR NEW.natural_key IS DISTINCT FROM OLD.natural_key
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'command receipt identity, digest, and natural key are immutable'
            USING ERRCODE = '55000';
    END IF;

    IF OLD.operation_result_id IS NOT NULL
       AND NEW.operation_result_id IS DISTINCT FROM OLD.operation_result_id THEN
        RAISE EXCEPTION 'command receipt terminal result is immutable'
            USING ERRCODE = '55000';
    END IF;

    -- Heartbeat: same fence and state, expiry moves strictly forward.
    IF OLD.status = 'in_progress'
       AND NEW.status = 'in_progress'
       AND NEW.lease_generation = OLD.lease_generation
       AND NEW.claim_token = OLD.claim_token
       AND NEW.claimant_request_id = OLD.claimant_request_id
       AND NEW.operation_result_id IS NOT DISTINCT FROM OLD.operation_result_id
       AND NEW.attempt_count = OLD.attempt_count
       AND NEW.last_error_class IS NOT DISTINCT FROM OLD.last_error_class
       AND NEW.terminal_reason IS NOT DISTINCT FROM OLD.terminal_reason
       AND NEW.completed_at IS NOT DISTINCT FROM OLD.completed_at
       AND NEW.lease_expires_at > OLD.lease_expires_at
       AND NEW.lease_expires_at > now_at_database THEN
        RETURN NEW;
    END IF;

    -- Reclaim: only an expired/retryable non-terminal lease, exactly next fence.
    IF OLD.status IN ('in_progress', 'retryable_failure')
       AND (OLD.status = 'retryable_failure'
            OR OLD.lease_expires_at IS NULL
            OR OLD.lease_expires_at <= now_at_database)
       AND NEW.status = 'in_progress'
       AND NEW.lease_generation = OLD.lease_generation + 1
       AND NEW.claim_token IS NOT NULL
       AND length(NEW.claim_token) >= 32
       AND NEW.claim_token IS DISTINCT FROM OLD.claim_token
       AND NEW.lease_expires_at > now_at_database
       AND NEW.operation_result_id IS NULL
       AND NEW.attempt_count = OLD.attempt_count + 1
       AND NEW.terminal_reason IS NULL
       AND NEW.completed_at IS NULL THEN
        RETURN NEW;
    END IF;

    -- A known pre-commit transient is immediately reclaimable by the same digest.
    IF OLD.status = 'in_progress'
       AND NEW.status = 'retryable_failure'
       AND NEW.lease_generation = OLD.lease_generation
       AND NEW.claim_token = OLD.claim_token
       AND NEW.claimant_request_id = OLD.claimant_request_id
       AND NEW.operation_result_id IS NULL
       AND NEW.attempt_count = OLD.attempt_count
       AND NEW.last_error_class IS NOT NULL
       AND NEW.terminal_reason IS NULL
       AND NEW.lease_expires_at <= now_at_database
       AND NEW.completed_at IS NULL THEN
        RETURN NEW;
    END IF;

    -- Only the application-secret proof emitted for the exact materialisation
    -- boundary may persist the contender marker used by later reconciliation.
    IF OLD.status = 'in_progress'
       AND NEW.status = 'failed_non_retryable'
       AND NEW.lease_generation = OLD.lease_generation
       AND NEW.claim_token = OLD.claim_token
       AND NEW.claimant_request_id = OLD.claimant_request_id
       AND NEW.operation_result_id IS NULL
       AND NEW.attempt_count = OLD.attempt_count
       AND NEW.last_error_class = '_NaturalKeyContenderConflict'
       AND NEW.terminal_reason =
           'natural_key_contender_materialisation_conflict'
       AND NEW.lease_expires_at IS NULL
       AND NEW.completed_at IS NOT NULL THEN
        BEGIN
            contender_failure_document :=
                public.archie_verify_command_capability(
                    current_setting(
                        'archie.contender_failure_document', TRUE
                    ),
                    current_setting(
                        'archie.contender_failure_capability', TRUE
                    ),
                    'transformation-contender-failure-r1'
                );
        EXCEPTION WHEN OTHERS THEN
            contender_failure_document := NULL;
        END;
        IF contender_failure_document IS NOT NULL
           AND contender_failure_document IS NOT DISTINCT FROM
               jsonb_build_object(
                   'schema_version', 'transformation-contender-failure-r1',
                   'key_id', contender_failure_document->>'key_id',
                   'transaction_id', txid_current(),
                   'receipt_id', OLD.id,
                   'organization_id', OLD.organization_id,
                   'actor_id', OLD.actor_id,
                   'operation', OLD.operation,
                   'idempotency_key', OLD.idempotency_key,
                   'request_digest', OLD.request_digest,
                   'natural_key', OLD.natural_key,
                   'generation', OLD.lease_generation,
                   'claim_token', OLD.claim_token,
                   'claimant_request_id', OLD.claimant_request_id,
                   'error_class', NEW.last_error_class,
                   'contender_reason', NEW.terminal_reason
               ) THEN
            RETURN NEW;
        END IF;
    END IF;

    -- Validation/authorization failures are terminal but never business success.
    IF OLD.status = 'in_progress'
       AND NEW.status = 'failed_non_retryable'
       AND NEW.lease_generation = OLD.lease_generation
       AND NEW.claim_token = OLD.claim_token
       AND NEW.claimant_request_id = OLD.claimant_request_id
       AND NEW.operation_result_id IS NULL
       AND NEW.attempt_count = OLD.attempt_count
       AND NEW.last_error_class IS NOT NULL
       AND NEW.terminal_reason IS NOT NULL
       AND NEW.terminal_reason <>
           'natural_key_contender_materialisation_conflict'
       AND NEW.lease_expires_at IS NULL
       AND NEW.completed_at IS NOT NULL THEN
        RETURN NEW;
    END IF;

    -- Atomic finalise or result-led repair. The immutable result is the proof.
    IF OLD.status IN ('in_progress', 'retryable_failure', 'succeeded')
       AND OLD.operation_result_id IS NULL
       AND NEW.status = 'succeeded'
       AND NEW.lease_generation = OLD.lease_generation
       AND NEW.claim_token = OLD.claim_token
       AND NEW.claimant_request_id = OLD.claimant_request_id
       AND NEW.operation_result_id IS NOT NULL
       AND NEW.attempt_count = OLD.attempt_count
       AND NEW.terminal_reason IS NOT DISTINCT FROM OLD.terminal_reason
       AND NEW.lease_expires_at IS NULL
       AND NEW.completed_at IS NOT NULL
       AND EXISTS (
           SELECT 1
           FROM public.operation_results result
           WHERE result.id = NEW.operation_result_id
             AND result.organization_id = NEW.organization_id
             AND result.actor_id = NEW.actor_id
             AND result.operation = NEW.operation
             AND result.natural_key = NEW.natural_key
             AND result.request_digest = NEW.request_digest
       ) THEN
        RETURN NEW;
    END IF;

    -- A previously failed contender may converge on another receipt's exact
    -- immutable result only while the signed claim function holds a fresh,
    -- owner-secret fence bound to this transaction and next generation.
    IF OLD.status = 'failed_non_retryable'
       AND OLD.terminal_reason = 'natural_key_contender_materialisation_conflict'
       AND OLD.operation_result_id IS NULL
       AND NEW.status = 'succeeded'
       AND NEW.lease_generation = OLD.lease_generation + 1
       AND NEW.claim_token IS NOT NULL
       AND length(NEW.claim_token) >= 32
       AND NEW.claim_token IS DISTINCT FROM OLD.claim_token
       AND NEW.claimant_request_id IS NOT NULL
       AND NEW.operation_result_id IS NOT NULL
       AND NEW.attempt_count = OLD.attempt_count + 1
       AND NEW.last_error_class IS NULL
       AND NEW.terminal_reason IS NOT DISTINCT FROM OLD.terminal_reason
       AND NEW.lease_expires_at IS NULL
       AND NEW.completed_at IS NOT NULL
       AND EXISTS (
           SELECT 1
           FROM public.operation_results result
           WHERE result.id = NEW.operation_result_id
             AND result.organization_id = NEW.organization_id
             AND result.actor_id = NEW.actor_id
             AND result.operation = NEW.operation
             AND result.natural_key = NEW.natural_key
             AND result.request_digest = NEW.request_digest
       ) THEN
        SELECT challenge.*
          INTO reconciliation_challenge
          FROM public.archie_command_claim_challenges AS challenge
         WHERE challenge.nonce::text = current_setting(
                   'archie.receipt_reconcile_claim_nonce', TRUE
               )
           AND challenge.organization_id = OLD.organization_id
           AND challenge.actor_id = OLD.actor_id
           AND challenge.consumed_at IS NOT NULL
           AND challenge.consumed_at >= to_timestamp(
               challenge.issued_at_ms::double precision / 1000.0
           )
           AND challenge.consumed_at < to_timestamp(
               challenge.expires_at_ms::double precision / 1000.0
           );
        IF FOUND THEN
            SELECT capability.secret
              INTO reconciliation_secret
              FROM public.archie_command_capability_keys AS capability
             WHERE capability.key_id = current_setting(
                       'archie.receipt_reconcile_key_id', TRUE
                   )
               AND capability.active IS TRUE;
        END IF;
        IF reconciliation_challenge.nonce IS NOT NULL
           AND FOUND
           AND current_setting(
               'archie.receipt_reconcile_fence', TRUE
           ) = encode(
               public.archie_hmac_sha256(
                   convert_to(
                       jsonb_build_object(
                           'schema_version',
                           'transformation-receipt-reconcile-r1',
                           'transaction_id', txid_current(),
                           'receipt_id', OLD.id,
                           'organization_id', OLD.organization_id,
                           'actor_id', OLD.actor_id,
                           'operation', OLD.operation,
                           'idempotency_key', OLD.idempotency_key,
                           'request_digest', OLD.request_digest,
                           'natural_key', OLD.natural_key,
                           'previous_generation', OLD.lease_generation,
                           'claim_token', NEW.claim_token,
                           'claimant_request_id', NEW.claimant_request_id,
                           'operation_result_id', NEW.operation_result_id,
                           'claim_nonce', reconciliation_challenge.nonce::text,
                           'claim_issued_at_ms',
                               reconciliation_challenge.issued_at_ms,
                           'claim_expires_at_ms',
                               reconciliation_challenge.expires_at_ms,
                           'contender_reason', OLD.terminal_reason
                       )::text,
                       'UTF8'
                   ),
                   reconciliation_secret
               ),
               'hex'
           ) THEN
            RETURN NEW;
        END IF;
    END IF;

    RAISE EXCEPTION 'invalid command receipt transition or fence'
        USING ERRCODE = '55000';
END;
$$
""",
    # archie_guard_evidence_head.
    r"""
CREATE OR REPLACE FUNCTION public.archie_guard_evidence_head()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.current_record_id IS NOT NULL OR NEW.revision <> 0 THEN
            RAISE EXCEPTION 'evidence head must be created empty at revision zero'
                USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.current_record_id IS NOT NULL OR EXISTS (
            SELECT 1 FROM public.evidence_head_events event
            WHERE event.head_id = OLD.id
        ) THEN
            RAISE EXCEPTION 'evidence head with history cannot be deleted'
                USING ERRCODE = '55000';
        END IF;
        RETURN OLD;
    END IF;

    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.subject_type IS DISTINCT FROM OLD.subject_type
       OR NEW.subject_id IS DISTINCT FROM OLD.subject_id
       OR NEW.claim_key IS DISTINCT FROM OLD.claim_key
       OR NEW.source_identity IS DISTINCT FROM OLD.source_identity
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'evidence head identity is immutable'
            USING ERRCODE = '55000';
    END IF;
    IF NEW.current_record_id IS NULL OR NEW.revision <> OLD.revision + 1 THEN
        RAISE EXCEPTION 'evidence head revision must advance exactly once'
            USING ERRCODE = '55000';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.evidence_records record
        WHERE record.id = NEW.current_record_id
          AND record.organization_id = NEW.organization_id
          AND record.subject_type = NEW.subject_type
          AND record.subject_id = NEW.subject_id
          AND record.claim_key = NEW.claim_key
          AND record.source_identity = NEW.source_identity
          AND record.supersedes_id IS NOT DISTINCT FROM OLD.current_record_id
    ) THEN
        RAISE EXCEPTION 'evidence head target is outside its chain or has wrong predecessor'
            USING ERRCODE = '55000';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.evidence_head_events event
        JOIN public.command_idempotency_records receipt
          ON receipt.id = event.command_receipt_id
         AND receipt.organization_id = event.organization_id
         AND receipt.actor_id = event.actor_id
        WHERE event.organization_id = NEW.organization_id
          AND event.head_id = NEW.id
          AND event.old_record_id IS NOT DISTINCT FROM OLD.current_record_id
          AND event.new_record_id = NEW.current_record_id
          AND event.revision = NEW.revision
          AND event.created_txid = txid_current()
          AND receipt.status = 'in_progress'
          AND receipt.lease_generation = event.command_generation
          AND receipt.lease_expires_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'evidence head move requires same-transaction fenced event'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$
""",
    # archie_guard_arb_cycle_history (arb_review_cycles, arb_review_items).
    r"""
CREATE OR REPLACE FUNCTION public.archie_guard_arb_cycle_history()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_TABLE_NAME = 'arb_review_cycles' THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'ARB review cycle history is append-only'
                USING ERRCODE = '55000';
        END IF;
        IF ROW(
            OLD.organization_id, OLD.subject_type, OLD.subject_id,
            OLD.decision_brief_id, OLD.solution_id, OLD.architecture_model_id,
            OLD.adr_id, OLD.decision_brief_version_id,
            OLD.solution_evidence_snapshot_id, OLD.subject_evidence_snapshot_id,
            OLD.review_number, OLD.cycle_number, OLD.predecessor_cycle_id,
            OLD.migration_gap_reason, OLD.legacy_source_type,
            OLD.legacy_source_id, OLD.opened_at
        ) IS DISTINCT FROM ROW(
            NEW.organization_id, NEW.subject_type, NEW.subject_id,
            NEW.decision_brief_id, NEW.solution_id, NEW.architecture_model_id,
            NEW.adr_id, NEW.decision_brief_version_id,
            NEW.solution_evidence_snapshot_id, NEW.subject_evidence_snapshot_id,
            NEW.review_number, NEW.cycle_number, NEW.predecessor_cycle_id,
            NEW.migration_gap_reason, NEW.legacy_source_type,
            NEW.legacy_source_id, NEW.opened_at
        ) THEN
            RAISE EXCEPTION 'ARB review cycle identity and evidence are immutable'
                USING ERRCODE = '55000';
        END IF;
        IF OLD.closed_at IS NOT NULL AND ROW(
            OLD.status, OLD.closed_at, OLD.terminal_outcome,
            OLD.condition_projection_revision
        ) IS DISTINCT FROM ROW(
            NEW.status, NEW.closed_at, NEW.terminal_outcome,
            NEW.condition_projection_revision
        ) AND NOT (
            OLD.terminal_outcome = 'approved_with_conditions'
            AND NEW.terminal_outcome = OLD.terminal_outcome
            AND NEW.closed_at = OLD.closed_at
            AND OLD.status IN ('approved_with_conditions', 'approved')
            AND NEW.status IN ('approved_with_conditions', 'approved')
            AND NEW.condition_projection_revision =
                COALESCE(OLD.condition_projection_revision, 0) + 1
            AND NEW.status = CASE WHEN NOT EXISTS (
                SELECT 1 FROM arb_canonical_conditions aggregate_condition
                WHERE aggregate_condition.review_cycle_id = NEW.id
                  AND aggregate_condition.organization_id = NEW.organization_id
                  AND aggregate_condition.status NOT IN ('fulfilled', 'waived')
            ) THEN 'approved' ELSE 'approved_with_conditions' END
            AND EXISTS (
                SELECT 1 FROM arb_condition_events condition_event
                JOIN command_idempotency_records receipt
                  ON receipt.id = condition_event.command_receipt_id
                 AND receipt.organization_id = condition_event.organization_id
                 AND receipt.actor_id = condition_event.actor_id
                 AND receipt.lease_generation = condition_event.command_generation
                 AND receipt.operation = CASE condition_event.event_type
                     WHEN 'submit_evidence' THEN 'arb.condition.evidence.submit'
                     WHEN 'verify' THEN 'arb.condition.evidence.verify'
                     WHEN 'waive' THEN 'arb.condition.waive'
                     WHEN 'waiver_expired' THEN 'arb.condition.waiver.expire'
                   END
                 AND receipt.natural_key = 'arb-condition:' ||
                     condition_event.organization_id::text || ':' ||
                     condition_event.condition_id::text || ':' ||
                     condition_event.event_type || ':' ||
                     condition_event.condition_revision::text
                WHERE condition_event.review_cycle_id = NEW.id
                  AND condition_event.organization_id = NEW.organization_id
                  AND condition_event.projection_status = NEW.status
                  AND condition_event.projection_revision =
                      NEW.condition_projection_revision
            )
        ) THEN
            RAISE EXCEPTION 'closed ARB review cycle history is immutable'
                USING ERRCODE = '55000';
        END IF;
    ELSIF TG_TABLE_NAME = 'arb_review_items' THEN
        IF TG_OP = 'DELETE' AND OLD.review_cycle_id IS NOT NULL THEN
            RAISE EXCEPTION 'typed ARB review history is append-only'
                USING ERRCODE = '55000';
        END IF;
        IF TG_OP = 'UPDATE' AND OLD.review_cycle_id IS NOT NULL AND ROW(
            OLD.organization_id, OLD.subject_type, OLD.subject_id,
            OLD.decision_brief_id, OLD.solution_id, OLD.architecture_model_id,
            OLD.adr_id, OLD.decision_brief_version_id,
            OLD.solution_evidence_snapshot_id, OLD.subject_evidence_snapshot_id,
            OLD.review_cycle_id
        ) IS DISTINCT FROM ROW(
            NEW.organization_id, NEW.subject_type, NEW.subject_id,
            NEW.decision_brief_id, NEW.solution_id, NEW.architecture_model_id,
            NEW.adr_id, NEW.decision_brief_version_id,
            NEW.solution_evidence_snapshot_id, NEW.subject_evidence_snapshot_id,
            NEW.review_cycle_id
        ) THEN
            RAISE EXCEPTION 'typed ARB review projection is immutable'
                USING ERRCODE = '55000';
        END IF;
        IF TG_OP = 'UPDATE' AND OLD.review_cycle_id IS NOT NULL
           AND OLD.status = 'historical_unverified'
           AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
            RAISE EXCEPTION 'historical unverified ARB review is immutable'
                USING ERRCODE = '55000';
        END IF;
        IF TG_OP = 'UPDATE' AND OLD.review_cycle_id IS NOT NULL
           AND OLD.decision IS NOT NULL
           AND ROW(OLD.status, OLD.decision,
                   OLD.condition_projection_revision)
               IS DISTINCT FROM
               ROW(NEW.status, NEW.decision,
                   NEW.condition_projection_revision)
           AND NOT (
               OLD.decision = 'approved_with_conditions'
               AND NEW.decision = OLD.decision
               AND OLD.status IN ('approved_with_conditions', 'approved')
               AND NEW.status IN ('approved_with_conditions', 'approved')
               AND NEW.condition_projection_revision =
                   COALESCE(OLD.condition_projection_revision, 0) + 1
               AND NEW.status = CASE WHEN NOT EXISTS (
                   SELECT 1 FROM arb_canonical_conditions aggregate_condition
                   WHERE aggregate_condition.review_item_id = NEW.id
                     AND aggregate_condition.organization_id = NEW.organization_id
                     AND aggregate_condition.status NOT IN ('fulfilled', 'waived')
               ) THEN 'approved' ELSE 'approved_with_conditions' END
               AND EXISTS (
                   SELECT 1 FROM arb_condition_events condition_event
                   JOIN command_idempotency_records receipt
                     ON receipt.id = condition_event.command_receipt_id
                    AND receipt.organization_id = condition_event.organization_id
                    AND receipt.actor_id = condition_event.actor_id
                    AND receipt.lease_generation = condition_event.command_generation
                    AND receipt.operation = CASE condition_event.event_type
                        WHEN 'submit_evidence' THEN 'arb.condition.evidence.submit'
                        WHEN 'verify' THEN 'arb.condition.evidence.verify'
                        WHEN 'waive' THEN 'arb.condition.waive'
                        WHEN 'waiver_expired' THEN 'arb.condition.waiver.expire'
                      END
                    AND receipt.natural_key = 'arb-condition:' ||
                        condition_event.organization_id::text || ':' ||
                        condition_event.condition_id::text || ':' ||
                        condition_event.event_type || ':' ||
                        condition_event.condition_revision::text
                   WHERE condition_event.review_item_id = NEW.id
                     AND condition_event.organization_id = NEW.organization_id
                     AND condition_event.projection_status = NEW.status
                     AND condition_event.projection_revision =
                         NEW.condition_projection_revision
               )
           ) THEN
            RAISE EXCEPTION 'typed ARB condition projection is invalid'
                USING ERRCODE = '55000';
        END IF;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$
""",
    # archie_guard_arb_decision_immutable (arb_decision_events, arb_canonical_conditions)
    # plus the triggers that use it — unaffected, re-declared for completeness.
    r"""
CREATE OR REPLACE FUNCTION public.archie_guard_arb_decision_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ARB decision history is append-only' USING ERRCODE='55000'; END IF;
 IF TG_TABLE_NAME='arb_decision_events' THEN RAISE EXCEPTION 'ARB decision events are append-only' USING ERRCODE='55000'; END IF;
 IF ROW(OLD.organization_id,OLD.decision_event_id,OLD.review_cycle_id,OLD.review_item_id,OLD.condition_number,OLD.description,OLD.category,OLD.due_date,OLD.blocks_execution,OLD.created_at) IS DISTINCT FROM ROW(NEW.organization_id,NEW.decision_event_id,NEW.review_cycle_id,NEW.review_item_id,NEW.condition_number,NEW.description,NEW.category,NEW.due_date,NEW.blocks_execution,NEW.created_at) THEN RAISE EXCEPTION 'ARB condition identity and terms are immutable' USING ERRCODE='55000'; END IF;
 IF OLD.legacy_lifecycle_provenance::jsonb IS DISTINCT FROM NEW.legacy_lifecycle_provenance::jsonb
 THEN RAISE EXCEPTION 'legacy ARB condition provenance is immutable' USING ERRCODE='55000'; END IF;
 IF ROW(OLD.status,OLD.revision,OLD.responsible_id,OLD.submitted_evidence_id,
        OLD.evidence_submitted_by_id,OLD.evidence_submitted_at,OLD.verified_by_id,
        OLD.verified_at,OLD.fulfilled_at,OLD.fulfilled_by_id,
        OLD.fulfilment_evidence_id,OLD.waived_at,OLD.waived_by_id,
        OLD.waiver_reason,OLD.waiver_expires_at,OLD.compensating_control,
        OLD.waiver_prior_status,OLD.waiver_scope_json::jsonb)
    IS DISTINCT FROM
    ROW(NEW.status,NEW.revision,NEW.responsible_id,NEW.submitted_evidence_id,
        NEW.evidence_submitted_by_id,NEW.evidence_submitted_at,NEW.verified_by_id,
        NEW.verified_at,NEW.fulfilled_at,NEW.fulfilled_by_id,
        NEW.fulfilment_evidence_id,NEW.waived_at,NEW.waived_by_id,
        NEW.waiver_reason,NEW.waiver_expires_at,NEW.compensating_control,
        NEW.waiver_prior_status,NEW.waiver_scope_json::jsonb)
 AND NOT EXISTS (
   SELECT 1 FROM public.arb_condition_events condition_event
   JOIN public.command_idempotency_records receipt
     ON receipt.id=condition_event.command_receipt_id
    AND receipt.organization_id=condition_event.organization_id
    AND receipt.actor_id=condition_event.actor_id
    AND receipt.lease_generation=condition_event.command_generation
    AND receipt.operation=CASE condition_event.event_type
      WHEN 'submit_evidence' THEN 'arb.condition.evidence.submit'
      WHEN 'verify' THEN 'arb.condition.evidence.verify'
      WHEN 'waive' THEN 'arb.condition.waive'
      WHEN 'waiver_expired' THEN 'arb.condition.waiver.expire' END
    AND receipt.natural_key='arb-condition:' || condition_event.organization_id::text || ':' || condition_event.condition_id::text || ':' || condition_event.event_type || ':' || condition_event.condition_revision::text
   WHERE condition_event.condition_id=NEW.id
     AND condition_event.organization_id=NEW.organization_id
     AND condition_event.decision_event_id=NEW.decision_event_id
     AND condition_event.review_cycle_id=NEW.review_cycle_id
     AND condition_event.review_item_id=NEW.review_item_id
     AND condition_event.from_state=OLD.status
     AND condition_event.to_state=NEW.status
     AND condition_event.condition_revision=NEW.revision
     AND NEW.revision=OLD.revision + 1)
 THEN RAISE EXCEPTION 'ARB condition lifecycle mutation lacks exact event provenance' USING ERRCODE='55000'; END IF;
 RETURN NEW; END $$;
 DROP TRIGGER IF EXISTS trg_arb_decision_event_open_state ON public.arb_decision_events; CREATE TRIGGER trg_arb_decision_event_open_state BEFORE INSERT ON public.arb_decision_events FOR EACH ROW EXECUTE FUNCTION public.archie_validate_arb_decision_open_state();
 DROP TRIGGER IF EXISTS trg_arb_decision_event_membership ON public.arb_decision_events; CREATE CONSTRAINT TRIGGER trg_arb_decision_event_membership AFTER INSERT OR UPDATE ON public.arb_decision_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.archie_validate_arb_decision_event();
 DROP TRIGGER IF EXISTS trg_arb_decision_event_immutable ON public.arb_decision_events; CREATE TRIGGER trg_arb_decision_event_immutable BEFORE UPDATE OR DELETE ON public.arb_decision_events FOR EACH ROW EXECUTE FUNCTION public.archie_guard_arb_decision_immutable();
 DROP TRIGGER IF EXISTS trg_arb_condition_membership ON public.arb_canonical_conditions; CREATE CONSTRAINT TRIGGER trg_arb_condition_membership AFTER INSERT OR UPDATE ON public.arb_canonical_conditions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.archie_validate_arb_condition();
 DROP TRIGGER IF EXISTS trg_arb_condition_immutable ON public.arb_canonical_conditions; CREATE TRIGGER trg_arb_condition_immutable BEFORE UPDATE OR DELETE ON public.arb_canonical_conditions FOR EACH ROW EXECUTE FUNCTION public.archie_guard_arb_decision_immutable();
""",
    # archie_reject_arb_condition_event_mutation (arb_condition_events).
    r"""
CREATE OR REPLACE FUNCTION public.archie_reject_arb_condition_event_mutation() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN RAISE EXCEPTION 'ARB condition events are append-only' USING ERRCODE='55000'; END $$;
DROP TRIGGER IF EXISTS trg_arb_condition_event_source ON public.arb_condition_events; CREATE TRIGGER trg_arb_condition_event_source BEFORE INSERT ON public.arb_condition_events FOR EACH ROW EXECUTE FUNCTION public.archie_validate_arb_condition_event_source();
DROP TRIGGER IF EXISTS trg_arb_condition_event_final ON public.arb_condition_events; CREATE CONSTRAINT TRIGGER trg_arb_condition_event_final AFTER INSERT OR UPDATE ON public.arb_condition_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.archie_validate_arb_condition_event_final();
DROP TRIGGER IF EXISTS trg_arb_condition_event_immutable ON public.arb_condition_events; CREATE TRIGGER trg_arb_condition_event_immutable BEFORE UPDATE OR DELETE ON public.arb_condition_events FOR EACH ROW EXECUTE FUNCTION public.archie_reject_arb_condition_event_mutation();
""",
    # archie_reject_arb_condition_evidence_mutation (arb_condition_evidence_records).
    r"""
CREATE OR REPLACE FUNCTION public.archie_reject_arb_condition_evidence_mutation() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN RAISE EXCEPTION 'ARB condition evidence is append-only' USING ERRCODE='55000'; END $$;
DROP TRIGGER IF EXISTS trg_arb_condition_evidence_membership ON public.arb_condition_evidence_records; CREATE CONSTRAINT TRIGGER trg_arb_condition_evidence_membership AFTER INSERT OR UPDATE ON public.arb_condition_evidence_records DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.archie_validate_arb_condition_evidence();
DROP TRIGGER IF EXISTS trg_arb_condition_evidence_immutable ON public.arb_condition_evidence_records; CREATE TRIGGER trg_arb_condition_evidence_immutable BEFORE UPDATE OR DELETE ON public.arb_condition_evidence_records FOR EACH ROW EXECUTE FUNCTION public.archie_reject_arb_condition_evidence_mutation();
""",
    # archie_reject_arb_submission_event_mutation (arb_submission_events).
    r"""
CREATE OR REPLACE FUNCTION public.archie_reject_arb_submission_event_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'ARB submission events are append-only' USING ERRCODE = '55000';
END;
$$
""",
    # reject_archie_evidence_mutation (arb_submission_evidence_snapshots, workbench_artifact_evidence).
    r"""
CREATE OR REPLACE FUNCTION reject_archie_evidence_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ARB and workbench evidence records are append-only'
        USING ERRCODE = '55000';
END;
$$
""",
    # archie_reject_arb_subject_snapshot_mutation (arb_subject_evidence_snapshots).
    r"""
CREATE OR REPLACE FUNCTION archie_reject_arb_subject_snapshot_mutation()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION 'ARB subject evidence snapshots are append-only'
        USING ERRCODE = '55000';
END;
$$
""",
    # archie_reject_outcome_measurement_mutation (outcome_measurements).
    r"""
CREATE OR REPLACE FUNCTION public.archie_reject_outcome_measurement_mutation()
RETURNS trigger LANGUAGE plpgsql AS $guard$
        BEGIN
            RAISE EXCEPTION 'outcome measurements are append-only';
        END;
    $guard$
""",
    # archie_guard_delivery_export_attempt_mutation (delivery_export_attempts).
    r"""
CREATE OR REPLACE FUNCTION public.archie_guard_delivery_export_attempt_mutation()
RETURNS trigger LANGUAGE plpgsql AS $guard$
        BEGIN
            IF OLD.status IN ('succeeded', 'failed', 'indeterminate') THEN
                RAISE EXCEPTION 'completed delivery export attempts are immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
    $guard$
""",
)


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    for statement in _DOWNGRADE_STATEMENTS:
        connection.exec_driver_sql(statement)
    # The helper is no longer referenced by any of the twelve functions above.
    connection.exec_driver_sql(
        "DROP FUNCTION IF EXISTS public.archie_org_deletion_admits(integer)"
    )
