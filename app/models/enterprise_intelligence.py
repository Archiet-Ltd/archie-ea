"""
Enterprise Intelligence Models

Relationship tables and models to enable enterprise-level portfolio intelligence:
1. Portfolio-to-Application linkage
2. Organization-to-Application ownership/usage
3. Cost/Financial tracking per application
4. Risk aggregation at portfolio level
5. Strategic alignment tracking

These models bridge the gap between strategic initiatives and tactical execution.
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin

# ============================================================================
# Priority 1: Portfolio Initiative <-> Application Linkage
# ============================================================================


class PortfolioInitiative(db.Model):
    """
    Enterprise/Portfolio-level strategic initiative
    Maps to business strategy and cascades to projects/applications
    """

    __tablename__ = "portfolio_initiatives"

    id = db.Column(db.Integer, primary_key=True)

    # Identity
    name = db.Column(db.String(255), nullable=False, index=True)
    code = db.Column(db.String(50), unique=True, index=True)
    description = db.Column(db.Text)

    # Strategic Context
    strategic_objective = db.Column(db.String(500))  # Links to corporate strategy
    strategic_theme = db.Column(
        db.String(100), index=True
    )  # Digital Transformation, Cost Optimization, etc.
    initiative_type = db.Column(db.String(50))  # Strategic, Operational, Tactical

    # Lifecycle
    status = db.Column(
        db.String(50), default="Proposed", index=True
    )  # Proposed, Approved, Active, On Hold, Completed, Cancelled
    priority = db.Column(db.String(20), index=True)  # Critical, High, Medium, Low
    start_date = db.Column(db.Date)
    target_end_date = db.Column(db.Date)
    actual_end_date = db.Column(db.Date)

    # Financial
    total_budget = db.Column(db.Numeric(15, 2))
    spent_to_date = db.Column(db.Numeric(15, 2))
    forecast_cost = db.Column(db.Numeric(15, 2))
    expected_roi_percentage = db.Column(db.Numeric(5, 2))

    # Business Value
    business_value_score = db.Column(db.Integer)  # 1 - 100
    risk_score = db.Column(db.Integer)  # 1 - 100
    strategic_alignment_score = db.Column(db.Integer)  # 1 - 100

    # Ownership
    executive_sponsor = db.Column(db.String(255))
    program_manager = db.Column(db.String(255))
    business_owner_unit = db.Column(db.String(255))

    # Tracking
    completion_percentage = db.Column(db.Integer, default=0)
    health_status = db.Column(db.String(20))  # Green, Amber, Red
    last_status_update = db.Column(db.DateTime)

    # Links
    parent_initiative_id = db.Column(db.Integer, db.ForeignKey("portfolio_initiatives.id"))
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    child_initiatives = db.relationship(
        "PortfolioInitiative", backref=db.backref("parent", remote_side="PortfolioInitiative.id")
    )
    # Disabled: requires foreign key migration for portfolio_initiative_applications
    # applications = db.relationship('ApplicationComponent', secondary='portfolio_initiative_applications', back_populates='portfolio_initiatives')
    success_metrics = db.relationship(
        "InitiativeSuccessMetric", back_populates="initiative", cascade="all, delete-orphan"
    )


class InitiativeSuccessMetric(db.Model):
    """Success metrics for portfolio initiatives (OKRs, KPIs)"""

    __tablename__ = "initiative_success_metrics"

    id = db.Column(db.Integer, primary_key=True)
    initiative_id = db.Column(db.Integer, db.ForeignKey("portfolio_initiatives.id"), nullable=False)

    metric_name = db.Column(db.String(255), nullable=False)
    metric_type = db.Column(db.String(50))  # KPI, OKR, Financial, Operational
    target_value = db.Column(db.String(100))
    actual_value = db.Column(db.String(100))
    unit_of_measure = db.Column(db.String(50))
    measurement_frequency = db.Column(db.String(50))  # Daily, Weekly, Monthly, Quarterly

    baseline_value = db.Column(db.String(100))
    baseline_date = db.Column(db.Date)
    target_date = db.Column(db.Date)

    status = db.Column(db.String(20))  # On Track, At Risk, Behind
    last_measured_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    initiative = db.relationship("PortfolioInitiative", back_populates="success_metrics")


# ============================================================================
# Priority 2: Organization Unit <-> Application Ownership/Usage
# ============================================================================


class OrganizationUnit(TenantMixin, db.Model):
    """
    Business units, departments, teams within the enterprise
    Maps organizational structure for stakeholder management
    """

    __tablename__ = "organization_units"

    id = db.Column(db.Integer, primary_key=True)

    # Identity
    name = db.Column(db.String(255), nullable=False, index=True)
    code = db.Column(db.String(50), unique=True, index=True)
    description = db.Column(db.Text)

    # Hierarchy
    unit_type = db.Column(db.String(50), index=True)  # Division, Department, Team, Function
    parent_unit_id = db.Column(db.Integer, db.ForeignKey("organization_units.id"))
    level = db.Column(db.Integer)  # Hierarchy depth

    # Leadership
    head_of_unit = db.Column(db.String(255))
    contact_email = db.Column(db.String(255))

    # Financial
    cost_center_code = db.Column(db.String(50), index=True)
    annual_budget = db.Column(db.Numeric(15, 2))
    it_budget = db.Column(db.Numeric(15, 2))

    # Location
    primary_location = db.Column(db.String(255))
    region = db.Column(db.String(100))
    country = db.Column(db.String(100))

    # Status
    status = db.Column(db.String(50), default="Active")  # Active, Inactive, Reorganized
    effective_date = db.Column(db.Date)
    end_date = db.Column(db.Date)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    child_units = db.relationship(
        "OrganizationUnit", backref=db.backref("parent", remote_side="OrganizationUnit.id")
    )
    owned_applications = db.relationship("ApplicationOwnership", back_populates="organization_unit")
    used_applications = db.relationship("ApplicationUsage", back_populates="organization_unit")


class ApplicationOwnership(TenantMixin, db.Model):
    """
    Which organization unit OWNS/is responsible for an application
    (Product owner, budget holder, strategic direction)
    """

    __tablename__ = "application_ownership"

    id = db.Column(db.Integer, primary_key=True)

    application_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )
    organization_unit_id = db.Column(
        db.Integer, db.ForeignKey("organization_units.id"), nullable=False, index=True
    )

    ownership_type = db.Column(
        db.String(50), nullable=False
    )  # Business Owner, Product Owner, Technical Owner, Budget Holder
    ownership_percentage = db.Column(db.Integer)  # For shared ownership (0 - 100)

    primary_contact = db.Column(db.String(255))
    contact_email = db.Column(db.String(255))

    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)

    notes = db.Column(db.Text)

    # Consolidation: points to the ApplicationOwner row this record was
    # merged into during backfill
    retired_into_id = db.Column(
        db.Integer,
        db.ForeignKey("application_owners.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    application = db.relationship("ApplicationComponent", backref="ownership_records")
    organization_unit = db.relationship("OrganizationUnit", back_populates="owned_applications")


class ApplicationUsage(db.Model):
    """
    Which organization units USE an application (user base, dependency)
    """

    __tablename__ = "application_usage"

    id = db.Column(db.Integer, primary_key=True)

    application_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )
    organization_unit_id = db.Column(
        db.Integer, db.ForeignKey("organization_units.id"), nullable=False, index=True
    )

    usage_type = db.Column(db.String(50))  # Primary User, Secondary User, Occasional User
    user_count = db.Column(db.Integer)
    criticality = db.Column(db.String(20))  # Critical, High, Medium, Low

    business_processes_supported = db.Column(db.Text)
    usage_frequency = db.Column(db.String(50))  # Daily, Weekly, Monthly, Quarterly

    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)

    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    application = db.relationship("ApplicationComponent", backref="usage_records")
    organization_unit = db.relationship("OrganizationUnit", back_populates="used_applications")


# ============================================================================
# Priority 3: Cost/Financial Tracking per Application
# ============================================================================


class ApplicationCost(db.Model):
    """
    RETIRED — The annual cost of an application is now stored in
    ApplicationComponent.total_cost_of_ownership and accessed through
    app.services.application_cost_accessor.get_annual_cost().
    This table exists for historical data only; no new writes arrive here.
    """

    __tablename__ = "application_costs"

    id = db.Column(db.Integer, primary_key=True)

    application_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )

    # Time Period
    fiscal_year = db.Column(db.Integer, nullable=False, index=True)
    fiscal_quarter = db.Column(db.Integer, index=True)  # 1 - 4
    cost_period_start = db.Column(db.Date)
    cost_period_end = db.Column(db.Date)

    # Cost Breakdown
    license_cost = db.Column(db.Numeric(15, 2))  # Software licenses
    subscription_cost = db.Column(db.Numeric(15, 2))  # SaaS subscriptions
    maintenance_cost = db.Column(db.Numeric(15, 2))  # Support & maintenance
    infrastructure_cost = db.Column(db.Numeric(15, 2))  # Hosting, cloud, servers
    development_cost = db.Column(db.Numeric(15, 2))  # New features, enhancements
    support_cost = db.Column(db.Numeric(15, 2))  # Operations, helpdesk
    training_cost = db.Column(db.Numeric(15, 2))  # User training
    other_cost = db.Column(db.Numeric(15, 2))

    # Totals
    total_cost = db.Column(db.Numeric(15, 2), nullable=False)  # Sum of above
    total_budget = db.Column(db.Numeric(15, 2))
    variance = db.Column(db.Numeric(15, 2))  # Budget - Actual

    # Cost Allocation
    cost_center = db.Column(db.String(50))
    gl_account = db.Column(db.String(50))
    cost_type = db.Column(db.String(50))  # CapEx, OpEx

    # Business Metrics
    user_count = db.Column(db.Integer)
    cost_per_user = db.Column(db.Numeric(10, 2))
    transaction_volume = db.Column(db.Integer)
    cost_per_transaction = db.Column(db.Numeric(10, 4))

    # Notes
    notes = db.Column(db.Text)
    cost_assumptions = db.Column(db.Text)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    application = db.relationship("ApplicationComponent", backref="cost_records")


class ApplicationROI(db.Model):
    """
    Return on Investment tracking for applications
    """

    __tablename__ = "application_roi"

    id = db.Column(db.Integer, primary_key=True)

    application_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )

    # Assessment Period
    assessment_date = db.Column(db.Date, nullable=False)
    assessment_period_months = db.Column(db.Integer)  # Lookback period

    # Benefits (Annual)
    cost_savings = db.Column(db.Numeric(15, 2))
    revenue_generation = db.Column(db.Numeric(15, 2))
    productivity_gain_hours = db.Column(db.Numeric(10, 2))
    productivity_gain_value = db.Column(db.Numeric(15, 2))
    risk_mitigation_value = db.Column(db.Numeric(15, 2))

    total_benefits = db.Column(db.Numeric(15, 2))

    # Costs (from ApplicationCost)
    total_costs = db.Column(db.Numeric(15, 2))

    # ROI Calculation
    net_benefit = db.Column(db.Numeric(15, 2))  # Benefits - Costs
    roi_percentage = db.Column(db.Numeric(5, 2))  # (Benefits - Costs) / Costs * 100
    payback_period_months = db.Column(db.Integer)

    # Business Value Score
    business_value_score = db.Column(db.Integer)  # 1 - 100
    strategic_value_score = db.Column(db.Integer)  # 1 - 100

    # Qualitative Benefits
    qualitative_benefits = db.Column(db.Text)

    # Status
    status = db.Column(db.String(50))  # Draft, Approved, Under Review
    approved_by = db.Column(db.String(255))
    approval_date = db.Column(db.Date)

    # Metadata
    notes = db.Column(db.Text)
    assumptions = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    application = db.relationship("ApplicationComponent", backref="roi_assessments")


# ============================================================================
# Supporting: Business Outcome Metrics
# ============================================================================


class ApplicationBusinessMetric(db.Model):
    """
    Business KPIs that an application measures or improves
    Links applications to business outcomes
    """

    __tablename__ = "application_business_metrics"

    id = db.Column(db.Integer, primary_key=True)

    application_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )

    # Metric Definition
    metric_name = db.Column(db.String(255), nullable=False)
    metric_type = db.Column(
        db.String(50)
    )  # Revenue, Cost, Efficiency, Quality, Customer Satisfaction
    metric_category = db.Column(db.String(100))

    # Measurement
    baseline_value = db.Column(db.String(100))
    current_value = db.Column(db.String(100))
    target_value = db.Column(db.String(100))
    unit_of_measure = db.Column(db.String(50))

    # Trend
    trend_direction = db.Column(db.String(20))  # Improving, Stable, Declining
    measurement_frequency = db.Column(db.String(50))
    last_measured_at = db.Column(db.DateTime)

    # Impact
    application_contribution = db.Column(db.String(255))  # How app influences this metric
    improvement_percentage = db.Column(db.Numeric(5, 2))

    # Status
    status = db.Column(db.String(50))  # Active, Archived

    # Metadata
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    application = db.relationship("ApplicationComponent", backref="business_metrics")
