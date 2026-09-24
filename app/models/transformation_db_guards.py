"""PostgreSQL append-only and fenced-transition guards for command records."""

from __future__ import annotations

import hashlib
import os

from flask import current_app, has_app_context
import logging

from sqlalchemy import event

from app.models.transformation_execution import (
    CommandMaterialisation,
    CommandIdempotencyRecord,
    OperationOutboxEvent,
    OperationResult,
)
from app.models.transformation_decision import (
    DecisionBriefEvidenceCitation,
    DecisionBriefOptionCitation,
    DecisionBriefVersion,
    DecisionEvent,
    TransformationOptionVersion,
)
from app.models.transformation_evidence import (
    CandidateOverlapDisposition,
    CandidateSignal,
    EvidenceClaimHead,
    EvidenceHeadEvent,
    EvidenceRecord,
)
from scripts.database.transformation_privilege_policy import (
    PROTECTED_RUNTIME_TABLE_PRIVILEGES,
    PROTECTED_RUNTIME_UPDATE_COLUMNS,
    RUNTIME_EXECUTE_FUNCTIONS,
)


TRANSFORMATION_RUNTIME_ROLE = "archie_runtime"
COMMAND_CAPABILITY_SECRET_ENV = "TRANSFORMATION_COMMAND_CAPABILITY_SECRET"
COMMAND_CAPABILITY_PREVIOUS_SECRETS_ENV = (
    "TRANSFORMATION_COMMAND_CAPABILITY_PREVIOUS_SECRETS"
)


_COMMAND_CAPABILITY_TABLE_SQL = r"""
CREATE TABLE IF NOT EXISTS public.archie_command_capability_keys (
    key_id text PRIMARY KEY,
    secret bytea NOT NULL,
    active boolean NOT NULL DEFAULT TRUE,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    retired_at timestamptz
)
"""


_COMMAND_CLAIM_CHALLENGE_TABLE_SQL = r"""
CREATE TABLE IF NOT EXISTS public.archie_command_claim_challenges (
    nonce uuid PRIMARY KEY,
    organization_id bigint NOT NULL,
    actor_id bigint NOT NULL,
    issued_at_ms bigint NOT NULL,
    expires_at_ms bigint NOT NULL,
    consumed_at timestamptz,
    CONSTRAINT ck_command_claim_challenge_lifetime
        CHECK (issued_at_ms > 0 AND expires_at_ms > issued_at_ms)
)
"""


_HMAC_SHA256_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_hmac_sha256(p_data bytea, p_key bytea)
RETURNS bytea
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    block_key bytea;
    inner_pad bytea := decode(repeat('36', 64), 'hex');
    outer_pad bytea := decode(repeat('5c', 64), 'hex');
    position integer;
BEGIN
    block_key := CASE WHEN length(p_key) > 64 THEN sha256(p_key) ELSE p_key END;
    block_key := block_key || decode(repeat('00', 64 - length(block_key)), 'hex');
    FOR position IN 0..63 LOOP
        inner_pad := set_byte(
            inner_pad, position,
            get_byte(inner_pad, position) # get_byte(block_key, position)
        );
        outer_pad := set_byte(
            outer_pad, position,
            get_byte(outer_pad, position) # get_byte(block_key, position)
        );
    END LOOP;
    RETURN sha256(outer_pad || sha256(inner_pad || p_data));
END;
$$
"""


_VERIFY_COMMAND_CAPABILITY_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_verify_command_capability(
    p_document text,
    p_capability text,
    p_schema_version text
)
RETURNS jsonb
LANGUAGE plpgsql
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    document jsonb;
    signing_secret bytea;
    supplied bytea;
BEGIN
    BEGIN
        document := p_document::jsonb;
        supplied := decode(p_capability, 'hex');
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'command capability is invalid' USING ERRCODE = '42501';
    END;
    IF jsonb_typeof(document) <> 'object'
       OR document->>'schema_version' IS DISTINCT FROM p_schema_version
       OR length(p_capability) <> 64 THEN
        RAISE EXCEPTION 'command capability is invalid' USING ERRCODE = '42501';
    END IF;
    SELECT capability.secret
      INTO signing_secret
      FROM public.archie_command_capability_keys AS capability
     WHERE capability.key_id = document->>'key_id'
       AND capability.active IS TRUE;
    IF NOT FOUND OR supplied IS DISTINCT FROM public.archie_hmac_sha256(
        convert_to(p_document, 'UTF8'), signing_secret
    ) THEN
        RAISE EXCEPTION 'command capability is invalid' USING ERRCODE = '42501';
    END IF;
    RETURN document;
END;
$$
"""


