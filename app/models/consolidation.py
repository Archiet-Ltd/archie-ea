"""
Consolidation Module Models

Tracks application consolidation opportunities and savings calculations.
Provides comprehensive tracking from candidate detection through realized savings.

Features:
- Duplicate/similar application candidate detection
- Consolidation opportunity management
- Savings estimation and tracking
- ROI and payback period calculations
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, event
from sqlalchemy.orm import attributes, relationship

from .. import db
from .mixins.core import TenantMixin

_state_logger = logging.getLogger(__name__ + ".state_machine")

# Valid status transitions for ConsolidationCandidate
_CANDIDATE_TRANSITIONS = {
    "pending_review": {"approved", "rejected"},
    "approved": {"merged"},
    "rejected": set(),  # terminal
    "merged": set(),  # terminal
}

# Valid status transitions for ConsolidationOpportunity
_OPPORTUNITY_TRANSITIONS = {
    "identified": {"analysis", "cancelled"},
    "analysis": {"approved", "cancelled"},
    "approved": {"in_progress", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed": set(),  # terminal
    "cancelled": set(),  # terminal
}


class ConsolidationCandidate(TenantMixin, db.Model):
    """
    Consolidation Candidate Model

    Represents potential duplicate or similar applications detected
    through various detection methods. Candidates can be reviewed,
    approved, or rejected before becoming consolidation opportunities.
    """

    __tablename__ = "consolidation_candidates"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)

    # Application references
    primary_application_id = Column(
        Integer,
        ForeignKey("application_components.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    duplicate_application_id = Column(
        Integer,
        ForeignKey("application_components.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Similarity metrics
    similarity_score = Column(Float, default=0.0)  # 0.0 to 1.0

    # Detection method
    detection_method = Column(
        String(50)
    )  # name_match, capability_overlap, vendor_match, function_overlap

    # Review status
    status = Column(
        String(50), default="pending_review"
    )  # pending_review, approved, rejected, merged
    notes = Column(Text)

    # Detection and review timestamps
    detected_at = Column(DateTime, default=datetime.utcnow)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    primary_application = relationship(
        "ApplicationComponent",
        foreign_keys=[primary_application_id],
        backref="consolidation_candidates_as_primary",
    )
    duplicate_application = relationship(
        "ApplicationComponent",
        foreign_keys=[duplicate_application_id],
        backref="consolidation_candidates_as_duplicate",
    )
    reviewer = relationship("User", foreign_keys=[reviewed_by], backref="reviewed_candidates")

    # Indexes for performance
    __table_args__ = (
        Index("idx_candidate_status", "status"),
        Index("idx_candidate_similarity", "similarity_score"),
        Index("idx_candidate_detection", "detection_method"),
        Index("idx_candidate_apps", "primary_application_id", "duplicate_application_id"),
        {"extend_existing": True},
    )

    def __repr__(self):
        return f"<ConsolidationCandidate {self.id}: {self.primary_application_id} <-> {self.duplicate_application_id}>"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "primary_application_id": self.primary_application_id,
            "primary_application_name": self.primary_application.name
            if self.primary_application
            else None,
            "duplicate_application_id": self.duplicate_application_id,
            "duplicate_application_name": self.duplicate_application.name
            if self.duplicate_application
            else None,
            "similarity_score": self.similarity_score,
            "detection_method": self.detection_method,
            "status": self.status,
            "notes": self.notes,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "reviewed_by": self.reviewed_by,
            "reviewer_name": self.reviewer.full_name() if self.reviewer else None,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ConsolidationOpportunity(TenantMixin, db.Model):
    """
    Consolidation Opportunity Model

    Represents an approved consolidation project that combines multiple
    applications into a single target application. Tracks financial
    estimates, implementation progress, and business impact.
    """

    __tablename__ = "consolidation_opportunities"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)

    # Opportunity identification
    name = Column(String(256), nullable=False, index=True)
    description = Column(Text)

    # Status and priority
    status = Column(
        String(50), default="identified"
    )  # identified, analysis, approved, in_progress, completed, cancelled
    priority = Column(String(20), default="medium")  # critical, high, medium, low

    # Target application (the one to keep)
    target_application_id = Column(
        Integer,
        ForeignKey("application_components.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Source applications to consolidate (stored as JSON list of IDs)
    source_applications = Column(Text)  # JSON list of application IDs

    # Financial estimates
    estimated_annual_savings = Column(Float, default=0.0)
    estimated_one_time_savings = Column(Float, default=0.0)
    implementation_cost = Column(Float, default=0.0)
    roi_percentage = Column(
        Float, default=0.0
    )  # Calculated: (annual_savings - impl_cost) / impl_cost * 100
    payback_period_months = Column(Integer, default=0)  # impl_cost / (annual_savings / 12)

    # Risk and complexity assessment
    risk_level = Column(String(20), default="medium")  # low, medium, high
    complexity = Column(String(20), default="moderate")  # simple, moderate, complex

    # Business impact
    business_impact = Column(Text)
    technical_dependencies = Column(Text)  # JSON list of dependencies

    # Timeline
    start_date = Column(Date, nullable=True)
    target_completion_date = Column(Date, nullable=True)
    actual_completion_date = Column(Date, nullable=True)

    # Ownership
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    target_application = relationship(
        "ApplicationComponent",
        foreign_keys=[target_application_id],
        backref="consolidation_opportunities_as_target",
    )
    owner = relationship(
        "User", foreign_keys=[owner_id], backref="owned_consolidation_opportunities"
    )
    savings_realizations = relationship(
        "SavingsRealization", back_populates="opportunity", cascade="all, delete-orphan"
    )

    # Indexes for performance
    __table_args__ = (
        Index("idx_opportunity_status", "status"),
        Index("idx_opportunity_priority", "priority"),
        Index("idx_opportunity_target", "target_application_id"),
        Index("idx_opportunity_dates", "start_date", "target_completion_date"),
        {"extend_existing": True},
    )

    def __repr__(self):
        return f"<ConsolidationOpportunity {self.name}>"

    def get_source_application_ids(self) -> List[int]:
        """Parse JSON source applications and return list of IDs"""
        if self.source_applications:
            try:
                import json

                return json.loads(self.source_applications)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def set_source_application_ids(self, app_ids: List[int]):
        """Set source applications as JSON"""
        import json

        self.source_applications = json.dumps(app_ids)

    def get_technical_dependencies_list(self) -> List[str]:
        """Parse JSON technical dependencies"""
        if self.technical_dependencies:
            try:
                import json

                return json.loads(self.technical_dependencies)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def set_technical_dependencies_list(self, dependencies: List[str]):
        """Set technical dependencies as JSON"""
        import json

        self.technical_dependencies = json.dumps(dependencies)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "target_application_id": self.target_application_id,
            "target_application_name": self.target_application.name
            if self.target_application
            else None,
            "source_application_ids": self.get_source_application_ids(),
            "estimated_annual_savings": self.estimated_annual_savings,
            "estimated_one_time_savings": self.estimated_one_time_savings,
            "implementation_cost": self.implementation_cost,
            "roi_percentage": self.roi_percentage,
            "payback_period_months": self.payback_period_months,
            "risk_level": self.risk_level,
            "complexity": self.complexity,
            "business_impact": self.business_impact,
            "technical_dependencies": self.get_technical_dependencies_list(),
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "target_completion_date": self.target_completion_date.isoformat()
            if self.target_completion_date
            else None,
            "actual_completion_date": self.actual_completion_date.isoformat()
            if self.actual_completion_date
            else None,
            "owner_id": self.owner_id,
            "owner_name": self.owner.full_name() if self.owner else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SavingsRealization(TenantMixin, db.Model):
    """
    Savings Realization Model

    Tracks actual savings achieved after consolidation completion.
    Allows for period-by-period tracking of different savings categories.
    """

    __tablename__ = "savings_realizations"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)

    # Link to opportunity
    opportunity_id = Column(
        Integer,
        ForeignKey("consolidation_opportunities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Savings period
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

    # Savings amount and category
    realized_savings = Column(Float, default=0.0)
    savings_category = Column(String(50))  # license, infrastructure, support, maintenance

    # Notes and verification
    notes = Column(Text)
    verified_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    opportunity = relationship("ConsolidationOpportunity", back_populates="savings_realizations")
    verifier = relationship("User", foreign_keys=[verified_by], backref="verified_savings")

    # Indexes for performance
    __table_args__ = (
        Index("idx_savings_opportunity", "opportunity_id"),
        Index("idx_savings_period", "period_start", "period_end"),
        Index("idx_savings_category", "savings_category"),
        {"extend_existing": True},
    )

    def __repr__(self):
        return f"<SavingsRealization {self.id}: ${self.realized_savings} ({self.savings_category})>"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "opportunity_id": self.opportunity_id,
            "opportunity_name": self.opportunity.name if self.opportunity else None,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "realized_savings": self.realized_savings,
            "savings_category": self.savings_category,
            "notes": self.notes,
            "verified_by": self.verified_by,
            "verifier_name": self.verifier.full_name() if self.verifier else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================================
# State Machine Enforcement via SQLAlchemy Events
# ============================================================================


def _validate_status_transition(transitions_map, model_name):
    """Create a reusable SQLAlchemy event listener for status validation."""
    def _listener(target, value, oldvalue, initiator):
        if oldvalue is value or oldvalue is None:
            return value
        if oldvalue is attributes.NO_VALUE or oldvalue is attributes.NEVER_SET:
            return value
        # Allow same-state transitions (idempotency)
        if oldvalue == value:
            return value
        allowed = transitions_map.get(oldvalue, set())
        if value not in allowed:
            _state_logger.warning(
                "Invalid %s transition blocked: %s -> %s (allowed: %s)",
                model_name, oldvalue, value, allowed or "none (terminal)",
            )
            raise ValueError(
                f"Invalid {model_name} status transition: {oldvalue} -> {value}. "
                f"Allowed: {allowed or 'none (terminal state)'}"
            )
        _state_logger.debug("%s status: %s -> %s", model_name, oldvalue, value)
        return value
    return _listener


event.listen(
    ConsolidationCandidate.status, "set",
    _validate_status_transition(_CANDIDATE_TRANSITIONS, "ConsolidationCandidate"),
    retval=True,
)
event.listen(
    ConsolidationOpportunity.status, "set",
    _validate_status_transition(_OPPORTUNITY_TRANSITIONS, "ConsolidationOpportunity"),
    retval=True,
)
