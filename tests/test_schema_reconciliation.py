"""Transformation schema reconciliation for fresh and long-lived databases."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, String, Table, create_engine, inspect, text

from app import db
from app.commands.reconcile_schema import _reconcile
from app.models.transformation_db_guards import inspect_transformation_db_guards
from app.models.transformation_execution import (
    inspect_execution_history_immutability,
)


@pytest.fixture
def pre_feature_transformation_schema(app):
    """Real pre-Task-1 tables in an isolated PostgreSQL schema.

    Unlike the old synthetic-column test, these are the actual table names and
    legacy columns/FKs.  A dedicated search_path lets the production reconciler
    operate unchanged without touching the shared public test schema.
    """
    schema = f"test_transformation_pre_{uuid.uuid4().hex[:12]}"
    with app.app_context():
        public_engine = db.engine
        with public_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(
                text(
                    f"""
                    CREATE TABLE "{schema}".organizations (
                        id INTEGER PRIMARY KEY
                    );
                    CREATE TABLE "{schema}".users (
                        id INTEGER PRIMARY KEY,
                        organization_id INTEGER NOT NULL
                    );
                    CREATE TABLE "{schema}".strategic_initiatives (
                        id INTEGER PRIMARY KEY,
                        name VARCHAR(256) NOT NULL,
                        organization_id INTEGER NOT NULL
                    );
                    CREATE TABLE "{schema}".enterprise_initiatives (
                        id INTEGER PRIMARY KEY,
                        name VARCHAR(200) NOT NULL,
                        organization_id INTEGER
                    );
                    CREATE TABLE "{schema}".work_packages (
                        id INTEGER PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        organization_id INTEGER NOT NULL
                    );
                    CREATE TABLE "{schema}".strategic_roadmap_items (
                        id INTEGER PRIMARY KEY,
                        initiative_id INTEGER REFERENCES "{schema}".strategic_initiatives(id),
                        title VARCHAR(256) NOT NULL
                    );
                    CREATE TABLE "{schema}".benefits (
                        id INTEGER PRIMARY KEY,
                        initiative_id INTEGER,
                        name VARCHAR(255) NOT NULL,
                        CONSTRAINT benefits_initiative_id_fkey
                          FOREIGN KEY (initiative_id)
                          REFERENCES "{schema}".enterprise_initiatives(id)
                          ON DELETE CASCADE
                    );
                    CREATE TABLE "{schema}".solutions (
                        id INTEGER PRIMARY KEY,
                        initiative_id INTEGER REFERENCES "{schema}".strategic_initiatives(id),
                        name VARCHAR(255) NOT NULL,
                        organization_id INTEGER NOT NULL
                    );
                    CREATE TABLE "{schema}".evidence_requests (
                        id INTEGER PRIMARY KEY,
                        organization_id INTEGER NOT NULL,
                        workstream_id INTEGER NOT NULL,
                        candidate_id INTEGER NOT NULL,
                        subject_type VARCHAR(40) NOT NULL,
                        subject_id INTEGER NOT NULL,
                        claim_key VARCHAR(100) NOT NULL,
                        assigned_to_id INTEGER NOT NULL,
                        required BOOLEAN NOT NULL DEFAULT true,
                        status VARCHAR(30) NOT NULL DEFAULT 'open',
                        accepted_evidence_id INTEGER,
                        acknowledgement_id INTEGER,
                        waiver_id INTEGER,
                        due_at TIMESTAMPTZ,
                        created_by_id INTEGER NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                        revision INTEGER NOT NULL DEFAULT 1
                    );
                    INSERT INTO "{schema}".organizations (id) VALUES (1), (2);
                    INSERT INTO "{schema}".users (id, organization_id) VALUES (1, 1), (2, 2);
                    INSERT INTO "{schema}".strategic_initiatives
                        (id, name, organization_id)
                        VALUES (10, 'Programme A', 1), (20, 'Programme B', 2);
                    INSERT INTO "{schema}".enterprise_initiatives
                        (id, name, organization_id)
                        VALUES (30, 'Legacy A', 1);
                    INSERT INTO "{schema}".strategic_roadmap_items
                        (id, initiative_id, title)
                        VALUES (100, 10, 'Existing roadmap row');
                    INSERT INTO "{schema}".benefits (id, initiative_id, name)
                        VALUES (200, 30, 'Existing benefit');
                    INSERT INTO "{schema}".solutions
                        (id, initiative_id, name, organization_id)
                        VALUES (300, 10, 'Existing solution', 1);
                    INSERT INTO "{schema}".evidence_requests (
                        id, organization_id, workstream_id, candidate_id,
                        subject_type, subject_id, claim_key, assigned_to_id,
                        created_by_id
                    ) VALUES (
                        400, 1, 500, 600, 'application', 700,
                        'application_owner', 1, 1
                    );
                    """
                )
            )

        isolated_engine = create_engine(
            public_engine.url,
            connect_args={"options": f"-csearch_path={schema},public"},
        )
        original_engine = db.engines[None]
        db.session.remove()
        db.engines[None] = isolated_engine
        try:
            yield schema, isolated_engine
        finally:
            db.session.remove()
            db.engines[None] = original_engine
            isolated_engine.dispose()
            with public_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def test_fresh_schema_contains_transformation_tables(app, _schema):
    expected = {
        "programme_workstreams",
        "programme_role_assignments",
        "programme_outcome_commitments",
        "measure_definitions",
    }
    with app.app_context():
        assert expected <= set(inspect(db.engine).get_table_names())


def test_existing_schema_reconciles_additive_columns_idempotently(app, _schema):
    table_name = f"test_reconcile_{uuid.uuid4().hex[:12]}"
    model_table = Table(
        table_name,
        db.metadata,
        Column("id", Integer, primary_key=True),
        Column("additive_value", String(40), nullable=True),
    )
    try:
        with app.app_context(), db.engine.begin() as connection:
            connection.execute(text(f'CREATE TABLE "{table_name}" (id INTEGER PRIMARY KEY)'))

        with app.app_context():
            dry_added, dry_failed, _missing, _blocking = _reconcile(dry_run=True)
            assert dry_failed == []
            assert any(
                item.startswith(f"{table_name}.additive_value ::") for item in dry_added
            )
            assert "additive_value" not in {
                column["name"] for column in inspect(db.engine).get_columns(table_name)
            }

            first_added, first_failed, _missing, _blocking = _reconcile(dry_run=False)
            assert first_failed == []
            assert any(
                item.startswith(f"{table_name}.additive_value ::") for item in first_added
            )
            second_added, second_failed, _missing, _blocking = _reconcile(dry_run=False)
            assert second_failed == []
            assert not any(item.startswith(f"{table_name}.") for item in second_added)
    finally:
        with app.app_context(), db.engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))
        db.metadata.remove(model_table)


@pytest.mark.timeout(60)
def test_reconcile_reflects_full_schema_without_one_connection_per_table(app, _schema):
    """Regression: reconciling the whole mapped schema used to hang.

    `_reconcile` used to build its top-level Inspector from `db.engine`, and
    then call `get_columns()` with it once per table in `db.metadata.tables`
    across two full passes (the NOT-NULL drift scan, then the ADD COLUMN
    scan). `db.engine.connect()` opens a brand-new physical connection every
    time it's called, and the test suite's engine is configured with
    `NullPool` (every checkout is a fresh connect, nothing stays pooled), so
    on the ~800-table model that was roughly 1,600 extra physical
    PostgreSQL connections for one `_reconcile()` call. That turned a merely
    slow operation into something that blew straight through a 90 second
    test timeout - the failure mode reported against this file - rather than
    completing (if slowly).

    The fix reflects off `db.session`'s own already-open connection instead,
    so this counts how many *new* physical connections a single dry-run
    reconciliation opens and asserts it stays in the range the remaining,
    untouched single-table call sites in this module account for (measured
    at roughly 100-115 on the current model) rather than regressing back
    into the thousands. The `@pytest.mark.timeout` above is a second,
    independent signal: a real regression would not finish inside it.
    """
    from sqlalchemy import event

    connect_count = {"n": 0}

    def _count_connect(dbapi_connection, connection_record):  # noqa: ARG001
        connect_count["n"] += 1

    with app.app_context():
        event.listen(db.engine, "connect", _count_connect)
        try:
            added, failed, _missing, _blocking = _reconcile(dry_run=True)
            assert failed == []
            assert added is not None  # a real result, not a short-circuited no-op
        finally:
            event.remove(db.engine, "connect", _count_connect)

    assert connect_count["n"] < 300, (
        f"reconcile-schema opened {connect_count['n']} new physical "
        "connections during a single dry run over the full schema - that is "
        "in the range a per-table inspect(db.engine) reflection call would "
        "produce again (roughly 1,600+ on this model), not the ~100-115 the "
        "remaining single-table call sites account for. See the fix for "
        "PR132's inspect(db.engine) deadlock/connection-churn pattern in "
        "_reconcile()."
    )


def test_existing_command_table_is_upgraded_before_new_guarded_tables(
    app, pre_feature_transformation_schema
):
    """A guarded table create must not inspect an older peer prematurely."""
    _schema_name, isolated_engine = pre_feature_transformation_schema
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE command_idempotency_records (
                    id SERIAL PRIMARY KEY,
                    organization_id INTEGER NOT NULL,
                    actor_id INTEGER NOT NULL,
                    operation VARCHAR(120) NOT NULL,
                    idempotency_key VARCHAR(255) NOT NULL,
                    request_digest VARCHAR(64) NOT NULL,
                    natural_key VARCHAR(512) NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'in_progress',
                    lease_generation INTEGER NOT NULL DEFAULT 1,
                    claim_token VARCHAR(64) NOT NULL,
                    claimant_request_id VARCHAR(255) NOT NULL,
                    lease_expires_at TIMESTAMPTZ,
                    operation_result_id INTEGER,
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    last_error_class VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                    completed_at TIMESTAMPTZ
                )
                """
            )
        )

    with app.app_context():
        added, failed, _missing, _blocking = _reconcile(dry_run=False)
        assert failed == []
        assert any(
            item.startswith("command_idempotency_records.terminal_reason ::")
            for item in added
        )
        assert {
            "terminal_reason",
        } <= {
            column["name"]
            for column in inspect(db.engine).get_columns(
                "command_idempotency_records"
            )
        }
        assert "command_materialisations" in inspect(db.engine).get_table_names()


