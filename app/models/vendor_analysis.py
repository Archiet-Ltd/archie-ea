"""
Vendor Options Analysis Models

This module implements comprehensive vendor analysis and comparison for Enterprise architects:
1. Multi-vendor capability analysis
2. Multi-criteria decision analysis (MCDA)
3. AI-powered recommendations
4. TCO and ROI calculations
5. Risk assessment and mitigation

Integration with existing models:
- Links to BusinessCapability (business_capabilities.py)
- Links to TechnologyStack (models.py)
- Links to User (user.py)
"""

import json
from datetime import datetime

from .. import db
from app.models.mixins import TenantMixin


class OptionsAnalysis(TenantMixin, db.Model):
    """
    Main analysis session for comparing vendor options.

    Manages the complete lifecycle of a vendor comparison analysis including:
    - Capability requirements definition
    - Vendor selection
    - Scoring criteria configuration
    - Analysis execution and results
    """

    __tablename__ = "options_analysis"

    id = db.Column(db.Integer, primary_key=True)

    # Analysis identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)

    # Capability being analyzed
    capability_id = db.Column(db.Integer, db.ForeignKey("business_capability.id"), nullable=False)
    capability = db.relationship("BusinessCapability", backref="vendor_analyses")

    # Analysis ownership
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_by = db.relationship("User", foreign_keys=[created_by_id], backref="created_analyses")

    # Analysis status
    status = db.Column(
        db.String(30), default="draft"
    )  # draft, running, completed, approved, rejected

    # Criteria configuration (JSON)
    # Structure: {"cost": 0.3, "capability_coverage": 0.25, "risk": 0.2, "strategic_fit": 0.15, "implementation": 0.1}
    criteria_weights = db.Column(db.Text)  # JSON object with scoring weights

    # Analysis parameters
    analysis_type = db.Column(
        db.String(50), default="comprehensive"
    )  # quick, standard, comprehensive
    analysis_mode = db.Column(
        db.String(30), default="single_product"
    )  # single_product, vendor_suite, multi_capability
    tco_years = db.Column(db.Integer, default=5)  # TCO calculation period
    include_ai_recommendation = db.Column(db.Boolean, default=True)

    # Multi-capability analysis support (for suite-level comparisons)
    additional_capability_ids = db.Column(
        db.Text
    )  # JSON array of capability IDs for suite analysis

    # Analysis execution tracking
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    execution_duration_seconds = db.Column(db.Integer)

    # AI/LLM tracking
    llm_tokens_used = db.Column(db.Integer, default=0)
    llm_cost = db.Column(db.Numeric(10, 4), default=0.0)

    # Results summary
    total_vendors_analyzed = db.Column(db.Integer, default=0)
    recommended_vendor_id = db.Column(db.Integer, db.ForeignKey("technology_stacks.id"))
    recommended_vendor = db.relationship("TechnologyStack", foreign_keys=[recommended_vendor_id])
    recommendation_confidence = db.Column(db.Float)  # 0.0 - 1.0

    # Budget and cost
    estimated_cost_range_min = db.Column(db.Numeric(12, 2))  # From analysis results
    estimated_cost_range_max = db.Column(db.Numeric(12, 2))
    budget_constraint = db.Column(db.Numeric(12, 2))  # Optional budget limit

    # Approval workflow
    approval_status = db.Column(db.String(30))  # pending_approval, approved, rejected
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])
    approved_at = db.Column(db.DateTime)
    approval_notes = db.Column(db.Text)

    # === ENHANCED CONTEXT FIELDS ===
    # Organization and deployment context
    organization_size = db.Column(db.String(30))  # smb, midmarket, enterprise
    industry_vertical = db.Column(
        db.String(100)
    )  # healthcare, financial, retail, manufacturing, etc.
    deployment_scale = db.Column(db.String(30))  # pilot, department, division, enterprise_wide
    user_count_estimate = db.Column(db.Integer)  # Expected number of users
    integration_complexity = db.Column(db.String(30))  # low, medium, high, very_high

    # Decision urgency and timeline
    decision_deadline = db.Column(db.Date)  # When decision must be made
    implementation_target_date = db.Column(db.Date)  # Target go-live date
    is_urgent = db.Column(db.Boolean, default=False)

    # Stakeholder collaboration
    requires_multi_stakeholder_approval = db.Column(db.Boolean, default=False)
    stakeholder_consensus_threshold = db.Column(db.Float, default=0.75)  # % agreement needed

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    vendor_options = db.relationship(
        "VendorOption",
        back_populates="analysis",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="VendorOption.ranking",
    )
    recommendations = db.relationship(
        "AnalysisRecommendation", back_populates="analysis", cascade="all, delete-orphan"
    )
    stakeholder_inputs = db.relationship(
        "StakeholderInput", back_populates="analysis", cascade="all, delete-orphan"
    )
    scenarios = db.relationship(
        "AnalysisScenario", back_populates="analysis", cascade="all, delete-orphan"
    )
    required_capabilities = db.relationship(
        "RequiredCapability", back_populates="analysis", cascade="all, delete-orphan"
    )
    audit_logs = db.relationship(
        "AnalysisAuditLog", back_populates="analysis", cascade="all, delete-orphan"
    )

    def get_criteria_weights(self):
        """Parse criteria_weights JSON into dict."""
        if self.criteria_weights:
            return json.loads(self.criteria_weights)
        # Default weights
        return {
            "cost": 0.25,
            "capability_coverage": 0.25,
            "risk": 0.20,
            "strategic_fit": 0.15,
            "implementation": 0.15,
        }

    def set_criteria_weights(self, weights_dict):
        """Set criteria weights from dict."""
        # Validate weights sum to 1.0
        total = sum(weights_dict.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Criteria weights must sum to 1.0, got {total}")
        self.criteria_weights = json.dumps(weights_dict)

    def calculate_completion_percentage(self):
        """Calculate analysis completion percentage based on actual data."""
        if self.status == "completed":
            return 100

        total_vendors = len(self.vendor_options)
        if total_vendors == 0:
            has_name = bool(self.name)
            bool(self.criteria_weights)
            return 10 if has_name else 0

        completed_vendors = len(
            [v for v in self.vendor_options if v.analysis_status == "completed"]
        )
        return int((completed_vendors / total_vendors) * 100)

    def get_winner(self):
        """Get the highest-scoring completed vendor option."""
        scored = [
            v for v in self.vendor_options
            if v.total_score and v.analysis_status == "completed"
        ]
        if not scored:
            return None
        return max(scored, key=lambda v: v.total_score or 0)

    def __repr__(self):
        return f"<OptionsAnalysis {self.id}: {self.name} ({self.status})>"


class VendorOption(db.Model):
    """
    Individual vendor being evaluated in an analysis.

    Stores comprehensive scoring across multiple dimensions:
    - Cost analysis (TCO, licensing, support)
    - Capability coverage and gap analysis
    - Risk assessment (lock-in, market, support)
    - Strategic fit and alignment
    - Implementation complexity and timeline
    """

    __tablename__ = "vendor_options"

    id = db.Column(db.Integer, primary_key=True)

    # Analysis relationship
    analysis_id = db.Column(
        db.Integer, db.ForeignKey("options_analysis.id"), nullable=False, index=True
    )
    analysis = db.relationship("OptionsAnalysis", back_populates="vendor_options")

    # Vendor/Technology Stack
    technology_stack_id = db.Column(
        db.Integer, db.ForeignKey("technology_stacks.id"), nullable=True
    )
    technology_stack = db.relationship("TechnologyStack", backref="vendor_evaluations")

    # Vendor Organization (NEW)
    vendor_organization_id = db.Column(
        db.Integer, db.ForeignKey("vendor_organizations.id"), nullable=True
    )
    vendor_organization = db.relationship("VendorOrganization", backref="vendor_evaluations")

    # Vendor Product (NEW)
    vendor_product_id = db.Column(db.Integer, db.ForeignKey("vendor_products.id"), nullable=True)
    vendor_product = db.relationship("VendorProduct", backref="vendor_evaluations")

    # Vendor type for classification
    vendor_type = db.Column(
        db.String(50), default="technology_stack"
    )  # organization, product, technology_stack

    # Analysis execution
    analysis_status = db.Column(
        db.String(30), default="pending"
    )  # pending, analyzing, completed, failed
    analysis_started_at = db.Column(db.DateTime)
    analysis_completed_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text)

    # === SCORING DIMENSIONS (0 - 100 each) ===

    # 1. Cost Score (lower cost = higher score)
    cost_score = db.Column(db.Float, default=0.0)
    tco_total = db.Column(db.Numeric(12, 2))  # Total Cost of Ownership
    tco_breakdown = db.Column(db.Text)  # JSON: {year1: {license: X, support: Y, infra: Z}, ...}
    license_cost_annual = db.Column(db.Numeric(12, 2))
    support_cost_annual = db.Column(db.Numeric(12, 2))
    infrastructure_cost_monthly = db.Column(db.Numeric(12, 2))
    training_cost_estimate = db.Column(db.Numeric(12, 2))

    # 2. Capability Coverage Score (gap analysis)
    capability_coverage_score = db.Column(db.Float, default=0.0)
    capability_match_percentage = db.Column(db.Float)  # 0 - 100%
    capability_gaps = db.Column(db.Text)  # JSON: [{gap: "X", severity: "high", workaround: "Y"}]
    supported_capabilities = db.Column(db.Text)  # JSON: ["cap1", "cap2", ...]
    missing_capabilities = db.Column(db.Text)  # JSON: ["cap3", "cap4", ...]

    # 3. Risk Score (lower risk = higher score)
    risk_score = db.Column(db.Float, default=0.0)
    vendor_lock_in_risk = db.Column(db.Integer)  # 1 - 10 (10 = high lock-in)
    market_position_risk = db.Column(db.Integer)  # 1 - 10 (10 = risky market position)
    support_continuity_risk = db.Column(db.Integer)  # 1 - 10 (10 = high risk)
    technology_maturity_risk = db.Column(db.Integer)  # 1 - 10 (10 = immature/risky)
    compliance_risk = db.Column(db.Integer)  # 1 - 10 (10 = high compliance risk)
    risk_mitigation_strategies = db.Column(db.Text)  # JSON: [{risk: "X", mitigation: "Y"}]

    # 4. Strategic Fit Score
    strategic_fit_score = db.Column(db.Float, default=0.0)
    technology_alignment = db.Column(db.Integer)  # 1 - 10 (10 = perfect alignment)
    roadmap_alignment = db.Column(db.Integer)  # 1 - 10
    vendor_relationship = db.Column(db.Integer)  # 1 - 10 (existing relationship bonus)
    future_proofing = db.Column(db.Integer)  # 1 - 10 (innovation, future capabilities)
    ecosystem_fit = db.Column(db.Integer)  # 1 - 10 (integration with existing tools)

    # 5. Implementation Score
    implementation_score = db.Column(db.Float, default=0.0)
    implementation_complexity = db.Column(db.Integer)  # 1 - 10 (1 = easy, 10 = very complex)
    integration_difficulty = db.Column(db.Integer)  # 1 - 10 (10 = very difficult)
    data_migration_risk = db.Column(db.Integer)  # 1 - 10 (10 = very risky)
    change_management_impact = db.Column(db.Integer)  # 1 - 10 (10 = high impact)
    training_requirements = db.Column(db.Integer)  # 1 - 10 (10 = extensive training)
    estimated_implementation_weeks = db.Column(db.Integer)
    resource_requirements = db.Column(
        db.Text
    )  # JSON: {developers: X, architects: Y, specialists: Z}
    required_skills = db.Column(db.Text)  # JSON: ["skill1", "skill2", ...]
    skill_availability = db.Column(db.Integer)  # 1 - 10 (10 = skills readily available)

    # === AGGREGATE SCORING ===
    total_score = db.Column(
        db.Float, default=0.0, index=True
    )  # Weighted sum of all dimension scores
    ranking = db.Column(db.Integer)  # 1, 2, 3, ... (calculated after all vendors scored)

    # === ADDITIONAL EVALUATION DATA ===

    # Technical characteristics (from TechnologyStackAnalyzer)
    scalability_rating = db.Column(db.Integer)  # 1 - 10
    security_rating = db.Column(db.Integer)  # 1 - 10
    performance_rating = db.Column(db.Integer)  # 1 - 10
    reliability_rating = db.Column(db.Integer)  # 1 - 10

    # Vendor information
    vendor_name = db.Column(db.String(256))
    vendor_market_share = db.Column(db.Float)  # Percentage
    vendor_year_founded = db.Column(db.Integer)
    vendor_employee_count = db.Column(db.Integer)
    vendor_health_score = db.Column(db.Integer)  # 1 - 100 (financial stability)

    # Certifications and compliance
    certifications = db.Column(db.Text)  # JSON: ["ISO27001", "SOC2", "GDPR"]
    compliance_frameworks = db.Column(db.Text)  # JSON: ["HIPAA", "PCI-DSS"]

    # AI analysis metadata
    ai_research_completed = db.Column(db.Boolean, default=False)
    ai_research_sources = db.Column(db.Text)  # JSON: [{url: "...", title: "...", confidence: 0.9}]
    ai_confidence = db.Column(db.Float)  # 0.0 - 1.0

    # Free-form analysis notes
    pros = db.Column(db.Text)  # JSON: ["pro1", "pro2", ...]
    cons = db.Column(db.Text)  # JSON: ["con1", "con2", ...]
    analyst_notes = db.Column(db.Text)

    # === EXTERNAL INTELLIGENCE & VALIDATION ===
    # Market intelligence (from G2, Gartner, etc.)
    g2_rating = db.Column(db.Float)  # 1.0 - 5.0
    g2_review_count = db.Column(db.Integer)
    g2_satisfaction_score = db.Column(db.Integer)  # 0 - 100
    gartner_quadrant = db.Column(db.String(30))  # leader, challenger, niche_player, visionary
    forrester_wave_score = db.Column(db.Float)  # 0.0 - 5.0

    # Customer validation
    customer_reference_count = db.Column(db.Integer)  # Number of verified references
    verified_implementations = db.Column(db.Integer)  # Proven deployments in similar contexts
    customer_retention_rate = db.Column(db.Float)  # Percentage (e.g., 95.0 for 95%)
    nps_score = db.Column(db.Integer)  # Net Promoter Score (-100 to +100)

    # Enhanced cost details
    data_migration_cost_estimate = db.Column(db.Numeric(12, 2))
    integration_development_cost = db.Column(db.Numeric(12, 2))
    change_management_cost = db.Column(db.Numeric(12, 2))
    exit_cost_estimate = db.Column(db.Numeric(12, 2))  # Cost to leave vendor
    hidden_costs_identified = db.Column(db.Text)  # JSON: [{cost_type, amount, description}]

    # Technical validation
    api_portability_score = db.Column(db.Integer)  # 1 - 10 (10 = fully portable, open standards)
    data_export_capability = db.Column(db.Integer)  # 1 - 10 (10 = full export capability)
    vendor_lock_in_score_calculated = db.Column(
        db.Integer
    )  # 1 - 10 from actual analysis, not keywords

    # Financial health (from credit agencies, financial reports)
    vendor_credit_rating = db.Column(db.String(10))  # e.g., "A+", "BBB"
    funding_status = db.Column(db.String(50))  # bootstrapped, series_a, series_b, public, acquired
    months_of_runway = db.Column(db.Integer)  # For startups: months of cash runway
    annual_revenue = db.Column(db.Numeric(15, 2))
    revenue_growth_rate = db.Column(db.Float)  # YoY percentage

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    proof_points = db.relationship(
        "VendorProofPoint", back_populates="vendor_option", cascade="all, delete-orphan"
    )

    def calculate_total_score(self, weights):
        """
        Calculate weighted total score based on criteria weights.

        Args:
            weights: Dict with keys: cost, capability_coverage, risk, strategic_fit, implementation

        Returns:
            float: Total weighted score (0 - 100)
        """
        score = 0.0
        score += (self.cost_score or 0.0) * weights.get("cost", 0.25)
        score += (self.capability_coverage_score or 0.0) * weights.get("capability_coverage", 0.25)
        score += (self.risk_score or 0.0) * weights.get("risk", 0.20)
        score += (self.strategic_fit_score or 0.0) * weights.get("strategic_fit", 0.15)
        score += (self.implementation_score or 0.0) * weights.get("implementation", 0.15)

        self.total_score = round(score, 2)
        return self.total_score

    def get_tco_breakdown(self):
        """Parse TCO breakdown JSON into dict."""
        if self.tco_breakdown:
            return json.loads(self.tco_breakdown)
        return {}

    def get_capability_gaps(self):
        """Parse capability gaps JSON into list."""
        if self.capability_gaps:
            return json.loads(self.capability_gaps)
        return []

    def get_pros_cons(self):
        """Get pros and cons as lists."""
        pros = json.loads(self.pros) if self.pros else []
        cons = json.loads(self.cons) if self.cons else []
        return pros, cons

    def __repr__(self):
        return f"<VendorOption {self.id}: {self.vendor_name} - Score: {self.total_score}>"


