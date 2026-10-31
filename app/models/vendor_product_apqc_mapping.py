"""
Vendor Product to APQC Process Mapping Model

This module defines the relationship between vendor products and APQC processes,
enabling process-centric vendor analysis and capability coverage assessment.

Enhanced for strategic portfolio-level analysis including:
- Coverage and capability assessment
- Gap analysis and workarounds
- Competitive positioning
- Industry fit evaluation
- Integration complexity tracking
"""

from datetime import datetime

from app import db
from app.models.mixins import HybridTenantMixin


class VendorProductAPQCMapping(HybridTenantMixin, db.Model):
    """
    Junction table linking vendor products to APQC processes with comprehensive
    strategic analysis capabilities.

    Captures the relationship between vendor products and the APQC Process Classification
    Framework (PCF) processes they support, enabling:
    - Process-centric vendor selection
    - Gap analysis between vendor capabilities and process requirements
    - Coverage assessment across the enterprise process landscape
    - Strategic vendor comparison and benchmarking
    - Portfolio-level decision support
    """

    __tablename__ = "vendor_product_apqc_mapping"
    __tenant_parent__ = (("vendor_product_id", "vendor_products"),)

    id = db.Column(db.Integer, primary_key=True)

    # ===== CORE RELATIONSHIPS =====
    vendor_product_id = db.Column(
        db.Integer,
        db.ForeignKey("vendor_products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    apqc_process_id = db.Column(
        db.Integer, db.ForeignKey("apqc_process.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ===== RELEVANCE & SCORING =====
    relevance_score = db.Column(db.Integer, default=0)  # 0 - 100 overall relevance
    confidence_level = db.Column(db.String(20), default="medium")  # high, medium, low

    # ===== COVERAGE & CAPABILITY =====
    coverage_level = db.Column(
        db.String(20), default="partial"
    )  # full, substantial, partial, minimal, none
    coverage_percentage = db.Column(db.Integer, default=0)  # 0 - 100% of process steps covered
    automation_capability = db.Column(db.Integer, default=0)  # 0 - 100% automation potential
    out_of_box_fit = db.Column(db.Integer, default=0)  # 0 - 100% without customization

    # Process level support (which APQC levels does this cover?)
    supports_level_1 = db.Column(db.Boolean, default=True)  # Category level
    supports_level_2 = db.Column(db.Boolean, default=True)  # Process Group level
    supports_level_3 = db.Column(db.Boolean, default=False)  # Process level
    supports_level_4 = db.Column(db.Boolean, default=False)  # Activity level
    supports_level_5 = db.Column(db.Boolean, default=False)  # Task level

    # ===== CUSTOMIZATION & INTEGRATION =====
    requires_customization = db.Column(db.Boolean, default=False)
    customization_effort = db.Column(db.String(20))  # none, low, medium, high, very_high
    customization_scope = db.Column(db.Text)  # Description of required customizations

    integration_complexity = db.Column(
        db.String(20), default="medium"
    )  # low, medium, high, very_high
    integration_pattern = db.Column(
        db.String(30)
    )  # native, api, batch, event_driven, manual, hybrid
    integration_prerequisites = db.Column(db.JSON)  # List of prerequisites

    # ===== GAP ANALYSIS =====
    gaps = db.Column(db.JSON)  # Array of identified gaps
    missing_features = db.Column(db.JSON)  # Specific features not supported
    workarounds = db.Column(db.JSON)  # Possible workarounds for gaps
    limitations = db.Column(db.Text)  # Known limitations

    # ===== STRATEGIC ANALYSIS =====
    # Industry fit
    industry_fit = db.Column(db.JSON)  # Array: ['manufacturing', 'finance', 'healthcare', etc.]
    industry_specific_features = db.Column(db.JSON)  # Industry-specific capabilities

    # Market positioning
    market_position = db.Column(db.String(20))  # leader, challenger, visionary, niche_player
    vendor_strength = db.Column(db.String(20))  # dominant, strong, moderate, limited, emerging
    competitive_advantage = db.Column(db.Text)  # Key differentiators for this process
    competitive_weaknesses = db.Column(db.Text)  # Areas where competitors excel

    # Maturity
    product_maturity = db.Column(db.String(20))  # mature, established, growing, emerging, beta
    feature_roadmap_alignment = db.Column(db.Integer)  # 0 - 100 alignment with process needs

    # ===== IMPLEMENTATION METRICS =====
    typical_implementation_weeks = db.Column(db.Integer)  # Estimated implementation time
    implementation_risk = db.Column(db.String(20))  # low, medium, high, very_high
    change_management_impact = db.Column(
        db.String(20)
    )  # minimal, moderate, significant, transformational

    # Expected benefits
    expected_efficiency_gain = db.Column(db.Integer)  # 0 - 100% efficiency improvement
    expected_cost_reduction = db.Column(db.Integer)  # 0 - 100% cost reduction
    expected_quality_improvement = db.Column(db.Integer)  # 0 - 100% quality improvement
    expected_cycle_time_reduction = db.Column(db.Integer)  # 0 - 100% time reduction

    # ===== EVIDENCE & VALIDATION =====
    reference_customers = db.Column(db.JSON)  # Array of reference customer names
    case_study_urls = db.Column(db.JSON)  # Array of case study URLs
    vendor_documentation_url = db.Column(db.String(500))
    analyst_report_references = db.Column(db.JSON)  # Gartner, Forrester references

    # ===== ASSESSMENT METADATA =====
    mapping_source = db.Column(db.String(50), default="auto")  # auto, manual, ai, import, analyst
    mapping_rationale = db.Column(db.Text)  # Explanation of why this mapping exists
    assessed_by = db.Column(db.String(100))  # Who performed the assessment
    assessment_date = db.Column(db.DateTime)
    assessment_notes = db.Column(db.Text)
    last_validated = db.Column(db.DateTime)  # When was this last verified
    validation_status = db.Column(
        db.String(20), default="pending"
    )  # validated, pending, outdated, disputed

    # ===== TIMESTAMPS =====
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ===== RELATIONSHIPS =====
    vendor_product = db.relationship(
        "VendorProduct", backref=db.backref("apqc_mappings", lazy="dynamic")
    )
    apqc_process = db.relationship(
        "APQCProcess", backref=db.backref("vendor_product_mappings", lazy="dynamic")
    )

    # Unique constraint
    __table_args__ = (
        db.UniqueConstraint("vendor_product_id", "apqc_process_id", name="uq_vendor_product_apqc"),
        {"extend_existing": True},
    )

    def __repr__(self):
        return f"<VendorProductAPQCMapping {self.vendor_product_id} -> {self.apqc_process_id} (score: {self.relevance_score}, coverage: {self.coverage_level})>"

    def to_dict(self):
        """Convert mapping to dictionary for API responses."""
        return {
            "id": self.id,
            "vendor_product_id": self.vendor_product_id,
            "apqc_process_id": self.apqc_process_id,
            "vendor_product_name": self.vendor_product.name if self.vendor_product else None,
            "vendor_name": self.vendor_product.vendor_organization.name
            if self.vendor_product and self.vendor_product.vendor_organization
            else None,
            "apqc_process_code": self.apqc_process.process_code if self.apqc_process else None,
            "apqc_process_name": self.apqc_process.process_name if self.apqc_process else None,
            # Scoring
            "relevance_score": self.relevance_score,
            "confidence_level": self.confidence_level,
            # Coverage
            "coverage_level": self.coverage_level,
            "coverage_percentage": self.coverage_percentage,
            "automation_capability": self.automation_capability,
            "out_of_box_fit": self.out_of_box_fit,
            # Customization
            "requires_customization": self.requires_customization,
            "customization_effort": self.customization_effort,
            "integration_complexity": self.integration_complexity,
            "integration_pattern": self.integration_pattern,
            # Gaps
            "gaps": self.gaps,
            "missing_features": self.missing_features,
            "workarounds": self.workarounds,
            # Strategic
            "industry_fit": self.industry_fit,
            "market_position": self.market_position,
            "vendor_strength": self.vendor_strength,
            "competitive_advantage": self.competitive_advantage,
            # Implementation
            "typical_implementation_weeks": self.typical_implementation_weeks,
            "expected_efficiency_gain": self.expected_efficiency_gain,
            "expected_cost_reduction": self.expected_cost_reduction,
            # Evidence
            "reference_customers": self.reference_customers,
            "case_study_urls": self.case_study_urls,
            # Metadata
            "mapping_source": self.mapping_source,
            "mapping_rationale": self.mapping_rationale,
            "validation_status": self.validation_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_summary_dict(self):
        """Lightweight dictionary for list views."""
        return {
            "id": self.id,
            "vendor_product_name": self.vendor_product.name if self.vendor_product else None,
            "vendor_name": self.vendor_product.vendor_organization.name
            if self.vendor_product and self.vendor_product.vendor_organization
            else None,
            "apqc_process_code": self.apqc_process.process_code if self.apqc_process else None,
            "apqc_process_name": self.apqc_process.process_name if self.apqc_process else None,
            "relevance_score": self.relevance_score,
            "coverage_level": self.coverage_level,
            "coverage_percentage": self.coverage_percentage,
            "out_of_box_fit": self.out_of_box_fit,
            "market_position": self.market_position,
        }

    def calculate_strategic_fit(self):
        """
        Calculate overall strategic fit score based on multiple factors.
        Returns a score from 0 - 100.
        """
        weights = {
            "relevance": 0.20,
            "coverage": 0.20,
            "automation": 0.15,
            "out_of_box": 0.15,
            "implementation_risk": 0.10,
            "market_position": 0.10,
            "expected_benefits": 0.10,
        }

        score = 0

        # Relevance score (already 0 - 100)
        score += (self.relevance_score or 0) * weights["relevance"]

        # Coverage percentage (0 - 100)
        score += (self.coverage_percentage or 0) * weights["coverage"]

        # Automation capability (0 - 100)
        score += (self.automation_capability or 0) * weights["automation"]

        # Out of box fit (0 - 100)
        score += (self.out_of_box_fit or 0) * weights["out_of_box"]

        # Implementation risk (inverse - low risk = high score)
        risk_scores = {"low": 100, "medium": 70, "high": 40, "very_high": 10}
        risk_score = risk_scores.get(self.implementation_risk, 50)
        score += risk_score * weights["implementation_risk"]

        # Market position
        position_scores = {"leader": 100, "challenger": 80, "visionary": 70, "niche_player": 50}
        position_score = position_scores.get(self.market_position, 50)
        score += position_score * weights["market_position"]

        # Expected benefits (average of all benefit metrics)
        benefits = [
            self.expected_efficiency_gain or 0,
            self.expected_cost_reduction or 0,
            self.expected_quality_improvement or 0,
            self.expected_cycle_time_reduction or 0,
        ]
        avg_benefits = sum(benefits) / len(benefits) if benefits else 0
        score += avg_benefits * weights["expected_benefits"]

        return round(score, 1)

    def get_gap_summary(self):
        """Get a summary of gaps and their impact."""
        gaps = self.gaps or []
        missing = self.missing_features or []
        workarounds = self.workarounds or []

        return {
            "total_gaps": len(gaps),
            "total_missing_features": len(missing),
            "workarounds_available": len(workarounds),
            "gaps_with_workarounds": min(len(gaps), len(workarounds)),
            "unmitigated_gaps": max(0, len(gaps) - len(workarounds)),
            "gap_severity": "critical"
            if len(gaps) > 5
            else "high"
            if len(gaps) > 3
            else "medium"
            if len(gaps) > 1
            else "low",
        }

    def get_implementation_summary(self):
        """Get implementation readiness summary."""
        return {
            "estimated_weeks": self.typical_implementation_weeks,
            "risk_level": self.implementation_risk,
            "change_impact": self.change_management_impact,
            "customization_required": self.requires_customization,
            "customization_effort": self.customization_effort,
            "integration_complexity": self.integration_complexity,
            "integration_pattern": self.integration_pattern,
            "readiness_score": self._calculate_readiness_score(),
        }

    def _calculate_readiness_score(self):
        """Calculate implementation readiness score (0 - 100)."""
        score = 100

        # Deduct for customization
        if self.requires_customization:
            effort_deductions = {"low": 5, "medium": 15, "high": 25, "very_high": 40}
            score -= effort_deductions.get(self.customization_effort, 15)

        # Deduct for integration complexity
        complexity_deductions = {"low": 0, "medium": 10, "high": 20, "very_high": 35}
        score -= complexity_deductions.get(self.integration_complexity, 10)

        # Deduct for implementation risk
        risk_deductions = {"low": 0, "medium": 10, "high": 20, "very_high": 30}
        score -= risk_deductions.get(self.implementation_risk, 10)

        return max(0, min(100, score))

    @classmethod
    def get_by_product(cls, vendor_product_id, min_score=0):
        """Get all APQC process mappings for a specific vendor product."""
        return (
            cls.query.filter(
                cls.vendor_product_id == vendor_product_id, cls.relevance_score >= min_score
            )
            .order_by(cls.relevance_score.desc())
            .all()
        )

    @classmethod
    def get_by_process(cls, apqc_process_id, min_score=0):
        """Get all vendor product mappings for a specific APQC process."""
        return (
            cls.query.filter(
                cls.apqc_process_id == apqc_process_id, cls.relevance_score >= min_score
            )
            .order_by(cls.relevance_score.desc())
            .all()
        )

    @classmethod
    def get_top_vendors_for_process(cls, apqc_process_id, limit=10):
        """Get top-rated vendors for a specific APQC process."""
        return (
            cls.query.filter(cls.apqc_process_id == apqc_process_id)
            .order_by(cls.relevance_score.desc(), cls.coverage_percentage.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def get_coverage_summary(cls, vendor_product_id):
        """Get summary of APQC process coverage for a vendor product."""
        from sqlalchemy import func

        result = (
            db.session.query(
                func.count(cls.id).label("total_mappings"),
                func.avg(cls.relevance_score).label("avg_relevance"),
                func.avg(cls.coverage_percentage).label("avg_coverage"),
                func.avg(cls.out_of_box_fit).label("avg_out_of_box"),
                func.max(cls.relevance_score).label("max_score"),
                func.count(func.nullif(cls.relevance_score >= 75, False)).label(
                    "high_relevance_count"
                ),
                func.count(func.nullif(cls.coverage_level == "full", False)).label(
                    "full_coverage_count"
                ),
            )
            .filter(cls.vendor_product_id == vendor_product_id)
            .first()
        )

        return {
            "total_mappings": result.total_mappings or 0,
            "avg_relevance": round(float(result.avg_relevance or 0), 1),
            "avg_coverage": round(float(result.avg_coverage or 0), 1),
            "avg_out_of_box": round(float(result.avg_out_of_box or 0), 1),
            "max_score": result.max_score or 0,
            "high_relevance_count": result.high_relevance_count or 0,
            "full_coverage_count": result.full_coverage_count or 0,
        }

    @classmethod
    def get_process_coverage_by_category(cls, vendor_product_id):
        """Get APQC process coverage grouped by category for a vendor product."""
        from sqlalchemy import func

        from app.models.apqc_process import APQCProcess

        results = (
            db.session.query(
                APQCProcess.category_level_1,
                func.count(cls.id).label("process_count"),
                func.avg(cls.relevance_score).label("avg_relevance"),
                func.avg(cls.coverage_percentage).label("avg_coverage"),
            )
            .join(APQCProcess, cls.apqc_process_id == APQCProcess.id)
            .filter(cls.vendor_product_id == vendor_product_id)
            .group_by(APQCProcess.category_level_1)
            .all()
        )

        return [
            {
                "category": r.category_level_1,
                "process_count": r.process_count,
                "avg_relevance": round(float(r.avg_relevance or 0), 1),
                "avg_coverage": round(float(r.avg_coverage or 0), 1),
            }
            for r in results
        ]

    @classmethod
    def compare_vendors_for_process(cls, apqc_process_id, vendor_product_ids=None):
        """
        Compare multiple vendors for a specific APQC process.
        Returns comparative analysis.
        """
        query = cls.query.filter(cls.apqc_process_id == apqc_process_id)

        if vendor_product_ids:
            query = query.filter(cls.vendor_product_id.in_(vendor_product_ids))

        mappings = query.order_by(cls.relevance_score.desc()).all()

        if not mappings:
            return {"vendors": [], "summary": {}}

        vendors = []
        for m in mappings:
            vendors.append(
                {
                    "vendor_product_id": m.vendor_product_id,
                    "vendor_product_name": m.vendor_product.name if m.vendor_product else None,
                    "vendor_name": m.vendor_product.vendor_organization.name
                    if m.vendor_product and m.vendor_product.vendor_organization
                    else None,
                    "relevance_score": m.relevance_score,
                    "coverage_percentage": m.coverage_percentage,
                    "out_of_box_fit": m.out_of_box_fit,
                    "automation_capability": m.automation_capability,
                    "market_position": m.market_position,
                    "implementation_risk": m.implementation_risk,
                    "strategic_fit": m.calculate_strategic_fit(),
                    "gap_count": len(m.gaps or []),
                }
            )

        # Summary statistics
        summary = {
            "vendor_count": len(vendors),
            "avg_relevance": round(
                sum(v["relevance_score"] or 0 for v in vendors) / len(vendors), 1
            ),
            "avg_coverage": round(
                sum(v["coverage_percentage"] or 0 for v in vendors) / len(vendors), 1
            ),
            "best_vendor": vendors[0] if vendors else None,
            "leaders": [v for v in vendors if v["market_position"] == "leader"],
        }

        return {"vendors": vendors, "summary": summary}

    @classmethod
    def get_industry_fit_analysis(cls, industry, min_score=50):
        """Get vendors that fit a specific industry across all processes."""
        # Query mappings where industry_fit contains the specified industry
        mappings = cls.query.filter(
            cls.relevance_score >= min_score, cls.industry_fit.isnot(None)
        ).all()

        # Filter by industry (JSON array contains)
        return [
            m
            for m in mappings
            if m.industry_fit and industry.lower() in [i.lower() for i in m.industry_fit]
        ]
