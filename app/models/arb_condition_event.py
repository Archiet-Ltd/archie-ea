"""Immutable command-fenced lifecycle events for canonical ARB conditions."""

from sqlalchemy import event

from app import db
from app.models.mixins import TenantMixin


class ARBConditionEvent(TenantMixin, db.Model):
    __tablename__ = "arb_condition_events"

    id = db.Column(db.Integer, primary_key=True)
    condition_id = db.Column(db.Integer, db.ForeignKey("arb_canonical_conditions.id", ondelete="RESTRICT"), nullable=False)
    decision_event_id = db.Column(db.Integer, db.ForeignKey("arb_decision_events.id", ondelete="RESTRICT"), nullable=False)
    review_cycle_id = db.Column(db.Integer, db.ForeignKey("arb_review_cycles.id", ondelete="RESTRICT"), nullable=False)
    review_item_id = db.Column(db.Integer, db.ForeignKey("arb_review_items.id", ondelete="RESTRICT"), nullable=False)
    subject_type = db.Column(db.String(40), nullable=False)
    subject_id = db.Column(db.Integer, nullable=False)
    event_type = db.Column(db.String(40), nullable=False)
    from_state = db.Column(db.String(30), nullable=False)
    to_state = db.Column(db.String(30), nullable=False)
    condition_revision = db.Column(db.Integer, nullable=False)
    submitted_evidence_id = db.Column(db.Integer, db.ForeignKey("arb_condition_evidence_records.id", ondelete="RESTRICT", use_alter=True, name="fk_arb_condition_event_evidence"))
    waiver_scope_json = db.Column(db.JSON(none_as_null=True))
    projection_status = db.Column(db.String(40), nullable=False)
    projection_revision = db.Column(db.Integer, nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    command_receipt_id = db.Column(db.Integer, db.ForeignKey("command_idempotency_records.id", ondelete="RESTRICT"), nullable=False, unique=True)
    command_generation = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("condition_id", "condition_revision", name="uq_arb_condition_event_revision"),
        db.CheckConstraint("condition_revision > 1 AND projection_revision > 0 AND command_generation > 0", name="ck_arb_condition_event_revisions"),
        db.CheckConstraint("(event_type='submit_evidence' AND from_state='pending' AND to_state='evidence_submitted' AND submitted_evidence_id IS NOT NULL AND waiver_scope_json IS NULL) OR (event_type='verify' AND from_state='evidence_submitted' AND to_state='fulfilled' AND submitted_evidence_id IS NOT NULL AND waiver_scope_json IS NULL) OR (event_type='waive' AND from_state IN ('pending','evidence_submitted') AND to_state='waived' AND waiver_scope_json IS NOT NULL) OR (event_type='waiver_expired' AND from_state='waived' AND to_state IN ('pending','evidence_submitted') AND waiver_scope_json IS NOT NULL)", name="ck_arb_condition_event_transition"),
    )


def _condition_event_source_sql(schema):
    return f"""
CREATE OR REPLACE FUNCTION {schema}.archie_validate_arb_condition_event_source() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,{schema} AS $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM arb_canonical_conditions condition JOIN arb_decision_events decision ON decision.id=condition.decision_event_id
 WHERE condition.id=NEW.condition_id AND condition.organization_id=NEW.organization_id
 AND condition.status=NEW.from_state AND condition.revision + 1 = NEW.condition_revision
 AND condition.review_cycle_id=NEW.review_cycle_id AND condition.review_item_id=NEW.review_item_id
 AND decision.id=NEW.decision_event_id AND decision.subject_type=NEW.subject_type AND decision.subject_id=NEW.subject_id)
 THEN RAISE EXCEPTION 'ARB condition event source state is stale or outside membership' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
"""