class VendorComparisonCriteria(db.Model):
    """
    Configurable decision criteria for vendor comparisons.

    Allows organizations to define and customize their vendor selection criteria
    beyond the default five dimensions.
    """

    __tablename__ = "vendor_comparison_criteria"

    id = db.Column(db.Integer, primary_key=True)

    # Criteria definition
    name = db.Column(db.String(100), nullable=False, index=True)
    description = db.Column(db.Text)
    category = db.Column(db.String(50))  # cost, capability, risk, strategic, implementation, custom

    # Scoring configuration
    weight = db.Column(db.Float, default=0.1)  # Default weight in scoring
    min_value = db.Column(db.Float, default=0.0)
    max_value = db.Column(db.Float, default=100.0)
    higher_is_better = db.Column(db.Boolean, default=True)

    # Calculation method
    calculation_method = db.Column(db.String(50))  # manual, formula, ai, external_api
    formula = db.Column(db.Text)  # Python expression for calculated criteria

    # Organizational configuration
    is_mandatory = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    organization_specific = db.Column(db.Boolean, default=False)

    # Metadata
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_by = db.relationship("User", backref="created_criteria")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<VendorComparisonCriteria {self.name}: weight={self.weight}>"


class AnalysisRecommendation(db.Model):
    """
    AI-generated recommendation for an options analysis.

    Stores the AI's reasoning, confidence, and alternative suggestions
    to provide transparent, explainable vendor selection recommendations.
    """

    __tablename__ = "analysis_recommendations"

    id = db.Column(db.Integer, primary_key=True)

    # Analysis relationship
    analysis_id = db.Column(
        db.Integer, db.ForeignKey("options_analysis.id"), nullable=False, index=True
    )
    analysis = db.relationship("OptionsAnalysis", back_populates="recommendations")

    # Recommended vendor
    recommended_vendor_option_id = db.Column(
        db.Integer, db.ForeignKey("vendor_options.id"), nullable=False
    )
    recommended_vendor = db.relationship(
        "VendorOption", foreign_keys=[recommended_vendor_option_id]
    )

    # Alternative options
    second_choice_id = db.Column(db.Integer, db.ForeignKey("vendor_options.id"))
    second_choice = db.relationship("VendorOption", foreign_keys=[second_choice_id])
    third_choice_id = db.Column(db.Integer, db.ForeignKey("vendor_options.id"))
    third_choice = db.relationship("VendorOption", foreign_keys=[third_choice_id])

    # Recommendation details
    rationale = db.Column(db.Text, nullable=False)  # Why this vendor was recommended
    confidence_score = db.Column(db.Float)  # 0.0 - 1.0
    confidence_explanation = db.Column(db.Text)  # Why this confidence level

    # Decision factors
    key_strengths = db.Column(db.Text)  # JSON: ["strength1", "strength2", ...]
    key_concerns = db.Column(db.Text)  # JSON: ["concern1", "concern2", ...]
    decision_factors = db.Column(db.Text)  # JSON: {factor: importance_score}

    # Risk and mitigation
    identified_risks = db.Column(
        db.Text
    )  # JSON: [{risk: "X", severity: "high", probability: "medium"}]
    mitigation_recommendations = db.Column(db.Text)  # JSON: [{risk: "X", mitigation: "Y", cost: Z}]

    # Implementation guidance
    implementation_roadmap = db.Column(db.Text)  # JSON: [{phase: 1, tasks: [...], duration: "4w"}]
    estimated_timeline_weeks = db.Column(db.Integer)
    estimated_total_cost = db.Column(db.Numeric(12, 2))
    roi_estimate = db.Column(db.Text)  # JSON: {year1: -100k, year2: 50k, year3: 200k}
    payback_period_months = db.Column(db.Integer)

    # Alternative scenarios
    alternative_scenarios = db.Column(
        db.Text
    )  # JSON: [{scenario: "Budget 20% lower", recommendation: "Vendor B"}]

    # AI generation metadata
    llm_model_used = db.Column(db.String(50))  # "gpt - 4", "claude - 3 - opus"
    llm_tokens_used = db.Column(db.Integer)
    llm_cost = db.Column(db.Numeric(10, 4))
    prompt_version = db.Column(db.String(20))  # Track prompt engineering versions

    # Human review
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    reviewed_at = db.Column(db.DateTime)
    review_notes = db.Column(db.Text)
    human_agrees = db.Column(db.Boolean)  # Did human reviewer agree with AI?

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def get_key_strengths(self):
        """Parse key strengths JSON into list."""
        if self.key_strengths:
            return json.loads(self.key_strengths)
        return []

    def get_key_concerns(self):
        """Parse key concerns JSON into list."""
        if self.key_concerns:
            return json.loads(self.key_concerns)
        return []

    def get_implementation_roadmap(self):
        """Parse implementation roadmap JSON into list."""
        if self.implementation_roadmap:
            return json.loads(self.implementation_roadmap)
        return []

    def get_roi_estimate(self):
        """Parse ROI estimate JSON into dict."""
        if self.roi_estimate:
            return json.loads(self.roi_estimate)
        return {}

    def __repr__(self):
        return f"<AnalysisRecommendation {self.id}: {self.recommended_vendor.vendor_name if self.recommended_vendor else 'None'} (confidence: {self.confidence_score})>"


