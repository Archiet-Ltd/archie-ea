"""
ArchiMate 3.2 Application Layer Domain Models

Comprehensive domain models for Application Layer elements with rich integration
and operational attributes for Integration Architecture and Solution Architecture.

Design Pattern:
- Each domain model has archimate_element_id foreign key linking to ArchiMateElement
- Domain models contain integration-specific attributes (100+ fields)
- ArchiMateElement provides metamodel compliance and relationship tracking
- Auto-creates ArchiMateElement on insert via SQLAlchemy event listeners

Models:
- ApplicationInterface: API catalog (REST/SOAP/GraphQL, OpenAPI specs, authentication)
- ApplicationEvent: Event-driven architecture (pub/sub, event sourcing, AsyncAPI)
- ApplicationCollaboration: Microservices patterns and service mesh
"""

import json
import logging
from datetime import date, datetime
from decimal import Decimal  # dead-code-ok

from sqlalchemy import CheckConstraint, event
from sqlalchemy.orm import relationship, validates

from .. import db
from .mixins import TenantMixin

logger = logging.getLogger(__name__)

# Re-export ApplicationComponent from application_portfolio for backward compatibility
# Many modules import ApplicationComponent from here; this re-export maintains compatibility
from .application_portfolio import ApplicationComponent  # noqa: F401
from .relationship_tables import application_roadmap_items, portfolio_initiative_applications  # dead-code-ok

# ============================================================================
# ApplicationInterface Domain Model
# ============================================================================


