"""
Industry-Specific APQC Models

Provides industry-tailored APQC process classification frameworks.
Supports Manufacturing, Finance, Healthcare, Pharma, Retail, and other industries.
"""

from datetime import datetime

from app import db
from app.models.mixins import HybridTenantMixin, TenantMixin


class IndustryAPQCFramework(HybridTenantMixin, db.Model):
    """
    Industry-specific APQC Process Classification Framework.

    Each industry has its own variant of the APQC PCF with
    industry-specific processes, terminology, and benchmarks.
    """

    __tablename__ = "industry_apqc_framework"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)

    # Industry identification
    industry_code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    # e.g., 'MFG', 'BFS', 'HCP', 'PHA', 'RET', 'CRS'

    industry_name = db.Column(db.String(100), nullable=False)
    industry_subcategory = db.Column(db.String(100))
    # e.g., 'Discrete Manufacturing', 'Process Manufacturing'

    description = db.Column(db.Text)

    # PCF Version tracking
    pcf_version = db.Column(db.String(20))
    release_date = db.Column(db.Date)

    # Industry characteristics
    regulatory_intensity = db.Column(db.String(20), default="medium")
    # 'low', 'medium', 'high', 'very_high'

    digital_maturity_typical = db.Column(db.Integer, default=3)
    # 1 - 5 scale of typical industry digital maturity

    process_complexity = db.Column(db.String(20), default="moderate")
    # 'simple', 'moderate', 'complex', 'highly_complex'

    # Industry-specific differentiators (JSON arrays)
    key_differentiators = db.Column(db.JSON, default=list)
    unique_process_areas = db.Column(db.JSON, default=list)

    # Benchmark data
    benchmark_metrics = db.Column(db.JSON)
    maturity_benchmarks = db.Column(db.JSON)

    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    processes = db.relationship("IndustryAPQCProcess", backref="framework", lazy=True)
    recommendations = db.relationship(
        "IndustryProcessRecommendation", backref="framework", lazy=True
    )

    def __repr__(self):
        return f"<IndustryAPQCFramework {self.industry_code} - {self.industry_name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "industry_code": self.industry_code,
            "industry_name": self.industry_name,
            "industry_subcategory": self.industry_subcategory,
            "description": self.description,
            "pcf_version": self.pcf_version,
            "release_date": self.release_date.isoformat() if self.release_date else None,
            "regulatory_intensity": self.regulatory_intensity,
            "digital_maturity_typical": self.digital_maturity_typical,
            "process_complexity": self.process_complexity,
            "key_differentiators": self.key_differentiators,
            "unique_process_areas": self.unique_process_areas,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class IndustryAPQCProcess(HybridTenantMixin, db.Model):
    """
    Industry-specific process within an APQC framework.

    Extends base APQC processes with industry-specific details,
    benchmarks, and regulatory requirements.
    """

    __tablename__ = "industry_apqc_process"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)

    # Link to framework
    industry_framework_id = db.Column(
        db.Integer,
        db.ForeignKey("industry_apqc_framework.id"),
        nullable=False,
        index=True,
    )

    # Link to base APQC process (optional)
    base_process_id = db.Column(db.Integer, db.ForeignKey("apqc_process.id"))

    # Process identification
    industry_process_code = db.Column(db.String(30), nullable=False)
    industry_process_name = db.Column(db.String(256), nullable=False)
    industry_process_description = db.Column(db.Text)

    # Industry hierarchy
    industry_category_1 = db.Column(db.String(100))
    industry_category_2 = db.Column(db.String(100))
    industry_category_3 = db.Column(db.String(100))

    # Industry-specific flags
    is_industry_unique = db.Column(db.Boolean, default=False)
    # True if this process only exists in this industry

    is_modified_from_base = db.Column(db.Boolean, default=False)
    # True if modified from cross-industry base

    # Benchmarks
    typical_automation_level = db.Column(db.Integer)
    # 0 - 100 typical automation percentage

    typical_maturity_level = db.Column(db.Integer)
    # 1 - 5 typical maturity

    industry_benchmarks = db.Column(db.JSON)
    # {"cycle_time": {"p25": 5, "p50": 10, "p75": 20}, ...}

    # Technology and vendors
    technology_enablers = db.Column(db.JSON, default=list)
    # ["ERP", "MES", "PLM", ...]

    leading_vendors = db.Column(db.JSON, default=list)
    # ["SAP", "Oracle", ...]

    # Regulatory requirements
    regulatory_requirements = db.Column(db.JSON, default=list)
    # [{"regulation": "SOX", "requirement": "...", "criticality": "high"}]

    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<IndustryAPQCProcess {self.industry_process_code} - {self.industry_process_name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "industry_framework_id": self.industry_framework_id,
            "base_process_id": self.base_process_id,
            "industry_process_code": self.industry_process_code,
            "industry_process_name": self.industry_process_name,
            "industry_process_description": self.industry_process_description,
            "industry_category_1": self.industry_category_1,
            "industry_category_2": self.industry_category_2,
            "industry_category_3": self.industry_category_3,
            "is_industry_unique": self.is_industry_unique,
            "is_modified_from_base": self.is_modified_from_base,
            "typical_automation_level": self.typical_automation_level,
            "typical_maturity_level": self.typical_maturity_level,
            "technology_enablers": self.technology_enablers,
            "leading_vendors": self.leading_vendors,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def get_industry_unique_processes(cls, industry_code):
        """Get processes unique to an industry."""
        return (
            cls.query.join(IndustryAPQCFramework)
            .filter(
                IndustryAPQCFramework.industry_code == industry_code,
                cls.is_industry_unique == True,
                cls.is_active == True,
            )
            .all()
        )

    @classmethod
    def get_with_regulatory_requirements(cls, industry_code):
        """Get processes with regulatory requirements."""
        return (
            cls.query.join(IndustryAPQCFramework)
            .filter(
                IndustryAPQCFramework.industry_code == industry_code,
                cls.regulatory_requirements.isnot(None),
                cls.is_active == True,
            )
            .all()
        )


class IndustryProcessRecommendation(TenantMixin, db.Model):
    """
    AI-generated process improvement recommendation.

    Based on maturity assessments and industry benchmarks,
    provides actionable recommendations for process improvement.

    Unlike the shared framework/process catalogue it references, a generated
    recommendation is real per-organisation output (like StrategicRecommendation),
    so it is fenced (TenantMixin) rather than left shared (HybridTenantMixin).
    """

    __tablename__ = "industry_process_recommendation"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)

    # Links
    industry_framework_id = db.Column(
        db.Integer, db.ForeignKey("industry_apqc_framework.id"), nullable=False, index=True
    )
    industry_process_id = db.Column(
        db.Integer, db.ForeignKey("industry_apqc_process.id"), index=True
    )

    # Current state assessment
    current_maturity = db.Column(db.Integer)
    current_automation = db.Column(db.Integer)
    performance_gap = db.Column(db.Integer)
    # Gap between current and target/benchmark

    # Recommendation details
    recommendation_type = db.Column(db.String(50), nullable=False)
    # 'automation', 'process_improvement', 'technology_upgrade', 'outsourcing', 'consolidation'

    recommendation_title = db.Column(db.String(256), nullable=False)
    recommendation_description = db.Column(db.Text)

    # Scoring
    impact_score = db.Column(db.Integer)
    # 1 - 10 potential impact

    effort_score = db.Column(db.Integer)
    # 1 - 10 implementation effort

    priority_score = db.Column(db.Float)
    # Calculated priority (impact / effort, adjusted)

    estimated_roi = db.Column(db.Float)
    # Estimated ROI percentage

    # Implementation details
    implementation_steps = db.Column(db.JSON, default=list)
    # [{"step": 1, "action": "...", "duration": "2 weeks"}, ...]

    required_technologies = db.Column(db.JSON, default=list)
    typical_timeline = db.Column(db.String(50))
    recommended_vendors = db.Column(db.JSON, default=list)

    # Classification
    is_quick_win = db.Column(db.Boolean, default=False)
    is_strategic_initiative = db.Column(db.Boolean, default=False)

    # Status tracking
    status = db.Column(db.String(20), default="pending")
    # 'pending', 'accepted', 'rejected', 'in_progress', 'completed'

    rejection_reason = db.Column(db.Text)

    # Approval tracking
    accepted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    accepted_at = db.Column(db.DateTime)

    # AI confidence
    ai_confidence = db.Column(db.Float)
    # 0 - 1 confidence score

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    industry_process = db.relationship("IndustryAPQCProcess", backref="recommendations")
    accepted_by = db.relationship("User")

    def __repr__(self):
        return f"<IndustryProcessRecommendation {self.id}: {self.recommendation_title[:50]}>"

    def to_dict(self):
        return {
            "id": self.id,
            "industry_framework_id": self.industry_framework_id,
            "industry_process_id": self.industry_process_id,
            "current_maturity": self.current_maturity,
            "current_automation": self.current_automation,
            "performance_gap": self.performance_gap,
            "recommendation_type": self.recommendation_type,
            "recommendation_title": self.recommendation_title,
            "recommendation_description": self.recommendation_description,
            "impact_score": self.impact_score,
            "effort_score": self.effort_score,
            "priority_score": self.priority_score,
            "estimated_roi": self.estimated_roi,
            "implementation_steps": self.implementation_steps,
            "required_technologies": self.required_technologies,
            "typical_timeline": self.typical_timeline,
            "recommended_vendors": self.recommended_vendors,
            "is_quick_win": self.is_quick_win,
            "is_strategic_initiative": self.is_strategic_initiative,
            "status": self.status,
            "ai_confidence": self.ai_confidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