# === ENHANCED MODELS FOR IMPROVED VENDOR ANALYSIS ===


class VendorProofPoint(db.Model):
    """
    Evidence-based validation of vendor capability claims.

    Tracks proof points for vendor claims to ensure data quality and validation.
    Prevents "garbage in, garbage out" by requiring evidence for capabilities.
    """

    __tablename__ = "vendor_proof_points"

    id = db.Column(db.Integer, primary_key=True)

    # Relationship to vendor option
    vendor_option_id = db.Column(db.Integer, db.ForeignKey("vendor_options.id"), nullable=False)
    vendor_option = db.relationship("VendorOption", back_populates="proof_points")

    # Capability claim being validated
    capability_claim = db.Column(db.String(256), nullable=False)
    claim_category = db.Column(
        db.String(50)
    )  # feature, performance, security, compliance, integration

    # Proof type and evidence
    proof_type = db.Column(
        db.String(50), nullable=False
    )  # documentation, demo, poc, reference, certification, benchmark
    evidence_url = db.Column(db.String(512))
    evidence_description = db.Column(db.Text)
    evidence_file_path = db.Column(db.String(512))  # For uploaded documents

    # Validation
    verified_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    verified_by = db.relationship("User")
    verified_at = db.Column(db.DateTime)
    verification_confidence = db.Column(db.Float)  # 0.0 - 1.0
    verification_notes = db.Column(db.Text)

    # Status
    status = db.Column(
        db.String(30), default="pending"
    )  # pending, verified, rejected, needs_clarification

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<VendorProofPoint {self.id}: {self.capability_claim} ({self.proof_type})>"


