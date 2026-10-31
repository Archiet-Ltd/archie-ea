"""Canonical immutable typed ARB decisions and execution-blocking conditions."""

from sqlalchemy import event, inspect

from app import db
from app.models.mixins import TenantMixin


_TYPED_SHAPE = (
    "((subject_type = 'decision_brief' AND subject_id = decision_brief_id "
    "AND decision_brief_id IS NOT NULL AND decision_brief_version_id IS NOT NULL "
    "AND solution_id IS NULL AND architecture_model_id IS NULL AND adr_id IS NULL "
    "AND solution_evidence_snapshot_id IS NULL AND subject_evidence_snapshot_id IS NULL) OR "
    "(subject_type = 'solution' AND subject_id = solution_id AND solution_id IS NOT NULL "
    "AND solution_evidence_snapshot_id IS NOT NULL AND decision_brief_id IS NULL "
    "AND architecture_model_id IS NULL AND adr_id IS NULL AND decision_brief_version_id IS NULL "
    "AND subject_evidence_snapshot_id IS NULL) OR "
    "(subject_type = 'architecture_model' AND subject_id = architecture_model_id "
    "AND architecture_model_id IS NOT NULL AND subject_evidence_snapshot_id IS NOT NULL "
    "AND decision_brief_id IS NULL AND solution_id IS NULL AND adr_id IS NULL "
    "AND decision_brief_version_id IS NULL AND solution_evidence_snapshot_id IS NULL) OR "
    "(subject_type = 'adr' AND subject_id = adr_id AND adr_id IS NOT NULL "
    "AND subject_evidence_snapshot_id IS NOT NULL AND decision_brief_id IS NULL "
    "AND solution_id IS NULL AND architecture_model_id IS NULL "
    "AND decision_brief_version_id IS NULL AND solution_evidence_snapshot_id IS NULL))"
)


class ARBDecisionEvent(TenantMixin, db.Model):
    __tablename__ = "arb_decision_events"

    id = db.Column(db.Integer, primary_key=True)
    review_cycle_id = db.Column(db.Integer, db.ForeignKey("arb_review_cycles.id", ondelete="RESTRICT"), nullable=False, unique=True)
    review_item_id = db.Column(db.Integer, db.ForeignKey("arb_review_items.id", ondelete="RESTRICT"), nullable=False, unique=True)
    outcome = db.Column(db.String(40), nullable=False)
    from_state = db.Column(db.String(40), nullable=False)
    to_state = db.Column(db.String(40), nullable=False)
    rationale = db.Column(db.Text, nullable=False)
    conditions_json = db.Column(db.JSON, nullable=False, default=list, server_default=db.text("'[]'::json"))
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
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    command_receipt_id = db.Column(db.Integer, db.ForeignKey("command_idempotency_records.id", ondelete="RESTRICT"), nullable=False, unique=True)
    command_generation = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.CheckConstraint(_TYPED_SHAPE, name="ck_arb_decision_event_typed_shape"),
        db.CheckConstraint("outcome IN ('approved','approved_with_conditions','rejected','returned_for_evidence','returned_for_options') AND to_state = outcome AND from_state IN ('submitted','under_review','pending_information','pending_info','pending')", name="ck_arb_decision_event_outcome"),
        db.CheckConstraint("length(btrim(rationale)) > 0 AND command_generation > 0", name="ck_arb_decision_event_required"),
        db.CheckConstraint("json_typeof(conditions_json) = 'array' AND ((outcome = 'approved_with_conditions' AND json_array_length(conditions_json) > 0) OR (outcome <> 'approved_with_conditions' AND json_array_length(conditions_json) = 0))", name="ck_arb_decision_event_conditions"),
    )


