"""
Application Rationalization Models

Enhanced models to support comprehensive application portfolio rationalization:
- Application replacement tracking (structured, not string-based)
- Application dependency graphs for impact analysis
- Rationalization scoring (TIME framework: Tolerate/Invest/Migrate/Eliminate)
- Canonical disposition taxonomy (7Rs: Retain/Rehost/Replatform/Refactor/Replace/Retire/Consolidate)
- Vendor concentration risk analysis
- License allocation tracking per application

Fills critical gaps identified in EA analysis for consolidation and rationalization.
"""

import enum
import logging
from datetime import date, datetime

from .. import db
from .mixins import TenantMixin


# ============================================================================
# CANONICAL DISPOSITION TAXONOMY — Single governed set of 7R actions
# ============================================================================


class DispositionAction(str, enum.Enum):
    """
    Canonical 7R disposition taxonomy for application portfolio rationalization.

    Replaces three conflicting label sets previously used across the platform:
    - TIME labels (TOLERATE/INVEST/MIGRATE/ELIMINATE) on ApplicationRationalizationScore
    - APP_DISPOSITION workflow values (retire/consolidate/replace/re-engineer/retain)
    - ConsolidationListEntry free-text values (decommission/retire/merge/replace/…)

    The TIME framework remains the *scoring* mechanism.  DispositionAction is the
    *action* vocabulary that EA practitioners communicate to business stakeholders.

    Stored as plain lowercase strings so the column is human-readable in SQL and
    interoperable with the existing string-typed disposition fields on other models.
    """

    RETAIN = "retain"
    """Keep the application as-is.  Maps from TIME: TOLERATE."""

    REHOST = "rehost"
    """Lift-and-shift to new infrastructure with no code changes (cloud migration)."""

    REPLATFORM = "replatform"
    """Move to a new platform with minor modifications to exploit cloud/PaaS benefits."""

    REFACTOR = "refactor"
    """Re-architect significantly to improve quality, scalability, or strategic fit.
    Maps from TIME: INVEST (high business value, needs technical uplift)."""

    REPLACE = "replace"
    """Swap out for a different product or custom build.
    Maps from TIME: MIGRATE (technical obsolescence, value preserved)."""

    RETIRE = "retire"
    """Decommission — no replacement required.
    Maps from TIME: ELIMINATE when no critical dependencies exist."""

    CONSOLIDATE = "consolidate"
    """Merge with one or more other applications.
    Maps from TIME: ELIMINATE when sibling capability overlap is the driver."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    """Recommendation cannot be made — critical evidence dimensions are missing.
    Assigned when evaluate_readiness() returns is_decision_ready=False.
    The TIME score is still computed and stored for tracking, but the disposition
    is flagged as unreliable until the missing data is supplied."""

    @property
    def label(self) -> str:
        """Human-readable label suitable for display in badges and dropdowns."""
        _labels = {
            "retain": "Retain",
            "rehost": "Rehost",
            "replatform": "Replatform",
            "refactor": "Refactor",
            "replace": "Replace",
            "retire": "Retire",
            "consolidate": "Consolidate",
            "insufficient_evidence": "Insufficient Evidence",
        }
        return _labels[self.value]

    @property
    def description(self) -> str:
        """One-line description for legend / tooltip display."""
        _descriptions = {
            "retain": "Keep as-is — adequate fit, minimal risk",
            "rehost": "Lift and shift to new infrastructure with no code changes",
            "replatform": "Move to a new platform with minor modifications",
            "refactor": "Re-architect to improve quality or strategic alignment",
            "replace": "Swap for a different product or custom-built solution",
            "retire": "Decommission — no replacement needed",
            "consolidate": "Merge with one or more overlapping applications",
            "insufficient_evidence": "Cannot recommend — critical evidence dimensions are missing",
        }
        return _descriptions[self.value]

    @property
    def badge_color(self) -> str:
        """Tailwind color stem for badge rendering (e.g. 'green' → bg-green-500/10)."""
        _colors = {
            "retain": "blue",
            "rehost": "sky",
            "replatform": "violet",
            "refactor": "amber",
            "replace": "orange",
            "retire": "red",
            "consolidate": "purple",
            "insufficient_evidence": "gray",
        }
        return _colors[self.value]


# Mapping from TIME framework labels to canonical DispositionAction values.
# Used by RationalizationScoringService to populate disposition_action from
# the existing rationalization_action (TIME) field.
TIME_TO_DISPOSITION: dict[str, DispositionAction] = {
    "TOLERATE": DispositionAction.RETAIN,
    "INVEST": DispositionAction.REFACTOR,
    "MIGRATE": DispositionAction.REPLACE,
    "ELIMINATE": DispositionAction.RETIRE,  # may be overridden to CONSOLIDATE by service
}

# Complete list of DispositionAction values, ordered for UI display.
DISPOSITION_ACTIONS_ORDERED: list[DispositionAction] = [
    DispositionAction.RETAIN,
    DispositionAction.REHOST,
    DispositionAction.REPLATFORM,
    DispositionAction.REFACTOR,
    DispositionAction.REPLACE,
    DispositionAction.CONSOLIDATE,
    DispositionAction.RETIRE,
]


def get_disposition_label(action: str | None) -> str:
    """
    Return a human-readable label for a disposition action value.

    Accepts either a DispositionAction value string (e.g. 'retain') or a
    TIME framework string (e.g. 'TOLERATE') and returns the canonical label.
    Returns the raw value unchanged if not recognised.

    Args:
        action: Disposition action string or TIME label.

    Returns:
        Human-readable label string.
    """
    if action is None:
        return "—"
    # Try canonical disposition value first
    try:
        return DispositionAction(action.lower()).label
    except ValueError:
        pass
    # Fall back to TIME → disposition mapping
    mapped = TIME_TO_DISPOSITION.get(action.upper())
    if mapped:
        return f"{action} → {mapped.label}"
    return action


# ============================================================================
# DATA READINESS — Dimension definitions for evidence completeness tracking
# ============================================================================

# Maps each readiness dimension name to its severity level.
# "high" severity dimensions must ALL be populated for is_decision_ready=True.
# "medium" severity dimensions contribute to readiness_score but do not gate it.
READINESS_DIMENSIONS = {
    "owner": {
        "severity": "high",
        "label": "Application Owner",
        "description": "Named owner responsible for the application",
    },
    "lifecycle": {
        "severity": "high",
        "label": "Lifecycle Status",
        "description": "Current lifecycle stage (active, deprecated, retired, etc.)",
    },
    "cost": {
        "severity": "medium",
        "label": "Cost Data",
        "description": "At least one cost field populated (TCO, license, maintenance, or infrastructure)",
    },
    "usage": {
        "severity": "medium",
        "label": "Usage / User Count",
        "description": "Number of active users recorded",
    },
    "dependencies": {
        "severity": "medium",
        "label": "Dependency Map",
        "description": "Application dependencies recorded (ApplicationDependency records or dependencies_count > 0)",
    },
    "capability": {
        "severity": "medium",
        "label": "Capability Mapping",
        "description": "At least one capability mapped to this application",
    },
    "vendor": {
        "severity": "medium",
        "label": "Vendor / Product",
        "description": "Vendor product linked to this application",
    },
    "risk": {
        "severity": "high",
        "label": "Risk Assessment",
        "description": "Technical or business risk level recorded",
    },
}

# ============================================================================
# APPLICATION REPLACEMENT - Structured Lifecycle Management
# ============================================================================


class ApplicationReplacement(TenantMixin, db.Model):
    """
    Structured application replacement and sunset tracking.

    Replaces the string-based ApplicationComponent.replacement_application
    with proper foreign key relationships and workflow tracking.

    Enables:
    - Structured replacement planning (App A replaces App B, C, D)
    - Migration workflow tracking (data, users, integrations)
    - Cost-benefit analysis of replacement
    - Sunset/decommission timeline management
    """

    __tablename__ = "application_replacements"

    id = db.Column(db.Integer, primary_key=True)

    # Core replacement relationship
    legacy_app_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )
    replacement_app_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )

    # Replacement strategy
    replacement_type = db.Column(
        db.String(30)
    )  # direct_replacement, phased_migration, multi_app_consolidation, cloud_migration
    replacement_rationale = db.Column(db.Text)

    # Migration complexity assessment
    migration_complexity = db.Column(db.String(20))  # simple, moderate, complex, critical
    estimated_effort_hours = db.Column(db.Integer)
    estimated_cost = db.Column(db.Numeric(15, 2))
    estimated_savings_annual = db.Column(db.Numeric(15, 2))
    payback_period_months = db.Column(db.Integer)

    # Migration planning
    data_migration_required = db.Column(db.Boolean, default=True)
    data_migration_plan = db.Column(db.Text)
    data_migration_status = db.Column(db.String(30))  # planned, in_progress, completed, blocked
    data_volume_gb = db.Column(db.Numeric(15, 2))
    data_quality_issues = db.Column(db.Text)

    user_migration_required = db.Column(db.Boolean, default=True)
    user_migration_plan = db.Column(db.Text)
    user_migration_status = db.Column(db.String(30))
    affected_user_count = db.Column(db.Integer)
    training_required = db.Column(db.Boolean, default=True)

    integration_changes_required = db.Column(db.Boolean, default=True)
    integration_migration_plan = db.Column(db.Text)
    affected_integrations_count = db.Column(db.Integer)
    integration_dependencies = db.Column(db.Text)  # JSON array

    # Timeline
    planned_start_date = db.Column(db.Date)
    planned_cutover_date = db.Column(db.Date)
    planned_decommission_date = db.Column(db.Date)
    actual_start_date = db.Column(db.Date)
    actual_cutover_date = db.Column(db.Date)
    actual_decommission_date = db.Column(db.Date)

    # Status tracking
    status = db.Column(db.String(30), default="planned", index=True)
    # planned, approved, in_progress, pilot, cutover_complete, decommissioned, cancelled, on_hold

    current_phase = db.Column(
        db.String(50)
    )  # planning, development, testing, pilot, cutover, decommission
    completion_percentage = db.Column(db.Integer, default=0)

    # Risk assessment
    business_risk_level = db.Column(db.String(20))  # low, medium, high, critical
    technical_risk_level = db.Column(db.String(20))
    data_loss_risk = db.Column(db.String(20))
    downtime_risk_hours = db.Column(db.Integer)
    rollback_plan = db.Column(db.Text)
    contingency_plan = db.Column(db.Text)

    # Governance
    business_sponsor = db.Column(db.String(200))
    technical_lead = db.Column(db.String(200))
    program_manager = db.Column(db.String(200))
    approval_status = db.Column(db.String(30))  # pending, approved, rejected
    approved_by = db.Column(db.String(200))
    approval_date = db.Column(db.Date)

    # Success criteria
    success_criteria = db.Column(db.Text)  # JSON array
    kpis_measured = db.Column(db.Text)  # JSON array
    post_migration_validation = db.Column(db.Text)

    # Cost tracking
    actual_cost = db.Column(db.Numeric(15, 2))
    budget_variance = db.Column(db.Numeric(15, 2))

    # Lessons learned
    lessons_learned = db.Column(db.Text)
    issues_encountered = db.Column(db.Text)  # JSON array

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    notes = db.Column(db.Text)

    # Relationships
    legacy_app = db.relationship(
        "ApplicationComponent", foreign_keys=[legacy_app_id], backref="being_replaced_by"
    )
    replacement_app = db.relationship(
        "ApplicationComponent", foreign_keys=[replacement_app_id], backref="replacing_apps"
    )
    created_by = db.relationship("User", backref="created_replacements")

    def __repr__(self):
        return f"<ApplicationReplacement Legacy:{self.legacy_app_id} → New:{self.replacement_app_id} ({self.status})>"

    def is_on_schedule(self):
        """Check if replacement is on schedule."""
        if self.status in ("cutover_complete", "decommissioned"):
            return True
        if not self.planned_cutover_date:
            return None
        return datetime.now().date() <= self.planned_cutover_date

    def calculate_roi(self):
        """Calculate ROI percentage if data available."""
        if self.estimated_savings_annual and self.estimated_cost and self.estimated_cost > 0:
            return float((self.estimated_savings_annual / self.estimated_cost) * 100)
        return None


# ============================================================================
# APPLICATION DEPENDENCY - Impact Analysis Graph
# ============================================================================


class ApplicationDependency(TenantMixin, db.Model):
    """
    Application-to-application dependencies for impact analysis.

    Tracks upstream/downstream dependencies to understand:
    - What breaks if we retire Application X?
    - What applications are critical (many dependencies)?
    - Circular dependencies that increase risk
    - Dependency strength/criticality

    Essential for rationalization impact assessment and retirement planning.
    """

    __tablename__ = "application_dependencies"

    id = db.Column(db.Integer, primary_key=True)

    # Core dependency relationship (source depends on target)
    source_app_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )
    target_app_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )

    # Dependency characteristics
    dependency_type = db.Column(db.String(50), nullable=False)
    # api_call, data_feed, batch_job, event_subscription, shared_database, authentication, reporting, orchestration

    dependency_strength = db.Column(
        db.String(20), default="medium"
    )  # critical, high, medium, low, optional

    # Technical details
    integration_pattern = db.Column(
        db.String(50)
    )  # rest_api, soap, message_queue, batch_file, database_link, event_stream
    communication_direction = db.Column(
        db.String(20)
    )  # bidirectional, unidirectional_push, unidirectional_pull
    data_flow_type = db.Column(db.String(30))  # real_time, near_real_time, batch, on_demand

    # Volume and frequency
    calls_per_day = db.Column(db.Integer)
    data_volume_mb_per_day = db.Column(db.Numeric(10, 2))
    frequency = db.Column(db.String(50))  # continuous, hourly, daily, weekly, monthly, on_demand
    peak_usage_time = db.Column(db.String(50))

    # Impact assessment
    business_criticality = db.Column(
        db.String(20)
    )  # mission_critical, business_critical, important, supporting
    can_be_replaced = db.Column(db.Boolean, default=False)
    replacement_cost_estimate = db.Column(db.Numeric(12, 2))
    alternative_solutions = db.Column(db.Text)  # JSON array of alternatives

    # Rationalization impact
    blocks_retirement = db.Column(
        db.Boolean, default=False
    )  # Does this dependency prevent retiring source app?
    retirement_sequence_order = db.Column(db.Integer)  # If retiring apps, what order? (1 = first)
    decoupling_complexity = db.Column(db.String(20))  # simple, moderate, complex, infeasible
    decoupling_cost_estimate = db.Column(db.Numeric(12, 2))

    # Resilience
    has_fallback = db.Column(db.Boolean, default=False)
    fallback_strategy = db.Column(db.Text)
    circuit_breaker_enabled = db.Column(db.Boolean, default=False)
    timeout_seconds = db.Column(db.Integer)
    retry_policy = db.Column(db.String(100))

    # SLA and performance
    sla_availability_percentage = db.Column(db.Numeric(5, 2))  # Expected uptime 99.9%
    actual_uptime_percentage = db.Column(db.Numeric(5, 2))
    average_response_time_ms = db.Column(db.Integer)
    error_rate_percentage = db.Column(db.Numeric(5, 2))

    # Documentation
    interface_documentation_url = db.Column(db.String(500))
    api_specification_url = db.Column(db.String(500))
    data_contract_url = db.Column(db.String(500))

    # Status
    status = db.Column(
        db.String(30), default="active"
    )  # active, deprecated, planned_removal, removed
    established_date = db.Column(db.Date)
    planned_removal_date = db.Column(db.Date)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships
    source_app = db.relationship(
        "ApplicationComponent", foreign_keys=[source_app_id], backref="dependencies_out"
    )
    target_app = db.relationship(
        "ApplicationComponent", foreign_keys=[target_app_id], backref="dependencies_in"
    )

    # Unique constraint: only one dependency record per app pair/type
    __table_args__ = (
        db.UniqueConstraint(
            "source_app_id", "target_app_id", "dependency_type", name="unique_app_dependency"
        ),
        db.Index("idx_dependency_strength", "dependency_strength"),
        db.Index("idx_blocks_retirement", "blocks_retirement"),
    )

    def __repr__(self):
        return f"<ApplicationDependency Source:{self.source_app_id} → Target:{self.target_app_id} ({self.dependency_type})>"


# ============================================================================
# APPLICATION RATIONALIZATION SCORE - TIME Framework
# ============================================================================


class ApplicationRationalizationScore(TenantMixin, db.Model):
    """
    Multi-dimensional application health scoring for rationalization decisions.

    Implements the TIME framework:
    - TOLERATE: Keep as-is (low cost, low value but necessary)
    - INVEST: Strategic investment needed (high value, needs improvement)
    - MIGRATE: Move to better platform (technical obsolescence)
    - ELIMINATE: Retire/consolidate (low value, high cost, or redundant)

    Aggregates scores across multiple dimensions:
    - Technical health (code quality, tech debt, architecture)
    - Business value (criticality, usage, strategic alignment)
    - Cost efficiency (TCO vs value delivered)
    - Vendor risk (lock-in, EOL, support)
    """

    __tablename__ = "application_rationalization_scores"

    # RAT-111: Valid state transitions for the review workflow.
    # key = current state, value = list of states that may follow.
    REVIEW_TRANSITIONS: dict[str, list[str]] = {
        "draft": ["reviewed"],
        "reviewed": ["approved", "rejected"],
        "rejected": ["draft"],          # re-submit after rework
        "approved": ["exception_approved"],  # exceptional override
        "exception_approved": [],        # terminal state
    }

    # RAT-112: Governed dispositions require ARB approval before "approved" transition.
    GOVERNED_DISPOSITIONS: frozenset = frozenset({"retire", "replace", "consolidate"})

    id = db.Column(db.Integer, primary_key=True)

    # Core relationship
    application_component_id = db.Column(
        db.Integer,
        db.ForeignKey("application_components.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Assessment period
    assessment_date = db.Column(db.Date, nullable=False, default=date.today)
    assessment_version = db.Column(db.Integer, default=1)

    # ==== DIMENSION 1: TECHNICAL HEALTH (0 - 100) ====
    technical_health_score = db.Column(db.Integer)  # Aggregate score

    # Technical health components
    code_quality_score = db.Column(db.Integer)  # From SonarQube, code reviews
    architecture_quality_score = db.Column(db.Integer)  # Modern patterns, modularity
    technical_debt_score = db.Column(db.Integer)  # 0 = high debt, 100 = no debt
    technology_currency_score = db.Column(db.Integer)  # How modern is the tech stack?
    scalability_score = db.Column(db.Integer)
    security_posture_score = db.Column(db.Integer)
    test_coverage_score = db.Column(db.Integer)
    documentation_quality_score = db.Column(db.Integer)

    # Technical risk factors
    uses_eol_technology = db.Column(db.Boolean, default=False)  # End-of-life tech
    eol_technologies = db.Column(db.Text)  # JSON array
    has_critical_vulnerabilities = db.Column(db.Boolean, default=False)
    critical_vulnerability_count = db.Column(db.Integer)
    compliance_gaps = db.Column(db.Text)  # JSON array

    # ==== DIMENSION 2: BUSINESS VALUE (0 - 100) ====
    business_value_score = db.Column(db.Integer)  # Aggregate score

    # Business value components
    strategic_alignment_score = db.Column(db.Integer)  # Supports strategic goals?
    business_criticality_score = db.Column(db.Integer)  # How critical to business?
    user_satisfaction_score = db.Column(db.Integer)  # NPS, CSAT
    functionality_coverage_score = db.Column(db.Integer)  # Meets user needs?
    innovation_potential_score = db.Column(db.Integer)  # Can enable new capabilities?

    # Usage metrics
    active_user_count = db.Column(db.Integer)
    user_growth_trend = db.Column(db.String(20))  # increasing, stable, declining
    transaction_volume = db.Column(db.Integer)
    business_processes_supported = db.Column(db.Integer)  # Count
    capabilities_supported = db.Column(db.Integer)  # Count

    # ==== DIMENSION 3: COST EFFICIENCY (0 - 100) ====
    cost_efficiency_score = db.Column(db.Integer)  # Aggregate score

    # Cost components (annual)
    total_cost_of_ownership = db.Column(db.Numeric(15, 2))
    cost_per_user = db.Column(db.Numeric(10, 2))
    cost_per_transaction = db.Column(db.Numeric(8, 4))
    license_cost_annual = db.Column(db.Numeric(15, 2))
    infrastructure_cost_annual = db.Column(db.Numeric(15, 2))
    maintenance_cost_annual = db.Column(db.Numeric(15, 2))
    support_cost_annual = db.Column(db.Numeric(15, 2))

    # Cost trends
    cost_trend = db.Column(db.String(20))  # increasing, stable, decreasing
    budget_variance_percentage = db.Column(db.Float)

    # Value delivered
    business_value_generated = db.Column(db.Numeric(15, 2))  # Revenue or savings
    roi_percentage = db.Column(db.Float)

    # ==== DIMENSION 4: VENDOR RISK (0 - 100) ====
    vendor_risk_score = db.Column(db.Integer)  # 0 = high risk, 100 = low risk

    # Vendor risk factors
    vendor_lock_in_level = db.Column(db.Integer)  # 1 - 10 scale
    vendor_viability_score = db.Column(db.Integer)  # Vendor financial health
    vendor_support_quality_score = db.Column(db.Integer)
    exit_complexity = db.Column(db.String(20))  # simple, moderate, complex, infeasible
    exit_cost_estimate = db.Column(db.Numeric(15, 2))
    alternative_vendors_available = db.Column(db.Integer)  # Count
    contract_flexibility_score = db.Column(db.Integer)  # Easy to exit contract?

    # ==== OVERALL RATIONALIZATION ASSESSMENT ====
    overall_health_score = db.Column(db.Integer, nullable=False)  # Weighted average 0 - 100

    # R1-B34 (TB-0135): the FormulaRegister version this score was computed
    # with, so a reader can trace the number to the exact reviewed weights
    # rather than trust an unversioned constant. Nullable -- a score
    # computed before this column existed, or with no formula registered
    # for this organisation yet, carries no version rather than a fabricated
    # one.
    formula_version = db.Column(db.Integer, nullable=True)

    # TIME framework recommendation (legacy scoring output — do NOT remove)
    rationalization_action = db.Column(db.String(20), nullable=False, index=True)
    # TOLERATE, INVEST, MIGRATE, ELIMINATE

    # Canonical disposition taxonomy (7Rs) — derived from rationalization_action via
    # TIME_TO_DISPOSITION mapping, may be overridden by the scoring service when
    # sibling overlap or dependency patterns make CONSOLIDATE more appropriate than RETIRE.
    # Coexists with rationalization_action for backwards compatibility.
    disposition_action = db.Column(db.String(50), nullable=True, index=True)
    # retain, rehost, replatform, refactor, replace, retire, consolidate

    # Confidence level for the disposition recommendation (derived from score evidence).
    # "high"   — multiple strong signals align (>2 score dimensions corroborate)
    # "medium" — primary signal clear, secondary signals mixed
    # "low"    — scores close to thresholds, limited structured data
    disposition_confidence = db.Column(db.String(20), nullable=True)

    # Structured list of reasons explaining why confidence is not "high".
    # Populated by RationalizationScoringService._derive_disposition() alongside
    # disposition_confidence.  Empty list when confidence is "high".
    # Example entries:
    #   "Score near threshold boundary"
    #   "No cost data for TCO analysis"
    #   "Limited vendor data available"
    #   "No dependency data available"
    #   "Missing lifecycle status"
    confidence_reasons = db.Column(db.JSON, nullable=True)

    action_rationale = db.Column(db.Text)  # Why this recommendation?

    # Priority scoring
    priority = db.Column(db.String(20), index=True)  # critical, high, medium, low
    priority_score = db.Column(db.Integer)  # Numeric score for sorting (higher = more urgent)

    # Financial impact
    estimated_annual_savings = db.Column(db.Numeric(15, 2))  # If ELIMINATE or MIGRATE
    estimated_investment_needed = db.Column(db.Numeric(15, 2))  # If INVEST or MIGRATE
    estimated_roi_months = db.Column(db.Integer)  # Payback period

    # Recommended actions
    recommended_next_steps = db.Column(db.Text)  # JSON array of action items
    quick_wins = db.Column(db.Text)  # JSON array of easy improvements

    # Review and approval (legacy fields — kept for backward compatibility)
    reviewed_date = db.Column(db.Date)

    # RAT-111: Explicit review workflow — state machine fields
    # Valid states: draft, reviewed, approved, rejected, exception_approved
    review_status = db.Column(db.String(30), nullable=False, default="draft", index=True)
    reviewed_by = db.Column(db.String(100), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    review_notes = db.Column(db.Text, nullable=True)
    approved_by = db.Column(db.String(100), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    # legacy alias — kept so existing queries that read approval_status don't break
    approval_status = db.Column(db.String(30))  # pending, approved, rejected, under_review

    # RAT-112: ARB governance linkage
    # Governed dispositions (retire, replace, consolidate) require ARB approval
    # before the review workflow can transition to "approved".
    arb_required = db.Column(db.Boolean, default=False, nullable=False)
    arb_submission_id = db.Column(db.Integer, nullable=True)  # References ARBReviewItem.id conceptually
    arb_submission_status = db.Column(db.String(30), nullable=True)  # pending, submitted, approved, rejected, deferred
    arb_submitted_at = db.Column(db.DateTime, nullable=True)
    arb_submitted_by = db.Column(db.String(100), nullable=True)
    arb_decision = db.Column(db.String(50), nullable=True)  # approved, approved_with_conditions, rejected, deferred
    arb_decision_at = db.Column(db.DateTime, nullable=True)
    arb_decision_notes = db.Column(db.Text, nullable=True)

    # RAT-113: Override mechanism — controlled manual override of system recommendations
    # Overrides must have explicit rationale and time-bounded expiry.
    override_active = db.Column(db.Boolean, default=False, nullable=False)
    override_disposition = db.Column(db.String(50), nullable=True)  # The overridden disposition action
    override_rationale = db.Column(db.Text, nullable=True)
    override_actor = db.Column(db.String(100), nullable=True)  # Who set the override
    override_created_at = db.Column(db.DateTime, nullable=True)
    override_expiry = db.Column(db.DateTime, nullable=True)  # When the override expires
    override_original_disposition = db.Column(db.String(50), nullable=True)  # The system recommendation being overridden

    # ==== DATA READINESS ====
    # Per-dimension completeness: {"owner": true, "lifecycle": true, "cost": false, ...}
    readiness_dimensions = db.Column(db.JSON, nullable=True)
    # Fraction of all dimensions satisfied (0.0 – 1.0)
    readiness_score = db.Column(db.Float, nullable=True)
    # True only when every HIGH-severity dimension is populated
    is_decision_ready = db.Column(db.Boolean, default=False, nullable=False)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    scoring_model_version = db.Column(db.String(20), default="1.0")
    ai_generated = db.Column(db.Boolean, default=False)
    ai_model_used = db.Column(db.String(100))
    notes = db.Column(db.Text)

    # Policy overlay — links this score to the RationalizationPolicy that was active
    # when the score was computed.  Nullable so existing rows without a policy remain
    # valid; populated by RationalizationScoringService.resolve_policy().
    policy_id = db.Column(
        db.Integer, db.ForeignKey("rationalization_policies.id"), nullable=True, index=True
    )
    policy_name = db.Column(db.String(100), nullable=True)

    # Relationships
    application = db.relationship("ApplicationComponent", backref="rationalization_score")
    policy = db.relationship("RationalizationPolicy", back_populates="scores")

    def __repr__(self):
        return f"<ApplicationRationalizationScore App:{self.application_component_id} Action:{self.rationalization_action} Score:{self.overall_health_score}>"

    @property
    def requires_arb(self) -> bool:
        """Whether this recommendation's disposition requires ARB governance.

        RAT-112: Governed dispositions (retire, replace, consolidate) must have
        ARB approval before the review workflow can advance to "approved".
        """
        return (self.disposition_action or "").lower() in self.GOVERNED_DISPOSITIONS

    @property
    def readiness_confidence_label(self) -> str:
        """
        Human-readable confidence label derived from readiness_score.

        Returns one of: 'high', 'medium', 'low', 'unknown'.
        'high' and 'medium' require is_decision_ready to also be True for
        a score to be treated as fully reliable.
        """
        if self.readiness_score is None:
            return "unknown"
        if self.readiness_score >= 0.875:
            return "high"
        if self.readiness_score >= 0.625:
            return "medium"
        return "low"

    @property
    def effective_disposition(self):
        """Return the currently effective disposition, accounting for active overrides."""
        if getattr(self, "override_active", None) and getattr(self, "override_expiry", None):  # model-safety-ok
            from datetime import datetime
            if datetime.utcnow() < self.override_expiry:
                return self.override_disposition
        return self.disposition_action

    def calculate_overall_score(self):
        """
        Calculate weighted overall health score.

        Weights (must sum to 100):
        - Technical Health: 30%
        - Business Value: 35%
        - Cost Efficiency: 25%
        - Vendor Risk: 10%
        """
        if any(
            score is None
            for score in [
                self.technical_health_score,
                self.business_value_score,
                self.cost_efficiency_score,
                self.vendor_risk_score,
            ]
        ):
            return None

        weighted = (
            self.technical_health_score * 0.30
            + self.business_value_score * 0.35
            + self.cost_efficiency_score * 0.25
            + self.vendor_risk_score * 0.10
        )

        return int(round(weighted))

    def determine_time_action(self):
        """
        Determine TIME framework action based on scores.

        .. deprecated::
            Use ``RationalizationScoringService._determine_time_action()`` instead.
            The service method includes dependency-aware blocking logic and
            strategic importance overrides not available here.

        This model method is kept aligned for offline/standalone use but the
        service method should be preferred for production scoring.

        Logic (aligned with service thresholds):
        - ELIMINATE: Overall < 40 OR (Low business value AND High cost)
        - MIGRATE: Technical health < 40 AND Business value > 50
        - INVEST: Business value > 70 AND Technical health > 50
        - TOLERATE: Everything else (good enough, low priority)
        """
        logging.getLogger(__name__).debug(
            "RationalizationScore.determine_time_action() called — "
            "prefer RationalizationScoringService._determine_time_action() "
            "for dependency-aware scoring"
        )
        if self.overall_health_score is None:
            return "TOLERATE"
        if self.overall_health_score < 40:
            return "ELIMINATE"

        if (
            self.business_value_score
            and self.business_value_score < 40
            and self.cost_efficiency_score
            and self.cost_efficiency_score < 40
        ):
            return "ELIMINATE"

        if (
            self.technical_health_score
            and self.technical_health_score < 40
            and self.business_value_score
            and self.business_value_score > 50
        ):
            return "MIGRATE"

        if (
            self.business_value_score
            and self.business_value_score > 70
            and self.technical_health_score
            and self.technical_health_score > 50
        ):
            return "INVEST"

        return "TOLERATE"


# ============================================================================
# RATIONALIZATION POLICY - Scoped threshold + weight overlays
# ============================================================================


class RationalizationPolicy(db.Model):
    """
    Scoped policy overlay for rationalization scoring.

    Allows portfolio owners and business unit leads to customise the thresholds
    and mandatory checks used by RationalizationScoringService without forking
    the core scoring logic.  The service resolves the most specific matching
    policy for each application via scope_filter before scoring begins.

    Exactly one policy should have ``is_default = True``; this is the fallback
    when no scope_filter matches the application being scored.

    ``thresholds`` keys:
        eliminate_below      — overall score below which an app is ELIMINATE
        invest_above         — business-value score above which an app is INVEST
        migrate_range        — [tech_threshold, business_threshold] for MIGRATE
        invest_tech_min      — minimum technical score for INVEST classification

    ``dimension_weights`` keys (values are integer percentages, must sum to 100):
        technical_health, business_value, cost_efficiency, vendor_risk

    ``mandatory_checks`` values (list of str):
        "owner_assigned"    — ApplicationComponent.application_owner is non-empty
        "cost_data_present" — at least one cost field is non-zero
        "risk_assessed"     — technical_risk or business_risk is set
        "lifecycle_set"     — lifecycle_status is set

    ``scope_filter`` keys (dict — ALL specified keys must match for the policy
    to be selected for a given application):
        business_unit  — matched against ApplicationComponent.business_unit
        portfolio      — matched against ApplicationComponent.portfolio (if present)
        lifecycle      — matched against ApplicationComponent.lifecycle_status
    """

    __tablename__ = "rationalization_policies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)

    # Exactly one policy should be the fallback when no scope_filter matches.
    is_default = db.Column(db.Boolean, default=False, nullable=False, index=True)

    # Override map for TIME-framework thresholds used by the scoring service.
    # Keys not present in this dict fall back to ScoringConfiguration defaults.
    # Example: {"eliminate_below": 30, "invest_above": 70,
    #           "migrate_range": [30, 55], "invest_tech_min": 45}
    thresholds = db.Column(db.JSON, nullable=True)

    # Percentage weights for the four scoring dimensions (integers summing to 100).
    # When absent the active ScoringConfiguration weights are used unchanged.
    # Example: {"technical_health": 30, "business_value": 25,
    #           "cost_efficiency": 25, "vendor_risk": 20}
    dimension_weights = db.Column(db.JSON, nullable=True)

    # List of named checks that must pass before a recommendation can be finalised.
    # Supported tokens: "owner_assigned", "cost_data_present",
    #                   "risk_assessed", "lifecycle_set"
    # Example: ["owner_assigned", "cost_data_present"]
    mandatory_checks = db.Column(db.JSON, nullable=True)

    # Dict of application attribute criteria for automatic policy resolution.
    # All specified keys must match for the policy to be selected.
    # Example: {"business_unit": "Finance"} or {"portfolio": "Legacy"}
    scope_filter = db.Column(db.JSON, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Back-reference to scores that were computed under this policy.
    scores = db.relationship(
        "ApplicationRationalizationScore", back_populates="policy", lazy="dynamic"
    )

    def __repr__(self):
        return (
            f"<RationalizationPolicy id={self.id} name={self.name!r} "
            f"is_default={self.is_default}>"
        )

    def get_effective_weights(self, base_config: "ScoringConfiguration") -> dict:
        """
        Return a weights dict (keys: technical_health, business_value,
        cost_efficiency, vendor_risk; values: 0.0–1.0) by overlaying this
        policy's dimension_weights on top of the base ScoringConfiguration.

        Args:
            base_config: Active ScoringConfiguration providing fallback weights.

        Returns:
            Dict with float weight values summing to 1.0.
        """
        base = base_config.get_weights_dict()
        if not self.dimension_weights:
            return base
        overlay = {}
        for key in ("technical_health", "business_value", "cost_efficiency", "vendor_risk"):
            raw = self.dimension_weights.get(key)
            if raw is not None:
                overlay[key] = raw / 100.0
            else:
                overlay[key] = base[key]
        return overlay

    def get_effective_thresholds(self, base_config: "ScoringConfiguration") -> dict:
        """
        Return a thresholds dict by overlaying this policy's thresholds on top
        of the base ScoringConfiguration column values.

        Args:
            base_config: Active ScoringConfiguration providing fallback thresholds.

        Returns:
            Dict with keys: eliminate_below, invest_above, invest_tech_min,
            migrate_tech_threshold, migrate_business_threshold.
        """
        base = {
            "eliminate_below": base_config.eliminate_threshold,
            "invest_above": base_config.invest_business_threshold,
            "invest_tech_min": base_config.invest_technical_threshold,
            "migrate_tech_threshold": base_config.migrate_technical_threshold,
            "migrate_business_threshold": base_config.migrate_business_threshold,
        }
        if not self.thresholds:
            return base
        result = dict(base)
        if "eliminate_below" in self.thresholds:
            result["eliminate_below"] = self.thresholds["eliminate_below"]
        if "invest_above" in self.thresholds:
            result["invest_above"] = self.thresholds["invest_above"]
        if "invest_tech_min" in self.thresholds:
            result["invest_tech_min"] = self.thresholds["invest_tech_min"]
        if "migrate_range" in self.thresholds:
            rng = self.thresholds["migrate_range"]
            if isinstance(rng, (list, tuple)) and len(rng) == 2:
                result["migrate_tech_threshold"] = rng[0]
                result["migrate_business_threshold"] = rng[1]
        return result

    def evaluate_mandatory_checks(self, app: object) -> dict:
        """
        Evaluate mandatory pre-finalisation checks against an application.

        Args:
            app: ApplicationComponent instance to evaluate.

        Returns:
            Dict with keys:
                passed       – bool, True only when all mandatory checks pass
                failed       – list of str, names of checks that did not pass
                results      – dict mapping check name to bool
        """
        checks = self.mandatory_checks or []
        results = {}
        for check in checks:
            if check == "owner_assigned":
                val = getattr(app, "application_owner", None)  # model-safety-ok
                results[check] = bool(val and str(val).strip())
            elif check == "cost_data_present":
                cost_fields = [
                    getattr(app, "total_cost_of_ownership", None),  # model-safety-ok
                    getattr(app, "license_cost", None),  # model-safety-ok
                    getattr(app, "maintenance_cost", None),  # model-safety-ok
                    getattr(app, "infrastructure_cost", None),  # model-safety-ok
                ]
                results[check] = any(
                    v is not None and float(v) > 0 for v in cost_fields
                )
            elif check == "risk_assessed":
                tech_risk = getattr(app, "technical_risk", None)  # model-safety-ok
                biz_risk = getattr(app, "business_risk", None)  # model-safety-ok
                results[check] = bool(
                    (tech_risk and str(tech_risk).strip())
                    or (biz_risk and str(biz_risk).strip())
                )
            elif check == "lifecycle_set":
                lc = getattr(app, "lifecycle_status", None)  # model-safety-ok
                results[check] = bool(lc and str(lc).strip())
            else:
                # Unknown check token — treat as passing so unknown tokens
                # do not silently block finalisation.
                results[check] = True

        failed = [name for name, ok in results.items() if not ok]
        return {
            "passed": len(failed) == 0,
            "failed": failed,
            "results": results,
        }

    def to_dict(self) -> dict:
        """Serialize this policy to a JSON-safe dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "is_default": self.is_default,
            "thresholds": self.thresholds,
            "dimension_weights": self.dimension_weights,
            "mandatory_checks": self.mandatory_checks,
            "scope_filter": self.scope_filter,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================================
