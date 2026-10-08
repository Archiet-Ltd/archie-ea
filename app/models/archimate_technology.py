"""
ArchiMate 3.2 Technology Layer - Missing Behavioral and Active Structure Elements

This module completes the ArchiMate 3.2 Technology Layer with behavioral elements
that were not included in the main technology_layer.py module.

Models:
- TechnologyCollaboration: Aggregate of technology nodes working together
- TechnologyFunction: Collection of technology behavior grouped by inputs/outputs/behavior
- TechnologyProcess: Sequence of technology behaviors achieving a specific result
- TechnologyInteraction: Unit of collective technology behavior performed by nodes
- TechnologyEvent: State change in technology layer
- Resource: Asset owned or controlled that can be used to achieve objectives (Strategy layer)

Design Pattern:
- Each domain model has archimate_element_id foreign key linking to ArchiMateElement
- model_id links to ArchitectureModel for model-level organization
- Auto-creates ArchiMateElement on insert via SQLAlchemy event listeners
- Follows Flask-SQLAlchemy patterns consistent with existing models
"""

from datetime import datetime

from sqlalchemy import event
from sqlalchemy.orm import relationship

from app.models.mixins import TenantMixin

from .. import db

# ============================================================================
# TechnologyCollaboration Domain Model
# ============================================================================


# ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
class TechnologyCollaborationFull(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Technology Collaboration - Aggregate of technology nodes working together

    An aggregate of two or more nodes that work together to perform collective
    technology behavior.

    Examples:
    - Mainframe Cluster
    - High Availability Database Cluster
    - Load Balanced Web Server Group
    - Kubernetes Pod Group
    - Distributed Processing Grid

    Usage:
        collaboration = TechnologyCollaborationFull(
            name="Production Database Cluster",
            description="High-availability PostgreSQL cluster with streaming replication",
            collaboration_type="Database Cluster",
            node_count=3
        )
    """

    __tablename__ = "technology_collaborations_full"

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate Integration
    archimate_id = db.Column(
        db.String(50), unique=True, index=True
    )  # External ArchiMate identifier
    model_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id"), nullable=True, index=True
    )
    layer = db.Column(db.String(50), default="technology")
    element_type = db.Column(db.String(50), default="TechnologyCollaboration")

    # Link to ArchiMate metamodel
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Technology Collaboration Specific Fields
    collaboration_type = db.Column(db.String(50))  # Cluster, Grid, Cloud, Distributed, Federated
    node_count = db.Column(db.Integer)  # Number of participating nodes

    # Additional collaboration attributes
    topology = db.Column(db.String(50))  # Star, Mesh, Ring, Hierarchical
    redundancy_mode = db.Column(db.String(50))  # Active-Active, Active-Passive, N + 1
    coordination_protocol = db.Column(db.String(100))  # Raft, Paxos, Gossip, ZAB
    service_mesh = db.Column(db.String(100))  # Istio, Linkerd, Consul Connect

    # Operational characteristics
    operational_status = db.Column(
        db.String(20), default="planned"
    )  # planned, active, degraded, failed
    health_status = db.Column(db.String(20))  # Healthy, Warning, Critical
    sla_percentage = db.Column(db.Numeric(5, 2))  # 99.99%

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture_model = relationship("ArchitectureModel", foreign_keys=[model_id])

    def __repr__(self):
        return f"<TechnologyCollaborationFull {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "archimate_id": self.archimate_id,
            "collaboration_type": self.collaboration_type,
            "node_count": self.node_count,
            "layer": self.layer,
            "element_type": self.element_type,
        }


# ============================================================================
# TechnologyFunction Domain Model
# ============================================================================


# ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
class TechnologyFunction(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Technology Function - Collection of technology behavior

    A collection of technology behavior that can be performed by a node.
    Groups technology behavior based on chosen criteria (typically input/output/behavior).

    Examples:
    - Data Processing Function
    - Network Routing Function
    - Authentication Function
    - Backup and Recovery Function
    - Load Balancing Function

    Usage:
        function = TechnologyFunction(
            name="Data Encryption Function",
            description="Provides AES - 256 encryption/decryption capabilities",
            function_type="Security",
            is_automated=True
        )
    """

    __tablename__ = "technology_functions"

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate Integration
    archimate_id = db.Column(db.String(50), unique=True, index=True)
    model_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id"), nullable=True, index=True
    )
    layer = db.Column(db.String(50), default="technology")
    element_type = db.Column(db.String(50), default="TechnologyFunction")

    # Link to ArchiMate metamodel
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Technology Function Specific Fields
    function_type = db.Column(db.String(50))  # Compute, Storage, Network, Security, Monitoring
    is_automated = db.Column(db.Boolean, default=False)

    # Additional function attributes
    function_category = db.Column(db.String(50))  # Core, Support, Management
    input_type = db.Column(db.String(100))  # Data, Events, Commands
    output_type = db.Column(db.String(100))  # Data, Events, Results
    trigger_mechanism = db.Column(db.String(50))  # Event-driven, Scheduled, On-demand, Continuous

    # Performance characteristics
    throughput_capacity = db.Column(db.String(50))  # Transactions/sec, MB/sec
    latency_requirement_ms = db.Column(db.Integer)
    scalability_mode = db.Column(db.String(30))  # Horizontal, Vertical, None

    # Node association
    performed_by_node_id = db.Column(
        db.Integer, db.ForeignKey("technology_nodes.id"), nullable=True
    )

    # Operational status
    operational_status = db.Column(db.String(20), default="active")

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture_model = relationship("ArchitectureModel", foreign_keys=[model_id])
    performed_by_node = relationship("Node", foreign_keys=[performed_by_node_id])

    def __repr__(self):
        return f"<TechnologyFunction {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "archimate_id": self.archimate_id,
            "function_type": self.function_type,
            "is_automated": self.is_automated,
            "layer": self.layer,
            "element_type": self.element_type,
        }