def test_pre_expiry_schema_reconciles_checkpoint_and_runs_empty_batch(
    app, pre_feature_transformation_schema, monkeypatch
):
    """A long-lived database can enable expiry without a preceding init-db."""
    schema_name, isolated_engine = pre_feature_transformation_schema
    from app.modules.transformation_room.arb_waiver_expiry_batch_service import (
        ARBWaiverExpiryBatchService,
    )

    with isolated_engine.connect() as connection:
        assert "arb_waiver_expiry_checkpoints" not in inspect(
            connection
        ).get_table_names(schema=schema_name)

    with app.app_context():
        monkeypatch.setitem(
            app.config, "ARB_CONDITION_EXPIRY_CAPABILITY", "reconcile-expiry-test"
        )
        added, failed, missing, _blocking = _reconcile(dry_run=False)
        assert failed == []
        assert "table.arb_waiver_expiry_checkpoints :: CREATE TABLE" in added
        assert "arb_waiver_expiry_checkpoints" not in missing

        result = ARBWaiverExpiryBatchService.run(
            organization_ids=[1], batch_size=1
        )
        assert result.selected_count == 0
        assert result.failed_count == 0

        with isolated_engine.connect() as connection:
            checkpoint = connection.execute(
                text(
                    "SELECT organization_ids_json, cursor_condition_id "
                    f'FROM "{schema_name}".arb_waiver_expiry_checkpoints'
                )
            ).one()
        assert checkpoint.organization_ids_json == [1]
        assert checkpoint.cursor_condition_id is None

        second_added, second_failed, second_missing, _blocking = _reconcile(
            dry_run=True
        )
        assert second_failed == []
        assert "arb_waiver_expiry_checkpoints" not in second_missing
        assert not any(
            item.startswith("table.arb_waiver_expiry_checkpoints")
            for item in second_added
        )