class StakeholderInput(TenantMixin, db.Model):
    """
    Multi-stakeholder input for collaborative vendor selection.

    Enables IT, Finance, Business, Legal, and Security stakeholders
    to provide independent scoring and feedback, supporting consensus-based decisions.
    """

    __tablename__ = "stakeholder_inputs"

    id = db.Column(db.Integer, primary_key=True)

    # Analysis relationship
    analysis_id = db.Column(
        db.Integer, db.ForeignKey("options_analysis.id"), nullable=False, index=True
    )
    analysis = db.relationship("OptionsAnalysis", back_populates="stakeholder_inputs")

    # Stakeholder identity
    stakeholder_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    stakeholder = db.relationship("User", foreign_keys=[stakeholder_id])
    stakeholder_role = db.Column(
        db.String(50), nullable=False
    )  # IT, Finance, Business, Legal, Security, Executive
    department = db.Column(db.String(100))

    # Custom criteria weights from this stakeholder's perspective
    custom_weights = db.Column(db.Text)  # JSON: {cost: 0.4, capability: 0.2, ...}

    # Vendor scoring from this stakeholder (JSON: {vendor_id: {scores, notes}})
    vendor_scores = db.Column(
        db.Text
    )  # JSON: {vendor_id: {cost: 80, capability: 90, notes: "..."}}

    # Overall feedback
    preferred_vendor_id = db.Column(db.Integer, db.ForeignKey("vendor_options.id"))
    preferred_vendor = db.relationship("VendorOption", foreign_keys=[preferred_vendor_id])
    comments = db.Column(db.Text)
    concerns = db.Column(db.Text)  # JSON: ["concern1", "concern2"]
    additional_requirements = db.Column(db.Text)  # JSON: ["req1", "req2"]

    # Participation tracking
    invitation_sent_at = db.Column(db.DateTime)
    invitation_accepted_at = db.Column(db.DateTime)
    submitted_at = db.Column(db.DateTime)
    is_complete = db.Column(db.Boolean, default=False)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def get_custom_weights(self):
        """Parse custom weights JSON."""
        if self.custom_weights:
            return json.loads(self.custom_weights)
        return None

    def get_vendor_scores(self):
        """Parse vendor scores JSON."""
        if self.vendor_scores:
            return json.loads(self.vendor_scores)
        return {}

    def __repr__(self):
        return (
            f"<StakeholderInput {self.id}: {self.stakeholder_role} for Analysis {self.analysis_id}>"
        )


