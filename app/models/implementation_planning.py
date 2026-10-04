"""
ArchiMate 3.2 Implementation Planning Models

This module implements the ArchiMate 3.2 Implementation & Migration layer elements
for enterprise implementation and migration planning.

ArchiMate 3.2 Implementation Layer Elements:
- WorkPackage: A unit of work that can be assigned to an actor
- Deliverable: A concrete outcome of a process or work package
- ImplementationEvent: A relevant occurrence in the implementation process
- Plateau: A relatively stable state of the architecture that exists during a limited period of time
- Gap: A statement of difference between the baseline and target architectures

Complies with:
- ArchiMate 3.2 Specification (The Open Group)
- SQLAlchemy ORM patterns
- Flask application structure
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from flask import current_app
from sqlalchemy import (
    JSON,
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
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import validates

from .. import db


class ImplementationWorkPackage(db.Model):
    """
    ArchiMate 3.2 WorkPackage Element

    A work package represents a unit of work that can be assigned to an actor
    and is used to structure the implementation and migration planning.
    """

    __tablename__ = "implementation_work_packages"
    __table_args__ = {"extend_existing": True}

    # Primary Key
    id = Column(Integer, primary_key=True)

    # Core Attributes
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    documentation = Column(Text)

    # ArchiMate 3.2 Specific Attributes
    element_type = Column(String(50), default="WorkPackage")
    layer = Column(String(20), default="implementation")

    # Planning Attributes
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    duration_days = Column(Integer, default=0)
    progress_percentage = Column(Float, default=0.0)
    status = Column(String(20), default="planned")  # planned, in_progress, completed, cancelled

    # Assignment and Responsibility
    assigned_to = Column(String(255))  # Actor/team name
    priority = Column(String(10), default="medium")  # low, medium, high, critical

    # Dependencies and Relationships
    work_dependencies = Column(JSON)  # List of work package IDs this depends on
    prerequisites = Column(JSON)  # List of prerequisites

    # Cost and Resources
    estimated_cost = Column(Float, default=0.0)
    actual_cost = Column(Float, default=0.0)
    required_resources = Column(JSON)  # Resources needed

    # Risk and Issues
    risk_level = Column(String(10), default="low")  # low, medium, high, critical
    risk_mitigation = Column(Text)
    known_issues = Column(JSON)  # List of issues

    # Metadata
    properties = Column(JSON)  # Additional properties as key-value pairs
    tags = Column(JSON)  # Tags for categorization

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String(255))

    # Foreign Keys
    architecture_id = Column(Integer, ForeignKey("architecture_models.id"), nullable=True)
    parent_work_package_id = Column(
        Integer, ForeignKey("implementation_work_packages.id"), nullable=True
    )
    application_component_id = Column(
        Integer,
        ForeignKey("application_components.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # this store is retired into unified_work_packages (never dropped).
    # NULL until `merge-work-package-stores` copies the row across.
    retired_into_id = Column(
        BigInteger,
        ForeignKey("unified_work_packages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    architecture = db.relationship("ArchitectureModel", backref="planning_work_packages")
    parent_work_package = db.relationship(
        "ImplementationWorkPackage", remote_side=[id], backref="child_work_packages"
    )
    deliverables = db.relationship(
        "PlanningDeliverable", back_populates="work_package", cascade="all, delete-orphan"
    )
    application_component = db.relationship(
        "ApplicationComponent",
        backref="implementation_work_packages",
        foreign_keys=[application_component_id],
    )

    @validates("status")
    def validate_status(self, key, status):
        valid_statuses = ["planned", "in_progress", "completed", "cancelled", "on_hold"]
        if status not in valid_statuses:
            raise ValueError(f"Status must be one of: {valid_statuses}")
        return status

    @validates("priority")
    def validate_priority(self, key, priority):
        valid_priorities = ["low", "medium", "high", "critical"]
        if priority not in valid_priorities:
            raise ValueError(f"Priority must be one of: {valid_priorities}")
        return priority

    @validates("risk_level")
    def validate_risk_level(self, key, risk_level):
        valid_risk_levels = ["low", "medium", "high", "critical"]
        if risk_level not in valid_risk_levels:
            raise ValueError(f"Risk level must be one of: {valid_risk_levels}")
        return risk_level

    def calculate_duration(self):
        """Calculate duration in days between start and end dates"""
        if self.start_date and self.end_date:
            delta = self.end_date - self.start_date
            self.duration_days = delta.days
        return self.duration_days

    def is_overdue(self):
        """Check if work package is overdue"""
        if self.end_date and self.status not in ["completed", "cancelled"]:
            return datetime.utcnow() > self.end_date
        return False

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "element_type": self.element_type,
            "layer": self.layer,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "duration_days": self.duration_days,
            "progress_percentage": self.progress_percentage,
            "status": self.status,
            "assigned_to": self.assigned_to,
            "priority": self.priority,
            "estimated_cost": self.estimated_cost,
            "actual_cost": self.actual_cost,
            "risk_level": self.risk_level,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "is_overdue": self.is_overdue(),
            "work_dependencies": self.work_dependencies,
            "properties": self.properties,
        }


class PlanningDeliverable(db.Model):
    """
    ArchiMate 3.2 Deliverable Element

    A deliverable represents a concrete outcome of a process or work package.
    """

    __tablename__ = (
        "planning_deliverables"  # Changed to avoid conflict with Deliverable and FastDeliverable
    )
    __table_args__ = {"extend_existing": True}

    # Primary Key
    id = Column(Integer, primary_key=True)

    # Core Attributes
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    documentation = Column(Text)

    # ArchiMate 3.2 Specific Attributes
    element_type = Column(String(50), default="Deliverable")
    layer = Column(String(20), default="implementation")

    # Deliverable Specific Attributes
    deliverable_type = Column(String(50))  # document, software, hardware, service, etc.
    format = Column(String(50))  # PDF, DOCX, XLSX, etc.
    version = Column(String(20), default="1.0")

    # Status and Quality
    status = Column(
        String(20), default="planned"
    )  # planned, in_progress, completed, approved, rejected
    quality_status = Column(
        String(20), default="pending"
    )  # pending, approved, rejected, needs_revision

    # Delivery Information
    due_date = Column(DateTime, nullable=True)
    delivered_date = Column(DateTime, nullable=True)
    approved_date = Column(DateTime, nullable=True)

    # Approval and Review
    approved_by = Column(String(255))
    reviewers = Column(JSON)  # List of reviewers
    approval_criteria = Column(Text)  # Criteria for approval

    # Location and Access
    file_path = Column(String(500))  # Path to file if stored
    url = Column(String(500))  # URL if external
    repository = Column(String(255))  # Repository location

    # Metadata
    properties = Column(JSON)
    tags = Column(JSON)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String(255))

    # Foreign Keys
    work_package_id = Column(Integer, ForeignKey("implementation_work_packages.id"), nullable=True)
    architecture_id = Column(Integer, ForeignKey("architecture_models.id"), nullable=True)

    # Relationships
    work_package = db.relationship("ImplementationWorkPackage", back_populates="deliverables")
    architecture = db.relationship("ArchitectureModel", backref="planning_deliverables")

    @validates("status")
    def validate_status(self, key, status):
        valid_statuses = [
            "planned",
            "in_progress",
            "completed",
            "approved",
            "rejected",
            "cancelled",
        ]
        if status not in valid_statuses:
            raise ValueError(f"Status must be one of: {valid_statuses}")
        return status

    def is_overdue(self):
        """Check if deliverable is overdue"""
        if self.due_date and self.status not in ["completed", "approved", "rejected", "cancelled"]:
            return datetime.utcnow() > self.due_date
        return False

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "element_type": self.element_type,
            "layer": self.layer,
            "deliverable_type": self.deliverable_type,
            "format": self.format,
            "version": self.version,
            "status": self.status,
            "quality_status": self.quality_status,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "delivered_date": self.delivered_date.isoformat() if self.delivered_date else None,
            "approved_date": self.approved_date.isoformat() if self.approved_date else None,
            "approved_by": self.approved_by,
            "file_path": self.file_path,
            "url": self.url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "is_overdue": self.is_overdue(),
            "properties": self.properties,
        }


# Alias for backward compatibility with existing imports
Deliverable = PlanningDeliverable


class ImplementationPlateau(db.Model):
    """
    ArchiMate 3.2 Plateau Element

    A plateau represents a relatively stable state of the architecture that exists
    during a limited period of time.
    """

    __tablename__ = "implementation_plateaus"
    __table_args__ = {"extend_existing": True}

    # Primary Key
    id = Column(Integer, primary_key=True)

    # Core Attributes
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    documentation = Column(Text)

    # ArchiMate 3.2 Specific Attributes
    element_type = Column(String(50), default="Plateau")
    layer = Column(String(20), default="implementation")

    # Plateau Specific Attributes
    plateau_type = Column(String(50))  # baseline, interim, target, future

    # Time Period
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)

    # Architecture State
    architecture_snapshot = Column(JSON)  # Snapshot of architecture at this plateau
    state_description = Column(Text)  # Description of the architectural state

    # Transition Information
    transition_from_plateau_id = Column(
        Integer, ForeignKey("implementation_plateaus.id"), nullable=True
    )
    transition_to_plateau_id = Column(
        Integer, ForeignKey("implementation_plateaus.id"), nullable=True
    )
    transition_strategy = Column(Text)

    # Business Value and Benefits
    business_value = Column(Text)
    expected_benefits = Column(JSON)  # List of expected benefits
    achieved_benefits = Column(JSON)  # List of achieved benefits

    # Governance and Compliance
    compliance_status = Column(String(20), default="pending")  # pending, compliant, non_compliant
    governance_notes = Column(Text)

    # Automation Fields (from roadmap_models.py)
    stability_period = Column(Integer)  # in days
    transition_state = Column(String(100))  # current, transitional, target
    transition_type = Column(String(50))  # incremental, big_bang, phased
    auto_generated = Column(Boolean, default=False)
    generation_method = Column(String(100))
    source_scenario_id = Column(Integer)  # ID of scenario that generated this plateau
    validation_status = Column(String(20), default="pending")  # pending, validated, rejected
    validation_criteria = Column(Text)

    # Metadata
    properties = Column(JSON)
    tags = Column(JSON)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String(255))
    updated_by = Column(Integer, ForeignKey("users.id"))

    # Foreign Keys
    architecture_id = Column(Integer, ForeignKey("architecture_models.id"), nullable=True)

    # Relationships
    architecture = db.relationship("ArchitectureModel", backref="implementation_plateaus")
    transition_from = db.relationship(
        "ImplementationPlateau", remote_side=[id], foreign_keys=[transition_from_plateau_id]
    )
    transition_to = db.relationship(
        "ImplementationPlateau", remote_side=[id], foreign_keys=[transition_to_plateau_id]
    )

    @validates("plateau_type")
    def validate_plateau_type(self, key, plateau_type):
        valid_types = ["baseline", "interim", "target", "future", "current"]
        if plateau_type and plateau_type not in valid_types:
            raise ValueError(f"Plateau type must be one of: {valid_types}")
        return plateau_type

    def is_current(self):
        """Check if this plateau represents the current state"""
        now = datetime.utcnow()
        if self.start_date and self.end_date:
            return self.start_date <= now <= self.end_date
        return False

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "element_type": self.element_type,
            "layer": self.layer,
            "plateau_type": self.plateau_type,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "state_description": self.state_description,
            "business_value": self.business_value,
            "compliance_status": self.compliance_status,
            # Automation fields
            "stability_period": self.stability_period,
            "transition_state": self.transition_state,
            "transition_type": self.transition_type,
            "auto_generated": self.auto_generated,
            "generation_method": self.generation_method,
            "source_scenario_id": self.source_scenario_id,
            "validation_status": self.validation_status,
            "validation_criteria": self.validation_criteria,
            # Metadata
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "is_current": self.is_current(),
            "properties": self.properties,
        }


class ImplementationGap(db.Model):
    """
    ArchiMate 3.2 Gap Element

    A gap represents a statement of difference between the baseline and target architectures.
    """

    __tablename__ = "implementation_gaps"
    __table_args__ = {"extend_existing": True}

    # Primary Key
    id = Column(Integer, primary_key=True)

    # Core Attributes
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    documentation = Column(Text)

    # ArchiMate 3.2 Specific Attributes
    element_type = Column(String(50), default="Gap")
    layer = Column(String(20), default="implementation")

    # Gap Specific Attributes
    gap_type = Column(String(50))  # capability, application, technology, process, data

    # Gap Analysis
    baseline_state = Column(Text)  # Description of current state
    target_state = Column(Text)  # Description of desired state
    gap_description = Column(Text)  # Detailed description of the gap

    # Impact Assessment
    impact_level = Column(String(10), default="medium")  # low, medium, high, critical
    impact_description = Column(Text)
    affected_elements = Column(JSON)  # List of affected architecture elements

    # Business Context
    business_risk = Column(String(10), default="medium")  # low, medium, high, critical
    business_impact = Column(Text)
    urgency = Column(String(10), default="medium")  # low, medium, high, critical

    # Resolution Planning
    resolution_strategy = Column(Text)
    proposed_solution = Column(Text)
    required_work_packages = Column(JSON)  # List of work package IDs to address this gap

    # Status and Tracking
    status = Column(
        String(20), default="identified"
    )  # identified, analyzed, planned, in_progress, resolved, closed
    priority = Column(String(10), default="medium")  # low, medium, high, critical

    # Metrics and KPIs
    success_criteria = Column(Text)
    measurement_metrics = Column(JSON)  # Metrics to measure gap resolution

    # Metadata
    properties = Column(JSON)
    tags = Column(JSON)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String(255))
    resolved_date = Column(DateTime, nullable=True)

    # Set once this row has been merged into the one gap register (app.models.
    # implementation_migration.Gap) by app/commands/consolidate_gaps.py. NULL
    # means not yet merged; the row itself is never dropped (CLAUDE.md).
    retired_into_id = Column(Integer, ForeignKey("gaps.id", ondelete="SET NULL"), nullable=True, index=True)

    # Foreign Keys
    architecture_id = Column(Integer, ForeignKey("architecture_models.id"), nullable=True)
    baseline_plateau_id = Column(Integer, ForeignKey("implementation_plateaus.id"), nullable=True)
    target_plateau_id = Column(Integer, ForeignKey("implementation_plateaus.id"), nullable=True)

    # Relationships
    architecture = db.relationship("ArchitectureModel", backref="implementation_gaps")
    baseline_plateau = db.relationship("ImplementationPlateau", foreign_keys=[baseline_plateau_id])
    target_plateau = db.relationship("ImplementationPlateau", foreign_keys=[target_plateau_id])

    @validates("gap_type")
    def validate_gap_type(self, key, gap_type):
        valid_types = [
            "capability",
            "application",
            "technology",
            "process",
            "data",
            "organization",
            "information",
        ]
        if gap_type and gap_type not in valid_types:
            raise ValueError(f"Gap type must be one of: {valid_types}")
        return gap_type

    @validates("impact_level")
    def validate_impact_level(self, key, impact_level):
        valid_levels = ["low", "medium", "high", "critical"]
        if impact_level not in valid_levels:
            raise ValueError(f"Impact level must be one of: {valid_levels}")
        return impact_level

    @validates("status")
    def validate_status(self, key, status):
        valid_statuses = [
            "identified",
            "analyzed",
            "planned",
            "in_progress",
            "resolved",
            "closed",
            "deferred",
        ]
        if status not in valid_statuses:
            raise ValueError(f"Status must be one of: {valid_statuses}")
        return status

    def is_critical(self):
        """Check if this is a critical gap requiring immediate attention"""
        return (
            self.impact_level in ["high", "critical"]
            and self.urgency in ["high", "critical"]
            and self.status not in ["resolved", "closed"]
        )

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "element_type": self.element_type,
            "layer": self.layer,
            "gap_type": self.gap_type,
            "baseline_state": self.baseline_state,
            "target_state": self.target_state,
            "gap_description": self.gap_description,
            "impact_level": self.impact_level,
            "impact_description": self.impact_description,
            "business_risk": self.business_risk,
            "business_impact": self.business_impact,
            "urgency": self.urgency,
            "resolution_strategy": self.resolution_strategy,
            "proposed_solution": self.proposed_solution,
            "status": self.status,
            "priority": self.priority,
            "success_criteria": self.success_criteria,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "resolved_date": self.resolved_date.isoformat() if self.resolved_date else None,
            "is_critical": self.is_critical(),
            "properties": self.properties,
        }


# Import ImplementationEvent from implementation_migration to avoid duplication
from .implementation_migration import ImplementationEvent