def test_pre_task6_evidence_waiver_constraint_reconciles_idempotently(
    app, pre_feature_transformation_schema
):
    """Catches add-only reconciliation omitting the Task 6 waiver invariant."""
    _schema_name, isolated_engine = pre_feature_transformation_schema
    label = "constraint.ck_evidence_request_waiver_complete"
    with app.app_context():
        dry_added, dry_failed, _missing, _blocking = _reconcile(dry_run=True)
        assert dry_failed
        assert all(
            item.startswith("transformation_db_guards:function_missing:")
            or item
            in {
                "transformation_db_guards:"
                "table_missing:archie_command_capability_keys",
                "transformation_db_guards:"
                "table_missing:archie_command_claim_challenges",
            }
            for item in dry_failed
        )
        assert f"{label} :: CHECK NOT VALID THEN VALIDATE" in dry_added
        with isolated_engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conrelid = 'evidence_requests'::regclass "
                    "AND conname = 'ck_evidence_request_waiver_complete'"
                )
            ) == 0

        first_added, first_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert first_failed == []
        assert f"{label} :: CHECK NOT VALID THEN VALIDATE" in first_added
        with isolated_engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT convalidated FROM pg_constraint "
                    "WHERE conrelid = 'evidence_requests'::regclass "
                    "AND conname = 'ck_evidence_request_waiver_complete'"
                )
            ) is True
            with pytest.raises(Exception):
                connection.execute(
                    text("UPDATE evidence_requests SET waiver_id = 77 WHERE id = 400")
                )

        second_added, second_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert second_failed == []
        assert not any(item.startswith(label) for item in second_added)


