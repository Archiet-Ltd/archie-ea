"""
Process-Capability-Data Trinity Models

Implements missing semantic traceability between:
- Business Processes (how work is done)
- Business Capabilities (what the business can do)
- Data Entities (what information is needed)

Key EA Intelligence Fix #2:
- Complete BPMN process hierarchy (move from JSON to relational)
- Process-to-Capability mapping (what capabilities enable which processes)
- Data lineage and ownership (what data supports which capabilities and processes)
- Vendor process integration patterns (how vendor products map to processes)
"""

from datetime import datetime
from typing import Optional, Set

from sqlalchemy import event, insert

try:
    from sqlalchemy.ext.associationproxy import association_proxy
except ImportError:
    association_proxy = None

from .. import db
from app.models.mixins import TenantMixin

# ============================================================================
# BUSINESS PROCESS MODEL - BPMN 2.0 Semantic Storage
# ============================================================================


class BusinessProcess(TenantMixin, db.Model):
    # ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
    """
    Business Process definition with full BPMN 2.0 semantic storage.

    Replaces JSON-based process storage with relational model providing:
    - Process hierarchy and decomposition
    - Capability-to-Process mapping
    - Vendor process integration patterns
    - Data flow and lineage tracking

    Examples:
    - "Order-to-Cash Process"
    - "Procure-to-Pay Process"
    - "Hire-to-Retire Process"
    """

    __tablename__ = "business_processes"

    id = db.Column(db.Integer, primary_key=True)

    # Identity
    name = db.Column(db.String(200), nullable=False, index=True)
    process_code = db.Column(db.String(50), unique=True)  # P2P - 001, O2C - 001
    description = db.Column(db.Text)

    # ArchiMate linkage (Business Process is ArchiMate element)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Application association (for application-centric views like /applications/<id>)
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True, index=True
    )

    # Process classification
    process_type = db.Column(db.String(50))  # 'core', 'support', 'management'
    process_category = db.Column(db.String(50))  # 'operational', 'strategic', 'enabling'
    value_chain_stage = db.Column(
        db.String(50)
    )  # 'inbound', 'operations', 'outbound', 'service', 'support'

    # Process hierarchy (self-referential)
    parent_process_id = db.Column(db.Integer, db.ForeignKey("business_processes.id"), index=True)
    level = db.Column(
        db.Integer
    )  # 0=Value Chain, 1=Process Group, 2=Process, 3=Subprocess, 4=Activity
    sequence_order = db.Column(db.Integer)  # Order within parent

    # Capability relationship (Process implements Capability)
    primary_capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id"), index=True
    )

    # APQC Process relationship (1:1 mapping to standard framework)
    apqc_process_id = db.Column(db.Integer, db.ForeignKey("apqc_process.id"), index=True)

    # Process ownership
    process_owner = db.Column(db.String(200))
    business_unit = db.Column(db.String(100))
    responsible_role = db.Column(db.String(100))

    # BPMN attributes
    bpmn_type = db.Column(db.String(50))  # 'process', 'subprocess', 'task', 'event', 'gateway'
    is_automated = db.Column(db.Boolean, default=False)
    automation_percentage = db.Column(db.Integer)  # 0 - 100% automated

    # Process metrics
    cycle_time_hours = db.Column(db.Float)  # Average duration
    frequency = db.Column(db.String(30))  # 'continuous', 'daily', 'weekly', 'monthly', 'quarterly'
    volume_per_period = db.Column(db.Integer)  # Transactions per period
    cost_per_execution = db.Column(db.Numeric(10, 2))
    error_rate_percentage = db.Column(db.Float)

    # Compliance and control
    requires_approval = db.Column(db.Boolean, default=False)
    sox_relevant = db.Column(db.Boolean, default=False)
    gdpr_relevant = db.Column(db.Boolean, default=False)
    control_objectives = db.Column(db.Text)  # JSON array

    # Maturity assessment
    maturity_level = db.Column(db.Integer)  # 1 - 5 CMM levels
    standardization_level = db.Column(
        db.String(30)
    )  # 'ad_hoc', 'defined', 'standardized', 'optimized'
    digitalization_level = db.Column(
        db.String(30)
    )  # 'manual', 'partially_automated', 'fully_automated', 'intelligent'

    # Documentation
    bpmn_model_url = db.Column(db.String(500))  # Link to BPMN diagram
    process_documentation_url = db.Column(db.String(500))
    sop_url = db.Column(db.String(500))  # Standard Operating Procedure

    # Status
    status = db.Column(
        db.String(30), default="active"
    )  # active, deprecated, under_redesign, sunset
    last_reviewed_date = db.Column(db.Date)
    next_review_date = db.Column(db.Date)

    # Metadata
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture = db.relationship("ArchitectureModel", backref="business_processes")
    created_by = db.relationship("User", backref="created_processes")

    # ArchiMate 3.2 Relationships - Commented out to avoid conflicts
    # logical_models = db.relationship('LogicalDataModel',
    #                                 secondary='logical_model_processes',
    #                                 back_populates='business_processes')

    # Process hierarchy
    parent_process = db.relationship(
        "BusinessProcess", remote_side="BusinessProcess.id", backref="subprocesses"
    )

    # Capability relationship
    primary_capability = db.relationship("BusinessCapability", backref="primary_processes")

    # APQC Process relationship (1:1 mapping to standard framework)
    apqc_process = db.relationship("APQCProcess", backref="business_process")

    # Supporting capabilities (many-to-many via junction table)
    supporting_capabilities = db.relationship(
        "BusinessCapability",
        secondary="process_capability_mapping",
        back_populates="supporting_processes",
    )

    # Supporting applications (many-to-many via junction table)
    supporting_applications = db.relationship(
        "ApplicationComponent",
        secondary="application_process_support",
        back_populates="supported_processes",
    )

    decision_links = db.relationship(
        "ADRProcessLink", back_populates="process", cascade="all, delete-orphan"
    )
    decision_records = association_proxy("decision_links", "adr")

    # Data relationships
    input_data_entities = db.relationship(
        "DataEntity",
        secondary="process_data_flow",
        primaryjoin="and_(BusinessProcess.id==process_data_flow.c.process_id, "
        "process_data_flow.c.flow_type=='input')",
        back_populates="consuming_processes",
    )

    output_data_entities = db.relationship(
        "DataEntity",
        secondary="process_data_flow",
        primaryjoin="and_(BusinessProcess.id==process_data_flow.c.process_id, "
        "process_data_flow.c.flow_type=='output')",
        back_populates="producing_processes",
        overlaps="input_data_entities,consuming_processes",
    )

    # Vendor process mapping
    vendor_processes = db.relationship(
        "VendorProcessMapping", back_populates="business_process", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<BusinessProcess {self.process_code or self.name}>"

    def ensure_archimate_element(self):
        """Guarantee a linked ArchiMate Business Process element exists."""
        from .archimate_core import ArchiMateElement  # Local import to avoid circular dependency

        if self.archimate_element_id:
            element = ArchiMateElement.query.get(self.archimate_element_id)
            if element:
                return element

        element = ArchiMateElement(
            name=self.name,
            type="BusinessProcess",
            layer="business",
            description=self.description or f"Business process: {self.name}",
        )
        db.session.add(element)
        db.session.flush()

        self.archimate_element_id = element.id
        return element


# ============================================================================
# JUNCTION TABLES - Process Relationships
# ============================================================================

# Process ↔ Capability (Many-to-Many) - Supporting capabilities
process_capability_mapping = db.Table(
    "process_capability_mapping",
    db.Column(
        "process_id",
        db.Integer,
        db.ForeignKey("business_processes.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "capability_id",
        db.Integer,
        db.ForeignKey("business_capability.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("relationship_type", db.String(30)),  # 'primary', 'supporting', 'enabling'
    db.Column("dependency_level", db.String(20)),  # 'critical', 'high', 'medium', 'low'
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)

# Process ↔ Data (Many-to-Many) - Data flow
process_data_flow = db.Table(
    "process_data_flow",
    db.Column(
        "process_id",
        db.Integer,
        db.ForeignKey("business_processes.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "data_entity_id",
        db.Integer,
        db.ForeignKey("data_entities.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("flow_type", db.String(20)),  # 'input', 'output', 'reference', 'master'
    db.Column("is_critical", db.Boolean, default=False),
    db.Column("data_volume", db.String(50)),  # Estimated volume per execution
    db.Column(
        "data_sensitivity", db.String(20)
    ),  # 'public', 'internal', 'confidential', 'restricted'
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)


# ============================================================================
# VENDOR PROCESS MAPPING - How vendor products map to business processes
# ============================================================================


class VendorProcessMapping(TenantMixin, db.Model):
    """
    Maps vendor product capabilities to business processes.

    Captures HOW vendor products support or automate specific business processes.
    Critical for vendor selection, integration planning, and process optimization.

    Example: SAP S/4HANA Order-to-Cash module → Order-to-Cash Process
    """

    __tablename__ = "vendor_process_mappings"

    id = db.Column(db.Integer, primary_key=True)

    # Core relationships
    vendor_product_id = db.Column(
        db.Integer, db.ForeignKey("vendor_products.id"), nullable=False, index=True
    )
    business_process_id = db.Column(
        db.Integer, db.ForeignKey("business_processes.id"), nullable=False, index=True
    )

    # Process support metrics
    support_level = db.Column(
        db.String(30)
    )  # 'full_automation', 'partial_automation', 'supporting_tool', 'manual_workaround'
    automation_coverage = db.Column(db.Integer)  # 0 - 100% of process automated by vendor product
    out_of_box_fit = db.Column(db.Integer)  # 0 - 100% fit without customization

    # Integration pattern
    integration_pattern = db.Column(db.String(50))  # 'native', 'api', 'batch', 'manual', 'custom'
    integration_complexity = db.Column(db.String(20))  # 'low', 'medium', 'high', 'very_high'

    # Gap analysis
    gaps = db.Column(db.Text)  # JSON array of functionality gaps
    workarounds = db.Column(db.Text)  # JSON array of required workarounds
    customization_required = db.Column(db.Boolean, default=False)
    customization_scope = db.Column(db.Text)

    # Process improvement potential
    expected_cycle_time_reduction = db.Column(db.Float)  # Percentage reduction
    expected_cost_reduction = db.Column(db.Float)  # Percentage reduction
    expected_error_rate_reduction = db.Column(db.Float)  # Percentage reduction

    # Implementation
    implementation_effort_weeks = db.Column(db.Integer)
    configuration_complexity = db.Column(db.String(20))
    change_management_impact = db.Column(
        db.String(20)
    )  # 'low', 'medium', 'high', 'transformational'

    # Evidence
    reference_implementation_url = db.Column(db.String(500))
    case_study_url = db.Column(db.String(500))
    vendor_documentation_url = db.Column(db.String(500))

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    validated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    vendor_product = db.relationship("VendorProduct", backref="process_mappings")
    business_process = db.relationship("BusinessProcess", back_populates="vendor_processes")
    validated_by = db.relationship("User", backref="validated_process_mappings")

    # Unique constraint
    __table_args__ = (
        db.UniqueConstraint(
            "vendor_product_id", "business_process_id", name="uq_vendor_process_mapping"
        ),
    )

    def __repr__(self):
        return f"<VendorProcessMapping {self.vendor_product.name if self.vendor_product else 'Unknown'} → {self.business_process.name if self.business_process else 'Unknown'}>"


# ============================================================================
# DATA DOMAIN & ENTITY MODELS - Information Architecture
# ============================================================================


class DataDomain(TenantMixin, db.Model):
    # ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
    """
    High-level data domain representing a logical grouping of related data entities.

    Examples:
    - Customer Data Domain
    - Product Data Domain
    - Financial Data Domain
    - Operational Data Domain
    """

    __tablename__ = "data_domains"

    id = db.Column(db.Integer, primary_key=True)

    # Identity
    name = db.Column(db.String(200), nullable=False, index=True)
    code = db.Column(db.String(50), unique=True)  # CUST, PROD, FIN, OPS
    description = db.Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Domain classification
    domain_type = db.Column(db.String(50))  # 'master', 'transactional', 'reference', 'analytical'
    criticality = db.Column(
        db.String(20)
    )  # 'mission_critical', 'business_critical', 'important', 'supporting'

    # Ownership and governance
    data_owner = db.Column(db.String(200))
    data_steward = db.Column(db.String(200))
    business_custodian = db.Column(db.String(200))
    technical_custodian = db.Column(db.String(200))

    # Capability ownership (which capability owns/manages this data domain)
    owning_capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id"), index=True
    )

    # Golden source: the application that masters this domain's data (nullable)
    golden_source_application_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True, index=True
    )

    # Data quality
    data_quality_score = db.Column(db.Integer)  # 0 - 100
    completeness_percentage = db.Column(db.Float)
    accuracy_percentage = db.Column(db.Float)
    timeliness_rating = db.Column(db.Integer)  # 1 - 10

    # Compliance and security
    contains_pii = db.Column(db.Boolean, default=False)
    contains_sensitive_data = db.Column(db.Boolean, default=False)
    gdpr_scope = db.Column(db.Boolean, default=False)
    data_classification = db.Column(
        db.String(30)
    )  # 'public', 'internal', 'confidential', 'restricted'
    retention_period_years = db.Column(db.Integer)

    # Metadata
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture = db.relationship("ArchitectureModel", backref="data_domains")
    created_by = db.relationship("User", backref="created_data_domains")
    owning_capability = db.relationship("BusinessCapability", backref="owned_data_domains")
    golden_source_application = db.relationship(
        "ApplicationComponent", foreign_keys=[golden_source_application_id]
    )
    entities = db.relationship("DataEntity", back_populates="domain", cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint(
            "organization_id", "name", name="uq_data_domains_org_name"
        ),
    )

    def __repr__(self):
        return f"<DataDomain {self.name}>"


class DataEntity(TenantMixin, db.Model):
    # ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
    """
    Represents a data entity/object managed within a data domain.

    Examples:
    - Customer
    - Product
    - Order
    - Invoice
    - Employee

    Provides data lineage, capability-data mapping, and process-data flow tracking.
    """

    __tablename__ = "data_entities"

    id = db.Column(db.Integer, primary_key=True)

    # Identity
    name = db.Column(db.String(200), nullable=False, index=True)
    business_name = db.Column(db.String(200))  # Business-friendly name
    technical_name = db.Column(db.String(200))  # Database table/entity name
    description = db.Column(db.Text)

    # Domain relationship
    domain_id = db.Column(db.Integer, db.ForeignKey("data_domains.id"), nullable=False, index=True)

    # ArchiMate linkage (Basecoat pattern compliance)
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Entity classification
    entity_type = db.Column(db.String(50))  # 'master', 'transactional', 'reference', 'derived'
    is_master_data = db.Column(db.Boolean, default=False)
    master_data_scope = db.Column(db.String(30))  # 'global', 'regional', 'local'

    # Capability relationship (which capability manages/owns this entity)
    owning_capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id"), index=True
    )

    # Data volume and growth
    estimated_record_count = db.Column(db.BigInteger)
    growth_rate_percentage = db.Column(db.Float)  # Annual growth rate
    data_volume_size_gb = db.Column(db.Float)

    # Data quality
    data_quality_score = db.Column(db.Integer)  # 0 - 100
    duplicate_rate_percentage = db.Column(db.Float)
    completeness_percentage = db.Column(db.Float)
    accuracy_percentage = db.Column(db.Float)

    # Compliance and security
    contains_pii = db.Column(db.Boolean, default=False)
    pii_fields = db.Column(db.Text)  # JSON array of PII field names
    data_classification = db.Column(
        db.String(30)
    )  # 'public', 'internal', 'confidential', 'restricted'
    encryption_required = db.Column(db.Boolean, default=False)
    anonymization_required = db.Column(db.Boolean, default=False)

    # GDPR/Privacy
    gdpr_article_13_applies = db.Column(db.Boolean, default=False)  # Right to be informed
    gdpr_article_17_applies = db.Column(db.Boolean, default=False)  # Right to erasure
    retention_period_days = db.Column(db.Integer)
    legal_basis_for_processing = db.Column(db.String(100))

    # System implementation
    system_of_record = db.Column(db.String(200))  # Which system is authoritative source
    # The declared system of record as a real application (nullable: undeclared
    # until a data architect declares it). The text column above is kept in step
    # with this application's name when a declaration is made.
    system_of_record_application_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True, index=True
    )
    system_of_record_declared_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    system_of_record_declared_at = db.Column(db.DateTime, nullable=True)
    replication_frequency = db.Column(db.String(30))  # 'real_time', 'hourly', 'daily', 'batch'

    # Metadata
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Data Governance
    access_level = db.Column(
        db.String(30), default="public"
    )  # public, internal, restricted, confidential
    access_roles = db.Column(db.JSON, nullable=True)  # List of roles with access
    last_accessed = db.Column(db.DateTime, nullable=True)  # Last access timestamp
    auto_delete_date = db.Column(db.DateTime, nullable=True)  # Automatic deletion date
    retention_reason = db.Column(db.String(255), nullable=True)  # Reason for retention

    # Relationships
    domain = db.relationship("DataDomain", back_populates="entities")
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture = db.relationship("ArchitectureModel", backref="data_entities")
    created_by = db.relationship(
        "User", foreign_keys=[created_by_id], backref="created_data_entities"
    )
    owning_capability = db.relationship("BusinessCapability", backref="owned_data_entities")
    system_of_record_application = db.relationship(
        "ApplicationComponent", foreign_keys=[system_of_record_application_id]
    )

    # Data Architecture Relationships
    conceptual_models = db.relationship(
        "ConceptualDataModel", secondary="conceptual_model_entities", back_populates="data_entities"
    )
    data_lineage = db.relationship(
        "DataLineage", secondary="data_lineage_entities", back_populates="data_entities"
    )

    # Process relationships (via junction tables)
    consuming_processes = db.relationship(
        "BusinessProcess",
        secondary="process_data_flow",
        primaryjoin="and_(DataEntity.id==process_data_flow.c.data_entity_id, "
        "process_data_flow.c.flow_type=='input')",
        back_populates="input_data_entities",
        overlaps="output_data_entities,producing_processes",
    )

    producing_processes = db.relationship(
        "BusinessProcess",
        secondary="process_data_flow",
        primaryjoin="and_(DataEntity.id==process_data_flow.c.data_entity_id, "
        "process_data_flow.c.flow_type=='output')",
        back_populates="output_data_entities",
        overlaps="input_data_entities,consuming_processes",
    )

    def __repr__(self):
        return f"<DataEntity {self.name} ({self.entity_type})>"

    def ensure_archimate_element(self):
        """Ensure this data entity has an ArchiMate element (Data Object)."""
        from .archimate_core import ArchiMateElement  # Local import to avoid circular dependency

        if self.archimate_element_id:
            element = ArchiMateElement.query.get(self.archimate_element_id)
            if element:
                return element

        element = ArchiMateElement(
            name=self.name,
            type="DataObject",
            layer="application",
            description=self.description or f"Data object for entity: {self.name}",
        )
        db.session.add(element)
        db.session.flush()

        self.archimate_element_id = element.id
        return element


# Auto-create an ArchiMate element when a DataEntity is created, mirroring the
# strategy_layer.py pattern (see app/models/strategy_layer.py::_link_strategy_archimate).
#
# ArchiMate 3.2 is the backbone, not a view — but ArchiMate 3.2 has no "DataEntity"
# element type. A DataEntity is a data-architecture catalogue concept, so it mirrors
# as the closest ArchiMate 3.2 fit: an Application-layer "DataObject" (this is what
# ``ensure_archimate_element`` above already builds, but nothing on the create path
# called it — ``create_data_entity`` in data_architecture_routes.py inserted the row
# directly, so entities created via the UI never got an element. The AI assistant,
# which reads the ArchiMate layer, would report none of them existed even though the
# catalogue page listed them).
#
# This runs as a mapper ``after_insert`` event, i.e. INSIDE the unit-of-work flush.
# Writing through ``db.session`` there (session.add + flush) would be a reentrant
# flush — "Session is already flushing" — so this writes through the event's own
# ``connection`` (Core, not the session) and reflects the new FK with
# ``set_committed_value`` so no further flush is provoked. A Core insert does NOT
# re-fire ORM mapper events, so there is no cascade.
#
# DataEntity is TenantMixin as of ADR-0003, so the mirror takes its tenant from
# the row itself: before_flush stamps target.organization_id before the flush
# that fires this event, making it strictly more reliable than the ambient
# g.current_org_id (which the ArchiMateElement column default would otherwise
# read, and which is absent outside a request context).
def _link_data_entity_archimate(connection, target, organization_id=None):
    """Create the ArchiMateElement and point ``target.archimate_element_id`` at it.

    ``organization_id`` is an explicit override for callers that hold rows
    predating tenancy (the backfill command). Left as ``None`` (the
    mapper-event call site), the tenant is read from ``target.organization_id``,
    falling back to ArchiMateElement.organization_id's own column default, which resolves the
    tenant from ``g.current_org_id``.
    """
    from sqlalchemy.orm.attributes import set_committed_value

    from .archimate_core import ArchiMateElement

    archimate_table = ArchiMateElement.__table__
    values = {
        "name": target.name,
        "type": "DataObject",
        "layer": "application",
        "description": target.description or f"Data object for entity: {target.name}",
    }
    effective_org = (
        organization_id
        if organization_id is not None
        else getattr(target, "organization_id", None)
    )
    if effective_org is not None:
        values["organization_id"] = effective_org

    result = connection.execute(archimate_table.insert().values(**values))
    new_id = result.inserted_primary_key[0]

    connection.execute(
        target.__table__.update()
        .where(target.__table__.c.id == target.id)
        .values(archimate_element_id=new_id)
    )
    # keep the in-memory object consistent without marking it dirty (no re-flush)
    set_committed_value(target, "archimate_element_id", new_id)


@event.listens_for(DataEntity, "after_insert")
def create_archimate_data_entity(mapper, connection, target):
    """Automatically create/link an ArchiMateElement (type DataObject) for a new DataEntity."""
    if not target.archimate_element_id:
        _link_data_entity_archimate(connection, target)


def _ensure_archimate_relationship(
    session,
    rel_type: str,
    source_id: Optional[int],
    target_id: Optional[int],
    architecture_id: Optional[int],
):
    """Create the relationship if it does not already exist."""
    if not source_id or not target_id:
        return None

    from .archimate_core import ArchiMateRelationship

    query = session.query(ArchiMateRelationship).filter_by(
        type=rel_type, source_id=source_id, target_id=target_id
    )

    if architecture_id is None:
        query = query.filter(ArchiMateRelationship.architecture_id.is_(None))
    else:
        query = query.filter(ArchiMateRelationship.architecture_id == architecture_id)

    relationship = query.one_or_none()
    if relationship:
        return relationship

    relationship = ArchiMateRelationship(
        type=rel_type, source_id=source_id, target_id=target_id, architecture_id=architecture_id
    )
    session.add(relationship)
    return relationship


def _resolve_architecture_id(process_element, target_element, process):
    if process_element and getattr(process_element, "architecture_id", None):
        return process_element.architecture_id
    if target_element and getattr(target_element, "architecture_id", None):
        return target_element.architecture_id
    return getattr(process, "architecture_id", None)


def _sync_process_relationships(process: "BusinessProcess", session) -> None:
    """Ensure mandatory relationships for a business process exist."""
    if process is None:
        return

    process_element = process.archimate_element or process.ensure_archimate_element()
    if not process_element:
        return

    # Realization relationships to capabilities
    capability_elements = []
    if process.primary_capability:
        capability_elements.append(process.primary_capability.ensure_archimate_element())
    if process.supporting_capabilities:
        for capability in process.supporting_capabilities:
            capability_elements.append(capability.ensure_archimate_element())

    seen_targets: Set[int] = set()
    for element in capability_elements:
        if not element or element.id in seen_targets:
            continue
        seen_targets.add(element.id)
        architecture_id = _resolve_architecture_id(process_element, element, process)
        _ensure_archimate_relationship(
            session=session,
            rel_type="realization",
            source_id=process_element.id,
            target_id=element.id,
            architecture_id=architecture_id,
        )

    # Access relationships to data entities (read + write)
    data_elements = []
    for entity in getattr(process, "input_data_entities", []):
        if entity:
            data_elements.append(entity.ensure_archimate_element())
    for entity in getattr(process, "output_data_entities", []):
        if entity:
            data_elements.append(entity.ensure_archimate_element())

    seen_data: Set[int] = set()
    for element in data_elements:
        if not element or element.id in seen_data:
            continue
        seen_data.add(element.id)
        architecture_id = _resolve_architecture_id(process_element, element, process)
        _ensure_archimate_relationship(
            session=session,
            rel_type="access",
            source_id=process_element.id,
            target_id=element.id,
            architecture_id=architecture_id,
        )


def sync_all_business_process_relationships(session=None) -> int:
    """Backfill required relationships for every business process."""
    session = session or db.session

    processes = session.query(BusinessProcess).all()
    for process in processes:
        _sync_process_relationships(process, session)

    session.commit()
    return len(processes)


@event.listens_for(BusinessProcess, "before_insert")
def create_businessprocess_archimate_element(mapper, connection, target):
    if target.archimate_element_id is not None:
        return

    from .archimate_core import ArchiMateElement

    result = connection.execute(
        insert(ArchiMateElement.__table__).values(
            organization_id=target.organization_id,
            name=target.name,
            type="BusinessProcess",
            layer="business",
            description=target.description or f"Business process: {target.name}",
        )
    )
    target.archimate_element_id = result.inserted_primary_key[0]


def _link_process_relationship_via_connection(
    connection, rel_type, source_id, target_id, architecture_id
):
    """Create an ArchiMate relationship using the flush's own Core connection.

    Used from ``after_insert``/``after_update`` mapper events, which run INSIDE the
    unit-of-work flush. Writing through ``db.session`` there triggers a reentrant
    ``db.session.flush()`` → "Session is already flushing" that poisons the whole
    transaction (UIQA-005: this broke document-upload element creation). A Core
    insert on the event ``connection`` does not re-enter the flush and does not
    re-fire ORM events, so there is no cascade.
    """
    if not source_id or not target_id:
        return

    from .archimate_core import ArchiMateRelationship

    rel_table = ArchiMateRelationship.__table__
    sel = rel_table.select().where(
        (rel_table.c.type == rel_type)
        & (rel_table.c.source_id == source_id)
        & (rel_table.c.target_id == target_id)
    )
    if architecture_id is None:
        sel = sel.where(rel_table.c.architecture_id.is_(None))
    else:
        sel = sel.where(rel_table.c.architecture_id == architecture_id)

    if connection.execute(sel.limit(1)).first():
        return

    connection.execute(
        rel_table.insert().values(
            type=rel_type,
            source_id=source_id,
            target_id=target_id,
            architecture_id=architecture_id,
        )
    )


def _sync_process_relationships_via_connection(connection, process: "BusinessProcess") -> None:
    """Flush-safe variant of ``_sync_process_relationships`` for mapper events.

    The process's own ArchiMate element is created in the ``before_insert`` event, so we
    read its id directly rather than re-fetching it. We only link capabilities and data
    entities that ALREADY have an ArchiMate element — their elements are created by their
    own mapper events, never here — and every write goes through the event ``connection``.
    """
    if process is None:
        return

    process_element_id = process.archimate_element_id
    if not process_element_id:
        return

    architecture_id = getattr(process, "architecture_id", None)

    # Realization relationships to capabilities
    capabilities = []
    if process.primary_capability:
        capabilities.append(process.primary_capability)
    for capability in process.supporting_capabilities or []:
        capabilities.append(capability)

    seen: Set[int] = set()
    for capability in capabilities:
        cap_eid = getattr(capability, "archimate_element_id", None)
        if not cap_eid or cap_eid in seen:
            continue
        seen.add(cap_eid)
        _link_process_relationship_via_connection(
            connection, "realization", process_element_id, cap_eid, architecture_id
        )

    # Access relationships to data entities (read + write)
    entities = list(getattr(process, "input_data_entities", []) or []) + list(
        getattr(process, "output_data_entities", []) or []
    )
    seen_data: Set[int] = set()
    for entity in entities:
        de_eid = getattr(entity, "archimate_element_id", None)
        if not de_eid or de_eid in seen_data:
            continue
        seen_data.add(de_eid)
        _link_process_relationship_via_connection(
            connection, "access", process_element_id, de_eid, architecture_id
        )


@event.listens_for(BusinessProcess, "after_insert")
def sync_process_relationships_after_insert(mapper, connection, target):
    _sync_process_relationships_via_connection(connection, target)


@event.listens_for(BusinessProcess, "after_update")
def sync_process_relationships_after_update(mapper, connection, target):
    _sync_process_relationships_via_connection(connection, target)