_ISSUE_COMMAND_CLAIM_CHALLENGE_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_issue_command_claim_challenge(
    p_organization_id bigint,
    p_actor_id bigint,
    p_valid_for_milliseconds integer
)
RETURNS TABLE (
    claim_nonce text,
    claim_issued_at_ms bigint,
    claim_expires_at_ms bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    issued_at_milliseconds bigint := floor(
        extract(epoch FROM clock_timestamp()) * 1000
    )::bigint;
    expires_at_milliseconds bigint;
    issued_nonce uuid := __ARCHIE_GEN_RANDOM_UUID__();
BEGIN
    IF p_organization_id <= 0 OR p_actor_id <= 0
       OR p_valid_for_milliseconds < 50
       OR p_valid_for_milliseconds > 60000 THEN
        RAISE EXCEPTION 'command claim challenge fields are invalid'
            USING ERRCODE = '42501';
    END IF;
    expires_at_milliseconds :=
        issued_at_milliseconds + p_valid_for_milliseconds;
    DELETE FROM public.archie_command_claim_challenges AS expired
     WHERE expired.expires_at_ms < issued_at_milliseconds - 86400000;
    INSERT INTO public.archie_command_claim_challenges (
        nonce, organization_id, actor_id, issued_at_ms, expires_at_ms
    ) VALUES (
        issued_nonce, p_organization_id, p_actor_id,
        issued_at_milliseconds, expires_at_milliseconds
    );
    RETURN QUERY SELECT issued_nonce::text, issued_at_milliseconds,
                        expires_at_milliseconds;
END;
$$
"""


_CLAIM_COMMAND_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_claim_transformation_command(
    p_document text,
    p_capability text
)
RETURNS TABLE (
    claim_outcome text,
    command_receipt_id bigint,
    command_generation integer,
    command_claim_token text,
    operation_result_id bigint,
    conflict_reason text,
    conflict_error_class text,
    retry_after_seconds double precision
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    document jsonb;
    command_org_id bigint;
    command_actor_id bigint;
    command_operation text;
    command_idempotency_key text;
    command_request_digest text;
    command_natural_key text;
    new_claim_token text;
    new_claimant_request_id text;
    claim_nonce uuid;
    claim_issued_at_ms bigint;
    claim_expires_at_ms bigint;
    contender_reason text;
    lease_milliseconds integer;
    now_at_database timestamptz := clock_timestamp();
    expires_at timestamptz;
    receipt record;
    result record;
    inserted_id bigint;
    reconciliation_secret bytea;
    claim_challenge record;
BEGIN
    document := public.archie_verify_command_capability(
        p_document, p_capability, 'transformation-command-claim-r1'
    );
    BEGIN
        command_org_id := (document->>'organization_id')::bigint;
        command_actor_id := (document->>'actor_id')::bigint;
        command_operation := document->>'operation';
        command_idempotency_key := document->>'idempotency_key';
        command_request_digest := document->>'request_digest';
        command_natural_key := document->>'natural_key';
        new_claim_token := document->>'claim_token';
        new_claimant_request_id := document->>'claimant_request_id';
        claim_nonce := (document->>'claim_nonce')::uuid;
        claim_issued_at_ms := (document->>'claim_issued_at_ms')::bigint;
        claim_expires_at_ms := (document->>'claim_expires_at_ms')::bigint;
        contender_reason := document->>'contender_reason';
        lease_milliseconds := (document->>'lease_milliseconds')::integer;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'command capability fields are invalid' USING ERRCODE = '42501';
    END;
    IF document IS DISTINCT FROM jsonb_build_object(
           'schema_version', 'transformation-command-claim-r1',
           'key_id', document->>'key_id',
           'organization_id', command_org_id,
           'actor_id', command_actor_id,
           'operation', command_operation,
           'idempotency_key', command_idempotency_key,
           'request_digest', command_request_digest,
           'natural_key', command_natural_key,
           'claim_token', new_claim_token,
           'claimant_request_id', new_claimant_request_id,
           'claim_nonce', claim_nonce::text,
           'claim_issued_at_ms', claim_issued_at_ms,
           'claim_expires_at_ms', claim_expires_at_ms,
           'contender_reason', contender_reason,
           'lease_milliseconds', lease_milliseconds
       )
       OR command_org_id <= 0 OR command_actor_id <= 0
       OR command_operation IS NULL OR command_operation = ''
       OR command_idempotency_key IS NULL OR command_idempotency_key = ''
       OR command_natural_key IS NULL OR command_natural_key = ''
       OR length(command_request_digest) <> 64
       OR length(new_claim_token) <> 64
       OR new_claimant_request_id IS NULL OR new_claimant_request_id = ''
       OR claim_issued_at_ms <= 0
       OR claim_expires_at_ms <= claim_issued_at_ms
       OR contender_reason <>
           'natural_key_contender_materialisation_conflict'
       OR lease_milliseconds < 10 OR lease_milliseconds > 3600000 THEN
        RAISE EXCEPTION 'command capability fields are invalid' USING ERRCODE = '42501';
    END IF;
    UPDATE public.archie_command_claim_challenges AS challenge
       SET consumed_at = now_at_database
     WHERE challenge.nonce = claim_nonce
       AND challenge.organization_id = command_org_id
       AND challenge.actor_id = command_actor_id
       AND challenge.issued_at_ms = claim_issued_at_ms
       AND challenge.expires_at_ms = claim_expires_at_ms
       AND challenge.consumed_at IS NULL
       AND challenge.issued_at_ms <= floor(
           extract(epoch FROM now_at_database) * 1000
       )::bigint
       AND challenge.expires_at_ms > floor(
           extract(epoch FROM now_at_database) * 1000
       )::bigint
    RETURNING challenge.* INTO claim_challenge;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'command capability is invalid' USING ERRCODE = '42501';
    END IF;
    expires_at := now_at_database + make_interval(
        secs => lease_milliseconds::double precision / 1000.0
    );

    -- A different idempotency key may legitimately name the same immutable
    -- natural operation.  Reconcile it before granting a second live lease so
    -- the domain handler cannot rerun merely because the caller rotated its
    -- transport key.  The new receipt points at the original result while the
    -- result keeps its original receipt/generation provenance.
    SELECT existing_result.* INTO result
      FROM public.operation_results AS existing_result
     WHERE existing_result.organization_id = command_org_id
       AND existing_result.actor_id = command_actor_id
       AND existing_result.operation = command_operation
       AND existing_result.natural_key = command_natural_key
       AND existing_result.request_digest = command_request_digest;
    IF FOUND THEN
        INSERT INTO public.command_idempotency_records (
            organization_id, actor_id, operation, idempotency_key,
            request_digest, natural_key, status, lease_generation, claim_token,
            claimant_request_id, lease_expires_at, operation_result_id,
            attempt_count, completed_at
        ) VALUES (
            command_org_id, command_actor_id, command_operation,
            command_idempotency_key, command_request_digest, command_natural_key,
            'succeeded', 1, new_claim_token, new_claimant_request_id, NULL,
            result.id, 1, now_at_database
        ) ON CONFLICT (organization_id, actor_id, operation, idempotency_key)
          DO NOTHING
        RETURNING id INTO inserted_id;
        IF inserted_id IS NOT NULL THEN
            RETURN QUERY SELECT 'reconciled', inserted_id, 1, NULL::text,
                                result.id::bigint, NULL::text, NULL::text,
                                NULL::double precision;
            RETURN;
        END IF;
    END IF;

    INSERT INTO public.command_idempotency_records (
        organization_id, actor_id, operation, idempotency_key,
        request_digest, natural_key, status, lease_generation, claim_token,
        claimant_request_id, lease_expires_at, attempt_count
    ) VALUES (
        command_org_id, command_actor_id, command_operation,
        command_idempotency_key, command_request_digest, command_natural_key,
        'in_progress', 1, new_claim_token, new_claimant_request_id, expires_at, 1
    ) ON CONFLICT (organization_id, actor_id, operation, idempotency_key)
      DO NOTHING
    RETURNING id INTO inserted_id;
    IF inserted_id IS NOT NULL THEN
        RETURN QUERY SELECT 'claimed', inserted_id, 1, new_claim_token,
                            NULL::bigint, NULL::text, NULL::text, NULL::double precision;
        RETURN;
    END IF;

    SELECT existing.* INTO receipt
      FROM public.command_idempotency_records AS existing
     WHERE existing.organization_id = command_org_id
       AND existing.actor_id = command_actor_id
       AND existing.operation = command_operation
       AND existing.idempotency_key = command_idempotency_key
     FOR UPDATE;
    now_at_database := clock_timestamp();
    expires_at := now_at_database + make_interval(
        secs => lease_milliseconds::double precision / 1000.0
    );
    IF receipt.request_digest IS DISTINCT FROM command_request_digest THEN
        RETURN QUERY SELECT 'conflict', receipt.id::bigint, receipt.lease_generation,
                            NULL::text, NULL::bigint, 'idempotency_digest_mismatch',
                            NULL::text, NULL::double precision;
        RETURN;
    END IF;
    IF receipt.natural_key IS DISTINCT FROM command_natural_key THEN
        RETURN QUERY SELECT 'conflict', receipt.id::bigint, receipt.lease_generation,
                            NULL::text, NULL::bigint, 'idempotency_natural_key_mismatch',
                            NULL::text, NULL::double precision;
        RETURN;
    END IF;

    SELECT existing_result.* INTO result
      FROM public.operation_results AS existing_result
     WHERE existing_result.organization_id = command_org_id
       AND existing_result.actor_id = command_actor_id
       AND existing_result.operation = command_operation
       AND existing_result.natural_key = command_natural_key;
    IF FOUND THEN
        IF result.request_digest IS DISTINCT FROM command_request_digest THEN
            RETURN QUERY SELECT 'conflict', receipt.id::bigint, receipt.lease_generation,
                                NULL::text, result.id::bigint, 'natural_key_digest_mismatch',
                                NULL::text, NULL::double precision;
            RETURN;
        END IF;
        IF receipt.status <> 'succeeded'
           OR receipt.operation_result_id IS DISTINCT FROM result.id THEN
            IF receipt.status = 'failed_non_retryable'
               AND receipt.terminal_reason = contender_reason THEN
                -- A failed natural-key contender can converge only through this
                -- fresh signed claim.  The trigger validates an owner-secret,
                -- transaction-bound fence before accepting the terminal repair.
                SELECT capability.secret
                  INTO reconciliation_secret
                  FROM public.archie_command_capability_keys AS capability
                 WHERE capability.key_id = document->>'key_id'
                   AND capability.active IS TRUE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'command capability is invalid'
                        USING ERRCODE = '42501';
                END IF;
                PERFORM set_config(
                    'archie.receipt_reconcile_key_id', document->>'key_id', TRUE
                );
                PERFORM set_config(
                    'archie.receipt_reconcile_claim_nonce', claim_nonce::text, TRUE
                );
                PERFORM set_config(
                    'archie.receipt_reconcile_fence',
                    encode(
                        public.archie_hmac_sha256(
                            convert_to(
                                jsonb_build_object(
                                    'schema_version',
                                    'transformation-receipt-reconcile-r1',
                                    'transaction_id', txid_current(),
                                    'receipt_id', receipt.id,
                                    'organization_id', receipt.organization_id,
                                    'actor_id', receipt.actor_id,
                                    'operation', receipt.operation,
                                    'idempotency_key', receipt.idempotency_key,
                                    'request_digest', receipt.request_digest,
                                    'natural_key', receipt.natural_key,
                                    'previous_generation', receipt.lease_generation,
                                    'claim_token', new_claim_token,
                                    'claimant_request_id', new_claimant_request_id,
                                    'operation_result_id', result.id,
                                    'claim_nonce', claim_nonce::text,
                                    'claim_issued_at_ms', claim_issued_at_ms,
                                    'claim_expires_at_ms', claim_expires_at_ms,
                                    'contender_reason', contender_reason
                                )::text,
                                'UTF8'
                            ),
                            reconciliation_secret
                        ),
                        'hex'
                    ),
                    TRUE
                );
                UPDATE public.command_idempotency_records
                   SET status = 'succeeded',
                       lease_generation = receipt.lease_generation + 1,
                       claim_token = new_claim_token,
                       claimant_request_id = new_claimant_request_id,
                       lease_expires_at = NULL,
                       operation_result_id = result.id,
                       attempt_count = receipt.attempt_count + 1,
                       last_error_class = NULL,
                       completed_at = now_at_database
                 WHERE id = receipt.id
                RETURNING * INTO receipt;
                PERFORM set_config('archie.receipt_reconcile_fence', '', TRUE);
                PERFORM set_config('archie.receipt_reconcile_key_id', '', TRUE);
                PERFORM set_config(
                    'archie.receipt_reconcile_claim_nonce', '', TRUE
                );
            ELSIF receipt.status = 'failed_non_retryable' THEN
                RETURN QUERY SELECT 'conflict', receipt.id::bigint,
                                    receipt.lease_generation, NULL::text,
                                    result.id::bigint, 'failed_non_retryable',
                                    receipt.last_error_class::text,
                                    NULL::double precision;
                RETURN;
            ELSE
                UPDATE public.command_idempotency_records
                   SET status = 'succeeded', operation_result_id = result.id,
                       lease_expires_at = NULL, completed_at = now_at_database
                 WHERE id = receipt.id
                RETURNING * INTO receipt;
            END IF;
        END IF;
        RETURN QUERY SELECT 'reconciled', receipt.id::bigint,
                            receipt.lease_generation, NULL::text, result.id::bigint,
                            NULL::text, NULL::text, NULL::double precision;
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.operation_results AS existing_result
         WHERE existing_result.organization_id = command_org_id
           AND existing_result.operation = command_operation
           AND existing_result.natural_key = command_natural_key
    ) THEN
        RETURN QUERY SELECT 'conflict', receipt.id::bigint, receipt.lease_generation,
                            NULL::text, NULL::bigint,
                            'natural_key_owned_by_another_actor', NULL::text,
                            NULL::double precision;
        RETURN;
    END IF;
    IF receipt.status = 'failed_non_retryable' THEN
        RETURN QUERY SELECT 'conflict', receipt.id::bigint, receipt.lease_generation,
                            NULL::text, NULL::bigint, 'failed_non_retryable',
                            receipt.last_error_class::text, NULL::double precision;
        RETURN;
    END IF;
    IF receipt.status = 'in_progress'
       AND receipt.lease_expires_at IS NOT NULL
       AND receipt.lease_expires_at > now_at_database THEN
        RETURN QUERY SELECT 'conflict', receipt.id::bigint, receipt.lease_generation,
                            NULL::text, NULL::bigint, 'active_lease', NULL::text,
                            greatest(0.001, extract(epoch FROM
                                (receipt.lease_expires_at - now_at_database)))
                                ::double precision;
        RETURN;
    END IF;

    UPDATE public.command_idempotency_records
       SET status = 'in_progress',
           lease_generation = receipt.lease_generation + 1,
           claim_token = new_claim_token,
           claimant_request_id = new_claimant_request_id,
           lease_expires_at = expires_at,
           operation_result_id = NULL,
           attempt_count = receipt.attempt_count + 1,
           completed_at = NULL
     WHERE id = receipt.id
    RETURNING * INTO receipt;
    RETURN QUERY SELECT 'claimed', receipt.id::bigint, receipt.lease_generation,
                        receipt.claim_token::text, NULL::bigint, NULL::text, NULL::text,
                        NULL::double precision;
END;
$$
"""


_COMMAND_ENVELOPE_INSERT_GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_guard_command_envelope_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF current_setting('archie.signed_envelope_repair', TRUE) = 'on' THEN
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME IN ('command_materialisations', 'operation_results') THEN
        IF NOT EXISTS (
            SELECT 1
              FROM public.command_idempotency_records AS receipt
             WHERE receipt.id = NEW.receipt_id
               AND receipt.organization_id = NEW.organization_id
               AND receipt.actor_id = NEW.actor_id
               AND receipt.operation = NEW.operation
               AND receipt.natural_key = NEW.natural_key
               AND receipt.request_digest = NEW.request_digest
               AND receipt.status = 'in_progress'
               AND receipt.lease_generation = NEW.receipt_generation
               AND receipt.claim_token IS NOT NULL
               AND receipt.lease_expires_at > clock_timestamp()
        ) THEN
            RAISE EXCEPTION 'command envelope insert is outside its live fence'
                USING ERRCODE = '55000';
        END IF;
    ELSIF TG_TABLE_NAME = 'transformation_outbox_events' THEN
        IF NOT EXISTS (
            SELECT 1
              FROM public.operation_results AS result
              JOIN public.command_idempotency_records AS receipt
                ON receipt.id = result.receipt_id
               AND receipt.organization_id = result.organization_id
               AND receipt.actor_id = result.actor_id
               AND receipt.operation = result.operation
               AND receipt.natural_key = result.natural_key
               AND receipt.request_digest = result.request_digest
               AND receipt.lease_generation = result.receipt_generation
             WHERE result.id = NEW.operation_result_id
               AND result.organization_id = NEW.organization_id
               AND receipt.status = 'in_progress'
               AND receipt.claim_token IS NOT NULL
               AND receipt.lease_expires_at > clock_timestamp()
        ) THEN
            RAISE EXCEPTION 'outbox insert is outside its live command fence'
                USING ERRCODE = '55000';
        END IF;
    ELSE
        RAISE EXCEPTION 'unsupported command envelope table'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$
"""


_PERSIST_COMMAND_ENVELOPE_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_persist_command_envelope(
    p_capability_document text,
    p_capability text,
    p_request_document text
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    capability jsonb;
    request jsonb;
    command_org_id bigint;
    command_actor_id bigint;
    command_operation text;
    command_idempotency_key text;
    command_request_digest text;
    command_natural_key text;
    command_receipt_id bigint;
    command_generation integer;
    command_claim_token text;
    command_claimant_request_id text;
    receipt record;
    origin_receipt record;
    materialisation record;
    result record;
    event_document jsonb;
    event_ordinal bigint;
    existing_event record;
    result_id bigint;
    result_receipt_id bigint;
    result_receipt_generation integer;
    recovering_materialisation boolean := FALSE;
BEGIN
    capability := public.archie_verify_command_capability(
        p_capability_document,
        p_capability,
        'transformation-command-execution-r1'
    );
    BEGIN
        command_org_id := (capability->>'organization_id')::bigint;
        command_actor_id := (capability->>'actor_id')::bigint;
        command_operation := capability->>'operation';
        command_idempotency_key := capability->>'idempotency_key';
        command_request_digest := capability->>'request_digest';
        command_natural_key := capability->>'natural_key';
        command_receipt_id := (capability->>'receipt_id')::bigint;
        command_generation := (capability->>'generation')::integer;
        command_claim_token := capability->>'claim_token';
        command_claimant_request_id := capability->>'claimant_request_id';
        request := p_request_document::jsonb;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'command envelope fields are invalid' USING ERRCODE = '42501';
    END;
    IF capability IS DISTINCT FROM jsonb_build_object(
           'schema_version', 'transformation-command-execution-r1',
           'key_id', capability->>'key_id',
           'organization_id', command_org_id,
           'actor_id', command_actor_id,
           'operation', command_operation,
           'idempotency_key', command_idempotency_key,
           'request_digest', command_request_digest,
           'natural_key', command_natural_key,
           'receipt_id', command_receipt_id,
           'generation', command_generation,
           'claim_token', command_claim_token,
           'claimant_request_id', command_claimant_request_id
       )
       OR request IS DISTINCT FROM jsonb_build_object(
           'object_ids', request->'object_ids',
           'response', request->'response',
           'outbox_events', request->'outbox_events'
       )
       OR jsonb_typeof(request->'object_ids') <> 'object'
       OR jsonb_typeof(request->'response') <> 'object'
       OR jsonb_typeof(request->'outbox_events') <> 'array' THEN
        RAISE EXCEPTION 'command envelope fields are invalid' USING ERRCODE = '42501';
    END IF;

    SELECT existing.* INTO receipt
      FROM public.command_idempotency_records AS existing
     WHERE existing.id = command_receipt_id
       AND existing.organization_id = command_org_id
       AND existing.actor_id = command_actor_id
       AND existing.operation = command_operation
       AND existing.idempotency_key = command_idempotency_key
       AND existing.request_digest = command_request_digest
       AND existing.natural_key = command_natural_key
       AND existing.status = 'in_progress'
       AND existing.lease_generation = command_generation
       AND existing.claim_token = command_claim_token
       AND existing.claimant_request_id = command_claimant_request_id
       AND existing.lease_expires_at > clock_timestamp()
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'command envelope live fence is invalid' USING ERRCODE = '40001';
    END IF;
    result_receipt_id := command_receipt_id;
    result_receipt_generation := command_generation;

    SELECT existing.* INTO materialisation
      FROM public.command_materialisations AS existing
     WHERE existing.organization_id = command_org_id
       AND existing.operation = command_operation
       AND existing.natural_key = command_natural_key;
    IF FOUND THEN
        IF materialisation.actor_id IS DISTINCT FROM command_actor_id
           OR materialisation.request_digest IS DISTINCT FROM command_request_digest
           OR materialisation.receipt_generation > command_generation
           OR materialisation.object_ids::jsonb IS DISTINCT FROM request->'object_ids'
           OR materialisation.response_json::jsonb IS DISTINCT FROM request->'response'
           OR materialisation.outbox_events::jsonb IS DISTINCT FROM request->'outbox_events' THEN
            RAISE EXCEPTION 'command materialisation identity mismatch'
                USING ERRCODE = '23505';
        END IF;
        IF materialisation.receipt_id IS DISTINCT FROM command_receipt_id THEN
            RAISE EXCEPTION 'natural_key_contender_materialisation_conflict'
                USING ERRCODE = '23505';
        END IF;
        IF materialisation.receipt_generation IS DISTINCT FROM command_generation THEN
            -- A reclaimed live fence may recover a result deleted after the
            -- immutable domain envelope committed.  The signed execution
            -- capability authorizes the repair, but the materialisation's
            -- original receipt/generation remains the effect's provenance.
            SELECT existing.* INTO origin_receipt
              FROM public.command_idempotency_records AS existing
             WHERE existing.id = materialisation.receipt_id
               AND existing.organization_id = command_org_id
               AND existing.actor_id = command_actor_id
               AND existing.operation = command_operation
               AND existing.request_digest = command_request_digest
               AND existing.natural_key = command_natural_key
               AND existing.lease_generation >= materialisation.receipt_generation;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'command materialisation origin receipt mismatch'
                    USING ERRCODE = '55000';
            END IF;
            result_receipt_id := materialisation.receipt_id;
            result_receipt_generation := materialisation.receipt_generation;
            recovering_materialisation := TRUE;
        END IF;
    ELSE
        INSERT INTO public.command_materialisations (
            organization_id, actor_id, operation, natural_key, request_digest,
            receipt_id, receipt_generation, object_ids, response_json, outbox_events
        ) VALUES (
            command_org_id, command_actor_id, command_operation, command_natural_key,
            command_request_digest, command_receipt_id, command_generation,
            request->'object_ids', request->'response', request->'outbox_events'
        );
    END IF;

    SELECT existing.* INTO result
      FROM public.operation_results AS existing
     WHERE existing.organization_id = command_org_id
       AND existing.operation = command_operation
       AND existing.natural_key = command_natural_key;
    IF FOUND THEN
        IF result.actor_id IS DISTINCT FROM command_actor_id
           OR result.request_digest IS DISTINCT FROM command_request_digest
           OR result.receipt_id IS DISTINCT FROM result_receipt_id
           OR result.receipt_generation IS DISTINCT FROM result_receipt_generation
           OR result.object_ids::jsonb IS DISTINCT FROM request->'object_ids'
           OR result.response_json::jsonb IS DISTINCT FROM request->'response' THEN
            RAISE EXCEPTION 'operation result identity mismatch' USING ERRCODE = '23505';
        END IF;
        result_id := result.id;
    ELSE
        IF recovering_materialisation THEN
            PERFORM set_config('archie.signed_envelope_repair', 'on', TRUE);
        END IF;
        INSERT INTO public.operation_results (
            organization_id, actor_id, operation, natural_key, request_digest,
            receipt_id, receipt_generation, object_ids, response_json
        ) VALUES (
            command_org_id, command_actor_id, command_operation, command_natural_key,
            command_request_digest, result_receipt_id, result_receipt_generation,
            request->'object_ids', request->'response'
        ) RETURNING id INTO result_id;
        IF recovering_materialisation THEN
            PERFORM set_config('archie.signed_envelope_repair', 'off', TRUE);
        END IF;
    END IF;

    FOR event_document, event_ordinal IN
        SELECT item.value, item.ordinality - 1
          FROM jsonb_array_elements(request->'outbox_events')
               WITH ORDINALITY AS item(value, ordinality)
         ORDER BY item.ordinality
    LOOP
        IF event_document IS DISTINCT FROM jsonb_build_object(
               'event_id', event_document->>'event_id',
               'event_type', event_document->>'event_type',
               'payload', event_document->'payload'
           )
           OR coalesce(event_document->>'event_type', '') = ''
           OR jsonb_typeof(event_document->'payload') <> 'object' THEN
            RAISE EXCEPTION 'outbox event document is invalid' USING ERRCODE = '22023';
        END IF;
        BEGIN
            IF (event_document->>'event_id')::uuid::text
               IS DISTINCT FROM event_document->>'event_id' THEN
                RAISE EXCEPTION 'outbox event id is noncanonical';
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'outbox event document is invalid'
                USING ERRCODE = '22023';
        END;
        SELECT existing.* INTO existing_event
          FROM public.transformation_outbox_events AS existing
         WHERE existing.operation_result_id = result_id
           AND existing.ordinal = event_ordinal;
        IF FOUND THEN
            IF existing_event.organization_id IS DISTINCT FROM command_org_id
               OR existing_event.event_id IS DISTINCT FROM event_document->>'event_id'
               OR existing_event.event_type IS DISTINCT FROM event_document->>'event_type'
               OR existing_event.payload_json::jsonb IS DISTINCT FROM event_document->'payload' THEN
                RAISE EXCEPTION 'operation outbox materialisation mismatch'
                    USING ERRCODE = '23505';
            END IF;
        ELSE
            IF recovering_materialisation THEN
                PERFORM set_config('archie.signed_envelope_repair', 'on', TRUE);
            END IF;
            INSERT INTO public.transformation_outbox_events (
                organization_id, operation_result_id, event_id, ordinal,
                event_type, payload_json
            ) VALUES (
                command_org_id, result_id, event_document->>'event_id', event_ordinal,
                event_document->>'event_type', event_document->'payload'
            );
            IF recovering_materialisation THEN
                PERFORM set_config('archie.signed_envelope_repair', 'off', TRUE);
            END IF;
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM public.transformation_outbox_events AS existing
         WHERE existing.operation_result_id = result_id)
       <> jsonb_array_length(request->'outbox_events') THEN
        RAISE EXCEPTION 'operation outbox materialisation mismatch'
            USING ERRCODE = '23505';
    END IF;
    RETURN result_id;
END;
$$
"""


_REPAIR_COMMAND_ENVELOPE_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_repair_command_envelope(
    p_claim_document text,
    p_claim_capability text,
    p_operation_result_id bigint
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    document jsonb;
    command_org_id bigint;
    command_actor_id bigint;
    command_operation text;
    command_idempotency_key text;
    command_request_digest text;
    command_natural_key text;
    command_claim_token text;
    command_claimant_request_id text;
    claim_nonce uuid;
    claim_issued_at_ms bigint;
    claim_expires_at_ms bigint;
    contender_reason text;
    lease_milliseconds integer;
    receipt record;
    origin_receipt record;
    result record;
    materialisation record;
    event_document jsonb;
    event_ordinal bigint;
    existing_event record;
    event_documents jsonb;
    normalized_events jsonb;
    supplied_event_id text;
    materialisation_present boolean := FALSE;
BEGIN
    document := public.archie_verify_command_capability(
        p_claim_document,
        p_claim_capability,
        'transformation-command-claim-r1'
    );
    BEGIN
        command_org_id := (document->>'organization_id')::bigint;
        command_actor_id := (document->>'actor_id')::bigint;
        command_operation := document->>'operation';
        command_idempotency_key := document->>'idempotency_key';
        command_request_digest := document->>'request_digest';
        command_natural_key := document->>'natural_key';
        command_claim_token := document->>'claim_token';
        command_claimant_request_id := document->>'claimant_request_id';
        claim_nonce := (document->>'claim_nonce')::uuid;
        claim_issued_at_ms := (document->>'claim_issued_at_ms')::bigint;
        claim_expires_at_ms := (document->>'claim_expires_at_ms')::bigint;
        contender_reason := document->>'contender_reason';
        lease_milliseconds := (document->>'lease_milliseconds')::integer;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'command repair capability fields are invalid'
            USING ERRCODE = '42501';
    END;
    IF document IS DISTINCT FROM jsonb_build_object(
           'schema_version', 'transformation-command-claim-r1',
           'key_id', document->>'key_id',
           'organization_id', command_org_id,
           'actor_id', command_actor_id,
           'operation', command_operation,
           'idempotency_key', command_idempotency_key,
           'request_digest', command_request_digest,
           'natural_key', command_natural_key,
           'claim_token', command_claim_token,
           'claimant_request_id', command_claimant_request_id,
           'claim_nonce', claim_nonce::text,
           'claim_issued_at_ms', claim_issued_at_ms,
           'claim_expires_at_ms', claim_expires_at_ms,
           'contender_reason', contender_reason,
           'lease_milliseconds', lease_milliseconds
       ) THEN
        RAISE EXCEPTION 'command repair capability fields are invalid'
            USING ERRCODE = '42501';
    END IF;

    SELECT existing.* INTO receipt
      FROM public.command_idempotency_records AS existing
     WHERE existing.organization_id = command_org_id
       AND existing.actor_id = command_actor_id
       AND existing.operation = command_operation
       AND existing.idempotency_key = command_idempotency_key
       AND existing.request_digest = command_request_digest
       AND existing.natural_key = command_natural_key
       AND existing.status = 'succeeded'
       AND existing.operation_result_id = p_operation_result_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'command repair receipt identity mismatch'
            USING ERRCODE = '55000';
    END IF;
    SELECT existing.* INTO result
      FROM public.operation_results AS existing
     WHERE existing.id = p_operation_result_id
       AND existing.organization_id = command_org_id
       AND existing.actor_id = command_actor_id
       AND existing.operation = command_operation
       AND existing.natural_key = command_natural_key
       AND existing.request_digest = command_request_digest;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'operation result repair identity mismatch'
            USING ERRCODE = '55000';
    END IF;
    SELECT existing.* INTO materialisation
      FROM public.command_materialisations AS existing
     WHERE existing.organization_id = command_org_id
       AND existing.operation = command_operation
       AND existing.natural_key = command_natural_key;
    materialisation_present := FOUND;
    IF materialisation_present THEN
        IF materialisation.actor_id IS DISTINCT FROM command_actor_id
           OR materialisation.request_digest IS DISTINCT FROM command_request_digest
           OR materialisation.receipt_id IS DISTINCT FROM result.receipt_id
           OR materialisation.receipt_generation IS DISTINCT FROM result.receipt_generation
           OR materialisation.object_ids::jsonb IS DISTINCT FROM result.object_ids::jsonb
           OR materialisation.response_json::jsonb IS DISTINCT FROM result.response_json::jsonb
           OR jsonb_typeof(materialisation.outbox_events::jsonb) <> 'array' THEN
            RAISE EXCEPTION 'operation result materialisation mismatch'
                USING ERRCODE = '23505';
        END IF;
    END IF;
    SELECT existing.* INTO origin_receipt
      FROM public.command_idempotency_records AS existing
     WHERE existing.id = result.receipt_id
       AND existing.organization_id = command_org_id
       AND existing.actor_id = command_actor_id
       AND existing.operation = command_operation
       AND existing.request_digest = command_request_digest
       AND existing.natural_key = command_natural_key
       AND existing.status = 'succeeded'
       AND (
           (materialisation_present
            AND existing.lease_generation >= result.receipt_generation)
           OR (NOT materialisation_present
               AND existing.lease_generation = result.receipt_generation)
       )
       AND existing.operation_result_id = result.id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'operation result origin receipt mismatch'
            USING ERRCODE = '55000';
    END IF;

    IF NOT materialisation_present THEN
        SELECT coalesce(
                   jsonb_agg(
                       jsonb_build_object(
                           'event_id', existing.event_id,
                           'event_type', existing.event_type,
                           'payload', existing.payload_json::jsonb
                       ) ORDER BY existing.ordinal
                   ),
                   '[]'::jsonb
               )
          INTO event_documents
          FROM public.transformation_outbox_events AS existing
         WHERE existing.organization_id = command_org_id
           AND existing.operation_result_id = result.id;
        IF EXISTS (
            SELECT 1
              FROM public.transformation_outbox_events AS existing
             WHERE existing.organization_id = command_org_id
               AND existing.operation_result_id = result.id
               AND existing.ordinal <> (
                   SELECT count(*)
                     FROM public.transformation_outbox_events AS preceding
                    WHERE preceding.organization_id = command_org_id
                      AND preceding.operation_result_id = result.id
                      AND preceding.ordinal < existing.ordinal
               )
        ) THEN
            RAISE EXCEPTION 'operation outbox materialisation mismatch'
                USING ERRCODE = '23505';
        END IF;
        PERFORM set_config('archie.signed_envelope_repair', 'on', TRUE);
        INSERT INTO public.command_materialisations (
            organization_id, actor_id, operation, natural_key, request_digest,
            receipt_id, receipt_generation, object_ids, response_json, outbox_events
        ) VALUES (
            command_org_id, command_actor_id, command_operation, command_natural_key,
            command_request_digest, result.receipt_id, result.receipt_generation,
            result.object_ids, result.response_json, event_documents
        );
        PERFORM set_config('archie.signed_envelope_repair', 'off', TRUE);
    ELSE
        event_documents := materialisation.outbox_events::jsonb;
    END IF;

    normalized_events := '[]'::jsonb;
    FOR event_document, event_ordinal IN
        SELECT item.value, item.ordinality - 1
          FROM jsonb_array_elements(event_documents)
               WITH ORDINALITY AS item(value, ordinality)
         ORDER BY item.ordinality
    LOOP
        IF event_document - 'event_id' IS DISTINCT FROM jsonb_build_object(
               'event_type', event_document->'event_type',
               'payload', event_document->'payload'
           )
           OR coalesce(event_document->>'event_type', '') = ''
           OR jsonb_typeof(event_document->'payload') <> 'object' THEN
            RAISE EXCEPTION 'outbox event document is invalid'
                USING ERRCODE = '22023';
        END IF;
        supplied_event_id := event_document->>'event_id';
        SELECT existing.* INTO existing_event
          FROM public.transformation_outbox_events AS existing
         WHERE existing.operation_result_id = result.id
           AND existing.ordinal = event_ordinal;
        IF supplied_event_id IS NULL THEN
            IF NOT FOUND
               OR existing_event.organization_id IS DISTINCT FROM command_org_id
               OR existing_event.event_type IS DISTINCT FROM event_document->>'event_type'
               OR existing_event.payload_json::jsonb IS DISTINCT FROM event_document->'payload' THEN
                RAISE EXCEPTION 'operation outbox materialisation mismatch'
                    USING ERRCODE = '23505';
            END IF;
            supplied_event_id := existing_event.event_id;
        END IF;
        BEGIN
            IF supplied_event_id::uuid::text IS DISTINCT FROM supplied_event_id THEN
                RAISE EXCEPTION 'outbox event id is noncanonical';
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'outbox event document is invalid'
                USING ERRCODE = '22023';
        END;
        normalized_events := normalized_events || jsonb_build_array(
            jsonb_build_object(
                'event_id', supplied_event_id,
                'event_type', event_document->>'event_type',
                'payload', event_document->'payload'
            )
        );
        IF FOUND THEN
            IF existing_event.organization_id IS DISTINCT FROM command_org_id
               OR existing_event.event_id IS DISTINCT FROM supplied_event_id
               OR existing_event.event_type IS DISTINCT FROM event_document->>'event_type'
               OR existing_event.payload_json::jsonb IS DISTINCT FROM event_document->'payload' THEN
                RAISE EXCEPTION 'operation outbox materialisation mismatch'
                    USING ERRCODE = '23505';
            END IF;
        ELSE
            PERFORM set_config('archie.signed_envelope_repair', 'on', TRUE);
            INSERT INTO public.transformation_outbox_events (
                organization_id, operation_result_id, event_id, ordinal,
                event_type, payload_json
            ) VALUES (
                command_org_id, result.id, supplied_event_id, event_ordinal,
                event_document->>'event_type', event_document->'payload'
            );
            PERFORM set_config('archie.signed_envelope_repair', 'off', TRUE);
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM public.transformation_outbox_events AS existing
         WHERE existing.operation_result_id = result.id)
       <> jsonb_array_length(event_documents) THEN
        RAISE EXCEPTION 'operation outbox materialisation mismatch'
            USING ERRCODE = '23505';
    END IF;
    IF normalized_events IS DISTINCT FROM event_documents THEN
        PERFORM set_config('archie.signed_envelope_repair', 'on', TRUE);
        UPDATE public.command_materialisations
           SET outbox_events = normalized_events
         WHERE id = materialisation.id;
        PERFORM set_config('archie.signed_envelope_repair', 'off', TRUE);
    END IF;
    RETURN result.id;
END;
$$
"""


_OVERLAP_DISPOSITION_INSERT_GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_guard_overlap_disposition_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    candidate record;
    signal record;
BEGIN
    SELECT existing.* INTO candidate
      FROM public.transformation_candidates AS existing
     WHERE existing.id = NEW.candidate_id
       AND existing.organization_id = NEW.organization_id
       AND existing.subject_type = 'application';
    IF NOT FOUND
       OR NOT EXISTS (
           SELECT 1
             FROM public.application_components AS application
            WHERE application.id = candidate.subject_id
              AND application.organization_id = NEW.organization_id
              AND application.deleted_at IS NULL
       )
       OR NOT EXISTS (
           SELECT 1
             FROM public.users AS decider
            WHERE decider.id = NEW.decided_by_id
              AND decider.organization_id = NEW.organization_id
       )
       OR NOT EXISTS (
           SELECT 1
             FROM public.command_idempotency_records AS receipt
            WHERE receipt.id = NEW.command_receipt_id
              AND receipt.organization_id = NEW.organization_id
              AND receipt.actor_id = NEW.decided_by_id
              AND receipt.operation = 'candidate.accept'
              AND receipt.natural_key =
                  'candidate:' || candidate.workstream_id::text ||
                  ':application:' || candidate.subject_id::text
              AND receipt.status = 'in_progress'
              AND receipt.lease_generation = NEW.command_generation
              AND receipt.claim_token IS NOT NULL
              AND receipt.lease_expires_at > clock_timestamp()
       ) THEN
        RAISE EXCEPTION 'overlap disposition is outside its live command fence'
            USING ERRCODE = '55000';
    END IF;

    SELECT existing.* INTO signal
      FROM public.candidate_signals AS existing
     WHERE existing.organization_id = NEW.organization_id
       AND existing.candidate_id = NEW.candidate_id
       AND existing.rule_code = 'capability_overlap'
       AND existing.content_hash = NEW.signal_digest;
    IF NOT FOUND
       OR jsonb_typeof(NEW.overlapping_application_ids::jsonb) <> 'array'
       OR jsonb_array_length(NEW.overlapping_application_ids::jsonb) = 0
       OR signal.payload_json::jsonb->'observed_values'->'overlapping_application_ids'
          IS DISTINCT FROM NEW.overlapping_application_ids::jsonb
       OR EXISTS (
           SELECT 1
             FROM jsonb_array_elements_text(
                      NEW.overlapping_application_ids::jsonb
                  ) AS overlap(application_id)
            WHERE overlap.application_id !~ '^[1-9][0-9]*$'
               OR NOT EXISTS (
                   SELECT 1
                     FROM public.application_components AS application
                    WHERE application.id = overlap.application_id::bigint
                      AND application.organization_id = NEW.organization_id
                      AND application.deleted_at IS NULL
               )
       )
       OR (
           NEW.target_application_id IS NOT NULL
           AND (
               NOT (NEW.overlapping_application_ids::jsonb @>
                    jsonb_build_array(NEW.target_application_id))
               OR NOT EXISTS (
                   SELECT 1
                     FROM public.application_components AS target
                    WHERE target.id = NEW.target_application_id
                      AND target.organization_id = NEW.organization_id
                      AND target.deleted_at IS NULL
               )
           )
       ) THEN
        RAISE EXCEPTION 'overlap disposition candidate or signal binding is invalid'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$
"""


_PERSIST_OVERLAP_DISPOSITION_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_persist_overlap_disposition(
    p_capability_document text,
    p_capability text,
    p_request_document text
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    capability jsonb;
    request jsonb;
    command_org_id bigint;
    command_actor_id bigint;
    command_operation text;
    command_idempotency_key text;
    command_request_digest text;
    command_natural_key text;
    command_receipt_id bigint;
    command_generation integer;
    command_claim_token text;
    command_claimant_request_id text;
    requested_candidate_id bigint;
    requested_target_application_id bigint;
    decided_at timestamptz;
    existing_disposition record;
    disposition_id bigint;
BEGIN
    capability := public.archie_verify_command_capability(
        p_capability_document,
        p_capability,
        'transformation-command-execution-r1'
    );
    BEGIN
        command_org_id := (capability->>'organization_id')::bigint;
        command_actor_id := (capability->>'actor_id')::bigint;
        command_operation := capability->>'operation';
        command_idempotency_key := capability->>'idempotency_key';
        command_request_digest := capability->>'request_digest';
        command_natural_key := capability->>'natural_key';
        command_receipt_id := (capability->>'receipt_id')::bigint;
        command_generation := (capability->>'generation')::integer;
        command_claim_token := capability->>'claim_token';
        command_claimant_request_id := capability->>'claimant_request_id';
        request := p_request_document::jsonb;
        requested_candidate_id := (request->>'candidate_id')::bigint;
        requested_target_application_id := CASE
            WHEN request->'target_application_id' = 'null'::jsonb THEN NULL
            ELSE (request->>'target_application_id')::bigint
        END;
        decided_at := (request->>'decided_at')::timestamptz;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'overlap disposition fields are invalid'
            USING ERRCODE = '42501';
    END;
    IF capability IS DISTINCT FROM jsonb_build_object(
           'schema_version', 'transformation-command-execution-r1',
           'key_id', capability->>'key_id',
           'organization_id', command_org_id,
           'actor_id', command_actor_id,
           'operation', command_operation,
           'idempotency_key', command_idempotency_key,
           'request_digest', command_request_digest,
           'natural_key', command_natural_key,
           'receipt_id', command_receipt_id,
           'generation', command_generation,
           'claim_token', command_claim_token,
           'claimant_request_id', command_claimant_request_id
       )
       OR request IS DISTINCT FROM jsonb_build_object(
           'candidate_id', requested_candidate_id,
           'signal_digest', request->>'signal_digest',
           'decision', request->>'decision',
           'overlapping_application_ids', request->'overlapping_application_ids',
           'rationale', request->>'rationale',
           'target_application_id', requested_target_application_id,
           'decided_at', request->>'decided_at'
       )
       OR command_operation <> 'candidate.accept'
       OR requested_candidate_id <= 0
       OR length(request->>'signal_digest') <> 64
       OR request->>'decision' NOT IN (
           'confirmed_duplicate', 'justified_distinct', 'merge_repoint'
       )
       OR length(btrim(request->>'rationale')) = 0
       OR jsonb_typeof(request->'overlapping_application_ids') <> 'array'
       OR jsonb_array_length(request->'overlapping_application_ids') = 0
       OR decided_at > clock_timestamp()
       OR (
           request->>'decision' IN ('confirmed_duplicate', 'merge_repoint')
           AND requested_target_application_id IS NULL
       ) THEN
        RAISE EXCEPTION 'overlap disposition fields are invalid'
            USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM public.command_idempotency_records AS receipt
         WHERE receipt.id = command_receipt_id
           AND receipt.organization_id = command_org_id
           AND receipt.actor_id = command_actor_id
           AND receipt.operation = command_operation
           AND receipt.idempotency_key = command_idempotency_key
           AND receipt.request_digest = command_request_digest
           AND receipt.natural_key = command_natural_key
           AND receipt.status = 'in_progress'
           AND receipt.lease_generation = command_generation
           AND receipt.claim_token = command_claim_token
           AND receipt.claimant_request_id = command_claimant_request_id
           AND receipt.lease_expires_at > clock_timestamp()
         FOR UPDATE
    ) THEN
        RAISE EXCEPTION 'overlap disposition live fence is invalid'
            USING ERRCODE = '40001';
    END IF;

    SELECT existing.* INTO existing_disposition
     FROM public.candidate_overlap_dispositions AS existing
     WHERE existing.organization_id = command_org_id
       AND existing.candidate_id = requested_candidate_id;
    IF FOUND THEN
        IF existing_disposition.signal_digest IS DISTINCT FROM request->>'signal_digest'
           OR existing_disposition.decision IS DISTINCT FROM request->>'decision'
           OR existing_disposition.overlapping_application_ids::jsonb
              IS DISTINCT FROM request->'overlapping_application_ids'
           OR existing_disposition.rationale IS DISTINCT FROM request->>'rationale'
           OR existing_disposition.target_application_id
              IS DISTINCT FROM requested_target_application_id
           OR existing_disposition.decided_by_id IS DISTINCT FROM command_actor_id
           OR existing_disposition.command_receipt_id IS DISTINCT FROM command_receipt_id
           OR existing_disposition.command_generation IS DISTINCT FROM command_generation
           OR existing_disposition.decided_at IS DISTINCT FROM decided_at THEN
            RAISE EXCEPTION 'overlap disposition materialisation mismatch'
                USING ERRCODE = '23505';
        END IF;
        RETURN existing_disposition.id;
    END IF;
    INSERT INTO public.candidate_overlap_dispositions (
        organization_id, candidate_id, signal_digest, decision,
        overlapping_application_ids, rationale, target_application_id,
        decided_by_id, command_receipt_id, command_generation, decided_at
    ) VALUES (
        command_org_id, requested_candidate_id, request->>'signal_digest', request->>'decision',
        request->'overlapping_application_ids', request->>'rationale',
        requested_target_application_id, command_actor_id, command_receipt_id,
        command_generation, decided_at
    ) RETURNING id INTO disposition_id;
    RETURN disposition_id;
END;
$$
"""


_IMMUTABILITY_FUNCTION_SQL = r"""
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
           AND NEW.operation_result_id = OLD.operation_result_id
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
"""


_DECISION_CITATION_MEMBERSHIP_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_guard_decision_citation_membership()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    parent_organization_id bigint;
    parent_brief_id bigint;
    parent_source_revision integer;
    parent_created_by_id bigint;
    frozen_membership jsonb;
BEGIN
    SELECT version.organization_id,
           version.brief_id,
           version.source_revision,
           version.created_by_id,
           CASE
               WHEN TG_TABLE_NAME = 'decision_brief_option_citations'
                   THEN version.option_version_ids::jsonb
               ELSE version.cited_evidence_ids::jsonb
           END
      INTO parent_organization_id, parent_brief_id, parent_source_revision,
           parent_created_by_id, frozen_membership
      FROM public.decision_brief_versions AS version
     WHERE version.id = NEW.brief_version_id
     FOR KEY SHARE;

    IF NOT FOUND
       OR parent_organization_id IS DISTINCT FROM NEW.organization_id
       OR NOT EXISTS (
           SELECT 1
             FROM public.decision_briefs AS brief
             JOIN public.command_idempotency_records AS receipt
               ON receipt.organization_id = brief.organization_id
              AND receipt.actor_id = parent_created_by_id
              AND receipt.operation = 'brief.freeze'
              AND receipt.natural_key =
                  'brief:' || brief.id::text || ':version:' ||
                  parent_source_revision::text
              AND receipt.status = 'in_progress'
              AND receipt.lease_generation > 0
              AND receipt.claim_token IS NOT NULL
              AND receipt.lease_expires_at > clock_timestamp()
            WHERE brief.id = parent_brief_id
              AND brief.organization_id = parent_organization_id
              AND brief.status = 'draft'
              AND brief.revision = parent_source_revision
       ) THEN
        RAISE EXCEPTION 'decision brief citation membership is frozen'
            USING ERRCODE = '55000';
    END IF;

    IF TG_TABLE_NAME = 'decision_brief_option_citations' THEN
        IF NOT frozen_membership @> jsonb_build_array(NEW.option_version_id) THEN
            RAISE EXCEPTION 'decision brief citation membership is frozen'
                USING ERRCODE = '55000';
        END IF;
    ELSIF TG_TABLE_NAME = 'decision_brief_evidence_citations' THEN
        IF NOT frozen_membership @> jsonb_build_array(NEW.evidence_record_id) THEN
            RAISE EXCEPTION 'decision brief citation membership is frozen'
                USING ERRCODE = '55000';
        END IF;
    ELSE
        RAISE EXCEPTION 'unsupported decision brief citation table'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$
"""


_DECISION_BRIEF_CREATE_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_create_decision_brief(
    p_capability_document text,
    p_capability text,
    p_request_document text
)
RETURNS TABLE (
    decision_brief_id bigint,
    decision_brief_revision integer,
    decision_brief_created boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict use_variable
DECLARE
    capability_document jsonb;
    request_payload jsonb;
    command_organization_id bigint;
    command_actor_id bigint;
    command_receipt_id bigint;
    command_generation integer;
    command_claim_token text;
    command_natural_key text;
    command_request_digest text;
    workstream_id bigint;
    candidate_id bigint;
    recommendation_option_id bigint;
    decision_authority_id bigint;
    workstream_programme_id bigint;
    exception_payload jsonb;
    exception_authority_id bigint;
    existing record;
    inserted_id bigint;
BEGIN
    capability_document := public.archie_verify_command_capability(
        p_capability_document,
        p_capability,
        'transformation-command-execution-r1'
    );
    BEGIN
        request_payload := p_request_document::jsonb;
        command_organization_id :=
            (capability_document->>'organization_id')::bigint;
        command_actor_id := (capability_document->>'actor_id')::bigint;
        command_receipt_id := (capability_document->>'receipt_id')::bigint;
        command_generation := (capability_document->>'generation')::integer;
        command_claim_token := capability_document->>'claim_token';
        command_natural_key := capability_document->>'natural_key';
        command_request_digest := capability_document->>'request_digest';
        workstream_id := (request_payload->>'workstream_id')::bigint;
        candidate_id := NULLIF(request_payload->>'candidate_id', '')::bigint;
        recommendation_option_id :=
            (request_payload->>'recommendation_option_id')::bigint;
        decision_authority_id :=
            (request_payload->>'decision_authority_id')::bigint;
        exception_payload := request_payload->'option_exception';
        exception_authority_id := NULLIF(
            exception_payload->>'authority_id', ''
        )::bigint;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'decision brief create document is invalid'
            USING ERRCODE = '42501';
    END;

    IF jsonb_typeof(request_payload) <> 'object'
       OR request_payload IS DISTINCT FROM jsonb_build_object(
           'workstream_id', workstream_id,
           'candidate_id', candidate_id,
           'title', request_payload->>'title',
           'recommendation_option_id', recommendation_option_id,
           'decision_authority_id', decision_authority_id,
           'unknown_codes', request_payload->'unknown_codes',
           'conflicts', request_payload->'conflicts',
           'expected_impacts', request_payload->'expected_impacts',
           'option_exception', exception_payload
       )
       OR workstream_id <= 0
       OR (candidate_id IS NOT NULL AND candidate_id <= 0)
       OR recommendation_option_id <= 0 OR decision_authority_id <= 0
       OR NULLIF(btrim(request_payload->>'title'), '') IS NULL
       OR length(request_payload->>'title') > 255
       OR jsonb_typeof(request_payload->'unknown_codes') <> 'array'
       OR jsonb_typeof(request_payload->'conflicts') <> 'array'
       OR jsonb_typeof(request_payload->'expected_impacts') <> 'array'
       OR (
           exception_payload <> 'null'::jsonb
           AND (
               jsonb_typeof(exception_payload) <> 'object'
               OR exception_payload IS DISTINCT FROM jsonb_build_object(
                   'type', exception_payload->>'type',
                   'name', exception_payload->>'name',
                   'reason', exception_payload->>'reason',
                   'authority_id', exception_authority_id
               )
               OR exception_payload->>'type' NOT IN ('policy', 'legal')
               OR NULLIF(btrim(exception_payload->>'name'), '') IS NULL
               OR NULLIF(btrim(exception_payload->>'reason'), '') IS NULL
               OR exception_authority_id IS NULL OR exception_authority_id <= 0
           )
       ) THEN
        RAISE EXCEPTION 'decision brief create fields are invalid'
            USING ERRCODE = '42501';
    END IF;

    IF capability_document IS DISTINCT FROM jsonb_build_object(
        'schema_version', 'transformation-command-execution-r1',
        'key_id', capability_document->>'key_id',
        'organization_id', command_organization_id,
        'actor_id', command_actor_id,
        'operation', 'brief.create',
        'idempotency_key', capability_document->>'idempotency_key',
        'request_digest', command_request_digest,
        'natural_key',
            'brief:workstream:' || workstream_id::text ||
            ':candidate:' || COALESCE(candidate_id::text, 'all'),
        'receipt_id', command_receipt_id,
        'generation', command_generation,
        'claim_token', command_claim_token,
        'claimant_request_id', capability_document->>'claimant_request_id'
    ) THEN
        RAISE EXCEPTION 'decision brief create capability is invalid'
            USING ERRCODE = '42501';
    END IF;

    PERFORM 1
      FROM public.command_idempotency_records AS receipt
     WHERE receipt.id = command_receipt_id
       AND receipt.organization_id = command_organization_id
       AND receipt.actor_id = command_actor_id
       AND receipt.operation = 'brief.create'
       AND receipt.idempotency_key = capability_document->>'idempotency_key'
       AND receipt.request_digest = command_request_digest
       AND receipt.natural_key = command_natural_key
       AND receipt.status = 'in_progress'
       AND receipt.lease_generation = command_generation
       AND receipt.claim_token = command_claim_token
       AND receipt.claimant_request_id =
           capability_document->>'claimant_request_id'
       AND receipt.lease_expires_at > clock_timestamp()
       AND receipt.operation_result_id IS NULL
     FOR UPDATE;
    IF NOT FOUND OR command_request_digest IS DISTINCT FROM encode(
        sha256(convert_to(p_request_document, 'UTF8')), 'hex'
    ) THEN
        RAISE EXCEPTION 'decision brief create command fence is invalid'
            USING ERRCODE = '55000';
    END IF;

    SELECT workstream.programme_id
      INTO workstream_programme_id
      FROM public.programme_workstreams AS workstream
      JOIN public.strategic_initiatives AS programme
        ON programme.id = workstream.programme_id
       AND programme.organization_id = workstream.organization_id
       AND programme.record_kind = 'transformation_programme'
       AND programme.status <> 'archived'
       AND programme.archived_at IS NULL
     WHERE workstream.id = workstream_id
       AND workstream.organization_id = command_organization_id
     FOR UPDATE OF programme, workstream;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'decision brief workstream is outside active tenant scope'
            USING ERRCODE = '55000';
    END IF;

    PERFORM 1
      FROM public.users AS governed_user
     WHERE governed_user.organization_id = command_organization_id
       AND governed_user.id IN (
           command_actor_id, decision_authority_id,
           COALESCE(exception_authority_id, decision_authority_id)
       )
     ORDER BY governed_user.id
     FOR UPDATE;
    PERFORM 1
      FROM public.programme_role_assignments AS assignment
     WHERE assignment.organization_id = command_organization_id
       AND assignment.programme_id = workstream_programme_id
       AND assignment.user_id IN (
           command_actor_id, decision_authority_id,
           COALESCE(exception_authority_id, decision_authority_id)
       )
       AND (assignment.workstream_id IS NULL OR
            assignment.workstream_id = workstream_id)
     ORDER BY assignment.id
     FOR UPDATE;

    IF NOT EXISTS (
        SELECT 1
          FROM public.users AS actor
         WHERE actor.id = command_actor_id
           AND actor.organization_id = command_organization_id
           AND (
               actor.enterprise_role IN (
                   'enterprise_architect', 'chief_architect', 'cto',
                   'platform_admin', 'organization_admin', 'administrator'
               )
               OR actor.is_org_admin IS TRUE OR actor.is_platform_admin IS TRUE
               OR EXISTS (
                   SELECT 1
                     FROM public.programme_role_assignments AS assignment
                    WHERE assignment.organization_id = command_organization_id
                      AND assignment.programme_id = workstream_programme_id
                      AND assignment.user_id = command_actor_id
                      AND (assignment.workstream_id IS NULL OR
                           assignment.workstream_id = workstream_id)
                      AND assignment.role IN ('programme_owner', 'workstream_lead')
                      AND assignment.effective_from <= CURRENT_DATE
                      AND (assignment.effective_to IS NULL OR
                           assignment.effective_to >= CURRENT_DATE)
               )
           )
    ) THEN
        RAISE EXCEPTION 'decision brief actor is not currently authorized'
            USING ERRCODE = '42501';
    END IF;

    IF candidate_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
          FROM public.transformation_candidates AS candidate
         WHERE candidate.id = candidate_id
           AND candidate.organization_id = command_organization_id
           AND candidate.workstream_id = workstream_id
           AND candidate.inclusion_status = 'accepted'
         FOR UPDATE
    ) THEN
        RAISE EXCEPTION 'decision brief candidate is outside governed scope'
            USING ERRCODE = '55000';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM public.transformation_options AS recommendation
         WHERE recommendation.id = recommendation_option_id
           AND recommendation.organization_id = command_organization_id
           AND recommendation.workstream_id = workstream_id
           AND recommendation.candidate_id IS NOT DISTINCT FROM candidate_id
         FOR UPDATE
    ) THEN
        RAISE EXCEPTION 'decision brief recommendation is outside governed scope'
            USING ERRCODE = '55000';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM public.users AS authority
         WHERE authority.id = decision_authority_id
           AND authority.organization_id = command_organization_id
           AND (
               authority.enterprise_role IN (
                   'chief_architect', 'cto', 'enterprise_architect',
                   'platform_admin', 'organization_admin', 'administrator'
               )
               OR authority.is_org_admin IS TRUE
               OR authority.is_platform_admin IS TRUE
               OR EXISTS (
                   SELECT 1
                     FROM public.programme_role_assignments AS assignment
                    WHERE assignment.organization_id = command_organization_id
                      AND assignment.programme_id = workstream_programme_id
                      AND assignment.user_id = decision_authority_id
                      AND (assignment.workstream_id IS NULL OR
                           assignment.workstream_id = workstream_id)
                      AND assignment.role = 'decision_authority'
                      AND assignment.effective_from <= CURRENT_DATE
                      AND (assignment.effective_to IS NULL OR
                           assignment.effective_to >= CURRENT_DATE)
               )
           )
    ) THEN
        RAISE EXCEPTION 'decision brief decision authority is not current'
            USING ERRCODE = '42501';
    END IF;
    IF exception_payload <> 'null'::jsonb AND NOT EXISTS (
        SELECT 1
          FROM public.users AS authority
         WHERE authority.id = exception_authority_id
           AND authority.organization_id = command_organization_id
           AND (
               authority.enterprise_role IN (
                   'chief_architect', 'cto', 'enterprise_architect',
                   'platform_admin', 'organization_admin', 'administrator'
               )
               OR authority.is_org_admin IS TRUE
               OR authority.is_platform_admin IS TRUE
               OR EXISTS (
                   SELECT 1
                     FROM public.programme_role_assignments AS assignment
                    WHERE assignment.organization_id = command_organization_id
                      AND assignment.programme_id = workstream_programme_id
                      AND assignment.user_id = exception_authority_id
                      AND (assignment.workstream_id IS NULL OR
                           assignment.workstream_id = workstream_id)
                      AND assignment.role = 'decision_authority'
                      AND assignment.effective_from <= CURRENT_DATE
                      AND (assignment.effective_to IS NULL OR
                           assignment.effective_to >= CURRENT_DATE)
               )
           )
    ) THEN
        RAISE EXCEPTION 'decision brief exception authority is not current'
            USING ERRCODE = '42501';
    END IF;

    SELECT brief.* INTO existing
      FROM public.decision_briefs AS brief
     WHERE brief.organization_id = command_organization_id
       AND brief.workstream_id = workstream_id
       AND brief.candidate_id IS NOT DISTINCT FROM candidate_id
     FOR UPDATE;
    IF FOUND THEN
        IF existing.status <> 'draft'
           OR existing.title IS DISTINCT FROM request_payload->>'title'
           OR existing.recommendation_option_id IS DISTINCT FROM
              recommendation_option_id
           OR existing.decision_authority_id IS DISTINCT FROM
              decision_authority_id
           OR existing.unknown_codes::jsonb IS DISTINCT FROM
              request_payload->'unknown_codes'
           OR existing.conflicts::jsonb IS DISTINCT FROM
              request_payload->'conflicts'
           OR existing.expected_impacts::jsonb IS DISTINCT FROM
              request_payload->'expected_impacts'
           OR existing.option_exception_type IS DISTINCT FROM
              exception_payload->>'type'
           OR existing.option_exception_name IS DISTINCT FROM
              exception_payload->>'name'
           OR existing.option_exception_reason IS DISTINCT FROM
              exception_payload->>'reason'
           OR existing.option_exception_authority_id IS DISTINCT FROM
              exception_authority_id THEN
            RAISE EXCEPTION 'decision brief scope already has a different case'
                USING ERRCODE = '55000';
        END IF;
        decision_brief_id := existing.id;
        decision_brief_revision := existing.revision;
        decision_brief_created := FALSE;
        RETURN NEXT;
        RETURN;
    END IF;

    INSERT INTO public.decision_briefs (
        organization_id, workstream_id, candidate_id, title,
        recommendation_option_id, decision_authority_id,
        unknown_codes, conflicts, expected_impacts,
        option_exception_type, option_exception_name,
        option_exception_reason, option_exception_authority_id,
        status, revision
    ) VALUES (
        command_organization_id, workstream_id, candidate_id,
        request_payload->>'title', recommendation_option_id,
        decision_authority_id, request_payload->'unknown_codes',
        request_payload->'conflicts', request_payload->'expected_impacts',
        exception_payload->>'type', exception_payload->>'name',
        exception_payload->>'reason', exception_authority_id,
        'draft', 1
    ) RETURNING id INTO inserted_id;
    decision_brief_id := inserted_id;
    decision_brief_revision := 1;
    decision_brief_created := TRUE;
    RETURN NEXT;