def test_pre_task7_schema_creates_decision_tables_and_partial_scope_indexes_idempotently(
    app, pre_feature_transformation_schema
):
    """Catches reconcile leaving long-lived databases without Task 7 uniqueness."""
    _schema_name, isolated_engine = pre_feature_transformation_schema
    expected_tables = {
        "transformation_options",
        "transformation_option_versions",
        "decision_briefs",
        "decision_brief_versions",
        "decision_brief_option_citations",
        "decision_brief_evidence_citations",
        "decision_events",
    }
    expected_indexes = {
        "uq_decision_brief_workstream_scope",
        "uq_decision_brief_candidate_scope",
    }
    with app.app_context():
        first_added, first_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert first_failed == []
        with isolated_engine.connect() as connection:
            assert expected_tables <= set(inspect(connection).get_table_names())
            rows = connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND indexname IN "
                    "('uq_decision_brief_workstream_scope', "
                    " 'uq_decision_brief_candidate_scope')"
                )
            ).mappings().all()
        assert {row["indexname"] for row in rows} == expected_indexes
        assert any("candidate_id IS NULL" in row["indexdef"] for row in rows)
        assert any("candidate_id IS NOT NULL" in row["indexdef"] for row in rows)

        second_added, second_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert second_failed == []
        assert not any(
            item.startswith("index.uq_decision_brief_") for item in second_added
        )
        assert not expected_tables.intersection(
            item.split(".", 1)[0] for item in second_added
        )


def test_task7_guards_install_and_repair_inside_the_active_non_public_schema(
    app, pre_feature_transformation_schema
):
    """Catches guard DDL, inspection, triggers or grants silently targeting public."""
    schema_name, isolated_engine = pre_feature_transformation_schema
    with app.app_context():
        first_added, first_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert first_failed == []
        second_added, second_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert second_failed == []
        assert not any(item.startswith("transformation_db_guards:") for item in second_added)

        with isolated_engine.begin() as connection:
            assert inspect_transformation_db_guards(connection) == []
            function_schemas = connection.execute(
                text(
                    "SELECT DISTINCT namespace.nspname "
                    "FROM pg_proc proc "
                    "JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace "
                    "WHERE proc.proname IN "
                    "('archie_reject_transformation_mutation', "
                    " 'archie_hmac_sha256', "
                    " 'archie_verify_command_capability', "
                    " 'archie_claim_transformation_command', "
                    " 'archie_guard_decision_citation_membership', "
                    " 'archie_create_decision_brief', "
                    " 'archie_freeze_decision_brief_version', "
                    " 'archie_guard_transformation_receipt', "
                    " 'archie_guard_evidence_head', "
                    " 'archie_guard_evidence_event_binding', "
                    " 'archie_advance_evidence_head') "
                    "AND namespace.nspname = current_schema()"
                )
            ).scalars().all()
            assert function_schemas == [schema_name]
            trigger_schemas = connection.execute(
                text(
                    "SELECT DISTINCT function_namespace.nspname "
                    "FROM pg_trigger trigger "
                    "JOIN pg_class target ON target.oid = trigger.tgrelid "
                    "JOIN pg_namespace target_namespace "
                    "  ON target_namespace.oid = target.relnamespace "
                    "JOIN pg_proc proc ON proc.oid = trigger.tgfoid "
                    "JOIN pg_namespace function_namespace "
                    "  ON function_namespace.oid = proc.pronamespace "
                    "WHERE target_namespace.nspname = current_schema() "
                    "AND NOT trigger.tgisinternal"
                )
            ).scalars().all()
            assert trigger_schemas == [schema_name]

            # Seed the pre-existing row as the bootstrap superuser, then restore
            # production trigger enforcement before exercising append-only repair.
            connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
            receipt_id = connection.scalar(
                text(
                    "INSERT INTO command_idempotency_records "
                    "(organization_id, actor_id, operation, idempotency_key, "
                    " request_digest, natural_key, status, lease_generation, "
                    " claim_token, claimant_request_id, lease_expires_at, attempt_count) "
                    "VALUES (1, 1, 'brief.freeze', 'isolated-guard', :digest, "
                    " 'brief:999:version:1', 'in_progress', 1, :token, "
                    " 'isolated-guard', clock_timestamp() + interval '1 minute', 1) "
                    "RETURNING id"
                ),
                {"digest": "d" * 64, "token": "t" * 64},
            )
            result_id = connection.scalar(
                text(
                    "INSERT INTO operation_results "
                    "(organization_id, actor_id, operation, natural_key, "
                    " request_digest, receipt_id, receipt_generation, "
                    " object_ids, response_json) "
                    "VALUES (1, 1, 'brief.freeze', 'isolated-result', :digest, "
                    " :receipt_id, 1, '{}'::json, '{}'::json) RETURNING id"
                ),
                {"digest": "d" * 64, "receipt_id": receipt_id},
            )
            connection.exec_driver_sql("SET LOCAL session_replication_role = origin")
            with pytest.raises(Exception, match="append-only"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE operation_results "
                            "SET response_json = CAST(:bad_payload AS json) "
                            "WHERE id = :result_id AND organization_id = 1"
                        ),
                        {"bad_payload": '{"bad":true}', "result_id": result_id},
                    )

        with isolated_engine.begin() as connection:
            with pytest.raises(Exception, match="citation membership is frozen"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO decision_brief_option_citations "
                            "(organization_id, brief_version_id, option_version_id) "
                            "VALUES (1, 999999, 999999)"
                        )
                    )


