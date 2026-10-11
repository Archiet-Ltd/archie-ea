"""
Enhanced Roadmap Database Models
Complete models with automation fields and relationships for CRUD operations
"""

import json
from datetime import datetime, timedelta  # dead-code-ok

from flask_sqlalchemy import SQLAlchemy  # dead-code-ok
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func  # dead-code-ok

from app import db
from app.models.mixins import TenantMixin

# Association table for work package dependencies
work_package_dependencies = Table(
    "work_package_dependencies",
    db.metadata,
    Column("work_package_id", BigInteger, ForeignKey("roadmap_work_packages.id"), primary_key=True),
    Column("dependency_id", BigInteger, ForeignKey("roadmap_work_packages.id"), primary_key=True),
    Column(
        "dependency_type", String(50), default="finish_to_start"
    ),  # finish_to_start, start_to_start, etc.
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("created_by", Integer, ForeignKey("users.id")),
)

# Association table for work package resources
work_package_resources = Table(
    "work_package_resources",
    db.metadata,
    Column("work_package_id", Integer, ForeignKey("roadmap_work_packages.id"), primary_key=True),
    Column("resource_id", Integer, ForeignKey("resources.id"), primary_key=True),
    Column("allocation_percentage", Float, default=100.0),
    Column("role", String(100)),
    Column("created_at", DateTime, default=datetime.utcnow),
)

# Association table for work package capabilities
work_package_capabilities = Table(
    "work_package_capabilities",
    db.metadata,
    Column("work_package_id", Integer, ForeignKey("roadmap_work_packages.id"), primary_key=True),
    Column("capability_id", Integer, ForeignKey("unified_capabilities.id"), primary_key=True),
    Column("contribution_type", String(50), default="supports"),  # supports, enables, delivers
    Column("weight", Float, default=1.0),
    Column("created_at", DateTime, default=datetime.utcnow),
)