class ApplicationInterface(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Application Interface - Point of access where services are made available

    Represents REST APIs, SOAP services, GraphQL endpoints, message queues, file interfaces.
    Extends ArchiMate with Example Corp UK API catalog and integration attributes.

    Examples:
    - Customer API (REST, OAuth2, 99.9% SLA, 300 consumers)
    - ERP SOAP Service (Legacy, certificate auth, business hours)
    - Inventory GraphQL API (Real-time, API key, high volume)
    - Order Queue (AMQP, async, guaranteed delivery)

    Usage:
        interface = ApplicationInterface(
            name="Customer API v2",
            interface_type="REST",
            protocol="HTTPS",
            authentication_method="OAuth2",
            openapi_spec_url="https://api.example.com/openapi.json",
            sla_response_time_ms=500,
            sla_availability_percentage=99.9
        )
    """

    __tablename__ = "application_interfaces"

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # Link to ArchiMate metamodel
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Application association (used by /applications/<id> views)
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True, index=True
    )

    # Interface Classification
    interface_type = db.Column(
        db.String(50), index=True
    )  # REST, SOAP, GraphQL, gRPC, Message Queue, Event Stream, File Transfer, Database
    protocol = db.Column(db.String(50))  # HTTP, HTTPS, TCP, AMQP, MQTT, STOMP, FTP, SFTP, JDBC
    data_format = db.Column(db.String(50))  # JSON, XML, CSV, Avro, Protobuf, EDI, Parquet
    message_pattern = db.Column(
        db.String(50)
    )  # Request-Response, Fire-and-Forget, Pub-Sub, Streaming, Batch

    # Endpoint Details
    base_url = db.Column(db.String(500))  # https://api.example.com/v2
    endpoint_path = db.Column(db.String(500))  # /customers/{id}
    http_methods = db.Column(db.String(100))  # GET, POST, PUT, DELETE
    port_number = db.Column(db.Integer)

    # Provider & Consumer
    provider_application_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True
    )  # ApplicationComponent
    provider_team = db.Column(db.String(100))
    consumer_count = db.Column(db.Integer, default=0)  # Number of consuming systems
    internal_external = db.Column(db.String(20))  # Internal, External-Partner, External-Public

    # Security & Authentication
    authentication_method = db.Column(
        db.String(50)
    )  # OAuth2, API Key, Basic Auth, Certificate, SAML, JWT, Kerberos, None
    authorization_model = db.Column(db.String(50))  # RBAC, ABAC, ACL, Scope-based, None
    encryption_in_transit = db.Column(db.Boolean, default=True)
    encryption_at_rest = db.Column(db.Boolean, default=False)
    tls_version = db.Column(db.String(20))  # TLS 1.3, TLS 1.2
    certificate_expiry_date = db.Column(db.Date, nullable=True)

    # API Contract & Documentation
    openapi_spec_url = db.Column(db.String(500))  # OpenAPI 3.x specification URL
    openapi_spec = db.Column(db.Text)  # Full OpenAPI specification (JSON/YAML)
    asyncapi_spec = db.Column(db.Text)  # AsyncAPI specification for async interfaces
    graphql_schema = db.Column(db.Text)  # GraphQL schema definition
    wsdl_url = db.Column(db.String(500))  # WSDL URL for SOAP services
    documentation_url = db.Column(db.String(500))  # Developer portal link
    swagger_ui_url = db.Column(db.String(500))  # Interactive API docs

    # Service Level Agreement (SLA)
    sla_response_time_ms = db.Column(db.Integer)  # Target response time in milliseconds
    sla_availability_percentage = db.Column(db.Numeric(5, 2))  # e.g., 99.9%
    sla_uptime_hours = db.Column(db.String(50))  # "24/7", "Business Hours 8am - 6pm"
    max_throughput_tps = db.Column(db.Integer)  # Transactions per second capacity
    rate_limit_per_minute = db.Column(db.Integer)  # API rate limit
    rate_limit_per_day = db.Column(db.Integer)

    # Operational Characteristics
    is_synchronous = db.Column(db.Boolean, default=True)
    supports_retry = db.Column(db.Boolean, default=False)
    idempotent = db.Column(db.Boolean, default=False)  # Can safely retry without side effects
    timeout_seconds = db.Column(db.Integer)
    circuit_breaker_enabled = db.Column(db.Boolean, default=False)

    # Data Governance
    data_classification = db.Column(db.String(50))  # Public, Internal, Confidential, Restricted
    contains_pii = db.Column(db.Boolean, default=False)
    pii_fields = db.Column(db.Text)  # JSON array of PII field names
    gdpr_scope = db.Column(db.Boolean, default=False)
    data_residency_requirements = db.Column(db.String(200))  # e.g., "EU only", "UK only"

    # Versioning & Lifecycle
    version = db.Column(db.String(20), index=True)  # v1, v2, v2.1
    api_version_strategy = db.Column(db.String(50))  # URL Path, Header, Query Parameter
    operational_status = db.Column(
        db.String(20), default="planned"
    )  # planned, development, testing, production, deprecated, retired
    go_live_date = db.Column(db.Date)
    deprecation_date = db.Column(db.Date, nullable=True)
    retirement_date = db.Column(db.Date, nullable=True)
    breaking_changes = db.Column(db.Text)  # JSON array of breaking changes per version

    # Monitoring & Health
    monitoring_enabled = db.Column(db.Boolean, default=False)
    health_check_url = db.Column(db.String(500))
    health_check_interval_seconds = db.Column(db.Integer, default=60)
    last_health_check = db.Column(db.DateTime)
    health_status = db.Column(db.String(20))  # Healthy, Degraded, Unhealthy, Unknown

    # Performance Metrics (Actual Measured)
    current_uptime_percentage = db.Column(db.Numeric(5, 2))
    average_response_time_ms = db.Column(db.Integer)
    p95_response_time_ms = db.Column(db.Integer)
    p99_response_time_ms = db.Column(db.Integer)
    error_rate_percentage = db.Column(db.Numeric(5, 2))
    daily_request_count = db.Column(db.BigInteger)

    # Business Context
    business_criticality = db.Column(db.String(50))  # Critical, High, Medium, Low
    business_domain = db.Column(db.String(100))  # Order Management, Inventory, Customer, Finance
    transaction_volume_daily = db.Column(db.BigInteger)
    peak_usage_hours = db.Column(db.String(100))  # "9am - 11am, 2pm - 4pm"

    # Cost & Commercial
    cost_per_call = db.Column(db.Numeric(10, 6))  # Cost per API call
    monthly_cost = db.Column(db.Numeric(12, 2))  # Hosting/infrastructure cost
    is_monetized = db.Column(db.Boolean, default=False)  # Revenue-generating API?
    pricing_tier = db.Column(db.String(50))  # Free, Basic, Premium, Enterprise

    # Integration Patterns
    integration_pattern = db.Column(db.String(50))  # Point-to-Point, API Gateway, Service Mesh, ESB
    api_gateway = db.Column(db.String(100))  # AWS API Gateway, Kong, Apigee
    load_balancer = db.Column(db.String(100))
    cache_enabled = db.Column(db.Boolean, default=False)
    cache_ttl_seconds = db.Column(db.Integer)

    # Testing & Quality
    test_coverage_percentage = db.Column(db.Numeric(5, 2))
    automated_tests = db.Column(db.Boolean, default=False)
    contract_testing_enabled = db.Column(db.Boolean, default=False)  # Pact, Spring Cloud Contract

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.String(100))

    # Relationships
    archimate_element = db.relationship(
        "ArchiMateElement", foreign_keys=[archimate_element_id], backref="application_interface"
    )
    provider_application = db.relationship(
        "ArchiMateElement", foreign_keys=[provider_application_id], backref="provided_interfaces"
    )

    # Application Component relationship (direct foreign key)
    component = db.relationship(
        "ApplicationComponent", foreign_keys=[application_component_id], backref="direct_interfaces"
    )

    # Application Component relationships (via junction table for many-to-many)
    components = db.relationship(
        "ApplicationComponent",
        secondary="application_interface_mapping",
        back_populates="interfaces",
    )

    # Helper Methods
    @property
    def sla_compliance_percentage(self):
        """Calculate SLA compliance"""
        if self.sla_response_time_ms and self.average_response_time_ms:
            if self.average_response_time_ms <= self.sla_response_time_ms:
                return 100.0
            return (float(self.sla_response_time_ms) / self.average_response_time_ms) * 100
        return None

    @property
    def is_sla_breach(self):
        """Check if SLA is breached"""
        if self.sla_response_time_ms and self.average_response_time_ms:
            return self.average_response_time_ms > self.sla_response_time_ms
        if self.sla_availability_percentage and self.current_uptime_percentage:
            return self.current_uptime_percentage < self.sla_availability_percentage
        return False

    @property
    def monthly_call_volume(self):
        """Estimate monthly call volume"""
        if self.daily_request_count:
            return self.daily_request_count * 30
        return 0

    @property
    def is_deprecated(self):
        """Check if API is deprecated"""
        return self.operational_status == "deprecated"

    @property
    def days_until_retirement(self):
        """Days until API retirement"""
        if self.retirement_date:
            delta = self.retirement_date - date.today()
            return delta.days
        return None

    def get_pii_fields_list(self):
        """Parse PII fields from JSON"""
        if self.pii_fields:
            try:
                return json.loads(self.pii_fields)
            except (ValueError, KeyError, TypeError):
                return []
        return []

    # ========================================================================
    # VALIDATION RULES (ArchiMate 3.2 Compliance + Business Rules)
    # ========================================================================

    __table_args__ = (
        CheckConstraint(
            "sla_availability_percentage IS NULL OR (sla_availability_percentage >= 0 AND sla_availability_percentage <= 100)",
            name="check_sla_availability_percentage",
        ),
        CheckConstraint(
            "sla_response_time_ms IS NULL OR sla_response_time_ms > 0",
            name="check_sla_response_time_positive",
        ),
        CheckConstraint(
            "port_number IS NULL OR (port_number >= 1 AND port_number <= 65535)",
            name="check_port_number_range",
        ),
        CheckConstraint("consumer_count >= 0", name="check_consumer_count_positive"),
        CheckConstraint(
            "rate_limit_per_minute IS NULL OR rate_limit_per_minute > 0",
            name="check_rate_limit_positive",
        ),
    )

    @validates("interface_type")
    def validate_interface_type(self, key, value):
        """Validate interface_type against ArchiMate-compliant values"""
        valid_types = [
            "REST",
            "SOAP",
            "GraphQL",
            "gRPC",
            "Message Queue",
            "Event Stream",
            "File Transfer",
            "Database",
            "WebSocket",
            "RPC",
        ]
        if value and value not in valid_types:
            raise ValueError(f"Invalid interface_type: {value}. Must be one of {valid_types}")
        return value

    @validates("protocol")
    def validate_protocol(self, key, value):
        """Validate protocol"""
        valid_protocols = [
            "HTTP",
            "HTTPS",
            "TCP",
            "UDP",
            "AMQP",
            "MQTT",
            "STOMP",
            "FTP",
            "SFTP",
            "JDBC",
            "WS",
            "WSS",
        ]
        if value and value not in valid_protocols:
            raise ValueError(f"Invalid protocol: {value}. Must be one of {valid_protocols}")
        return value

    @validates("operational_status")
    def validate_operational_status(self, key, value):
        """Validate operational status"""
        valid_statuses = ["Active", "Deprecated", "Beta", "Alpha", "Retired", "Planned"]
        if value and value not in valid_statuses:
            raise ValueError(
                f"Invalid operational_status: {value}. Must be one of {valid_statuses}"
            )
        return value

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": self.id,
            "name": self.name,
            "interface_type": self.interface_type,
            "version": self.version,
            "base_url": self.base_url,
            "operational_status": self.operational_status,
            "sla_response_time_ms": self.sla_response_time_ms,
            "consumer_count": self.consumer_count,
        }

    def __repr__(self):
        return f"<ApplicationInterface {self.name} {self.version} ({self.interface_type})>"


# ============================================================================
# ApplicationEvent Domain Model
# ============================================================================


class ApplicationEvent(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Application Event - Application state change

    Represents events in event-driven architecture (EDA), pub/sub patterns, event sourcing.
    Extends ArchiMate with Example Corp UK event integration attributes.

    Examples:
    - OrderCreated (Kafka topic, 10k/day, critical)
    - InventoryLevelChanged (RabbitMQ, real-time, high volume)
    - QualityCheckCompleted (EventBridge, async, standard priority)
    - CustomerUpdated (Event sourcing, GDPR scope)

    Usage:
        event = ApplicationEvent(
            name="OrderCreated",
            event_type="Domain Event",
            message_broker="Kafka",
            topic_name="orders.created.v1",
            schema_registry_enabled=True,
            delivery_guarantee="At-least-once"
        )
    """

    __tablename__ = "application_events"

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # Link to ArchiMate metamodel
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Application association
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True, index=True
    )

    # Event Classification
    event_type = db.Column(
        db.String(50)
    )  # Domain Event, Integration Event, System Event, Business Event
    event_category = db.Column(db.String(100))  # Order, Customer, Inventory, Manufacturing, Quality
    trigger_type = db.Column(db.String(50))  # Automatic, Manual, Scheduled, External

    # Message Broker & Infrastructure
    message_broker = db.Column(
        db.String(50)
    )  # Kafka, RabbitMQ, AWS EventBridge, Azure Event Hub, Google Pub/Sub
    topic_name = db.Column(db.String(255), index=True)  # Kafka topic, RabbitMQ queue
    exchange_name = db.Column(db.String(255))  # RabbitMQ exchange
    routing_key = db.Column(db.String(255))  # RabbitMQ routing key
    partition_key = db.Column(db.String(100))  # Kafka partition key strategy

    # Event Schema & Contract
    schema_format = db.Column(db.String(50))  # JSON Schema, Avro, Protobuf, XML Schema
    schema_registry_enabled = db.Column(db.Boolean, default=False)
    schema_version = db.Column(db.String(20))
    asyncapi_spec = db.Column(db.Text)  # AsyncAPI specification
    event_schema = db.Column(db.Text)  # Full schema definition
    example_payload = db.Column(db.Text)  # Example event JSON

    # Publisher & Subscribers
    publisher_application_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True
    )
    publisher_team = db.Column(db.String(100))
    subscriber_count = db.Column(db.Integer, default=0)

    # Delivery Guarantees
    delivery_guarantee = db.Column(db.String(50))  # At-most-once, At-least-once, Exactly-once
    ordering_guarantee = db.Column(db.String(50))  # FIFO, Unordered, Partition-ordered
    retention_period_days = db.Column(db.Integer)  # How long events are retained

    # Event Characteristics
    is_idempotent = db.Column(db.Boolean, default=False)
    is_immutable = db.Column(db.Boolean, default=True)  # Event sourcing pattern
    supports_replay = db.Column(db.Boolean, default=False)
    dead_letter_queue_enabled = db.Column(db.Boolean, default=False)

    # Performance & Volume
    average_event_size_bytes = db.Column(db.Integer)
    daily_event_count = db.Column(db.BigInteger)
    peak_events_per_second = db.Column(db.Integer)
    average_processing_time_ms = db.Column(db.Integer)

    # Data Governance
    data_classification = db.Column(db.String(50))  # Public, Internal, Confidential
    contains_pii = db.Column(db.Boolean, default=False)
    pii_fields = db.Column(db.Text)  # JSON array
    gdpr_scope = db.Column(db.Boolean, default=False)

    # Business Context
    business_criticality = db.Column(db.String(50))  # Critical, High, Medium, Low
    business_domain = db.Column(db.String(100))
    sla_processing_time_ms = db.Column(db.Integer)

    # Event Sourcing
    is_event_sourced = db.Column(db.Boolean, default=False)
    aggregate_root = db.Column(db.String(100))  # DDD aggregate root
    event_store = db.Column(db.String(100))  # EventStoreDB, Kafka, Custom

    # Monitoring & Alerting
    monitoring_enabled = db.Column(db.Boolean, default=False)
    alert_on_failure = db.Column(db.Boolean, default=False)
    max_retry_attempts = db.Column(db.Integer, default=3)

    # Operational Status
    operational_status = db.Column(db.String(20), default="active")  # active, deprecated, retired
    created_date = db.Column(db.Date, default=date.today)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    publisher_application = db.relationship(
        "ArchiMateElement", foreign_keys=[publisher_application_id]
    )

    # Helper Methods
    @property
    def monthly_event_volume(self):
        """Estimate monthly event volume"""
        if self.daily_event_count:
            return self.daily_event_count * 30
        return 0

    @property
    def monthly_data_volume_gb(self):
        """Estimate monthly data volume"""
        if self.daily_event_count and self.average_event_size_bytes:
            monthly_bytes = self.daily_event_count * 30 * self.average_event_size_bytes
            return monthly_bytes / (1024**3)
        return 0

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": self.id,
            "name": self.name,
            "event_type": self.event_type,
            "message_broker": self.message_broker,
            "topic_name": self.topic_name,
            "operational_status": self.operational_status,
        }

    def __repr__(self):
        return f"<ApplicationEvent {self.name} ({self.event_type})>"


