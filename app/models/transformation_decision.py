"""Mutable decision roots and immutable Transformation Room snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import timezone

from sqlalchemy import event
from sqlalchemy.orm import validates

from app import db
from app.models.mixins import TenantMixin
from app.models.transformation_programme import ISO_4217_CURRENCIES


OPTION_EXCEPTION_TYPES = ("policy", "legal")
BRIEF_STATUSES = ("draft", "frozen", "in_governance", "terminal")

_ARB_SUBJECT_SNAPSHOT_IMMUTABILITY_BODY = """
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF public.archie_org_deletion_admits(OLD.organization_id) THEN
            RETURN OLD;
        END IF;
    END IF;
    RAISE EXCEPTION 'ARB subject evidence snapshots are append-only'
        USING ERRCODE = '55000';
END;
""".strip()


def _sql_values(values):
    return ", ".join(f"'{value}'" for value in values)


class ARBSubjectEvidenceSnapshot(TenantMixin, db.Model):
    """Immutable typed evidence captured for model and ADR ARB subjects.

    Decision Brief cycles pin ``DecisionBriefVersion`` and Solution cycles keep
    using ``ARBSubmissionEvidenceSnapshot``.  This table exists only for the
    two governed subjects that did not already have an immutable evidence type.

    The nullable declarations are deliberate deployment compatibility: a
    long-lived database may receive these columns through add-only schema
    reconciliation.  The check constraint supplies the strict new-row shape.
    """

    __tablename__ = "arb_subject_evidence_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    subject_type = db.Column(db.String(40), nullable=True, index=True)
    subject_id = db.Column(db.Integer, nullable=True, index=True)
    architecture_model_id = db.Column(
        db.Integer,
        db.ForeignKey("architecture_models.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # E2E-H: repointed from architecture_decision_records (0 rows in
    # production, unreachable via any real UI) to architecture_decisions,
    # the model the real Decision Register writes.
    adr_id = db.Column(
        db.Integer,
        db.ForeignKey("architecture_decisions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    schema_version = db.Column(db.Integer, nullable=True)
    policy_version = db.Column(db.String(160), nullable=True)
    captured_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    captured_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        server_default=db.func.now(),
    )
    payload = db.Column(db.JSON, nullable=True)
    citations = db.Column(db.JSON, nullable=True)
    content_hash = db.Column(db.String(64), nullable=True, index=True)

    __table_args__ = (
        db.CheckConstraint(
            "subject_type IS NOT NULL AND subject_id IS NOT NULL "
            "AND schema_version IS NOT NULL AND schema_version > 0 "
            "AND policy_version IS NOT NULL AND captured_at IS NOT NULL "
            "AND payload IS NOT NULL AND citations IS NOT NULL "
            "AND content_hash IS NOT NULL AND length(content_hash) = 64 "
            "AND ((subject_type = 'architecture_model' "
            "AND subject_id = architecture_model_id "
            "AND architecture_model_id IS NOT NULL AND adr_id IS NULL) "
            "OR (subject_type = 'adr' AND subject_id = adr_id "
            "AND adr_id IS NOT NULL AND architecture_model_id IS NULL))",
            name="ck_arb_subject_evidence_snapshot_shape",
        ),
    )

    def canonical_content(self):
        """Return the exact typed dossier protected by ``content_hash``."""
        captured_at = self.captured_at
        if captured_at is not None and captured_at.tzinfo is not None:
            captured_at = captured_at.astimezone(timezone.utc)
        return {
            "schema_version": self.schema_version,
            "organization_id": self.organization_id,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "architecture_model_id": self.architecture_model_id,
            "adr_id": self.adr_id,
            "policy_version": self.policy_version,
            "captured_by_id": self.captured_by_id,
            "captured_at": captured_at.isoformat() if captured_at else None,
            "payload": self.payload,
            "citations": self.citations,
        }

    def recompute_content_hash(self):
        document = json.dumps(
            self.canonical_content(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(document.encode("utf-8")).hexdigest()


def ensure_arb_subject_snapshot_immutability(connection):
    """Install the PostgreSQL append-only trigger on fresh and existing tables."""
    if connection.dialect.name != "postgresql":
        return
    connection.exec_driver_sql(
        "SELECT pg_advisory_xact_lock(hashtext("
        "'archie_arb_subject_snapshot_immutability'))"
    )
    from app.models.transformation_db_guards import _ORG_DELETION_ADMITS_SQL

    connection.exec_driver_sql(_ORG_DELETION_ADMITS_SQL)
    connection.exec_driver_sql(
        f"""
        CREATE OR REPLACE FUNCTION archie_reject_arb_subject_snapshot_mutation()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $$
        {_ARB_SUBJECT_SNAPSHOT_IMMUTABILITY_BODY}
        $$
        """
    )
    connection.exec_driver_sql(
        """
        DO $$
        DECLARE qualified_table text;
        BEGIN
            qualified_table := format('%%I.%%I', current_schema(),
                                      'arb_subject_evidence_snapshots');
            IF to_regclass(qualified_table) IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1
                   FROM pg_trigger trigger
                   WHERE trigger.tgname =
                         'trg_reject_arb_subject_snapshot_mutation'
                     AND trigger.tgrelid = to_regclass(qualified_table)
               ) THEN
                EXECUTE format(
                    'CREATE TRIGGER trg_reject_arb_subject_snapshot_mutation '
                    'BEFORE UPDATE OR DELETE ON %%s FOR EACH ROW EXECUTE FUNCTION '
                    'archie_reject_arb_subject_snapshot_mutation()',
                    qualified_table
                );
            END IF;
            IF to_regclass(qualified_table) IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE %%s ENABLE TRIGGER '
                    'trg_reject_arb_subject_snapshot_mutation',
                    qualified_table
                );
            END IF;
        END;
        $$
        """
    )


@event.listens_for(ARBSubjectEvidenceSnapshot.__table__, "after_create")
def _install_arb_subject_snapshot_immutability(_target, connection, **_kwargs):
    ensure_arb_subject_snapshot_immutability(connection)


def _reject_arb_subject_snapshot_mutation(_mapper, _connection, _target):
    raise ValueError("ARB subject evidence snapshots are append-only")


event.listen(
    ARBSubjectEvidenceSnapshot,
    "before_update",
    _reject_arb_subject_snapshot_mutation,
)
event.listen(
    ARBSubjectEvidenceSnapshot,
    "before_delete",
    _reject_arb_subject_snapshot_mutation,
)


class TransformationOption(TenantMixin, db.Model):
    """Editable logical option; immutable versions preserve governed captures."""

    __tablename__ = "transformation_options"

    id = db.Column(db.Integer, primary_key=True)
    workstream_id = db.Column(
        db.Integer,
        db.ForeignKey("programme_workstreams.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    candidate_id = db.Column(
        db.Integer,
        db.ForeignKey("transformation_candidates.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    title = db.Column(db.String(255), nullable=False)
    action_type = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=False)
    assumptions = db.Column(db.JSON, nullable=False, default=list)
    dependencies = db.Column(db.JSON, nullable=False, default=list)
    impacts = db.Column(db.JSON, nullable=False, default=list)
    risks = db.Column(db.JSON, nullable=False, default=list)
    reversibility = db.Column(db.Text)
    transition_approach = db.Column(db.Text)
    affected_capability_ids = db.Column(db.JSON, nullable=False, default=list)
    affected_value_stream_ids = db.Column(db.JSON, nullable=False, default=list)
    recommendation_rationale = db.Column(db.Text)
    cost_min = db.Column(db.Numeric(18, 2))
    cost_max = db.Column(db.Numeric(18, 2))
    benefit_min = db.Column(db.Numeric(18, 2))
    benefit_max = db.Column(db.Numeric(18, 2))
    risk_min = db.Column(db.Numeric(12, 4))
    risk_max = db.Column(db.Numeric(12, 4))
    currency = db.Column(db.String(3))
    technology_required = db.Column(db.Boolean)
    revision = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )

    workstream = db.relationship("ProgrammeWorkstream", foreign_keys=[workstream_id])
    candidate = db.relationship("TransformationCandidate", foreign_keys=[candidate_id])

    __mapper_args__ = {"version_id_col": revision}
    __table_args__ = (
        db.CheckConstraint("revision > 0", name="ck_transformation_option_revision"),
        db.CheckConstraint(
            f"currency IS NULL OR currency IN ({_sql_values(ISO_4217_CURRENCIES)})",
            name="ck_transformation_option_currency",
        ),
    )

    @validates("currency")
    def _validate_currency(self, _key, value):
        if value is None:
            return None
        normalized = value.strip().upper() if isinstance(value, str) else value
        if normalized not in ISO_4217_CURRENCIES:
            raise ValueError("currency must be an ISO 4217 code")
        return normalized


class TransformationOptionVersion(TenantMixin, db.Model):
    """Append-only canonical capture of one option draft revision."""

    __tablename__ = "transformation_option_versions"

    id = db.Column(db.Integer, primary_key=True)
    option_id = db.Column(
        db.Integer,
        db.ForeignKey("transformation_options.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workstream_id = db.Column(
        db.Integer,
        db.ForeignKey("programme_workstreams.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    candidate_id = db.Column(
        db.Integer,
        db.ForeignKey("transformation_candidates.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    version = db.Column(db.Integer, nullable=False)
    source_revision = db.Column(db.Integer, nullable=False)
    content_json = db.Column(db.JSON, nullable=False)
    cost_min = db.Column(db.Numeric(18, 2), nullable=False)
    cost_max = db.Column(db.Numeric(18, 2), nullable=False)
    benefit_min = db.Column(db.Numeric(18, 2), nullable=False)
    benefit_max = db.Column(db.Numeric(18, 2), nullable=False)
    risk_min = db.Column(db.Numeric(12, 4), nullable=False)
    risk_max = db.Column(db.Numeric(12, 4), nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    technology_required = db.Column(db.Boolean, nullable=False)
    captured_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    captured_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )
    content_hash = db.Column(db.String(64), nullable=False)

    option = db.relationship("TransformationOption", foreign_keys=[option_id])
    captured_by = db.relationship("User", foreign_keys=[captured_by_id])

    __table_args__ = (
        db.UniqueConstraint(
            "option_id", "version", name="uq_transformation_option_version"
        ),
        db.UniqueConstraint(
            "option_id", "source_revision", name="uq_transformation_option_source_revision"
        ),
        db.CheckConstraint("version > 0", name="ck_transformation_option_version"),
        db.CheckConstraint(
            "source_revision > 0", name="ck_transformation_option_source_revision"
        ),
        db.CheckConstraint(
            "cost_min <= cost_max AND benefit_min <= benefit_max "
            "AND risk_min <= risk_max",
            name="ck_transformation_option_version_ranges",
        ),
        db.CheckConstraint(
            f"currency IN ({_sql_values(ISO_4217_CURRENCIES)})",
            name="ck_transformation_option_version_currency",
        ),
        db.CheckConstraint(
            "length(content_hash) = 64", name="ck_transformation_option_version_hash"
        ),
    )

    @property
    def immutable(self):
        return True

    def _content(self, key):
        return (self.content_json or {}).get(key)

    @property
    def assumptions(self):
        return self._content("assumptions")

    @property
    def dependencies(self):
        return self._content("dependencies")

    @property
    def impacts(self):
        return self._content("impacts")

    @property
    def risks(self):
        return self._content("risks")

    @property
    def reversibility(self):
        return self._content("reversibility")

    @property
    def transition_approach(self):
        return self._content("transition_approach")

    @property
    def affected_capability_ids(self):
        return self._content("affected_capability_ids")

    @property
    def affected_value_stream_ids(self):
        return self._content("affected_value_stream_ids")

    @property
    def recommendation_rationale(self):
        return self._content("recommendation_rationale")


class DecisionBrief(TenantMixin, db.Model):
    """Stable logical decision case and mutable governance projection."""

    __tablename__ = "decision_briefs"

    id = db.Column(db.Integer, primary_key=True)
    workstream_id = db.Column(
        db.Integer,
        db.ForeignKey("programme_workstreams.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    candidate_id = db.Column(
        db.Integer,
        db.ForeignKey("transformation_candidates.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    title = db.Column(db.String(255), nullable=False)
    recommendation_option_id = db.Column(
        db.Integer,
        db.ForeignKey("transformation_options.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    decision_authority_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    unknown_codes = db.Column(db.JSON, nullable=False, default=list)
    conflicts = db.Column(db.JSON, nullable=False, default=list)
    expected_impacts = db.Column(db.JSON, nullable=False, default=list)
    option_exception_type = db.Column(db.String(20))
    option_exception_name = db.Column(db.String(255))
    option_exception_reason = db.Column(db.Text)
    option_exception_authority_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    status = db.Column(db.String(30), nullable=False, default="draft", server_default="draft")
    revision = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )

    workstream = db.relationship("ProgrammeWorkstream", foreign_keys=[workstream_id])
    candidate = db.relationship("TransformationCandidate", foreign_keys=[candidate_id])
    recommendation_option = db.relationship(
        "TransformationOption", foreign_keys=[recommendation_option_id]
    )

    __mapper_args__ = {"version_id_col": revision}
    __table_args__ = (
        db.Index(
            "uq_decision_brief_workstream_scope",
            "organization_id",
            "workstream_id",
            unique=True,
            postgresql_where=db.text("candidate_id IS NULL"),
        ),
        db.Index(
            "uq_decision_brief_candidate_scope",
            "organization_id",
            "workstream_id",
            "candidate_id",
            unique=True,
            postgresql_where=db.text("candidate_id IS NOT NULL"),
        ),
        db.CheckConstraint("revision > 0", name="ck_decision_brief_revision"),
        db.CheckConstraint(
            f"status IN ({_sql_values(BRIEF_STATUSES)})", name="ck_decision_brief_status"
        ),
        db.CheckConstraint(
            "(option_exception_type IS NULL AND option_exception_name IS NULL "
            "AND option_exception_reason IS NULL AND option_exception_authority_id IS NULL) "
            "OR (option_exception_type IN ('policy', 'legal') "
            "AND option_exception_name IS NOT NULL AND option_exception_reason IS NOT NULL "
            "AND option_exception_authority_id IS NOT NULL)",
            name="ck_decision_brief_option_exception_complete",
        ),
    )

    @property
    def constraint_type(self):
        return self.option_exception_type

    @property
    def reason(self):
        return self.option_exception_reason

    @property
    def authority_id(self):
        return self.option_exception_authority_id


class DecisionBriefVersion(TenantMixin, db.Model):
    """Append-only frozen decision dossier submitted to later governance."""

    __tablename__ = "decision_brief_versions"

    id = db.Column(db.Integer, primary_key=True)
    brief_id = db.Column(
        db.Integer,
        db.ForeignKey("decision_briefs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workstream_id = db.Column(
        db.Integer,
        db.ForeignKey("programme_workstreams.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version = db.Column(db.Integer, nullable=False)
    source_revision = db.Column(db.Integer, nullable=False)
    frozen_payload = db.Column(db.JSON, nullable=False)
    recommendation_option_version_id = db.Column(
        db.Integer,
        db.ForeignKey("transformation_option_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    option_version_ids = db.Column(db.JSON, nullable=False)
    cited_evidence_ids = db.Column(db.JSON, nullable=False)
    outcome_ids = db.Column(db.JSON, nullable=False)
    measure_ids = db.Column(db.JSON, nullable=False)
    policy_version = db.Column(db.String(160), nullable=False)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )
    content_hash = db.Column(db.String(64), nullable=False)
    # Exact UTF-8 text produced by the Python canonical serializer. Nullable
    # for add-only reconciliation of historical databases; all new freezes
    # require and populate it atomically.
    canonical_document = db.Column(db.Text, nullable=True)
    submitted_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    submitter_authorized = db.Column(db.Boolean, nullable=False)
    decision_authority_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    human_reviewed_ai = db.Column(db.Boolean, nullable=False)
    blockers_cleared = db.Column(db.Boolean, nullable=False)
    unknowns_acknowledged = db.Column(db.Boolean, nullable=False)

    brief = db.relationship("DecisionBrief", foreign_keys=[brief_id])

    __table_args__ = (
        db.UniqueConstraint("brief_id", "version", name="uq_decision_brief_version"),
        db.UniqueConstraint(
            "brief_id", "source_revision", name="uq_decision_brief_source_revision"
        ),
        db.CheckConstraint("version > 0", name="ck_decision_brief_version"),
        db.CheckConstraint(
            "source_revision > 0", name="ck_decision_brief_source_revision"
        ),
        db.CheckConstraint(
            "length(content_hash) = 64", name="ck_decision_brief_version_hash"
        ),
    )

    @property
    def immutable(self):
        return True


class DecisionBriefOptionCitation(TenantMixin, db.Model):
    """Immutable exact option-version membership of one brief version."""

    __tablename__ = "decision_brief_option_citations"

    id = db.Column(db.Integer, primary_key=True)
    brief_version_id = db.Column(
        db.Integer,
        db.ForeignKey("decision_brief_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    option_version_id = db.Column(
        db.Integer,
        db.ForeignKey("transformation_option_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )

    __table_args__ = (
        db.UniqueConstraint(
            "brief_version_id", "option_version_id",
            name="uq_decision_brief_option_citation",
        ),
    )


class DecisionBriefEvidenceCitation(TenantMixin, db.Model):
    """Immutable exact evidence citation plus global-head state at freeze."""

    __tablename__ = "decision_brief_evidence_citations"

    id = db.Column(db.Integer, primary_key=True)
    brief_version_id = db.Column(
        db.Integer,
        db.ForeignKey("decision_brief_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    evidence_record_id = db.Column(
        db.Integer,
        db.ForeignKey("evidence_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    evidence_head_id = db.Column(
        db.Integer,
        db.ForeignKey("evidence_claim_heads.id", ondelete="RESTRICT"),
        nullable=False,
    )
    head_revision_at_freeze = db.Column(db.Integer, nullable=False)
    current_record_id_at_freeze = db.Column(
        db.Integer,
        db.ForeignKey("evidence_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    was_current = db.Column(db.Boolean, nullable=False)
    acknowledged = db.Column(db.Boolean, nullable=False)
    freshness_status = db.Column(db.String(30), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )

    __table_args__ = (
        db.UniqueConstraint(
            "brief_version_id", "evidence_record_id",
            name="uq_decision_brief_evidence_citation",
        ),
        db.CheckConstraint(
            "head_revision_at_freeze > 0",
            name="ck_decision_brief_evidence_head_revision",
        ),
        db.CheckConstraint(
            "was_current OR acknowledged",
            name="ck_decision_brief_evidence_acknowledged",
        ),
    )


class DecisionEvent(TenantMixin, db.Model):
    """Append-only workflow history owned only by a Decision Brief/ARB flow."""

    __tablename__ = "decision_events"

    id = db.Column(db.Integer, primary_key=True)
    brief_id = db.Column(
        db.Integer,
        db.ForeignKey("decision_briefs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    brief_version_id = db.Column(
        db.Integer,
        db.ForeignKey("decision_brief_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    event_type = db.Column(db.String(100), nullable=False)
    from_state = db.Column(db.String(40), nullable=False)
    to_state = db.Column(db.String(40), nullable=False)
    actor_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    rationale = db.Column(db.Text, nullable=False)
    conditions_json = db.Column(
        db.JSON, nullable=False, default=list, server_default=db.text("'[]'::json")
    )
    source_review_id = db.Column(db.Integer)
    command_receipt_id = db.Column(
        db.Integer,
        db.ForeignKey("command_idempotency_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    command_generation = db.Column(db.Integer, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )

    __table_args__ = (
        db.CheckConstraint(
            "command_generation > 0", name="ck_decision_event_command_generation"
        ),
    )


__all__ = [
    "ARBSubjectEvidenceSnapshot",
    "BRIEF_STATUSES",
    "DecisionBrief",
    "DecisionBriefEvidenceCitation",
    "DecisionBriefOptionCitation",
    "DecisionBriefVersion",
    "DecisionEvent",
    "OPTION_EXCEPTION_TYPES",
    "TransformationOption",
    "TransformationOptionVersion",
    "ensure_arb_subject_snapshot_immutability",
]