def test_task9_history_tables_and_dropped_triggers_reconcile_idempotently(
    app, pre_feature_transformation_schema
):
    """Catches existing Task 9 tables remaining writable after trigger drift."""
    _schema_name, isolated_engine = pre_feature_transformation_schema
    with app.app_context():
        first_added, first_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert first_failed == []
        assert "table.delivery_export_attempts :: CREATE TABLE" in first_added
        assert "table.outcome_measurements :: CREATE TABLE" in first_added

        with isolated_engine.begin() as connection:
            assert inspect_execution_history_immutability(connection) == []
            connection.execute(
                text(
                    "DROP TRIGGER trg_guard_delivery_export_attempt_mutation "
                    "ON delivery_export_attempts; "
                    "DROP TRIGGER trg_reject_outcome_measurement_mutation "
                    "ON outcome_measurements"
                )
            )

        dry_added, dry_failed, _missing, _blocking = _reconcile(dry_run=True)
        assert dry_added == []
        assert {
            "execution_history_guards:trigger_missing:"
            "delivery_export_attempts.trg_guard_delivery_export_attempt_mutation",
            "execution_history_guards:trigger_missing:"
            "outcome_measurements.trg_reject_outcome_measurement_mutation",
        } <= set(dry_failed)

        repaired, repair_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert repair_failed == []
        assert {
            "execution_history_guards:trigger_missing:"
            "delivery_export_attempts.trg_guard_delivery_export_attempt_mutation",
            "execution_history_guards:trigger_missing:"
            "outcome_measurements.trg_reject_outcome_measurement_mutation",
        } <= set(repaired)

        with isolated_engine.begin() as connection:
            assert inspect_execution_history_immutability(connection) == []
            connection.execute(
                text(
                    "INSERT INTO work_packages (id, name, organization_id) "
                    "VALUES (901, 'Task 9 guarded work', 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO delivery_export_attempts "
                    "(id, organization_id, work_package_id, provider_key, attempt_key, "
                    " request_json, status, error_class, error_message, attempted_by_id, "
                    " completed_at) VALUES "
                    "(902, 1, 901, 'delivery', :attempt_key, '{}'::json, 'failed', "
                    " 'ConnectionError', 'unavailable', 1, clock_timestamp())"
                ),
                {"attempt_key": "9" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO outcome_measurements "
                    "(id, organization_id, benefit_id, value, observed_at, "
                    " source_identity, source_version, recorded_by_id) VALUES "
                    "(903, 1, 200, 1.000000, clock_timestamp(), "
                    " 'ledger:run-cost', 'v1', 1)"
                )
            )
            with pytest.raises(Exception, match="completed delivery export attempts"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE delivery_export_attempts SET error_message='changed' "
                            "WHERE id=902 AND organization_id=1"
                        )
                    )
            with pytest.raises(Exception, match="outcome measurements are append-only"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "DELETE FROM outcome_measurements "
                            "WHERE id=903 AND organization_id=1"
                        )
                        )