class AnalysisScenario(db.Model):
    """
    What-if scenario analysis for vendor comparison.

    Enables comparison of different weighting strategies and constraints
    (e.g., "cost-optimized" vs. "best-of-breed" vs. "quick-win").
    """

    __tablename__ = "analysis_scenarios"

    id = db.Column(db.Integer, primary_key=True)

    # Analysis relationship
    analysis_id = db.Column(
        db.Integer, db.ForeignKey("options_analysis.id"), nullable=False, index=True
    )
    analysis = db.relationship("OptionsAnalysis", back_populates="scenarios")

    # Scenario definition
    scenario_name = db.Column(
        db.String(128), nullable=False
    )  # "Cost-optimized", "Best-of-breed", "Quick-win"
    description = db.Column(db.Text)
    is_baseline = db.Column(db.Boolean, default=False)  # One scenario is the baseline

    # Scenario parameters
    criteria_weights = db.Column(db.Text, nullable=False)  # JSON: Different weight allocation
    vendor_constraints = db.Column(
        db.Text
    )  # JSON: {budget_max: 100000, implementation_max_weeks: 12}
    excluded_vendor_ids = db.Column(db.Text)  # JSON: [vendor_ids] to exclude from this scenario

    # Results
    recommended_vendor_id = db.Column(db.Integer, db.ForeignKey("vendor_options.id"))
    recommended_vendor = db.relationship("VendorOption", foreign_keys=[recommended_vendor_id])
    scenario_winner_score = db.Column(db.Float)
    vendor_rankings = db.Column(db.Text)  # JSON: [{vendor_id, rank, score}]

    # Analysis
    trade_offs = db.Column(db.Text)  # JSON: What is gained/lost vs. baseline
    risk_delta = db.Column(db.Text)  # JSON: Risk changes vs. baseline
    cost_delta = db.Column(db.Numeric(12, 2))  # Cost difference vs. baseline

    # Metadata
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_by = db.relationship("User")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def get_criteria_weights(self):
        """Parse criteria weights JSON."""
        if self.criteria_weights:
            return json.loads(self.criteria_weights)
        return {}

    def get_vendor_rankings(self):
        """Parse vendor rankings JSON."""
        if self.vendor_rankings:
            return json.loads(self.vendor_rankings)
        return []

    def __repr__(self):
        return f"<AnalysisScenario {self.id}: {self.scenario_name}>"