# ============================================================================
# ApplicationCollaboration Domain Model
# ============================================================================


class ApplicationCollaboration(db.Model):
    """
    ArchiMate 3.2 Application Collaboration - Aggregate of application components working together

    Represents microservices patterns, service mesh, distributed systems.
    Extends ArchiMate with Example Corp UK microservices architecture attributes.

    Examples:
    - Order Management Microservices (5 services, Kubernetes, Istio)
    - Payment Processing Cluster (3 services, high availability)
    - Manufacturing Execution System (10+ services, real-time)

    Usage:
        collab = ApplicationCollaboration(
            name="Order Management Microservices",
            collaboration_type="Microservices",
            service_count=5,
            orchestration_pattern="Choreography",
            service_mesh="Istio"
        )
    """

    __tablename__ = "application_collaborations"

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # Link to ArchiMate metamodel
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Application association
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True, index=True
    )

    # Collaboration Classification
    collaboration_type = db.Column(
        db.String(50)
    )  # Microservices, SOA, Distributed System, Service Cluster
    architecture_pattern = db.Column(
        db.String(50)
    )  # Event-Driven, Request-Response, Pub-Sub, CQRS, Saga
    orchestration_pattern = db.Column(db.String(50))  # Choreography, Orchestration, Hybrid

    # Service Mesh & Infrastructure
    service_mesh = db.Column(db.String(50))  # Istio, Linkerd, Consul, None
    api_gateway = db.Column(db.String(50))  # Kong, Apigee, AWS API Gateway
    service_discovery = db.Column(db.String(50))  # Consul, Eureka, Kubernetes DNS
    container_platform = db.Column(db.String(50))  # Kubernetes, Docker Swarm, ECS

    # Collaboration Characteristics
    service_count = db.Column(db.Integer)  # Number of participating services
    is_distributed = db.Column(db.Boolean, default=True)
    is_highly_available = db.Column(db.Boolean, default=False)
    fault_tolerance_enabled = db.Column(db.Boolean, default=False)

    # Communication
    primary_protocol = db.Column(db.String(50))  # gRPC, HTTP/REST, Message Queue
    sync_async_mix = db.Column(db.String(50))  # Synchronous, Asynchronous, Hybrid

    # Business Context
    business_domain = db.Column(db.String(100))
    business_criticality = db.Column(db.String(20))

    # Operational Status
    operational_status = db.Column(db.String(20), default="active")

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])

    def __repr__(self):
        return f"<ApplicationCollaboration {self.name} ({self.collaboration_type})>"