# ============================================================================
# TechnologyProcess Domain Model
# ============================================================================


# ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
class TechnologyProcess(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Technology Process - Sequence of technology behaviors

    A sequence of technology behaviors that achieves a specific result.
    Similar to business processes but at the technology layer.

    Examples:
    - Data Backup Process
    - Deployment Pipeline Process
    - Incident Response Process
    - Disaster Recovery Process
    - Log Aggregation Process

    Usage:
        process = TechnologyProcess(
            name="CI/CD Pipeline",
            description="Automated build, test, and deployment process",
            process_type="Deployment",
            automation_level="full"
        )
    """

    __tablename__ = "technology_processes"

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate Integration
    archimate_id = db.Column(db.String(50), unique=True, index=True)
    model_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id"), nullable=True, index=True
    )
    layer = db.Column(db.String(50), default="technology")
    element_type = db.Column(db.String(50), default="TechnologyProcess")

    # Link to ArchiMate metamodel
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Technology Process Specific Fields
    process_type = db.Column(
        db.String(50)
    )  # Deployment, Monitoring, Backup, Recovery, Provisioning
    automation_level = db.Column(db.String(20))  # manual, partial, full

    # Process attributes
    process_category = db.Column(db.String(50))  # Operations, DevOps, Security, Maintenance
    trigger_type = db.Column(db.String(50))  # Scheduled, Event-driven, Manual, Continuous
    frequency = db.Column(db.String(50))  # Real-time, Hourly, Daily, Weekly

    # Process steps/flow
    step_count = db.Column(db.Integer)
    process_flow = db.Column(db.Text)  # JSON representation of process steps

    # Duration and SLA
    expected_duration_minutes = db.Column(db.Integer)
    sla_target_minutes = db.Column(db.Integer)

    # Error handling
    error_handling_strategy = db.Column(db.String(50))  # Retry, Rollback, Alert, Skip
    rollback_capability = db.Column(db.Boolean, default=False)

    # Operational status
    operational_status = db.Column(db.String(20), default="active")
    last_execution = db.Column(db.DateTime)
    last_execution_status = db.Column(db.String(20))  # success, failed, partial

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture_model = relationship("ArchitectureModel", foreign_keys=[model_id])

    def __repr__(self):
        return f"<TechnologyProcess {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "archimate_id": self.archimate_id,
            "process_type": self.process_type,
            "automation_level": self.automation_level,
            "layer": self.layer,
            "element_type": self.element_type,
        }


# ============================================================================
# TechnologyInteraction Domain Model
# ============================================================================


# ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
class TechnologyInteraction(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Technology Interaction - Unit of collective technology behavior

    A unit of collective technology behavior performed by (a collaboration of)
    two or more nodes.

    Examples:
    - Database Replication Sync
    - Load Balancer Health Check
    - Cluster Node Heartbeat
    - Distributed Transaction
    - Service Discovery Exchange

    Usage:
        interaction = TechnologyInteraction(
            name="Primary-Replica Sync",
            description="PostgreSQL streaming replication between primary and replica",
            interaction_type="Replication",
            protocol="PostgreSQL Streaming Protocol"
        )
    """

    __tablename__ = "technology_interactions"

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate Integration
    archimate_id = db.Column(db.String(50), unique=True, index=True)
    model_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id"), nullable=True, index=True
    )
    layer = db.Column(db.String(50), default="technology")
    element_type = db.Column(db.String(50), default="TechnologyInteraction")

    # Link to ArchiMate metamodel
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Technology Interaction Specific Fields
    interaction_type = db.Column(db.String(50))  # Sync, Async, Request-Response, Publish-Subscribe
    protocol = db.Column(db.String(100))  # HTTP, gRPC, AMQP, Kafka, TCP, UDP

    # Interaction attributes
    communication_pattern = db.Column(db.String(50))  # Synchronous, Asynchronous, Fire-and-forget
    message_format = db.Column(db.String(50))  # JSON, XML, Protobuf, Binary
    exchange_pattern = db.Column(db.String(50))  # Request-Response, One-way, Duplex

    # Participating nodes
    initiator_node_id = db.Column(db.Integer, db.ForeignKey("technology_nodes.id"), nullable=True)
    responder_node_id = db.Column(db.Integer, db.ForeignKey("technology_nodes.id"), nullable=True)
    collaboration_id = db.Column(
        db.Integer, db.ForeignKey("technology_collaborations_full.id"), nullable=True
    )

    # Performance characteristics
    latency_avg_ms = db.Column(db.Integer)
    latency_p99_ms = db.Column(db.Integer)
    throughput_per_second = db.Column(db.Integer)

    # Reliability
    retry_policy = db.Column(db.String(100))  # Exponential backoff, Fixed delay, Circuit breaker
    timeout_ms = db.Column(db.Integer)

    # Security
    encryption_required = db.Column(db.Boolean, default=False)
    authentication_method = db.Column(db.String(50))  # mTLS, API Key, JWT, None

    # Operational status
    operational_status = db.Column(db.String(20), default="active")

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture_model = relationship("ArchitectureModel", foreign_keys=[model_id])
    initiator_node = relationship("Node", foreign_keys=[initiator_node_id])
    responder_node = relationship("Node", foreign_keys=[responder_node_id])
    collaboration = relationship("TechnologyCollaborationFull", foreign_keys=[collaboration_id])

    def __repr__(self):
        return f"<TechnologyInteraction {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "archimate_id": self.archimate_id,
            "interaction_type": self.interaction_type,
            "protocol": self.protocol,
            "layer": self.layer,
            "element_type": self.element_type,
        }