class RequiredCapability(db.Model):
    """
    Structured capability requirements with importance weighting.

    Defines what capabilities are required, their criticality,
    and acceptance criteria for validation.
    """

    __tablename__ = "required_capabilities"

    id = db.Column(db.Integer, primary_key=True)

    # Analysis relationship
    analysis_id = db.Column(
        db.Integer, db.ForeignKey("options_analysis.id"), nullable=False, index=True
    )
    analysis = db.relationship("OptionsAnalysis", back_populates="required_capabilities")

    # Capability definition
    capability_name = db.Column(db.String(256), nullable=False)
    capability_description = db.Column(db.Text)
    category = db.Column(db.String(50))  # functional, technical, security, compliance, integration

    # Importance and criticality
    importance = db.Column(db.String(30), nullable=False)  # critical, high, medium, low
    must_have = db.Column(db.Boolean, default=False)  # If True, eliminates vendor if missing
    weight_multiplier = db.Column(
        db.Float, default=1.0
    )  # Multiplier for scoring (critical = 2.0, low = 0.5)

    # Validation criteria
    acceptance_criteria = db.Column(db.Text)  # How to verify this capability exists
    measurement_method = db.Column(db.String(50))  # demo, documentation, poc, reference, benchmark

    # Taxonomy mapping (for semantic matching)
    synonyms = db.Column(db.Text)  # JSON: ["synonym1", "synonym2"] for matching
    parent_capability_id = db.Column(
        db.Integer, db.ForeignKey("required_capabilities.id")
    )  # Hierarchical

    # Fulfillment tracking: JSON dict mapping vendor_option_id -> "met"|"partial"|"not_met"
    fulfillment_data = db.Column(db.Text)  # JSON: {"vendor_option_id": "met"|"partial"|"not_met"}

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_synonyms(self):
        """Parse synonyms JSON."""
        if self.synonyms:
            return json.loads(self.synonyms)
        return []

    def get_fulfillment(self):
        """Parse fulfillment_data JSON into dict."""
        if self.fulfillment_data:
            try:
                return json.loads(self.fulfillment_data)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    def set_fulfillment(self, vendor_option_id, status):
        """Set fulfillment status for a vendor option."""
        data = self.get_fulfillment()
        data[str(vendor_option_id)] = status
        self.fulfillment_data = json.dumps(data)

    def __repr__(self):
        return f"<RequiredCapability {self.id}: {self.capability_name} ({self.importance})>"