class ARBCondition(TenantMixin, db.Model):
    __tablename__ = "arb_canonical_conditions"

    id = db.Column(db.Integer, primary_key=True)
    decision_event_id = db.Column(db.Integer, db.ForeignKey("arb_decision_events.id", ondelete="RESTRICT"), nullable=False)
    review_cycle_id = db.Column(db.Integer, db.ForeignKey("arb_review_cycles.id", ondelete="RESTRICT"), nullable=False)
    review_item_id = db.Column(db.Integer, db.ForeignKey("arb_review_items.id", ondelete="RESTRICT"), nullable=False)
    condition_number = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(80))
    due_date = db.Column(db.Date)
    blocks_execution = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    status = db.Column(db.String(30), nullable=False, default="pending", server_default="pending")
    revision = db.Column(db.Integer, nullable=True, default=1, server_default="1")
    responsible_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    submitted_evidence_id = db.Column(db.Integer, db.ForeignKey("arb_condition_evidence_records.id", ondelete="RESTRICT", use_alter=True, name="fk_arb_condition_submitted_evidence"), nullable=True)
    evidence_submitted_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    evidence_submitted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    verified_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    verified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    fulfilled_at = db.Column(db.DateTime(timezone=True))
    fulfilled_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"))
    fulfilment_evidence_id = db.Column(db.Integer, db.ForeignKey("arb_condition_evidence_records.id", ondelete="RESTRICT", use_alter=True, name="fk_arb_condition_fulfilment_evidence"))
    waived_at = db.Column(db.DateTime(timezone=True))
    waived_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"))
    waiver_reason = db.Column(db.Text)
    waiver_expires_at = db.Column(db.DateTime(timezone=True))
    compensating_control = db.Column(db.Text)
    waiver_prior_status = db.Column(db.String(30), nullable=True)
    waiver_scope_json = db.Column(db.JSON(none_as_null=True), nullable=True)
    legacy_lifecycle_provenance = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("organization_id", "review_cycle_id", "condition_number", name="uq_arb_condition_number"),
        db.CheckConstraint("length(btrim(condition_number)) > 0 AND length(btrim(description)) > 0", name="ck_arb_condition_terms"),
        db.CheckConstraint("blocks_execution IS TRUE", name="ck_arb_condition_blocks_execution"),
        db.CheckConstraint("status IN ('pending','evidence_submitted','fulfilled','waived') AND revision > 0", name="ck_arb_condition_status"),
        db.CheckConstraint(
            "legacy_lifecycle_provenance IS NULL OR "
            "(status='fulfilled' AND jsonb_typeof(legacy_lifecycle_provenance::jsonb)='object' "
            "AND legacy_lifecycle_provenance::jsonb ?& ARRAY['classification','legacy_fulfilment_evidence_id'] "
            "AND legacy_lifecycle_provenance::jsonb - ARRAY['classification','legacy_fulfilment_evidence_id'] = '{}'::jsonb "
            "AND legacy_lifecycle_provenance::jsonb->>'classification'='pre_c3_fulfilment' "
            "AND jsonb_typeof(legacy_lifecycle_provenance::jsonb->'legacy_fulfilment_evidence_id') IN ('number','null')) OR "
            "(status='waived' AND legacy_lifecycle_provenance::jsonb="
            "'{\"classification\":\"pre_c3_waiver\"}'::jsonb)",
            name="ck_arb_condition_legacy_provenance",
        ),
        db.CheckConstraint(
            "(status = 'pending' AND fulfilled_at IS NULL AND fulfilled_by_id IS NULL "
            "AND fulfilment_evidence_id IS NULL AND evidence_submitted_at IS NULL "
            "AND evidence_submitted_by_id IS NULL AND submitted_evidence_id IS NULL "
            "AND verified_at IS NULL AND verified_by_id IS NULL AND waived_at IS NULL "
            "AND waived_by_id IS NULL AND waiver_reason IS NULL "
            "AND waiver_expires_at IS NULL AND compensating_control IS NULL "
            "AND waiver_prior_status IS NULL AND waiver_scope_json IS NULL) OR "
            "(status = 'evidence_submitted' AND submitted_evidence_id IS NOT NULL "
            "AND evidence_submitted_by_id IS NOT NULL "
            "AND evidence_submitted_at IS NOT NULL AND fulfilled_at IS NULL "
            "AND fulfilled_by_id IS NULL AND fulfilment_evidence_id IS NULL "
            "AND verified_at IS NULL AND verified_by_id IS NULL AND waived_at IS NULL "
            "AND waived_by_id IS NULL AND waiver_reason IS NULL AND waiver_expires_at IS NULL "
            "AND compensating_control IS NULL AND waiver_prior_status IS NULL "
            "AND waiver_scope_json IS NULL) OR "
            "(status = 'fulfilled' AND fulfilled_at IS NOT NULL "
            "AND fulfilled_by_id IS NOT NULL "
            "AND ((fulfilment_evidence_id IS NOT NULL AND legacy_lifecycle_provenance IS NULL) "
            "OR (fulfilment_evidence_id IS NULL AND legacy_lifecycle_provenance IS NOT NULL)) "
            "AND verified_at IS NOT NULL AND verified_by_id IS NOT NULL "
            "AND waived_at IS NULL AND waived_by_id IS NULL AND waiver_reason IS NULL "
            "AND waiver_expires_at IS NULL AND compensating_control IS NULL "
            "AND waiver_prior_status IS NULL AND waiver_scope_json IS NULL) OR "
            "(status = 'waived' AND fulfilled_at IS NULL AND fulfilled_by_id IS NULL "
            "AND fulfilment_evidence_id IS NULL AND waived_at IS NOT NULL "
            "AND waived_by_id IS NOT NULL AND length(btrim(waiver_reason)) > 0 "
            "AND waiver_expires_at > waived_at "
            "AND length(btrim(compensating_control)) > 0 "
            "AND ((waiver_prior_status IN ('pending','evidence_submitted') "
            "AND waiver_scope_json IS NOT NULL AND legacy_lifecycle_provenance IS NULL) "
            "OR (waiver_prior_status IS NULL AND waiver_scope_json IS NULL "
            "AND legacy_lifecycle_provenance IS NOT NULL)))",
            name="ck_arb_condition_lifecycle",
        ),
    )

    __mapper_args__ = {"version_id_col": revision}


