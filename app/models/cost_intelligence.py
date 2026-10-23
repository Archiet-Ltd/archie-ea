"""
Cost-Capability-Vendor Intelligence Models

Implements comprehensive TCO (Total Cost of Ownership) analysis and cost allocation:
- Cost allocation to capabilities (what does each capability cost?)
- Vendor cost tracking and contract management
- Service Level Agreements (SLAs) and vendor accountability
- Time-series cost metrics for trend analysis

Key EA Intelligence Fix #3:
- Complete cost intelligence for capability-based TCO analysis
- Vendor contract and SLA management
- Service delivery metrics and vendor performance tracking
"""

from datetime import datetime
from decimal import Decimal

from .. import db
from .mixins import TenantMixin

# ============================================================================
# CAPABILITY COST ALLOCATION - TCO Analysis
# ============================================================================


class CapabilityCostAllocation(TenantMixin, db.Model):
    """
    Cost allocation to business capabilities for TCO analysis.

    Tracks all costs associated with a capability including:
    - Vendor software licensing
    - Infrastructure and hosting
    - Personnel (FTE allocation)
    - Professional services and consulting
    - Maintenance and support

    Enables answers to questions like:
    - "What is the total cost of our Order Management capability?"
    - "Which capabilities consume the most budget?"
    - "What's the ROI of capability investments?"
    """

    __tablename__ = "capability_cost_allocations"

    id = db.Column(db.Integer, primary_key=True)

    # Core relationship
    capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id"), nullable=False, index=True
    )

    # Cost period
    fiscal_year = db.Column(db.Integer, nullable=False, index=True)
    fiscal_quarter = db.Column(db.Integer)  # 1, 2, 3, 4 (optional for quarterly tracking)
    fiscal_month = db.Column(db.Integer)  # 1 - 12 (optional for monthly tracking)
    period_start_date = db.Column(db.Date, nullable=False)
    period_end_date = db.Column(db.Date, nullable=False)

    # Cost categories (all in same currency - defined by currency field)
    # Vendor costs
    software_licensing_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))
    software_maintenance_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))
    saas_subscription_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))

    # Infrastructure costs
    infrastructure_hosting_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))
    cloud_compute_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))
    storage_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))
    network_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))

    # Personnel costs
    fte_count = db.Column(db.Float)  # Full-time equivalent headcount
    personnel_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))
    contractor_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))

    # Services costs
    professional_services_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))
    consulting_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))
    training_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))

    # Operational costs
    support_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))
    maintenance_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))
    incident_resolution_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))

    # Other costs
    depreciation_cost = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))
    other_costs = db.Column(db.Numeric(15, 2), default=Decimal("0.00"))
    other_costs_description = db.Column(db.Text)

    # Cost metadata
    currency = db.Column(db.String(3), default="USD")  # ISO 4217 currency code
    cost_type = db.Column(db.String(30))  # 'actual', 'budget', 'forecast'
    allocation_method = db.Column(
        db.String(50)
    )  # 'direct', 'allocated', 'activity_based', 'hybrid'

    # Business value metrics (for ROI calculation)
    business_value_generated = db.Column(db.Numeric(15, 2))  # Revenue or cost savings attributed
    transaction_volume = db.Column(db.Integer)  # Number of transactions supported
    user_count = db.Column(db.Integer)  # Active user count for the capability

    # Variance tracking
    budget_variance_amount = db.Column(db.Numeric(15, 2))
    budget_variance_percentage = db.Column(db.Float)

    # Notes and documentation
    notes = db.Column(db.Text)
    cost_breakdown_url = db.Column(db.String(500))  # Link to detailed cost analysis

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    capability = db.relationship("BusinessCapability", backref="cost_allocations")
    created_by = db.relationship("User", backref="created_cost_allocations")

    # Vendor contracts contributing to this cost
    vendor_contracts = db.relationship(
        "VendorContract",
        secondary="capability_vendor_costs",
        back_populates="capability_allocations",
    )

    def calculate_total_cost(self):
        """Calculate total cost across all categories."""
        cost_fields = [
            self.software_licensing_cost,
            self.software_maintenance_cost,
            self.saas_subscription_cost,
            self.infrastructure_hosting_cost,
            self.cloud_compute_cost,
            self.storage_cost,
            self.network_cost,
            self.personnel_cost,
            self.contractor_cost,
            self.professional_services_cost,
            self.consulting_cost,
            self.training_cost,
            self.support_cost,
            self.maintenance_cost,
            self.incident_resolution_cost,
            self.depreciation_cost,
            self.other_costs,
        ]
        return sum(cost or Decimal("0.00") for cost in cost_fields)

    def calculate_cost_per_transaction(self):
        """Calculate cost per transaction if volume data available."""
        total = self.calculate_total_cost()
        if self.transaction_volume and self.transaction_volume > 0:
            return total / Decimal(str(self.transaction_volume))
        return None

    def calculate_cost_per_user(self):
        """Calculate cost per user if user count available."""
        total = self.calculate_total_cost()
        if self.user_count and self.user_count > 0:
            return total / Decimal(str(self.user_count))
        return None

    def calculate_roi_percentage(self):
        """Calculate ROI if business value data available."""
        total_cost = self.calculate_total_cost()
        if self.business_value_generated and total_cost > 0:
            roi = ((self.business_value_generated - total_cost) / total_cost) * 100
            return float(roi)
        return None

    __table_args__ = (
        db.Index("idx_capability_cost_period", "capability_id", "fiscal_year", "fiscal_quarter"),
    )

    def __repr__(self):
        return f"<CapabilityCostAllocation {self.capability.name if self.capability else 'Unknown'} FY{self.fiscal_year}Q{self.fiscal_quarter or 'ALL'}>"


