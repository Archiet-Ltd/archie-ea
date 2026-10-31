"""Immutable command-fenced receipt of a typed ARB submission."""

from __future__ import annotations

from sqlalchemy import event

from app import db
from app.models.mixins import TenantMixin


_SUBMISSION_SHAPE = (
    "event_type = 'submitted' AND subject_type IS NOT NULL AND subject_id IS NOT NULL "
    "AND command_generation > 0 AND ((subject_type = 'decision_brief' "
    "AND subject_id = decision_brief_id AND decision_brief_id IS NOT NULL "
    "AND solution_id IS NULL AND architecture_model_id IS NULL AND adr_id IS NULL "
    "AND decision_brief_version_id IS NOT NULL "
    "AND solution_evidence_snapshot_id IS NULL AND subject_evidence_snapshot_id IS NULL) "
    "OR (subject_type = 'solution' AND subject_id = solution_id "
    "AND solution_id IS NOT NULL AND decision_brief_id IS NULL "
    "AND architecture_model_id IS NULL AND adr_id IS NULL "
    "AND decision_brief_version_id IS NULL "
    "AND solution_evidence_snapshot_id IS NOT NULL "
    "AND subject_evidence_snapshot_id IS NULL) "
    "OR (subject_type = 'architecture_model' "
    "AND subject_id = architecture_model_id AND architecture_model_id IS NOT NULL "
    "AND decision_brief_id IS NULL AND solution_id IS NULL AND adr_id IS NULL "
    "AND decision_brief_version_id IS NULL "
    "AND solution_evidence_snapshot_id IS NULL "
    "AND subject_evidence_snapshot_id IS NOT NULL) "
    "OR (subject_type = 'adr' AND subject_id = adr_id AND adr_id IS NOT NULL "
    "AND decision_brief_id IS NULL AND solution_id IS NULL "
    "AND architecture_model_id IS NULL AND decision_brief_version_id IS NULL "
    "AND solution_evidence_snapshot_id IS NULL "
    "AND subject_evidence_snapshot_id IS NOT NULL))"
)


