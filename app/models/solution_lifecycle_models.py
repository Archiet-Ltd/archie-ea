"""Solution lifecycle models — risk register, TCO, metrics, plateaus.

These models extend the Solution record with TOGAF Phase E-H data
that isn't captured by the Architecture Assistant wizard.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey,
    Integer, Numeric, String, Text,
)
from sqlalchemy.orm import relationship

from app import db
from app.models.mixins import TenantMixin


class SolutionRisk(TenantMixin, db.Model):
    """Risk register entry for a solution (TOGAF Phase C-D).

    Superseded store: ``risks`` (app/models/risk.py) plus its link table
    ``risk_entity_links`` is now the one writer for every risk, per
    app/services/risk_service.py. ``flask backfill-solution-risk-merge``
    (app/commands/backfill_solution_risk_merge.py) copies each row still
    unmerged into a canonical Risk (linked back to this solution via a
    RiskEntityLink of entity_type="solution") and sets
    ``retired_into_risk_id`` here so re-running the backfill is a no-op. This
    table's own create/update/delete/CSV-import routes
    (app/modules/solutions_strategic/v2/routes/solution_phase_routes.py) now
    write through risk_service.py too -- no code path adds a new row here.
    The GET list on that same blueprint still reads this table directly;
    repointing that reader to the canonical store, like the risk register and
    programme screens, is PR 2 scope.
    """
    __tablename__ = "solution_risks"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    solution_id = Column(Integer, ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    risk_name = Column(String(200), nullable=True)
    # The ArchiMate mirror of this row. Added 31 Aug 2026: CLAUDE.md requires
    # every motivation entity to have a matching ArchiMateElement, but this
    # model had nowhere to record one, so its creation paths could never
    # comply. Nullable and SET NULL so reconcile-schema can add it to
    # existing databases; populated automatically by
    # app/services/archimate_backbone.py.
    archimate_element_id = Column(
        Integer, ForeignKey("archimate_elements.id", ondelete="SET NULL"),
        index=True, nullable=True,
    )
    risk_description = Column(Text, nullable=False)
    impact = Column(String(20), nullable=False, default="medium")
    probability = Column(String(20), nullable=False, default="medium")
    mitigation = Column(Text)
    status = Column(String(20), default="open")
    owner = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    constraint_id = Column(Integer, ForeignKey("solution_constraints.id", ondelete="SET NULL"), nullable=True, index=True)  # migration-exempt
    # NULL until flask backfill-solution-risk-merge copies this row into the
    # canonical risks table; set once, never cleared. See the class docstring.
    retired_into_risk_id = Column(
        Integer, ForeignKey("risks.id", ondelete="SET NULL"), nullable=True, index=True
    )

    solution = relationship("Solution", backref="risks")
    created_by = relationship("User", foreign_keys=[created_by_id])
    constraint = relationship("SolutionConstraint", foreign_keys=[constraint_id])

    def to_dict(self):
        return {
            "id": self.id,
            "risk_name": self.risk_name,
            "risk_description": self.risk_description,
            "impact": self.impact,
            "probability": self.probability,
            "mitigation": self.mitigation,
            "status": self.status,
            "owner": self.owner,
            "retired_into_risk_id": self.retired_into_risk_id,
        }


class SolutionTCOItem(db.Model):
    """Total Cost of Ownership line item (TOGAF Phase E)."""
    __tablename__ = "solution_tco_items"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    solution_id = Column(Integer, ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    option_label = Column(String(100))
    cost_category = Column(String(100), nullable=False)
    is_recurring = Column(Boolean, default=True)
    year = Column(Integer, default=1)
    amount = Column(Numeric(15, 2), default=0)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    solution = relationship("Solution", backref="tco_items")

    def to_dict(self):
        return {
            "id": self.id,
            "option_label": self.option_label,
            "cost_category": self.cost_category,
            "is_recurring": self.is_recurring,
            "year": self.year,
            "amount": float(self.amount) if self.amount else 0,
            "notes": self.notes,
        }


class SolutionMetric(TenantMixin, db.Model):
    """Success metric with baseline/target/actual tracking (TOGAF Phase H)."""
    __tablename__ = "solution_metrics"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    solution_id = Column(Integer, ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    goal_id = Column(Integer, ForeignKey("solution_goals.id", ondelete="SET NULL"), nullable=True, index=True)  # migration-exempt
    name = Column(String(255), nullable=False)
    unit = Column(String(50))
    baseline_value = Column(String(100))
    target_value = Column(String(100))
    actual_value = Column(String(100))
    measurement_date = Column(Date, nullable=True)
    status = Column(String(20), default="not_measured")
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    solution = relationship("Solution", backref="metrics")
    goal = relationship("SolutionGoal", foreign_keys=[goal_id])

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "unit": self.unit,
            "baseline_value": self.baseline_value,
            "target_value": self.target_value,
            "actual_value": self.actual_value,
            "measurement_date": self.measurement_date.isoformat() if self.measurement_date else None,
            "status": self.status,
        }


class SolutionOption(TenantMixin, db.Model):
    """Solution option for trade-off analysis (wizard Step 4)."""
    __tablename__ = "solution_options"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    solution_id = Column(Integer, ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    strategic_fit = Column(Numeric(5, 2), nullable=True)
    risk_score = Column(Numeric(5, 2), nullable=True)
    coverage = Column(Numeric(5, 2), nullable=True)
    pros = Column(db.JSON, nullable=True)
    cons = Column(db.JSON, nullable=True)
    ai_generated = Column(Boolean, default=False, nullable=False)
    is_selected = Column(Boolean, default=False, nullable=False)
    rank = Column(Integer, nullable=True)
    approval_status = Column(String(50), default="approved", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    solution = relationship("Solution", backref="options")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "strategic_fit": float(self.strategic_fit) if self.strategic_fit else None,
            "risk_score": float(self.risk_score) if self.risk_score else None,
            "coverage": float(self.coverage) if self.coverage else None,
            "pros": self.pros or [],
            "cons": self.cons or [],
            "ai_generated": self.ai_generated,
            "is_selected": self.is_selected,
            "rank": self.rank,
            "approval_status": self.approval_status,
        }


class SolutionARBDraft(TenantMixin, db.Model):
    """ARB submission draft (wizard Step 6)."""
    __tablename__ = "solution_arb_drafts"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    solution_id = Column(Integer, ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, default=1, nullable=False)
    business_justification = Column(Text, nullable=True)
    technical_assessment = Column(Text, nullable=True)
    risk_analysis = Column(Text, nullable=True)
    cost_summary = Column(Text, nullable=True)
    submitted = Column(Boolean, default=False, nullable=False)
    submitted_at = Column(DateTime, nullable=True)
    submitted_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    ai_generated = Column(Boolean, default=False, nullable=False)
    approval_status = Column(String(50), default="approved", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    solution = relationship("Solution", backref="arb_drafts")
    submitted_by = relationship("User", foreign_keys=[submitted_by_id])

    def can_submit(self) -> bool:
        return self.approval_status not in ("pending_review",)

    def to_dict(self):
        return {
            "id": self.id,
            "version": self.version,
            "business_justification": self.business_justification,
            "technical_assessment": self.technical_assessment,
            "risk_analysis": self.risk_analysis,
            "cost_summary": self.cost_summary,
            "submitted": self.submitted,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "approval_status": self.approval_status,
        }


class SolutionPlateau(db.Model):
    """Transition architecture plateau (TOGAF Phase F)."""
    __tablename__ = "solution_plateaus"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    solution_id = Column(Integer, ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    target_date = Column(Date, nullable=True)
    order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    solution = relationship("Solution", backref="plateaus")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "order": self.order,
        }
