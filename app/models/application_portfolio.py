"""
Application Portfolio Management

Comprehensive application portfolio management system for capability mapping.
Supports application inventory, lifecycle management, and strategic alignment.
Provides foundation for application-capability gap analysis.

Features:
- Application inventory and classification
- Technology stack management
- Financial and performance tracking
- Strategic importance assessment
- Lifecycle management
- Vendor and contract management
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional  # dead-code-ok

from sqlalchemy import (  # dead-code-ok
    JSON,
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    event,
)
from sqlalchemy.orm import relationship, validates  # dead-code-ok

from .. import db
from .mixins import OptimisticLockMixin, TenantMixin

logger = logging.getLogger(__name__)

# Re-export ApplicationCapabilityMapping for backward compatibility
from .application_capability import ApplicationCapabilityMapping

# Import junction table for vendor products relationship
from .relationship_tables import (
    application_component_vendor_products,
    application_compliance_realization,
)

# Note: Junction tables are referenced by string name to avoid circular imports
# Tables: 'application_business_actor_mapping', 'application_interface_mapping',
# 'application_requirement_mapping', 'application_technology_mapping', 'application_process_support',
# 'application_component_vendor_products'

# The TOGAF-decommission-phase vocabulary this app actually uses for
# lifecycle_status (rendered by list_simple.html's STATUS_MAP badge lookup,
# which is case-sensitive on these exact lowercase strings). Any write path
# that sets lifecycle_status — not just the create/edit form — must validate
# against this set, not app.models.constants.LifecycleStatus (a different,
# unrelated generic vocabulary; a bulk-update route validating against that
# one rejected every value this UI actually sends — see api_bulk_lifecycle).
APPLICATION_LIFECYCLE_STAGES = [
    "1. undetermined",
    "2.1 strategic",
    "2.2 tactical",
    "3. sunset",
    "4.1 decom decided",
    "4.2 decom planned",
    "4.3 read-only",
    "4.4 stopped",
    "5. decommissioned",
]

# Canonical assessed-value vocabularies. Null and any value outside these sets
# mean "not assessed"; consumers must not present arbitrary strings as facts.
APPLICATION_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
APPLICATION_HEALTH_STATUSES = frozenset({"healthy", "at_risk", "critical"})


class ApplicationComponent(TenantMixin, db.Model, OptimisticLockMixin):
    """
    Application Component Model

    Core application portfolio management.
    Represents individual applications in the enterprise portfolio.
    """

    __tablename__ = "application_components"
    __table_args__ = {"extend_existing": True}

    # 'version' on this model is the application's own release number ("2.1.0"),
    # a String the user edits — not a lock counter. It collides by name with
    # OptimisticLockMixin's integer column, so the lock gets its own name here.
    #
    # This previously read `__mapper_args__ = {}`, which resolved the collision
    # by switching optimistic locking off altogether. Nothing failed visibly:
    # two architects editing the same application both saved, and the second
    # silently overwrote the first. ApplicationComponent is the most-edited
    # entity in the product, so it is the row where that matters most.
    #
    # Nullable on purpose — reconcile-schema cannot add a NOT NULL column to a
    # table that already has rows. The server_default is what matters: it makes
    # reconcile-schema emit ADD COLUMN ... DEFAULT 1, so existing applications
    # arrive at version 1 rather than NULL. A NULL here would be worse than the
    # bug being fixed — SQLAlchemy would compare `lock_version = NULL`, match no
    # row, and refuse every save on that application.
    lock_version = Column(db.Integer, nullable=True, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": lock_version}

    id = Column(db.Integer, primary_key=True)

    # Application identity
    name = Column(db.String(256), nullable=False, index=True)
    description = Column(db.Text)
    application_code = Column(
        db.String(50), unique=True, index=True
    )  # e.g., SAP-ERP - 01, CRM-SF - 02

    # Application classification
    component_type = Column(db.String(100))  # Component type for categorization
    application_type = Column(
        db.String(50)
    )  # enterprise, departmental, personal, mobile, web
    application_category = Column(
        db.String(50)
    )  # erp, crm, scm, hcm, bi, custom, legacy
    deployment_model = Column(db.String(30))  # on_premise, cloud, saas, hybrid, mobile
    # No default. It used to default to "development", which meant every
    # application whose deployment status had never been recorded was DISPLAYED
    # as "Development" -- a warning badge and a "Lifecycle Stage: Development"
    # tile on the detail page, sitting directly above an "Application Identity /
    # Lifecycle Status: Production" row read from lifecycle_status. The user
    # cannot tell that invented value from one somebody entered, which is
    # exactly the failure CLAUDE.md's "never invent data" rule names. Unrecorded
    # is NULL, and NULL renders as an em dash / no badge.
    deployment_status = Column(
        db.String(50)
    )  # development, testing, staging, production, done
    criticality = Column(
        db.String(20)
    )  # mission_critical, important, supporting, optional
    business_criticality = Column(db.String(50))  # Critical, High, Medium, Low

    # Business context
    business_domain = Column(db.String(100))  # e.g., Sales, HR, Finance, Manufacturing
    business_purpose = Column(db.Text)
    business_functions = Column(db.Text)  # JSON array of business functions
    user_base_size = Column(db.Integer)  # Number of users
    user_types = Column(
        db.Text
    )  # JSON array of user types (employees, customers, partners)
    user_count = Column(db.Integer)  # Total user count
    user_type = Column(db.String(100))  # Primary user type
    concurrent_users_max = Column(db.Integer)  # Maximum concurrent users
    average_daily_users = Column(db.Integer)  # Average daily active users

    # ACM (Application Capability Model) Technical Domains
    acm_domains = Column(
        db.Text
    )  # JSON array: USER-EXPERIENCE, APPLICATION-SERVICES, DATA-STORAGE, SECURITY-IDENTITY, DEVOPS-PLATFORM, AI-ANALYTICS, COMMUNICATION
    acm_primary_domain = Column(db.String(50))  # Primary ACM domain
    acm_capability_level = Column(db.String(10))  # L0, L1, L2, L3, L4

    # Strategic alignment
    strategic_importance = Column(db.String(20))  # critical, high, medium, low
    business_value = Column(db.String(20))  # high, medium, low
    competitive_advantage = Column(db.Boolean, default=False)
    differentiation_level = Column(db.String(20))  # core, differentiating, commodity

    # Technology stack
    technology_stack = Column(db.Text)  # JSON array of technologies
    programming_languages = Column(db.Text)  # JSON array of languages
    frameworks = Column(
        db.Text
    )  # JSON array of frameworks (e.g., React, Angular, Spring)
    database_platforms = Column(db.Text)  # JSON array of databases
    primary_database = Column(
        db.String(200)
    )  # Primary database technology (e.g., PostgreSQL, MySQL)
    cache_technology = Column(
        db.String(200)
    )  # Caching technology (e.g., Redis, Memcached)
    message_queue = Column(
        db.String(200)
    )  # Message queue technology (e.g., Kafka, RabbitMQ)
    integration_methods = Column(db.Text)  # JSON array of integration methods
    api_available = Column(db.Boolean, default=False)
    api_documentation = Column(db.String(255))
    exposes_api = Column(
        db.Boolean, default=False
    )  # Whether the application exposes an API
    integration_pattern = Column(
        db.String(100)
    )  # Integration pattern (e.g., REST, SOAP, Event-driven)

    # Financial context
    total_cost_of_ownership = Column(db.Float)  # Annual TCO
    license_cost = Column(db.Float)  # Annual license cost
    license_type = Column(
        db.String(100)
    )  # License type (e.g., perpetual, subscription, open-source)
    license_cost_annual = Column(db.Float)  # Annual license cost (explicit)
    maintenance_cost = Column(db.Float)  # Annual maintenance cost
    infrastructure_cost = Column(db.Float)  # Annual infrastructure cost
    infrastructure_cost_monthly = Column(db.Float)  # Monthly infrastructure cost
    support_cost = Column(db.Float)  # Annual support cost
    implementation_cost = Column(db.Float)  # One-time implementation cost
    development_cost_annual = Column(db.Float)  # Annual development cost
    roi_score = Column(db.Float)  # ROI percentage

    # Vendor information
    vendor_name = Column(db.String(100))  # Legacy: text field for vendor name
    vendor_type = Column(db.String(30))  # commercial, open_source, internal, custom
    contract_type = Column(db.String(30))  # perpetual, subscription, custom_development
    contract_expiry_date = Column(db.Date)
    support_level = Column(db.String(30))  # basic, standard, premium, custom

    # Primary Vendor Product (Option B+: Direct FK for main vendor product)
    # This is the primary/main vendor product this application is based on
    # For additional vendor products, use the vendor_products M:M relationship
    vendor_product_id = Column(
        db.Integer,
        ForeignKey(
            "vendor_products.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_application_components_vendor_product_id",
        ),
        nullable=True,
        index=True,
    )

    # Lifecycle management
    # ARCH-031: was default="operational", which meant an application created
    # with only a name silently claimed to be live in production and rendered
    # a green "Operational" badge — fabricated data per CLAUDE.md. Null now
    # means "not assessed"; list_simple.html's STATUS_MAP renders that as a
    # neutral "Not set" badge, never a green one.
    lifecycle_status = Column(
        db.String(20), default=None
    )  # planning, development, testing, operational, deprecated, retired
    # healthy, at_risk, critical. my_applications' dashboard and health overview
    # both read app.health_status, but no such column existed on this model - so
    # every owned application reported "unknown" and the health page could never
    # show anything else. Nullable, because reconcile-schema only adds nullable
    # columns; None keeps meaning "not assessed".
    health_status = Column(db.String(20))
    implementation_date = Column(db.Date)
    go_live_date = Column(db.Date)  # Date the application went live
    last_major_upgrade = Column(db.Date)
    planned_retirement_date = Column(db.Date)
    end_of_life_date = Column(db.Date)  # End of life date
    technology_age_years = Column(db.Integer)  # Age of core technology

    # Performance and quality
    availability_target = Column(db.Float)  # Target availability percentage
    availability_actual = Column(db.Float)  # Actual availability percentage
    performance_rating = Column(db.String(20))  # excellent, good, fair, poor
    user_satisfaction_score = Column(db.Float)  # User satisfaction 0 - 100
    defect_density = Column(db.Float)  # Defects per thousand lines of code

    # Integration and architecture
    integration_complexity = Column(db.String(20))  # low, medium, high
    number_of_integrations = Column(db.Integer, default=0)
    interfaces_count = Column(db.Integer, default=0)  # Number of interfaces
    dependencies_count = Column(db.Integer, default=0)  # Number of dependencies
    architecture_style = Column(
        db.String(30)
    )  # monolithic, service_oriented, microservices, event_driven
    data_architecture = Column(db.String(30))  # centralized, distributed, hybrid
    primary_data_store = Column(db.String(200))  # Primary data store
    database_size_gb = Column(db.Float)  # Database size in GB

    # Security and compliance
    data_classification = Column(
        db.String(255)
    )  # public, internal, confidential, restricted
    security_level = Column(db.String(255))  # low, medium, high, critical
    compliance_requirements = Column(db.Text)  # JSON array of compliance requirements
    security_certifications = Column(db.Text)  # JSON array of security certifications

    # Governance
    application_owner = Column(db.String(100))
    business_owner = Column(db.String(100))
    technical_owner = Column(db.String(100))
    technical_lead = Column(db.String(100))  # Technical lead for the application
    product_manager = Column(db.String(100))
    development_team = Column(db.String(100))
    support_team = Column(db.String(100))
    architecture_domain = Column(
        db.String(100)
    )  # Architecture domain (e.g., Data, Integration)

    # Risk assessment
    technical_risk = Column(db.String(20))  # low, medium, high, critical
    business_risk = Column(db.String(20))  # low, medium, high, critical
    vendor_risk = Column(db.String(20))  # low, medium, high, critical
    obsolescence_risk = Column(db.String(20))  # low, medium, high, critical

    # Manufacturing specific
    manufacturing_critical = Column(db.Boolean, default=False)
    manufacturing_processes_supported = Column(
        db.Text
    )  # JSON array of manufacturing processes
    shop_floor_system = Column(db.Boolean, default=False)
    real_time_requirements = Column(db.Boolean, default=False)

    # Technical Architecture (from application_layer.py)
    archimate_element_id = Column(
        db.Integer,
        ForeignKey(
            "archimate_elements.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_application_components_archimate_element_id",
        ),
        nullable=True,
        index=True,
    )
    version = Column(db.String(50), index=True)
    version_control_url = Column(db.Text)  # Git repository URL
    repository_type = Column(db.String(50))  # GitHub, GitLab, Bitbucket, Azure DevOps
    main_branch = Column(db.Text, default="main")

    # Deployment & Infrastructure (from application_layer.py)
    cloud_provider = Column(db.Text)  # AWS, Azure, GCP, On-Prem
    deployment_region = Column(db.Text)
    container_image = Column(db.Text)  # Docker image name
    kubernetes_namespace = Column(db.Text)

    # Performance & SLAs (from application_layer.py)
    response_time_target_ms = Column(db.Integer)
    throughput_target_tps = Column(db.Integer)  # Transactions per second
    current_response_time_ms = Column(db.Integer)
    current_throughput_tps = Column(db.Integer)
    scalability_model = Column(db.String(50))  # Horizontal, Vertical, Auto-scaling
    max_instances = Column(db.Integer)
    min_instances = Column(db.Integer)

    # Availability & Reliability (from application_layer.py)
    sla_availability_percentage = Column(db.Numeric(5, 2))  # 99.9%
    current_uptime_percentage = Column(db.Numeric(5, 2))
    disaster_recovery_enabled = Column(db.Boolean, default=False)
    rpo_hours = Column(db.Integer)  # Recovery Point Objective
    rto_hours = Column(db.Integer)  # Recovery Time Objective
    backup_frequency = Column(db.String(50))
    last_backup_date = Column(db.DateTime)

    # Security & Compliance (from application_layer.py)
    authentication_method = Column(db.Text)  # OAuth2, SAML, LDAP, API Key
    authorization_model = Column(db.String(50))  # RBAC, ABAC
    encryption_at_rest = Column(db.Boolean, default=False)
    encryption_in_transit = Column(db.Boolean, default=True)
    pii_data_processed = Column(db.Boolean, default=False)
    gdpr_compliant = Column(db.Boolean, default=False)
    compliance_tags = Column(db.Text)  # JSON: ["PCI-DSS", "HIPAA", "SOC2"]
    last_security_audit_date = Column(db.Date)
    last_penetration_test_date = Column(db.Date)

    # Metadata
    discovered_by_ai = Column(db.Boolean, default=True)
    discovery_confidence = Column(db.Float)
    last_assessed = Column(db.DateTime, default=datetime.utcnow)
    assessment_notes = Column(db.Text)
    notes = Column(db.Text)  # General notes field for functional capabilities etc.
    tags = Column(db.JSON, default=list)  # migration-exempt — PLT-022: user-defined labels e.g. ["Cloud Migration Wave 2", "Legacy"]

    # Import fields for auto-mapping functionality
    imported_capabilities = Column(db.Text)  # JSON: [{name, description, level}]
    application_services = Column(
        db.Text
    )  # JSON: [{name, type, description, endpoints}]
    application_functions_text = Column(db.Text)  # unrendered-field-ok: legacy comma-separated mirror of business_functions (JSON array), which is what fact_sheet.html/edit.html render
    imported_apqc_codes = Column(db.Text)  # JSON: [process_codes]

    # Abacus integration fields
    external_id = Column(db.String(255), unique=True, index=True)  # External system ID
    abacus_source = Column(db.Boolean, default=False)  # Whether sourced from Abacus
    last_sync_from_abacus = Column(db.DateTime)  # Last sync timestamp
    abacus_properties = Column(db.JSON)  # unrendered-field-ok: raw unstructured sync payload, not a business-user-facing field; abacus_source/last_sync_from_abacus already show the sync state that matters to a reader
    confidence_score = Column(db.Float)  # AI confidence in mapping

    # Power Platform CoE integration
    data_source = Column(db.String(50), nullable=True)        # 'power_platform_coe', 'abacus', 'manual'
    source_identifier = Column(db.String(255), nullable=True)  # external GUID for dedup (Power App GUID)
    provenance = Column(db.JSON, nullable=True)                # unrendered-field-ok: raw API response snapshot for audit/debugging, not a business-user-facing field; data_source/source_identifier already show what matters to a reader

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Soft-delete recovery window (finding A-04/ARCH-051/C-10). Nullable —
    # reconcile-schema only adds nullable columns; NULL means "not deleted".
    # Only the bulk-delete endpoint uses this path today; the single-record
    # delete route still hard-deletes.
    deleted_at = Column(db.DateTime, nullable=True)
    deleted_by = Column(db.Integer, nullable=True)

    # Data quality indicators
    @property
    def data_freshness_days(self):
        """Days since last meaningful update."""
        if self.updated_at is None:
            return None
        return (datetime.utcnow() - self.updated_at).days

    @property
    def data_freshness_tier(self):
        """Freshness tier: fresh (<90d), stale (90-180d), outdated (>180d), unknown."""
        days = self.data_freshness_days
        if days is None:
            return "unknown"
        if days < 90:
            return "fresh"
        if days < 180:
            return "stale"
        return "outdated"

    # M3: this used to carry its own unweighted 5-category/15-field rubric,
    # completely independent of app/services/application_fact_sheet.py's
    # weighted 11-field one -- so /applications/<id> (this property, via
    # applications/dashboard.html) and /applications/<id>/fact-sheet (the
    # service, which brands itself "the single source of truth") disagreed
    # for the same record (20% vs 21%, and any other pair by coincidence).
    # Both now delegate to the fact sheet's calculation -- lazy import to
    # avoid a circular import (the service imports app.db at module load).
    @property
    def completeness_score(self):
        """Data completeness percentage (0-100) -- see
        app.services.application_fact_sheet.compute_completeness for the rubric."""
        from app.services.application_fact_sheet import compute_completeness
        return compute_completeness(self)["pct"]

    @property
    def completeness_gaps(self):
        """List of missing field labels -- see
        app.services.application_fact_sheet.compute_completeness for the rubric."""
        from app.services.application_fact_sheet import compute_completeness
        return compute_completeness(self)["missing"]

    # Relationships
    capability_mappings = relationship(
        "ApplicationCapabilityMapping",
        back_populates="application",
        cascade="all, delete-orphan",
    )
    technology_instances = relationship(
        "ApplicationTechnologyInstance",
        back_populates="application",
        cascade="all, delete-orphan",
    )
    vendor_contracts = relationship(
        "VendorContract", back_populates="application", cascade="all, delete-orphan"
    )
    requirements = relationship(
        "Requirement",
        secondary="application_requirement_mapping",
        back_populates="implementing_applications",
        overlaps="application_component,requirement_mappings,app_requirement_mappings,application_mappings,requirement",
    )
    technology_stacks = relationship(
        "TechnologyStack",
        secondary="application_technology_mapping",
        back_populates="applications",
    )
    supported_processes = relationship(
        "BusinessProcess",
        secondary="application_process_support",
        back_populates="supporting_applications",
    )

    # Business Actor relationships (via junction table)
    business_actors = relationship(
        "BusinessActor",
        secondary="application_business_actor_mapping",
        back_populates="applications",
    )

    # Application Interface relationships (via junction table)
    interfaces = relationship(
        "ApplicationInterface",
        secondary="application_interface_mapping",
        back_populates="components",
    )

    # Software Architecture relationships (via junction table)
    software_modules = relationship(
        "SoftwareModule",
        secondary="application_modules",
        back_populates="parent_application",
    )

    # Vendor Product relationships (Option B+)
    # Primary vendor product (direct FK relationship)
    primary_vendor_product = relationship(
        "VendorProduct",
        foreign_keys=[vendor_product_id],
        backref="primary_applications",
    )
    # All vendor products (M:M via junction table)
    vendor_products = relationship(
        "VendorProduct",
        secondary=application_component_vendor_products,
        backref="portfolio_applications",
    )

    # Compliance realization relationships (via junction table)
    compliance_realizations = relationship(
        "ComplianceRequirement",
        secondary=application_compliance_realization,
        backref="realizing_applications",
    )

    # Indexes for performance
    __table_args__ = (
        Index(
            "idx_application_portfolio",
            "criticality",
            "strategic_importance",
            "lifecycle_status",
        ),
        Index(
            "idx_application_technology",
            "application_category",
            "deployment_model",
            "vendor_name",
        ),
        Index("idx_application_financial", "total_cost_of_ownership", "roi_score"),
        Index(
            "idx_application_manufacturing",
            "manufacturing_critical",
            "shop_floor_system",
        ),
        # Performance indexes for list page filtering
        Index("idx_app_deployment_status", "deployment_status"),
        Index("idx_app_business_criticality", "business_criticality"),
        Index(
            "idx_app_list_filter",
            "deployment_status",
            "business_criticality",
            "business_domain",
        ),
        {"extend_existing": True},
    )

    def __repr__(self):
        return f"<ApplicationComponent {self.name}>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "application_code": self.application_code,
            "application_type": self.application_type,
            "application_category": self.application_category,
            "deployment_model": self.deployment_model,
            "criticality": self.criticality,
            "strategic_importance": self.strategic_importance,
            "business_value": self.business_value,
            "competitive_advantage": self.competitive_advantage,
            "differentiation_level": self.differentiation_level,
            "user_base_size": self.user_base_size,
            "total_cost_of_ownership": self.total_cost_of_ownership,
            "license_cost": self.license_cost,
            "maintenance_cost": self.maintenance_cost,
            "roi_score": self.roi_score,
            "vendor_name": self.vendor_name,
            "vendor_type": self.vendor_type,
            "vendor_product_id": self.vendor_product_id,
            "primary_vendor_product": {
                "id": self.primary_vendor_product.id,
                "name": self.primary_vendor_product.name,
                "vendor_name": self.primary_vendor_product.vendor_organization.name
                if self.primary_vendor_product.vendor_organization
                else None,
            }
            if self.primary_vendor_product
            else None,
            "contract_type": self.contract_type,
            "contract_expiry_date": self.contract_expiry_date.isoformat()
            if self.contract_expiry_date
            else None,
            "lifecycle_status": self.lifecycle_status,
            "implementation_date": self.implementation_date.isoformat()
            if self.implementation_date
            else None,
            "planned_retirement_date": self.planned_retirement_date.isoformat()
            if self.planned_retirement_date
            else None,
            "technology_age_years": self.technology_age_years,
            "availability_target": self.availability_target,
            "availability_actual": self.availability_actual,
            "performance_rating": self.performance_rating,
            "user_satisfaction_score": self.user_satisfaction_score,
            "integration_complexity": self.integration_complexity,
            "number_of_integrations": self.number_of_integrations,
            "architecture_style": self.architecture_style,
            "data_classification": self.data_classification,
            "security_level": self.security_level,
            "technical_risk": self.technical_risk,
            "business_risk": self.business_risk,
            "vendor_risk": self.vendor_risk,
            "obsolescence_risk": self.obsolescence_risk,
            "manufacturing_critical": self.manufacturing_critical,
            "shop_floor_system": self.shop_floor_system,
            "real_time_requirements": self.real_time_requirements,
            "application_owner": self.application_owner,
            "business_owner": self.business_owner,
            "technical_owner": self.technical_owner,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def calculate_tco_breakdown(self):
        """Calculate TCO breakdown percentage"""
        total = self.total_cost_of_ownership or 0
        if total == 0:
            return {}

        return {
            "license_percentage": (self.license_cost or 0) / total * 100,
            "maintenance_percentage": (self.maintenance_cost or 0) / total * 100,
            "infrastructure_percentage": (self.infrastructure_cost or 0) / total * 100,
            "support_percentage": (self.support_cost or 0) / total * 100,
        }

    def get_business_functions(self):
        """Parse JSON business functions"""
        if self.business_functions:
            try:
                import json

                return json.loads(self.business_functions)
            except (ValueError, KeyError, TypeError):
                return []
        return []

    def set_business_functions(self, functions_list):
        """Set business functions as JSON"""
        import json

        self.business_functions = json.dumps(functions_list)


# NOTE: ApplicationCapabilityMapping DUPLICATE REMOVED - use app.models.application_capability import instead
# The duplicate class definition has been commented out to fix SQLAlchemy mapper conflict
# Use: from app.models.application_capability import ApplicationCapabilityMapping

"""
# COMMENTED OUT - DUPLICATE CLASS DEFINITION
# Use app.models.application_capability.ApplicationCapabilityMapping instead

