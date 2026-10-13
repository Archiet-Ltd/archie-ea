"""Business Case artifact (Business-Architect signature deliverable).

A NEW, self-contained module. Today, the financial/value story behind a
change is scattered across three places:
    - StrategicInitiative (app/models/strategic.py) — budget_allocated,
      budget_spent, business_value_score
    - CapabilityCostAllocation (app/models/cost_intelligence.py) — full TCO
      cost taxonomy per capability + business_value_generated
    - UnifiedCapability (app/models/unified_capability.py) — annual_cost,
      roi_percentage
    - Solution (app/models/solution_models.py) — estimated_cost,
      actual_cost, roi_percentage, payback_period_months

BusinessCase does NOT duplicate that data. It is a structured document
(problem / options / recommendation / costs / benefits / risks) that can
OPTIONALLY link to one capability, one strategic initiative, and/or one
solution, and the module's aggregate_financials() service helper pulls
cost/value figures from those links to pre-populate or cross-check the
case's own capex/opex/tco/roi fields — see
app/modules/business_case/service.py.

Convention notes (mirrors app/models/business_model.py / BMC-001):
    - TenantMixin supplies organization_id (multi-tenant FK); the app's
      SQLAlchemy event listener in app.middleware.tenant_isolation
      auto-installs tenant filters on any model that has this column.
    - Every new column is nullable so a brand-new table (built by
      db.create_all() on first boot) never conflicts with the
      reconcile-schema drift-repair step.
    - Only a NEW table (business_cases) with NEW columns — no existing
      table is touched.
"""

from app import db
from app.datetime_helpers import utcnow
from app.models.mixins import TenantMixin

# Business case lifecycle states.
BUSINESS_CASE_STATUSES = ("draft", "submitted", "approved", "rejected")


class BusinessCase(TenantMixin, db.Model):
    """A consolidated Business Case document.

    Assembles the standard business-case narrative (problem, options,
    recommendation, benefits, risks) alongside the financial summary
    (capex/opex/TCO/ROI/payback) that today lives scattered across
    StrategicInitiative budgets, CapabilityCostAllocation rows, and
    UnifiedCapability financial fields.
    """

    __tablename__ = "business_cases"

    id = db.Column(db.Integer, primary_key=True)

    # ── Identity ─────────────────────────────────────────────────────
    title = db.Column(db.String(256), nullable=True)
    description = db.Column(db.Text, nullable=True)

    # draft | submitted | approved | rejected
    status = db.Column(db.String(20), nullable=True, default="draft", index=True)

    # ── Optional links — aggregate_financials() pulls from whichever of
    # these are set. None/absent links are simply skipped (fault-tolerant).
    capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id"), nullable=True, index=True
    )
    strategic_initiative_id = db.Column(
        db.Integer, db.ForeignKey("strategic_initiatives.id"), nullable=True, index=True
    )
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id"), nullable=True, index=True
    )

    # ADR-CV-1: the saved diagram this business case projects onto, once one
    # has been chosen (export and share both resolve through this before
    # falling back to the tenant's most recent saved diagram of this
    # template kind — see resolve_canvas_saved_diagram_id). Nullable: most
    # business cases have no saved diagram yet.
    saved_diagram_id = db.Column(
        db.Integer, db.ForeignKey("saved_diagrams.id"), nullable=True, index=True
    )

    # ── The business-case document sections ─────────────────────────
    problem_statement = db.Column(db.Text, nullable=True)
    options_considered = db.Column(db.Text, nullable=True)
    recommended_option = db.Column(db.Text, nullable=True)
    expected_benefits = db.Column(db.Text, nullable=True)
    key_risks = db.Column(db.Text, nullable=True)

    # ── Financial summary — user-entered, or pre-populated / cross-checked
    # via aggregate_financials() against the linked entities above.
    financial_benefit_annual = db.Column(db.Numeric(15, 2), nullable=True)
    capex = db.Column(db.Numeric(15, 2), nullable=True)
    opex_annual = db.Column(db.Numeric(15, 2), nullable=True)
    tco_3yr = db.Column(db.Numeric(15, 2), nullable=True)
    roi_percentage = db.Column(db.Numeric(6, 2), nullable=True)
    payback_months = db.Column(db.Integer, nullable=True)

    # ── Audit ────────────────────────────────────────────────────────
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=True)

    # ── Relationships ────────────────────────────────────────────────
    capability = db.relationship(
        "BusinessCapability", backref=db.backref("business_cases", lazy="dynamic")
    )
    strategic_initiative = db.relationship(
        "StrategicInitiative", backref=db.backref("business_cases", lazy="dynamic")
    )
    solution = db.relationship(
        "Solution", backref=db.backref("business_cases", lazy="dynamic")
    )
    created_by = db.relationship(
        "User", backref=db.backref("business_cases_created", lazy="dynamic"),
        foreign_keys=[created_by_id],
    )

    def __repr__(self):
        return f"<BusinessCase {self.id} {self.title!r} status={self.status}>"

    @property
    def npv(self):
        """Net present value, computed live from capex/opex/financial_benefit_annual
        — never stored, since any of the three can be edited independently and a
        stored value would go stale the moment one changed. None (never a
        fabricated number) until at least capex or benefit is set. See
        app/modules/business_case/service.py::calculate_npv for the formula."""
        from app.modules.business_case.service import calculate_npv

        result = calculate_npv(self.capex, self.opex_annual, self.financial_benefit_annual)
        return float(result) if result is not None else None

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "capability_id": self.capability_id,
            "capability_name": self.capability.name if self.capability else None,
            "strategic_initiative_id": self.strategic_initiative_id,
            "strategic_initiative_name": (
                self.strategic_initiative.name if self.strategic_initiative else None
            ),
            "solution_id": self.solution_id,
            "solution_name": self.solution.name if self.solution else None,
            "saved_diagram_id": self.saved_diagram_id,
            "problem_statement": self.problem_statement,
            "options_considered": self.options_considered,
            "recommended_option": self.recommended_option,
            "expected_benefits": self.expected_benefits,
            "key_risks": self.key_risks,
            "financial_benefit_annual": (
                float(self.financial_benefit_annual)
                if self.financial_benefit_annual is not None
                else None
            ),
            "capex": float(self.capex) if self.capex is not None else None,
            "opex_annual": float(self.opex_annual) if self.opex_annual is not None else None,
            "tco_3yr": float(self.tco_3yr) if self.tco_3yr is not None else None,
            "roi_percentage": (
                float(self.roi_percentage) if self.roi_percentage is not None else None
            ),
            "payback_months": self.payback_months,
            "npv": self.npv,
            "created_by_id": self.created_by_id,
            "created_by_name": self.created_by.full_name() if self.created_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