def test_task9_history_guard_definition_drift_is_detected_and_repaired(
    app, pre_feature_transformation_schema
):
    """Catches permissive bodies and wrong trigger function/event/timing shapes."""
    _schema_name, isolated_engine = pre_feature_transformation_schema
    with app.app_context():
        _added, failed, _missing, _blocking = _reconcile(dry_run=False)
        assert failed == []
        with isolated_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE OR REPLACE FUNCTION "
                    "archie_reject_outcome_measurement_mutation() "
                    "RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN RETURN OLD; END; $$; "
                    "CREATE OR REPLACE FUNCTION archie_wrong_history_guard() "
                    "RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN RETURN NULL; END; $$; "
                    "DROP TRIGGER trg_reject_outcome_measurement_mutation "
                    "ON outcome_measurements; "
                    "CREATE TRIGGER trg_reject_outcome_measurement_mutation "
                    "BEFORE UPDATE OF value ON outcome_measurements FOR EACH ROW "
                    "WHEN (OLD.value IS DISTINCT FROM NEW.value) EXECUTE FUNCTION "
                    "archie_reject_outcome_measurement_mutation(); "
                    "DROP TRIGGER trg_guard_delivery_export_attempt_mutation "
                    "ON delivery_export_attempts; "
                    "CREATE TRIGGER trg_guard_delivery_export_attempt_mutation "
                    "AFTER UPDATE ON outcome_measurements FOR EACH STATEMENT "
                    "EXECUTE FUNCTION archie_wrong_history_guard()"
                )
            )
            assert {
                "function_body:archie_reject_outcome_measurement_mutation",
                "trigger_definition:delivery_export_attempts."
                "trg_guard_delivery_export_attempt_mutation",
                "trigger_definition:outcome_measurements."
                "trg_reject_outcome_measurement_mutation",
            } <= set(inspect_execution_history_immutability(connection))

        repaired, repair_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert repair_failed == []
        assert {
            "execution_history_guards:function_body:"
            "archie_reject_outcome_measurement_mutation",
            "execution_history_guards:trigger_definition:"
            "delivery_export_attempts.trg_guard_delivery_export_attempt_mutation",
            "execution_history_guards:trigger_definition:"
            "outcome_measurements.trg_reject_outcome_measurement_mutation",
        } <= set(repaired)

        with isolated_engine.begin() as connection:
            assert inspect_execution_history_immutability(connection) == []
            connection.execute(
                text(
                    "INSERT INTO work_packages (id, name, organization_id) "
                    "VALUES (911, 'Task 9 definition guarded work', 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO delivery_export_attempts "
                    "(id, organization_id, work_package_id, provider_key, attempt_key, "
                    " request_json, status, error_class, error_message, attempted_by_id, "
                    " completed_at) VALUES "
                    "(912, 1, 911, 'delivery', :attempt_key, '{}'::json, 'failed', "
                    " 'ConnectionError', 'unavailable', 1, clock_timestamp())"
                ),
                {"attempt_key": "8" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO outcome_measurements "
                    "(id, organization_id, benefit_id, value, observed_at, "
                    " source_identity, source_version, recorded_by_id) VALUES "
                    "(913, 1, 200, 1.000000, clock_timestamp(), "
                    " 'ledger:run-cost', 'v1', 1)"
                )
            )
            with pytest.raises(Exception, match="completed delivery export attempts"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "DELETE FROM delivery_export_attempts "
                            "WHERE id=912 AND organization_id=1"
                        )
                    )
            with pytest.raises(Exception, match="outcome measurements are append-only"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE outcome_measurements SET value=2 "
                            "WHERE id=913 AND organization_id=1"
                        )
                    )


def _task9_parameterized_multicommand_execute_sites():
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    target_tests = {
        "test_task9_history_tables_and_dropped_triggers_reconcile_idempotently",
        "test_task9_history_guard_definition_drift_is_detected_and_repaired",
    }

    offenders = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in target_tests:
            continue
        for descendant in ast.walk(node):
            if not isinstance(descendant, ast.Call):
                continue
            if not (
                isinstance(descendant.func, ast.Attribute)
                and descendant.func.attr == "execute"
                and descendant.args
            ):
                continue
            sql_call = descendant.args[0]
            if not (
                isinstance(sql_call, ast.Call)
                and isinstance(sql_call.func, ast.Name)
                and sql_call.func.id == "text"
                and sql_call.args
                and isinstance(sql_call.args[0], ast.Constant)
                and isinstance(sql_call.args[0].value, str)
            ):
                continue
            sql_text = sql_call.args[0].value
            has_parameters = len(descendant.args) > 1 or bool(descendant.keywords)
            if has_parameters and ";" in sql_text:
                offenders.append(f"{node.name}:{descendant.lineno}")
    return offenders