def _condition_event_final_sql(schema):
    return f"""
CREATE OR REPLACE FUNCTION {schema}.archie_validate_arb_condition_event_final() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,{schema} AS $$ DECLARE expected_operation text; BEGIN
 expected_operation := CASE NEW.event_type
  WHEN 'submit_evidence' THEN 'arb.condition.evidence.submit'
  WHEN 'verify' THEN 'arb.condition.evidence.verify'
  WHEN 'waive' THEN 'arb.condition.waive'
  WHEN 'waiver_expired' THEN 'arb.condition.waiver.expire'
 END;
 IF NOT EXISTS (SELECT 1 FROM arb_canonical_conditions condition
 JOIN arb_decision_events decision ON decision.id=condition.decision_event_id
 JOIN arb_review_cycles cycle ON cycle.id=condition.review_cycle_id
 JOIN arb_review_items review ON review.id=condition.review_item_id
 LEFT JOIN arb_condition_evidence_records evidence ON evidence.id=NEW.submitted_evidence_id
 LEFT JOIN arb_condition_events submission_event
 ON submission_event.condition_id=NEW.condition_id
 AND submission_event.event_type='submit_evidence'
 AND submission_event.submitted_evidence_id=evidence.id
 AND submission_event.organization_id=NEW.organization_id
 AND submission_event.decision_event_id=NEW.decision_event_id
 AND submission_event.review_cycle_id=NEW.review_cycle_id
 AND submission_event.review_item_id=NEW.review_item_id
 LEFT JOIN arb_condition_events waiver_event
 ON waiver_event.condition_id=NEW.condition_id
 AND waiver_event.event_type='waive'
 AND waiver_event.organization_id=NEW.organization_id
 AND waiver_event.decision_event_id=NEW.decision_event_id
 AND waiver_event.review_cycle_id=NEW.review_cycle_id
 AND waiver_event.review_item_id=NEW.review_item_id
 AND waiver_event.waiver_scope_json::jsonb IS NOT DISTINCT FROM NEW.waiver_scope_json::jsonb
 AND waiver_event.condition_revision<NEW.condition_revision
 WHERE condition.id=NEW.condition_id
 AND condition.organization_id=NEW.organization_id AND condition.status=NEW.to_state
 AND condition.revision=NEW.condition_revision AND condition.decision_event_id=NEW.decision_event_id
 AND condition.review_cycle_id=NEW.review_cycle_id AND condition.review_item_id=NEW.review_item_id
 AND decision.organization_id=NEW.organization_id AND decision.subject_type=NEW.subject_type
 AND decision.subject_id=NEW.subject_id AND cycle.organization_id=NEW.organization_id
 AND review.organization_id=NEW.organization_id AND cycle.status=NEW.projection_status
 AND review.status=NEW.projection_status
 AND cycle.condition_projection_revision=NEW.projection_revision
 AND review.condition_projection_revision=NEW.projection_revision
 AND ((NEW.event_type='submit_evidence' AND evidence.condition_id=NEW.condition_id
 AND evidence.decision_event_id=NEW.decision_event_id AND evidence.review_cycle_id=NEW.review_cycle_id
 AND evidence.review_item_id=NEW.review_item_id AND evidence.organization_id=NEW.organization_id
 AND evidence.condition_revision=NEW.condition_revision - 1
 AND condition.submitted_evidence_id=evidence.id
 AND condition.evidence_submitted_by_id=NEW.actor_id)
 OR (NEW.event_type='verify' AND condition.verified_by_id=NEW.actor_id
 AND evidence.id=NEW.submitted_evidence_id
 AND evidence.organization_id=NEW.organization_id
 AND evidence.condition_id=NEW.condition_id
 AND evidence.decision_event_id=NEW.decision_event_id
 AND evidence.review_cycle_id=NEW.review_cycle_id
 AND evidence.review_item_id=NEW.review_item_id
 AND submission_event.condition_revision=evidence.condition_revision + 1
 AND submission_event.condition_revision<NEW.condition_revision
 AND evidence.subject_type=decision.subject_type
 AND evidence.subject_id=decision.subject_id
 AND evidence.decision_brief_id IS NOT DISTINCT FROM decision.decision_brief_id
 AND evidence.solution_id IS NOT DISTINCT FROM decision.solution_id
 AND evidence.architecture_model_id IS NOT DISTINCT FROM decision.architecture_model_id
 AND evidence.adr_id IS NOT DISTINCT FROM decision.adr_id
 AND evidence.decision_brief_version_id IS NOT DISTINCT FROM decision.decision_brief_version_id
 AND evidence.solution_evidence_snapshot_id IS NOT DISTINCT FROM decision.solution_evidence_snapshot_id
 AND evidence.subject_evidence_snapshot_id IS NOT DISTINCT FROM decision.subject_evidence_snapshot_id
 AND condition.submitted_evidence_id=evidence.id
 AND condition.fulfilment_evidence_id=evidence.id
 AND condition.submitted_evidence_id=NEW.submitted_evidence_id
 AND condition.fulfilment_evidence_id=NEW.submitted_evidence_id
 AND condition.evidence_submitted_by_id IS NOT NULL
 AND condition.evidence_submitted_by_id<>NEW.actor_id
 AND review.submitter_id<>NEW.actor_id)
 OR (NEW.event_type='waive' AND condition.waived_by_id=NEW.actor_id
 AND condition.waiver_scope_json::jsonb IS NOT DISTINCT FROM NEW.waiver_scope_json::jsonb)
 OR (NEW.event_type='waiver_expired'
 AND waiver_event.waiver_scope_json::jsonb ->> 'prior_status'=NEW.to_state
 AND condition.waiver_prior_status IS NULL AND condition.waiver_scope_json IS NULL
 AND condition.waived_at IS NULL AND condition.waived_by_id IS NULL
 AND condition.waiver_reason IS NULL AND condition.waiver_expires_at IS NULL
 AND condition.compensating_control IS NULL)))
 THEN RAISE EXCEPTION 'ARB condition event final state or membership is invalid' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS (SELECT 1 FROM users actor WHERE actor.id=NEW.actor_id AND actor.organization_id=NEW.organization_id)
 THEN RAISE EXCEPTION 'ARB condition event actor is outside tenant' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS (SELECT 1 FROM command_idempotency_records receipt JOIN operation_results result ON result.id=receipt.operation_result_id AND result.receipt_id=receipt.id JOIN command_materialisations materialisation ON materialisation.receipt_id=receipt.id
 WHERE receipt.id=NEW.command_receipt_id AND receipt.organization_id=NEW.organization_id AND receipt.actor_id=NEW.actor_id
 AND receipt.operation=expected_operation AND receipt.natural_key='arb-condition:' || NEW.organization_id::text || ':' || NEW.condition_id::text || ':' || NEW.event_type || ':' || NEW.condition_revision::text
 AND receipt.status='succeeded' AND receipt.completed_at IS NOT NULL AND receipt.lease_generation=NEW.command_generation
 AND result.organization_id=NEW.organization_id AND result.actor_id=NEW.actor_id AND result.operation=receipt.operation AND result.natural_key=receipt.natural_key AND result.request_digest=receipt.request_digest AND result.receipt_generation=NEW.command_generation
 AND materialisation.organization_id=NEW.organization_id AND materialisation.actor_id=NEW.actor_id AND materialisation.operation=receipt.operation AND materialisation.natural_key=receipt.natural_key AND materialisation.request_digest=receipt.request_digest AND materialisation.receipt_generation=NEW.command_generation
 AND result.object_ids::jsonb @> jsonb_build_object('condition_id',NEW.condition_id,'condition_event_id',NEW.id,'condition_revision',NEW.condition_revision)
 AND materialisation.object_ids::jsonb @> jsonb_build_object('condition_id',NEW.condition_id,'condition_event_id',NEW.id,'condition_revision',NEW.condition_revision))
 THEN RAISE EXCEPTION 'ARB condition event command provenance is invalid' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
"""