# ============================================================================
# SQLAlchemy Event Listeners - Auto-create ArchiMateElements
# ============================================================================


# ApplicationInterface's ArchiMate element is now created by the shared
# flush listener in app/services/archimate_backbone.py.


@event.listens_for(ApplicationEvent, "before_insert")
def create_event_archimate_element(mapper, connection, target):
    """Automatically create ArchiMateElement when ApplicationEvent is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="ApplicationEvent",
                layer="Application",
                description=target.description or f"{target.event_type} event",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(ApplicationCollaboration, "before_insert")
def create_collaboration_archimate_element(mapper, connection, target):
    """Automatically create ArchiMateElement when ApplicationCollaboration is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                name=target.name,
                type="ApplicationCollaboration",
                layer="Application",
                description=target.description or f"{target.collaboration_type} collaboration",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


# ============================================================================
# ApplicationComponent Domain Model (NEW)
# ============================================================================
# NOTE: ApplicationComponent has been consolidated into application_portfolio.py
# This class is commented out to avoid SQLAlchemy conflicts
# NOTE: ApplicationComponent class has been consolidated into application_portfolio.py
# This prevents SQLAlchemy table conflicts while preserving all functionality
# The consolidated model includes all fields from both the portfolio and ArchiMate versions
# Please use ApplicationComponent from application_portfolio.py for all new development