class AnalysisAuditLog(db.Model):
    """
    Comprehensive audit trail for vendor analysis decisions.

    Tracks all changes to analysis, scoring, and decisions
    to provide complete traceability and justification.
    """

    __tablename__ = "analysis_audit_logs"

    id = db.Column(db.Integer, primary_key=True)

    # Analysis relationship
    analysis_id = db.Column(
        db.Integer, db.ForeignKey("options_analysis.id"), nullable=False, index=True
    )
    analysis = db.relationship("OptionsAnalysis", back_populates="audit_logs")

    # Event details
    event_type = db.Column(db.String(50), nullable=False, index=True)
    # score_changed, vendor_added, vendor_removed, weights_updated,
    # recommendation_generated, stakeholder_input_added, scenario_created, etc.

    event_data = db.Column(db.Text)  # JSON: Detailed event data
    previous_value = db.Column(db.Text)  # JSON: Before state
    new_value = db.Column(db.Text)  # JSON: After state

    # Who and why
    performed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    performed_by = db.relationship("User")
    rationale = db.Column(db.Text)  # Why this change was made

    # Context
    vendor_option_id = db.Column(
        db.Integer, db.ForeignKey("vendor_options.id")
    )  # If event relates to specific vendor
    vendor_option = db.relationship("VendorOption")

    # Timestamp
    performed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def get_event_data(self):
        """Parse event data JSON."""
        if self.event_data:
            return json.loads(self.event_data)
        return {}

    def __repr__(self):
        return f"<AnalysisAuditLog {self.id}: {self.event_type} at {self.performed_at}>"