def ensure_arb_condition_event_guards(connection):
    if connection.dialect.name != "postgresql":
        return
    schema = connection.exec_driver_sql("SELECT current_schema()").scalar()
    q = connection.dialect.identifier_preparer.quote(schema)
    connection.exec_driver_sql(_condition_event_source_sql(q))
    connection.exec_driver_sql(_condition_event_final_sql(q))
    from app.models.transformation_db_guards import (
        _ORG_DELETION_ADMITS_SQL,
        _render_guard_sql,
    )

    connection.exec_driver_sql(
        _render_guard_sql(connection, _ORG_DELETION_ADMITS_SQL, q)
    )
    connection.exec_driver_sql(f"""CREATE OR REPLACE FUNCTION {q}.archie_reject_arb_condition_event_mutation() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF TG_OP='DELETE' THEN
   IF {q}.archie_org_deletion_admits(OLD.organization_id) THEN RETURN OLD; END IF;
 END IF;
 RAISE EXCEPTION 'ARB condition events are append-only' USING ERRCODE='55000'; END $$;
DROP TRIGGER IF EXISTS trg_arb_condition_event_source ON {q}.arb_condition_events; CREATE TRIGGER trg_arb_condition_event_source BEFORE INSERT ON {q}.arb_condition_events FOR EACH ROW EXECUTE FUNCTION {q}.archie_validate_arb_condition_event_source();
DROP TRIGGER IF EXISTS trg_arb_condition_event_final ON {q}.arb_condition_events; CREATE CONSTRAINT TRIGGER trg_arb_condition_event_final AFTER INSERT OR UPDATE ON {q}.arb_condition_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.archie_validate_arb_condition_event_final();
DROP TRIGGER IF EXISTS trg_arb_condition_event_immutable ON {q}.arb_condition_events; CREATE TRIGGER trg_arb_condition_event_immutable BEFORE UPDATE OR DELETE ON {q}.arb_condition_events FOR EACH ROW EXECUTE FUNCTION {q}.archie_reject_arb_condition_event_mutation();""")


@event.listens_for(ARBConditionEvent.__table__, "after_create")
def _install(_target, connection, **_kwargs):
    ensure_arb_condition_event_guards(connection)


def _reject(_mapper, _connection, _target):
    raise ValueError("ARB condition events are append-only")


event.listen(ARBConditionEvent, "before_update", _reject)
event.listen(ARBConditionEvent, "before_delete", _reject)

__all__ = ["ARBConditionEvent", "ensure_arb_condition_event_guards"]