# VENDOR CONCENTRATION ANALYSIS - Portfolio Risk
# ============================================================================


class VendorConcentrationAnalysis(db.Model):
    """
    Portfolio-level analysis of vendor concentration and risk.

    Tracks how many applications, capabilities, and budget depend on each vendor.
    High concentration = high risk if vendor fails or relationship deteriorates.

    Enables:
    - "Blast radius" analysis: What happens if Vendor X fails?
    - Vendor diversification planning
    - Contract negotiation leverage assessment
    - Alternative vendor identification
    """

    __tablename__ = "vendor_concentration_analysis"

    id = db.Column(db.Integer, primary_key=True)

    # Core relationship
    vendor_organization_id = db.Column(
        db.Integer,
        db.ForeignKey("vendor_organizations.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Assessment period
    assessment_date = db.Column(db.Date, nullable=False, default=date.today)

    # Concentration metrics
    application_count = db.Column(db.Integer, default=0)  # How many apps use this vendor
    application_ids = db.Column(db.Text)  # JSON array of app IDs
    critical_application_count = db.Column(db.Integer, default=0)  # Mission-critical apps

    capability_count = db.Column(db.Integer, default=0)  # How many capabilities depend on vendor
    capability_ids = db.Column(db.Text)  # JSON array
    single_vendor_capability_count = db.Column(
        db.Integer, default=0
    )  # Capabilities with ONLY this vendor

    business_process_count = db.Column(db.Integer, default=0)  # How many processes affected
    affected_user_count = db.Column(db.Integer)  # Total users across all apps
    affected_departments = db.Column(db.Text)  # JSON array

    # Financial concentration
    annual_spend_total = db.Column(db.Numeric(15, 2))  # Total vendor spend
    contract_count = db.Column(db.Integer, default=0)
    contract_value_total = db.Column(db.Numeric(15, 2))
    percentage_of_it_budget = db.Column(db.Float)  # % of total IT budget

    # Risk assessment
    concentration_risk_score = db.Column(
        db.Integer
    )  # 0 - 100 (0=very high risk, 100=well distributed)

    # Concentration risk factors
    is_single_point_of_failure = db.Column(
        db.Boolean, default=False
    )  # Critical dependencies with no alternatives
    has_strategic_lock_in = db.Column(db.Boolean, default=False)  # Difficult/expensive to switch
    contract_exit_complexity = db.Column(db.String(20))  # simple, moderate, complex, infeasible

    # Exit planning
    estimated_switching_cost = db.Column(db.Numeric(15, 2))  # Cost to move away from vendor
    estimated_switching_duration_months = db.Column(db.Integer)
    data_portability_score = db.Column(db.Integer)  # 0 - 100 (how easy to export data)

    # Alternative vendors
    alternative_vendor_count = db.Column(db.Integer, default=0)
    alternative_vendors = db.Column(db.Text)  # JSON array of {vendor_id, name, coverage_percentage}
    multi_vendor_strategy_viable = db.Column(db.Boolean, default=False)

    # Mitigation strategies
    risk_mitigation_plan = db.Column(db.Text)
    diversification_plan = db.Column(db.Text)
    contingency_plan = db.Column(db.Text)
    backup_vendor_identified = db.Column(db.Boolean, default=False)

    # Contract leverage
    negotiation_leverage = db.Column(db.String(20))  # high, medium, low
    renewal_risk = db.Column(db.String(20))  # Must renew or business stops
    competitive_pressure = db.Column(db.String(20))  # Can we credibly threaten to switch?

    # Vendor health indicators
    vendor_financial_health = db.Column(db.String(20))  # strong, stable, concerning, at_risk
    vendor_market_position = db.Column(db.String(20))  # leader, challenger, niche, declining
    vendor_innovation_score = db.Column(db.Integer)  # 0 - 100
    vendor_support_quality_score = db.Column(db.Integer)  # 0 - 100

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    analyzed_by = db.Column(db.String(200))
    notes = db.Column(db.Text)

    # Relationships
    vendor = db.relationship("VendorOrganization", backref="concentration_analysis")

    def __repr__(self):
        return f"<VendorConcentrationAnalysis Vendor:{self.vendor_organization_id} Apps:{self.application_count} Risk:{self.concentration_risk_score}>"

    def calculate_concentration_risk(self):
        """
        Calculate concentration risk score (0 - 100, lower = higher risk).

        Factors:
        - High app count = higher risk
        - High budget percentage = higher risk
        - No alternatives = higher risk
        - Single point of failure = maximum risk
        """
        if self.is_single_point_of_failure:
            return 10  # Maximum risk

        score = 100

        # Deduct points for high concentration
        if self.application_count and self.application_count > 10:
            score -= 30
        elif self.application_count and self.application_count > 5:
            score -= 20

        if self.percentage_of_it_budget and self.percentage_of_it_budget > 20:
            score -= 25
        elif self.percentage_of_it_budget and self.percentage_of_it_budget > 10:
            score -= 15

        if self.alternative_vendor_count == 0:
            score -= 30
        elif self.alternative_vendor_count == 1:
            score -= 15

        if self.single_vendor_capability_count and self.single_vendor_capability_count > 3:
            score -= 15

        return max(0, score)


# ============================================================================
# REPLACEMENT PLAN - Disposition-linked migration planning record
# ============================================================================


class ReplacementPlan(db.Model):
    """
    Replacement/consolidation planning record linked to a rationalization decision.

    Created when an application's disposition is Replace or Consolidate.
    Captures the target replacement application (or TBD intent), migration phase,
    cost estimate, risk level, rollout strategy, and supporting notes — giving
    the portfolio team a single governed record per source application.

    One record per source application (unique on source_app_id).  If the target
    is not yet identified the target_app_id may be null and target_app_name used
    as a placeholder until the formal picker selection is made.
    """

    __tablename__ = "replacement_plans"

    id = db.Column(db.Integer, primary_key=True)

    # The application being replaced or consolidated (FK to application_components)
    source_app_id = db.Column(
        db.Integer,
        db.ForeignKey("application_components.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    # The intended replacement target — nullable when TBD
    target_app_id = db.Column(
        db.Integer,
        db.ForeignKey("application_components.id"),
        nullable=True,
        index=True,
    )
    # Cached display name — populated from the picker, avoids extra join in list views
    target_app_name = db.Column(db.String(200), nullable=True)

    # Where the migration is in its lifecycle
    # Values: planning | pilot | migration | cutover | decommission
    migration_phase = db.Column(db.String(50), nullable=False, default="planning")

    # Financial forecast
    estimated_cost = db.Column(db.Float, nullable=True)
    estimated_duration_months = db.Column(db.Integer, nullable=True)

    # Risk classification: low | medium | high | critical
    risk_level = db.Column(db.String(20), nullable=False, default="medium")

    # Deployment approach: big_bang | phased | parallel_run | pilot_first
    rollout_strategy = db.Column(db.String(50), nullable=False, default="phased")

    notes = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.String(100), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    source_app = db.relationship(
        "ApplicationComponent",
        foreign_keys=[source_app_id],
        backref=db.backref("replacement_plan", uselist=False),
    )
    target_app = db.relationship(
        "ApplicationComponent",
        foreign_keys=[target_app_id],
        backref="targeted_as_replacement",
    )

    __table_args__ = (
        db.Index("idx_replacement_plans_risk_level", "risk_level"),
        db.Index("idx_replacement_plans_migration_phase", "migration_phase"),
    )

    def __repr__(self):
        return (
            f"<ReplacementPlan source={self.source_app_id} "
            f"target={self.target_app_id} phase={self.migration_phase}>"
        )

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dictionary for API responses."""
        return {
            "id": self.id,
            "source_app_id": self.source_app_id,
            "source_app_name": self.source_app.name if self.source_app else None,
            "target_app_id": self.target_app_id,
            "target_app_name": self.target_app_name,
            "migration_phase": self.migration_phase,
            "estimated_cost": self.estimated_cost,
            "estimated_duration_months": self.estimated_duration_months,
            "risk_level": self.risk_level,
            "rollout_strategy": self.rollout_strategy,
            "notes": self.notes,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================================
# SCORING CONFIGURATION - Configurable Weights for Business Units
# ============================================================================


class ScoringConfiguration(db.Model):
    """
    Configurable scoring weights for application rationalization.

    Enables business units to customize the importance of each
    scoring dimension based on their specific priorities and mission needs.
    Falls back to default federal CIO weights if no custom config exists.

    Default weights (from CIO.gov Playbook baseline):
    - Technical Health: 30%
    - Business Value: 35%
    - Cost Efficiency: 25%
    - Vendor Risk: 10%

    Example custom configurations:
    - R&D Division: Higher technical weight (40%), lower cost weight (15%)
    - Operations: Higher business value weight (45%), higher vendor risk (15%)
    - Finance: Higher cost efficiency weight (35%), lower technical (20%)
    """

    __tablename__ = "scoring_configurations"

    id = db.Column(db.Integer, primary_key=True)

    # Configuration scope
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)

    # Scope type: global, division, department, business_unit, custom
    scope_type = db.Column(db.String(30), nullable=False, default="business_unit")

    # For non-global scopes, the specific entity identifier
    scope_entity_id = db.Column(db.Integer, nullable=True)
    scope_entity_type = db.Column(db.String(50), nullable=True)
    # e.g., "BusinessUnit", "Department", "Division"

    # ==== WEIGHT CONFIGURATION (must sum to 100) ====
    # Default: 30, 35, 25, 10 (CIO.gov federal baseline)
    technical_health_weight = db.Column(db.Integer, nullable=False, default=30)
    business_value_weight = db.Column(db.Integer, nullable=False, default=35)
    cost_efficiency_weight = db.Column(db.Integer, nullable=False, default=25)
    vendor_risk_weight = db.Column(db.Integer, nullable=False, default=10)

    # ==== THRESHOLD CONFIGURATION ====
    # Score thresholds for TIME framework decisions (0-100 scale)
    eliminate_threshold = db.Column(db.Integer, nullable=False, default=40)
    migrate_technical_threshold = db.Column(db.Integer, nullable=False, default=40)
    migrate_business_threshold = db.Column(db.Integer, nullable=False, default=50)
    invest_business_threshold = db.Column(db.Integer, nullable=False, default=70)
    invest_technical_threshold = db.Column(db.Integer, nullable=False, default=50)
    tolerate_min_threshold = db.Column(db.Integer, nullable=False, default=40)

    # ==== ENRICHMENT FACTORS ====
    # Multipliers for strategic importance overrides
    strategic_critical_boost = db.Column(db.Integer, nullable=False, default=10)
    strategic_high_boost = db.Column(db.Integer, nullable=False, default=5)

    # Dependency impact settings
    critical_dependency_penalty = db.Column(db.Integer, nullable=False, default=20)
    high_dependency_penalty = db.Column(db.Integer, nullable=False, default=10)

    # Status
    is_active = db.Column(db.Boolean, default=True, index=True)
    is_default = db.Column(db.Boolean, default=False, index=True)

    # Governance
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_by = db.Column(db.String(200))
    approval_date = db.Column(db.Date)
    review_cycle_months = db.Column(db.Integer, default=12)
    next_review_date = db.Column(db.Date)

    # Metadata
    notes = db.Column(db.Text)
    configuration_version = db.Column(db.Integer, default=1)

    # Relationships
    created_by = db.relationship("User", backref="scoring_configs_created")

    def __repr__(self):
        return f"<ScoringConfiguration {self.name} ({self.scope_type}) Tech:{self.technical_health_weight}% Bus:{self.business_value_weight}% Cost:{self.cost_efficiency_weight}% Vend:{self.vendor_risk_weight}%"

    def validate_weights(self):
        """
        Validate that weights sum to 100.

        Returns:
            Tuple (is_valid: bool, error_message: str)
        """
        total = (
            self.technical_health_weight
            + self.business_value_weight
            + self.cost_efficiency_weight
            + self.vendor_risk_weight
        )
        if total != 100:
            return False, f"Weights must sum to 100, got {total}"
        return True, "Valid"

    def get_weights_dict(self):
        """
        Get weights as dictionary for use in scoring calculations.

        Returns:
            Dictionary with weight keys and decimal values (0.0-1.0)
        """
        return {
            "technical_health": self.technical_health_weight / 100.0,
            "business_value": self.business_value_weight / 100.0,
            "cost_efficiency": self.cost_efficiency_weight / 100.0,
            "vendor_risk": self.vendor_risk_weight / 100.0,
        }

    def calculate_overall_score(
        self, technical_score, business_score, cost_score, vendor_score
    ):
        """
        Calculate weighted overall score using this configuration.

        Args:
            technical_score: Technical health score (0-100)
            business_score: Business value score (0-100)
            cost_score: Cost efficiency score (0-100)
            vendor_score: Vendor risk score (0-100)

        Returns:
            Weighted overall score (0-100)
        """
        weights = self.get_weights_dict()
        overall = (
            technical_score * weights["technical_health"]
            + business_score * weights["business_value"]
            + cost_score * weights["cost_efficiency"]
            + vendor_score * weights["vendor_risk"]
        )
        return round(overall, 2)

    def determine_time_action(
        self, overall_score, technical_score, business_score, cost_score, app=None
    ):
        """
        Determine TIME framework action using this configuration's thresholds.

        Args:
            overall_score: Overall weighted score
            technical_score: Technical health score
            business_score: Business value score
            cost_score: Cost efficiency score
            app: Optional ApplicationComponent for lifecycle status check

        Returns:
            TIME action string: TOLERATE, INVEST, MIGRATE, or ELIMINATE
        """
        # ELIMINATE: Low overall score or lifecycle status
        if app and app.lifecycle_status in ["deprecated", "retired"]:
            return "ELIMINATE"

        if overall_score < self.eliminate_threshold:
            return "ELIMINATE"

        # INVEST: High business value with decent technical foundation
        if (
            business_score > self.invest_business_threshold
            and technical_score > self.invest_technical_threshold
        ):
            return "INVEST"

        # MIGRATE: Technical debt but business value justifies replacement
        if (
            technical_score < self.migrate_technical_threshold
            and business_score > self.migrate_business_threshold
        ):
            return "MIGRATE"

        # Strategic importance override
        if app and hasattr(app, "strategic_importance"):
            if app.strategic_importance == "critical":
                return "INVEST"
            elif app.strategic_importance == "high":
                # High strategic apps that would be TOLERATE become INVEST
                if overall_score >= self.tolerate_min_threshold:
                    return "INVEST"

        # TOLERATE: Everything else
        return "TOLERATE"

    def to_dict(self):
        """Serialize configuration to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "scope_type": self.scope_type,
            "scope_entity_id": self.scope_entity_id,
            "scope_entity_type": self.scope_entity_type,
            "weights": self.get_weights_dict(),
            "technical_health_weight": self.technical_health_weight,
            "business_value_weight": self.business_value_weight,
            "cost_efficiency_weight": self.cost_efficiency_weight,
            "vendor_risk_weight": self.vendor_risk_weight,
            "thresholds": {
                "eliminate": self.eliminate_threshold,
                "migrate_technical": self.migrate_technical_threshold,
                "migrate_business": self.migrate_business_threshold,
                "invest_business": self.invest_business_threshold,
                "invest_technical": self.invest_technical_threshold,
                "tolerate_min": self.tolerate_min_threshold,
            },
            "enrichment": {
                "strategic_critical_boost": self.strategic_critical_boost,
                "strategic_high_boost": self.strategic_high_boost,
                "critical_dependency_penalty": self.critical_dependency_penalty,
                "high_dependency_penalty": self.high_dependency_penalty,
            },
            "is_active": self.is_active,
            "is_default": self.is_default,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "configuration_version": self.configuration_version,
        }


# ============================================================================
# RAT-114: IMMUTABLE AUDIT TRAIL
# ============================================================================


class RationalizationAuditEntry(db.Model):
    """Immutable audit trail for rationalization decisions.

    RAT-114: Records every state change, approval, override, evidence update,
    and scoring event with actor, action, before/after state, and timestamp.
    """

    __tablename__ = "rationalization_audit_entries"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(
        db.Integer,
        db.ForeignKey("application_components.id"),
        nullable=False,
        index=True,
    )
    score_id = db.Column(
        db.Integer,
        db.ForeignKey("application_rationalization_scores.id"),
        nullable=True,
        index=True,
    )

    # What happened
    action = db.Column(db.String(50), nullable=False, index=True)
    # Actions: score_created, score_updated, review_transition, arb_submitted, arb_decided,
    #          override_created, override_removed, evidence_updated, policy_applied

    # Who did it
    actor = db.Column(db.String(100), nullable=False)
    actor_type = db.Column(db.String(20), nullable=False, default="user")  # user, system, api

    # Before/after state (JSON)
    before_state = db.Column(db.JSON, nullable=True)
    after_state = db.Column(db.JSON, nullable=True)

    # Additional context
    details = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    # Set when this row has been copied into the one audit store
    # (soc2_audit_log); points at the copy. NULL until copied.
    retired_into_id = db.Column(db.BigInteger, db.ForeignKey("soc2_audit_log.id"), nullable=True)

    # Relationships
    application = db.relationship("ApplicationComponent", backref="rationalization_audit_entries")
    score = db.relationship("ApplicationRationalizationScore", backref="audit_entries")

    __table_args__ = (
        db.Index("idx_rat_audit_app_action", "application_id", "action"),
    )

    def __repr__(self):
        return f"<RationalizationAuditEntry {self.action} app:{self.application_id} by:{self.actor}>"


# ============================================================================
# RAT-116: DECOMMISSION PLAN — Structured workflow for retire/replace
# ============================================================================


class DecommissionPlan(db.Model):
    """RAT-116: Structured decommission plan for retire/replace recommendations.

    Requires explicit planning for migration, cutover, validation, rollback,
    and closure criteria before a recommendation proceeds to execution.
    """

    __tablename__ = "decommission_plans"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )
    score_id = db.Column(
        db.Integer, db.ForeignKey("application_rationalization_scores.id"), nullable=True, index=True
    )

    # Plan metadata
    plan_status = db.Column(db.String(30), default="draft", nullable=False)
    # draft, reviewed, approved, in_progress, completed
    plan_owner = db.Column(db.String(100), nullable=True)

    # Migration plan
    migration_approach = db.Column(db.String(50), nullable=True)
    # big_bang, phased, parallel_run, pilot
    migration_steps = db.Column(db.JSON, nullable=True)  # List of step descriptions
    data_migration_plan = db.Column(db.Text, nullable=True)

    # Cutover plan
    cutover_date = db.Column(db.Date, nullable=True)
    cutover_steps = db.Column(db.JSON, nullable=True)
    downtime_window = db.Column(db.String(100), nullable=True)  # e.g. "4 hours"

    # Validation criteria
    validation_criteria = db.Column(db.JSON, nullable=True)  # List of acceptance criteria
    smoke_test_plan = db.Column(db.Text, nullable=True)

    # Rollback plan
    rollback_steps = db.Column(db.JSON, nullable=True)
    rollback_trigger = db.Column(db.Text, nullable=True)  # What triggers a rollback
    rollback_window = db.Column(db.String(100), nullable=True)  # e.g. "48 hours post-cutover"

    # Closure criteria
    closure_criteria = db.Column(db.JSON, nullable=True)  # When is decommission "done"?
    data_retention_period = db.Column(db.String(100), nullable=True)  # e.g. "7 years per policy"

    # Stakeholder communication
    communication_plan = db.Column(db.Text, nullable=True)
    affected_teams = db.Column(db.JSON, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.String(100), nullable=True)

    # Relationships
    application = db.relationship("ApplicationComponent", backref="decommission_plans")
    score = db.relationship("ApplicationRationalizationScore", backref="decommission_plan")

    def __repr__(self):
        return f"<DecommissionPlan app:{self.application_id} status:{self.plan_status}>"


class RationalizationBenefitsTracker(db.Model):
    """RAT-117: Track projected vs realized benefits for rationalization decisions."""

    __tablename__ = "rationalization_benefits"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(
        db.Integer,
        db.ForeignKey("application_components.id"),
        nullable=False,
        index=True,
    )
    score_id = db.Column(
        db.Integer,
        db.ForeignKey("application_rationalization_scores.id"),
        nullable=True,
        index=True,
    )

    # Projected benefits (set when the recommendation is approved)
    projected_annual_savings = db.Column(db.Numeric(15, 2), nullable=True)
    projected_risk_reduction = db.Column(db.String(20), nullable=True)  # high, medium, low
    projected_simplification_score = db.Column(db.Integer, nullable=True)  # 0-100
    projected_timeline_months = db.Column(db.Integer, nullable=True)

    # Actual realized benefits (updated as outcomes are measured)
    actual_annual_savings = db.Column(db.Numeric(15, 2), nullable=True)
    actual_risk_reduction = db.Column(db.String(20), nullable=True)
    actual_simplification_score = db.Column(db.Integer, nullable=True)
    actual_timeline_months = db.Column(db.Integer, nullable=True)

    # Status tracking
    tracking_status = db.Column(db.String(30), default="projected", nullable=False)
    # projected, in_progress, measured, validated

    # Measurement metadata
    measurement_date = db.Column(db.Date, nullable=True)
    measured_by = db.Column(db.String(100), nullable=True)
    measurement_notes = db.Column(db.Text, nullable=True)

    # Variance analysis
    savings_variance_pct = db.Column(db.Float, nullable=True)  # (actual-projected)/projected * 100

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    application = db.relationship("ApplicationComponent", backref="rationalization_benefits")
    score = db.relationship("ApplicationRationalizationScore", backref="benefits_tracker")

    def __repr__(self):
        return f"<RationalizationBenefitsTracker app:{self.application_id} status:{self.tracking_status}>"

    def calculate_variance(self):
        """Calculate savings variance if both values exist."""
        if (
            self.actual_annual_savings is not None
            and self.projected_annual_savings
            and float(self.projected_annual_savings) != 0
        ):
            self.savings_variance_pct = float(
                (float(self.actual_annual_savings) - float(self.projected_annual_savings))
                / float(self.projected_annual_savings)
                * 100
            )
