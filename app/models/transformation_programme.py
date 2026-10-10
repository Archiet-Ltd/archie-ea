"""Canonical Transformation Programme aggregate children.

``StrategicInitiative`` remains the aggregate root.  These rows carry their
own tenant key deliberately: a foreign-key identifier is not proof that a
supplied parent belongs to the active organization.
"""

from __future__ import annotations

from babel.numbers import list_currencies
from sqlalchemy.orm import validates

from app import db
from app.models.mixins import OptimisticLockMixin, TenantMixin


WORKSTREAM_TYPES = (
    "application_rationalisation",
    "process",
    "organisation_skills",
    "policy_control",
    "data",
    "supplier",
    "technology",
    "other",
)

WORKSTREAM_LIFECYCLES = (
    "objective",
    "discover",
    "evidence",
    "options",
    "decision_ready",
    "in_governance",
    "approved",
    "approved_with_conditions",
    "rejected",
    "execute",
    "outcomes",
    "completed",
)

PROGRAMME_ROLES = (
    "programme_owner",
    "workstream_lead",
    "contributor",
    "evidence_owner",
    "decision_authority",
    "delivery_lead",
    "outcome_owner",
)

IMPROVEMENT_DIRECTIONS = ("increase", "decrease", "maintain")
OUTCOME_LIFECYCLES = ("committed", "monitoring", "realised", "not_realised", "cancelled")
MEASURE_AGGREGATIONS = ("sum", "average", "minimum", "maximum", "latest", "count")

# Babel ships CLDR's ISO-4217 registry with the application. A three-letter
# shape alone would accept invented currencies such as ``ZZZ``.
ISO_4217_CURRENCIES = tuple(sorted(list_currencies()))


def _sql_values(values):
    """Render a closed tuple of trusted module constants for CHECK clauses."""
    return ", ".join(f"'{value}'" for value in values)


