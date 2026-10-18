"""
System Architecture Models for ArchiMate 3.2

This module contains system architecture models that enable Systems Architects
to model system boundaries, hierarchies, interfaces, and deployment.

Models:
- SystemBoundary: Defines system scope and boundaries
- SystemHierarchy: Parent-child system relationships
- SystemInterface: System-level contracts beyond ApplicationInterface
- SystemDeployment: How systems are deployed across infrastructure
- SystemLifecycle: System states (planned, active, deprecated, retired)
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import event

from app.models.mixins import TenantMixin

from .. import db


class SystemBoundary(TenantMixin, db.Model):
    """
    System Boundary model for ArchiMate 3.2.

    Defines the scope and boundaries of a system, including what components
    are included and excluded from the system.

    Examples:
    - "Customer Management System" boundary including CRM, support, and analytics
    - "Payment Processing System" boundary with payment gateway and fraud detection
    """

    __tablename__ = "system_boundaries"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"), index=True)

    # Boundary characteristics
    boundary_type = db.Column(db.String(50))  # Logical, Physical, Organizational, Technical
    scope_description = db.Column(db.Text)  # What is included in this boundary
    exclusion_description = db.Column(db.Text)  # What is explicitly excluded

    # System identification
    system_name = db.Column(db.String(255), nullable=False, index=True)
    system_type = db.Column(
        db.String(50)
    )  # Application System, Platform System, Integration System
    system_category = db.Column(db.String(50))  # Core, Supporting, Enabling

    # Boundary relationships
    parent_boundary_id = db.Column(db.Integer, db.ForeignKey("system_boundaries.id"), index=True)

    # Governance
    boundary_owner = db.Column(db.String(255))
    approval_status = db.Column(db.String(30), default="draft")  # draft, approved, deprecated

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    parent_boundary = db.relationship(
        "SystemBoundary", remote_side=[id], backref="child_boundaries"
    )
    created_by = db.relationship("User", backref="created_system_boundaries")

    def __repr__(self):
        return f"<SystemBoundary {self.name} ({self.boundary_type})>"


class SystemHierarchy(db.Model):
    """
    System Hierarchy model for parent-child system relationships.

    Enables modeling of system decomposition and hierarchical structures.

    Examples:
    - "ERP System" contains "Financial Management", "Supply Chain", "HR" subsystems
    - "Customer Platform" contains "CRM", "Support", "Analytics" systems
    """

    __tablename__ = "system_hierarchies"

    id = db.Column(db.Integer, primary_key=True)

    # System relationships
    parent_system_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=False, index=True
    )
    child_system_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=False, index=True
    )

    # Hierarchy characteristics
    relationship_type = db.Column(
        db.String(50), default="composition"
    )  # composition, aggregation, specialization
    hierarchy_level = db.Column(db.Integer)  # Depth in hierarchy (1 = top level)

    # Relationship metadata
    description = db.Column(db.Text)
    is_required = db.Column(db.Boolean, default=True)  # Is child required for parent?

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    parent_system = db.relationship(
        "ArchiMateElement", foreign_keys=[parent_system_id], backref="child_systems"
    )
    child_system = db.relationship(
        "ArchiMateElement", foreign_keys=[child_system_id], backref="parent_systems"
    )
    created_by = db.relationship("User", backref="created_system_hierarchies")

    def __repr__(self):
        return f"<SystemHierarchy {self.parent_system_id} -> {self.child_system_id}>"


class SystemInterface(db.Model):
    """
    System Interface model for system-level contracts.

    Represents interfaces at the system level, beyond individual ApplicationInterfaces.
    Defines how systems interact with each other.

    Examples:
    - "ERP Integration Interface" for system-to-system ERP communication
    - "Payment Gateway Interface" for payment system integration
    """

    __tablename__ = "system_interfaces"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"), index=True)

    # Interface characteristics
    interface_type = db.Column(
        db.String(50)
    )  # API, Message Queue, File Transfer, Database, Event Stream
    protocol = db.Column(db.String(50))  # REST, SOAP, GraphQL, AMQP, FTP, JDBC, Kafka
    data_format = db.Column(db.String(50))  # JSON, XML, CSV, Binary, Protobuf

    # System relationships
    source_system_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"), index=True)
    target_system_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"), index=True)

    # Interface specifications
    authentication_method = db.Column(db.String(50))  # OAuth2, API Key, Certificate, None
    version = db.Column(db.String(20))
    endpoint_url = db.Column(db.Text)

    # Quality attributes
    reliability = db.Column(db.String(20))  # high, medium, low
    performance_requirement = db.Column(db.Text)  # Response time, throughput requirements
    availability_sla = db.Column(db.Float)  # 99.9, 99.99, etc.

    # Governance
    interface_owner = db.Column(db.String(255))
    approval_status = db.Column(db.String(30), default="draft")

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    source_system = db.relationship(
        "ArchiMateElement",
        foreign_keys=[source_system_id],
        back_populates="system_provided_interfaces",
    )
    target_system = db.relationship(
        "ArchiMateElement",
        foreign_keys=[target_system_id],
        back_populates="system_consumed_interfaces",
    )
    created_by = db.relationship("User", backref="created_system_interfaces")

    def __repr__(self):
        return f"<SystemInterface {self.name} ({self.interface_type})>"


class SystemDeployment(db.Model):
    """
    System Deployment model for infrastructure deployment.

    Tracks how systems are deployed across infrastructure components.

    Examples:
    - "CRM System" deployed on "AWS Production Cluster"
    - "Payment System" deployed on "Azure Kubernetes Service"
    """

    __tablename__ = "system_deployments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # System relationship
    system_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=False, index=True
    )

    # Deployment characteristics
    deployment_environment = db.Column(db.String(30))  # Development, Testing, Staging, Production
    deployment_model = db.Column(db.String(50))  # On-Premise, Cloud, Hybrid, SaaS
    cloud_provider = db.Column(db.String(50))  # AWS, Azure, GCP, On-Prem

    # Infrastructure relationships
    node_id = db.Column(db.Integer, db.ForeignKey("technology_nodes.id"), index=True)
    facility_id = db.Column(
        db.Integer, db.ForeignKey("physical_facilities.id"), index=True, nullable=True
    )

    # Deployment details
    deployment_date = db.Column(db.Date)
    deployment_status = db.Column(
        db.String(30), default="planned"
    )  # planned, deploying, deployed, failed
    rollback_capability = db.Column(db.Boolean, default=True)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    system = db.relationship("ArchiMateElement", foreign_keys=[system_id], backref="deployments")
    node = db.relationship("Node", backref="system_deployments")
    facility = db.relationship("PhysicalFacility", backref="system_deployments")
    created_by = db.relationship("User", backref="created_system_deployments")

    def __repr__(self):
        return f"<SystemDeployment {self.name} ({self.deployment_environment})>"


class SystemLifecycle(db.Model):
    """
    System Lifecycle model for tracking system states.

    Manages system lifecycle states: planned, active, deprecated, retired.

    Examples:
    - "Legacy ERP System" in "deprecated" state, scheduled for retirement
    - "New CRM System" in "active" state, fully operational
    """

    __tablename__ = "system_lifecycles"

    id = db.Column(db.Integer, primary_key=True)

    # System relationship
    system_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=False, index=True, unique=True
    )

    # Lifecycle state
    current_state = db.Column(
        db.String(30), nullable=False, default="planned"
    )  # planned, active, deprecated, retired
    state_transition_date = db.Column(db.Date)  # When state was last changed

    # Lifecycle dates
    planned_date = db.Column(db.Date)
    active_date = db.Column(db.Date)
    deprecated_date = db.Column(db.Date)
    retirement_date = db.Column(db.Date)

    # Lifecycle management
    retirement_reason = db.Column(db.Text)
    replacement_system_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), index=True
    )
    migration_plan = db.Column(db.Text)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    system = db.relationship("ArchiMateElement", foreign_keys=[system_id], backref="lifecycle")
    replacement_system = db.relationship(
        "ArchiMateElement", foreign_keys=[replacement_system_id], backref="replaces_systems"
    )
    created_by = db.relationship("User", backref="created_system_lifecycles")

    def __repr__(self):
        return f"<SystemLifecycle {self.system_id} ({self.current_state})>"


# ============================================================================
# Event Listeners - Auto-create ArchiMateElements
# ============================================================================


@event.listens_for(SystemBoundary, "before_insert")
def create_boundary_archimate_element(mapper, connection, target):
    """Auto-create ArchiMateElement when SystemBoundary is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                name=target.name,
                type="Grouping",  # SystemBoundary maps to Grouping in ArchiMate
                layer="Application",
                description=target.description or f"System boundary: {target.system_name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(SystemInterface, "before_insert")
def create_interface_archimate_element(mapper, connection, target):
    """Auto-create ArchiMateElement when SystemInterface is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                name=target.name,
                type="ApplicationInterface",
                layer="Application",
                description=target.description or f"System interface: {target.interface_type}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]
