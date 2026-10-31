"""
ArchiMate 3.2 Relationship Junction Tables

Many-to-many relationship tables linking domain models across Business and Application layers.
Enables RACI matrices, CRUD matrices, service dependencies, and integration landscapes.

Junction Tables:
- actor_role_assignment: BusinessActor ↔ BusinessRole (who performs which roles)
- process_actor_raci: BusinessProcess ↔ BusinessActor (RACI responsibility assignments)
- process_role_raci: BusinessProcess ↔ BusinessRole (RACI responsibility assignments)
- process_data_crud: BusinessProcess ↔ BusinessObject (CRUD operations)
- capability_actor_raci: BusinessCapability ↔ BusinessActor (capability ownership)
- service_dependency: BusinessService ↔ BusinessService (service dependencies)
- service_realization: BusinessProcess ↔ BusinessService (processes realize services)
- interface_consumer: ApplicationInterface ↔ ApplicationComponent (API consumers)
- data_object_storage: BusinessObject ↔ ApplicationComponent (where data is stored)
- capability_compliance_requirements: BusinessCapability ↔ ComplianceRequirement (requirements per capability)
- application_component_vendor_products: ApplicationComponent ↔ VendorProduct (which vendor products an application uses)
"""

from datetime import datetime

from .. import db
from .mixins import TenantMixin
from .mixins.core import parent_organization_default

# ============================================================================
# Vendor Product Association Tables (Option B+)
# ============================================================================