END;
$$
"""


_DECISION_BRIEF_FREEZE_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_freeze_decision_brief_version(
    p_brief_id bigint,
    p_actor_id bigint,
    p_receipt_id bigint,
    p_generation integer,
    p_claim_token text,
    p_capability_document text,
    p_capability text,
    p_expected_revision integer,
    p_request_document text,
    p_frozen_payload jsonb,
    p_canonical_document text
)
RETURNS TABLE (
    decision_brief_version_id bigint,
    decision_brief_version_number integer,
    decision_brief_content_hash text,
    decision_brief_created_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    brief_organization_id bigint;
    brief_workstream_id bigint;
    brief_candidate_id bigint;
    brief_recommendation_option_id bigint;
    brief_decision_authority_id bigint;
    brief_title text;
    brief_unknowns jsonb;
    brief_conflicts jsonb;
    brief_expected_impacts jsonb;
    brief_exception_type text;
    brief_exception_name text;
    brief_exception_reason text;
    brief_exception_authority_id bigint;
    workstream_programme_id bigint;
    workstream_objective text;
    workstream_scope jsonb;
    receipt_digest text;
    receipt_idempotency_key text;
    receipt_natural_key text;
    receipt_claimant_request_id text;
    receipt_created_at timestamptz;
    capability_document jsonb;
    computed_request_digest text;
    payload_option_ids bigint[];
    latest_option_ids bigint[];
    payload_evidence_ids bigint[];
    current_evidence_ids bigint[];
    payload_outcome_ids bigint[];
    current_outcome_ids bigint[];
    payload_measure_ids bigint[];
    current_measure_ids bigint[];
    acknowledged_unknowns text[];
    required_unknowns text[];
    root_count integer;
    next_version integer;
    frozen_at timestamptz;
    request_payload jsonb;
    hash_envelope jsonb;
    computed_hash text;
    inserted_version_id bigint;
    affected integer;
BEGIN
    SELECT brief.organization_id, brief.workstream_id
      INTO brief_organization_id, brief_workstream_id
      FROM public.decision_briefs AS brief
     WHERE brief.id = p_brief_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'decision brief draft does not exist'
            USING ERRCODE = '55000';
    END IF;

    SELECT workstream.programme_id, workstream.objective,
           workstream.scope_expression::jsonb
      INTO workstream_programme_id, workstream_objective, workstream_scope
      FROM public.programme_workstreams AS workstream
      JOIN public.strategic_initiatives AS programme
       ON programme.id = workstream.programme_id
       AND programme.organization_id = workstream.organization_id
       AND programme.record_kind = 'transformation_programme'
       AND programme.status <> 'archived'
       AND programme.archived_at IS NULL
     WHERE workstream.id = brief_workstream_id
       AND workstream.organization_id = brief_organization_id
     FOR UPDATE OF workstream, programme;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'decision brief programme scope is not active'
            USING ERRCODE = '55000';
    END IF;

    SELECT brief.organization_id, brief.workstream_id, brief.candidate_id,
           brief.recommendation_option_id, brief.decision_authority_id,
           brief.title, brief.unknown_codes::jsonb, brief.conflicts::jsonb,
           brief.expected_impacts::jsonb, brief.option_exception_type,
           brief.option_exception_name, brief.option_exception_reason,
           brief.option_exception_authority_id
      INTO brief_organization_id, brief_workstream_id, brief_candidate_id,
           brief_recommendation_option_id, brief_decision_authority_id,
           brief_title, brief_unknowns, brief_conflicts, brief_expected_impacts,
           brief_exception_type, brief_exception_name, brief_exception_reason,
           brief_exception_authority_id
      FROM public.decision_briefs AS brief
     WHERE brief.id = p_brief_id
       AND brief.organization_id = brief_organization_id
       AND brief.workstream_id = brief_workstream_id
       AND brief.status = 'draft'
       AND brief.revision = p_expected_revision
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'decision brief draft revision is not current'
            USING ERRCODE = '55000';
    END IF;

    SELECT receipt.request_digest, receipt.idempotency_key,
           receipt.natural_key, receipt.claimant_request_id, receipt.created_at
      INTO receipt_digest, receipt_idempotency_key, receipt_natural_key,
           receipt_claimant_request_id, receipt_created_at
      FROM public.command_idempotency_records AS receipt
     WHERE receipt.id = p_receipt_id
       AND receipt.organization_id = brief_organization_id
       AND receipt.actor_id = p_actor_id
       AND receipt.operation = 'brief.freeze'
       AND receipt.natural_key =
           'brief:' || p_brief_id::text || ':version:' || p_expected_revision::text
       AND receipt.status = 'in_progress'
       AND receipt.lease_generation = p_generation
       AND receipt.claim_token = p_claim_token
       AND receipt.lease_expires_at > clock_timestamp()
       AND receipt.operation_result_id IS NULL
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'decision brief command fence is invalid'
            USING ERRCODE = '55000';
    END IF;

    capability_document := public.archie_verify_command_capability(
        p_capability_document,
        p_capability,
        'transformation-command-execution-r1'
    );
    IF capability_document IS DISTINCT FROM jsonb_build_object(
        'schema_version', 'transformation-command-execution-r1',
        'key_id', capability_document->>'key_id',
        'organization_id', brief_organization_id,
        'actor_id', p_actor_id,
        'operation', 'brief.freeze',
        'idempotency_key', receipt_idempotency_key,
        'request_digest', receipt_digest,
        'natural_key', receipt_natural_key,
        'receipt_id', p_receipt_id,
        'generation', p_generation,
        'claim_token', p_claim_token,
        'claimant_request_id', receipt_claimant_request_id
    ) THEN
        RAISE EXCEPTION 'decision brief command capability is invalid'
            USING ERRCODE = '42501';
    END IF;

    PERFORM 1
      FROM public.users AS governed_user
     WHERE governed_user.organization_id = brief_organization_id
       AND governed_user.id IN (
           p_actor_id, brief_decision_authority_id,
           COALESCE(brief_exception_authority_id, brief_decision_authority_id)
       )
     ORDER BY governed_user.id
     FOR UPDATE;
    PERFORM 1
      FROM public.programme_role_assignments AS assignment
     WHERE assignment.organization_id = brief_organization_id
       AND assignment.programme_id = workstream_programme_id
       AND assignment.user_id IN (
           p_actor_id, brief_decision_authority_id,
           COALESCE(brief_exception_authority_id, brief_decision_authority_id)
       )
       AND (assignment.workstream_id IS NULL OR
            assignment.workstream_id = brief_workstream_id)
     ORDER BY assignment.id
     FOR UPDATE;

    IF NOT EXISTS (
        SELECT 1
          FROM public.users AS actor
         WHERE actor.id = p_actor_id
           AND actor.organization_id = brief_organization_id
           AND (
               actor.enterprise_role IN (
                   'enterprise_architect', 'chief_architect', 'cto',
                   'platform_admin', 'organization_admin', 'administrator'
               )
               OR actor.is_org_admin IS TRUE
               OR actor.is_platform_admin IS TRUE
               OR EXISTS (
                   SELECT 1
                     FROM public.programme_role_assignments AS assignment
                    WHERE assignment.organization_id = brief_organization_id
                      AND assignment.programme_id = workstream_programme_id
                      AND assignment.user_id = p_actor_id
                      AND (assignment.workstream_id IS NULL OR
                           assignment.workstream_id = brief_workstream_id)
                      AND assignment.role IN (
                          'programme_owner', 'workstream_lead', 'decision_authority'
                      )
                      AND assignment.effective_from <= CURRENT_DATE
                      AND (assignment.effective_to IS NULL OR
                           assignment.effective_to >= CURRENT_DATE)
               )
           )
    ) THEN
        RAISE EXCEPTION 'decision brief actor is not currently authorized'
            USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM public.users AS authority
         WHERE authority.id = brief_decision_authority_id
           AND authority.organization_id = brief_organization_id
           AND (
               authority.enterprise_role IN (
                   'chief_architect', 'cto', 'enterprise_architect',
                   'platform_admin', 'organization_admin', 'administrator'
               )
               OR authority.is_org_admin IS TRUE
               OR authority.is_platform_admin IS TRUE
               OR EXISTS (
                   SELECT 1
                     FROM public.programme_role_assignments AS assignment
                    WHERE assignment.organization_id = brief_organization_id
                      AND assignment.programme_id = workstream_programme_id
                      AND assignment.user_id = brief_decision_authority_id
                      AND (assignment.workstream_id IS NULL OR
                           assignment.workstream_id = brief_workstream_id)
                      AND assignment.role = 'decision_authority'
                      AND assignment.effective_from <= CURRENT_DATE
                      AND (assignment.effective_to IS NULL OR
                           assignment.effective_to >= CURRENT_DATE)
               )
           )
    ) THEN
        RAISE EXCEPTION 'decision brief authority is not current'
            USING ERRCODE = '42501';
    END IF;

    IF p_frozen_payload->>'schema_version' IS DISTINCT FROM 'decision-brief-r1.1'
       OR (p_frozen_payload->>'organization_id')::bigint IS DISTINCT FROM brief_organization_id
       OR (p_frozen_payload->>'brief_id')::bigint IS DISTINCT FROM p_brief_id
       OR (p_frozen_payload->>'workstream_id')::bigint IS DISTINCT FROM brief_workstream_id
       OR (p_frozen_payload->>'programme_id')::bigint IS DISTINCT FROM workstream_programme_id
       OR (p_frozen_payload->>'source_revision')::integer IS DISTINCT FROM p_expected_revision
       OR p_frozen_payload->>'title' IS DISTINCT FROM brief_title
       OR p_frozen_payload->>'objective' IS DISTINCT FROM workstream_objective
       OR p_frozen_payload->'scope_expression' IS DISTINCT FROM workstream_scope
       OR p_frozen_payload->'unknowns' IS DISTINCT FROM brief_unknowns
       OR p_frozen_payload->'conflicts' IS DISTINCT FROM brief_conflicts
       OR p_frozen_payload->'expected_impacts' IS DISTINCT FROM brief_expected_impacts
       OR p_frozen_payload->'candidate' IS DISTINCT FROM (CASE
           WHEN brief_candidate_id IS NULL THEN 'null'::jsonb
           ELSE (
               SELECT jsonb_build_object(
                   'id', candidate.id,
                   'subject_type', candidate.subject_type,
                   'subject_id', candidate.subject_id
               )
                 FROM public.transformation_candidates AS candidate
                WHERE candidate.id = brief_candidate_id
                  AND candidate.organization_id = brief_organization_id
                  AND candidate.workstream_id = brief_workstream_id
                  AND candidate.inclusion_status = 'accepted'
           )
       END)
       OR p_frozen_payload->'option_exception' IS DISTINCT FROM (CASE
           WHEN brief_exception_type IS NULL THEN 'null'::jsonb
           ELSE jsonb_build_object(
               'type', brief_exception_type,
               'name', btrim(brief_exception_name),
               'reason', btrim(brief_exception_reason),
               'authority_id', brief_exception_authority_id
           )
       END)
       OR (p_frozen_payload->>'decision_authority_id')::bigint
          IS DISTINCT FROM brief_decision_authority_id
       OR (p_frozen_payload->>'created_by_id')::bigint IS DISTINCT FROM p_actor_id
       OR p_frozen_payload->>'policy_version' IS DISTINCT FROM 'transformation-r1.1'
       OR COALESCE((p_frozen_payload->'human_assertions'->>'reviewed_ai_material')::boolean,
                   FALSE) IS NOT TRUE THEN
        RAISE EXCEPTION 'decision brief frozen payload does not match locked draft'
            USING ERRCODE = '55000';
    END IF;

    IF brief_exception_type IS NOT NULL AND NOT EXISTS (
        SELECT 1
          FROM public.users AS authority
         WHERE authority.id = brief_exception_authority_id
           AND authority.organization_id = brief_organization_id
           AND (
               authority.enterprise_role IN (
                   'chief_architect', 'cto', 'enterprise_architect',
                   'platform_admin', 'organization_admin', 'administrator'
               )
               OR authority.is_org_admin IS TRUE
               OR authority.is_platform_admin IS TRUE
               OR EXISTS (
                   SELECT 1
                     FROM public.programme_role_assignments AS assignment
                    WHERE assignment.organization_id = brief_organization_id
                      AND assignment.programme_id = workstream_programme_id
                      AND assignment.user_id = brief_exception_authority_id
                      AND (assignment.workstream_id IS NULL OR
                           assignment.workstream_id = brief_workstream_id)
                      AND assignment.role = 'decision_authority'
                      AND assignment.effective_from <= CURRENT_DATE
                      AND (assignment.effective_to IS NULL OR
                           assignment.effective_to >= CURRENT_DATE)
               )
           )
    ) THEN
        RAISE EXCEPTION 'decision brief option exception authority is not current'
            USING ERRCODE = '42501';
    END IF;

    SELECT COALESCE(array_agg((item->>'id')::bigint ORDER BY (item->>'id')::bigint),
                    ARRAY[]::bigint[])
      INTO payload_option_ids
      FROM jsonb_array_elements(p_frozen_payload->'option_versions') item;
    PERFORM 1
      FROM public.transformation_options AS root
     WHERE root.organization_id = brief_organization_id
       AND root.workstream_id = brief_workstream_id
       AND root.candidate_id IS NOT DISTINCT FROM brief_candidate_id
     ORDER BY root.id
     FOR UPDATE;
    PERFORM 1
      FROM public.transformation_option_versions AS version
      JOIN public.transformation_options AS root
        ON root.id = version.option_id
       AND root.organization_id = version.organization_id
     WHERE root.organization_id = brief_organization_id
       AND root.workstream_id = brief_workstream_id
       AND root.candidate_id IS NOT DISTINCT FROM brief_candidate_id
     ORDER BY version.option_id, version.version, version.id
     FOR UPDATE OF version;
    SELECT count(*) INTO root_count
      FROM public.transformation_options AS root
     WHERE root.organization_id = brief_organization_id
       AND root.workstream_id = brief_workstream_id
       AND root.candidate_id IS NOT DISTINCT FROM brief_candidate_id;
    SELECT COALESCE(array_agg(latest.id ORDER BY latest.id), ARRAY[]::bigint[])
      INTO latest_option_ids
      FROM (
          SELECT DISTINCT ON (version.option_id)
                 version.id, version.option_id
            FROM public.transformation_option_versions AS version
            JOIN public.transformation_options AS root
              ON root.id = version.option_id
             AND root.organization_id = version.organization_id
           WHERE root.organization_id = brief_organization_id
             AND root.workstream_id = brief_workstream_id
             AND root.candidate_id IS NOT DISTINCT FROM brief_candidate_id
           ORDER BY version.option_id, version.version DESC, version.id DESC
      ) AS latest;
    IF root_count = 0 OR cardinality(latest_option_ids) <> root_count THEN
        RAISE EXCEPTION 'decision brief scoped option version is missing'
            USING ERRCODE = '55000';
    END IF;
    IF payload_option_ids IS DISTINCT FROM latest_option_ids
       OR cardinality(payload_option_ids) IS DISTINCT FROM
          cardinality(ARRAY(SELECT DISTINCT unnest(payload_option_ids)))
       OR NOT EXISTS (
           SELECT 1
             FROM public.transformation_option_versions AS recommendation
            WHERE recommendation.id =
                  (p_frozen_payload->>'recommendation_option_version_id')::bigint
              AND recommendation.option_id = brief_recommendation_option_id
              AND recommendation.id = ANY(payload_option_ids)
       )
       OR EXISTS (
           SELECT 1
             FROM jsonb_array_elements(p_frozen_payload->'option_versions') item
            WHERE NOT EXISTS (
                SELECT 1
                  FROM public.transformation_option_versions AS version
                 WHERE version.id = (item->>'id')::bigint
                   AND version.organization_id = brief_organization_id
                   AND version.workstream_id = brief_workstream_id
                   AND version.candidate_id IS NOT DISTINCT FROM brief_candidate_id
                   AND version.option_id = (item->>'option_id')::bigint
                   AND version.version = (item->>'version')::integer
                   AND version.content_hash = item->>'content_hash'
                   AND version.content_json::jsonb = item->'content'
            )
       ) THEN
        RAISE EXCEPTION 'decision brief option membership is not current and complete'
            USING ERRCODE = '55000';
    END IF;

    PERFORM 1
      FROM public.transformation_candidates AS candidate
     WHERE candidate.organization_id = brief_organization_id
       AND candidate.workstream_id = brief_workstream_id
       AND candidate.inclusion_status = 'accepted'
       AND (brief_candidate_id IS NULL OR candidate.id = brief_candidate_id)
     ORDER BY candidate.id
     FOR UPDATE;
    PERFORM 1
      FROM public.evidence_claim_heads AS head
     WHERE head.organization_id = brief_organization_id
       AND head.current_record_id IS NOT NULL
       AND EXISTS (
           SELECT 1
             FROM public.transformation_candidates AS candidate
            WHERE candidate.organization_id = brief_organization_id
              AND candidate.workstream_id = brief_workstream_id
              AND candidate.inclusion_status = 'accepted'
              AND (brief_candidate_id IS NULL OR candidate.id = brief_candidate_id)
              AND candidate.subject_type = head.subject_type
              AND candidate.subject_id = head.subject_id
       )
     ORDER BY head.subject_type, head.subject_id, head.claim_key,
              head.source_identity, head.id
     FOR UPDATE;
    SELECT COALESCE(array_agg((item->>'id')::bigint ORDER BY (item->>'id')::bigint),
                    ARRAY[]::bigint[])
      INTO payload_evidence_ids
      FROM jsonb_array_elements(p_frozen_payload->'evidence') item;
    PERFORM 1
      FROM public.evidence_records AS record
     WHERE record.organization_id = brief_organization_id
       AND record.id = ANY(payload_evidence_ids)
     ORDER BY record.id
     FOR UPDATE;
    PERFORM 1
      FROM public.evidence_requests AS request
     WHERE request.organization_id = brief_organization_id
       AND EXISTS (
           SELECT 1
             FROM public.transformation_candidates AS candidate
            WHERE candidate.id = request.candidate_id
              AND candidate.organization_id = brief_organization_id
              AND candidate.workstream_id = brief_workstream_id
              AND candidate.inclusion_status = 'accepted'
              AND (brief_candidate_id IS NULL OR candidate.id = brief_candidate_id)
       )
     ORDER BY request.candidate_id, request.claim_key, request.id
     FOR UPDATE;
    SELECT COALESCE(array_agg(DISTINCT head.current_record_id
                              ORDER BY head.current_record_id), ARRAY[]::bigint[])
      INTO current_evidence_ids
      FROM public.evidence_claim_heads AS head
     WHERE head.organization_id = brief_organization_id
       AND head.current_record_id IS NOT NULL
       AND EXISTS (
           SELECT 1
             FROM public.transformation_candidates AS candidate
            WHERE candidate.organization_id = brief_organization_id
              AND candidate.workstream_id = brief_workstream_id
              AND candidate.inclusion_status = 'accepted'
              AND (brief_candidate_id IS NULL OR candidate.id = brief_candidate_id)
              AND candidate.subject_type = head.subject_type
              AND candidate.subject_id = head.subject_id
       );
    IF cardinality(current_evidence_ids) = 0
       OR NOT current_evidence_ids <@ payload_evidence_ids
       OR cardinality(payload_evidence_ids) IS DISTINCT FROM
          cardinality(ARRAY(SELECT DISTINCT unnest(payload_evidence_ids)))
       OR EXISTS (
           SELECT 1
             FROM jsonb_array_elements(p_frozen_payload->'evidence') item
            WHERE NOT EXISTS (
                SELECT 1
                  FROM public.evidence_records AS record
                  JOIN public.evidence_claim_heads AS head
                    ON head.id = (item->'head'->>'id')::bigint
                   AND head.organization_id = record.organization_id
                   AND head.subject_type = record.subject_type
                   AND head.subject_id = record.subject_id
                   AND head.claim_key = record.claim_key
                   AND head.source_identity = record.source_identity
                 WHERE record.id = (item->>'id')::bigint
                   AND record.organization_id = brief_organization_id
                   AND record.workstream_id = brief_workstream_id
                   AND EXISTS (
                       SELECT 1
                         FROM public.transformation_candidates AS candidate
                        WHERE candidate.id = record.candidate_id
                          AND candidate.organization_id = brief_organization_id
                          AND candidate.workstream_id = brief_workstream_id
                          AND candidate.inclusion_status = 'accepted'
                          AND (brief_candidate_id IS NULL OR
                               candidate.id = brief_candidate_id)
                   )
                   AND record.subject_type = item->>'subject_type'
                   AND record.subject_id = (item->>'subject_id')::bigint
                   AND record.claim_key = item->>'claim_key'
                   AND record.source_identity = item->>'source_identity'
                   AND record.source_version = item->>'source_version'
                   AND record.source_checksum = item->>'source_checksum'
                   AND record.value_type = item->>'value_type'
                   AND record.classification = item->>'classification'
                   AND record.value_json::jsonb = item->'value'
                   AND record.freshness_expires_at IS NOT DISTINCT FROM
                       (item->>'freshness_expires_at')::timestamptz
                   AND head.revision = (item->'head'->>'revision')::integer
                   AND head.current_record_id =
                       (item->'head'->>'current_record_id')::bigint
                   AND head.source_identity = item->'head'->>'source_identity'
                   AND (head.current_record_id = record.id) =
                       (item->>'was_current')::boolean
                   AND (item->>'acknowledged')::boolean = EXISTS (
                       SELECT 1
                         FROM jsonb_array_elements_text(
                             p_frozen_payload->'human_assertions'->
                             'acknowledged_superseded_evidence_ids'
                         ) AS acknowledged(value)
                        WHERE acknowledged.value::bigint = record.id
                   )
                   AND (
                       (
                           head.current_record_id = record.id
                           AND record.freshness_status IN ('fresh', 'not_applicable')
                           AND (record.freshness_expires_at IS NULL OR
                                record.freshness_expires_at > clock_timestamp())
                       )
                       OR (item->>'acknowledged')::boolean IS TRUE
                   )
                   AND item->>'freshness_status' = CASE
                       WHEN record.freshness_expires_at IS NOT NULL
                            AND record.freshness_expires_at <= clock_timestamp()
                           THEN 'expired'
                       ELSE record.freshness_status
                   END
            )
       ) THEN
        RAISE EXCEPTION 'decision brief evidence membership is not current and complete'
            USING ERRCODE = '55000';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.evidence_requests AS request
         WHERE request.organization_id = brief_organization_id
           AND request.required IS TRUE
           AND EXISTS (
               SELECT 1 FROM public.transformation_candidates AS candidate
                WHERE candidate.id = request.candidate_id
                  AND candidate.organization_id = brief_organization_id
                  AND candidate.workstream_id = brief_workstream_id
                  AND candidate.inclusion_status = 'accepted'
                  AND (brief_candidate_id IS NULL OR candidate.id = brief_candidate_id)
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM public.evidence_records AS accepted
                 JOIN public.evidence_claim_heads AS head
                   ON head.organization_id = accepted.organization_id
                  AND head.subject_type = accepted.subject_type
                  AND head.subject_id = accepted.subject_id
                  AND head.claim_key = accepted.claim_key
                  AND head.source_identity = accepted.source_identity
                  AND head.current_record_id = accepted.id
                WHERE request.status = 'accepted'
                  AND accepted.id = request.accepted_evidence_id
                  AND accepted.organization_id = brief_organization_id
                  AND accepted.classification <> 'conflict'
                  AND accepted.subject_type = request.subject_type
                  AND accepted.subject_id = request.subject_id
                  AND accepted.claim_key = request.claim_key
           )
    ) THEN
        RAISE EXCEPTION 'decision brief required evidence is incomplete'
            USING ERRCODE = '55000';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.evidence_claim_heads AS current_head
          JOIN public.evidence_records AS current_record
            ON current_record.id = current_head.current_record_id
           AND current_record.organization_id = current_head.organization_id
         WHERE current_head.organization_id = brief_organization_id
           AND EXISTS (
               SELECT 1
                 FROM public.transformation_candidates AS candidate
                WHERE candidate.organization_id = brief_organization_id
                  AND candidate.workstream_id = brief_workstream_id
                  AND candidate.inclusion_status = 'accepted'
                  AND (brief_candidate_id IS NULL OR
                       candidate.id = brief_candidate_id)
                  AND candidate.subject_type = current_head.subject_type
                  AND candidate.subject_id = current_head.subject_id
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM public.evidence_requests AS request
                 JOIN public.evidence_records AS accepted
                   ON accepted.id = request.accepted_evidence_id
                  AND accepted.organization_id = request.organization_id
                 JOIN public.evidence_claim_heads AS accepted_head
                   ON accepted_head.organization_id = accepted.organization_id
                  AND accepted_head.subject_type = accepted.subject_type
                  AND accepted_head.subject_id = accepted.subject_id
                  AND accepted_head.claim_key = accepted.claim_key
                  AND accepted_head.source_identity = accepted.source_identity
                  AND accepted_head.current_record_id = accepted.id
                WHERE request.organization_id = brief_organization_id
                  AND request.status = 'accepted'
                  AND EXISTS (
                      SELECT 1
                        FROM public.transformation_candidates AS candidate
                       WHERE candidate.id = request.candidate_id
                         AND candidate.organization_id = brief_organization_id
                         AND candidate.workstream_id = brief_workstream_id
                         AND candidate.inclusion_status = 'accepted'
                         AND (brief_candidate_id IS NULL OR
                              candidate.id = brief_candidate_id)
                  )
                  AND accepted.classification <> 'conflict'
                  AND request.subject_type = current_record.subject_type
                  AND request.subject_id = current_record.subject_id
                  AND request.claim_key = current_record.claim_key
           )
    ) THEN
        RAISE EXCEPTION 'decision brief current evidence is not accepted'
            USING ERRCODE = '55000';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.evidence_claim_heads AS conflict_head
          JOIN public.evidence_records AS conflict
            ON conflict.id = conflict_head.current_record_id
           AND conflict.organization_id = conflict_head.organization_id
         WHERE conflict_head.organization_id = brief_organization_id
           AND conflict.classification = 'conflict'
           AND conflict.id = ANY(payload_evidence_ids)
           AND NOT EXISTS (
               SELECT 1
                 FROM public.evidence_claim_heads AS resolution_head
                 JOIN public.evidence_records AS resolution
                   ON resolution.id = resolution_head.current_record_id
                  AND resolution.organization_id = resolution_head.organization_id
                WHERE resolution_head.organization_id = brief_organization_id
                  AND resolution.subject_type = conflict.subject_type
                  AND resolution.subject_id = conflict.subject_id
                  AND resolution.claim_key = conflict.claim_key
                  AND resolution.source_type = 'governance_resolution'
                  AND resolution.cited_evidence_ids::jsonb @>
                      jsonb_build_array(conflict.id)
                  AND resolution.id = ANY(payload_evidence_ids)
           )
    ) THEN
        RAISE EXCEPTION 'decision brief current evidence conflict is unresolved'
            USING ERRCODE = '55000';
    END IF;

    PERFORM 1
      FROM public.programme_outcome_commitments AS outcome
     WHERE outcome.organization_id = brief_organization_id
       AND outcome.programme_id = workstream_programme_id
       AND (outcome.workstream_id IS NULL OR
            outcome.workstream_id = brief_workstream_id)
     ORDER BY outcome.id
     FOR UPDATE;
    SELECT COALESCE(array_agg((item->>'id')::bigint ORDER BY (item->>'id')::bigint),
                    ARRAY[]::bigint[])
      INTO payload_outcome_ids
      FROM jsonb_array_elements(p_frozen_payload->'outcomes') item;
    SELECT COALESCE(array_agg(outcome.id ORDER BY outcome.id), ARRAY[]::bigint[])
      INTO current_outcome_ids
      FROM public.programme_outcome_commitments AS outcome
     WHERE outcome.organization_id = brief_organization_id
       AND outcome.programme_id = workstream_programme_id
       AND (outcome.workstream_id IS NULL OR
            outcome.workstream_id = brief_workstream_id);
    PERFORM 1
      FROM public.measure_definitions AS measure
     WHERE measure.organization_id = brief_organization_id
       AND measure.outcome_commitment_id = ANY(current_outcome_ids)
     ORDER BY measure.id
     FOR UPDATE;
    SELECT COALESCE(array_agg((item->>'id')::bigint ORDER BY (item->>'id')::bigint),
                    ARRAY[]::bigint[])
      INTO payload_measure_ids
      FROM jsonb_array_elements(p_frozen_payload->'measures') item;
    SELECT COALESCE(array_agg(measure.id ORDER BY measure.id), ARRAY[]::bigint[])
      INTO current_measure_ids
      FROM public.measure_definitions AS measure
     WHERE measure.organization_id = brief_organization_id
       AND measure.outcome_commitment_id = ANY(current_outcome_ids);
    IF payload_outcome_ids IS DISTINCT FROM current_outcome_ids
       OR payload_measure_ids IS DISTINCT FROM current_measure_ids
       OR EXISTS (
           SELECT 1
             FROM jsonb_array_elements(p_frozen_payload->'outcomes') item
            WHERE NOT EXISTS (
                SELECT 1
                  FROM public.programme_outcome_commitments AS outcome
                 WHERE outcome.id = (item->>'id')::bigint
                   AND outcome.organization_id = brief_organization_id
                   AND outcome.programme_id = workstream_programme_id
                   AND (outcome.workstream_id IS NULL OR
                        outcome.workstream_id = brief_workstream_id)
                   AND outcome.statement = item->>'statement'
                   AND outcome.owner_id = (item->>'owner_id')::bigint
                   AND outcome.improvement_direction = item->>'improvement_direction'
                   AND to_char(outcome.target_date, 'YYYY-MM-DD')
                       IS NOT DISTINCT FROM item->>'target_date'
                   AND outcome.lifecycle = item->>'lifecycle'
            )
       )
       OR EXISTS (
           SELECT 1
             FROM jsonb_array_elements(p_frozen_payload->'measures') item
            WHERE NOT EXISTS (
                SELECT 1
                  FROM public.measure_definitions AS measure
                 WHERE measure.id = (item->>'id')::bigint
                   AND measure.organization_id = brief_organization_id
                   AND measure.outcome_commitment_id =
                       (item->>'outcome_commitment_id')::bigint
                   AND measure.metric_name = item->>'metric_name'
                   AND measure.unit = item->>'unit'
                   AND measure.currency IS NOT DISTINCT FROM item->>'currency'
                   AND measure.aggregation = item->>'aggregation'
                   AND measure.baseline_amount IS NOT DISTINCT FROM
                       (item->>'baseline_amount')::numeric
                   AND measure.target_amount IS NOT DISTINCT FROM
                       (item->>'target_amount')::numeric
                   AND measure.tolerance_amount IS NOT DISTINCT FROM
                       (item->>'tolerance_amount')::numeric
                   AND measure.baseline_value IS NOT DISTINCT FROM
                       (item->>'baseline_value')::numeric
                   AND measure.target_value IS NOT DISTINCT FROM
                       (item->>'target_value')::numeric
                   AND to_char(measure.baseline_date, 'YYYY-MM-DD')
                       IS NOT DISTINCT FROM item->>'baseline_date'
                   AND to_char(measure.target_date, 'YYYY-MM-DD')
                       IS NOT DISTINCT FROM item->>'target_date'
                   AND measure.cadence IS NOT DISTINCT FROM item->>'cadence'
                   AND measure.source_adapter IS NOT DISTINCT FROM
                       item->>'source_adapter'
                   AND measure.source_key IS NOT DISTINCT FROM item->>'source_key'
                   AND measure.tolerance IS NOT DISTINCT FROM
                       (item->>'tolerance')::numeric
                   AND measure.unavailable_reason IS NOT DISTINCT FROM
                       item->>'unavailable_reason'
            )
       ) THEN
        RAISE EXCEPTION 'decision brief outcome membership is not current and complete'
            USING ERRCODE = '55000';
    END IF;

    SELECT COALESCE(array_agg(value ORDER BY value), ARRAY[]::text[])
      INTO acknowledged_unknowns
      FROM jsonb_array_elements_text(
          p_frozen_payload->'human_assertions'->'acknowledged_unknown_codes'
      ) value;
    SELECT COALESCE(array_agg(value ORDER BY value), ARRAY[]::text[])
      INTO required_unknowns
      FROM jsonb_array_elements_text(brief_unknowns) value;
    IF acknowledged_unknowns IS DISTINCT FROM required_unknowns THEN
        RAISE EXCEPTION 'decision brief unknown acknowledgements are incomplete'
            USING ERRCODE = '55000';
    END IF;

    request_payload := jsonb_build_object(
        'brief_id', p_brief_id,
        'workstream_id', brief_workstream_id,
        'option_version_ids', to_jsonb(payload_option_ids),
        'evidence_ids', to_jsonb(payload_evidence_ids),
        'assertions', p_frozen_payload->'human_assertions',
        'expected_revision', p_expected_revision
    );
    BEGIN
        IF p_request_document::jsonb IS DISTINCT FROM request_payload THEN
            RAISE EXCEPTION 'decision brief request document does not match payload'
                USING ERRCODE = '55000';
        END IF;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'decision brief request document is invalid'
            USING ERRCODE = '55000';
    END;
    computed_request_digest := encode(
        sha256(convert_to(p_request_document, 'UTF8')),
        'hex'
    );
    IF receipt_digest IS DISTINCT FROM computed_request_digest THEN
        RAISE EXCEPTION 'decision brief request digest is invalid'
            USING ERRCODE = '55000';
    END IF;

    SELECT COALESCE(max(version.version), 0) + 1
      INTO next_version
      FROM public.decision_brief_versions AS version
     WHERE version.organization_id = brief_organization_id
       AND version.brief_id = p_brief_id;
    IF (p_frozen_payload->>'brief_version')::integer IS DISTINCT FROM next_version THEN
        RAISE EXCEPTION 'decision brief version sequence changed'
            USING ERRCODE = '55000';
    END IF;
    frozen_at := (p_frozen_payload->>'created_at')::timestamptz;
    IF frozen_at < receipt_created_at OR
       frozen_at > clock_timestamp() + interval '1 minute' THEN
        RAISE EXCEPTION 'decision brief freeze timestamp is outside command lease'
            USING ERRCODE = '55000';
    END IF;

    hash_envelope := jsonb_build_object(
        'schema_version', 'decision-brief-hash-r1.1',
        'organization_id', brief_organization_id,
        'brief_id', p_brief_id,
        'workstream_id', brief_workstream_id,
        'version', next_version,
        'source_revision', p_expected_revision,
        'created_by_id', p_actor_id,
        'created_at', p_frozen_payload->'created_at',
        'frozen_payload', p_frozen_payload,
        'recommendation_option_version_id',
            (p_frozen_payload->>'recommendation_option_version_id')::bigint,
        'option_version_ids', to_jsonb(payload_option_ids),
        'cited_evidence_ids', to_jsonb(payload_evidence_ids),
        'outcome_ids', to_jsonb(payload_outcome_ids),
        'measure_ids', to_jsonb(payload_measure_ids),
        'policy_version', p_frozen_payload->>'policy_version',
        'submitted_by_id', p_actor_id,
        'submitter_authorized', TRUE,
        'decision_authority_id', brief_decision_authority_id,
        'human_reviewed_ai', TRUE,
        'blockers_cleared', TRUE,
        'unknowns_acknowledged', TRUE
    );
    BEGIN
        IF p_canonical_document::jsonb IS DISTINCT FROM hash_envelope THEN
            RAISE EXCEPTION 'decision brief canonical document does not match snapshot'
                USING ERRCODE = '55000';
        END IF;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'decision brief canonical document is invalid'
            USING ERRCODE = '55000';
    END;
    computed_hash := encode(
        sha256(convert_to(p_canonical_document, 'UTF8')),
        'hex'
    );

    INSERT INTO public.decision_brief_versions (
        organization_id, brief_id, workstream_id, version, source_revision,
        frozen_payload, recommendation_option_version_id, option_version_ids,
        cited_evidence_ids, outcome_ids, measure_ids, policy_version,
        created_by_id, created_at, content_hash, canonical_document, submitted_by_id,
        submitter_authorized, decision_authority_id, human_reviewed_ai,
        blockers_cleared, unknowns_acknowledged
    ) VALUES (
        brief_organization_id, p_brief_id, brief_workstream_id, next_version,
        p_expected_revision, p_frozen_payload,
        (p_frozen_payload->>'recommendation_option_version_id')::bigint,
        to_jsonb(payload_option_ids), to_jsonb(payload_evidence_ids),
        to_jsonb(payload_outcome_ids), to_jsonb(payload_measure_ids),
        p_frozen_payload->>'policy_version', p_actor_id, frozen_at,
        computed_hash, p_canonical_document, p_actor_id,
        TRUE, brief_decision_authority_id, TRUE,
        TRUE, TRUE
    ) RETURNING id INTO inserted_version_id;

    INSERT INTO public.decision_brief_option_citations
        (organization_id, brief_version_id, option_version_id)
    SELECT brief_organization_id, inserted_version_id, option_id
      FROM unnest(payload_option_ids) option_id
     ORDER BY option_id;

    INSERT INTO public.decision_brief_evidence_citations
        (organization_id, brief_version_id, evidence_record_id,
         evidence_head_id, head_revision_at_freeze,
         current_record_id_at_freeze, was_current, acknowledged,
         freshness_status)
    SELECT brief_organization_id,
           inserted_version_id,
           (item->>'id')::bigint,
           (item->'head'->>'id')::bigint,
           (item->'head'->>'revision')::integer,
           (item->'head'->>'current_record_id')::bigint,
           (item->>'was_current')::boolean,
           (item->>'acknowledged')::boolean,
           item->>'freshness_status'
      FROM jsonb_array_elements(p_frozen_payload->'evidence') item
     ORDER BY (item->>'id')::bigint;

    UPDATE public.decision_briefs
       SET status = 'frozen',
           revision = p_expected_revision + 1,
           updated_at = frozen_at
     WHERE id = p_brief_id
       AND organization_id = brief_organization_id
       AND status = 'draft'
       AND revision = p_expected_revision;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 1 THEN
        RAISE EXCEPTION 'decision brief draft transition lost its fence'
            USING ERRCODE = '40001';
    END IF;

    decision_brief_version_id := inserted_version_id;
    decision_brief_version_number := next_version;
    decision_brief_content_hash := computed_hash;
    decision_brief_created_at := frozen_at;
    RETURN NEXT;
END;
$$
"""