class RFPTemplate(db.Model):
    """
    Reusable RFP (Request for Proposal) templates for vendor questionnaires.

    Enables standardized vendor evaluation through structured questions.
    """

    __tablename__ = "rfp_templates"

    id = db.Column(db.Integer, primary_key=True)

    # Template identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)
    version = db.Column(db.String(20), default="1.0")

    # Capability targeting
    capability_id = db.Column(db.Integer, db.ForeignKey("business_capability.id"))
    capability = db.relationship("BusinessCapability")
    industry_vertical = db.Column(db.String(100))  # healthcare, financial, etc.

    # Template structure (JSON)
    # [{section: "Technical", questions: [{id, question, response_type, is_mandatory, scoring_weight}]}]
    sections = db.Column(db.Text, nullable=False)

    # Metadata
    is_active = db.Column(db.Boolean, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_by = db.relationship("User")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def get_sections(self):
        """Parse sections JSON."""
        if self.sections:
            return json.loads(self.sections)
        return []

    def __repr__(self):
        return f"<RFPTemplate {self.id}: {self.name} v{self.version}>"


class VendorResponse(db.Model):
    """
    Vendor responses to RFP questions.

    Captures structured vendor submissions for standardized evaluation.
    """

    __tablename__ = "vendor_responses"

    id = db.Column(db.Integer, primary_key=True)

    # Relationships
    vendor_option_id = db.Column(
        db.Integer, db.ForeignKey("vendor_options.id"), nullable=False, index=True
    )
    vendor_option = db.relationship("VendorOption", backref="rfp_responses")

    rfp_template_id = db.Column(db.Integer, db.ForeignKey("rfp_templates.id"), nullable=False)
    rfp_template = db.relationship("RFPTemplate")

    # Response details
    question_id = db.Column(db.String(50), nullable=False)  # Reference to question in template
    question_text = db.Column(db.Text)  # Snapshot of question
    response_text = db.Column(db.Text)
    response_score = db.Column(db.Float)  # Evaluated score for this response

    # Supporting materials
    attachments = db.Column(db.Text)  # JSON: [{filename, file_path, file_type}]

    # Submission tracking
    submitted_by = db.Column(db.String(128))  # Vendor contact name
    submitted_by_email = db.Column(db.String(256))
    submitted_at = db.Column(db.DateTime)

    # Evaluation
    evaluated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    evaluated_by = db.relationship("User")
    evaluation_notes = db.Column(db.Text)
    is_complete = db.Column(db.Boolean, default=False)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def get_attachments(self):
        """Parse attachments JSON."""
        if self.attachments:
            return json.loads(self.attachments)
        return []

    def __repr__(self):
        return f"<VendorResponse {self.id}: Question {self.question_id}>"


class TCOBenchmark(db.Model):
    """
    Industry TCO benchmarks for dynamic scoring ranges.

    Replaces hardcoded $50k-$500k ranges with context-aware benchmarks
    based on capability type, organization size, and industry.
    """

    __tablename__ = "tco_benchmarks"

    id = db.Column(db.Integer, primary_key=True)

    # Context
    capability_category = db.Column(
        db.String(100), nullable=False, index=True
    )  # CRM, ERP, BI, etc.
    organization_size = db.Column(db.String(30), nullable=False)  # smb, midmarket, enterprise
    industry_vertical = db.Column(db.String(100))  # Optional industry-specific
    deployment_scale = db.Column(db.String(30))  # pilot, department, enterprise_wide

    # TCO ranges (for 5 - year period, scaled in service)
    min_tco = db.Column(db.Numeric(12, 2), nullable=False)
    max_tco = db.Column(db.Numeric(12, 2), nullable=False)
    median_tco = db.Column(db.Numeric(12, 2))

    # Cost breakdown multipliers
    license_cost_multiplier = db.Column(
        db.Float, default=1.5
    )  # Implementation cost = license * multiplier
    data_migration_per_gb = db.Column(db.Numeric(10, 2))  # Cost per GB migrated
    integration_per_endpoint = db.Column(db.Numeric(10, 2))  # Cost per API integration
    training_per_user = db.Column(db.Numeric(10, 2))  # Cost per user trained

    # Source and confidence
    data_source = db.Column(db.String(256))  # Gartner, Forrester, industry survey, etc.
    sample_size = db.Column(db.Integer)  # Number of implementations in benchmark
    confidence_level = db.Column(db.Float)  # 0.0 - 1.0

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f"<TCOBenchmark {self.id}: {self.capability_category} ({self.organization_size})>"
