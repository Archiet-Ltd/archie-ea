"""Immutable evidence captured specifically for canonical ARB conditions."""

from __future__ import annotations

import hashlib

from sqlalchemy import event

from app import db
from app.models.mixins import TenantMixin


_TYPED_SHAPE = (
    "((subject_type='decision_brief' AND subject_id=decision_brief_id "
    "AND decision_brief_id IS NOT NULL AND decision_brief_version_id IS NOT NULL "
    "AND solution_id IS NULL AND architecture_model_id IS NULL AND adr_id IS NULL "
    "AND solution_evidence_snapshot_id IS NULL AND subject_evidence_snapshot_id IS NULL) OR "
    "(subject_type='solution' AND subject_id=solution_id AND solution_id IS NOT NULL "
    "AND solution_evidence_snapshot_id IS NOT NULL AND decision_brief_id IS NULL "
    "AND architecture_model_id IS NULL AND adr_id IS NULL "
    "AND decision_brief_version_id IS NULL AND subject_evidence_snapshot_id IS NULL) OR "
    "(subject_type='architecture_model' AND subject_id=architecture_model_id "
    "AND architecture_model_id IS NOT NULL AND subject_evidence_snapshot_id IS NOT NULL "
    "AND decision_brief_id IS NULL AND solution_id IS NULL AND adr_id IS NULL "
    "AND decision_brief_version_id IS NULL AND solution_evidence_snapshot_id IS NULL) OR "
    "(subject_type='adr' AND subject_id=adr_id AND adr_id IS NOT NULL "
    "AND subject_evidence_snapshot_id IS NOT NULL AND decision_brief_id IS NULL "
    "AND solution_id IS NULL AND architecture_model_id IS NULL "
    "AND decision_brief_version_id IS NULL AND solution_evidence_snapshot_id IS NULL))"
)

IMMUTABLE_CONDITION_EVIDENCE_FIELDS = frozenset(
    {
        "organization_id", "condition_id", "condition_revision", "decision_event_id", "review_cycle_id",
        "review_item_id", "subject_type", "subject_id", "value_json", "content_hash",
        "source_identity", "source_type", "source_version", "source_checksum",
        "observed_at", "collected_at", "freshness_status", "freshness_expires_at",
        "freshness_rule_version", "created_by_id", "command_receipt_id",
        "command_generation", "created_at",
    }
)
CONDITION_EVIDENCE_MEMBERSHIP_IS_EXACT = True