_RECEIPT_FUNCTION_SQL = r"""
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
"""


_EVIDENCE_HEAD_GUARD_SQL = r"""
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
"""


_EVIDENCE_EVENT_BINDING_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_guard_evidence_event_binding()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM public.evidence_claim_heads head
        JOIN public.evidence_records record
          ON record.id = NEW.new_record_id
         AND record.organization_id = NEW.organization_id
         AND record.subject_type = head.subject_type
         AND record.subject_id = head.subject_id
         AND record.claim_key = head.claim_key
         AND record.source_identity = head.source_identity
         AND record.supersedes_id IS NOT DISTINCT FROM NEW.old_record_id
        JOIN public.command_idempotency_records receipt
          ON receipt.id = NEW.command_receipt_id
         AND receipt.organization_id = NEW.organization_id
         AND receipt.actor_id = NEW.actor_id
         AND receipt.lease_generation = NEW.command_generation
         AND receipt.status IN ('in_progress', 'succeeded')
        WHERE head.id = NEW.head_id
          AND head.organization_id = NEW.organization_id
          AND head.current_record_id = NEW.new_record_id
          AND head.revision = NEW.revision
          AND NEW.created_txid = txid_current()
    ) THEN
        RAISE EXCEPTION 'evidence event is not bound to its head movement'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$
