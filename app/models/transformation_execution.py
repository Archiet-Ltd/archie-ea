"""Fenced command receipts and immutable transformation operation envelopes."""

from __future__ import annotations

from sqlalchemy import event, inspect

from app import db
from app.models.mixins import TenantMixin


COMMAND_STATUSES = (
    "in_progress",
    "retryable_failure",
    "failed_non_retryable",
    "succeeded",
)


class CommandIdempotencyRecord(TenantMixin, db.Model):
    """Short-transaction claim for one actor/operation/idempotency key."""

    __tablename__ = "command_idempotency_records"

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    operation = db.Column(db.String(120), nullable=False)
    idempotency_key = db.Column(db.String(255), nullable=False)
    request_digest = db.Column(db.String(64), nullable=False)
    natural_key = db.Column(db.String(512), nullable=False)
    status = db.Column(
        db.String(32), nullable=False, default="in_progress", server_default="in_progress"
    )
    lease_generation = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    claim_token = db.Column(db.String(64), nullable=False)
    claimant_request_id = db.Column(db.String(255), nullable=False)
    lease_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    operation_result_id = db.Column(db.Integer, nullable=True, index=True)
    attempt_count = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    last_error_class = db.Column(db.String(255), nullable=True)
    terminal_reason = db.Column(db.String(255), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    actor = db.relationship("User", foreign_keys=[actor_id])

    __table_args__ = (
        db.UniqueConstraint(
            "organization_id",
            "actor_id",
            "operation",
            "idempotency_key",
            name="uq_command_idempotency_identity",
        ),
        db.CheckConstraint("lease_generation > 0", name="ck_command_lease_generation"),
        db.CheckConstraint("attempt_count > 0", name="ck_command_attempt_count"),
        db.CheckConstraint(
            "status IN ('in_progress', 'retryable_failure', "
            "'failed_non_retryable', 'succeeded')",
            name="ck_command_status",
        ),
    )


class OperationResult(TenantMixin, db.Model):
    """Canonical, append-only response for one natural business operation."""

    __tablename__ = "operation_results"

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    operation = db.Column(db.String(120), nullable=False)
    natural_key = db.Column(db.String(512), nullable=False)
    request_digest = db.Column(db.String(64), nullable=False)
    receipt_id = db.Column(
        db.Integer,
        db.ForeignKey("command_idempotency_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    receipt_generation = db.Column(db.Integer, nullable=False)
    object_ids = db.Column(db.JSON, nullable=False)
    response_json = db.Column(db.JSON, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )

    receipt = db.relationship("CommandIdempotencyRecord", foreign_keys=[receipt_id])

    __table_args__ = (
        db.UniqueConstraint(
            "organization_id",
            "operation",
            "natural_key",
            name="uq_operation_result_natural_key",
        ),
        db.UniqueConstraint("receipt_id", name="uq_operation_result_receipt"),
        db.CheckConstraint(
            "receipt_generation > 0", name="ck_operation_result_generation"
        ),
    )


class CommandMaterialisation(TenantMixin, db.Model):
    """Immutable recovery envelope for one exact natural-key domain mutation.

    This row is committed atomically with the domain mutation.  It deliberately
    does not depend on ``operation_results``: a damaged receipt/result envelope
    can therefore be rebuilt without guessing from mutable domain state.
    """

    __tablename__ = "command_materialisations"

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    operation = db.Column(db.String(120), nullable=False)
    natural_key = db.Column(db.String(512), nullable=False)
    request_digest = db.Column(db.String(64), nullable=False)
    receipt_id = db.Column(
        db.Integer,
        db.ForeignKey("command_idempotency_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    receipt_generation = db.Column(db.Integer, nullable=False)
    object_ids = db.Column(db.JSON, nullable=False)
    response_json = db.Column(db.JSON, nullable=False)
    outbox_events = db.Column(db.JSON, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )

    actor = db.relationship("User", foreign_keys=[actor_id])
    receipt = db.relationship("CommandIdempotencyRecord", foreign_keys=[receipt_id])

    __table_args__ = (
        db.UniqueConstraint(
            "organization_id",
            "operation",
            "natural_key",
            name="uq_command_materialisation_natural_key",
        ),
        db.UniqueConstraint("receipt_id", name="uq_command_materialisation_receipt"),
        db.CheckConstraint(
            "length(request_digest) = 64",
            name="ck_command_materialisation_digest",
        ),
        db.CheckConstraint(
            "receipt_generation > 0",
            name="ck_command_materialisation_generation",
        ),
    )


class OperationOutboxEvent(TenantMixin, db.Model):
    """Append-only, at-least-once delivery payload with a deduplication ID.

    When ``operation_result_id`` is set this is a transformation-command event;
    when ``entity_type`` and ``entity_id`` are set it is an entity-mutation event
    produced by ``app/services/outbox.py``.  Both kinds are relayed into
    ``event_log`` by the outbox-relay job.
    """

    __tablename__ = "transformation_outbox_events"

    id = db.Column(db.Integer, primary_key=True)
    operation_result_id = db.Column(
        db.Integer,
        db.ForeignKey("operation_results.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    entity_type = db.Column(db.String(80), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)
    event_id = db.Column(db.String(36), nullable=False, unique=True)
    ordinal = db.Column(db.Integer, nullable=False)
    event_type = db.Column(db.String(160), nullable=False)
    payload_json = db.Column(db.JSON, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )
    published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    delivery_attempts = db.Column(db.Integer, nullable=False, default=0, server_default="0")

    operation_result = db.relationship(
        "OperationResult",
        foreign_keys=[operation_result_id],
        backref=db.backref("outbox_events", lazy="select", passive_deletes=True),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "operation_result_id", "ordinal", name="uq_operation_outbox_ordinal"
        ),
        db.CheckConstraint("ordinal >= 0", name="ck_operation_outbox_ordinal"),
        db.CheckConstraint(
            "delivery_attempts >= 0", name="ck_operation_outbox_delivery_attempts"
        ),
    )


class DeliveryExportAttempt(TenantMixin, db.Model):
    """Append-only result of one attempt to export canonical delivery work."""

    __tablename__ = "delivery_export_attempts"

    id = db.Column(db.Integer, primary_key=True)
    work_package_id = db.Column(
        db.Integer,
        db.ForeignKey("work_packages.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    predecessor_attempt_id = db.Column(
        db.Integer,
        db.ForeignKey("delivery_export_attempts.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    provider_key = db.Column(db.String(120), nullable=False)
    attempt_key = db.Column(db.String(64), nullable=False)
    provider_idempotency_key = db.Column(db.String(64), nullable=True)
    dispatch_lease_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    request_json = db.Column(db.JSON, nullable=False)
    response_digest = db.Column(db.String(64), nullable=True)
    external_key = db.Column(db.String(512), nullable=True)
    status = db.Column(db.String(24), nullable=False)
    error_class = db.Column(db.String(255), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    attempted_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    work_package = db.relationship("WorkPackage", foreign_keys=[work_package_id])
    predecessor_attempt = db.relationship(
        "DeliveryExportAttempt", remote_side=[id], foreign_keys=[predecessor_attempt_id]
    )
    attempted_by = db.relationship("User", foreign_keys=[attempted_by_id])

    __table_args__ = (
        db.UniqueConstraint(
            "organization_id",
            "attempt_key",
            name="uq_delivery_export_attempt_key",
        ),
        db.CheckConstraint(
            "length(btrim(provider_key)) > 0 AND length(attempt_key) = 64 "
            "AND (provider_idempotency_key IS NULL "
            "OR length(provider_idempotency_key) = 64)",
            name="ck_delivery_export_attempt_identity",
        ),
        db.CheckConstraint(
            "status IN ('in_progress','succeeded','failed','indeterminate')",
            name="ck_delivery_export_attempt_status",
        ),
        db.CheckConstraint(
            "(status = 'in_progress' AND completed_at IS NULL "
            "AND response_digest IS NULL AND external_key IS NULL "
            "AND error_class IS NULL AND error_message IS NULL) OR "
            "(status = 'succeeded' AND completed_at IS NOT NULL "
            "AND length(response_digest) = 64 "
            "AND length(btrim(external_key)) > 0 "
            "AND error_class IS NULL AND error_message IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL "
            "AND length(btrim(error_message)) > 0 "
            "AND (response_digest IS NULL OR length(response_digest) = 64)) OR "
            "(status = 'indeterminate' AND completed_at IS NOT NULL "
            "AND response_digest IS NULL AND external_key IS NULL "
            "AND length(btrim(error_class)) > 0 "
            "AND length(btrim(error_message)) > 0)",
            name="ck_delivery_export_attempt_completion",
        ),
    )


class OutcomeMeasurement(TenantMixin, db.Model):
    """Immutable observation against the canonical Benefit projection."""

    __tablename__ = "outcome_measurements"

    id = db.Column(db.Integer, primary_key=True)
    benefit_id = db.Column(
        db.Integer,
        db.ForeignKey("benefits.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    value = db.Column(db.Numeric(24, 6), nullable=True)
    unavailable_reason = db.Column(db.Text, nullable=True)
    observed_at = db.Column(db.DateTime(timezone=True), nullable=False)
    source_identity = db.Column(db.String(512), nullable=False)
    source_version = db.Column(db.String(255), nullable=False)
    recorded_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )

    benefit = db.relationship("Benefit", foreign_keys=[benefit_id])
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_id])

    __table_args__ = (
        db.UniqueConstraint(
            "organization_id",
            "benefit_id",
            "source_identity",
            "observed_at",
            "source_version",
            name="uq_outcome_measurement_observation",
        ),
        db.CheckConstraint(
            "length(btrim(source_identity)) > 0 "
            "AND length(btrim(source_version)) > 0",
            name="ck_outcome_measurement_source",
        ),
        db.CheckConstraint(
            "(value IS NOT NULL AND unavailable_reason IS NULL) OR "
            "(value IS NULL AND length(btrim(unavailable_reason)) > 0)",
            name="ck_outcome_measurement_fact",
        ),
    )


def _reject_outcome_measurement_mutation(_mapper, _connection, _target):
    raise ValueError("outcome measurements are append-only")


def _guard_completed_export_attempt(_mapper, _connection, target):
    history = inspect(target).attrs.status.history
    previous = history.deleted[0] if history.deleted else target.status
    if previous in {"succeeded", "failed", "indeterminate"}:
        raise ValueError("completed delivery export attempts are immutable")


def _guard_completed_export_delete(_mapper, _connection, target):
    if target.status in {"succeeded", "failed", "indeterminate"}:
        raise ValueError("completed delivery export attempts are immutable")


event.listen(OutcomeMeasurement, "before_update", _reject_outcome_measurement_mutation)
event.listen(OutcomeMeasurement, "before_delete", _reject_outcome_measurement_mutation)
event.listen(DeliveryExportAttempt, "before_update", _guard_completed_export_attempt)
event.listen(DeliveryExportAttempt, "before_delete", _guard_completed_export_delete)


_EXECUTION_GUARD_FUNCTION_BODIES = {
    "archie_reject_outcome_measurement_mutation": """
        BEGIN
            RAISE EXCEPTION 'outcome measurements are append-only';
        END;
    """,
    "archie_guard_delivery_export_attempt_mutation": """
        BEGIN
            IF OLD.status IN ('succeeded', 'failed', 'indeterminate') THEN
                RAISE EXCEPTION 'completed delivery export attempts are immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
    """,
}

_EXECUTION_GUARD_TRIGGERS = (
    (
        "outcome_measurements",
        "trg_reject_outcome_measurement_mutation",
        "archie_reject_outcome_measurement_mutation",
    ),
    (
        "delivery_export_attempts",
        "trg_guard_delivery_export_attempt_mutation",
        "archie_guard_delivery_export_attempt_mutation",
    ),
)

_DELIVERY_EXPORT_CONSTRAINTS = {
    "ck_delivery_export_attempt_identity": (
        "length(btrim(provider_key)) > 0 AND length(attempt_key) = 64 "
        "AND (provider_idempotency_key IS NULL "
        "OR length(provider_idempotency_key) = 64)"
    ),
    "ck_delivery_export_attempt_status": (
        "status IN ('in_progress','succeeded','failed','indeterminate')"
    ),
    "ck_delivery_export_attempt_completion": (
        "(status = 'in_progress' AND completed_at IS NULL "
        "AND response_digest IS NULL AND external_key IS NULL "
        "AND error_class IS NULL AND error_message IS NULL) OR "
        "(status = 'succeeded' AND completed_at IS NOT NULL "
        "AND length(response_digest) = 64 "
        "AND length(btrim(external_key)) > 0 "
        "AND error_class IS NULL AND error_message IS NULL) OR "
        "(status = 'failed' AND completed_at IS NOT NULL "
        "AND length(btrim(error_message)) > 0 "
        "AND (response_digest IS NULL OR length(response_digest) = 64)) OR "
        "(status = 'indeterminate' AND completed_at IS NOT NULL "
        "AND response_digest IS NULL AND external_key IS NULL "
        "AND length(btrim(error_class)) > 0 "
        "AND length(btrim(error_message)) > 0)"
    ),
}


def _normalized_guard_body(value):
    return " ".join((value or "").split())


def _guard_function_is_exact(connection, schema, function):
    rows = connection.execute(
        db.text(
            "SELECT p.prosrc, l.lanname, p.pronargs, p.prosecdef, "
            "p.provolatile, p.prokind, "
            "pg_catalog.format_type(p.prorettype, NULL) AS return_type "
            "FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "JOIN pg_language l ON l.oid=p.prolang "
            "WHERE n.nspname=:schema AND p.proname=:function"
        ),
        {"schema": schema, "function": function},
    ).mappings().all()
    if not rows:
        return None
    if len(rows) != 1:
        return False
    row = rows[0]
    return bool(
        row["lanname"] == "plpgsql"
        and row["pronargs"] == 0
        and row["return_type"] == "trigger"
        and row["prosecdef"] is False
        and row["provolatile"] == "v"
        and row["prokind"] == "f"
        and _normalized_guard_body(row["prosrc"])
        == _normalized_guard_body(_EXECUTION_GUARD_FUNCTION_BODIES[function])
    )


def _guard_trigger_rows(connection, schema, trigger):
    return connection.execute(
        db.text(
            "SELECT c.relname AS table_name, t.tgenabled, t.tgtype, "
            "t.tgqual IS NULL AS has_no_when, t.tgattr::text AS update_columns, "
            "pn.nspname AS function_schema, p.proname AS function_name, "
            "t.tgnargs "
            "FROM pg_trigger t "
            "JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_proc p ON p.oid=t.tgfoid "
            "JOIN pg_namespace pn ON pn.oid=p.pronamespace "
            "WHERE n.nspname=:schema AND t.tgname=:trigger "
            "AND NOT t.tgisinternal"
        ),
        {"schema": schema, "trigger": trigger},
    ).mappings().all()


def _guard_trigger_is_exact(rows, *, schema, table, function):
    return len(rows) == 1 and bool(
        rows[0]["table_name"] == table
        and rows[0]["tgenabled"] == "O"
        and rows[0]["tgtype"] == 27  # BEFORE, ROW, UPDATE and DELETE only
        and rows[0]["has_no_when"]
        and rows[0]["update_columns"] == ""
        and rows[0]["function_schema"] == schema
        and rows[0]["function_name"] == function
        and rows[0]["tgnargs"] == 0
    )


def _delivery_constraint_is_current(connection, schema, constraint):
    definition = connection.scalar(
        db.text(
            "SELECT pg_get_constraintdef(c.oid, true) "
            "FROM pg_constraint c "
            "JOIN pg_class r ON r.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=r.relnamespace "
            "WHERE n.nspname=:schema AND r.relname='delivery_export_attempts' "
            "AND c.conname=:constraint AND c.contype='c'"
        ),
        {"schema": schema, "constraint": constraint},
    )
    if definition is None:
        return False
    normalized = definition.lower()
    required_tokens = {
        "ck_delivery_export_attempt_identity": ("provider_idempotency_key",),
        "ck_delivery_export_attempt_status": ("indeterminate",),
        "ck_delivery_export_attempt_completion": (
            "indeterminate",
            "response_digest is null",
            "external_key is null",
        ),
    }[constraint]
    return all(token in normalized for token in required_tokens)


def ensure_execution_history_immutability(connection):
    """Install database guards for Task 9 append-only/completed history."""
    if connection.dialect.name != "postgresql":
        return
    schema = connection.scalar(db.text("SELECT current_schema()"))
    preparer = connection.dialect.identifier_preparer
    quoted_schema = preparer.quote(schema)
    connection.execute(
        db.text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
        {"scope": f"{schema}:transformation-execution-history-guards"},
    )
    delivery_table_present = connection.scalar(
        db.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema=:schema "
            "AND table_name='delivery_export_attempts')"
        ),
        {"schema": schema},
    )
    if delivery_table_present:
        quoted_delivery = preparer.quote("delivery_export_attempts")
        for constraint, expression in _DELIVERY_EXPORT_CONSTRAINTS.items():
            if _delivery_constraint_is_current(connection, schema, constraint):
                continue
            quoted_constraint = preparer.quote(constraint)
            connection.execute(
                db.text(
                    f"ALTER TABLE {quoted_schema}.{quoted_delivery} "
                    f"DROP CONSTRAINT IF EXISTS {quoted_constraint}, "
                    f"ADD CONSTRAINT {quoted_constraint} CHECK ({expression})"
                )
            )
    for function, body in _EXECUTION_GUARD_FUNCTION_BODIES.items():
        connection.execute(
            db.text(
                f"CREATE OR REPLACE FUNCTION {quoted_schema}.{preparer.quote(function)}() "
                f"RETURNS trigger LANGUAGE plpgsql AS $guard${body}$guard$"
            )
        )
    for table, trigger, function in _EXECUTION_GUARD_TRIGGERS:
        exists = connection.scalar(
            db.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema=:schema AND table_name=:table)"
            ),
            {"schema": schema, "table": table},
        )
        if not exists:
            continue
        trigger_rows = _guard_trigger_rows(connection, schema, trigger)
        quoted_table = preparer.quote(table)
        quoted_trigger = preparer.quote(trigger)
        if not _guard_trigger_is_exact(
            trigger_rows, schema=schema, table=table, function=function
        ):
            for row in trigger_rows:
                connection.execute(
                    db.text(
                        f"DROP TRIGGER IF EXISTS {quoted_trigger} ON "
                        f"{quoted_schema}.{preparer.quote(row['table_name'])}"
                    )
                )
            connection.execute(
                db.text(
                    f"CREATE TRIGGER {quoted_trigger} BEFORE UPDATE OR DELETE "
                    f"ON {quoted_schema}.{quoted_table} FOR EACH ROW EXECUTE FUNCTION "
                    f"{quoted_schema}.{preparer.quote(function)}()"
                )
            )


def inspect_execution_history_immutability(connection):
    """Return deterministic Task 9 function/trigger drift labels."""
    if connection.dialect.name != "postgresql":
        return []
    schema = connection.scalar(db.text("SELECT current_schema()"))
    drift = []
    delivery_table_present = connection.scalar(
        db.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema=:schema "
            "AND table_name='delivery_export_attempts')"
        ),
        {"schema": schema},
    )
    if delivery_table_present:
        for constraint in _DELIVERY_EXPORT_CONSTRAINTS:
            if not _delivery_constraint_is_current(
                connection, schema, constraint
            ):
                drift.append(
                    f"constraint_definition:delivery_export_attempts.{constraint}"
                )
    for function in _EXECUTION_GUARD_FUNCTION_BODIES:
        exact = _guard_function_is_exact(connection, schema, function)
        if exact is None:
            drift.append(f"function_missing:{function}")
        elif not exact:
            drift.append(f"function_body:{function}")
    for table, trigger, function in _EXECUTION_GUARD_TRIGGERS:
        table_present = connection.scalar(
            db.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema=:schema AND table_name=:table)"
            ),
            {"schema": schema, "table": table},
        )
        if not table_present:
            continue
        rows = _guard_trigger_rows(connection, schema, trigger)
        expected_row = next(
            (row for row in rows if row["table_name"] == table), None
        )
        if not rows:
            drift.append(f"trigger_missing:{table}.{trigger}")
        elif (
            len(rows) == 1
            and expected_row is not None
            and expected_row["tgenabled"] == "D"
            and all(
                (
                    expected_row["tgtype"] == 27,
                    expected_row["has_no_when"],
                    expected_row["update_columns"] == "",
                    expected_row["function_schema"] == schema,
                    expected_row["function_name"] == function,
                    expected_row["tgnargs"] == 0,
                )
            )
        ):
            drift.append(f"trigger_disabled:{table}.{trigger}")
        elif not _guard_trigger_is_exact(
            rows, schema=schema, table=table, function=function
        ):
            drift.append(f"trigger_definition:{table}.{trigger}")
    return sorted(drift)


@event.listens_for(OutcomeMeasurement.__table__, "after_create")
def _install_outcome_measurement_immutability(_target, connection, **_kwargs):
    ensure_execution_history_immutability(connection)


@event.listens_for(DeliveryExportAttempt.__table__, "after_create")
def _install_delivery_export_immutability(_target, connection, **_kwargs):
    ensure_execution_history_immutability(connection)


__all__ = [
    "COMMAND_STATUSES",
    "CommandMaterialisation",
    "CommandIdempotencyRecord",
    "DeliveryExportAttempt",
    "OperationOutboxEvent",
    "OperationResult",
    "OutcomeMeasurement",
    "ensure_execution_history_immutability",
    "inspect_execution_history_immutability",
]