# ============================================================================
# ApplicationFunction Domain Model (NEW)
# ============================================================================


class ApplicationFunction(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Application Function - Automated behavior performed by application component

    Represents specific functionalities, features, capabilities within applications.
    """

    __tablename__ = "application_functions"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # Application Context
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True, index=True
    )

    # ArchiMate integration
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Entry point for execution
    entry_point = db.Column(db.String(500))  # Main class or function
    maintenance_provider = db.Column(db.String(500))  # Maintenance Provider
    cost_currency = db.Column(db.String(10))  # Cost Currency
    total_run_cost = db.Column(db.Numeric(15, 2))  # Total Run Cost (Auto)
    hardware_cost = db.Column(db.Numeric(15, 2))  # Hardware Cost
    software_cost = db.Column(db.Numeric(15, 2))  # Software Cost
    facilities_utilities_cost = db.Column(db.Numeric(15, 2))  # Facilities and Utilities Cost
    internal_labor_cost = db.Column(db.Numeric(15, 2))  # Internal Labor Cost
    external_labor_cost = db.Column(db.Numeric(15, 2))  # External Labor Cost
    external_services_cost = db.Column(db.Numeric(15, 2))  # External Services Cost
    internal_services_cost = db.Column(db.Numeric(15, 2))  # Internal Services Cost
    telecom_services_cost = db.Column(db.Numeric(15, 2))  # Telecom Services Cost
    other_costs = db.Column(db.Numeric(15, 2))  # Other Costs
    it_unit_managing_app = db.Column(db.String(500))  # IT Unit Managing the App
    application_manager = db.Column(db.String(500))  # Application Manager
    app_business_owner = db.Column(db.String(500))  # App Business Owner (maps to business_owner)
    it_security_officer = db.Column(db.String(500))  # IT Security Officer
    business_security_officer = db.Column(db.String(500))  # Business Security Officer
    business_unit_owner = db.Column(db.String(500))  # Business Unit Owner of the App
    countries_where_used = db.Column(db.Text)  # Countries where the App is used (JSON array)
    apps_portal_url = db.Column(db.Text)  # Apps Portal URL
    psat_status = db.Column(db.String(100))  # PSAT Status
    certified = db.Column(db.Boolean, default=False)  # Certified
    risk_assessment_status = db.Column(db.String(100))  # Risk Assessment Status
    core_data_ok = db.Column(db.Boolean, default=False)  # Core Data OK
    operational_data_ok = db.Column(db.Boolean, default=False)  # Operational Data OK
    data_quality_analysis = db.Column(db.Text)  # Data Quality Analysis
    certified_at = db.Column(db.Date)  # Certified At
    certified_by = db.Column(db.String(500))  # Certified By

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])

    # NOTE: The following relationships are accessed via parent ApplicationComponent:
    # - requirements: self.application_component.requirements
    # - technology_stacks: self.application_component.technology_stacks
    # - business_actors: self.application_component.business_actors
    # - interfaces: self.application_component.interfaces
    # - supported_processes: self.application_component.supported_processes
    # - compliance_realizations: self.application_component (via application_compliance_realization)
    # - software_modules: self.application_component (via application_modules)
    #
    # All junction tables have foreign keys to application_components.id, not application_functions.id.
    # ApplicationFunction connects to ApplicationComponent via application_component_id.

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "application_component_id": self.application_component_id,
            "archimate_element_id": self.archimate_element_id,
            "entry_point": self.entry_point,
            "maintenance_provider": self.maintenance_provider,
            "cost_currency": self.cost_currency,
            "total_run_cost": float(self.total_run_cost) if self.total_run_cost else None,
            "certified": self.certified,
            "psat_status": self.psat_status,
        }

    def __repr__(self):
        return f"<ApplicationFunction {self.name}>"

    """
    # NOTE: Entire ApplicationComponent class has been consolidated into application_portfolio.py
    # All functionality is now available in the consolidated ApplicationComponent model
    # This prevents SQLAlchemy table conflicts while preserving all functionality
    # The class definition below is commented out to avoid conflicts
    """
    # Original class content commented out for consolidation
    pass


# ============================================================================
# ApplicationProcess Domain Model (NEW)
# ============================================================================


class ApplicationProcess(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Application Process - Sequence of application behaviors

    Represents workflows, business processes automated by applications.
    """

    __tablename__ = "application_processes"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Application association
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True, index=True
    )

    # Process Classification
    process_type = db.Column(db.String(50))  # Sequential, Parallel, Conditional, Loop
    automation_level = db.Column(
        db.String(50)
    )  # Fully Automated, Semi-Automated, Manual with System Support

    # Workflow
    steps_count = db.Column(db.Integer)
    average_duration_minutes = db.Column(db.Integer)
    workflow_engine = db.Column(db.String(100))  # Camunda, Activiti, Custom
    workflow_definition_url = db.Column(db.String(500))

    # Performance
    executions_per_day = db.Column(db.Integer)
    success_rate_percent = db.Column(db.Numeric(5, 2))
    average_processing_time_minutes = db.Column(db.Integer)

    # Business Context
    business_process_id = db.Column(db.Integer, nullable=True)  # Link to BusinessProcess
    business_owner = db.Column(db.String(200))

    # Status
    operational_status = db.Column(db.String(20), default="active")

    # Metadata
    tags = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships - Commented out to avoid conflict with ApplicationComponent
    # archimate_element = db.relationship('ArchiMateElement', foreign_keys=[archimate_element_id], back_populates='application_component')

    def __repr__(self):
        return f"<ApplicationProcess {self.name}>"