"""


_EVIDENCE_HEAD_ADVANCE_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_advance_evidence_head(
    p_head_id bigint,
    p_new_record_id bigint,
    p_expected_revision integer,
    p_actor_id bigint,
    p_receipt_id bigint,
    p_generation integer,
    p_claim_token text
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    head_organization_id bigint;
    head_subject_type text;
    head_subject_id bigint;
    head_claim_key text;
    head_source_identity text;
    head_current_record_id bigint;
    head_revision integer;
    receipt_actor_id bigint;
    receipt_organization_id bigint;
    receipt_operation text;
    receipt_natural_key text;
    receipt_status text;
    receipt_generation integer;
    receipt_token text;
    receipt_expiry timestamptz;
    record_candidate_id bigint;
    record_classification text;
    record_source_type text;
    record_source_record_id bigint;
    record_created_by_id bigint;
    record_value jsonb;
    record_citations jsonb;
    governing_record_id bigint;
    governing_source_identity text;
    governing_head_id bigint;
    governing_current_record_id bigint;
    attestation_candidate_id bigint;
    attestation_source_identity text;
    attestation_revision integer;
    expected_natural_key text;
    event_reason text;
    affected integer;
BEGIN
    -- Read immutable identity inputs first, then acquire every evidence-head
    -- lock in the same natural-key order.  The values are re-read after the
    -- locks, so neither this preliminary snapshot nor the caller's snapshot
    -- can authorize a stale movement.
    SELECT organization_id, subject_type, subject_id, claim_key,
           source_identity, current_record_id, revision
      INTO head_organization_id, head_subject_type, head_subject_id,
           head_claim_key, head_source_identity, head_current_record_id,
           head_revision
      FROM public.evidence_claim_heads
     WHERE id = p_head_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'evidence head not found' USING ERRCODE = '55000';
    END IF;
    SELECT record.candidate_id, record.classification, record.source_type,
           record.source_record_id, record.created_by_id,
           record.value_json::jsonb, record.cited_evidence_ids::jsonb
      INTO record_candidate_id, record_classification, record_source_type,
           record_source_record_id, record_created_by_id,
           record_value, record_citations
      FROM public.evidence_records record
        WHERE record.id = p_new_record_id
          AND record.organization_id = head_organization_id
          AND record.subject_type = head_subject_type
          AND record.subject_id = head_subject_id
          AND record.claim_key = head_claim_key
          AND record.source_identity = head_source_identity
          AND record.supersedes_id IS NOT DISTINCT FROM head_current_record_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'evidence record does not extend the evidence head'
            USING ERRCODE = '55000';
    END IF;

    SELECT actor_id, organization_id, operation, natural_key, status,
           lease_generation, claim_token, lease_expires_at
      INTO receipt_actor_id, receipt_organization_id, receipt_operation,
           receipt_natural_key, receipt_status, receipt_generation,
           receipt_token, receipt_expiry
      FROM public.command_idempotency_records
     WHERE id = p_receipt_id;
    IF receipt_operation = 'evidence.conflict.resolve'
       AND record_value ->> 'governing_evidence_id' ~ '^[1-9][0-9]*$' THEN
        governing_record_id := (record_value ->> 'governing_evidence_id')::bigint;
        SELECT governing.source_identity, governing_head.id
          INTO governing_source_identity, governing_head_id
          FROM public.evidence_records governing
          LEFT JOIN public.evidence_claim_heads governing_head
            ON governing_head.organization_id = governing.organization_id
           AND governing_head.subject_type = governing.subject_type
           AND governing_head.subject_id = governing.subject_id
           AND governing_head.claim_key = governing.claim_key
           AND governing_head.source_identity = governing.source_identity
         WHERE governing.id = governing_record_id
           AND governing.organization_id = head_organization_id
           AND governing.subject_type = head_subject_type
           AND governing.subject_id = head_subject_id
           AND governing.claim_key = head_claim_key;
    END IF;

    IF governing_head_id IS NULL THEN
        PERFORM head.id
          FROM public.evidence_claim_heads head
         WHERE head.id = p_head_id
         ORDER BY head.organization_id, head.subject_type, head.subject_id,
                  head.claim_key, head.source_identity, head.id
         FOR UPDATE;
    ELSE
        PERFORM head.id
          FROM public.evidence_claim_heads head
         WHERE head.id IN (p_head_id, governing_head_id)
         ORDER BY head.organization_id, head.subject_type, head.subject_id,
                  head.claim_key, head.source_identity, head.id
         FOR UPDATE;
    END IF;

    SELECT organization_id, subject_type, subject_id, claim_key,
           source_identity, current_record_id, revision
      INTO head_organization_id, head_subject_type, head_subject_id,
           head_claim_key, head_source_identity, head_current_record_id,
           head_revision
      FROM public.evidence_claim_heads
     WHERE id = p_head_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'evidence head not found' USING ERRCODE = '55000';
    END IF;
    IF head_revision <> p_expected_revision THEN
        RAISE EXCEPTION 'stale evidence head revision' USING ERRCODE = '40001';
    END IF;
    SELECT record.candidate_id, record.classification, record.source_type,
           record.source_record_id, record.created_by_id,
           record.value_json::jsonb, record.cited_evidence_ids::jsonb
      INTO record_candidate_id, record_classification, record_source_type,
           record_source_record_id, record_created_by_id,
           record_value, record_citations
      FROM public.evidence_records record
        WHERE record.id = p_new_record_id
          AND record.organization_id = head_organization_id
          AND record.subject_type = head_subject_type
          AND record.subject_id = head_subject_id
          AND record.claim_key = head_claim_key
          AND record.source_identity = head_source_identity
          AND record.supersedes_id IS NOT DISTINCT FROM head_current_record_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'evidence record does not extend the locked head'
            USING ERRCODE = '55000';
    END IF;

    SELECT actor_id, organization_id, operation, natural_key, status,
           lease_generation, claim_token, lease_expires_at
      INTO receipt_actor_id, receipt_organization_id, receipt_operation,
           receipt_natural_key, receipt_status, receipt_generation,
           receipt_token, receipt_expiry
      FROM public.command_idempotency_records
     WHERE id = p_receipt_id
     FOR UPDATE;
    IF NOT FOUND
       OR receipt_actor_id <> p_actor_id
       OR receipt_organization_id <> head_organization_id
       OR receipt_status <> 'in_progress'
       OR receipt_generation <> p_generation
       OR receipt_token IS DISTINCT FROM p_claim_token
       OR receipt_expiry IS NULL
       OR receipt_expiry <= clock_timestamp() THEN
        RAISE EXCEPTION 'evidence head command fence is stale or unrelated'
            USING ERRCODE = '55000';
    END IF;

    IF receipt_operation = 'evidence.conflict.resolve' THEN
        -- The governing lookup already binds tenant, subject and claim.  Reject
        -- both the literal target head and any duplicate head alias carrying
        -- that same complete key/source identity before an event can be written.
        IF governing_head_id IS NOT DISTINCT FROM p_head_id
           OR governing_source_identity IS NOT DISTINCT FROM head_source_identity THEN
            RAISE EXCEPTION 'governing evidence source must differ from resolution source'
                USING ERRCODE = '55000';
        END IF;
        SELECT head.current_record_id
          INTO governing_current_record_id
          FROM public.evidence_claim_heads head
         WHERE head.id = governing_head_id
           AND head.organization_id = head_organization_id
           AND head.subject_type = head_subject_type
           AND head.subject_id = head_subject_id
           AND head.claim_key = head_claim_key
           AND head.source_identity = governing_source_identity;
        IF NOT FOUND
           OR governing_current_record_id IS DISTINCT FROM governing_record_id THEN
            RAISE EXCEPTION 'governing evidence is not current'
                USING ERRCODE = '55000';
        END IF;
    END IF;

    IF record_created_by_id <> p_actor_id THEN
        RAISE EXCEPTION 'evidence record actor does not match command actor'
            USING ERRCODE = '55000';
    END IF;

    IF receipt_operation = 'evidence.observe'
       AND record_classification = 'observed' THEN
        expected_natural_key := format(
            'evidence:%%s:%%s:%%s:%%s',
            record_candidate_id,
            head_claim_key,
            encode(sha256(convert_to(head_source_identity, 'UTF8')), 'hex'),
            head_revision + 1
        );
        event_reason := 'canonical source observation';
    ELSIF receipt_operation = 'evidence.attest'
       AND record_classification = 'attested'
       AND record_source_type = 'attestation'
       AND record_source_record_id = p_actor_id
       AND head_source_identity = format('attestation:user:%%s', p_actor_id) THEN
        expected_natural_key := format(
            'evidence:%%s:%%s:%%s:%%s',
            record_candidate_id,
            head_claim_key,
            encode(sha256(convert_to(head_source_identity, 'UTF8')), 'hex'),
            head_revision + 1
        );
        event_reason := 'human attestation submitted';
    ELSIF receipt_operation = 'evidence.attest'
       AND record_classification = 'conflict'
       AND record_source_type = 'governance_conflict'
       AND head_source_identity = format(
           'conflict:request:%%s', record_source_record_id
       )
       AND EXISTS (
           SELECT 1
           FROM public.evidence_requests request
           WHERE request.id = record_source_record_id
             AND request.organization_id = head_organization_id
             AND request.candidate_id = record_candidate_id
             AND request.subject_type = head_subject_type
             AND request.subject_id = head_subject_id
             AND request.claim_key = head_claim_key
             AND request.assigned_to_id = p_actor_id
       ) THEN
        SELECT attestation.candidate_id, attestation.source_identity, event.revision
          INTO attestation_candidate_id, attestation_source_identity,
               attestation_revision
          FROM jsonb_array_elements_text(
                   COALESCE(record_citations, '[]'::jsonb)
               ) AS citation(evidence_id)
          JOIN public.evidence_records attestation
            ON attestation.id = citation.evidence_id::bigint
          JOIN public.evidence_head_events event
            ON event.new_record_id = attestation.id
           AND event.organization_id = head_organization_id
           AND event.command_receipt_id = p_receipt_id
           AND event.command_generation = p_generation
           AND event.created_txid = txid_current()
         WHERE attestation.organization_id = head_organization_id
           AND attestation.subject_type = head_subject_type
           AND attestation.subject_id = head_subject_id
           AND attestation.claim_key = head_claim_key
           AND attestation.classification = 'attested'
           AND attestation.source_type = 'attestation'
           AND attestation.source_record_id = p_actor_id
           AND attestation.created_by_id = p_actor_id
         ORDER BY attestation.id DESC
         LIMIT 1;
        IF FOUND THEN
            IF attestation_candidate_id IS DISTINCT FROM record_candidate_id THEN
                RAISE EXCEPTION 'attestation candidate does not match conflict request candidate'
                    USING ERRCODE = '55000';
            END IF;
            expected_natural_key := format(
                'evidence:%%s:%%s:%%s:%%s',
                attestation_candidate_id,
                head_claim_key,
                encode(
                    sha256(convert_to(attestation_source_identity, 'UTF8')),
                    'hex'
                ),
                attestation_revision
            );
            event_reason := 'attestation disagrees with canonical observation';
        END IF;
    ELSIF receipt_operation = 'evidence.conflict.resolve'
       AND record_classification = 'derived'
       AND record_source_type = 'governance_resolution'
       AND head_source_identity = format(
           'resolution:conflict:%%s', record_source_record_id
       )
       AND record_value ->> 'conflict_evidence_id' = record_source_record_id::text
       AND EXISTS (
           SELECT 1
           FROM public.evidence_records conflict
           JOIN public.evidence_records governing
             ON governing.id = (record_value ->> 'governing_evidence_id')::bigint
            AND governing.organization_id = conflict.organization_id
            AND governing.subject_type = conflict.subject_type
            AND governing.subject_id = conflict.subject_id
            AND governing.claim_key = conflict.claim_key
           WHERE conflict.id = record_source_record_id
             AND conflict.organization_id = head_organization_id
             AND conflict.subject_type = head_subject_type
             AND conflict.subject_id = head_subject_id
             AND conflict.claim_key = head_claim_key
             AND conflict.classification = 'conflict'
             AND EXISTS (
                 SELECT 1
                 FROM jsonb_array_elements_text(
                     conflict.cited_evidence_ids::jsonb
                 ) AS cited(evidence_id)
                 WHERE cited.evidence_id =
                       record_value ->> 'governing_evidence_id'
             )
       ) THEN
        expected_natural_key := format(
            'evidence-conflict-resolution:%%s:%%s',
            record_source_record_id,
            record_value ->> 'governing_evidence_id'
        );
        event_reason := 'decision authority selected governing source';
    END IF;

    IF expected_natural_key IS NULL
       OR receipt_natural_key IS DISTINCT FROM expected_natural_key THEN
        RAISE EXCEPTION 'receipt operation or natural key does not authorize evidence head'
            USING ERRCODE = '55000';
    END IF;

    INSERT INTO public.evidence_head_events (
        organization_id, head_id, old_record_id, new_record_id, actor_id,
        command_receipt_id, command_generation, reason, revision, created_txid
    ) VALUES (
        head_organization_id, p_head_id, head_current_record_id, p_new_record_id,
        p_actor_id, p_receipt_id, p_generation, event_reason,
        head_revision + 1, txid_current()
    );

    UPDATE public.evidence_claim_heads
       SET current_record_id = p_new_record_id,
           revision = head_revision + 1,
           updated_at = clock_timestamp()
     WHERE id = p_head_id
       AND organization_id = head_organization_id
       AND revision = head_revision
       AND current_record_id IS NOT DISTINCT FROM head_current_record_id;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 1 THEN
        RAISE EXCEPTION 'evidence head compare-and-swap failed'
            USING ERRCODE = '40001';
    END IF;
    RETURN head_revision + 1;
END;
$$
"""