# ============================================================================
# TechnologyEvent Domain Model
# ============================================================================


# ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
class TechnologyEvent(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Technology Event - State change in technology layer

    A technology behavior element that denotes a state change.
    Represents significant occurrences in the technology infrastructure.

    Examples:
    - Server Startup Complete
    - Database Connection Pool Exhausted
    - Certificate Expiration Warning
    - Disk Space Threshold Exceeded
    - Container Restart Event

    Usage:
        event = TechnologyEvent(
            name="High CPU Utilization Alert",
            description="CPU utilization exceeded 90% threshold",
            event_type="Threshold",
            severity="warning"
        )
    """

    __tablename__ = "technology_events"

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate Integration
    archimate_id = db.Column(db.String(50), unique=True, index=True)
    model_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id"), nullable=True, index=True
    )
    layer = db.Column(db.String(50), default="technology")
    element_type = db.Column(db.String(50), default="TechnologyEvent")

    # Link to ArchiMate metamodel
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Technology Event Specific Fields
    event_type = db.Column(db.String(50))  # Lifecycle, Threshold, Scheduled, Error, Security
    severity = db.Column(db.String(20))  # info, warning, error, critical

    # Event attributes
    event_category = db.Column(db.String(50))  # Infrastructure, Application, Security, Performance
    event_source = db.Column(db.String(100))  # Node name, service name, etc.
    trigger_condition = db.Column(db.Text)  # Condition that triggers the event

    # Event timing
    event_frequency = db.Column(db.String(50))  # One-time, Recurring, Continuous
    expected_occurrence = db.Column(db.String(50))  # Rare, Occasional, Frequent

    # Impact
    impact_scope = db.Column(db.String(50))  # Single node, Cluster, Region, Global
    business_impact = db.Column(db.String(20))  # None, Low, Medium, High, Critical

    # Response
    requires_acknowledgment = db.Column(db.Boolean, default=False)
    auto_remediation = db.Column(db.Boolean, default=False)
    remediation_action = db.Column(db.Text)  # Description of remediation steps
    escalation_policy = db.Column(db.String(100))

    # Source node association
    source_node_id = db.Column(db.Integer, db.ForeignKey("technology_nodes.id"), nullable=True)

    # Operational status
    operational_status = db.Column(db.String(20), default="active")  # active, suppressed, disabled

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture_model = relationship("ArchitectureModel", foreign_keys=[model_id])
    source_node = relationship("Node", foreign_keys=[source_node_id])

    def __repr__(self):
        return f"<TechnologyEvent {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "archimate_id": self.archimate_id,
            "event_type": self.event_type,
            "severity": self.severity,
            "layer": self.layer,
            "element_type": self.element_type,
        }


# ============================================================================
# Resource Domain Model (Strategy Layer)
# ============================================================================


class Resource(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Resource - Asset owned or controlled to achieve objectives

    An asset owned or controlled by an individual or organization.
    Represents tangible and intangible resources needed to achieve goals.

    Examples:
    - IT Infrastructure Budget
    - Development Team
    - Cloud Computing Capacity
    - Intellectual Property Portfolio
    - Customer Data Assets

    Usage:
        resource = Resource(
            name="Cloud Infrastructure Budget",
            description="Annual budget allocation for AWS and Azure services",
            resource_type="Financial",
            cost=500000.00,
            availability="committed"
        )
    """

    __tablename__ = "archimate_resources"

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate Integration
    archimate_id = db.Column(db.String(50), unique=True, index=True)
    model_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id"), nullable=True, index=True
    )
    layer = db.Column(db.String(50), default="strategy")  # Strategy layer for Resource
    element_type = db.Column(db.String(50), default="Resource")

    # Link to ArchiMate metamodel
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Resource Specific Fields
    resource_type = db.Column(db.String(50))  # Human, Financial, Information, Technology, Physical
    cost = db.Column(db.Float)  # Associated cost or value
    availability = db.Column(db.String(20))  # available, committed, reserved, constrained

    # Resource attributes
    resource_category = db.Column(db.String(50))  # Tangible, Intangible
    ownership_type = db.Column(db.String(50))  # Owned, Leased, Shared, External
    criticality = db.Column(db.String(20))  # Low, Medium, High, Critical

    # Capacity and utilization
    capacity_unit = db.Column(db.String(50))  # FTE, USD, GB, Hours, Units
    total_capacity = db.Column(db.Float)
    allocated_capacity = db.Column(db.Float)
    available_capacity = db.Column(db.Float)
    utilization_percentage = db.Column(db.Float)

    # Financial aspects
    annual_cost = db.Column(db.Numeric(15, 2))
    replacement_cost = db.Column(db.Numeric(15, 2))
    depreciation_method = db.Column(db.String(50))  # Straight-line, Declining balance

    # Lifecycle
    acquisition_date = db.Column(db.Date)
    expiration_date = db.Column(db.Date)
    lifecycle_status = db.Column(db.String(20))  # planned, active, retiring, retired

    # Ownership
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    owning_org_unit_id = db.Column(db.Integer, db.ForeignKey("business_actors.id"), nullable=True)

    # Operational status
    operational_status = db.Column(db.String(20), default="active")

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture_model = relationship("ArchitectureModel", foreign_keys=[model_id])
    owner = relationship("User", foreign_keys=[owner_id])
    owning_org_unit = relationship("BusinessActor", foreign_keys=[owning_org_unit_id])

    def __repr__(self):
        return f"<Resource {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "archimate_id": self.archimate_id,
            "resource_type": self.resource_type,
            "cost": self.cost,
            "availability": self.availability,
            "layer": self.layer,
            "element_type": self.element_type,
        }