class ProgrammeWorkstream(TenantMixin, OptimisticLockMixin, db.Model):
    __tablename__ = "programme_workstreams"

    id = db.Column(db.Integer, primary_key=True)
    programme_id = db.Column(
        db.Integer,
        db.ForeignKey("strategic_initiatives.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workstream_type = db.Column(db.String(40), nullable=False)
    objective = db.Column(db.Text, nullable=False)
    scope_expression = db.Column(db.JSON, nullable=False, default=dict)
    lifecycle_stage = db.Column(db.String(40), nullable=False, default="objective")
    lead_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    target_date = db.Column(db.Date)
    target_date_unavailable_reason = db.Column(db.Text)
    archived_at = db.Column(db.DateTime)
    revision = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    # R1-05 (US-11). A short display name, distinct from `objective` (free
    # text); nullable so old rows are tolerated. `template_key` names the
    # sdd.md 4.2 workstream key this row was instantiated from, or NULL for
    # a manually created workstream. `archimate_element_id` is set only by
    # the ArchiMate backbone sync, never from request input.
    name = db.Column(db.String(255), nullable=True)
    template_key = db.Column(db.String(80), nullable=True)
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id", ondelete="SET NULL"), nullable=True
    )

    programme = db.relationship(
        "StrategicInitiative",
        foreign_keys=[programme_id],
        backref=db.backref("programme_workstreams", lazy="dynamic", passive_deletes=True),
    )
    lead = db.relationship("User", foreign_keys=[lead_id])

    __table_args__ = (
        db.CheckConstraint(
            f"workstream_type IN ({_sql_values(WORKSTREAM_TYPES)})",
            name="ck_programme_workstream_type",
        ),
        db.CheckConstraint(
            f"lifecycle_stage IN ({_sql_values(WORKSTREAM_LIFECYCLES)})",
            name="ck_programme_workstream_lifecycle",
        ),
        db.CheckConstraint("revision > 0", name="ck_programme_workstream_revision"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "programme_id": self.programme_id,
            "workstream_type": self.workstream_type,
            "objective": self.objective,
            "scope_expression": self.scope_expression,
            "lifecycle_stage": self.lifecycle_stage,
            "lead_id": self.lead_id,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "target_date_unavailable_reason": self.target_date_unavailable_reason,
            "archived_at": self.archived_at.isoformat() if self.archived_at else None,
            "revision": self.revision,
        }


class ProgrammeRoleAssignment(TenantMixin, OptimisticLockMixin, db.Model):
    __tablename__ = "programme_role_assignments"

    id = db.Column(db.Integer, primary_key=True)
    programme_id = db.Column(
        db.Integer,
        db.ForeignKey("strategic_initiatives.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workstream_id = db.Column(
        db.Integer,
        db.ForeignKey("programme_workstreams.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    role = db.Column(db.String(40), nullable=False)
    effective_from = db.Column(db.Date, nullable=False)
    effective_to = db.Column(db.Date)
    assigned_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    programme = db.relationship("StrategicInitiative", foreign_keys=[programme_id])
    workstream = db.relationship("ProgrammeWorkstream", foreign_keys=[workstream_id])
    user = db.relationship("User", foreign_keys=[user_id])
    assigned_by = db.relationship("User", foreign_keys=[assigned_by_id])

    __table_args__ = (
        db.CheckConstraint(
            f"role IN ({_sql_values(PROGRAMME_ROLES)})", name="ck_programme_role"
        ),
        db.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_programme_role_effective_dates",
        ),
        db.Index(
            "uq_programme_role_active_assignment",
            "organization_id",
            "programme_id",
            db.func.coalesce(workstream_id, 0),
            "user_id",
            "role",
            unique=True,
            postgresql_where=effective_to.is_(None),
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "programme_id": self.programme_id,
            "workstream_id": self.workstream_id,
            "user_id": self.user_id,
            "role": self.role,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "assigned_by_id": self.assigned_by_id,
        }


class ProgrammeOutcomeCommitment(TenantMixin, OptimisticLockMixin, db.Model):
    __tablename__ = "programme_outcome_commitments"

    id = db.Column(db.Integer, primary_key=True)
    programme_id = db.Column(
        db.Integer,
        db.ForeignKey("strategic_initiatives.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workstream_id = db.Column(
        db.Integer,
        db.ForeignKey("programme_workstreams.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    statement = db.Column(db.Text, nullable=False)
    owner_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    improvement_direction = db.Column(db.String(20), nullable=False)
    target_date = db.Column(db.Date)
    lifecycle = db.Column(db.String(30), nullable=False, default="committed")

    programme = db.relationship("StrategicInitiative", foreign_keys=[programme_id])
    workstream = db.relationship("ProgrammeWorkstream", foreign_keys=[workstream_id])
    owner = db.relationship("User", foreign_keys=[owner_id])

    __table_args__ = (
        db.CheckConstraint(
            f"improvement_direction IN ({_sql_values(IMPROVEMENT_DIRECTIONS)})",
            name="ck_programme_outcome_direction",
        ),
        db.CheckConstraint(
            f"lifecycle IN ({_sql_values(OUTCOME_LIFECYCLES)})",
            name="ck_programme_outcome_lifecycle",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "programme_id": self.programme_id,
            "workstream_id": self.workstream_id,
            "statement": self.statement,
            "owner_id": self.owner_id,
            "improvement_direction": self.improvement_direction,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "lifecycle": self.lifecycle,
        }


class MeasureDefinition(TenantMixin, OptimisticLockMixin, db.Model):
    __tablename__ = "measure_definitions"

    id = db.Column(db.Integer, primary_key=True)
    outcome_commitment_id = db.Column(
        db.Integer,
        db.ForeignKey("programme_outcome_commitments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    metric_name = db.Column(db.String(255), nullable=False)
    unit = db.Column(db.String(64), nullable=False)
    currency = db.Column(db.String(3))
    aggregation = db.Column(db.String(30), nullable=False)
    baseline_amount = db.Column(db.Numeric(18, 2))
    target_amount = db.Column(db.Numeric(18, 2))
    tolerance_amount = db.Column(db.Numeric(18, 2))
    baseline_value = db.Column(db.Numeric(24, 6))
    target_value = db.Column(db.Numeric(24, 6))
    baseline_date = db.Column(db.Date)
    target_date = db.Column(db.Date)
    cadence = db.Column(db.String(30))
    source_adapter = db.Column(db.String(80))
    source_key = db.Column(db.String(512))
    tolerance = db.Column(db.Numeric(24, 6))
    unavailable_reason = db.Column(db.Text)

    outcome_commitment = db.relationship(
        "ProgrammeOutcomeCommitment",
        foreign_keys=[outcome_commitment_id],
        backref=db.backref("measure_definitions", lazy="dynamic", passive_deletes=True),
    )

    __table_args__ = (
        db.CheckConstraint(
            f"aggregation IN ({_sql_values(MEASURE_AGGREGATIONS)})",
            name="ck_measure_definition_aggregation",
        ),
        db.CheckConstraint(
            f"currency IS NULL OR currency IN ({_sql_values(ISO_4217_CURRENCIES)})",
            name="ck_measure_definition_currency",
        ),
        db.CheckConstraint(
            "(currency IS NULL AND baseline_amount IS NULL "
            "AND target_amount IS NULL AND tolerance_amount IS NULL) OR "
            "(currency IS NOT NULL AND baseline_value IS NULL "
            "AND target_value IS NULL AND tolerance IS NULL)",
            name="ck_measure_definition_value_storage",
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

    @staticmethod
    def _numeric(value):
        return str(value) if value is not None else None

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "outcome_commitment_id": self.outcome_commitment_id,
            "metric_name": self.metric_name,
            "unit": self.unit,
            "currency": self.currency,
            "aggregation": self.aggregation,
            "baseline_value": self._numeric(
                (self.baseline_amount if self.baseline_amount is not None else self.baseline_value)
                if self.currency
                else self.baseline_value
            ),
            "target_value": self._numeric(
                (self.target_amount if self.target_amount is not None else self.target_value)
                if self.currency
                else self.target_value
            ),
            "baseline_date": self.baseline_date.isoformat() if self.baseline_date else None,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "cadence": self.cadence,
            "source_adapter": self.source_adapter,
            "source_key": self.source_key,
            "tolerance": self._numeric(
                (self.tolerance_amount if self.tolerance_amount is not None else self.tolerance)
                if self.currency
                else self.tolerance
            ),
            "unavailable_reason": self.unavailable_reason,
        }


__all__ = [
    "MeasureDefinition",
    "ProgrammeOutcomeCommitment",
    "ProgrammeRoleAssignment",
    "ProgrammeWorkstream",
]