def test_task9_regression_avoids_parameterized_multicommand_sql_blocks():
    """Parameterized multi-command SQL breaks prepared execution on psycopg v3."""
    assert _task9_parameterized_multicommand_execute_sites() == []


def test_genuine_pre_feature_schema_adds_the_roadmap_tenant_column_and_repairs_delivery_fks(
    app, pre_feature_transformation_schema
):
    """reconcile-schema adds the nullable organization_id column it finds
    missing and reports that addition -- it does not write a row's tenant
    value. That row-level backfill is ``flask backfill-layer-tenancy``'s job
    alone (app/commands/backfill_layer_tenancy.py), per the single-write-path
    consolidation: a schema run must never fail the deploy over a tenant row
    it did not, itself, create.
    """
    _schema_name, isolated_engine = pre_feature_transformation_schema
    with app.app_context():
        dry_added, dry_failed, _missing, _blocking = _reconcile(dry_run=True)
        assert dry_failed
        assert all(
            item.startswith("transformation_db_guards:function_missing:")
            or item
            in {
                "transformation_db_guards:"
                "table_missing:archie_command_capability_keys",
                "transformation_db_guards:"
                "table_missing:archie_command_claim_challenges",
            }
            for item in dry_failed
        )
        assert any(
            item.startswith("strategic_roadmap_items.organization_id ::")
            for item in dry_added
        )
        with isolated_engine.connect() as connection:
            assert "organization_id" not in {
                column["name"]
                for column in inspect(connection).get_columns("strategic_roadmap_items")
            }

        first_added, first_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert first_failed == []
        assert not any(
            item.startswith("backfill.strategic_roadmap_items") for item in first_added
        )

        with isolated_engine.connect() as connection:
            roadmap_org = connection.scalar(
                text("SELECT organization_id FROM strategic_roadmap_items WHERE id = 100")
            )
            assert roadmap_org is None
            benefit_fk = connection.execute(
                text(
                    """
                    SELECT rc.delete_rule
                    FROM information_schema.referential_constraints rc
                    WHERE rc.constraint_name = 'fk_benefits_legacy_enterprise_initiative'
                      AND rc.constraint_schema = current_schema()
                    """
                )
            ).scalar_one()
            assert benefit_fk == "SET NULL"
            solution_fk = connection.execute(
                text(
                    """
                    SELECT rc.delete_rule
                    FROM information_schema.referential_constraints rc
                    WHERE rc.constraint_name = 'fk_solutions_strategic_initiative'
                      AND rc.constraint_schema = current_schema()
                    """
                )
            ).scalar_one()
            assert solution_fk == "RESTRICT"

        second_added, second_failed, _missing, _blocking = _reconcile(dry_run=False)
        assert second_failed == []
        assert not any(item.startswith("backfill.strategic_roadmap_items") for item in second_added)