# ============================================================================
# ApplicationInteraction Domain Model (NEW)
# ============================================================================


class ApplicationInteraction(db.Model):
    """
    ArchiMate 3.2 Application Interaction - Collaboration between application components

    Represents point-to-point integrations, API calls, data exchanges.
    """

    __tablename__ = "application_interactions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Application association
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True, index=True
    )

    # Interaction Classification
    interaction_type = db.Column(
        db.String(50)
    )  # API Call, Message Exchange, Data Sync, Event Notification
    communication_pattern = db.Column(
        db.String(50)
    )  # Synchronous, Asynchronous, Request-Response, Fire-and-Forget
    protocol = db.Column(db.String(50))  # HTTP/REST, gRPC, AMQP, MQTT

    # Endpoints
    source_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True
    )
    target_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True
    )

    # Performance
    average_latency_ms = db.Column(db.Integer)
    throughput_per_minute = db.Column(db.Integer)
    error_rate_percent = db.Column(db.Numeric(5, 2))

    # Data
    data_format = db.Column(db.String(50))  # JSON, XML, Protobuf, Avro
    payload_size_kb = db.Column(db.Integer)

    # Status
    operational_status = db.Column(db.String(20), default="active")

    # Metadata
    tags = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = db.relationship(
        "ArchiMateElement", foreign_keys=[archimate_element_id], backref="application_interaction"
    )
    source_component = db.relationship(
        "ApplicationComponent", foreign_keys=[source_component_id], backref="outgoing_interactions"
    )
    target_component = db.relationship(
        "ApplicationComponent", foreign_keys=[target_component_id], backref="incoming_interactions"
    )

    def __repr__(self):
        return f"<ApplicationInteraction {self.name}>"