class ARBSubmissionEvent(TenantMixin, db.Model):
    """One append-only, authoritative submission event per cycle and review."""

    __tablename__ = "arb_submission_events"

    id = db.Column(db.Integer, primary_key=True)
    review_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("arb_review_cycles.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    review_item_id = db.Column(
        db.Integer,
        db.ForeignKey("arb_review_items.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    event_type = db.Column(
        db.String(40), nullable=False, default="submitted", server_default="submitted"
    )
    subject_type = db.Column(db.String(40), nullable=False, index=True)
    subject_id = db.Column(db.Integer, nullable=False, index=True)
    decision_brief_id = db.Column(
        db.Integer, db.ForeignKey("decision_briefs.id", ondelete="RESTRICT")
    )
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id", ondelete="RESTRICT")
    )
    architecture_model_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id", ondelete="RESTRICT")
    )
    # E2E-H: repointed from architecture_decision_records (0 rows in
    # production, unreachable via any real UI) to architecture_decisions,
    # the model the real Decision Register writes.
    adr_id = db.Column(
        db.Integer,
        db.ForeignKey("architecture_decisions.id", ondelete="RESTRICT"),
    )
    decision_brief_version_id = db.Column(
        db.Integer, db.ForeignKey("decision_brief_versions.id", ondelete="RESTRICT")
    )
    solution_evidence_snapshot_id = db.Column(
        db.Integer,
        db.ForeignKey("arb_submission_evidence_snapshots.id", ondelete="RESTRICT"),
    )
    subject_evidence_snapshot_id = db.Column(
        db.Integer,
        db.ForeignKey("arb_subject_evidence_snapshots.id", ondelete="RESTRICT"),
    )
    actor_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    command_receipt_id = db.Column(
        db.Integer,
        db.ForeignKey("command_idempotency_records.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    command_generation = db.Column(db.Integer, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )

    __table_args__ = (
        db.CheckConstraint(_SUBMISSION_SHAPE, name="ck_arb_submission_event_shape"),
    )


def _membership_sql(quoted_schema):
    return f"""
    CREATE OR REPLACE FUNCTION {quoted_schema}.archie_validate_arb_submission_event()
    RETURNS trigger LANGUAGE plpgsql
    SET search_path = pg_catalog, {quoted_schema}
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM arb_review_cycles cycle
            JOIN arb_review_items review
              ON review.id = NEW.review_item_id
             AND review.review_cycle_id = cycle.id
            WHERE cycle.id = NEW.review_cycle_id
              AND cycle.organization_id = NEW.organization_id
              AND review.organization_id = NEW.organization_id
              AND review.submitter_id = NEW.actor_id
              AND cycle.subject_type = NEW.subject_type
              AND cycle.subject_id = NEW.subject_id
              AND review.subject_type = NEW.subject_type
              AND review.subject_id = NEW.subject_id
              AND cycle.decision_brief_id IS NOT DISTINCT FROM NEW.decision_brief_id
              AND review.decision_brief_id IS NOT DISTINCT FROM NEW.decision_brief_id
              AND cycle.solution_id IS NOT DISTINCT FROM NEW.solution_id
              AND review.solution_id IS NOT DISTINCT FROM NEW.solution_id
              AND cycle.architecture_model_id IS NOT DISTINCT FROM NEW.architecture_model_id
              AND review.architecture_model_id IS NOT DISTINCT FROM NEW.architecture_model_id
              AND cycle.adr_id IS NOT DISTINCT FROM NEW.adr_id
              AND review.adr_id IS NOT DISTINCT FROM NEW.adr_id
              AND cycle.decision_brief_version_id IS NOT DISTINCT FROM NEW.decision_brief_version_id
              AND review.decision_brief_version_id IS NOT DISTINCT FROM NEW.decision_brief_version_id
              AND cycle.solution_evidence_snapshot_id IS NOT DISTINCT FROM NEW.solution_evidence_snapshot_id
              AND review.solution_evidence_snapshot_id IS NOT DISTINCT FROM NEW.solution_evidence_snapshot_id
              AND cycle.subject_evidence_snapshot_id IS NOT DISTINCT FROM NEW.subject_evidence_snapshot_id
              AND review.subject_evidence_snapshot_id IS NOT DISTINCT FROM NEW.subject_evidence_snapshot_id
        ) THEN
            RAISE EXCEPTION 'ARB submission event membership or submitter/actor disagrees'
                USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM users actor
            WHERE actor.id = NEW.actor_id
              AND actor.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'ARB submission event actor is outside its tenant'
                USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM command_idempotency_records receipt
            JOIN operation_results result
              ON result.id = receipt.operation_result_id
             AND result.receipt_id = receipt.id
            JOIN arb_review_cycles provenance_cycle
              ON provenance_cycle.id = NEW.review_cycle_id
             AND provenance_cycle.organization_id = NEW.organization_id
            WHERE receipt.id = NEW.command_receipt_id
              AND receipt.organization_id = NEW.organization_id
              AND receipt.actor_id = NEW.actor_id
              AND receipt.operation = 'arb.submit'
              AND receipt.natural_key =
                  'arb-submission:' || NEW.organization_id::text || ':' ||
                  NEW.subject_type || ':' || NEW.subject_id::text || ':after:' ||
                  COALESCE(provenance_cycle.predecessor_cycle_id::text, 'root')
              AND receipt.status = 'succeeded'
              AND receipt.operation_result_id IS NOT NULL
              AND receipt.completed_at IS NOT NULL
              AND receipt.lease_generation = NEW.command_generation
              AND result.organization_id = NEW.organization_id
              AND result.actor_id = NEW.actor_id
              AND result.operation = receipt.operation
              AND result.natural_key = receipt.natural_key
              AND result.request_digest = receipt.request_digest
              AND result.receipt_generation = NEW.command_generation
              AND result.object_ids::jsonb @> jsonb_build_object(
                  'review_cycle_id', NEW.review_cycle_id,
                  'review_item_id', NEW.review_item_id,
                  'evidence_id', COALESCE(
                      NEW.decision_brief_version_id,
                      NEW.solution_evidence_snapshot_id,
                      NEW.subject_evidence_snapshot_id
                  )
              )
        ) THEN
            RAISE EXCEPTION 'ARB submission event receipt/result provenance is invalid'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """


def ensure_arb_submission_event_guards(connection):
    """Create or repair the event's membership and append-only guards."""
    if connection.dialect.name != "postgresql":
        return
    preparer = connection.dialect.identifier_preparer
    quoted_schema = preparer.quote(connection.exec_driver_sql("SELECT current_schema()").scalar())
    connection.exec_driver_sql(_membership_sql(quoted_schema))
    from app.models.transformation_db_guards import (
        _ORG_DELETION_ADMITS_SQL,
        _render_guard_sql,
    )

    connection.exec_driver_sql(
        _render_guard_sql(connection, _ORG_DELETION_ADMITS_SQL, quoted_schema)
    )
    connection.exec_driver_sql(
        f"""
        CREATE OR REPLACE FUNCTION {quoted_schema}.archie_reject_arb_submission_event_mutation()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF {quoted_schema}.archie_org_deletion_admits(OLD.organization_id) THEN
                    RETURN OLD;
                END IF;
            END IF;
            RAISE EXCEPTION 'ARB submission events are append-only' USING ERRCODE = '55000';
        END;
        $$
        """
    )
    connection.exec_driver_sql(
        f"""
        DROP TRIGGER IF EXISTS trg_arb_submission_event_membership
            ON {quoted_schema}.arb_submission_events;
        CREATE CONSTRAINT TRIGGER trg_arb_submission_event_membership
            AFTER INSERT OR UPDATE ON {quoted_schema}.arb_submission_events
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION {quoted_schema}.archie_validate_arb_submission_event();
        DROP TRIGGER IF EXISTS trg_arb_submission_event_immutable
            ON {quoted_schema}.arb_submission_events;
        CREATE TRIGGER trg_arb_submission_event_immutable
            BEFORE UPDATE OR DELETE ON {quoted_schema}.arb_submission_events
            FOR EACH ROW EXECUTE FUNCTION {quoted_schema}.archie_reject_arb_submission_event_mutation();
        """
    )


@event.listens_for(ARBSubmissionEvent.__table__, "after_create")
def _install_submission_event_guards(_target, connection, **_kwargs):
    ensure_arb_submission_event_guards(connection)


def _reject_mutation(_mapper, _connection, _target):
    raise ValueError("ARB submission events are append-only")


event.listen(ARBSubmissionEvent, "before_update", _reject_mutation)
event.listen(ARBSubmissionEvent, "before_delete", _reject_mutation)


__all__ = ["ARBSubmissionEvent", "ensure_arb_submission_event_guards"]