# ============================================================================
# Event Listeners for ArchiMate Element Auto-creation
# ============================================================================


@event.listens_for(TechnologyCollaborationFull, "before_insert")
def create_collaboration_full_archimate_element(mapper, connection, target):
    """Auto-create ArchiMateElement when TechnologyCollaborationFull is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="TechnologyCollaboration",
                layer="Technology",
                description=target.description or f"Technology Collaboration: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(TechnologyFunction, "before_insert")
def create_function_archimate_element(mapper, connection, target):
    """Auto-create ArchiMateElement when TechnologyFunction is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="TechnologyFunction",
                layer="Technology",
                description=target.description or f"Technology Function: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(TechnologyProcess, "before_insert")
def create_process_archimate_element(mapper, connection, target):
    """Auto-create ArchiMateElement when TechnologyProcess is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="TechnologyProcess",
                layer="Technology",
                description=target.description or f"Technology Process: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(TechnologyInteraction, "before_insert")
def create_interaction_archimate_element(mapper, connection, target):
    """Auto-create ArchiMateElement when TechnologyInteraction is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="TechnologyInteraction",
                layer="Technology",
                description=target.description or f"Technology Interaction: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(TechnologyEvent, "before_insert")
def create_event_archimate_element(mapper, connection, target):
    """Auto-create ArchiMateElement when TechnologyEvent is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="TechnologyEvent",
                layer="Technology",
                description=target.description or f"Technology Event: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(Resource, "before_insert")
def create_resource_archimate_element(mapper, connection, target):
    """Auto-create ArchiMateElement when Resource is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                name=target.name,
                type="Resource",
                layer="Strategy",
                description=target.description or f"Resource: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    "TechnologyCollaborationFull",
    "TechnologyFunction",
    "TechnologyProcess",
    "TechnologyInteraction",
    "TechnologyEvent",
    "Resource",
]