class ARBConditionEvidenceRecord(TenantMixin, db.Model):
    __tablename__ = "arb_condition_evidence_records"

    id = db.Column(db.Integer, primary_key=True)
    condition_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "arb_canonical_conditions.id",
            ondelete="RESTRICT",
            name="fk_arb_condition_evidence_condition",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
        index=True,
    )
    condition_revision = db.Column(db.Integer, nullable=False)
    decision_event_id = db.Column(db.Integer, db.ForeignKey("arb_decision_events.id", ondelete="RESTRICT"), nullable=False)
    review_cycle_id = db.Column(db.Integer, db.ForeignKey("arb_review_cycles.id", ondelete="RESTRICT"), nullable=False)
    review_item_id = db.Column(db.Integer, db.ForeignKey("arb_review_items.id", ondelete="RESTRICT"), nullable=False)
    subject_type = db.Column(db.String(40), nullable=False)
    subject_id = db.Column(db.Integer, nullable=False)
    decision_brief_id = db.Column(db.Integer, db.ForeignKey("decision_briefs.id", ondelete="RESTRICT"))
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="RESTRICT"))
    architecture_model_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id", ondelete="RESTRICT"))
    # E2E-H: repointed from architecture_decision_records (0 rows in
    # production, unreachable via any real UI) to architecture_decisions,
    # the model the real Decision Register writes.
    adr_id = db.Column(db.Integer, db.ForeignKey("architecture_decisions.id", ondelete="RESTRICT"))
    decision_brief_version_id = db.Column(db.Integer, db.ForeignKey("decision_brief_versions.id", ondelete="RESTRICT"))
    solution_evidence_snapshot_id = db.Column(db.Integer, db.ForeignKey("arb_submission_evidence_snapshots.id", ondelete="RESTRICT"))
    subject_evidence_snapshot_id = db.Column(db.Integer, db.ForeignKey("arb_subject_evidence_snapshots.id", ondelete="RESTRICT"))
    value_json = db.Column(db.JSON, nullable=False)
    canonical_document = db.Column(db.Text, nullable=False)
    content_hash = db.Column(db.String(64), nullable=False)
    source_identity = db.Column(db.String(1024), nullable=False)
    source_type = db.Column(db.String(80), nullable=False)
    source_version = db.Column(db.String(512), nullable=False)
    source_checksum = db.Column(db.String(64), nullable=False)
    observed_at = db.Column(db.DateTime(timezone=True), nullable=False)
    collected_at = db.Column(db.DateTime(timezone=True), nullable=False)
    freshness_status = db.Column(db.String(30), nullable=False)
    freshness_expires_at = db.Column(db.DateTime(timezone=True))
    freshness_rule_version = db.Column(db.String(160), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    command_receipt_id = db.Column(db.Integer, db.ForeignKey("command_idempotency_records.id", ondelete="RESTRICT"), nullable=False, unique=True)
    command_generation = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.CheckConstraint(_TYPED_SHAPE, name="ck_arb_condition_evidence_typed_shape"),
        db.CheckConstraint("length(content_hash) = 64", name="ck_arb_condition_evidence_hash"),
        db.CheckConstraint(
            "encode(sha256(convert_to(canonical_document,'UTF8')),'hex') = content_hash",
            name="ck_arb_condition_evidence_document_hash",
        ),
        db.CheckConstraint("length(source_checksum) = 64", name="ck_arb_condition_evidence_source_hash"),
        db.CheckConstraint("freshness_status IN ('fresh','stale','unknown','not_applicable')", name="ck_arb_condition_evidence_freshness"),
        db.CheckConstraint("command_generation > 0", name="ck_arb_condition_evidence_generation"),
        db.CheckConstraint("condition_revision > 0", name="ck_arb_condition_evidence_revision"),
        db.UniqueConstraint("organization_id", "condition_id", "content_hash", name="uq_arb_condition_evidence_content"),
    )

    def recompute_content_hash(self):
        return hashlib.sha256(self.canonical_document.encode("utf-8")).hexdigest()