# ============================================================================
# VENDOR CONTRACT MANAGEMENT
# ============================================================================
# NOTE: VendorContract is now defined in application_portfolio.py
# This class is commented out to avoid SQLAlchemy conflicts

# NOTE: VendorContract class has been moved to application_portfolio.py
# The duplicate class definition below has been removed to avoid SQLAlchemy conflicts


# ============================================================================
# SERVICE LEVEL AGREEMENTS (SLAs)
# ============================================================================


class ServiceLevelAgreement(db.Model):
    """
    Service Level Agreement definition and compliance tracking.

    Tracks SLA metrics for vendor services including:
    - Availability targets
    - Performance metrics
    - Response and resolution times
    - Actual performance vs. targets
    - SLA violations and penalties

    Maps the `service_level_agreements` table. No route or service currently
    reads or writes this model — left in place (not deleted) per ADR 0008,
    since a table with live schema is not code to remove on a hunch.
    """

    __tablename__ = "service_level_agreements"

    id = db.Column(db.Integer, primary_key=True)

    # SLA identity
    sla_name = db.Column(db.String(200), nullable=False)
    sla_code = db.Column(db.String(50))
    description = db.Column(db.Text)

    # Contract relationship
    contract_id = db.Column(
        db.Integer, db.ForeignKey("vendor_contracts.id"), nullable=False, index=True
    )

    # Capability relationship (which capability does this SLA protect?)
    capability_id = db.Column(db.Integer, db.ForeignKey("business_capability.id"), index=True)

    # SLA metric type
    metric_type = db.Column(db.String(50), nullable=False)
    # 'availability', 'response_time', 'resolution_time', 'throughput', 'error_rate', 'data_accuracy'

    metric_category = db.Column(
        db.String(30)
    )  # 'performance', 'availability', 'quality', 'security'

    # SLA targets
    target_value = db.Column(db.String(100), nullable=False)  # "99.9%", "< 2 seconds", "< 4 hours"
    measurement_unit = db.Column(
        db.String(50)
    )  # "percentage", "seconds", "hours", "requests_per_second"
    measurement_period = db.Column(db.String(30))  # 'monthly', 'quarterly', 'annually'

    # Thresholds
    warning_threshold = db.Column(db.String(100))  # Warning level before violation
    critical_threshold = db.Column(db.String(100))  # Critical failure threshold

    # Current performance
    current_value = db.Column(db.String(100))  # Latest measured value
    last_measured_at = db.Column(db.DateTime)
    compliance_status = db.Column(
        db.String(30)
    )  # 'compliant', 'warning', 'violation', 'not_measured'

    # Performance tracking
    ytd_compliance_percentage = db.Column(db.Float)  # Year-to-date compliance %
    last_12_months_compliance = db.Column(db.Float)
    violation_count_ytd = db.Column(db.Integer, default=0)
    violation_count_total = db.Column(db.Integer, default=0)

    # Consequences
    has_penalty_clause = db.Column(db.Boolean, default=False)
    penalty_per_violation = db.Column(db.Numeric(12, 2))
    service_credits_available = db.Column(db.Boolean, default=False)
    service_credit_percentage = db.Column(db.Float)

    # Measurement and reporting
    measurement_method = db.Column(db.Text)  # How is this measured
    reporting_frequency = db.Column(db.String(30))  # How often are SLA reports provided
    responsible_party = db.Column(db.String(100))  # Who measures and reports

    # Exclusions and exceptions
    scheduled_maintenance_excluded = db.Column(db.Boolean, default=True)
    force_majeure_exclusions = db.Column(db.Text)

    # Status
    status = db.Column(
        db.String(30), default="active"
    )  # active, suspended, under_review, terminated
    effective_date = db.Column(db.Date)
    review_frequency = db.Column(db.String(30))  # How often SLA is reviewed
    next_review_date = db.Column(db.Date)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    contract = db.relationship("VendorContract", back_populates="slas")
    capability = db.relationship("BusinessCapability", backref="slas")
    created_by = db.relationship("User", backref="created_slas")

    # SLA violation history
    violations = db.relationship("SLAViolation", back_populates="sla", cascade="all, delete-orphan")

    def is_compliant(self):
        """Check if SLA is currently compliant."""
        return self.compliance_status == "compliant"

    def calculate_compliance_rate(self, period_days=365):
        """Calculate compliance rate over specified period."""
        # This would query SLAViolation records to calculate actual compliance
        # Simplified implementation returns stored value
        return (
            self.last_12_months_compliance if period_days >= 365 else self.ytd_compliance_percentage
        )

    def __repr__(self):
        return f"<SLA {self.sla_code or self.sla_name}: {self.metric_type} = {self.target_value}>"