def test_pre_feature_roadmap_without_tenant_provenance_is_left_for_the_backfill_command(
    app, pre_feature_transformation_schema
):
    """An unprovenanced roadmap row is not reconcile-schema's to resolve or
    report on: it leaves the row exactly as it found it (nullable column,
    NULL value) with no failure recorded, so ``flask backfill-layer-tenancy``
    -- the one place allowed to write organization_id on an existing row --
    is the only thing that assigns or defers it.
    """
    _schema_name, isolated_engine = pre_feature_transformation_schema
    with app.app_context():
        _added, failed, _missing, _blocking = _reconcile(dry_run=False)
        assert failed == []
        with isolated_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO strategic_roadmap_items
                        (id, initiative_id, title, organization_id)
                    VALUES (101, NULL, 'Unowned legacy roadmap row', NULL)
                    """
                )
            )

        added, failed, _missing, _blocking = _reconcile(dry_run=False)
        assert not any(item.startswith("backfill.strategic_roadmap_items") for item in added)
        assert failed == []
        with isolated_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT organization_id FROM strategic_roadmap_items WHERE id = 101")
            ) is None


def test_transformation_fk_checks_and_membership_triggers_are_installed(app, _schema):
    with app.app_context():
        _added, failed, _missing, _blocking = _reconcile(dry_run=False)
        assert failed == []
        constraints = db.session.execute(
            text(
                """
                SELECT c.conname, c.contype, c.confdeltype, t.relname AS table_name
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                WHERE t.relname IN (
                    'programme_workstreams', 'programme_role_assignments',
                    'programme_outcome_commitments', 'measure_definitions',
                    'work_packages', 'strategic_roadmap_items', 'benefits', 'solutions'
                )
                """
            )
        ).mappings().all()
        names = {row["conname"] for row in constraints}
        assert "ck_programme_workstream_type" in names
        assert "ck_programme_outcome_direction" in names
        assert "ck_measure_definition_aggregation" in names

        benefit_legacy_fk = next(
            row
            for row in constraints
            if row["table_name"] == "benefits"
            and row["contype"] == "f"
            and row["conname"] == "fk_benefits_legacy_enterprise_initiative"
        )
        assert benefit_legacy_fk["confdeltype"] == "n"  # SET NULL

        trigger_tables = set(
            db.session.scalars(
                text(
                    """
                    SELECT event_object_table
                    FROM information_schema.triggers
                    WHERE trigger_name = 'trg_transformation_membership'
                    """
                )
            )
        )
        assert {
            "programme_workstreams",
            "programme_role_assignments",
            "programme_outcome_commitments",
            "measure_definitions",
            "work_packages",
            "strategic_roadmap_items",
            "benefits",
            "solutions",
        } <= trigger_tables


def test_membership_function_is_refreshed_when_all_triggers_already_exist(app, _schema):
    """Catches trigger presence incorrectly suppressing function upgrades."""
    from app.commands.reconcile_schema import _MEMBERSHIP_FUNCTION_SQL, _reconcile

    stale_sql = """
        CREATE OR REPLACE FUNCTION archie_validate_transformation_membership()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'stale membership function';
        END;
        $$
    """
    with app.app_context():
        try:
            db.session.execute(text(stale_sql))
            db.session.commit()
            _added, failed, _missing, _blocking = _reconcile(dry_run=False)
            assert failed == []
            definition = db.session.scalar(
                text(
                    "SELECT pg_get_functiondef(proc.oid) "
                    "FROM pg_proc proc "
                    "JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace "
                    "WHERE proc.proname = 'archie_validate_transformation_membership' "
                    "AND namespace.nspname = current_schema()"
                )
            )
            assert "stale membership function" not in definition
            assert "workstream programme is outside its tenant" in definition
        finally:
            db.session.rollback()
            db.session.execute(text(_MEMBERSHIP_FUNCTION_SQL))
            db.session.commit()


def test_canonical_programme_and_workstream_fks_use_delete_restrict(app, _schema):
    expected = {
        ("work_packages", "strategic_initiative_id"),
        ("work_packages", "programme_workstream_id"),
        ("strategic_roadmap_items", "initiative_id"),
        ("strategic_roadmap_items", "programme_workstream_id"),
        ("benefits", "strategic_initiative_id"),
        ("benefits", "programme_workstream_id"),
        ("benefits", "outcome_commitment_id"),
        ("solutions", "initiative_id"),
        ("solutions", "workstream_id"),
    }
    with app.app_context():
        _added, failed, _missing, _blocking = _reconcile(dry_run=False)
        assert failed == []
        rows = db.session.execute(
            text(
                """
                SELECT tc.table_name, kcu.column_name, rc.delete_rule
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON kcu.constraint_schema = tc.constraint_schema
                 AND kcu.constraint_name = tc.constraint_name
                JOIN information_schema.referential_constraints rc
                  ON rc.constraint_schema = tc.constraint_schema
                 AND rc.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND (tc.table_name, kcu.column_name) IN (
                    ('work_packages', 'strategic_initiative_id'),
                    ('work_packages', 'programme_workstream_id'),
                    ('strategic_roadmap_items', 'initiative_id'),
                    ('strategic_roadmap_items', 'programme_workstream_id'),
                    ('benefits', 'strategic_initiative_id'),
                    ('benefits', 'programme_workstream_id'),
                    ('benefits', 'outcome_commitment_id'),
                    ('solutions', 'initiative_id'),
                    ('solutions', 'workstream_id')
                  )
                """
            )
        ).mappings().all()
        assert {(row["table_name"], row["column_name"]) for row in rows} == expected
        assert all(row["delete_rule"] == "RESTRICT" for row in rows)


def test_materialisation_indexes_are_partial_and_unique(app, _schema):
    with app.app_context():
        rows = db.session.execute(
            text(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE indexname IN (
                    'uq_work_package_materialisation',
                    'uq_roadmap_item_materialisation',
                    'uq_benefit_materialisation'
                )
                """
            )
        ).mappings().all()
        assert {row["indexname"] for row in rows} == {
            "uq_work_package_materialisation",
            "uq_roadmap_item_materialisation",
            "uq_benefit_materialisation",
        }
        assert all("UNIQUE INDEX" in row["indexdef"] for row in rows)
        assert all("WHERE (materialisation_key IS NOT NULL)" in row["indexdef"] for row in rows)