_FUNCTION_SPECS = (
    (
        "archie_guard_command_envelope_insert",
        _COMMAND_ENVELOPE_INSERT_GUARD_SQL,
    ),
    (
        "archie_guard_overlap_disposition_insert",
        _OVERLAP_DISPOSITION_INSERT_GUARD_SQL,
    ),
    (
        "archie_reject_transformation_mutation",
        _IMMUTABILITY_FUNCTION_SQL,
    ),
    (
        "archie_guard_decision_citation_membership",
        _DECISION_CITATION_MEMBERSHIP_SQL,
    ),
    ("archie_guard_transformation_receipt", _RECEIPT_FUNCTION_SQL),
    ("archie_guard_evidence_head", _EVIDENCE_HEAD_GUARD_SQL),
    ("archie_guard_evidence_event_binding", _EVIDENCE_EVENT_BINDING_SQL),
)

_COMMAND_ENVELOPE_INSERT_TRIGGER_SPECS = (
    (
        "command_materialisations",
        "trg_command_materialisation_insert_guard",
    ),
    (
        "operation_results",
        "trg_transformation_result_insert_guard",
    ),
    (
        "transformation_outbox_events",
        "trg_transformation_outbox_insert_guard",
    ),
)

_OVERLAP_DISPOSITION_INSERT_TRIGGER_SPEC = (
    "candidate_overlap_dispositions",
    "trg_candidate_overlap_disposition_insert_guard",
)

_TRIGGER_SPECS = (
    (
        "command_materialisations",
        "trg_command_materialisation_immutable",
        "archie_reject_transformation_mutation",
    ),
    (
        "operation_results",
        "trg_transformation_result_immutable",
        "archie_reject_transformation_mutation",
    ),
    (
        "transformation_outbox_events",
        "trg_transformation_outbox_immutable",
        "archie_reject_transformation_mutation",
    ),
    (
        "command_idempotency_records",
        "trg_transformation_receipt_guard",
        "archie_guard_transformation_receipt",
    ),
    (
        "candidate_signals",
        "trg_candidate_signal_immutable",
        "archie_reject_transformation_mutation",
    ),
    (
        "candidate_overlap_dispositions",
        "trg_candidate_overlap_disposition_immutable",
        "archie_reject_transformation_mutation",
    ),
    (
        "evidence_records",
        "trg_evidence_record_immutable",
        "archie_reject_transformation_mutation",
    ),
    (
        "evidence_head_events",
        "trg_evidence_event_immutable",
        "archie_reject_transformation_mutation",
    ),
    (
        "evidence_claim_heads",
        "trg_evidence_head_guard",
        "archie_guard_evidence_head",
    ),
    (
        "transformation_option_versions",
        "trg_transformation_option_version_immutable",
        "archie_reject_transformation_mutation",
    ),
    (
        "decision_brief_versions",
        "trg_decision_brief_version_immutable",
        "archie_reject_transformation_mutation",
    ),
    (
        "decision_brief_option_citations",
        "trg_decision_brief_option_citation_immutable",
        "archie_reject_transformation_mutation",
    ),
    (
        "decision_brief_evidence_citations",
        "trg_decision_brief_evidence_citation_immutable",
        "archie_reject_transformation_mutation",
    ),
    (
        "decision_events",
        "trg_decision_event_immutable",
        "archie_reject_transformation_mutation",
    ),
)