class ARBWaiverExpiryCheckpoint(db.Model):
    """Durable fair-scan cursor for one explicit set of scheduler tenants."""

    __tablename__ = "arb_waiver_expiry_checkpoints"

    scope_key = db.Column(db.String(64), primary_key=True)
    organization_ids_json = db.Column(db.JSON, nullable=False)
    cursor_waiver_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    cursor_organization_id = db.Column(db.Integer, nullable=True)
    cursor_condition_id = db.Column(db.Integer, nullable=True)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )

    __table_args__ = (
        db.CheckConstraint(
            "(cursor_waiver_expires_at IS NULL AND cursor_organization_id IS NULL "
            "AND cursor_condition_id IS NULL) OR "
            "(cursor_waiver_expires_at IS NOT NULL "
            "AND cursor_organization_id IS NOT NULL "
            "AND cursor_condition_id IS NOT NULL)",
            name="ck_arb_waiver_expiry_checkpoint_cursor",
        ),
    )


def _decision_membership_sql(schema):
    return f"""
CREATE OR REPLACE FUNCTION {schema}.archie_validate_arb_decision_event() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,{schema} AS $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM arb_review_cycles cycle JOIN arb_review_items review
 ON review.id=NEW.review_item_id AND review.review_cycle_id=cycle.id
 WHERE cycle.id=NEW.review_cycle_id AND cycle.organization_id=NEW.organization_id
 AND review.organization_id=NEW.organization_id AND cycle.status=NEW.outcome
 AND cycle.terminal_outcome = NEW.outcome AND cycle.closed_at IS NOT NULL
 AND review.status=NEW.outcome AND review.decision = NEW.outcome
 AND review.decided_by_id = NEW.actor_id AND review.submitter_id <> NEW.actor_id
 AND review.decision_rationale=NEW.rationale
 AND review.conditions::jsonb IS NOT DISTINCT FROM NEW.conditions_json::jsonb
 AND review.decision_date IS NOT NULL AND review.review_completed_at IS NOT NULL
 AND review.decision_date=(cycle.closed_at AT TIME ZONE 'UTC')
 AND review.review_completed_at=(cycle.closed_at AT TIME ZONE 'UTC')
 AND cycle.subject_type=NEW.subject_type
 AND cycle.subject_id=NEW.subject_id AND review.subject_type=NEW.subject_type
 AND review.subject_id=NEW.subject_id AND cycle.decision_brief_id IS NOT DISTINCT FROM NEW.decision_brief_id
 AND review.decision_brief_id IS NOT DISTINCT FROM NEW.decision_brief_id
 AND cycle.solution_id IS NOT DISTINCT FROM NEW.solution_id AND review.solution_id IS NOT DISTINCT FROM NEW.solution_id
 AND cycle.architecture_model_id IS NOT DISTINCT FROM NEW.architecture_model_id AND review.architecture_model_id IS NOT DISTINCT FROM NEW.architecture_model_id
 AND cycle.adr_id IS NOT DISTINCT FROM NEW.adr_id AND review.adr_id IS NOT DISTINCT FROM NEW.adr_id
 AND cycle.decision_brief_version_id IS NOT DISTINCT FROM NEW.decision_brief_version_id AND review.decision_brief_version_id IS NOT DISTINCT FROM NEW.decision_brief_version_id
 AND cycle.solution_evidence_snapshot_id IS NOT DISTINCT FROM NEW.solution_evidence_snapshot_id AND review.solution_evidence_snapshot_id IS NOT DISTINCT FROM NEW.solution_evidence_snapshot_id
 AND cycle.subject_evidence_snapshot_id IS NOT DISTINCT FROM NEW.subject_evidence_snapshot_id AND review.subject_evidence_snapshot_id IS NOT DISTINCT FROM NEW.subject_evidence_snapshot_id)
 THEN RAISE EXCEPTION 'ARB decision projection, separation, or typed membership is invalid' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS (SELECT 1 FROM command_idempotency_records receipt
 JOIN operation_results result ON result.id=receipt.operation_result_id AND result.receipt_id=receipt.id
 JOIN command_materialisations materialisation ON materialisation.receipt_id=receipt.id
 WHERE receipt.id=NEW.command_receipt_id AND receipt.organization_id=NEW.organization_id
 AND receipt.actor_id=NEW.actor_id AND receipt.operation='arb.decision.record'
 AND receipt.natural_key='arb-decision:' || NEW.organization_id::text || ':' || NEW.review_cycle_id::text
 AND receipt.status = 'succeeded' AND receipt.completed_at IS NOT NULL
 AND receipt.lease_generation=NEW.command_generation AND result.organization_id=NEW.organization_id
 AND result.actor_id=NEW.actor_id AND result.operation=receipt.operation
 AND result.natural_key=receipt.natural_key AND result.request_digest=receipt.request_digest
 AND result.receipt_generation=NEW.command_generation
 AND materialisation.organization_id=NEW.organization_id AND materialisation.actor_id=NEW.actor_id
 AND materialisation.operation=receipt.operation AND materialisation.natural_key=receipt.natural_key
 AND materialisation.request_digest=receipt.request_digest AND materialisation.receipt_generation=NEW.command_generation
 AND result.object_ids::jsonb @> jsonb_build_object('review_cycle_id',NEW.review_cycle_id,'review_item_id',NEW.review_item_id,'decision_event_id',NEW.id)
 AND materialisation.object_ids::jsonb @> jsonb_build_object('review_cycle_id',NEW.review_cycle_id,'review_item_id',NEW.review_item_id,'decision_event_id',NEW.id)
 AND result.object_ids::jsonb->'condition_ids' IS NOT DISTINCT FROM COALESCE((SELECT jsonb_agg(condition.id ORDER BY condition.condition_number) FROM arb_canonical_conditions condition WHERE condition.decision_event_id=NEW.id),'[]'::jsonb)
 AND materialisation.object_ids::jsonb->'condition_ids' IS NOT DISTINCT FROM COALESCE((SELECT jsonb_agg(condition.id ORDER BY condition.condition_number) FROM arb_canonical_conditions condition WHERE condition.decision_event_id=NEW.id),'[]'::jsonb))
 THEN RAISE EXCEPTION 'ARB decision command result/materialisation provenance is invalid' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS (SELECT 1 FROM users actor WHERE actor.id=NEW.actor_id AND actor.organization_id=NEW.organization_id)
 THEN RAISE EXCEPTION 'ARB decision actor is outside its tenant' USING ERRCODE='23514'; END IF;
 IF (NEW.outcome='approved_with_conditions' AND (SELECT count(*) FROM arb_canonical_conditions condition WHERE condition.decision_event_id=NEW.id AND condition.organization_id=NEW.organization_id)=0)
 OR (NEW.outcome<>'approved_with_conditions' AND EXISTS (SELECT 1 FROM arb_canonical_conditions condition WHERE condition.decision_event_id=NEW.id))
 THEN RAISE EXCEPTION 'ARB decision condition cardinality is invalid' USING ERRCODE='23514'; END IF;
 IF NEW.conditions_json::jsonb IS DISTINCT FROM COALESCE((SELECT jsonb_agg(jsonb_build_object('condition_number',condition.condition_number,'description',condition.description,'category',condition.category,'due_date',condition.due_date,'blocks_execution',condition.blocks_execution) ORDER BY condition.condition_number) FROM arb_canonical_conditions condition WHERE condition.decision_event_id=NEW.id),'[]'::jsonb)
 THEN RAISE EXCEPTION 'ARB decision canonical conditions disagree' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
"""


