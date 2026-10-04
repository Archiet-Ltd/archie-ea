"""
ArchiMate 3.2 Motivation Layer - Complete Semantic Traceability

Implements missing Driver and Goal models to complete Strategy-to-Implementation chain:
Driver → Goal → Outcome → Requirement → Capability → Process → Application → Technology

Key EA Intelligence Fix #1:
- Driver: External forces requiring organizational response
- Goal: Measurable strategic objectives
- Complete semantic traceability from strategy to implementation
"""

from datetime import datetime

from .. import db
from .mixins import TenantMixin

# ============================================================================
# JUNCTION TABLES - Define before models to avoid NameError
# ============================================================================

# Junction table: Goal ↔ EnterpriseInitiative (Many-to-Many)
initiative_goals = db.Table(
    "initiative_goals",
    db.Column(
        "initiative_id",
        db.Integer,
        db.ForeignKey("enterprise_initiatives.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "goal_id", db.Integer, db.ForeignKey("goals.id", ondelete="CASCADE"), primary_key=True
    ),
    db.Column("contribution_level", db.String(20)),  # 'primary', 'supporting', 'indirect'
    db.Column("expected_impact", db.Text),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)

# Junction table: Principle ↔ EnterpriseInitiative (Many-to-Many)
initiative_principles = db.Table(
    "initiative_principles",
    db.Column(
        "initiative_id",
        db.Integer,
        db.ForeignKey("enterprise_initiatives.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "principle_id",
        db.Integer,
        db.ForeignKey("principles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("compliance_status", db.String(30)),  # 'compliant', 'partial', 'at_risk', 'violation'
    db.Column("compliance_notes", db.Text),
    db.Column("exception_approved", db.Boolean, default=False),
    db.Column("exception_rationale", db.Text),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)

# Junction table: Driver ↔ Outcome (Many-to-Many)
driver_outcomes = db.Table(
    "driver_outcomes",
    db.Column(
        "driver_id", db.Integer, db.ForeignKey("drivers.id", ondelete="CASCADE"), primary_key=True
    ),
    db.Column(
        "outcome_id", db.Integer, db.ForeignKey("outcomes.id", ondelete="CASCADE"), primary_key=True
    ),
    db.Column("contribution_type", db.String(30)),  # 'direct', 'indirect', 'supporting'
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)


# ============================================================================
# MOTIVATION LAYER MODELS
# ============================================================================


class Driver(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Driver element (Motivation Layer).

    An external or internal condition that motivates an organization to define goals
    and implement changes to achieve them.

    Examples:
    - "Regulatory compliance mandate for data privacy"
    - "Competitive pressure from market disruption"
    - "Customer demand for mobile experiences"
    - "Cost reduction mandate from board"

    Semantic Chain: Driver → Goal → Outcome → Requirement → Capability
    """

    __tablename__ = "drivers"

    id = db.Column(db.Integer, primary_key=True)

    # Identity
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate linkage (Basecoat pattern)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Driver classification
    driver_type = db.Column(
        db.String(50)
    )  # 'regulatory', 'competitive', 'customer', 'technology', 'financial', 'operational'
    source = db.Column(db.String(50))  # 'external' or 'internal'
    urgency = db.Column(db.String(20))  # 'critical', 'high', 'medium', 'low'

    # Impact assessment
    impact_scope = db.Column(db.String(50))  # 'global', 'regional', 'business_unit', 'department'
    impact_magnitude = db.Column(db.Integer)  # 1 - 10 scale
    time_sensitivity = db.Column(
        db.String(30)
    )  # 'immediate', 'short_term', 'medium_term', 'long_term'

    # Regulatory/Compliance drivers
    regulatory_body = db.Column(db.String(200))  # "EU Commission", "SEC", "FDA"
    regulation_reference = db.Column(db.String(200))  # "GDPR Article 17", "SOX Section 404"
    compliance_deadline = db.Column(db.Date)
    penalty_for_non_compliance = db.Column(db.Text)

    # Business context
    business_priority = db.Column(db.Integer)  # 1 - 10 priority ranking
    stakeholder_group = db.Column(db.String(100))  # Who's driving this
    strategic_importance = db.Column(db.Integer)  # 1 - 10 scale

    # Status
    status = db.Column(db.String(30), default="active")  # active, monitoring, resolved, deprecated
    identified_date = db.Column(db.Date)
    resolution_date = db.Column(db.Date)

    # Documentation
    evidence_url = db.Column(db.String(500))
    notes = db.Column(db.Text)

    # Metadata
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))
    application_component_id = db.Column(
        db.Integer,
        db.ForeignKey("application_components.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )  # Optional: link to specific application
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture = db.relationship("ArchitectureModel", backref="drivers")
    created_by = db.relationship("User", backref="created_drivers")

    # Forward relationships (Driver → Goal → Outcome)
    goals = db.relationship("Goal", back_populates="driver", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Driver {self.name} ({self.driver_type})>"


class Goal(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Goal element (Motivation Layer).

    A high-level statement of intent, direction, or desired end state for an organization.
    Goals are motivated by Drivers and realized through Outcomes.

    Examples:
    - "Achieve GDPR compliance by Q2 2024"
    - "Reduce operational costs by 20% within 18 months"
    - "Become market leader in customer satisfaction"

    Semantic Chain: Driver → Goal → Outcome → Capability → Process
    """

    __tablename__ = "goals"

    id = db.Column(db.Integer, primary_key=True)

    # Identity
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate linkage (Basecoat pattern)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Motivation relationship (Goal is motivated by Driver)
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"), index=True)

    # Goal classification
    goal_type = db.Column(db.String(50))  # 'strategic', 'operational', 'tactical'
    category = db.Column(
        db.String(50)
    )  # 'growth', 'efficiency', 'innovation', 'compliance', 'quality'
    time_horizon = db.Column(
        db.String(30)
    )  # 'short_term', 'medium_term', 'long_term'  # <1yr, 1 - 3yr, >3yr

    # SMART goal attributes
    specific_objective = db.Column(db.Text)  # Specific - What exactly will be achieved
    measurable_metrics = db.Column(db.Text)  # Measurable - JSON array of KPIs
    achievable_assessment = db.Column(db.Text)  # Achievable - Feasibility notes
    relevant_alignment = db.Column(db.Text)  # Relevant - How it aligns with strategy
    time_bound_target = db.Column(db.Date)  # Time-bound - Target completion date

    # Goal metrics
    target_value = db.Column(db.String(100))  # "20% reduction", "€50M revenue"
    current_value = db.Column(db.String(100))  # Current state
    baseline_value = db.Column(db.String(100))  # Starting point
    measurement_unit = db.Column(db.String(50))  # "percentage", "EUR", "count"

    # Progress tracking
    progress_percentage = db.Column(db.Integer)  # 0 - 100% progress toward goal
    achievement_status = db.Column(db.String(30), default="not_started")
    # not_started, in_progress, on_track, at_risk, achieved, failed

    # Governance
    business_owner = db.Column(db.String(200))
    executive_sponsor = db.Column(db.String(200))
    review_frequency = db.Column(db.String(30))  # monthly, quarterly, annual
    next_review_date = db.Column(db.Date)

    # Strategic alignment
    strategic_priority = db.Column(db.Integer)  # 1 - 10 ranking
    business_impact = db.Column(db.Integer)  # 1 - 10 scale
    investment_required = db.Column(db.Numeric(15, 2))  # Budget allocation

    # Status
    status = db.Column(
        db.String(30), default="active"
    )  # draft, active, on_hold, achieved, cancelled
    start_date = db.Column(db.Date)
    target_date = db.Column(db.Date)
    achieved_date = db.Column(db.Date)

    # Dependencies
    parent_goal_id = db.Column(db.Integer, db.ForeignKey("goals.id"))  # Hierarchical goals

    # Documentation
    business_case_url = db.Column(db.String(500))
    notes = db.Column(db.Text)

    # Metadata
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))
    application_component_id = db.Column(
        db.Integer,
        db.ForeignKey("application_components.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )  # Optional: link to specific application
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture = db.relationship("ArchitectureModel", backref="goals")
    created_by = db.relationship("User", backref="created_goals")
    driver = db.relationship("Driver", back_populates="goals")

    # Self-referential hierarchy
    parent_goal = db.relationship("Goal", remote_side=[id], backref="sub_goals")

    # Initiative relationships (Goal supports Initiatives)
    initiatives = db.relationship(
        "EnterpriseInitiative", secondary=initiative_goals, back_populates="strategic_goals"
    )

    # Note: Outcome → Goal relationship is defined in Outcome model via goal_id foreign key

    def calculate_progress(self):
        """Calculate progress percentage from current vs target values."""
        try:
            if self.current_value and self.target_value and self.baseline_value:
                current = float(self.current_value.replace("%", "").replace(",", ""))
                target = float(self.target_value.replace("%", "").replace(",", ""))
                baseline = float(self.baseline_value.replace("%", "").replace(",", ""))

                if target != baseline:
                    progress = ((current - baseline) / (target - baseline)) * 100
                    self.progress_percentage = max(0, min(100, int(progress)))
                    return self.progress_percentage
        except (ValueError, ZeroDivisionError, AttributeError):
            pass
        return self.progress_percentage or 0

    def __repr__(self):
        return f"<Goal {self.name} ({self.achievement_status})>"


# ============================================================================
# Additional Motivation Layer Models
# ============================================================================


class Meaning(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Meaning element (Motivation Layer).

    Represents the knowledge or expertise present in, or the interpretation given to,
    a core element in a particular context.

    Examples:
    - "Legal Interpretation of GDPR" associated with a Representation
    - "Customer Satisfaction" meaning associated with a Business Object
    """

    __tablename__ = "meanings"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])

    def __repr__(self):
        return f"<Meaning {self.name}>"


class Value(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Value element (Motivation Layer).

    Represents the relative worth, utility, or importance of a core element or an outcome.

    Examples:
    - "Cost Reduction"
    - "Improved Customer Experience"
    - "Risk Mitigation"
    """

    __tablename__ = "values"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Value Specifics
    value_type = db.Column(db.String(50))  # Financial, Operational, Strategic, Social
    amount = db.Column(db.Numeric(15, 2))  # Optional quantified value
    currency = db.Column(db.String(10))

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])

    def __repr__(self):
        return f"<Value {self.name}>"


class Assessment(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Assessment element (Motivation Layer).

    Represents the outcome of some analysis of some state of affairs.

    Examples:
    - "Customer Satisfaction Survey 2023"
    - "Architecture Maturity Assessment"
    - "Gap Analysis Report"
    """

    __tablename__ = "assessments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Driver linkage — an Assessment is conducted against a Driver
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"), nullable=True, index=True)

    # Assessment Specifics
    assessment_type = db.Column(db.String(50))  # SWOT, Maturity, Risk, Performance
    result_score = db.Column(db.String(50))
    assessor = db.Column(db.String(100))
    date_assessed = db.Column(db.Date)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    driver = db.relationship("Driver", backref="assessments", foreign_keys=[driver_id])

    def __repr__(self):
        return f"<Assessment {self.name}>"


class Stakeholder(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Stakeholder element (Motivation Layer).

    Represents the role of an individual, team, or organization (or classes thereof)
    that represents their interests in the effects of the architecture.

    Examples:
    - "Chief Financial Officer"
    - "Architecture Review Board"
    - "End User Community"
    - "Regulatory Body"
    """

    __tablename__ = "stakeholders"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate linkage (Basecoat pattern)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Stakeholder specifics
    role = db.Column(db.String(100))  # Role/title of the stakeholder
    stakeholder_type = db.Column(db.String(50))  # 'individual', 'group', 'organization', 'role'
    power_level = db.Column(db.String(20))  # high, medium, low
    interest_level = db.Column(db.String(20))  # high, medium, low
    influence_score = db.Column(db.Integer)  # 0-100 influence score
    engagement_strategy = db.Column(db.String(50))  # manage_closely, keep_satisfied, keep_informed, monitor
    communication_frequency = db.Column(db.String(30))  # daily, weekly, monthly, quarterly, as_needed

    # Contact information
    contact_name = db.Column(db.String(200))
    contact_email = db.Column(db.String(255))
    contact_phone = db.Column(db.String(50))
    department = db.Column(db.String(100))
    organization = db.Column(db.String(200))

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    created_by = db.relationship("User", backref="created_stakeholders")

    def __repr__(self):
        return f"<Stakeholder {self.name} ({self.role or 'no role'})>"


# ============================================================================
# Motivation Bridge (journey <-> enterprise motivation layer)
# ============================================================================


class MotivationBridgeLink(TenantMixin, db.Model):
    """
    Non-destructive bridge record linking a solution-scoped motivation element
    (SolutionDriver, SolutionGoal, SolutionOutcome, SolutionPrinciple — created
    during the AI solution-design journey) to the corresponding enterprise
    motivation entity (Driver, Goal, Outcome, Principle) it was promoted to.

    This is a brand-new table (safe under db.create_all(), which never adds
    columns to existing tables but does create missing tables). It never
    mutates, deletes, or retypes any existing table or Solution* row — it is
    purely an additive mapping used by app.services.motivation_bridge_service
    to make journey motivation visible/traceable at the enterprise level.

    The unique constraint on (solution_element_type, solution_element_id)
    ensures at most one bridge link per solution-side element, which is what
    makes re-running the bridge idempotent (a no-op on already-bridged rows).
    """

    __tablename__ = "motivation_bridge_links"

    id = db.Column(db.Integer, primary_key=True)

    # Which Solution this bridge link was produced for (journey side)
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Solution-scoped source element, e.g. ('SolutionDriver', 17)
    solution_element_type = db.Column(db.String(50), nullable=False)
    solution_element_id = db.Column(db.Integer, nullable=False)

    # Enterprise-side element it was bridged to, e.g. ('Driver', 42)
    enterprise_element_type = db.Column(db.String(50), nullable=False)
    enterprise_element_id = db.Column(db.Integer, nullable=False)

    # Convenience pointer to the shared ArchiMateElement row created for the
    # enterprise entity (nullable — the enterprise entity may not have one in
    # rare edge cases, e.g. it was matched to a pre-existing row without one).
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "solution_element_type",
            "solution_element_id",
            name="uq_motivation_bridge_solution_element",
        ),
    )

    def __repr__(self):
        return (
            f"<MotivationBridgeLink {self.solution_element_type}:{self.solution_element_id} "
            f"-> {self.enterprise_element_type}:{self.enterprise_element_id}>"
        )


# Event Listeners

from sqlalchemy import event


@event.listens_for(Meaning, "after_insert")
def create_meaning_archimate(mapper, connection, target):
    """Auto-create ArchiMateElement for Meaning"""
    from sqlalchemy import insert

    from .archimate_core import ArchiMateElement

    if not target.archimate_element_id:
        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                name=target.name,
                type="Meaning",
                layer="Motivation",
                description=target.description or f"Meaning: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(Value, "after_insert")
def create_value_archimate(mapper, connection, target):
    """Auto-create ArchiMateElement for Value"""
    from sqlalchemy import insert

    from .archimate_core import ArchiMateElement

    if not target.archimate_element_id:
        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                name=target.name,
                type="Value",
                layer="Motivation",
                description=target.description or f"Value: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(Assessment, "after_insert")
def create_assessment_archimate(mapper, connection, target):
    """Auto-create ArchiMateElement for Assessment"""
    from sqlalchemy import insert

    from .archimate_core import ArchiMateElement

    if not target.archimate_element_id:
        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                name=target.name,
                type="Assessment",
                layer="Motivation",
                description=target.description or f"Assessment: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]