class SLAViolation(db.Model):
    """
    SLA violation incident tracking.

    Records individual SLA breach incidents for:
    - Compliance reporting
    - Penalty calculation
    - Trend analysis
    - Vendor performance assessment

    Maps the `sla_violations` table. No route or service currently reads or
    writes this model — left in place (not deleted) per ADR 0008, since a
    table with live schema is not code to remove on a hunch.
    """

    __tablename__ = "sla_violations"

    id = db.Column(db.Integer, primary_key=True)

    # SLA relationship
    sla_id = db.Column(
        db.Integer, db.ForeignKey("service_level_agreements.id"), nullable=False, index=True
    )

    # Violation details
    violation_date = db.Column(db.DateTime, nullable=False, index=True)
    violation_end_date = db.Column(db.DateTime)  # When service was restored
    duration_minutes = db.Column(db.Integer)

    # Metric values
    target_value = db.Column(db.String(100))  # What should have been achieved
    actual_value = db.Column(db.String(100))  # What was actually achieved
    variance = db.Column(db.String(100))  # Difference

    # Severity
    severity = db.Column(db.String(20))  # 'minor', 'moderate', 'major', 'critical'

    # Impact assessment
    impacted_user_count = db.Column(db.Integer)
    business_impact = db.Column(db.Text)
    financial_impact = db.Column(db.Numeric(12, 2))

    # Root cause
    root_cause_category = db.Column(
        db.String(50)
    )  # 'infrastructure', 'software', 'process', 'human_error', 'vendor'
    root_cause_description = db.Column(db.Text)
    vendor_responsibility = db.Column(db.Boolean, default=True)

    # Resolution
    resolution_description = db.Column(db.Text)
    preventive_measures = db.Column(db.Text)

    # Financial consequences
    penalty_applied = db.Column(db.Boolean, default=False)
    penalty_amount = db.Column(db.Numeric(12, 2))
    service_credit_issued = db.Column(db.Boolean, default=False)
    service_credit_amount = db.Column(db.Numeric(12, 2))

    # Status
    status = db.Column(db.String(30), default="open")  # open, investigating, resolved, closed
    acknowledged_by_vendor = db.Column(db.Boolean, default=False)
    dispute_raised = db.Column(db.Boolean, default=False)
    dispute_resolution = db.Column(db.Text)

    # Metadata
    reported_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    sla = db.relationship("ServiceLevelAgreement", back_populates="violations")
    reported_by = db.relationship("User", backref="reported_sla_violations")

    def __repr__(self):
        return f"<SLAViolation {self.sla.sla_name if self.sla else 'Unknown'} on {self.violation_date}>"


# ============================================================================
# JUNCTION TABLES
# ============================================================================

# CapabilityCostAllocation ↔ VendorContract (Many-to-Many)
capability_vendor_costs = db.Table(
    "capability_vendor_costs",
    db.Column(
        "capability_cost_allocation_id",
        db.Integer,
        db.ForeignKey("capability_cost_allocations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "vendor_contract_id",
        db.Integer,
        db.ForeignKey("vendor_contracts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "allocated_amount", db.Numeric(15, 2)
    ),  # How much of this contract cost is allocated to this capability
    db.Column("allocation_percentage", db.Float),  # What % of contract cost goes to this capability
    db.Column(
        "allocation_basis", db.String(50)
    ),  # 'usage', 'user_count', 'transaction_volume', 'direct'
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)