def _decision_open_state_sql(schema):
    return f"""
CREATE OR REPLACE FUNCTION {schema}.archie_validate_arb_decision_open_state() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,{schema} AS $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM arb_review_cycles cycle JOIN arb_review_items review
 ON review.id=NEW.review_item_id AND review.review_cycle_id=cycle.id
 WHERE cycle.id=NEW.review_cycle_id AND cycle.organization_id=NEW.organization_id
 AND review.organization_id=NEW.organization_id AND cycle.closed_at IS NULL
 AND cycle.terminal_outcome IS NULL AND review.decision IS NULL
 AND cycle.status=NEW.from_state AND review.status=NEW.from_state
 AND NEW.from_state IN ('submitted','under_review','pending_information','pending_info','pending'))
 THEN RAISE EXCEPTION 'ARB decision from_state is not the current open projection' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
"""


def _condition_membership_sql(schema):
    return f"""
CREATE OR REPLACE FUNCTION {schema}.archie_validate_arb_condition() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,{schema} AS $$ BEGIN
 IF TG_OP='INSERT' AND NEW.legacy_lifecycle_provenance IS NOT NULL
 THEN RAISE EXCEPTION 'legacy ARB condition provenance is reconcile-only' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS (SELECT 1 FROM arb_decision_events decision WHERE decision.id=NEW.decision_event_id
 AND decision.outcome = 'approved_with_conditions' AND decision.review_cycle_id = NEW.review_cycle_id
 AND decision.review_item_id = NEW.review_item_id AND decision.organization_id = NEW.organization_id)
 THEN RAISE EXCEPTION 'ARB condition membership is invalid' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
"""