# ============================================================================
# DataObject Domain Model (NEW)
# ============================================================================


class DataObject(TenantMixin, db.Model):
    # ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
    """
    ArchiMate 3.2 Data Object - Data structured for automated processing

    Represents data entities, database tables, files, documents processed by applications.
    """

    __tablename__ = "application_data_objects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Application association
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True, index=True
    )

    # Data Classification
    data_type = db.Column(db.String(50))  # Database Table, File, Document, Message, Cache Entry
    data_format = db.Column(db.String(50))  # Relational, JSON, XML, CSV, Binary, Parquet
    data_classification = db.Column(db.String(50))  # Public, Internal, Confidential, Restricted

    # Data Location
    storage_location = db.Column(db.String(500))  # Database name, file path, S3 bucket
    schema_name = db.Column(db.String(200))
    table_name = db.Column(db.String(200))

    # Data Characteristics
    record_count = db.Column(db.BigInteger)
    size_mb = db.Column(db.Numeric(15, 2))
    growth_rate_mb_per_month = db.Column(db.Numeric(10, 2))

    # Data Governance
    data_owner = db.Column(db.String(200))
    data_steward = db.Column(db.String(200))
    contains_pii = db.Column(db.Boolean, default=False)
    pii_fields = db.Column(db.Text)  # JSON array
    gdpr_scope = db.Column(db.Boolean, default=False)
    retention_period_days = db.Column(db.Integer)

    # Quality
    data_quality_score = db.Column(db.Numeric(5, 2))  # 0 - 100
    completeness_percent = db.Column(db.Numeric(5, 2))
    accuracy_percent = db.Column(db.Numeric(5, 2))
    last_quality_check_date = db.Column(db.Date)

    # Access
    read_access_count = db.Column(db.Integer)  # Number of components/users with read access
    write_access_count = db.Column(db.Integer)
    is_master_data = db.Column(db.Boolean, default=False)

    # Status
    operational_status = db.Column(db.String(20), default="active")

    # Metadata
    tags = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Data Governance
    access_level = db.Column(
        db.String(30), default="public"
    )  # public, internal, restricted, confidential
    access_roles = db.Column(db.JSON, nullable=True)  # List of roles with access
    last_accessed = db.Column(db.DateTime, nullable=True)  # Last access timestamp
    auto_delete_date = db.Column(db.DateTime, nullable=True)  # Automatic deletion date
    retention_reason = db.Column(db.String(255), nullable=True)  # Reason for retention

    # Relationships
    archimate_element = db.relationship(
        "ArchiMateElement", foreign_keys=[archimate_element_id], backref="application_data_object"
    )
    application_component = db.relationship(
        "ApplicationComponent", foreign_keys=[application_component_id], backref="data_objects"
    )

    # ArchiMate 3.2 Relationships
    logical_models = db.relationship(
        "LogicalDataModel", secondary="logical_model_data_objects", back_populates="data_objects"
    )
    data_lineage = db.relationship(
        "DataLineage",
        secondary="data_lineage_access",
        back_populates="data_objects",
        overlaps="data_lineage",
    )

    def __repr__(self):
        return f"<DataObject {self.name}>"


