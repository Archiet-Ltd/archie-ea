"""Demand intake and Assumption.

Two gaps at opposite ends of the delivery chain.

**Demand** is the front door. Nothing in the repository represented a request
before it became an initiative, so work appeared already funded and already
architected — there was no funnel, no triage, no record of what was declined and
why. That record is what stops the same rejected idea returning every quarter,
and it is the only place the "we were never asked" argument can be settled.

**Assumption** completes RAID. Risk, Issue and Dependency all had models;
Assumption did not. It is the one people skip and the one that sinks delivery,
because an assumption is a risk nobody has agreed to own yet: when it proves
false it converts into an issue, usually late. Modelling the conversion is the
point — `invalidated_*` records what actually happened rather than deleting it.
"""
from datetime import datetime

from app import db
from app.models.mixins import TenantMixin

DEMAND_STATUSES = (
    "submitted",
    "triage",
    "assessing",       # architecture / cost / risk assessment
    "approved",        # converted into an initiative
    "declined",
    "deferred",
    "withdrawn",
)

# What the person triaging decides to do about a demand. The platform only
# recommends; this is the recorded decision.
DEMAND_ROUTES = ("reuse", "buy", "build")

DEMAND_SOURCES = (
    "business_unit",
    "it",
    "regulatory",
    "vendor",
    "incident",        # arose from an operational failure
    "strategy",        # flows down from a strategic goal
)

ASSUMPTION_STATUSES = (
    "open",         # still assumed, not yet proven
    "validated",    # proven true
    "invalidated",  # proven false — should have converted to an issue
    "expired",      # never tested before it stopped mattering
)


class Demand(TenantMixin, db.Model):
    """A request for change, before it is anything else."""

    __tablename__ = "demands"

    id = db.Column(db.Integer, primary_key=True)

    reference = db.Column(db.String(40), index=True)   # DEM-2026-014
    title = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)
    business_justification = db.Column(db.Text)

    status = db.Column(db.String(30), default="submitted", index=True)
    source = db.Column(db.String(30), index=True)

    # Triage scoring. Deliberately plain numbers a human sets — an auto-computed
    # priority nobody can explain is not a decision, it is a number.
    business_value_score = db.Column(db.Integer)   # 1-5
    urgency_score = db.Column(db.Integer)          # 1-5
    effort_estimate_days = db.Column(db.Integer)
    estimated_cost = db.Column(db.Numeric(18, 2))

    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    requested_for_business_unit = db.Column(db.String(200))
    submitted_date = db.Column(db.Date, default=lambda: datetime.utcnow().date())
    needed_by_date = db.Column(db.Date)

    # Triage outcome. decision_rationale is required in practice for a decline —
    # a declined demand with no reason is the one that comes back next quarter.
    triaged_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    decision_date = db.Column(db.Date)
    decision_rationale = db.Column(db.Text)
    triage_route = db.Column(db.String(20))     # reuse | buy | build, set by the person triaging
    triage_route_reasons = db.Column(db.Text)   # the coverage the recommendation was read from

    # What it became, and what it touches.
    initiative_id = db.Column(
        db.Integer, db.ForeignKey("enterprise_initiatives.id", ondelete="SET NULL"), index=True
    )
    capability_id = db.Column(
        db.Integer, db.ForeignKey("unified_capabilities.id", ondelete="SET NULL"), index=True
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    initiative = db.relationship(
        "EnterpriseInitiative", foreign_keys=[initiative_id], backref="demands"
    )
    requested_by = db.relationship("User", foreign_keys=[requested_by_id])
    triaged_by = db.relationship("User", foreign_keys=[triaged_by_id])
    capability = db.relationship("UnifiedCapability", foreign_keys=[capability_id])

    @property
    def priority_score(self):
        """Value x urgency. None — not 0 — when either input is missing."""
        if self.business_value_score is None or self.urgency_score is None:
            return None
        return self.business_value_score * self.urgency_score

    @property
    def is_decided(self) -> bool:
        return self.status in ("approved", "declined", "deferred", "withdrawn")

    @property
    def days_awaiting_decision(self):
        """Age of an undecided demand — the number that exposes a stalled funnel."""
        if self.is_decided or not self.submitted_date:
            return None
        return (datetime.utcnow().date() - self.submitted_date).days

    def to_dict(self):
        return {
            "id": self.id,
            "reference": self.reference,
            "title": self.title,
            "status": self.status,
            "source": self.source,
            "priority_score": self.priority_score,
            "estimated_cost": float(self.estimated_cost) if self.estimated_cost is not None else None,
            "needed_by_date": self.needed_by_date.isoformat() if self.needed_by_date else None,
            "days_awaiting_decision": self.days_awaiting_decision,
            "initiative_id": self.initiative_id,
            "capability_id": self.capability_id,
        }

    def __repr__(self):
        return f"<Demand {self.reference or self.id} [{self.status}]>"


class Assumption(TenantMixin, db.Model):
    """The A in RAID. An assumption is a risk nobody has agreed to own yet."""

    __tablename__ = "assumptions"

    id = db.Column(db.Integer, primary_key=True)

    statement = db.Column(db.Text, nullable=False)
    rationale = db.Column(db.Text)
    status = db.Column(db.String(20), default="open", index=True)

    # If this proves false, how much does it hurt? Same 1-5 scale as Risk.impact
    # so the two can be compared when an assumption converts.
    impact_if_false = db.Column(db.Integer)
    confidence = db.Column(db.Integer)   # 1-5, how sure are we

    owner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    validation_method = db.Column(db.Text)
    validate_by_date = db.Column(db.Date, index=True)
    validated_date = db.Column(db.Date)

    # An invalidated assumption should become an issue. Recording the link makes
    # that traceable instead of the assumption quietly disappearing.
    invalidated_date = db.Column(db.Date)
    invalidated_note = db.Column(db.Text)
    converted_to_risk_id = db.Column(
        db.Integer, db.ForeignKey("risks.id", ondelete="SET NULL"), index=True
    )

    initiative_id = db.Column(
        db.Integer, db.ForeignKey("enterprise_initiatives.id", ondelete="CASCADE"), index=True
    )
    work_package_id = db.Column(
        db.Integer, db.ForeignKey("work_packages.id", ondelete="SET NULL"), index=True
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    owner = db.relationship("User", foreign_keys=[owner_id])
    initiative = db.relationship(
        "EnterpriseInitiative", foreign_keys=[initiative_id], backref="assumptions"
    )
    work_package = db.relationship("WorkPackage", foreign_keys=[work_package_id], backref="assumptions")

    @property
    def exposure(self):
        """impact x (6 - confidence): high impact and low confidence ranks highest."""
        if self.impact_if_false is None or self.confidence is None:
            return None
        return self.impact_if_false * (6 - self.confidence)

    @property
    def is_overdue_for_validation(self) -> bool:
        if self.status != "open" or not self.validate_by_date:
            return False
        return self.validate_by_date < datetime.utcnow().date()

    def to_dict(self):
        return {
            "id": self.id,
            "statement": self.statement,
            "status": self.status,
            "impact_if_false": self.impact_if_false,
            "confidence": self.confidence,
            "exposure": self.exposure,
            "validate_by_date": self.validate_by_date.isoformat() if self.validate_by_date else None,
            "is_overdue_for_validation": self.is_overdue_for_validation,
            "owner": self.owner.email if self.owner else None,
            "initiative_id": self.initiative_id,
        }

    def __repr__(self):
        return f"<Assumption {self.id} [{self.status}]>"