def _condition_reconcile_sql(schema):
    status = next(
        str(item.sqltext)
        for item in ARBCondition.__table__.constraints
        if item.name == "ck_arb_condition_status"
    )
    lifecycle = next(
        str(item.sqltext)
        for item in ARBCondition.__table__.constraints
        if item.name == "ck_arb_condition_lifecycle"
    )
    legacy_provenance = next(
        str(item.sqltext)
        for item in ARBCondition.__table__.constraints
        if item.name == "ck_arb_condition_legacy_provenance"
    )
    return f"""
ALTER TABLE {schema}.arb_canonical_conditions
ADD COLUMN IF NOT EXISTS legacy_lifecycle_provenance JSON;
UPDATE {schema}.arb_canonical_conditions SET revision = 1 WHERE revision IS NULL;
UPDATE {schema}.arb_canonical_conditions condition
SET verified_at = fulfilled_at, verified_by_id = fulfilled_by_id,
    legacy_lifecycle_provenance = jsonb_build_object(
      'classification','pre_c3_fulfilment',
      'legacy_fulfilment_evidence_id',fulfilment_evidence_id)
WHERE condition.status='fulfilled' AND condition.legacy_lifecycle_provenance IS NULL
  AND (verified_at IS NULL OR verified_by_id IS NULL
       OR fulfilment_evidence_id IS NOT NULL)
  AND NOT EXISTS (
    SELECT 1 FROM {schema}.arb_condition_evidence_records evidence
    WHERE evidence.id=condition.fulfilment_evidence_id
      AND evidence.organization_id=condition.organization_id
      AND evidence.condition_id=condition.id
      AND evidence.decision_event_id=condition.decision_event_id
      AND evidence.review_cycle_id=condition.review_cycle_id
      AND evidence.review_item_id=condition.review_item_id);
UPDATE {schema}.arb_canonical_conditions
SET fulfilment_evidence_id = NULL
WHERE status='fulfilled' AND legacy_lifecycle_provenance->>'classification'='pre_c3_fulfilment';
UPDATE {schema}.arb_canonical_conditions
SET legacy_lifecycle_provenance = jsonb_build_object(
      'classification','pre_c3_waiver')
WHERE status='waived' AND legacy_lifecycle_provenance IS NULL
  AND waiver_prior_status IS NULL AND waiver_scope_json IS NULL;
SET CONSTRAINTS {schema}.trg_arb_condition_membership IMMEDIATE;
ALTER TABLE {schema}.arb_canonical_conditions ALTER COLUMN revision SET DEFAULT 1;
ALTER TABLE {schema}.arb_canonical_conditions ALTER COLUMN revision SET NOT NULL;
ALTER TABLE {schema}.arb_canonical_conditions DROP CONSTRAINT IF EXISTS ck_arb_condition_status;
ALTER TABLE {schema}.arb_canonical_conditions ADD CONSTRAINT ck_arb_condition_status CHECK ({status}) NOT VALID;
ALTER TABLE {schema}.arb_canonical_conditions VALIDATE CONSTRAINT ck_arb_condition_status;
ALTER TABLE {schema}.arb_canonical_conditions DROP CONSTRAINT IF EXISTS ck_arb_condition_lifecycle;
ALTER TABLE {schema}.arb_canonical_conditions ADD CONSTRAINT ck_arb_condition_lifecycle CHECK ({lifecycle}) NOT VALID;
ALTER TABLE {schema}.arb_canonical_conditions VALIDATE CONSTRAINT ck_arb_condition_lifecycle;
ALTER TABLE {schema}.arb_canonical_conditions DROP CONSTRAINT IF EXISTS ck_arb_condition_legacy_provenance;
ALTER TABLE {schema}.arb_canonical_conditions ADD CONSTRAINT ck_arb_condition_legacy_provenance CHECK ({legacy_provenance}) NOT VALID;
ALTER TABLE {schema}.arb_canonical_conditions VALIDATE CONSTRAINT ck_arb_condition_legacy_provenance;
ALTER TABLE {schema}.arb_canonical_conditions DROP CONSTRAINT IF EXISTS fk_arb_condition_submitted_evidence;
ALTER TABLE {schema}.arb_canonical_conditions ADD CONSTRAINT fk_arb_condition_submitted_evidence FOREIGN KEY (submitted_evidence_id) REFERENCES {schema}.arb_condition_evidence_records(id) ON DELETE RESTRICT NOT VALID;
ALTER TABLE {schema}.arb_canonical_conditions VALIDATE CONSTRAINT fk_arb_condition_submitted_evidence;
ALTER TABLE {schema}.arb_canonical_conditions DROP CONSTRAINT IF EXISTS arb_canonical_conditions_fulfilment_evidence_id_fkey;
ALTER TABLE {schema}.arb_canonical_conditions DROP CONSTRAINT IF EXISTS fk_arb_condition_fulfilment_evidence;
ALTER TABLE {schema}.arb_canonical_conditions ADD CONSTRAINT fk_arb_condition_fulfilment_evidence FOREIGN KEY (fulfilment_evidence_id) REFERENCES {schema}.arb_condition_evidence_records(id) ON DELETE RESTRICT NOT VALID;
ALTER TABLE {schema}.arb_canonical_conditions VALIDATE CONSTRAINT fk_arb_condition_fulfilment_evidence;
"""