# ApplicationComponent ↔ VendorProduct (Many-to-Many)
# Tracks which vendor products an application uses beyond its primary vendor product
application_component_vendor_products = db.Table(
    "application_component_vendor_products",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "application_component_id",
        db.Integer,
        db.ForeignKey("application_components.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "vendor_product_id",
        db.Integer,
        db.ForeignKey("vendor_products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    # The application's organisation, filled at insert from the application component.
    db.Column(
        "organization_id",
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        default=parent_organization_default("application_components", "application_component_id"),
    ),
    # Relationship metadata
    db.Column(
        "relationship_type", db.String(50), default="uses"
    ),  # 'primary', 'integration', 'data_source', 'reporting', 'uses'
    db.Column(
        "deployment_type", db.String(50)
    ),  # 'production', 'staging', 'development', 'disaster_recovery'
    db.Column(
        "criticality", db.String(20)
    ),  # 'mission_critical', 'business_critical', 'important', 'supporting'
    db.Column(
        "usage_percentage", db.Integer
    ),  # 0 - 100: How much of the product's capabilities are used
    db.Column("implementation_date", db.Date),
    db.Column("notes", db.Text),
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
    db.Column("updated_at", db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
    # Unique constraint to prevent duplicate mappings
    db.UniqueConstraint(
        "application_component_id", "vendor_product_id", name="uq_app_vendor_product"
    ),
)


# ============================================================================
# Implementation & Migration Layer Association Tables
# ============================================================================

work_package_events = db.Table(
    "work_package_events",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "work_package_id",
        db.Integer,
        db.ForeignKey("work_packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "implementation_event_id",
        db.Integer,
        db.ForeignKey("implementation_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("relationship_type", db.String(50)),
    db.Column("is_blocking", db.Boolean, default=False, nullable=False),
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


work_package_plateaus = db.Table(
    "work_package_plateaus",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "work_package_id",
        db.Integer,
        db.ForeignKey("work_packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "plateau_id",
        db.Integer,
        db.ForeignKey("plateaus.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("alignment_type", db.String(50)),
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


gap_work_packages = db.Table(
    "gap_work_packages",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "gap_id",
        db.Integer,
        db.ForeignKey("gaps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "work_package_id",
        db.Integer,
        db.ForeignKey("work_packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("resolution_role", db.String(50)),
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


plateau_gaps = db.Table(
    "plateau_gaps",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "plateau_id",
        db.Integer,
        db.ForeignKey("plateaus.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "gap_id",
        db.Integer,
        db.ForeignKey("gaps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("relationship_type", db.String(50)),
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


plateau_capabilities = db.Table(
    "plateau_capabilities",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "plateau_id",
        db.Integer,
        db.ForeignKey("plateaus.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "business_capability_id",
        db.Integer,
        db.ForeignKey("business_capability.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("role", db.String(50)),
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


gap_capabilities = db.Table(
    "gap_capabilities",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "gap_id",
        db.Integer,
        db.ForeignKey("gaps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "business_capability_id",
        db.Integer,
        db.ForeignKey("business_capability.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("impact_role", db.String(50)),
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


plateau_archimate_elements = db.Table(
    "plateau_archimate_elements",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "plateau_id",
        db.Integer,
        db.ForeignKey("plateaus.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "archimate_element_id",
        db.Integer,
        db.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("link_category", db.String(30), nullable=False, default="application"),
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


gap_archimate_elements = db.Table(
    "gap_archimate_elements",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "gap_id",
        db.Integer,
        db.ForeignKey("gaps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "archimate_element_id",
        db.Integer,
        db.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("link_category", db.String(30), nullable=False, default="application"),
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


capability_compliance_requirements = db.Table(
    "capability_compliance_requirements",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "business_capability_id",
        db.Integer,
        db.ForeignKey("business_capability.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "compliance_requirement_id",
        db.Integer,
        db.ForeignKey("compliance_requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("is_mandatory", db.Boolean, default=True),
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


application_compliance_realization = db.Table(
    "application_compliance_realization",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "application_component_id",
        db.Integer,
        db.ForeignKey("application_components.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "compliance_requirement_id",
        db.Integer,
        db.ForeignKey("compliance_requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("implementation_status", db.String(50), default="not_started"),
    db.Column("evidence_url", db.String(500)),
    db.Column("verified_at", db.DateTime),
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


# ============================================================================
# Business Actor ↔ Business Role Assignments
# ============================================================================

actor_role_assignment = db.Table(
    "actor_role_assignment",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "actor_id", db.Integer, db.ForeignKey("business_actors.id"), nullable=False, index=True
    ),
    db.Column(
        "role_id", db.Integer, db.ForeignKey("business_roles.id"), nullable=False, index=True
    ),
    db.Column("assignment_type", db.String(50)),  # Primary, Secondary, Acting, Temporary
    db.Column("fte_allocation", db.Numeric(4, 2), default=1.0),  # 1.0 = full-time in this role
    db.Column("start_date", db.Date),
    db.Column("end_date", db.Date, nullable=True),
    db.Column("is_active", db.Boolean, default=True),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    db.Column("notes", db.Text),
)


# ============================================================================
# Business Process ↔ Business Actor RACI Matrix
# ============================================================================


class ProcessActorRaci(db.Model):
    """
    RACI Matrix: Links BusinessProcess to BusinessActor with responsibility level

    RACI Framework:
    - R (Responsible): Does the work
    - A (Accountable): Ultimate ownership, single person
    - C (Consulted): Provides input
    - I (Informed): Kept updated

    Usage:
        raci = ProcessActorRaci(
            process_id=process.id,
            actor_id=quality_dept.id,
            responsibility='R',  # Quality Dept is Responsible
            workload_percentage=40
        )
    """

    __tablename__ = "process_actor_raci"

    id = db.Column(db.Integer, primary_key=True)

    # Foreign Keys
    process_id = db.Column(
        db.Integer, db.ForeignKey("business_processes.id"), nullable=False, index=True
    )
    actor_id = db.Column(
        db.Integer, db.ForeignKey("business_actors.id"), nullable=False, index=True
    )

    # RACI Responsibility
    responsibility = db.Column(db.String(1), nullable=False)  # R, A, C, I

    # Workload
    workload_percentage = db.Column(db.Integer)  # % of actor's time spent on this process
    estimated_hours_monthly = db.Column(db.Integer)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships
    process = db.relationship("BusinessProcess", backref="actor_raci_assignments")
    actor = db.relationship("BusinessActor", back_populates="process_raci_assignments")

    def __repr__(self):
        return f"<ProcessActorRaci Process:{self.process_id} Actor:{self.actor_id} {self.responsibility}>"


# ============================================================================
# Business Process ↔ Business Role RACI Matrix
# ============================================================================


class ProcessRoleRaci(TenantMixin, db.Model):
    """
    RACI Matrix: Links BusinessProcess to BusinessRole with responsibility level

    Similar to ProcessActorRaci but at role level instead of actor level.
    Useful for process design before specific actors are assigned.
    """

    __tablename__ = "process_role_raci"
    __tenant_parent__ = (('process_id', 'business_processes'),)

    id = db.Column(db.Integer, primary_key=True)

    # Foreign Keys
    process_id = db.Column(
        db.Integer, db.ForeignKey("business_processes.id"), nullable=False, index=True
    )
    role_id = db.Column(db.Integer, db.ForeignKey("business_roles.id"), nullable=False, index=True)

    # RACI Responsibility
    responsibility = db.Column(db.String(1), nullable=False)  # R, A, C, I

    # Workload
    workload_percentage = db.Column(db.Integer)
    estimated_hours_monthly = db.Column(db.Integer)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships
    process = db.relationship("BusinessProcess", backref="role_raci_assignments")
    role = db.relationship("BusinessRole", back_populates="process_raci_assignments")

    def __repr__(self):
        return (
            f"<ProcessRoleRaci Process:{self.process_id} Role:{self.role_id} {self.responsibility}>"
        )


# ============================================================================
# Business Process ↔ Business Object CRUD Matrix
# ============================================================================


class ProcessDataCrud(TenantMixin, db.Model):
    # ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
    """
    CRUD Matrix: Links BusinessProcess to BusinessObject with CRUD operations

    Tracks which processes Create, Read, Update, Delete which data objects.
    Essential for data lineage, impact analysis, GDPR compliance.

    Usage:
        crud = ProcessDataCrud(
            process_id=order_process.id,
            business_object_id=customer_order.id,
            creates=True,
            reads=True,
            updates=True,
            deletes=False
        )
    """

    __tablename__ = "process_data_crud"

    id = db.Column(db.Integer, primary_key=True)

    # Foreign Keys
    process_id = db.Column(
        db.Integer, db.ForeignKey("business_processes.id"), nullable=False, index=True
    )
    business_object_id = db.Column(
        db.Integer, db.ForeignKey("business_objects.id"), nullable=False, index=True
    )

    # CRUD Operations
    creates = db.Column(db.Boolean, default=False)  # C
    reads = db.Column(db.Boolean, default=False)  # R
    updates = db.Column(db.Boolean, default=False)  # U
    deletes = db.Column(db.Boolean, default=False)  # D

    # Operation Details
    operation_frequency = db.Column(db.String(50))  # Per transaction, Hourly, Daily, Weekly
    volume_daily = db.Column(db.Integer)  # Number of CRUD operations per day
    is_master_source = db.Column(
        db.Boolean, default=False
    )  # Is this process the master for this data?

    # Data Quality
    validation_rules = db.Column(db.Text)  # JSON array of validation rules
    data_quality_checks = db.Column(db.Text)  # JSON array of quality checks

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships
    process = db.relationship("BusinessProcess", backref="data_crud_operations")
    business_object = db.relationship("BusinessObject", backref="process_data_crud_operations")

    @property
    def crud_operations(self):
        """Return CRUD operations as string: 'CRUD', 'CR', 'R', etc."""
        ops = ""
        if self.creates:
            ops += "C"
        if self.reads:
            ops += "R"
        if self.updates:
            ops += "U"
        if self.deletes:
            ops += "D"
        return ops or "None"

    def __repr__(self):
        return f"<ProcessDataCrud Process:{self.process_id} Object:{self.business_object_id} {self.crud_operations}>"


# ============================================================================
# Business Capability ↔ Business Actor RACI Matrix
# ============================================================================


class CapabilityActorRaci(db.Model):
    """
    RACI Matrix: Links BusinessCapability to BusinessActor for capability ownership

    Tracks who is Responsible/Accountable for delivering capabilities.
    Essential for organizational design and capability-based planning.
    """

    __tablename__ = "capability_actor_raci"

    id = db.Column(db.Integer, primary_key=True)

    # Foreign Keys
    capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id"), nullable=False, index=True
    )
    actor_id = db.Column(
        db.Integer, db.ForeignKey("business_actors.id"), nullable=False, index=True
    )

    # RACI Responsibility
    responsibility = db.Column(db.String(1), nullable=False)  # R, A, C, I

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships
    capability = db.relationship("BusinessCapability", backref="actor_raci_assignments")
    actor = db.relationship("BusinessActor", back_populates="capability_raci_assignments")

    def __repr__(self):
        return f"<CapabilityActorRaci Capability:{self.capability_id} Actor:{self.actor_id} {self.responsibility}>"


# ============================================================================
# Business Service Dependencies
# ============================================================================


class ServiceDependency(TenantMixin, db.Model):
    """
    Service Dependency: BusinessService depends on another BusinessService

    Tracks service-to-service dependencies for impact analysis.
    Essential for understanding cascading failures and service resilience.

    Usage:
        dep = ServiceDependency(
            dependent_service_id=order_service.id,
            provider_service_id=inventory_service.id,
            dependency_type='Hard',
            criticality='High'
        )
    """

    __tablename__ = "service_dependency"
    __tenant_parent__ = (('dependent_service_id', 'business_services'),)

    id = db.Column(db.Integer, primary_key=True)

    # Foreign Keys
    dependent_service_id = db.Column(
        db.Integer, db.ForeignKey("business_services.id"), nullable=False, index=True
    )
    provider_service_id = db.Column(
        db.Integer, db.ForeignKey("business_services.id"), nullable=False, index=True
    )

    # Dependency Characteristics
    dependency_type = db.Column(db.String(20))  # Hard (required), Soft (optional), Transient
    criticality = db.Column(db.String(20))  # Critical, High, Medium, Low
    failure_impact = db.Column(db.String(50))  # Complete Failure, Degraded, No Impact

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships
    dependent_service = db.relationship(
        "BusinessService", foreign_keys=[dependent_service_id], backref="dependencies"
    )
    provider_service = db.relationship(
        "BusinessService", foreign_keys=[provider_service_id], backref="dependents"
    )

    def __repr__(self):
        return f"<ServiceDependency {self.dependent_service_id} → {self.provider_service_id} ({self.dependency_type})>"


# ============================================================================
# Business Service Realization (Process → Service)
# ============================================================================


class ServiceRealization(TenantMixin, db.Model):
    """
    Service Realization: BusinessProcess realizes BusinessService

    ArchiMate realization relationship: Which processes deliver which services?
    Essential for process-to-service traceability.
    """

    __tablename__ = "service_realization"
    __tenant_parent__ = (('process_id', 'business_processes'),)

    id = db.Column(db.Integer, primary_key=True)

    # Foreign Keys
    process_id = db.Column(
        db.Integer, db.ForeignKey("business_processes.id"), nullable=False, index=True
    )
    service_id = db.Column(
        db.Integer, db.ForeignKey("business_services.id"), nullable=False, index=True
    )

    # Realization Details
    realization_percentage = db.Column(db.Integer)  # % of service realized by this process
    is_primary = db.Column(db.Boolean, default=False)  # Primary process for this service?

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships
    process = db.relationship("BusinessProcess", backref="realized_services")
    service = db.relationship("BusinessService", back_populates="realizing_processes")

    def __repr__(self):
        return f"<ServiceRealization Process:{self.process_id} realizes Service:{self.service_id}>"


# ============================================================================
# Application Interface Consumers
# ============================================================================


class InterfaceConsumer(TenantMixin, db.Model):
    """
    Interface Consumer: ApplicationComponent consumes ApplicationInterface

    Tracks API consumers for impact analysis and integration landscape.
    Essential for understanding API usage and deprecation planning.
    """

    __tablename__ = "interface_consumer"
    __tenant_parent__ = (('interface_id', 'application_interfaces'),)

    id = db.Column(db.Integer, primary_key=True)

    # Foreign Keys
    interface_id = db.Column(
        db.Integer, db.ForeignKey("application_interfaces.id"), nullable=False, index=True
    )
    consumer_application_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=False, index=True
    )  # ApplicationComponent

    # Consumption Details
    consumer_type = db.Column(db.String(50))  # Internal System, External Partner, Public Consumer
    integration_pattern = db.Column(db.String(50))  # Synchronous, Asynchronous, Batch
    daily_call_volume = db.Column(db.BigInteger)
    criticality = db.Column(db.String(20))  # Critical, High, Medium, Low

    # API Key/Credentials
    api_key_id = db.Column(db.String(100))
    authentication_method = db.Column(db.String(50))

    # Metadata
    onboarded_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships
    interface = db.relationship("ApplicationInterface", backref="consumers")
    consumer_application = db.relationship(
        "ArchiMateElement", foreign_keys=[consumer_application_id]
    )

    def __repr__(self):
        return f"<InterfaceConsumer Interface:{self.interface_id} Consumer:{self.consumer_application_id}>"


# ============================================================================
# Business Object Storage (Where is data stored?)
# ============================================================================


class DataObjectStorage(TenantMixin, db.Model):
    # ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
    """
    Data Object Storage: BusinessObject is stored in ApplicationComponent

    Tracks where business data is physically stored for data lineage and GDPR.
    Essential for understanding data landscape and compliance.
    """

    __tablename__ = "data_object_storage"

    id = db.Column(db.Integer, primary_key=True)

    # Foreign Keys
    business_object_id = db.Column(
        db.Integer, db.ForeignKey("business_objects.id"), nullable=False, index=True
    )
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=False, index=True
    )  # ApplicationComponent

    # Storage Details
    is_master_source = db.Column(db.Boolean, default=False)  # Is this the master/golden source?
    storage_type = db.Column(
        db.String(50)
    )  # Database, File System, Cache, Data Lake, Data Warehouse
    data_classification = db.Column(db.String(50))  # Inherited or overridden from BusinessObject

    # Data Volume
    record_count = db.Column(db.BigInteger)
    storage_size_gb = db.Column(db.Numeric(15, 2))

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships
    business_object = db.relationship(
        "BusinessObject", back_populates="storage_locations", overlaps="owning_business_object"
    )
    application_component = db.relationship(
        "ArchiMateElement", foreign_keys=[application_component_id]
    )

    def __repr__(self):
        return f"<DataObjectStorage Object:{self.business_object_id} in App:{self.application_component_id}>"


# ============================================================================
# Application Component ↔ Business Capability
# ============================================================================
# NOTE: ApplicationCapabilityMapping is defined in application_portfolio.py


# ============================================================================
# Application Component ↔ Requirement
# ============================================================================


class ApplicationRequirementMapping(TenantMixin, db.Model):
    """
    Application ↔ Requirement: Which requirements are implemented by which applications

    Links Motivation Layer (Requirements) to Application Layer (Components).
    Essential for requirements traceability and impact analysis.
    """

    __tablename__ = "application_requirement_mapping"
    __tenant_parent__ = (('application_component_id', 'application_components'),)

    id = db.Column(db.Integer, primary_key=True)

    # Foreign Keys
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )
    requirement_id = db.Column(
        db.Integer, db.ForeignKey("requirements.id"), nullable=False, index=True
    )

    # Implementation Status
    implementation_status = db.Column(
        db.String(50)
    )  # Implemented, Partially Implemented, Planned, Not Implemented
    implementation_percentage = db.Column(db.Integer)  # 0 - 100
    implementation_date = db.Column(db.Date)

    # Verification
    is_verified = db.Column(db.Boolean, default=False)
    verification_date = db.Column(db.Date)
    verification_method = db.Column(db.String(100))  # Testing, Code Review, Demo, UAT

    # Traceability
    implemented_in_version = db.Column(db.String(50))
    code_reference = db.Column(db.String(500))  # Link to code/commit
    test_case_reference = db.Column(db.String(500))

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships
    application_component = db.relationship(
        "ApplicationComponent",
        backref=db.backref(
            "requirement_mappings", overlaps="requirements,implementing_applications"
        ),
        overlaps="requirements,implementing_applications",
    )
    requirement = db.relationship(
        "Requirement",
        backref=db.backref(
            "app_requirement_mappings", overlaps="requirements,implementing_applications"
        ),
        overlaps="requirements,implementing_applications",
    )

    def __repr__(self):
        return f"<ApplicationRequirementMapping App:{self.application_component_id} Req:{self.requirement_id}>"


# ============================================================================
# Application Component ↔ Business Process (NEW)
# ============================================================================


class ApplicationProcessSupport(TenantMixin, db.Model):
    """
    Application ↔ Business Process: Which applications support/automate which business processes

    Direct ArchiMate 3.2 relationship - Applications SERVE/REALIZE Business Processes.
    Essential for process automation analysis, digital transformation, and impact assessment.

    Use Cases:
    - Process automation tracking: Which processes are automated and to what degree?
    - Application rationalization: Which apps can be retired without impacting processes?
    - Digital transformation: Identify manual processes needing automation
    - Compliance: Map GDPR/SOX processes to systems of record
    - Cost allocation: TCO per business process
    """

    __tablename__ = "application_process_support"
    __tenant_parent__ = (('application_component_id', 'application_components'),)

    id = db.Column(db.Integer, primary_key=True)

    # Foreign Keys
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )
    business_process_id = db.Column(
        db.Integer, db.ForeignKey("business_processes.id"), nullable=False, index=True
    )

    # Support Type
    support_type = db.Column(
        db.String(50)
    )  # primary_execution, supporting_tool, data_source, monitoring, reporting, orchestration
    automation_level = db.Column(
        db.Integer
    )  # 0 - 100% (how much of process is automated by this app)

    # Strategic Context
    criticality = db.Column(
        db.String(20)
    )  # critical, high, medium, low - business impact if app fails
    is_system_of_record = db.Column(
        db.Boolean, default=False
    )  # True if app is authoritative source for process data

    # Technical Integration
    integration_pattern = db.Column(db.String(50))  # real_time, batch, event_driven, manual, hybrid
    integration_frequency = db.Column(db.String(50))  # continuous, hourly, daily, weekly, on_demand
    data_flow_direction = db.Column(db.String(20))  # bidirectional, inbound, outbound

    # Performance & Usage
    transaction_volume_daily = db.Column(
        db.Integer
    )  # Number of process transactions handled per day
    average_response_time_ms = db.Column(db.Integer)
    concurrent_process_users = db.Column(db.Integer)

    # Compliance & Audit
    regulatory_significance = db.Column(db.Boolean, default=False)  # Part of regulated process?
    audit_trail_required = db.Column(db.Boolean, default=False)
    data_retention_days = db.Column(db.Integer)

    # Lifecycle
    start_date = db.Column(db.Date)  # When app started supporting this process
    end_date = db.Column(db.Date, nullable=True)  # When support ended (for historical tracking)
    is_active = db.Column(db.Boolean, default=True)
    replacement_plan = db.Column(db.Text)  # If being replaced, what's the plan?

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.String(100))
    notes = db.Column(db.Text)

    def __repr__(self):
        return f"<ApplicationProcessSupport App:{self.application_component_id} Process:{self.business_process_id} {self.support_type}>"

    # NOTE: a stray copy of ApplicationRequirementMapping's tail used to follow here —
    # duplicate created_at/updated_at/notes columns and a second __repr__ referencing
    # self.requirement_id and self.implementation_status. Python kept the LAST
    # definition, so ApplicationProcessSupport.__repr__ raised AttributeError for
    # fields this class does not have. The real ApplicationRequirementMapping (line
    # ~752) is intact and declares all of them itself, so this was duplication only.


# ============================================================================
# Application Component ↔ Technology Stack (NEW)
# ============================================================================


class ApplicationTechnologyMapping(TenantMixin, db.Model):
    """
    Application ↔ Technology: Which applications use which technology stacks

    Links Application Layer (Components) to Technology Layer (Stacks, Nodes, etc).
    Essential for technology portfolio management and modernization planning.
    """

    __tablename__ = "application_technology_mapping"
    __tenant_parent__ = (('application_component_id', 'application_components'),)

    id = db.Column(db.Integer, primary_key=True)

    # Foreign Keys - Can link to different technology layer elements
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )

    # Technology References (one of these should be populated)
    technology_stack_id = db.Column(
        db.Integer, db.ForeignKey("technology_stacks.id"), nullable=True, index=True
    )
    technology_node_id = db.Column(
        db.Integer, db.ForeignKey("technology_nodes.id"), nullable=True, index=True
    )
    system_software_id = db.Column(
        db.Integer, db.ForeignKey("technology_system_software.id"), nullable=True, index=True
    )

    # Usage Details
    usage_type = db.Column(db.String(50))  # Deployment, Dependency, Infrastructure, Platform
    is_primary = db.Column(db.Boolean, default=False)
    dependency_level = db.Column(db.String(20))  # Critical, High, Medium, Low

    # Version & Compatibility
    technology_version = db.Column(db.String(100))
    is_compatible = db.Column(db.Boolean, default=True)
    compatibility_notes = db.Column(db.Text)

    # Lifecycle
    adoption_date = db.Column(db.Date)
    retirement_date = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, default=True)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships (will be set up via back_populates in models)

    def __repr__(self):
        return f"<ApplicationTechnologyMapping App:{self.application_component_id} Tech:{self.technology_stack_id or self.technology_node_id or self.system_software_id}>"


# ============================================================================
# Application Component ↔ Business Actor (NEW)
# ============================================================================


class ApplicationBusinessActorMapping(TenantMixin, db.Model):
    """
    Application ↔ Business Actor: Which actors use/own which applications

    Links Business Layer (Actors) to Application Layer (Components).
    Essential for understanding application ownership and usage patterns.
    """

    __tablename__ = "application_business_actor_mapping"
    __tenant_parent__ = (('application_component_id', 'application_components'),)

    id = db.Column(db.Integer, primary_key=True)

    # Foreign Keys
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )
    business_actor_id = db.Column(
        db.Integer, db.ForeignKey("business_actors.id"), nullable=False, index=True
    )

    # Relationship Type
    relationship_type = db.Column(
        db.String(50)
    )  # Owner, Primary User, Secondary User, Administrator, Sponsor

    # Usage Details
    user_count = db.Column(db.Integer)  # Number of users from this actor
    usage_frequency = db.Column(db.String(50))  # Daily, Weekly, Monthly, Occasional
    average_hours_per_week = db.Column(db.Integer)

    # Ownership (if type is Owner)
    is_business_owner = db.Column(db.Boolean, default=False)
    is_technical_owner = db.Column(db.Boolean, default=False)
    budget_responsibility = db.Column(db.Boolean, default=False)

    # Access Level
    access_level = db.Column(db.String(50))  # Admin, Power User, Standard User, Read-Only
    requires_training = db.Column(db.Boolean, default=False)
    training_completed = db.Column(db.Boolean, default=False)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships (will be set up via back_populates in models)

    def __repr__(self):
        return f"<ApplicationBusinessActorMapping App:{self.application_component_id} Actor:{self.business_actor_id} {self.relationship_type}>"


# ============================================================================
# Application Component ↔ Application Interface (NEW)
# ============================================================================


class ApplicationInterfaceMapping(TenantMixin, db.Model):
    """
    Application Component ↔ Application Interface: Which components expose/consume interfaces

    Essential for API governance and integration architecture.
    """

    __tablename__ = "application_interface_mapping"
    __tenant_parent__ = (('application_component_id', 'application_components'),)

    id = db.Column(db.Integer, primary_key=True)

    # Foreign Keys
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )
    application_interface_id = db.Column(
        db.Integer, db.ForeignKey("application_interfaces.id"), nullable=False, index=True
    )

    # Relationship Type
    relationship_type = db.Column(db.String(50))  # Provides, Consumes, Both

    # Usage Metrics
    calls_per_day = db.Column(db.Integer)
    data_volume_mb_per_day = db.Column(db.Numeric(15, 2))
    average_response_time_ms = db.Column(db.Integer)
    error_rate_percent = db.Column(db.Numeric(5, 2))

    # Dependency
    is_critical_dependency = db.Column(db.Boolean, default=False)
    has_fallback = db.Column(db.Boolean, default=False)
    fallback_strategy = db.Column(db.String(200))

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = db.Column(db.Text)

    def __repr__(self):
        return f"<ApplicationInterfaceMapping App:{self.application_component_id} Interface:{self.application_interface_id} {self.relationship_type}>"


# ============================================================================
# Value Stream Association Tables (Week 7 Implementation)
# ============================================================================

# Value Stream Stage ↔ Business Capability (Many-to-Many)
value_stream_stage_capabilities = db.Table(
    "value_stream_stage_capabilities",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "value_stream_stage_id",
        db.Integer,
        db.ForeignKey("unified_value_stream_stages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "business_capability_id",
        db.Integer,
        db.ForeignKey("business_capability.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("capability_role", db.String(50)),  # primary, supporting, enabling
    db.Column("importance", db.String(20)),  # critical, high, medium, low
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


# Value Stream Stage ↔ Business Process (Many-to-Many)
value_stream_stage_processes = db.Table(
    "value_stream_stage_processes",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "value_stream_stage_id",
        db.Integer,
        db.ForeignKey("unified_value_stream_stages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "business_process_id",
        db.Integer,
        db.ForeignKey("business_processes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("process_role", db.String(50)),  # execute, support, monitor
    db.Column("automation_level", db.Integer),  # 0 - 100% automation
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


# Value Stream ↔ Business Capability (Direct Many-to-Many)
value_stream_capabilities = db.Table(
    "value_stream_capabilities",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "value_stream_id",
        db.Integer,
        db.ForeignKey("value_streams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "business_capability_id",
        db.Integer,
        db.ForeignKey("business_capability.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("dependency_type", db.String(50)),  # required, optional, enabling
    db.Column("maturity_impact", db.String(50)),  # high, medium, low
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


# Value Stream ↔ Business Process (Direct Many-to-Many)
value_stream_processes = db.Table(
    "value_stream_processes",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "value_stream_id",
        db.Integer,
        db.ForeignKey("value_streams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column(
        "business_process_id",
        db.Integer,
        db.ForeignKey("business_processes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    db.Column("process_contribution", db.String(50)),  # core, supporting, enabling
    db.Column("sequence_order", db.Integer),  # Order in value stream
    db.Column("created_at", db.DateTime, default=datetime.utcnow, nullable=False),
)


# ============================================================================
# Enterprise Intelligence Association Tables (NEW)
# ============================================================================

# Portfolio Initiative ↔ Application Component
portfolio_initiative_applications = db.Table(
    "portfolio_initiative_applications",
    db.Column(
        "initiative_id", db.Integer, db.ForeignKey("portfolio_initiatives.id"), primary_key=True
    ),
    db.Column(
        "application_id", db.Integer, db.ForeignKey("application_components.id"), primary_key=True
    ),
    db.Column("relationship_type", db.String(50)),  # Impacted, Enabled, Retired, New
    db.Column("priority", db.String(20)),  # Critical, High, Medium, Low
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)

# Application Roadmap Items (application-level roadmap)
application_roadmap_items = db.Table(
    "application_roadmap_items",
    db.Column("id", db.Integer, primary_key=True),
    db.Column(
        "application_id",
        db.Integer,
        db.ForeignKey("application_components.id"),
        nullable=False,
        index=True,
    ),
    db.Column("title", db.String(255), nullable=False),
    db.Column("description", db.Text),
    db.Column("item_type", db.String(50)),  # Feature, Enhancement, Technical Debt, Modernization
    db.Column("status", db.String(50)),  # Planned, In Progress, Completed, Cancelled
    db.Column("priority", db.String(20)),  # Critical, High, Medium, Low
    db.Column("start_date", db.Date),
    db.Column("target_date", db.Date),
    db.Column("completion_date", db.Date),
    db.Column("owner", db.String(200)),
    db.Column("business_value", db.Text),
    db.Column("technical_complexity", db.String(20)),  # Simple, Medium, Complex, Very Complex
    db.Column("estimated_effort_days", db.Integer),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    db.Column("updated_at", db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)