def _membership_sql(schema):
    return f"""
CREATE OR REPLACE FUNCTION {schema}.archie_validate_arb_condition_evidence() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,{schema} AS $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM arb_canonical_conditions condition
 JOIN arb_decision_events decision ON decision.id=condition.decision_event_id
 JOIN arb_review_cycles cycle ON cycle.id=condition.review_cycle_id
 JOIN arb_review_items review ON review.id=condition.review_item_id
 WHERE condition.id=NEW.condition_id AND condition.organization_id=NEW.organization_id
 AND decision.id=NEW.decision_event_id AND decision.organization_id=NEW.organization_id
 AND cycle.id=NEW.review_cycle_id AND cycle.organization_id=NEW.organization_id
 AND review.id=NEW.review_item_id AND review.organization_id=NEW.organization_id
 AND decision.review_cycle_id=cycle.id AND decision.review_item_id=review.id
 AND decision.subject_type=NEW.subject_type AND decision.subject_id=NEW.subject_id
 AND decision.decision_brief_id IS NOT DISTINCT FROM NEW.decision_brief_id
 AND decision.solution_id IS NOT DISTINCT FROM NEW.solution_id
 AND decision.architecture_model_id IS NOT DISTINCT FROM NEW.architecture_model_id
 AND decision.adr_id IS NOT DISTINCT FROM NEW.adr_id
 AND decision.decision_brief_version_id IS NOT DISTINCT FROM NEW.decision_brief_version_id
 AND decision.solution_evidence_snapshot_id IS NOT DISTINCT FROM NEW.solution_evidence_snapshot_id
 AND decision.subject_evidence_snapshot_id IS NOT DISTINCT FROM NEW.subject_evidence_snapshot_id)
 THEN RAISE EXCEPTION 'ARB condition evidence membership is invalid' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS (SELECT 1 FROM users actor WHERE actor.id=NEW.created_by_id AND actor.organization_id=NEW.organization_id)
 THEN RAISE EXCEPTION 'ARB condition evidence actor is outside tenant' USING ERRCODE='23514'; END IF;
 IF encode(sha256(convert_to(NEW.canonical_document,'UTF8')),'hex') <> NEW.content_hash
 THEN RAISE EXCEPTION 'ARB condition evidence content hash is invalid' USING ERRCODE='23514'; END IF;
 BEGIN
  IF jsonb_typeof(NEW.canonical_document::jsonb) <> 'object'
   OR (NEW.canonical_document::jsonb->>'source_identity') IS DISTINCT FROM NEW.source_identity
   OR (NEW.canonical_document::jsonb->>'source_type') IS DISTINCT FROM NEW.source_type
   OR (NEW.canonical_document::jsonb->>'source_version') IS DISTINCT FROM NEW.source_version
   OR (NEW.canonical_document::jsonb->>'source_checksum') IS DISTINCT FROM NEW.source_checksum
   OR (NEW.canonical_document::jsonb->>'freshness_status') IS DISTINCT FROM NEW.freshness_status
   OR (NEW.canonical_document::jsonb->>'freshness_rule_version') IS DISTINCT FROM NEW.freshness_rule_version
   OR (NEW.canonical_document::jsonb->'value_json') IS DISTINCT FROM NEW.value_json::jsonb
   OR (NEW.canonical_document::jsonb->>'observed_at')::timestamptz IS DISTINCT FROM NEW.observed_at
   OR (CASE WHEN NEW.freshness_expires_at IS NULL
      THEN NEW.canonical_document::jsonb->'freshness_expires_at' <> 'null'::jsonb
      ELSE (NEW.canonical_document::jsonb->>'freshness_expires_at')::timestamptz IS DISTINCT FROM NEW.freshness_expires_at END)
   OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(NEW.canonical_document::jsonb) key)
      IS DISTINCT FROM ARRAY['freshness_expires_at','freshness_rule_version','freshness_status','observed_at','source_checksum','source_identity','source_type','source_version','value_json']::text[]
  THEN RAISE EXCEPTION 'ARB condition evidence canonical document disagrees with stored fields' USING ERRCODE='23514'; END IF;
 EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
  RAISE EXCEPTION 'ARB condition evidence canonical document is invalid' USING ERRCODE='23514';
 END;
 IF NOT EXISTS (SELECT 1 FROM command_idempotency_records receipt
 JOIN operation_results result ON result.id=receipt.operation_result_id AND result.receipt_id=receipt.id
 JOIN command_materialisations materialisation ON materialisation.receipt_id=receipt.id
 WHERE receipt.id=NEW.command_receipt_id AND receipt.organization_id=NEW.organization_id
 AND receipt.actor_id=NEW.created_by_id AND receipt.operation='arb.condition.evidence.capture'
 AND receipt.natural_key='arb-condition-evidence:' || NEW.organization_id::text || ':' || NEW.condition_id::text || ':' || NEW.condition_revision::text
 AND receipt.status='succeeded' AND receipt.completed_at IS NOT NULL
 AND receipt.lease_generation=NEW.command_generation
 AND result.organization_id=NEW.organization_id AND result.actor_id=NEW.created_by_id
 AND result.operation=receipt.operation AND result.natural_key=receipt.natural_key
 AND result.request_digest=receipt.request_digest AND result.receipt_generation=NEW.command_generation
 AND materialisation.organization_id=NEW.organization_id AND materialisation.actor_id=NEW.created_by_id
 AND materialisation.operation=receipt.operation AND materialisation.natural_key=receipt.natural_key
 AND materialisation.request_digest=receipt.request_digest
 AND materialisation.receipt_generation=NEW.command_generation
 AND result.object_ids::jsonb @> jsonb_build_object('condition_id',NEW.condition_id,'condition_evidence_id',NEW.id,'condition_revision',NEW.condition_revision)
 AND materialisation.object_ids::jsonb @> jsonb_build_object('condition_id',NEW.condition_id,'condition_evidence_id',NEW.id,'condition_revision',NEW.condition_revision))
 THEN RAISE EXCEPTION 'ARB condition evidence command provenance is invalid' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
"""