# ============================================================================
# Event Listeners for New Application Layer Models
# ============================================================================
# NOTE: ApplicationComponent event listener removed since ApplicationComponent is now in application_portfolio.py


@event.listens_for(ApplicationFunction, "before_insert")
def create_applicationfunction_archimate_element(mapper, connection, target):
    """Auto-create ArchiMateElement when ApplicationFunction is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="ApplicationFunction",
                layer="Application",
                description=target.description or "Application function",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(ApplicationProcess, "before_insert")
def create_applicationprocess_archimate_element(mapper, connection, target):
    """Auto-create ArchiMateElement when ApplicationProcess is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="ApplicationProcess",
                layer="Application",
                description=target.description or "Application process",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(ApplicationInteraction, "before_insert")
def create_applicationinteraction_archimate_element(mapper, connection, target):
    """Auto-create ArchiMateElement when ApplicationInteraction is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                name=target.name,
                type="ApplicationInteraction",
                layer="Application",
                description=target.description or "Application interaction",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(DataObject, "before_insert")
def create_dataobject_archimate_element(mapper, connection, target):
    """Auto-create ArchiMateElement when DataObject is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="DataObject",
                layer="Application",
                description=target.description or "Data object",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


class ApplicationService(db.Model):
    """
    ArchiMate 3.2 Application Service
    """

    __tablename__ = "application_services"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text)

    # ArchiMate integration
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Application association
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True, index=True
    )

    # Minimal metadata used by application management UI
    service_type = db.Column(db.String(50), default="Shared")

    # Relationships
    archimate_element = db.relationship(
        "ArchiMateElement", foreign_keys=[archimate_element_id], backref="application_service"
    )
    application_component = db.relationship(
        "ApplicationComponent", foreign_keys=[application_component_id], backref="services"
    )

    def __repr__(self):
        return f"<ApplicationService {self.name}>"


@event.listens_for(ApplicationService, "before_insert")
def create_applicationservice_archimate_element(mapper, connection, target):
    """Auto-create ArchiMateElement when ApplicationService is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                name=target.name,
                type="ApplicationService",
                layer="Application",
                description=target.description or "Application service",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]
