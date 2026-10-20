"""
Unified Duplicate Detection Models

Consolidated data models for duplicate detection following architectural best practices:
- Single unified model instead of fragmented simple/enhanced models
- Strategy-agnostic storage
- Full audit trail and workflow support
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Boolean, Column, DateTime
from sqlalchemy import Enum as SQLEnum  # dead-code-ok
from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship, validates

from .. import db
from ..models.mixins.core import TenantMixin


class DetectionStrategy(str, Enum):
    """Available detection strategies"""

    FAST = "fast"
    HYBRID = "hybrid"
    ENHANCED = "enhanced"


class DuplicateType(str, Enum):
    """Types of duplicates detected"""

    EXACT = "exact"
    FUZZY = "fuzzy"
    FUNCTIONAL = "functional"
    TECHNICAL = "technical"
    CAPABILITY = "capability"


class GroupStatus(str, Enum):
    """Workflow status for duplicate groups"""

    PENDING = "pending"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    RESOLVING = "resolving"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class ResolutionAction(str, Enum):
    """Actions taken to resolve duplicate groups"""

    KEEP_PRIMARY = "keep_primary"
    MERGE = "merge"
    RETIRE_ALL = "retire_all"
    NO_ACTION = "no_action"


# Association table for unified duplicate group members
unified_group_members = db.Table(
    "unified_group_members",
    Column(
        "group_id",
        Integer,
        ForeignKey("unified_duplicate_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "application_id",
        Integer,
        ForeignKey("application_components.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("similarity_to_primary", Float, default=0.0),
    Column("is_primary", Boolean, default=False),
    Column("added_at", DateTime, default=datetime.utcnow),
    extend_existing=True,
)


class UnifiedDetectionRun(TenantMixin, db.Model):
    """
    Tracks individual detection run executions.
    Strategy-agnostic - stores results from any detection method.

    TenantMixin declares organization_id NOT NULL, but this is an existing
    table and reconcile-schema can only ADD nullable columns (ADR 0002).
    Override to nullable so the column can land on deployed databases
    without a maintenance window.  Legacy rows with no known owner stay NULL
    and are hidden from every organisation by the tenant filter.
    """

    __tablename__ = "unified_detection_runs"
    __table_args__ = {"extend_existing": True}

    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    id = Column(Integer, primary_key=True)

    # Run identification
    run_name = Column(String(256), nullable=False)
    description = Column(Text)

    # Strategy used
    strategy = Column(String(20), nullable=False, default=DetectionStrategy.FAST.value)

    # Configuration
    similarity_threshold = Column(Float, default=0.55)
    config = Column(JSON, default=dict)  # Strategy-specific configuration

    # Execution status
    status = Column(String(20), default="pending")  # pending, running, completed, failed
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    duration_seconds = Column(Integer)

    # Results summary
    applications_analyzed = Column(Integer, default=0)
    groups_found = Column(Integer, default=0)
    exact_matches = Column(Integer, default=0)
    fuzzy_matches = Column(Integer, default=0)
    estimated_savings = Column(Float, default=0.0)

    # Error handling
    error_message = Column(Text)

    # Audit
    triggered_by = Column(String(50), default="user")  # user, scheduled, api
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    groups = relationship(
        "UnifiedDuplicateGroup", back_populates="detection_run", cascade="all, delete-orphan"
    )

    @validates("strategy")
    def validate_strategy(self, key, value):
        valid_strategies = [s.value for s in DetectionStrategy]
        if value not in valid_strategies:
            raise ValueError(f"Invalid strategy: {value}. Must be one of {valid_strategies}")
        return value

    @validates("status")
    def validate_status(self, key, value):
        valid_statuses = ["pending", "running", "completed", "failed"]
        if value not in valid_statuses:
            raise ValueError(f"Invalid status: {value}. Must be one of {valid_statuses}")
        return value

    def to_dict(self):
        return {
            "id": self.id,
            "run_name": self.run_name,
            "strategy": self.strategy,
            "status": self.status,
            "similarity_threshold": self.similarity_threshold,
            "applications_analyzed": self.applications_analyzed,
            "groups_found": self.groups_found,
            "exact_matches": self.exact_matches,
            "fuzzy_matches": self.fuzzy_matches,
            "estimated_savings": self.estimated_savings,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "triggered_by": self.triggered_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UnifiedDuplicateGroup(TenantMixin, db.Model):
    """
    Represents a group of duplicate applications.
    Supports full workflow from detection to resolution.

    TenantMixin declares organization_id NOT NULL, but this is an existing
    table and reconcile-schema can only ADD nullable columns (ADR 0002).
    Override to nullable so the column can land on deployed databases
    without a maintenance window.  Legacy rows with no known owner stay NULL
    and are hidden from every organisation by the tenant filter.
    """

    __tablename__ = "unified_duplicate_groups"
    __table_args__ = {"extend_existing": True}

    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    id = Column(Integer, primary_key=True)

    # Group identification
    name = Column(String(256), nullable=False)
    description = Column(Text)

    # Detection context
    detection_run_id = Column(
        Integer, ForeignKey("unified_detection_runs.id", ondelete="CASCADE"), nullable=True
    )
    duplicate_type = Column(String(20), default=DuplicateType.FUZZY.value)

    # Similarity metrics
    similarity_score = Column(Float, nullable=False, default=0.0)
    similarity_threshold = Column(Float, nullable=False, default=0.8)
    match_details = Column(JSON, default=dict)  # Detailed breakdown of similarity factors

    # Workflow status
    status = Column(String(20), default=GroupStatus.PENDING.value)
    resolution_action = Column(String(20), nullable=True)
    resolution_notes = Column(Text)
    resolved_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at = Column(DateTime)

    # Business impact
    estimated_savings = Column(Float, default=0.0)
    risk_level = Column(String(20), default="medium")  # low, medium, high

    # Audit
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    detection_run = relationship("UnifiedDetectionRun", back_populates="groups")
    applications = relationship(
        "ApplicationComponent",
        secondary=unified_group_members,
        backref=db.backref("unified_duplicate_groups", lazy="dynamic"),
        lazy="dynamic",
    )

    @validates("duplicate_type")
    def validate_duplicate_type(self, key, value):
        valid_types = [t.value for t in DuplicateType]
        if value not in valid_types:
            raise ValueError(f"Invalid duplicate_type: {value}. Must be one of {valid_types}")
        return value

    @validates("status")
    def validate_status(self, key, value):
        valid_statuses = [s.value for s in GroupStatus]
        if value not in valid_statuses:
            raise ValueError(f"Invalid status: {value}. Must be one of {valid_statuses}")
        return value

    @validates("similarity_score")
    def validate_similarity(self, key, value):
        if value is not None and (value < 0 or value > 1):
            raise ValueError(f"similarity_score must be between 0 and 1, got {value}")
        return value

    def get_primary_application(self):
        """Get the primary application in this group"""
        from sqlalchemy import select

        stmt = select(unified_group_members.c.application_id).where(
            unified_group_members.c.group_id == self.id, unified_group_members.c.is_primary == True
        )
        result = db.session.execute(stmt).first()  # tenant-filtered: scoped via parent FK (group_id)
        if result:
            from .application_portfolio import ApplicationComponent

            return ApplicationComponent.query.get(result[0])
        return None

    def get_member_count(self):
        """Get the number of applications in this group"""
        return self.applications.count()

    def to_dict(self, include_apps=False):
        result = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "duplicate_type": self.duplicate_type,
            "similarity_score": self.similarity_score,
            "status": self.status,
            "resolution_action": self.resolution_action,
            "estimated_savings": self.estimated_savings,
            "risk_level": self.risk_level,
            "member_count": self.get_member_count(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

        if include_apps:
            result["applications"] = [
                {
                    "id": app.id,
                    "name": app.name,
                    "vendor_name": app.vendor_name,
                    "deployment_status": app.deployment_status,
                    "lifecycle_status": app.lifecycle_status,
                    "imported_capabilities": getattr(app, "imported_capabilities", None),
                    "imported_processes": getattr(app, "imported_processes", None),
                    "imported_apqc_codes": getattr(app, "imported_apqc_codes", None),
                }
                for app in self.applications.all()
            ]

        return result


class DetectionSchedule(db.Model):
    """
    Scheduled detection runs for automated scanning.
    """

    __tablename__ = "detection_schedules"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)

    name = Column(String(256), nullable=False)
    description = Column(Text)

    # Schedule configuration
    is_active = Column(Boolean, default=True)
    cron_expression = Column(String(100))  # e.g., "0 2 * * *" for daily at 2am

    # Detection settings
    strategy = Column(String(20), default=DetectionStrategy.FAST.value)
    similarity_threshold = Column(Float, default=0.55)

    # Notification settings
    notify_on_completion = Column(Boolean, default=True)
    notification_emails = Column(JSON, default=list)

    # Tracking
    last_run_at = Column(DateTime)
    next_run_at = Column(DateTime)
    run_count = Column(Integer, default=0)

    # Audit
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "is_active": self.is_active,
            "cron_expression": self.cron_expression,
            "strategy": self.strategy,
            "similarity_threshold": self.similarity_threshold,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "run_count": self.run_count,
        }