_EVIDENCE_EVENT_BINDING_TRIGGER = "trg_evidence_event_binding"

_CITATION_INSERT_TRIGGER_SPECS = (
    (
        "decision_brief_option_citations",
        "trg_decision_brief_option_citation_membership",
    ),
    (
        "decision_brief_evidence_citations",
        "trg_decision_brief_evidence_citation_membership",
    ),
)


_COMMAND_TABLES = tuple(PROTECTED_RUNTIME_TABLE_PRIVILEGES)


def _normalise_function_body(body: str) -> str:
    return "\n".join(line.rstrip() for line in body.strip().splitlines())


def _expected_function_body(create_sql: str) -> str:
    body = create_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    # ``exec_driver_sql`` passes percent signs through psycopg2's paramstyle;
    # doubled literals are stored by PostgreSQL as the intended single sign.
    return _normalise_function_body(body.replace("%%", "%"))


def _guard_schema(connection) -> tuple[str, str]:
    schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    return schema, connection.dialect.identifier_preparer.quote(schema)


logger = logging.getLogger(__name__)


_GEN_RANDOM_UUID_LOOKUP_SQL = """
        SELECT namespace.nspname
        FROM pg_proc proc
        JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
        LEFT JOIN pg_depend dependency
          ON dependency.classid = 'pg_proc'::regclass
         AND dependency.objid = proc.oid
         AND dependency.refclassid = 'pg_extension'::regclass
         AND dependency.deptype = 'e'
        LEFT JOIN pg_extension extension ON extension.oid = dependency.refobjid
        WHERE proc.proname = 'gen_random_uuid'
          AND proc.pronargs = 0
          AND proc.prorettype = 'uuid'::regtype
          AND (namespace.nspname = 'pg_catalog' OR extension.extname = 'pgcrypto')
        ORDER BY (namespace.nspname = 'pg_catalog') DESC,
                 namespace.nspname
        LIMIT 1
        """


def _render_guard_sql(connection, create_sql: str, quoted_schema: str) -> str:
    rendered = create_sql.replace("public.", f"{quoted_schema}.").replace(
        "SET search_path = pg_catalog, public",
        f"SET search_path = pg_catalog, {quoted_schema}",
    )
    if "__ARCHIE_GEN_RANDOM_UUID__" not in rendered:
        return rendered
    uuid_schema = connection.exec_driver_sql(
        _GEN_RANDOM_UUID_LOOKUP_SQL
    ).scalar_one_or_none()
    if uuid_schema is None:
        # Try to provide it before giving up. gen_random_uuid() is built into
        # PostgreSQL 13+; on 12 and earlier it comes from pgcrypto.
        #
        # Until 31 Aug 2026 this raised immediately, so `flask init-db` on a
        # FRESH database died after creating one table, with an error that did
        # not say what to do about it. That is the exact command CONTRIBUTING.md
        # tells a new contributor to run, which makes it a first-five-minutes
        # wall for anyone cloning the project. The maintainer never saw it
        # because pgcrypto was already present in their template1.
        try:
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            logger.info("installed the pgcrypto extension for gen_random_uuid()")
            uuid_schema = connection.exec_driver_sql(
                _GEN_RANDOM_UUID_LOOKUP_SQL
            ).scalar_one_or_none()
        except Exception as exc:  # no permission to CREATE EXTENSION, usually
            logger.warning("could not install pgcrypto automatically: %s", exc)

    if uuid_schema is None:
        raise RuntimeError(
            "PostgreSQL gen_random_uuid() is required and is not available." "\n"
            "  * PostgreSQL 13 or newer provides it natively -- check the "
            "server version with: SELECT version();" "\n"
            "  * On PostgreSQL 12 or older, or wherever it is missing, run "
            "this once as a superuser against the target database:" "\n"
            "        CREATE EXTENSION IF NOT EXISTS pgcrypto;" "\n"
            "Entelim tried to create the extension itself and could not, "
            "which usually means the connecting role lacks permission."
        )
    preparer = connection.dialect.identifier_preparer
    qualified_uuid = (
        f"{preparer.quote(uuid_schema)}.{preparer.quote('gen_random_uuid')}"
    )
    return rendered.replace("__ARCHIE_GEN_RANDOM_UUID__", qualified_uuid)


def _qualified_name(connection, quoted_schema: str, object_name: str) -> str:
    return (
        f"{quoted_schema}."
        f"{connection.dialect.identifier_preparer.quote(object_name)}"
    )


def _configured_capability_secrets(
    explicit: tuple[str, ...] | None = None,
) -> tuple[tuple[str, bytes], ...]:
    if explicit is None:
        if has_app_context():
            current = str(
                current_app.config.get(COMMAND_CAPABILITY_SECRET_ENV, "") or ""
            )
            previous = str(
                current_app.config.get(
                    COMMAND_CAPABILITY_PREVIOUS_SECRETS_ENV, ""
                )
                or ""
            )
        else:
            current = os.environ.get(COMMAND_CAPABILITY_SECRET_ENV, "")
            previous = os.environ.get(
                COMMAND_CAPABILITY_PREVIOUS_SECRETS_ENV, ""
            )
        values = (current, *(item for item in previous.split(",") if item.strip()))
    else:
        values = explicit
    decoded: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        try:
            secret = bytes.fromhex(normalized)
        except ValueError as error:
            raise ValueError(
                f"{COMMAND_CAPABILITY_SECRET_ENV} must contain hexadecimal bytes"
            ) from error
        if len(secret) < 32:
            raise ValueError(
                f"{COMMAND_CAPABILITY_SECRET_ENV} must contain at least 32 bytes"
            )
        key_id = hashlib.sha256(secret).hexdigest()
        if key_id not in seen:
            decoded.append((key_id, secret))
            seen.add(key_id)
    if not decoded:
        raise ValueError(f"{COMMAND_CAPABILITY_SECRET_ENV} is required")
    return tuple(decoded)


def _provision_capability_keys(
    connection,
    quoted_schema: str,
    secrets: tuple[tuple[str, bytes], ...],
) -> None:
    connection.exec_driver_sql(
        _render_guard_sql(connection, _COMMAND_CAPABILITY_TABLE_SQL, quoted_schema)
    )
    qualified_keys = _qualified_name(
        connection, quoted_schema, "archie_command_capability_keys"
    )
    connection.exec_driver_sql(f"REVOKE ALL ON TABLE {qualified_keys} FROM PUBLIC")
    active_ids = []
    for key_id, secret in secrets:
        active_ids.append(key_id)
        connection.exec_driver_sql(
            f"INSERT INTO {qualified_keys} (key_id, secret, active, retired_at) "  # nosec B608 -- schema and literal table name are dialect-quoted; values are bound
            "VALUES (%s, %s, TRUE, NULL) "
            "ON CONFLICT (key_id) DO UPDATE "
            "SET secret = EXCLUDED.secret, active = TRUE, retired_at = NULL",
            (key_id, secret),
        )
    connection.exec_driver_sql(
        f"UPDATE {qualified_keys} SET active = FALSE, "  # nosec B608 -- schema and literal table name are dialect-quoted; values are bound
        "retired_at = COALESCE(retired_at, clock_timestamp()) "
        "WHERE active IS TRUE AND NOT (key_id = ANY(%s))",
        (active_ids,),
    )


def _provision_claim_challenges(connection, quoted_schema: str) -> None:
    connection.exec_driver_sql(
        _render_guard_sql(
            connection, _COMMAND_CLAIM_CHALLENGE_TABLE_SQL, quoted_schema
        )
    )
    qualified_challenges = _qualified_name(
        connection, quoted_schema, "archie_command_claim_challenges"
    )
    connection.exec_driver_sql(
        f"REVOKE ALL ON TABLE {qualified_challenges} FROM PUBLIC"
    )


def inspect_transformation_db_guards(connection) -> list[str]:
    """Return semantic guard drift without changing database state."""
    if connection.dialect.name != "postgresql":
        return []
    schema, quoted_schema = _guard_schema(connection)
    expected_search_path = f"search_path=pg_catalog, {quoted_schema}"
    drift: list[str] = []
    qualified_keys = _qualified_name(
        connection, quoted_schema, "archie_command_capability_keys"
    )
    if not connection.exec_driver_sql(
        "SELECT to_regclass(%s) IS NOT NULL", (qualified_keys,)
    ).scalar():
        drift.append("table_missing:archie_command_capability_keys")
    qualified_challenges = _qualified_name(
        connection, quoted_schema, "archie_command_claim_challenges"
    )
    if not connection.exec_driver_sql(
        "SELECT to_regclass(%s) IS NOT NULL", (qualified_challenges,)
    ).scalar():
        drift.append("table_missing:archie_command_claim_challenges")
    for function_name, create_sql in _FUNCTION_SPECS:
        row = connection.exec_driver_sql(
            """
            SELECT proc.prosrc, proc.prosecdef, proc.proconfig
            FROM pg_proc proc
            JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
            WHERE namespace.nspname = %s
              AND proc.proname = %s
              AND proc.pronargs = 0
              AND proc.prorettype = 'trigger'::regtype
            """,
            (schema, function_name),
        ).first()
        if row is None:
            drift.append(f"function_missing:{function_name}")
            continue
        rendered_sql = _render_guard_sql(connection, create_sql, quoted_schema)
        if _normalise_function_body(row.prosrc) != _expected_function_body(rendered_sql):
            drift.append(f"function_body:{function_name}")
        if row.prosecdef is not True:
            drift.append(f"function_security:{function_name}")
        if expected_search_path not in (row.proconfig or []):
            drift.append(f"function_search_path:{function_name}")

    for function_name, create_sql, argument_count, return_type in (
        ("archie_hmac_sha256", _HMAC_SHA256_FUNCTION_SQL, 2, "bytea"),
        (
            "archie_verify_command_capability",
            _VERIFY_COMMAND_CAPABILITY_SQL,
            3,
            "jsonb",
        ),
        (
            "archie_issue_command_claim_challenge",
            _ISSUE_COMMAND_CLAIM_CHALLENGE_SQL,
            3,
            "record",
        ),
        ("archie_claim_transformation_command", _CLAIM_COMMAND_SQL, 2, "record"),
        (
            "archie_persist_command_envelope",
            _PERSIST_COMMAND_ENVELOPE_SQL,
            3,
            "bigint",
        ),
        (
            "archie_persist_overlap_disposition",
            _PERSIST_OVERLAP_DISPOSITION_SQL,
            3,
            "bigint",
        ),
        (
            "archie_repair_command_envelope",
            _REPAIR_COMMAND_ENVELOPE_SQL,
            3,
            "bigint",
        ),
        ("archie_create_decision_brief", _DECISION_BRIEF_CREATE_SQL, 3, "record"),
    ):
        row = connection.exec_driver_sql(
            """
            SELECT proc.prosrc, proc.prosecdef, proc.proconfig
            FROM pg_proc proc
            JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
            WHERE namespace.nspname = %s
              AND proc.proname = %s
              AND proc.pronargs = %s
              AND proc.prorettype = %s::regtype
            """,
            (schema, function_name, argument_count, return_type),
        ).first()
        if row is None:
            drift.append(f"function_missing:{function_name}")
            continue
        rendered = _render_guard_sql(connection, create_sql, quoted_schema)
        if _normalise_function_body(row.prosrc) != _expected_function_body(rendered):
            drift.append(f"function_body:{function_name}")
        if row.prosecdef is not True:
            drift.append(f"function_security:{function_name}")
        if expected_search_path not in (row.proconfig or []):
            drift.append(f"function_search_path:{function_name}")

    advance = connection.exec_driver_sql(
        """
        SELECT proc.prosrc, proc.prosecdef, proc.proconfig
        FROM pg_proc proc
        JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
        WHERE namespace.nspname = %s
          AND proc.proname = 'archie_advance_evidence_head'
          AND proc.pronargs = 7
          AND proc.prorettype = 'integer'::regtype
        """,
        (schema,),
    ).first()
    if advance is None:
        drift.append("function_missing:archie_advance_evidence_head")
    else:
        if _normalise_function_body(advance.prosrc) != _expected_function_body(
            _render_guard_sql(connection, _EVIDENCE_HEAD_ADVANCE_SQL, quoted_schema)
        ):
            drift.append("function_body:archie_advance_evidence_head")
        if advance.prosecdef is not True:
            drift.append("function_security:archie_advance_evidence_head")
        if expected_search_path not in (advance.proconfig or []):
            drift.append("function_search_path:archie_advance_evidence_head")

    freeze_brief = connection.exec_driver_sql(
        """
        SELECT proc.prosrc, proc.prosecdef, proc.proconfig
        FROM pg_proc proc
        JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
        WHERE namespace.nspname = %s
          AND proc.proname = 'archie_freeze_decision_brief_version'
          AND proc.pronargs = 11
          AND proc.prorettype = 'record'::regtype
        """,
        (schema,),
    ).first()
    if freeze_brief is None:
        drift.append("function_missing:archie_freeze_decision_brief_version")
    else:
        rendered_freeze = _render_guard_sql(
            connection, _DECISION_BRIEF_FREEZE_SQL, quoted_schema
        )
        if _normalise_function_body(freeze_brief.prosrc) != _expected_function_body(
            rendered_freeze
        ):
            drift.append("function_body:archie_freeze_decision_brief_version")
        if freeze_brief.prosecdef is not True:
            drift.append("function_security:archie_freeze_decision_brief_version")
        if expected_search_path not in (freeze_brief.proconfig or []):
            drift.append("function_search_path:archie_freeze_decision_brief_version")

    for table_name, trigger_name, function_name in _TRIGGER_SPECS:
        qualified_table = _qualified_name(connection, quoted_schema, table_name)
        table_present = connection.exec_driver_sql(
            "SELECT to_regclass(%s) IS NOT NULL", (qualified_table,)
        ).scalar()
        if not table_present:
            continue
        row = connection.exec_driver_sql(
            """
            SELECT trigger.tgenabled, trigger.tgtype,
                   trigger.tgqual IS NOT NULL AS has_when,
                   trigger.tgattr::text AS update_columns,
                   namespace.nspname AS function_schema,
                   proc.proname AS function_name
            FROM pg_trigger trigger
            JOIN pg_proc proc ON proc.oid = trigger.tgfoid
            JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
            WHERE trigger.tgname = %s
              AND trigger.tgrelid = to_regclass(%s)
              AND NOT trigger.tgisinternal
            """,
            (trigger_name, qualified_table),
        ).first()
        if row is None:
            drift.append(f"trigger_missing:{trigger_name}")
            continue
        if row.tgenabled != "O":
            drift.append(f"trigger_disabled:{trigger_name}")
        if row.function_schema != schema or row.function_name != function_name:
            drift.append(f"trigger_function:{trigger_name}")
        # Evidence heads also guard INSERT so they can only start empty at revision 0.
        expected_tgtype = 31 if table_name == "evidence_claim_heads" else 27
        if row.tgtype != expected_tgtype:
            drift.append(f"trigger_shape:{trigger_name}")
        if row.has_when:
            drift.append(f"trigger_when:{trigger_name}")
        if row.update_columns:
            drift.append(f"trigger_columns:{trigger_name}")

    insert_guard_specs = (
        *(
            (table_name, trigger_name, "archie_guard_command_envelope_insert")
            for table_name, trigger_name in _COMMAND_ENVELOPE_INSERT_TRIGGER_SPECS
        ),
        (*_OVERLAP_DISPOSITION_INSERT_TRIGGER_SPEC,
         "archie_guard_overlap_disposition_insert"),
    )
    for table_name, trigger_name, expected_function in insert_guard_specs:
        qualified_table = _qualified_name(connection, quoted_schema, table_name)
        if not connection.exec_driver_sql(
            "SELECT to_regclass(%s) IS NOT NULL", (qualified_table,)
        ).scalar():
            continue
        row = connection.exec_driver_sql(
            """
            SELECT trigger.tgenabled, trigger.tgtype,
                   trigger.tgqual IS NOT NULL AS has_when,
                   trigger.tgattr::text AS update_columns,
                   namespace.nspname AS function_schema,
                   proc.proname AS function_name
            FROM pg_trigger trigger
            JOIN pg_proc proc ON proc.oid = trigger.tgfoid
            JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
            WHERE trigger.tgname = %s
              AND trigger.tgrelid = to_regclass(%s)
              AND NOT trigger.tgisinternal
            """,
            (trigger_name, qualified_table),
        ).first()
        if row is None:
            drift.append(f"trigger_missing:{trigger_name}")
            continue
        if (
            row.tgenabled != "O"
            or row.tgtype != 7
            or row.has_when
            or row.update_columns
            or row.function_schema != schema
            or row.function_name != expected_function
        ):
            drift.append(f"trigger_shape:{trigger_name}")

    for table_name, trigger_name in _CITATION_INSERT_TRIGGER_SPECS:
        qualified_table = _qualified_name(connection, quoted_schema, table_name)
        if not connection.exec_driver_sql(
            "SELECT to_regclass(%s) IS NOT NULL", (qualified_table,)
        ).scalar():
            continue
        row = connection.exec_driver_sql(
            """
            SELECT trigger.tgenabled, trigger.tgtype,
                   trigger.tgqual IS NOT NULL AS has_when,
                   trigger.tgattr::text AS update_columns,
                   namespace.nspname AS function_schema,
                   proc.proname AS function_name
            FROM pg_trigger trigger
            JOIN pg_proc proc ON proc.oid = trigger.tgfoid
            JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
            WHERE trigger.tgname = %s
              AND trigger.tgrelid = to_regclass(%s)
              AND NOT trigger.tgisinternal
            """,
            (trigger_name, qualified_table),
        ).first()
        if row is None:
            drift.append(f"trigger_missing:{trigger_name}")
            continue
        if (
            row.tgenabled != "O"
            or row.tgtype != 7
            or row.has_when
            or row.update_columns
            or row.function_schema != schema
            or row.function_name != "archie_guard_decision_citation_membership"
        ):
            drift.append(f"trigger_shape:{trigger_name}")

    qualified_events = _qualified_name(
        connection, quoted_schema, "evidence_head_events"
    )
    if connection.exec_driver_sql(
        "SELECT to_regclass(%s) IS NOT NULL", (qualified_events,)
    ).scalar():
        row = connection.exec_driver_sql(
            """
            SELECT trigger.tgenabled, trigger.tgtype, trigger.tgdeferrable,
                   trigger.tginitdeferred,
                   trigger.tgqual IS NOT NULL AS has_when,
                   trigger.tgattr::text AS update_columns,
                   namespace.nspname AS function_schema,
                   proc.proname AS function_name
            FROM pg_trigger trigger
            JOIN pg_proc proc ON proc.oid = trigger.tgfoid
            JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
            WHERE trigger.tgname = %s
              AND trigger.tgrelid = to_regclass(%s)
              AND NOT trigger.tgisinternal
            """,
            (_EVIDENCE_EVENT_BINDING_TRIGGER, qualified_events),
        ).first()
        if row is None:
            drift.append(
                f"trigger_missing:{_EVIDENCE_EVENT_BINDING_TRIGGER}"
            )
        else:
            if row.tgenabled != "O":
                drift.append(
                    f"trigger_disabled:{_EVIDENCE_EVENT_BINDING_TRIGGER}"
                )
            if (
                row.function_schema != schema
                or row.function_name != "archie_guard_evidence_event_binding"
            ):
                drift.append(
                    f"trigger_function:{_EVIDENCE_EVENT_BINDING_TRIGGER}"
                )
            if (
                row.tgtype != 5
                or not row.tgdeferrable
                or not row.tginitdeferred
            ):
                drift.append(
                    f"trigger_shape:{_EVIDENCE_EVENT_BINDING_TRIGGER}"
                )
            if row.has_when:
                drift.append(f"trigger_when:{_EVIDENCE_EVENT_BINDING_TRIGGER}")
            if row.update_columns:
                drift.append(
                    f"trigger_columns:{_EVIDENCE_EVENT_BINDING_TRIGGER}"
                )
    return drift


