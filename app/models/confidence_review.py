"""
Confidence Threshold Controls & Review Queue Models

Provides comprehensive confidence threshold management and review queue system
for AI-generated content with human-in-the-loop validation and approval workflows.
"""

import json
from datetime import datetime
from enum import Enum

from .. import db
from .mixins.core import TenantMixin


class ReviewStatus(Enum):
    """Review status enumeration."""

    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    REQUIRES_REVISION = "requires_revision"
    AUTO_APPROVED = "auto_approved"
    EXPIRED = "expired"


class ConfidenceThreshold(db.Model):
    """Global and context-specific confidence threshold settings."""

    __tablename__ = "confidence_thresholds"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.BigInteger, primary_key=True)
    threshold_name = db.Column(db.String(200), nullable=False, unique=True, index=True)
    threshold_type = db.Column(
        db.String(50), nullable=False
    )  # "global", "capability", "process", "vendor", "archimate"

    # Threshold values
    minimum_confidence = db.Column(db.Numeric(3, 2), nullable=False, default=0.6)  # 0.0 - 1.0
    auto_approval_threshold = db.Column(
        db.Numeric(3, 2), nullable=False, default=0.8
    )  # Auto-approve above this
    rejection_threshold = db.Column(
        db.Numeric(3, 2), nullable=False, default=0.3
    )  # Auto-reject below this

    # Context filters
    context_type = db.Column(db.String(50))  # "capability_level", "process_level", "vendor_tier"
    context_value = db.Column(db.String(100))  # Specific context value
    domain_filter = db.Column(db.String(100))  # "business", "technology", "data", "security"

    # Review settings
    requires_human_review = db.Column(db.Boolean, default=True)
    auto_review_enabled = db.Column(db.Boolean, default=False)
    review_queue_priority = db.Column(db.Integer, default=5)  # 1 - 10, lower is higher priority

    # Validation rules
    validation_rules = db.Column(db.Text)  # JSON with additional validation criteria
    quality_gates = db.Column(db.Text)  # JSON with quality gate requirements

    # Metadata
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    created_by = db.relationship("User", backref="created_thresholds")
    review_items = db.relationship(
        "ReviewQueueItem", back_populates="threshold", cascade="all, delete-orphan", lazy="select"
    )

    def __repr__(self):
        return f"<ConfidenceThreshold {self.threshold_name} ({self.threshold_type})>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "threshold_name": self.threshold_name,
            "threshold_type": self.threshold_type,
            "minimum_confidence": float(self.minimum_confidence),
            "auto_approval_threshold": float(self.auto_approval_threshold),
            "rejection_threshold": float(self.rejection_threshold),
            "context_type": self.context_type,
            "context_value": self.context_value,
            "domain_filter": self.domain_filter,
            "requires_human_review": self.requires_human_review,
            "auto_review_enabled": self.auto_review_enabled,
            "review_queue_priority": self.review_queue_priority,
            "validation_rules": json.loads(self.validation_rules) if self.validation_rules else {},
            "quality_gates": json.loads(self.quality_gates) if self.quality_gates else {},
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ReviewQueueItem(TenantMixin, db.Model):
    """Individual items in the review queue for human validation.

    Each item belongs to one organisation and is only visible to that
    organisation's users; the tenant filter applies through ``TenantMixin``.
    """

    __tablename__ = "review_queue_items"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.BigInteger, primary_key=True)

    # Nullable so reconcile-schema can add the column to an existing table (it
    # only adds nullable columns). The mixin still applies the tenant filter, and a
    # row with no organisation matches no organisation, so it is listed for
    # nobody. New rows take the organisation of the creating request or the
    # background service's reviewed application; there is no default owner.
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    threshold_id = db.Column(db.BigInteger, db.ForeignKey("confidence_thresholds.id"))

    # Item identification
    item_type = db.Column(
        db.String(50), nullable=False
    )  # "capability_mapping", "process_classification", "vendor_analysis", "archimate_element"
    item_id = db.Column(db.BigInteger)  # Foreign key to the actual item
    item_name = db.Column(db.String(500), nullable=False)
    item_data = db.Column(db.Text)  # JSON with item-specific data

    # Confidence analysis
    confidence_score = db.Column(db.Numeric(3, 2), nullable=False)  # 0.0 - 1.0
    confidence_factors = db.Column(db.Text)  # JSON with confidence breakdown
    ai_model_used = db.Column(db.String(100))  # LLM model used for generation
    generation_timestamp = db.Column(db.DateTime)

    # Review status
    status = db.Column(db.Enum(ReviewStatus), default=ReviewStatus.PENDING, index=True)
    review_priority = db.Column(db.Integer, default=5)  # 1 - 10, lower is higher priority
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    assigned_at = db.Column(db.DateTime)

    # Review process
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    review_notes = db.Column(db.Text)
    review_decision = db.Column(db.String(50))  # "approve", "reject", "request_revision"
    rejection_reason = db.Column(db.Text)

    # Quality assessment
    quality_score = db.Column(db.Numeric(3, 2))  # 0.0 - 1.0
    accuracy_rating = db.Column(db.Integer)  # 1 - 5 scale
    completeness_rating = db.Column(db.Integer)  # 1 - 5 scale
    relevance_rating = db.Column(db.Integer)  # 1 - 5 scale

    # Escalation and timeout
    escalation_level = db.Column(db.Integer, default=0)  # 0, 1, 2 for escalation
    escalated_to_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    escalated_at = db.Column(db.DateTime)
    review_deadline = db.Column(db.DateTime)
    auto_review_date = db.Column(db.DateTime)  # When auto-review will be triggered

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Consolidation: set by `flask backfill-review-queue-approvals` on
    # the row's canonical ai_chat_crud_approvals copy. NULL until backfilled;
    # this table stays readable (never dropped), it just stops gaining new
    # rows once the constructor site is repointed.
    retired_into_id = db.Column(
        db.Integer, db.ForeignKey("ai_chat_crud_approvals.id"), nullable=True
    )

    # Relationships
    threshold = db.relationship("ConfidenceThreshold", back_populates="review_items")
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id], backref="assigned_reviews")
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id], backref="reviewed_items")
    escalated_to = db.relationship(
        "User", foreign_keys=[escalated_to_id], backref="escalated_reviews"
    )

    def __repr__(self):
        return f"<ReviewQueueItem {self.item_name} ({self.status.value})>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "threshold_id": self.threshold_id,
            "item_type": self.item_type,
            "item_id": self.item_id,
            "item_name": self.item_name,
            "item_data": json.loads(self.item_data) if self.item_data else {},
            "confidence_score": float(self.confidence_score),
            "confidence_factors": json.loads(self.confidence_factors)
            if self.confidence_factors
            else {},
            "ai_model_used": self.ai_model_used,
            "generation_timestamp": self.generation_timestamp.isoformat()
            if self.generation_timestamp
            else None,
            "status": self.status.value,
            "review_priority": self.review_priority,
            "assigned_to_id": self.assigned_to_id,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "reviewed_by_id": self.reviewed_by_id,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "review_notes": self.review_notes,
            "review_decision": self.review_decision,
            "rejection_reason": self.rejection_reason,
            "quality_score": float(self.quality_score) if self.quality_score else None,
            "accuracy_rating": self.accuracy_rating,
            "completeness_rating": self.completeness_rating,
            "relevance_rating": self.relevance_rating,
            "escalation_level": self.escalation_level,
            "escalated_to_id": self.escalated_to_id,
            "escalated_at": self.escalated_at.isoformat() if self.escalated_at else None,
            "review_deadline": self.review_deadline.isoformat() if self.review_deadline else None,
            "auto_review_date": self.auto_review_date.isoformat()
            if self.auto_review_date
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ReviewDecision(db.Model):
    """Review decision records for audit and learning."""

    __tablename__ = "review_decisions"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.BigInteger, primary_key=True)
    review_item_id = db.Column(
        db.BigInteger, db.ForeignKey("review_queue_items.id"), nullable=False
    )

    # Decision details
    decision_type = db.Column(
        db.String(50), nullable=False
    )  # "approve", "reject", "request_revision", "auto_approve"
    decision_reason = db.Column(db.Text)
    confidence_adjustment = db.Column(db.Numeric(3, 2))  # Human-adjusted confidence score

    # Quality assessment
    quality_assessment = db.Column(db.Text)  # JSON with quality criteria assessment
    identified_issues = db.Column(db.Text)  # JSON array of identified issues
    suggested_improvements = db.Column(db.Text)  # JSON array of improvement suggestions

    # Learning data
    human_confidence_estimate = db.Column(db.Numeric(3, 2))  # Human's confidence in AI result
    ai_accuracy_assessment = db.Column(db.Integer)  # 1 - 5 scale of AI accuracy
    correction_made = db.Column(db.Boolean, default=False)
    corrected_data = db.Column(db.Text)  # JSON with corrected data

    # Reviewer information
    reviewer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reviewer_role = db.Column(db.String(50))  # "architect", "analyst", "domain_expert", "admin"
    reviewer_experience_level = db.Column(db.String(20))  # "junior", "senior", "expert"

    # Time tracking
    review_duration_seconds = db.Column(db.Integer)  # Time spent reviewing
    decision_timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Relationships
    review_item = db.relationship("ReviewQueueItem", backref="decisions")
    reviewer = db.relationship("User", backref="review_decisions")

    def __repr__(self):
        return f"<ReviewDecision {self.decision_type} for item {self.review_item_id}>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "review_item_id": self.review_item_id,
            "decision_type": self.decision_type,
            "decision_reason": self.decision_reason,
            "confidence_adjustment": float(self.confidence_adjustment)
            if self.confidence_adjustment
            else None,
            "quality_assessment": json.loads(self.quality_assessment)
            if self.quality_assessment
            else {},
            "identified_issues": json.loads(self.identified_issues)
            if self.identified_issues
            else [],
            "suggested_improvements": json.loads(self.suggested_improvements)
            if self.suggested_improvements
            else [],
            "human_confidence_estimate": float(self.human_confidence_estimate)
            if self.human_confidence_estimate
            else None,
            "ai_accuracy_assessment": self.ai_accuracy_assessment,
            "correction_made": self.correction_made,
            "corrected_data": json.loads(self.corrected_data) if self.corrected_data else {},
            "reviewer_id": self.reviewer_id,
            "reviewer_role": self.reviewer_role,
            "reviewer_experience_level": self.reviewer_experience_level,
            "review_duration_seconds": self.review_duration_seconds,
            "decision_timestamp": self.decision_timestamp.isoformat()
            if self.decision_timestamp
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ReviewQueueStatistics(db.Model):
    """Aggregated statistics for review queue performance monitoring."""

    __tablename__ = "review_queue_statistics"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.BigInteger, primary_key=True)
    date_bucket = db.Column(db.Date, nullable=False, index=True)  # Daily aggregation
    time_bucket = db.Column(db.String(20), index=True)  # "hourly", "daily", "weekly", "monthly"
    item_type = db.Column(
        db.String(50), index=True
    )  # "capability_mapping", "process_classification", etc.

    # Queue metrics
    total_items = db.Column(db.Integer, default=0)
    pending_items = db.Column(db.Integer, default=0)
    in_review_items = db.Column(db.Integer, default=0)
    approved_items = db.Column(db.Integer, default=0)
    rejected_items = db.Column(db.Integer, default=0)
    auto_approved_items = db.Column(db.Integer, default=0)
    expired_items = db.Column(db.Integer, default=0)

    # Performance metrics
    average_review_time_hours = db.Column(db.Numeric(10, 2))
    average_confidence_score = db.Column(db.Numeric(3, 2))
    average_quality_score = db.Column(db.Numeric(3, 2))
    approval_rate = db.Column(db.Numeric(5, 2))  # percentage

    # Reviewer metrics
    total_reviewers = db.Column(db.Integer, default=0)
    average_items_per_reviewer = db.Column(db.Numeric(10, 2))
    reviewer_utilization = db.Column(db.Numeric(5, 2))  # percentage

    # AI learning metrics
    confidence_accuracy_correlation = db.Column(
        db.Numeric(5, 2)
    )  # Correlation between confidence and accuracy
    human_ai_agreement_rate = db.Column(db.Numeric(5, 2))  # Percentage of human-AI agreement
    correction_rate = db.Column(db.Numeric(5, 2))  # Percentage of items requiring correction

    # Escalation metrics
    escalated_items = db.Column(db.Integer, default=0)
    escalation_rate = db.Column(db.Numeric(5, 2))  # percentage
    average_escalation_time_hours = db.Column(db.Numeric(10, 2))

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ReviewQueueStatistics {self.item_type} - {self.date_bucket}>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "date_bucket": self.date_bucket.isoformat() if self.date_bucket else None,
            "time_bucket": self.time_bucket,
            "item_type": self.item_type,
            "total_items": self.total_items,
            "pending_items": self.pending_items,
            "in_review_items": self.in_review_items,
            "approved_items": self.approved_items,
            "rejected_items": self.rejected_items,
            "auto_approved_items": self.auto_approved_items,
            "expired_items": self.expired_items,
            "average_review_time_hours": float(self.average_review_time_hours)
            if self.average_review_time_hours
            else None,
            "average_confidence_score": float(self.average_confidence_score)
            if self.average_confidence_score
            else None,
            "average_quality_score": float(self.average_quality_score)
            if self.average_quality_score
            else None,
            "approval_rate": float(self.approval_rate) if self.approval_rate else None,
            "total_reviewers": self.total_reviewers,
            "average_items_per_reviewer": float(self.average_items_per_reviewer)
            if self.average_items_per_reviewer
            else None,
            "reviewer_utilization": float(self.reviewer_utilization)
            if self.reviewer_utilization
            else None,
            "confidence_accuracy_correlation": float(self.confidence_accuracy_correlation)
            if self.confidence_accuracy_correlation
            else None,
            "human_ai_agreement_rate": float(self.human_ai_agreement_rate)
            if self.human_ai_agreement_rate
            else None,
            "correction_rate": float(self.correction_rate) if self.correction_rate else None,
            "escalated_items": self.escalated_items,
            "escalation_rate": float(self.escalation_rate) if self.escalation_rate else None,
            "average_escalation_time_hours": float(self.average_escalation_time_hours)
            if self.average_escalation_time_hours
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ThresholdConfiguration(db.Model):
    """Threshold configuration templates for different contexts."""

    __tablename__ = "threshold_configurations"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.BigInteger, primary_key=True)
    config_name = db.Column(db.String(200), nullable=False, unique=True, index=True)
    config_type = db.Column(
        db.String(50), nullable=False
    )  # "industry", "organization", "project", "domain"

    # Threshold presets
    high_confidence_threshold = db.Column(db.Numeric(3, 2), default=0.8)
    medium_confidence_threshold = db.Column(db.Numeric(3, 2), default=0.6)
    low_confidence_threshold = db.Column(db.Numeric(3, 2), default=0.4)

    # Context-specific adjustments
    strategic_adjustment = db.Column(
        db.Numeric(3, 2), default=0.0
    )  # +/- adjustment for strategic items
    tactical_adjustment = db.Column(
        db.Numeric(3, 2), default=0.0
    )  # +/- adjustment for tactical items
    operational_adjustment = db.Column(
        db.Numeric(3, 2), default=0.0
    )  # +/- adjustment for operational items

    # Domain-specific adjustments
    business_domain_adjustment = db.Column(db.Numeric(3, 2), default=0.0)
    technology_domain_adjustment = db.Column(db.Numeric(3, 2), default=0.0)
    data_domain_adjustment = db.Column(db.Numeric(3, 2), default=0.0)
    security_domain_adjustment = db.Column(db.Numeric(3, 2), default=0.0)

    # Quality gate requirements
    requires_quality_gate = db.Column(db.Boolean, default=False)
    quality_gate_criteria = db.Column(db.Text)  # JSON with quality gate requirements

    # Review workflow
    auto_review_enabled = db.Column(db.Boolean, default=False)
    review_timeout_hours = db.Column(db.Integer, default=24)
    escalation_enabled = db.Column(db.Boolean, default=True)
    max_escalation_levels = db.Column(db.Integer, default=3)

    # Metadata
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    created_by = db.relationship("User", backref="created_threshold_configs")

    def __repr__(self):
        return f"<ThresholdConfiguration {self.config_name} ({self.config_type})>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "config_name": self.config_name,
            "config_type": self.config_type,
            "high_confidence_threshold": float(self.high_confidence_threshold),
            "medium_confidence_threshold": float(self.medium_confidence_threshold),
            "low_confidence_threshold": float(self.low_confidence_threshold),
            "strategic_adjustment": float(self.strategic_adjustment),
            "tactical_adjustment": float(self.tactical_adjustment),
            "operational_adjustment": float(self.operational_adjustment),
            "business_domain_adjustment": float(self.business_domain_adjustment),
            "technology_domain_adjustment": float(self.technology_domain_adjustment),
            "data_domain_adjustment": float(self.data_domain_adjustment),
            "security_domain_adjustment": float(self.security_domain_adjustment),
            "requires_quality_gate": self.requires_quality_gate,
            "quality_gate_criteria": json.loads(self.quality_gate_criteria)
            if self.quality_gate_criteria
            else {},
            "auto_review_enabled": self.auto_review_enabled,
            "review_timeout_hours": self.review_timeout_hours,
            "escalation_enabled": self.escalation_enabled,
            "max_escalation_levels": self.max_escalation_levels,
            "description": self.description,
            "is_active": self.is_active,
            "created_by_id": self.created_by_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
