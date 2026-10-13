"""
Consolidation List Model

Simple model for tracking applications marked for consolidation.
Allows users to build a list of applications to consolidate, then plan actions
like decommissioning, retirement, or adding to roadmap.
"""

import enum
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text

from .. import db
from ..models.mixins.core import TenantMixin


# 7-stage consolidation lifecycle
CONSOLIDATION_STATUSES = [
    "identified", "under_review", "impact_assessed",
    "migration_planned", "approved", "in_progress", "completed",
]
CONSOLIDATION_STATUS_LABELS = {
    "identified": "Identified", "under_review": "Under Review",
    "impact_assessed": "Impact Assessed", "migration_planned": "Migration Planned",
    "approved": "Approved", "in_progress": "In Progress", "completed": "Completed",
}
# Map legacy status values to new lifecycle
CONSOLIDATION_STATUS_MAP = {"pending": "identified", "cancelled": "cancelled"}


class ConsolidationAction(enum.Enum):
    """Actions that can be taken on consolidated applications"""

    DECOMMISSION = "decommission"
    RETIRE = "retire"
    MERGE = "merge"
    REPLACE = "replace"
    MODERNIZE = "modernize"
    ADD_TO_ROADMAP = "add_to_roadmap"
    PENDING_REVIEW = "pending_review"


class ConsolidationListEntry(TenantMixin, db.Model):
    """
    Entry in the consolidation list - represents an application marked for consolidation.

    Users can add applications from duplicate groups to this list, then plan actions
    like decommissioning, retirement, or adding to roadmap.

    TenantMixin declares organization_id NOT NULL, but this is an existing
    table and reconcile-schema can only ADD nullable columns (ADR 0002).
    Override to nullable so the column can land on deployed databases
    without a maintenance window.  Legacy rows with no known owner stay NULL
    and are hidden from every organisation by the tenant filter.
    """

    __tablename__ = "consolidation_list_entries"

    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    id = Column(Integer, primary_key=True)

    # Application reference
    application_id = Column(
        Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )

    # Source information (where did this come from?)
    source_group_id = Column(
        Integer, nullable=True
    )  # ID of duplicate group (if from duplicate detection)
    source_group_name = Column(String(255))  # Name of duplicate group
    source_type = Column(
        String(50), default="duplicate_detection"
    )  # duplicate_detection, manual, etc.

    # Consolidation details
    # Use String instead of Enum for CockroachDB compatibility
    recommended_action = Column(String(50), default="pending_review")
    priority = Column(String(20), default="medium")  # critical, high, medium, low
    estimated_savings = Column(db.Numeric(15, 2))  # Annual savings if consolidated
    consolidation_complexity = Column(String(20))  # low, medium, high, critical

    # Planning & roadmap
    target_quarter = Column(String(20))  # Q1 2024, Q2 2024, etc.
    target_date = Column(db.Date)  # Specific target date
    roadmap_item_id = Column(Integer, nullable=True)  # Link to roadmap if added
    decommission_date = Column(db.Date, nullable=True)  # Planned decommission date
    retirement_date = Column(db.Date, nullable=True)  # Planned retirement date

    # Notes and rationale
    notes = Column(Text)
    business_rationale = Column(Text)  # Why consolidate this app?
    risk_assessment = Column(Text)
    dependencies = Column(JSON)  # JSON array of dependencies/blockers

    # Status tracking
    status = Column(
        String(30), default="pending", index=True
    )  # pending, approved, in_progress, completed, cancelled
    assigned_to = Column(String(200))  # Person/team responsible
    approved_by = Column(String(200))
    approved_at = Column(DateTime, nullable=True)

    # Consolidation target
    target_application_id = Column(
        Integer, db.ForeignKey("application_components.id"), nullable=True
    )

    # Financial
    migration_cost = Column(db.Numeric(15, 2), nullable=True)
    savings_verified = Column(db.Boolean, default=False)

    # Classification
    wave = Column(Integer, nullable=True)
    migration_complexity = Column(String(20), nullable=True)  # low, medium, high
    data_disposition = Column(String(20), nullable=True)  # migrate, archive, delete, retain
    regulatory_flags = Column(JSON, nullable=True)  # ["GDPR", "SOX", ...]

    # Status audit trail
    status_changed_at = Column(DateTime, nullable=True)
    status_changed_by = Column(String(200), nullable=True)

    # Metadata
    added_by = Column(String(200))
    added_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    application = db.relationship(
        "ApplicationComponent", foreign_keys=[application_id],
        backref="consolidation_list_entries",
    )
    target_application = db.relationship(
        "ApplicationComponent", foreign_keys=[target_application_id],
    )

    def __repr__(self):
        return f'<ConsolidationListEntry App:{self.application_id} Action:{self.recommended_action or "pending"}>'

    def to_dict(self):
        """Convert to dictionary for API responses"""
        app = self.application
        status = CONSOLIDATION_STATUS_MAP.get(self.status, self.status) or "identified"
        # M4: this used to say "Unknown" for a row whose application_id points
        # at a since-deleted ApplicationComponent (an orphaned FK) -- reading
        # identically to a real application that happens to have no name, or to
        # any other data gap. `application_orphaned` lets the UI flag this row
        # distinctly rather than folding "the reference is broken" into the
        # same "Unknown" text as "the value was never set".
        return {
            "id": self.id,
            "application_id": self.application_id,
            "application_orphaned": app is None,
            "application_name": (
                app.name if app else f"Deleted application (#{self.application_id})"
            ),
            "application_owner": getattr(app, "application_owner", None) if app else None,
            "business_domain": getattr(app, "business_domain", None) if app else None,
            "business_criticality": getattr(app, "business_criticality", None) if app else None,
            "user_count": getattr(app, "user_count", None) if app else None,
            "source_group_id": self.source_group_id,
            "source_group_name": self.source_group_name,
            "source_type": self.source_type,
            "recommended_action": self.recommended_action or None,
            "priority": self.priority,
            "estimated_savings": float(self.estimated_savings) if self.estimated_savings else 0,
            "savings_verified": self.savings_verified or False,
            "migration_cost": float(self.migration_cost) if self.migration_cost else None,
            "migration_complexity": self.migration_complexity,
            "wave": self.wave,
            "status": status,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "target_application_id": self.target_application_id,
            "target_application_name": (
                self.target_application.name if self.target_application else None
            ),
            "notes": self.notes,
            "business_rationale": self.business_rationale,
            "risk_assessment": self.risk_assessment,
            "regulatory_flags": self.regulatory_flags,
            "data_disposition": self.data_disposition,
            "assigned_to": self.assigned_to,
            "roadmap_item_id": self.roadmap_item_id,
            "added_at": self.added_at.isoformat() if self.added_at else None,
        }
