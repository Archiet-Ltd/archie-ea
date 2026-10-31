"""
MISSING CAPABILITY-DRIVEN MODELS FROM MDD/flask-base-master
These are the 4 truly missing models copied exactly from source
"""

import json
from datetime import datetime

from .. import db
from app.models.mixins import TenantMixin


# ============================================================================
# APPLICATION CAPABILITY MODEL (Copied from source)
# ============================================================================
class ApplicationCapability(TenantMixin, db.Model):
    """
    Maps business capabilities to application/IT capabilities.

    This is the bridge between business (what) and IT (how it's implemented).
    Links to existing systems and identifies gaps.
    """

    __tablename__ = "application_capability"
    __tenant_parent__ = (('business_capability_id', 'business_capability'),)

    id = db.Column(db.Integer, primary_key=True)
    business_capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id"), nullable=False
    )

    # Application info
    application_name = db.Column(db.String(256), nullable=False, index=True)
    application_type = db.Column(db.String(50))  # COTS, Custom, SaaS, Legacy, Salesforce
    vendor = db.Column(db.String(256))

    # Capability support
    support_level = db.Column(db.String(50))  # full, partial, none, gap
    coverage_percent = db.Column(db.Integer)  # 0 - 100%

    # Maturity contribution
    maturity_contribution = db.Column(db.Integer)  # How much this app enables maturity (0 - 5)

    # Technical health
    technical_debt_score = db.Column(db.Integer)  # 0 - 100 (higher = more debt)
    age_years = db.Column(db.Integer)
    replacement_priority = db.Column(db.String(20))  # critical, high, medium, low, none
    replacement_cost_estimate = db.Column(db.Float)

    # Integration with existing models
    archimate_application_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    technology_stack_id = db.Column(db.Integer, db.ForeignKey("technology_stacks.id"))
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ApplicationCapability {self.application_name} → {self.business_capability.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "application_name": self.application_name,
            "application_type": self.application_type,
            "support_level": self.support_level,
            "coverage_percent": self.coverage_percent,
            "technical_debt_score": self.technical_debt_score,
            "replacement_priority": self.replacement_priority,
        }


# ============================================================================
# CAPABILITY DEPENDENCY MODEL (Copied from source)
# ============================================================================
# NOTE: CapabilityDependency is defined in app/models/capability_models.py
# Removed duplicate definition that was causing SQLAlchemy mapper conflicts
# See app/models/capability_models.py for the authoritative CapabilityDependency model


# ============================================================================
# CAPABILITY MATURITY LEVEL MODEL (Copied from source)
# ============================================================================
class CapabilityMaturityLevel(db.Model):
    """
    Capability Maturity Model (CMM) Level Definitions.

    Defines the 5 levels of maturity and their characteristics.
    Used to drive NFR generation and architecture decisions.
    """

    __tablename__ = "capability_maturity_level"

    id = db.Column(db.Integer, primary_key=True)

    level = db.Column(db.Integer, unique=True, nullable=False)  # 1 - 5
    name = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text)

    # Level characteristics
    characteristics = db.Column(db.Text)  # JSON array
    required_practices = db.Column(db.Text)  # JSON array
    key_process_areas = db.Column(db.Text)  # JSON array

    # Typical NFRs for this level (templates)
    typical_nfrs = db.Column(db.Text)  # JSON object {performance: {...}, security: {...}}

    # Architecture patterns for this level
    architecture_patterns = db.Column(db.Text)  # JSON array
    technology_requirements = db.Column(db.Text)  # JSON object

    # Governance requirements
    governance_requirements = db.Column(db.Text)  # JSON array
    compliance_requirements = db.Column(db.Text)  # JSON array

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<CapabilityMaturityLevel {self.level}: {self.name}>"

    def to_dict(self):
        return {
            "level": self.level,
            "name": self.name,
            "description": self.description,
            "characteristics": json.loads(self.characteristics) if self.characteristics else [],
            "typical_nfrs": json.loads(self.typical_nfrs) if self.typical_nfrs else {},
            "architecture_patterns": json.loads(self.architecture_patterns)
            if self.architecture_patterns
            else [],
        }


# ============================================================================
# CAPABILITY PRIORITIZATION MODEL (Copied from source)
# ============================================================================
class CapabilityPrioritization(db.Model):
    """
    Tracks capability prioritization for investment decisions.

    AI-powered prioritization based on gap, value, and strategic importance.
    """

    __tablename__ = "capability_prioritization"

    id = db.Column(db.Integer, primary_key=True)
    capability_id = db.Column(db.Integer, db.ForeignKey("business_capability.id"), nullable=False)

    # Prioritization scores
    priority_score = db.Column(db.Integer)  # 0 - 100
    priority_tier = db.Column(db.String(20))  # P0, P1, P2, P3, P4

    # Score components
    gap_score = db.Column(db.Integer)  # Based on maturity gap
    value_score = db.Column(db.Integer)  # Based on business value
    strategic_score = db.Column(db.Integer)  # Based on strategic importance
    feasibility_score = db.Column(db.Integer)  # Based on complexity/effort

    # Investment recommendation
    recommended_investment = db.Column(db.Float)
    estimated_timeline_months = db.Column(db.Integer)
    expected_roi = db.Column(db.Float)
    expected_roi_timeframe_months = db.Column(db.Integer)

    # Rationale
    prioritization_rationale = db.Column(db.Text)
    key_benefits = db.Column(db.Text)  # JSON array
    key_risks = db.Column(db.Text)  # JSON array
    dependencies = db.Column(db.Text)  # JSON array of capability IDs

    # AI metadata
    generated_by_ai = db.Column(db.Boolean, default=True)
    ai_confidence = db.Column(db.Float)

    # Review
    reviewed_by = db.Column(db.String(256))
    review_date = db.Column(db.DateTime)
    approved = db.Column(db.Boolean)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    capability = db.relationship(
        "BusinessCapability",
    )

    def __repr__(self):
        return f"<CapabilityPrioritization {self.capability.name}: {self.priority_tier}>"

    def to_dict(self):
        return {
            "capability_name": self.capability.name if self.capability else None,
            "priority_score": self.priority_score,
            "priority_tier": self.priority_tier,
            "recommended_investment": self.recommended_investment,
            "estimated_timeline_months": self.estimated_timeline_months,
            "expected_roi": self.expected_roi,
            "prioritization_rationale": self.prioritization_rationale,
            "key_benefits": json.loads(self.key_benefits) if self.key_benefits else [],
            "key_risks": json.loads(self.key_risks) if self.key_risks else [],
        }


print("Missing capability-driven models copied from source successfully!")