class ApplicationCapabilityMapping(db.Model):
    # Application-Capability Mapping
    # Maps applications to the business capabilities they support.
    # Foundation for gap analysis and portfolio optimization.

    __tablename__ = "application_capability_mapping"
    __table_args__ = {"extend_existing": True}
    # ... (full definition commented out)
"""


class ApplicationTechnologyInstance(db.Model):
    """
    Application Technology Instance

    Tracks specific technology instances used by applications.
    Provides detailed technology stack management.
    """

    __tablename__ = "application_technology_instances"

    id = Column(db.Integer, primary_key=True)

    # Link entities
    application_id = Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False
    )
    technology_id = Column(
        db.Integer, db.ForeignKey("technology_capabilities.id"), nullable=False
    )

    # Instance details
    instance_name = Column(db.String(256))
    instance_type = Column(
        db.String(50)
    )  # database, server, middleware, framework, library
    version = Column(db.String(50))
    edition = Column(db.String(50))  # community, standard, enterprise

    # Deployment information
    deployment_location = Column(db.String(50))  # on_premise, aws, azure, gcp, hybrid
    environment = Column(db.String(20))  # development, testing, staging, production
    region = Column(db.String(50))  # Geographic region

    # Infrastructure details
    host_name = Column(db.String(255))
    ip_address = Column(db.String(45))  # IPv4 or IPv6
    port = Column(db.Integer)
    protocol = Column(db.String(10))  # http, https, tcp, udp

    # Resource allocation
    cpu_cores = Column(db.Integer)
    memory_gb = Column(db.Float)
    storage_gb = Column(db.Float)
    network_bandwidth_mbps = Column(db.Integer)

    # Status and health
    status = Column(
        db.String(20), default="unknown"
    )  # running, stopped, error, maintenance
    health_status = Column(
        db.String(20), default="unknown"
    )  # healthy, unhealthy, warning
    last_health_check = Column(db.DateTime)

    # Configuration
    configuration = Column(db.Text)  # JSON object with configuration parameters
    environment_variables = Column(db.Text)  # JSON object

    # Monitoring
    monitoring_enabled = Column(db.Boolean, default=True)
    log_level = Column(db.String(10))  # debug, info, warn, error
    metrics_endpoint = Column(db.String(255))

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_deployed_at = Column(db.DateTime)

    # Relationships
    application = relationship(
        "ApplicationComponent", back_populates="technology_instances"
    )
    technology = relationship("TechnologyCapability", backref="application_instances")

    def __repr__(self):
        return f"<ApplicationTechnologyInstance {self.instance_name}>"


class VendorContract(TenantMixin, db.Model):
    """
    Vendor Contract Model

    Manages vendor contracts and agreements for applications.
    Supports contract lifecycle management and compliance.

    Tenant-scoped: inherits TenantMixin so the automatic org filter applies.
    The explicit organization_id column + organization relationship below
    override the mixin's declared_attr versions (same definition), so this is
    a behavioural change only — VendorContract SELECTs are now org-filtered.
    """

    __tablename__ = "vendor_contracts"
    __table_args__ = {"extend_existing": True}

    id = Column(db.Integer, primary_key=True)

    # Contract identity
    contract_number = Column(db.String(100), unique=True, index=True)
    contract_name = Column(db.String(256), nullable=False)
    contract_description = Column(db.Text)

    # Organization (tenant isolation)
    organization_id = Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Link entities
    application_id = Column(db.Integer, db.ForeignKey("application_components.id"))
    vendor_id = Column(db.Integer, db.ForeignKey("vendor_organizations.id"))

    # Contract details
    contract_type = Column(
        db.String(30)
    )  # license, subscription, maintenance, support, custom_development
    contract_category = Column(db.String(30))  # software, hardware, service, consulting
    pricing_model = Column(
        db.String(30)
    )  # perpetual, subscription, usage_based, custom

    # Financial terms
    contract_value = Column(db.Float)  # Total contract value
    annual_cost = Column(db.Float)  # Annual recurring cost
    currency = Column(db.String(10), default="USD")
    payment_terms = Column(db.String(50))  # net_30, net_60, quarterly, annually

    # Contract period
    start_date = Column(db.Date, nullable=False)
    end_date = Column(db.Date)
    renewal_date = Column(db.Date)
    auto_renewal = Column(db.Boolean, default=False)
    notice_period_days = Column(db.Integer, default=90)

    # License and usage terms
    license_type = Column(
        db.String(30)
    )  # named_user, concurrent_user, per_device, per_core, subscription
    license_quantity = Column(db.Integer)
    usage_restrictions = Column(db.Text)  # JSON array of usage restrictions

    # Support and maintenance
    support_level = Column(db.String(30))  # basic, standard, premium, custom
    support_hours = Column(db.String(50))  # 24x7, business_hours, custom
    sla_response_time = Column(db.String(50))  # Response time SLA
    maintenance_included = Column(db.Boolean, default=True)

    # Compliance and security
    compliance_requirements = Column(db.Text)  # JSON array of compliance requirements
    security_requirements = Column(db.Text)  # JSON array of security requirements
    data_location_requirements = Column(db.String(100))  # Data residency requirements

    # Contract status
    status = Column(
        db.String(20), default="active"
    )  # draft, active, expired, terminated, renewed
    renewal_status = Column(db.String(20))  # not_renewing, renewing, under_negotiation

    # Governance
    contract_owner = Column(db.String(100))
    legal_approver = Column(db.String(100))
    business_approver = Column(db.String(100))

    # Risk assessment
    vendor_risk = Column(db.String(20))  # low, medium, high, critical
    contract_risk = Column(db.String(20))  # low, medium, high, critical
    exit_complexity = Column(db.String(20))  # low, medium, high

    # Metadata
    contract_document = Column(db.String(255))  # Path to contract document
    notes = Column(db.Text)

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    organization = relationship("Organization", backref="vendor_contracts")
    application = relationship(
        "ApplicationComponent", back_populates="vendor_contracts"
    )
    vendor = relationship("VendorOrganization", backref="contracts")
    capability_allocations = relationship(
        "CapabilityCostAllocation",
        secondary="capability_vendor_costs",
        back_populates="vendor_contracts",
    )

    # SLAs under this contract (required by ServiceLevelAgreement.contract back_populates)
    slas = relationship(
        "ServiceLevelAgreement", back_populates="contract", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<VendorContract {self.contract_number}>"


class ApplicationPortfolio(db.Model, OptimisticLockMixin):
    """
    Application Portfolio Summary

    Provides portfolio-level analytics and reporting.
    Supports strategic portfolio management and optimization.
    """

    __tablename__ = "application_portfolio_summary"

    id = Column(db.Integer, primary_key=True)

    # Portfolio identity
    portfolio_name = Column(db.String(256), nullable=False, index=True)
    portfolio_description = Column(db.Text)
    portfolio_type = Column(db.String(50))  # enterprise, department, domain, technology

    # Portfolio metrics
    total_applications = Column(db.Integer, default=0)
    mission_critical_applications = Column(db.Integer, default=0)
    strategic_applications = Column(db.Integer, default=0)
    legacy_applications = Column(db.Integer, default=0)
    cloud_applications = Column(db.Integer, default=0)
    on_premise_applications = Column(db.Integer, default=0)

    # Financial metrics
    total_tco = Column(db.Float, default=0)  # Total cost of ownership
    annual_license_cost = Column(db.Float, default=0)
    annual_maintenance_cost = Column(db.Float, default=0)
    annual_infrastructure_cost = Column(db.Float, default=0)
    average_application_age = Column(db.Float)  # Average age in years

    # Quality metrics
    average_availability = Column(db.Float)  # Average availability percentage
    average_user_satisfaction = Column(db.Float)  # Average satisfaction score
    high_risk_applications = Column(db.Integer, default=0)
    applications_with_security_issues = Column(db.Integer, default=0)

    # Capability coverage
    capabilities_covered = Column(db.Integer, default=0)
    capability_coverage_percentage = Column(
        db.Float, default=0
    )  # Percentage of capabilities covered
    critical_capabilities_covered = Column(db.Integer, default=0)
    critical_capability_coverage_percentage = Column(db.Float, default=0)

    # Integration metrics
    total_integrations = Column(db.Integer, default=0)
    average_integrations_per_application = Column(db.Float, default=0)
    highly_integrated_applications = Column(
        db.Integer, default=0
    )  # Apps with >10 integrations

    # Vendor diversity
    unique_vendors = Column(db.Integer, default=0)
    vendor_concentration_score = Column(
        db.Float
    )  # 0 - 100 (higher = more concentrated)
    contracts_expiring_12_months = Column(db.Integer, default=0)

    # Manufacturing specific
    manufacturing_critical_applications = Column(db.Integer, default=0)
    shop_floor_systems = Column(db.Integer, default=0)
    real_time_systems = Column(db.Integer, default=0)

    # Assessment metadata
    last_assessed = Column(db.DateTime, default=datetime.utcnow)
    assessment_period_start = Column(db.Date)
    assessment_period_end = Column(db.Date)

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ApplicationPortfolio {self.portfolio_name}>"


# Event listeners for ApplicationComponent validation
@event.listens_for(ApplicationComponent, "before_insert")
@event.listens_for(ApplicationComponent, "before_update")
def validate_application_name(mapper, connection, target):
    """Validate ApplicationComponent name before insert or update."""
    if not target.name or (isinstance(target.name, str) and target.name.strip() == ""):
        raise ValueError("Application name cannot be null or empty")


@event.listens_for(ApplicationComponent, "before_insert")
def create_app_component_archimate_element(mapper, connection, target):
    """Automatically create ArchiMateElement when ApplicationComponent is created."""
    if target.archimate_element_id is None:
        from sqlalchemy import insert, select

        from .archimate_core import ArchiMateElement
        from .organization import Organization

        # archimate_elements.organization_id is NOT NULL, and this raw insert is
        # invisible to the tenant middleware, so the org must be set explicitly.
        # The element belongs to the same tenant as its application (set by the
        # tenant before_flush); fall back to the default org if it is unset.
        org_id = getattr(target, "organization_id", None)
        if org_id is None:
            orgs = Organization.__table__
            row = connection.execute(
                select(orgs.c.id).where(orgs.c.slug == "default").limit(1)
            ).first()
            org_id = row[0] if row is not None else connection.execute(
                orgs.insert().values(name="Default Organization", slug="default")
            ).inserted_primary_key[0]

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                name=target.name,
                type="ApplicationComponent",
                layer="Application",
                description=target.description or f"Application: {target.name}",
                organization_id=org_id,
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]