def ensure_arb_decision_guards(connection):
    if connection.dialect.name != "postgresql":
        return
    schema = connection.exec_driver_sql("SELECT current_schema()").scalar()
    q = connection.dialect.identifier_preparer.quote(schema)
    connection.exec_driver_sql(_decision_membership_sql(q))
    connection.exec_driver_sql(_decision_open_state_sql(q))
    connection.exec_driver_sql(_condition_membership_sql(q))
    if connection.exec_driver_sql(
        "SELECT to_regclass(current_schema() || '.arb_condition_evidence_records')"
    ).scalar() is not None:
        connection.exec_driver_sql(_condition_reconcile_sql(q))
    from app.models.transformation_db_guards import (
        _ORG_DELETION_ADMITS_SQL,
        _render_guard_sql,
    )

    connection.exec_driver_sql(
        _render_guard_sql(connection, _ORG_DELETION_ADMITS_SQL, q)
    )
    connection.exec_driver_sql(f"""CREATE OR REPLACE FUNCTION {q}.archie_guard_arb_decision_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF TG_OP='DELETE' THEN
   IF {q}.archie_org_deletion_admits((to_jsonb(OLD) ->> 'organization_id')::integer) THEN RETURN OLD; END IF;
   RAISE EXCEPTION 'ARB decision history is append-only' USING ERRCODE='55000'; END IF;
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
   SELECT 1 FROM {q}.arb_condition_events condition_event
   JOIN {q}.command_idempotency_records receipt
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
 DROP TRIGGER IF EXISTS trg_arb_decision_event_open_state ON {q}.arb_decision_events; CREATE TRIGGER trg_arb_decision_event_open_state BEFORE INSERT ON {q}.arb_decision_events FOR EACH ROW EXECUTE FUNCTION {q}.archie_validate_arb_decision_open_state();
 DROP TRIGGER IF EXISTS trg_arb_decision_event_membership ON {q}.arb_decision_events; CREATE CONSTRAINT TRIGGER trg_arb_decision_event_membership AFTER INSERT OR UPDATE ON {q}.arb_decision_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.archie_validate_arb_decision_event();
 DROP TRIGGER IF EXISTS trg_arb_decision_event_immutable ON {q}.arb_decision_events; CREATE TRIGGER trg_arb_decision_event_immutable BEFORE UPDATE OR DELETE ON {q}.arb_decision_events FOR EACH ROW EXECUTE FUNCTION {q}.archie_guard_arb_decision_immutable();
 DROP TRIGGER IF EXISTS trg_arb_condition_membership ON {q}.arb_canonical_conditions; CREATE CONSTRAINT TRIGGER trg_arb_condition_membership AFTER INSERT OR UPDATE ON {q}.arb_canonical_conditions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.archie_validate_arb_condition();
 DROP TRIGGER IF EXISTS trg_arb_condition_immutable ON {q}.arb_canonical_conditions; CREATE TRIGGER trg_arb_condition_immutable BEFORE UPDATE OR DELETE ON {q}.arb_canonical_conditions FOR EACH ROW EXECUTE FUNCTION {q}.archie_guard_arb_decision_immutable();""")


@event.listens_for(ARBCondition.__table__, "after_create")
def _install(_target, connection, **_kwargs):
    if "arb_decision_events" in db.metadata.tables:
        ensure_arb_decision_guards(connection)


def _reject_decision_mutation(_mapper, _connection, _target):
    raise ValueError("ARB decision events are append-only")


def _guard_condition_terms(_mapper, _connection, target):
    immutable = {
        "organization_id", "decision_event_id", "review_cycle_id", "review_item_id",
        "condition_number", "description", "category", "due_date",
        "blocks_execution", "created_at",
    }
    if any(inspect(target).attrs[name].history.has_changes() for name in immutable):
        raise ValueError("ARB condition identity and terms are immutable")


event.listen(ARBDecisionEvent, "before_update", _reject_decision_mutation)
event.listen(ARBDecisionEvent, "before_delete", _reject_decision_mutation)
event.listen(ARBCondition, "before_update", _guard_condition_terms)
event.listen(ARBCondition, "before_delete", _reject_decision_mutation)


__all__ = [
    "ARBCondition",
    "ARBDecisionEvent",
    "ARBWaiverExpiryCheckpoint",
    "ensure_arb_decision_guards",
]