class RoadmapWorkPackage(db.Model):
    """Enhanced Work Package model with automation support (renamed to avoid conflict)"""

    __tablename__ = "roadmap_work_packages"
    __table_args__ = {"extend_existing": True}

    # Core fields
    id = Column(BigInteger, primary_key=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    status = Column(String(50), nullable=False, default="planned", index=True)
    business_capability = Column(String(100), nullable=False, index=True)
    assigned_to = Column(String(255), index=True)

    # Timeline fields
    start_date = Column(DateTime, index=True)
    end_date = Column(DateTime, index=True)
    duration_days = Column(Integer)  # Calculated field

    # Progress and cost
    progress_percentage = Column(Float, default=0.0)
    estimated_cost = Column(Float)
    actual_cost = Column(Float)
    budget_variance = Column(Float)  # Calculated field

    # Priority and risk
    priority = Column(String(20), default="medium", index=True)  # low, medium, high, critical
    risk_level = Column(String(20), default="medium", index=True)
    complexity_score = Column(Float, default=1.0)

    # Automation fields
    auto_generated = Column(Boolean, default=False, index=True)
    source_data = Column(Text)  # JSON string with source information
    source_type = Column(String(50))  # capability, gap, application, manual
    source_id = Column(Integer)  # ID of source entity
    confidence_score = Column(Float, default=1.0)
    generation_method = Column(String(100))  # AI, template, rule_based

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), index=True)
    updated_by = Column(Integer, ForeignKey("users.id"))

    # Additional fields for automation
    last_sync_at = Column(DateTime)
    sync_status = Column(String(20), default="synced")  # synced, pending, error
    automation_metadata = Column(Text)  # JSON string for additional automation data

    # this store is retired into unified_work_packages (never dropped).
    # NULL until `merge-work-package-stores` copies the row across.
    retired_into_id = Column(
        BigInteger,
        ForeignKey("unified_work_packages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Set when the row is copied across (merge or bridge); survives the unified
    # copy's deletion so the row is never merged again.
    retired_at = Column(DateTime, nullable=True)

    # Relationships
    deliverables = relationship(
        "RoadmapDeliverable", back_populates="work_package", cascade="all, delete-orphan"
    )
    dependencies = relationship(
        "RoadmapWorkPackage",
        secondary=work_package_dependencies,
        primaryjoin=(work_package_dependencies.c.work_package_id == id),
        secondaryjoin=(work_package_dependencies.c.dependency_id == id),
        backref="dependents",
    )
    resources = relationship(
        "RoadmapResource", secondary=work_package_resources, backref="work_packages"
    )
    capabilities = relationship(
        "UnifiedCapability", secondary=work_package_capabilities, backref="work_packages"
    )

    # Audit relationships
    creator = relationship("User", foreign_keys=[created_by], backref="created_work_packages")
    updater = relationship("User", foreign_keys=[updated_by], backref="updated_work_packages")

    def __repr__(self):
        return f"<WorkPackage {self.name}>"

    def to_dict(self, include_relations=False):
        """Convert to dictionary with optional relationships"""
        result = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "business_capability": self.business_capability,
            "assigned_to": self.assigned_to,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "duration_days": self.duration_days,
            "progress_percentage": self.progress_percentage,
            "estimated_cost": self.estimated_cost,
            "actual_cost": self.actual_cost,
            "budget_variance": self.budget_variance,
            "priority": self.priority,
            "risk_level": self.risk_level,
            "complexity_score": self.complexity_score,
            "auto_generated": self.auto_generated,
            "source_data": self.source_data,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "confidence_score": self.confidence_score,
            "generation_method": self.generation_method,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "sync_status": self.sync_status,
        }

        if include_relations:
            result["deliverables"] = [d.to_dict() for d in self.deliverables]
            result["dependencies"] = [
                {"id": d.id, "name": d.name, "status": d.status} for d in self.dependencies
            ]
            result["capabilities"] = [{"id": c.id, "name": c.name} for c in self.capabilities]

        return result

    def calculate_duration(self):
        """Calculate duration in days"""
        if self.start_date and self.end_date:
            self.duration_days = (self.end_date - self.start_date).days + 1
            return self.duration_days
        return None

    def calculate_budget_variance(self):
        """Calculate budget variance"""
        if self.estimated_cost and self.actual_cost:
            self.budget_variance = self.actual_cost - self.estimated_cost
            return self.budget_variance
        return None

    def is_overdue(self):
        """Check if work package is overdue"""
        if self.end_date and self.status not in ["completed", "cancelled"]:
            return datetime.utcnow() > self.end_date
        return False

    def get_critical_path_impact(self):
        """Calculate impact on critical path"""
        # This would be implemented with critical path method algorithm
        return {"is_critical": False, "slack_days": 0, "impact_score": 0.0}


class RoadmapDeliverable(TenantMixin, db.Model):
    """
    Roadmap Deliverable model - ArchiMate 3.2 Deliverable aligned.
    Deliverables represent outputs/artifacts produced by work packages.
    """

    __tablename__ = "roadmap_deliverables"
    __table_args__ = {"extend_existing": True}

    # Core fields
    id = Column(BigInteger, primary_key=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    work_package_id = Column(
        BigInteger, ForeignKey("roadmap_work_packages.id"), nullable=True, index=True
    )
    status = Column(String(50), nullable=False, default="planned", index=True)

    # === Link to UnifiedWorkPackage (for capability roadmap) ===
    # Using BigInteger to match database schema, no FK constraint for flexibility
    unified_work_package_id = Column(BigInteger, nullable=True, index=True)

    # === Related Tasks (JSON list of task IDs that produce this deliverable) ===
    related_task_ids = Column(Text)  # JSON list of RoadmapTask IDs

    # Timeline
    due_date = Column(DateTime, index=True)
    delivered_date = Column(DateTime)
    review_date = Column(DateTime)

    # Quality and approval
    approval_criteria = Column(Text)
    quality_score = Column(Float, default=0.0)
    approval_status = Column(String(20), default="pending")  # pending, approved, rejected

    # === ArchiMate 3.2 Compliance - Deliverable ===
    archimate_element_type = Column(String(50), default="Deliverable")
    deliverable_type = Column(String(50))  # document, software, hardware, service, other

    # Automation fields
    auto_generated = Column(Boolean, default=False)
    source_application_id = Column(Integer, ForeignKey("application_components.id"))
    generation_method = Column(String(100))

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, nullable=True)
    updated_by = Column(Integer, nullable=True)

    # This store is retired into ``deliverables`` (never dropped): the row copied
    # there is ``retired_into_id``; ``retired_at`` survives that copy's deletion.
    retired_into_id = Column(
        Integer, ForeignKey("deliverables.id", ondelete="SET NULL"), nullable=True, index=True
    )
    retired_at = Column(DateTime, nullable=True)

    # Relationships
    work_package = relationship("RoadmapWorkPackage", back_populates="deliverables")
    source_application = relationship(
        "ApplicationComponent", backref="generated_roadmap_deliverables"
    )

    def to_dict(self, include_related_tasks=False):
        """Convert to dictionary"""
        result = {
            "id": str(self.id) if self.id else None,  # String for JavaScript BigInt safety
            "name": self.name,
            "description": self.description,
            "work_package_id": str(self.work_package_id) if self.work_package_id else None,
            "unified_work_package_id": str(self.unified_work_package_id)
            if self.unified_work_package_id
            else None,
            "status": self.status,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "delivered_date": self.delivered_date.isoformat() if self.delivered_date else None,
            "review_date": self.review_date.isoformat() if self.review_date else None,
            "approval_criteria": self.approval_criteria,
            "quality_score": self.quality_score,
            "approval_status": self.approval_status,
            "archimate_element_type": self.archimate_element_type,
            "deliverable_type": self.deliverable_type,
            "related_task_ids": self.related_task_ids,
            "auto_generated": self.auto_generated,
            "source_application_id": str(self.source_application_id)
            if self.source_application_id
            else None,
            "generation_method": self.generation_method,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_related_tasks and self.related_task_ids:
            # Parse related task IDs and fetch task info
            try:
                task_ids = json.loads(self.related_task_ids) if self.related_task_ids else []
                result["related_tasks"] = task_ids
            except (json.JSONDecodeError, TypeError):
                result["related_tasks"] = []

        return result

    def is_overdue(self):
        """Check if deliverable is overdue"""
        if self.due_date and self.status not in ["delivered", "approved"]:
            return datetime.utcnow() > self.due_date
        return False


class RoadmapGap(db.Model):
    """Roadmap Gap model (separate from Gap in implementation_migration.py)"""

    __tablename__ = "roadmap_gaps"
    __table_args__ = {"extend_existing": True}

    # Core fields
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    gap_type = Column(
        String(100), nullable=False, index=True
    )  # technology, process, skill, resource
    priority = Column(String(20), default="medium", index=True)

    # Gap analysis
    current_state = Column(Text)
    target_state = Column(Text)
    impact_assessment = Column(Text)
    risk_level = Column(String(20), default="medium")

    # Resolution
    resolution_strategy = Column(Text)
    estimated_resolution_cost = Column(Float)
    estimated_resolution_time = Column(Integer)  # in days

    # Automation fields
    auto_detected = Column(Boolean, default=False)
    detection_method = Column(String(100))  # analysis, comparison, ai_detection
    confidence_score = Column(Float, default=1.0)
    source_capability_id = Column(Integer, ForeignKey("unified_capabilities.id"))
    source_application_id = Column(Integer, ForeignKey("application_components.id"))

    # Status tracking
    status = Column(String(20), default="open", index=True)  # open, in_progress, resolved, deferred
    resolution_date = Column(DateTime)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"))
    updated_by = Column(Integer, ForeignKey("users.id"))

    # Set once this row has been merged into the one gap register (app.models.
    # implementation_migration.Gap) by app/commands/consolidate_gaps.py. NULL
    # means not yet merged; the row itself is never dropped (CLAUDE.md).
    retired_into_id = Column(Integer, ForeignKey("gaps.id", ondelete="SET NULL"), nullable=True, index=True)

    # Relationships
    source_capability = relationship("UnifiedCapability", backref="related_roadmap_gaps")
    source_application = relationship("ApplicationComponent", backref="related_roadmap_gaps")

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "gap_type": self.gap_type,
            "priority": self.priority,
            "current_state": self.current_state,
            "target_state": self.target_state,
            "impact_assessment": self.impact_assessment,
            "risk_level": self.risk_level,
            "resolution_strategy": self.resolution_strategy,
            "estimated_resolution_cost": self.estimated_resolution_cost,
            "estimated_resolution_time": self.estimated_resolution_time,
            "auto_detected": self.auto_detected,
            "detection_method": self.detection_method,
            "confidence_score": self.confidence_score,
            "source_capability_id": self.source_capability_id,
            "source_application_id": self.source_application_id,
            "status": self.status,
            "resolution_date": self.resolution_date.isoformat() if self.resolution_date else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# Import Plateau from canonical implementation_migration location
# All automation fields have been merged into the canonical class in implementation_migration.py
from app.models.implementation_migration import Plateau as ImplementationPlateau  # noqa: F401  # dead-code-ok


class RoadmapResource(db.Model):
    """Resource model for work package assignments"""

    __tablename__ = "resources"
    __table_args__ = {"extend_existing": True}

    # Core fields
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, index=True)
    email = Column(String(255), unique=True, index=True)
    role = Column(String(100), index=True)
    department = Column(String(100), index=True)

    # Capacity and availability
    capacity_percentage = Column(Float, default=100.0)  # Full-time equivalent
    skill_level = Column(String(20), default="intermediate")  # junior, intermediate, senior, expert
    hourly_rate = Column(Float)

    # Status
    is_active = Column(Boolean, default=True, index=True)
    availability_start = Column(DateTime)
    availability_end = Column(DateTime)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "department": self.department,
            "capacity_percentage": self.capacity_percentage,
            "skill_level": self.skill_level,
            "hourly_rate": self.hourly_rate,
            "is_active": self.is_active,
            "availability_start": self.availability_start.isoformat()
            if self.availability_start
            else None,
            "availability_end": self.availability_end.isoformat()
            if self.availability_end
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class RoadmapScenario(db.Model):
    """Roadmap scenario model for what-if analysis"""

    __tablename__ = "roadmap_scenarios"

    # Core fields
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    scenario_type = Column(String(50), index=True)  # baseline, optimistic, pessimistic, custom

    # Parameters
    budget_constraint = Column(Float)
    timeline_constraint = Column(Integer)  # in days
    resource_constraint = Column(Text)  # JSON string of resource constraints

    # Results
    total_work_packages = Column(Integer, default=0)
    total_cost = Column(Float, default=0.0)
    total_duration = Column(Integer, default=0)  # in days
    success_probability = Column(Float, default=0.0)

    # Status
    status = Column(String(20), default="draft", index=True)  # draft, running, completed, failed
    generated_at = Column(DateTime)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"))

    # Relationships
    work_packages = relationship(
        "RoadmapWorkPackage", secondary="scenario_work_packages", backref="scenarios"
    )

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "scenario_type": self.scenario_type,
            "budget_constraint": self.budget_constraint,
            "timeline_constraint": self.timeline_constraint,
            "resource_constraint": self.resource_constraint,
            "total_work_packages": self.total_work_packages,
            "total_cost": self.total_cost,
            "total_duration": self.total_duration,
            "success_probability": self.success_probability,
            "status": self.status,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# Association table for scenarios and work packages
scenario_work_packages = Table(
    "scenario_work_packages",
    db.metadata,
    Column("scenario_id", Integer, ForeignKey("roadmap_scenarios.id"), primary_key=True),
    Column("work_package_id", BigInteger, ForeignKey("roadmap_work_packages.id"), primary_key=True),
    Column("added_at", DateTime, default=datetime.utcnow),
)


class RoadmapAudit(db.Model):
    """Audit trail for roadmap changes"""

    __tablename__ = "roadmap_audit"

    id = Column(Integer, primary_key=True)
    entity_type = Column(
        String(50), nullable=False, index=True
    )  # work_package, deliverable, gap, plateau
    entity_id = Column(BigInteger, nullable=False, index=True)
    action = Column(String(20), nullable=False, index=True)  # create, update, delete, sync

    # Change details
    old_values = Column(Text)  # JSON string
    new_values = Column(Text)  # JSON string
    changed_fields = Column(Text)  # JSON string of field names

    # Context
    reason = Column(Text)
    batch_id = Column(String(100), index=True)  # For grouping related changes

    # Metadata
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    ip_address = Column(String(45))
    user_agent = Column(Text)

    # Relationships
    user = relationship("User", backref="roadmap_audit_entries")

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "action": self.action,
            "old_values": self.old_values,
            "new_values": self.new_values,
            "changed_fields": self.changed_fields,
            "reason": self.reason,
            "batch_id": self.batch_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "user_id": self.user_id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
        }


# Backwards-compatibility aliases (KEEP - used in 16+ files)
# These point to roadmap-specific models with automation features
PlanningDeliverable = RoadmapDeliverable
ImplementationGap = RoadmapGap
Resource = RoadmapResource