def _repair_triggers(connection, schema: str, quoted_schema: str) -> None:
    for table_name, trigger_name, function_name in _TRIGGER_SPECS:
        qualified_table = _qualified_name(connection, quoted_schema, table_name)
        qualified_function = _qualified_name(connection, quoted_schema, function_name)
        quoted_trigger = connection.dialect.identifier_preparer.quote(trigger_name)
        table_present = connection.exec_driver_sql(
            "SELECT to_regclass(%s) IS NOT NULL", (qualified_table,)
        ).scalar()
        if not table_present:
            continue
        row = connection.exec_driver_sql(
            """
            SELECT trigger.tgenabled, trigger.tgtype,
                   trigger.tgqual IS NOT NULL AS has_when,
                   trigger.tgattr::text AS update_columns,
                   namespace.nspname AS function_schema,
                   proc.proname AS function_name
            FROM pg_trigger trigger
            JOIN pg_proc proc ON proc.oid = trigger.tgfoid
            JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
            WHERE trigger.tgname = %s
              AND trigger.tgrelid = to_regclass(%s)
              AND NOT trigger.tgisinternal
            """,
            (trigger_name, qualified_table),
        ).first()
        expected_tgtype = 31 if table_name == "evidence_claim_heads" else 27
        correct = (
            row is not None
            and row.tgenabled == "O"
            and row.tgtype == expected_tgtype
            and not row.has_when
            and not row.update_columns
            and row.function_schema == schema
            and row.function_name == function_name
        )
        if correct:
            continue
        connection.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS {quoted_trigger} ON {qualified_table}"
        )
        events = (
            "INSERT OR UPDATE OR DELETE"
            if table_name == "evidence_claim_heads"
            else "UPDATE OR DELETE"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {quoted_trigger} "
            f"BEFORE {events} ON {qualified_table} "
            "FOR EACH ROW "
            f"EXECUTE FUNCTION {qualified_function}()"
        )

    insert_guard_specs = (
        *(
            (table_name, trigger_name, "archie_guard_command_envelope_insert")
            for table_name, trigger_name in _COMMAND_ENVELOPE_INSERT_TRIGGER_SPECS
        ),
        (*_OVERLAP_DISPOSITION_INSERT_TRIGGER_SPEC,
         "archie_guard_overlap_disposition_insert"),
    )
    for table_name, trigger_name, expected_function in insert_guard_specs:
        qualified_table = _qualified_name(connection, quoted_schema, table_name)
        qualified_function = _qualified_name(
            connection, quoted_schema, expected_function
        )
        quoted_trigger = connection.dialect.identifier_preparer.quote(trigger_name)
        if not connection.exec_driver_sql(
            "SELECT to_regclass(%s) IS NOT NULL", (qualified_table,)
        ).scalar():
            continue
        row = connection.exec_driver_sql(
            """
            SELECT trigger.tgenabled, trigger.tgtype,
                   trigger.tgqual IS NOT NULL AS has_when,
                   trigger.tgattr::text AS update_columns,
                   namespace.nspname AS function_schema,
                   proc.proname AS function_name
            FROM pg_trigger trigger
            JOIN pg_proc proc ON proc.oid = trigger.tgfoid
            JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
            WHERE trigger.tgname = %s
              AND trigger.tgrelid = to_regclass(%s)
              AND NOT trigger.tgisinternal
            """,
            (trigger_name, qualified_table),
        ).first()
        correct = (
            row is not None
            and row.tgenabled == "O"
            and row.tgtype == 7
            and not row.has_when
            and not row.update_columns
            and row.function_schema == schema
            and row.function_name == expected_function
        )
        if correct:
            continue
        connection.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS {quoted_trigger} ON {qualified_table}"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {quoted_trigger} "
            f"BEFORE INSERT ON {qualified_table} FOR EACH ROW "
            f"EXECUTE FUNCTION {qualified_function}()"
        )

    for table_name, trigger_name in _CITATION_INSERT_TRIGGER_SPECS:
        qualified_table = _qualified_name(connection, quoted_schema, table_name)
        qualified_function = _qualified_name(
            connection, quoted_schema, "archie_guard_decision_citation_membership"
        )
        quoted_trigger = connection.dialect.identifier_preparer.quote(trigger_name)
        if not connection.exec_driver_sql(
            "SELECT to_regclass(%s) IS NOT NULL", (qualified_table,)
        ).scalar():
            continue
        row = connection.exec_driver_sql(
            """
            SELECT trigger.tgenabled, trigger.tgtype,
                   trigger.tgqual IS NOT NULL AS has_when,
                   trigger.tgattr::text AS update_columns,
                   namespace.nspname AS function_schema,
                   proc.proname AS function_name
            FROM pg_trigger trigger
            JOIN pg_proc proc ON proc.oid = trigger.tgfoid
            JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
            WHERE trigger.tgname = %s
              AND trigger.tgrelid = to_regclass(%s)
              AND NOT trigger.tgisinternal
            """,
            (trigger_name, qualified_table),
        ).first()
        correct = (
            row is not None
            and row.tgenabled == "O"
            and row.tgtype == 7
            and not row.has_when
            and not row.update_columns
            and row.function_schema == schema
            and row.function_name == "archie_guard_decision_citation_membership"
        )
        if correct:
            continue
        connection.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS {quoted_trigger} ON {qualified_table}"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {quoted_trigger} BEFORE INSERT ON {qualified_table} "
            "FOR EACH ROW EXECUTE FUNCTION "
            f"{qualified_function}()"
        )

    qualified_events = _qualified_name(
        connection, quoted_schema, "evidence_head_events"
    )
    if not connection.exec_driver_sql(
        "SELECT to_regclass(%s) IS NOT NULL", (qualified_events,)
    ).scalar():
        return
    row = connection.exec_driver_sql(
        """
        SELECT trigger.tgenabled, trigger.tgtype, trigger.tgdeferrable,
               trigger.tginitdeferred,
               trigger.tgqual IS NOT NULL AS has_when,
               trigger.tgattr::text AS update_columns,
               namespace.nspname AS function_schema,
               proc.proname AS function_name
        FROM pg_trigger trigger
        JOIN pg_proc proc ON proc.oid = trigger.tgfoid
        JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
        WHERE trigger.tgname = %s
          AND trigger.tgrelid = to_regclass(%s)
          AND NOT trigger.tgisinternal
        """,
        (_EVIDENCE_EVENT_BINDING_TRIGGER, qualified_events),
    ).first()
    correct = (
        row is not None
        and row.tgenabled == "O"
        and row.tgtype == 5
        and row.tgdeferrable
        and row.tginitdeferred
        and not row.has_when
        and not row.update_columns
        and row.function_schema == schema
        and row.function_name == "archie_guard_evidence_event_binding"
    )
    if not correct:
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS trg_evidence_event_binding "
            f"ON {qualified_events}"
        )
        qualified_function = _qualified_name(
            connection, quoted_schema, "archie_guard_evidence_event_binding"
        )
        connection.exec_driver_sql(
            "CREATE CONSTRAINT TRIGGER trg_evidence_event_binding "
            f"AFTER INSERT ON {qualified_events} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            f"EXECUTE FUNCTION {qualified_function}()"
        )


def ensure_transformation_db_guards(
    connection,
    *,
    runtime_role: str = TRANSFORMATION_RUNTIME_ROLE,
    capability_secrets: tuple[str, ...] | None = None,
):
    """Install/refresh guards once under a transaction-scoped advisory lock."""
    if connection.dialect.name != "postgresql":
        return
    schema, quoted_schema = _guard_schema(connection)
    connection.exec_driver_sql(
        "SELECT pg_advisory_xact_lock(hashtext(%s))",
        (f"archie_transformation_command_db_guards:{schema}",),
    )
    configured_secrets = _configured_capability_secrets(capability_secrets)
    _provision_capability_keys(connection, quoted_schema, configured_secrets)
    _provision_claim_challenges(connection, quoted_schema)
    legacy_freeze = _qualified_name(
        connection, quoted_schema, "archie_freeze_decision_brief_version"
    )
    connection.exec_driver_sql(
        f"DROP FUNCTION IF EXISTS {legacy_freeze}"
        "(bigint, bigint, bigint, integer, text, integer, jsonb)"
    )
    connection.exec_driver_sql(
        f"DROP FUNCTION IF EXISTS {legacy_freeze}"
        "(bigint, bigint, bigint, integer, text, text, text, integer, jsonb)"
    )
    legacy_canonical = _qualified_name(
        connection, quoted_schema, "archie_canonical_jsonb"
    )
    connection.exec_driver_sql(
        f"DROP FUNCTION IF EXISTS {legacy_canonical}(jsonb)"
    )
    for create_sql in (
        _HMAC_SHA256_FUNCTION_SQL,
        _VERIFY_COMMAND_CAPABILITY_SQL,
        _ISSUE_COMMAND_CLAIM_CHALLENGE_SQL,
        _CLAIM_COMMAND_SQL,
        _COMMAND_ENVELOPE_INSERT_GUARD_SQL,
        _PERSIST_COMMAND_ENVELOPE_SQL,
        _REPAIR_COMMAND_ENVELOPE_SQL,
        _OVERLAP_DISPOSITION_INSERT_GUARD_SQL,
        _PERSIST_OVERLAP_DISPOSITION_SQL,
        _IMMUTABILITY_FUNCTION_SQL,
        _DECISION_CITATION_MEMBERSHIP_SQL,
        _DECISION_BRIEF_CREATE_SQL,
        _DECISION_BRIEF_FREEZE_SQL,
        _RECEIPT_FUNCTION_SQL,
        _EVIDENCE_HEAD_GUARD_SQL,
        _EVIDENCE_EVENT_BINDING_SQL,
        _EVIDENCE_HEAD_ADVANCE_SQL,
    ):
        connection.exec_driver_sql(
            _render_guard_sql(connection, create_sql, quoted_schema)
        )
    qualified_receipt_guard = _qualified_name(
        connection, quoted_schema, "archie_guard_transformation_receipt"
    )
    connection.exec_driver_sql(
        f"ALTER FUNCTION {qualified_receipt_guard}() OWNER TO CURRENT_USER"
    )
    trigger_functions = (
        "archie_guard_command_envelope_insert",
        "archie_guard_overlap_disposition_insert",
        "archie_reject_transformation_mutation",
        "archie_guard_decision_citation_membership",
        "archie_guard_transformation_receipt",
        "archie_guard_evidence_head",
        "archie_guard_evidence_event_binding",
    )
    for function_name in trigger_functions:
        qualified_function = _qualified_name(
            connection, quoted_schema, function_name
        )
        connection.exec_driver_sql(
            f"REVOKE ALL ON FUNCTION {qualified_function}() FROM PUBLIC"
        )
    qualified_advance = _qualified_name(
        connection, quoted_schema, "archie_advance_evidence_head"
    )
    qualified_freeze = _qualified_name(
        connection, quoted_schema, "archie_freeze_decision_brief_version"
    )
    qualified_create_brief = _qualified_name(
        connection, quoted_schema, "archie_create_decision_brief"
    )
    qualified_hmac = _qualified_name(
        connection, quoted_schema, "archie_hmac_sha256"
    )
    qualified_verify_capability = _qualified_name(
        connection, quoted_schema, "archie_verify_command_capability"
    )
    qualified_claim = _qualified_name(
        connection, quoted_schema, "archie_claim_transformation_command"
    )
    qualified_issue_challenge = _qualified_name(
        connection, quoted_schema, "archie_issue_command_claim_challenge"
    )
    qualified_persist_envelope = _qualified_name(
        connection, quoted_schema, "archie_persist_command_envelope"
    )
    qualified_repair_envelope = _qualified_name(
        connection, quoted_schema, "archie_repair_command_envelope"
    )
    qualified_persist_overlap = _qualified_name(
        connection, quoted_schema, "archie_persist_overlap_disposition"
    )
    advance_signature = (
        "bigint, bigint, integer, bigint, bigint, integer, text"
    )
    freeze_signature = (
        "bigint, bigint, bigint, integer, text, text, text, integer, text, jsonb, text"
    )
    claim_signature = "text, text"
    create_brief_signature = "text, text, text"
    connection.exec_driver_sql(
        f"REVOKE ALL ON FUNCTION {qualified_hmac}(bytea, bytea) FROM PUBLIC"
    )
    connection.exec_driver_sql(
        f"REVOKE ALL ON FUNCTION {qualified_verify_capability}(text, text, text) "
        "FROM PUBLIC"
    )
    connection.exec_driver_sql(
        f"REVOKE ALL ON FUNCTION {qualified_claim}({claim_signature}) FROM PUBLIC"
    )
    connection.exec_driver_sql(
        f"REVOKE ALL ON FUNCTION {qualified_issue_challenge}"
        "(bigint, bigint, integer) FROM PUBLIC"
    )
    connection.exec_driver_sql(
        f"REVOKE ALL ON FUNCTION {qualified_persist_envelope}(text, text, text) "
        "FROM PUBLIC"
    )
    connection.exec_driver_sql(
        f"REVOKE ALL ON FUNCTION {qualified_repair_envelope}(text, text, bigint) "
        "FROM PUBLIC"
    )
    connection.exec_driver_sql(
        f"REVOKE ALL ON FUNCTION {qualified_persist_overlap}(text, text, text) "
        "FROM PUBLIC"
    )
    connection.exec_driver_sql(
        f"REVOKE ALL ON FUNCTION {qualified_advance}({advance_signature}) FROM PUBLIC"
    )
    connection.exec_driver_sql(
        f"REVOKE ALL ON FUNCTION {qualified_freeze}({freeze_signature}) FROM PUBLIC"
    )
    connection.exec_driver_sql(
        f"REVOKE ALL ON FUNCTION {qualified_create_brief}({create_brief_signature}) "
        "FROM PUBLIC"
    )
    legacy_insert = _qualified_name(
        connection, quoted_schema, "archie_insert_decision_brief_citations"
    )
    connection.exec_driver_sql(
        f"DROP FUNCTION IF EXISTS {legacy_insert}"
        "(bigint, bigint, bigint, integer, text)"
    )
    runtime_role_exists = connection.exec_driver_sql(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
        (runtime_role,),
    ).scalar()
    runtime_role_identifier = connection.dialect.identifier_preparer.quote(
        runtime_role
    )
    if runtime_role_exists:
        qualified_keys = _qualified_name(
            connection, quoted_schema, "archie_command_capability_keys"
        )
        qualified_challenges = _qualified_name(
            connection, quoted_schema, "archie_command_claim_challenges"
        )
        connection.exec_driver_sql(
            f"REVOKE ALL ON TABLE {qualified_keys} FROM {runtime_role_identifier}"
        )
        connection.exec_driver_sql(
            f"REVOKE ALL ON TABLE {qualified_challenges} "
            f"FROM {runtime_role_identifier}"
        )
        for function_name in trigger_functions:
            qualified_function = _qualified_name(
                connection, quoted_schema, function_name
            )
            connection.exec_driver_sql(
                f"REVOKE ALL ON FUNCTION {qualified_function}() "
                f"FROM {runtime_role_identifier}"
            )
        for function_name, signature in RUNTIME_EXECUTE_FUNCTIONS:
            qualified_function = _qualified_name(
                connection, quoted_schema, function_name
            )
            connection.exec_driver_sql(
                f"GRANT EXECUTE ON FUNCTION {qualified_function}({signature}) "
                f"TO {runtime_role_identifier}"
            )
    _repair_triggers(connection, schema, quoted_schema)
    # The runtime role gets only the columns required by the service protocol.
    # It never owns these objects and has no DELETE or TRUNCATE privilege.
    for table_name in _COMMAND_TABLES:
        qualified_table = _qualified_name(connection, quoted_schema, table_name)
        present = connection.exec_driver_sql(
            "SELECT to_regclass(%s) IS NOT NULL", (qualified_table,)
        ).scalar()
        if present:
            connection.exec_driver_sql(
                f"REVOKE ALL ON TABLE {qualified_table} FROM PUBLIC"
            )
    if runtime_role_exists:
        for table_name in _COMMAND_TABLES:
            qualified_table = _qualified_name(connection, quoted_schema, table_name)
            present = connection.exec_driver_sql(
                "SELECT to_regclass(%s) IS NOT NULL", (qualified_table,)
            ).scalar()
            if present:
                connection.exec_driver_sql(
                    f"REVOKE ALL ON TABLE {qualified_table} "
                    f"FROM {runtime_role_identifier}"
                )
                privileges = ", ".join(
                    PROTECTED_RUNTIME_TABLE_PRIVILEGES[table_name]
                )
                connection.exec_driver_sql(
                    f"GRANT {privileges} ON TABLE {qualified_table} "
                    f"TO {runtime_role_identifier}"
                )
        for table_name, columns in PROTECTED_RUNTIME_UPDATE_COLUMNS.items():
            qualified_table = _qualified_name(
                connection, quoted_schema, table_name
            )
            if connection.exec_driver_sql(
                "SELECT to_regclass(%s) IS NOT NULL", (qualified_table,)
            ).scalar():
                quoted_columns = ", ".join(
                    connection.dialect.identifier_preparer.quote(column)
                    for column in columns
                )
                connection.exec_driver_sql(
                    f"GRANT UPDATE ({quoted_columns}) ON TABLE {qualified_table} "
                    f"TO {runtime_role_identifier}"
                )
        for table_name, privileges in PROTECTED_RUNTIME_TABLE_PRIVILEGES.items():
            qualified_table = _qualified_name(
                connection, quoted_schema, table_name
            )
            if not connection.exec_driver_sql(
                "SELECT to_regclass(%s) IS NOT NULL", (qualified_table,)
            ).scalar():
                continue
            sequence_names = connection.exec_driver_sql(
                """
                SELECT sequence.relname
                FROM pg_class sequence
                JOIN pg_namespace sequence_namespace
                  ON sequence_namespace.oid = sequence.relnamespace
                JOIN pg_depend ownership
                  ON ownership.classid = 'pg_class'::regclass
                 AND ownership.objid = sequence.oid
                 AND ownership.refclassid = 'pg_class'::regclass
                 AND ownership.deptype IN ('a', 'i')
                JOIN pg_class owned_table ON owned_table.oid = ownership.refobjid
                JOIN pg_namespace table_namespace
                  ON table_namespace.oid = owned_table.relnamespace
                WHERE sequence.relkind = 'S'
                  AND sequence_namespace.nspname = %s
                  AND table_namespace.nspname = %s
                  AND owned_table.relname = %s
                ORDER BY sequence.relname
                """,
                (schema, schema, table_name),
            ).scalars().all()
            for sequence_name in sequence_names:
                qualified_sequence = _qualified_name(
                    connection, quoted_schema, sequence_name
                )
                connection.exec_driver_sql(
                    f"REVOKE ALL ON SEQUENCE {qualified_sequence} FROM PUBLIC"
                )
                connection.exec_driver_sql(
                    f"REVOKE ALL ON SEQUENCE {qualified_sequence} "
                    f"FROM {runtime_role_identifier}"
                )
                if "INSERT" in privileges:
                    connection.exec_driver_sql(
                        f"GRANT USAGE, SELECT ON SEQUENCE {qualified_sequence} "
                        f"TO {runtime_role_identifier}"
                    )
    remaining_drift = inspect_transformation_db_guards(connection)
    if remaining_drift:
        raise RuntimeError(
            "transformation database guard repair incomplete: "
            + ", ".join(remaining_drift)
        )


@event.listens_for(CommandIdempotencyRecord.__table__, "after_create")
@event.listens_for(CommandMaterialisation.__table__, "after_create")
@event.listens_for(OperationResult.__table__, "after_create")
@event.listens_for(OperationOutboxEvent.__table__, "after_create")
@event.listens_for(CandidateSignal.__table__, "after_create")
@event.listens_for(CandidateOverlapDisposition.__table__, "after_create")
@event.listens_for(EvidenceRecord.__table__, "after_create")
@event.listens_for(EvidenceClaimHead.__table__, "after_create")
@event.listens_for(EvidenceHeadEvent.__table__, "after_create")
@event.listens_for(TransformationOptionVersion.__table__, "after_create")
@event.listens_for(DecisionBriefVersion.__table__, "after_create")
@event.listens_for(DecisionBriefOptionCitation.__table__, "after_create")
@event.listens_for(DecisionBriefEvidenceCitation.__table__, "after_create")
@event.listens_for(DecisionEvent.__table__, "after_create")
def _install_transformation_guards_after_create(_target, connection, **_kwargs):
    ensure_transformation_db_guards(connection)


__all__ = [
    "TRANSFORMATION_RUNTIME_ROLE",
    "ensure_transformation_db_guards",
    "inspect_transformation_db_guards",
]