def _ensure_condition_evidence_condition_fk(connection):
    """Repair the cyclic condition/evidence FK on upgraded PostgreSQL schemas.

    arb_canonical_conditions and arb_condition_evidence_records are mutually append-only and
    reference each other (a condition's submitted/fulfilment evidence id; an evidence row's own
    condition id), so the deletion planner has no row order that satisfies both constraints at
    once and neither guard admits the UPDATE a column-null cycle-break would need. Deferring this
    one edge to commit removes its ordering requirement outright -- by commit every row on both
    sides of the cycle is gone, so there is nothing left for it to check.
    """
    tables_ready = connection.exec_driver_sql(
        "SELECT to_regclass(current_schema() || '.arb_condition_evidence_records') "
        "IS NOT NULL AND to_regclass(current_schema() || '.arb_canonical_conditions') IS NOT NULL"
    ).scalar()
    if not tables_ready:
        return
    row = connection.exec_driver_sql(
        """
        SELECT c.conname, c.condeferrable, c.condeferred,
               target.relname AS target_table
        FROM pg_constraint AS c
        JOIN pg_class AS source ON source.oid = c.conrelid
        JOIN pg_namespace AS n ON n.oid = source.relnamespace
        JOIN pg_class AS target ON target.oid = c.confrelid
        WHERE n.nspname = current_schema()
          AND source.relname = 'arb_condition_evidence_records'
          AND c.contype = 'f'
          AND c.conkey = ARRAY[
              (SELECT attnum FROM pg_attribute
               WHERE attrelid = source.oid AND attname = 'condition_id')
          ]::smallint[]
        """
    ).mappings().one_or_none()
    if (
        row is not None
        and row["conname"] == "fk_arb_condition_evidence_condition"
        and row["target_table"] == "arb_canonical_conditions"
        and row["condeferrable"]
        and row["condeferred"]
    ):
        return
    if row is not None:
        preparer = connection.dialect.identifier_preparer
        constraint_name = preparer.quote(row["conname"])
        connection.exec_driver_sql(
            "ALTER TABLE arb_condition_evidence_records "
            f"DROP CONSTRAINT {constraint_name}"
        )
    connection.exec_driver_sql(
        "ALTER TABLE arb_condition_evidence_records "
        "ADD CONSTRAINT fk_arb_condition_evidence_condition "
        "FOREIGN KEY (condition_id) REFERENCES arb_canonical_conditions(id) "
        "DEFERRABLE INITIALLY DEFERRED"
    )


def ensure_arb_condition_evidence_guards(connection):
    if connection.dialect.name != "postgresql":
        return
    schema = connection.exec_driver_sql("SELECT current_schema()").scalar()
    q = connection.dialect.identifier_preparer.quote(schema)
    _ensure_condition_evidence_condition_fk(connection)
    connection.exec_driver_sql(_membership_sql(q))
    from app.models.transformation_db_guards import (
        _ORG_DELETION_ADMITS_SQL,
        _render_guard_sql,
    )

    connection.exec_driver_sql(
        _render_guard_sql(connection, _ORG_DELETION_ADMITS_SQL, q)
    )
    connection.exec_driver_sql(f"""CREATE OR REPLACE FUNCTION {q}.archie_reject_arb_condition_evidence_mutation() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF TG_OP='DELETE' THEN
   IF {q}.archie_org_deletion_admits(OLD.organization_id) THEN RETURN OLD; END IF;
 END IF;
 RAISE EXCEPTION 'ARB condition evidence is append-only' USING ERRCODE='55000'; END $$;
DROP TRIGGER IF EXISTS trg_arb_condition_evidence_membership ON {q}.arb_condition_evidence_records; CREATE CONSTRAINT TRIGGER trg_arb_condition_evidence_membership AFTER INSERT OR UPDATE ON {q}.arb_condition_evidence_records DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.archie_validate_arb_condition_evidence();
DROP TRIGGER IF EXISTS trg_arb_condition_evidence_immutable ON {q}.arb_condition_evidence_records; CREATE TRIGGER trg_arb_condition_evidence_immutable BEFORE UPDATE OR DELETE ON {q}.arb_condition_evidence_records FOR EACH ROW EXECUTE FUNCTION {q}.archie_reject_arb_condition_evidence_mutation();""")


@event.listens_for(ARBConditionEvidenceRecord.__table__, "after_create")
def _install(_target, connection, **_kwargs):
    ensure_arb_condition_evidence_guards(connection)


def _reject(_mapper, _connection, _target):
    raise ValueError("ARB condition evidence is append-only")


event.listen(ARBConditionEvidenceRecord, "before_update", _reject)
event.listen(ARBConditionEvidenceRecord, "before_delete", _reject)

__all__ = [
    "ARBConditionEvidenceRecord", "CONDITION_EVIDENCE_MEMBERSHIP_IS_EXACT",
    "IMMUTABLE_CONDITION_EVIDENCE_FIELDS", "ensure_arb_condition_evidence_guards",
]
