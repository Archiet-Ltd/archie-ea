"""
Unified Application Capability Mapping

Bridge table for unified capabilities to applications.
Maps the new unified capability framework to existing applications.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app import db
from app.models.mixins import TenantMixin


class UnifiedApplicationCapabilityMapping(TenantMixin, db.Model):
    """
    Unified Application-Capability Mapping

    Maps unified capabilities to applications with comprehensive gap analysis.
    Bridges the new unified capability framework with existing applications.
    """

    __tablename__ = "unified_application_capability_mapping"
    __tenant_parent__ = (('unified_capability_id', 'unified_capabilities'),)

    id = Column(db.Integer, primary_key=True)

    # Link entities
    unified_capability_id = Column(
        BigInteger, db.ForeignKey("unified_capabilities.id"), nullable=False, index=True
    )
    application_component_id = Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )

    # Mapping characteristics
    support_level = Column(db.String(20), default="partial")  # full, partial, minimal
    coverage_percentage = Column(db.Integer, default=0)  # 0 - 100% coverage
    support_quality = Column(db.Integer, default=3)  # 1 - 5 quality of support

    # Maturity and strategic flags
    maturity_level = Column(db.Integer)  # 1 - 5 capability maturity level
    is_strategic = Column(db.Boolean, default=False)  # Strategic capability flag
    notes = Column(db.Text)  # Mapping notes

    # Relationship strength
    relationship_type = Column(
        db.String(20), default="enables"
    )  # enables, supports, governs, measures
    relationship_strength = Column(db.Integer, default=3)  # 1 - 5 strength scale
    dependency_level = Column(db.String(20), default="medium")  # critical, high, medium, low

    # Gap analysis
    gap_status = Column(
        db.String(20), default="unknown"
    )  # fully_covered, partially_covered, gap, excess
    gap_description = Column(db.Text)
    gap_impact = Column(db.String(20))  # high, medium, low
    priority = Column(db.String(20))  # high, medium, low

    # Implementation details
    integration_complexity = Column(db.String(20))  # low, medium, high
    integration_effort_person_days = Column(db.Integer)
    technical_debt_score = Column(db.Integer)  # 0 - 100

    # Business impact
    business_value_score = Column(db.Integer)  # 1 - 10
    user_satisfaction_score = Column(db.Integer)  # 1 - 10
    operational_efficiency_gain = Column(db.Integer)  # percentage

    # Financial impact
    annual_cost_savings = Column(db.Float)  # Potential annual cost savings
    implementation_cost = Column(db.Float)  # Cost to implement/upgrade
    payback_period_months = Column(db.Integer)  # Payback period in months

    # Timeline
    start_date = Column(db.Date)
    end_date = Column(db.Date)
    is_active = Column(db.Boolean, default=True)

    # Assessment metadata
    last_assessed = Column(db.DateTime, default=datetime.utcnow)
    assessor = Column(db.String(100))
    assessment_methodology = Column(db.String(50))  # interview, survey, operational_data
    assessment_notes = Column(db.Text)

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    unified_capability = relationship("UnifiedCapability")
    application = relationship("ApplicationComponent")

    def __repr__(self):
        return f"<UnifiedAppCapMapping app={self.application_component_id} -> cap={self.unified_capability_id}>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "unified_capability_id": self.unified_capability_id,
            "capability_name": self.unified_capability.name if self.unified_capability else None,
            "capability_code": self.unified_capability.code if self.unified_capability else None,
            "application_id": self.application_component_id,
            "application_name": self.application.name if self.application else None,
            "support_level": self.support_level,
            "coverage_percentage": self.coverage_percentage,
            "support_quality": self.support_quality,
            "maturity_level": self.maturity_level,
            "is_strategic": self.is_strategic,
            "notes": self.notes,
            "relationship_type": self.relationship_type,
            "relationship_strength": self.relationship_strength,
            "dependency_level": self.dependency_level,
            "gap_status": self.gap_status,
            "gap_description": self.gap_description,
            "gap_impact": self.gap_impact,
            "priority": self.priority,
            "integration_complexity": self.integration_complexity,
            "technical_debt_score": self.technical_debt_score,
            "business_value_score": self.business_value_score,
            "user_satisfaction_score": self.user_satisfaction_score,
            "operational_efficiency_gain": self.operational_efficiency_gain,
            "annual_cost_savings": self.annual_cost_savings,
            "implementation_cost": self.implementation_cost,
            "payback_period_months": self.payback_period_months,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "is_active": self.is_active,
            "last_assessed": self.last_assessed.isoformat() if self.last_assessed else None,
            "assessor": self.assessor,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
