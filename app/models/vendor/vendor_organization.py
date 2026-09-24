# migration-exempt
"""
Vendor Organization Models

Strategic vendor management for enterprise transformation programs.
Supports multi-product vendor analysis across business capabilities.

Key Features:
- Vendor organization master data (SAP, Oracle, Microsoft, Workday)
- Product portfolio management (S/4HANA, Fusion, Dynamics 365)
- Capability coverage scoring (0 - 100% maturity per product-capability)
- Suite-level analysis (compare full vendor stacks)
- Gartner positioning and market intelligence
- Semantic EA Intelligence: Full traceability across architecture layers
"""

from __future__ import annotations  # dead-code-ok

import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Dict, List

from app.models.business_capabilities import BusinessCapability

from .. import db
from ..mixins import TenantMixin

# ============================================================================
# SEMANTIC EA INTELLIGENCE - JUNCTION TABLES
# ============================================================================

# 1. VendorProduct ↔ TechnologyStack (Many-to-Many)
vendor_product_tech_stacks = db.Table(
    "vendor_product_tech_stacks",
    db.Column(
        "vendor_product_id",
        db.Integer,
        db.ForeignKey("vendor_products.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "technology_stack_id",
        db.Integer,
        db.ForeignKey("technology_stacks.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "usage_type", db.String(50)
    ),  # 'primary', 'integration', 'development', 'testing'
    db.Column(
        "deployment_status", db.String(30)
    ),  # 'production', 'staging', 'planned', 'deprecated'
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    extend_existing=True,
)

# 2. ApplicationComponent ↔ VendorProduct (Many-to-Many)
application_vendor_products = db.Table(
    "application_vendor_products",
    db.Column(
        "archimate_element_id",
        db.Integer,
        db.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "vendor_product_id",
        db.Integer,
        db.ForeignKey("vendor_products.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "deployment_type", db.String(50)
    ),  # 'primary_system', 'integration_layer', 'data_source', 'reporting'
    db.Column(
        "criticality", db.String(20)
    ),  # 'mission_critical', 'business_critical', 'important', 'supporting'
    db.Column(
        "hosting_model", db.String(30)
    ),  # 'cloud', 'on_premise', 'hybrid', 'saas'
    db.Column("implementation_date", db.DateTime),
    db.Column("retirement_date", db.DateTime),
    db.Column("notes", db.Text),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    extend_existing=True,
)

# 3. BusinessFunction ↔ ArchiMateElement (ApplicationComponent) (Many-to-Many)
business_function_applications = db.Table(
    "business_function_applications",
    db.Column(
        "business_function_id",
        db.Integer,
        db.ForeignKey("business_function.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "archimate_element_id",
        db.Integer,
        db.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "usage_type", db.String(50)
    ),  # 'primary', 'supporting', 'fallback', 'reporting'
    db.Column("automation_level", db.Integer),  # 0 - 100% automated by this application
    db.Column("user_count", db.Integer),  # Estimated users for this function
    db.Column("transaction_volume", db.Integer),  # Daily/monthly transactions
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    extend_existing=True,
)

# 4. ComplianceRequirement ↔ VendorProductCapability (Many-to-Many)
compliance_vendor_coverage = db.Table(
    "compliance_vendor_coverage",
    db.Column(
        "compliance_requirement_id",
        db.Integer,
        db.ForeignKey("compliance_requirements.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "vendor_product_capability_id",
        db.Integer,
        db.ForeignKey("vendor_product_capabilities.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "compliance_status", db.String(30)
    ),  # 'compliant', 'partial', 'non_compliant', 'not_applicable'
    db.Column("compliance_percentage", db.Integer),  # 0 - 100% compliance coverage
    db.Column("evidence_url", db.String(500)),
    db.Column("audit_date", db.DateTime),
    db.Column("auditor_notes", db.Text),
    db.Column("remediation_required", db.Boolean, default=False),
    db.Column("remediation_plan", db.Text),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    extend_existing=True,
)

# 5. EnterpriseInitiative ↔ BusinessCapability (Many-to-Many)
# NOTE: This table is also defined in relationship_tables.py with additional fields
initiative_capabilities = db.Table(
    "initiative_capabilities",
    db.Column(
        "initiative_id",
        db.Integer,
        db.ForeignKey("enterprise_initiatives.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "capability_id",
        db.Integer,
        db.ForeignKey("business_capability.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "transformation_type", db.String(50)
    ),  # 'enhance', 'replace', 'consolidate', 'retire'
    db.Column("target_maturity", db.Integer),  # Target CMM level
    db.Column("priority", db.String(20)),  # 'critical', 'high', 'medium', 'low'
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    extend_existing=True,
)

# 6. EnterpriseInitiative ↔ VendorOrganization (Many-to-Many)
initiative_vendors = db.Table(
    "initiative_vendors",
    db.Column(
        "initiative_id",
        db.Integer,
        db.ForeignKey("enterprise_initiatives.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "vendor_organization_id",
        db.Integer,
        db.ForeignKey("vendor_organizations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "evaluation_status", db.String(30)
    ),  # 'shortlisted', 'evaluating', 'selected', 'rejected'
    db.Column("evaluation_score", db.Integer),  # 0 - 100 overall score
    db.Column(
        "recommendation", db.String(20)
    ),  # 'recommended', 'acceptable', 'not_recommended'
    db.Column("notes", db.Text),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    extend_existing=True,
)

# 7. EnterpriseInitiative ↔ TechnologyStack (Many-to-Many)
initiative_tech_stacks = db.Table(
    "initiative_tech_stacks",
    db.Column(
        "initiative_id",
        db.Integer,
        db.ForeignKey("enterprise_initiatives.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "technology_stack_id",
        db.Integer,
        db.ForeignKey("technology_stacks.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "usage_type", db.String(50)
    ),  # 'target', 'current', 'migration', 'decommission'
    db.Column("implementation_priority", db.String(20)),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    extend_existing=True,
)

# 8. VendorOrganization ↔ BusinessCapability Risk Analysis (Many-to-Many)
# NOTE: This table is now defined in relationship_tables.py with enhanced fields
# Keeping this comment for reference - use: from .relationship_tables import vendor_capability_risks
# vendor_capability_risks = db.Table(
#     "vendor_capability_risks",
#     db.Column(
#         "vendor_organization_id",
#         db.Integer,
#         db.ForeignKey("vendor_organizations.id", ondelete="CASCADE"),
#         primary_key=True,
#     ),
#     db.Column(
#         "capability_id",
#         db.Integer,
#         db.ForeignKey("business_capability.id", ondelete="CASCADE"),
#         primary_key=True,
#     ),
#     db.Column("risk_level", db.String(20)),
#     db.Column("risk_type", db.String(50)),
#     db.Column("impact_description", db.Text),
#     db.Column("mitigation_strategy", db.Text),
#     db.Column("contingency_plan", db.Text),
#     db.Column("created_at", db.DateTime, default=datetime.utcnow),
# )
from .relationship_tables import vendor_capability_risks  # dead-code-ok


class VendorOrganization(db.Model):
    """
    Master vendor/organization entity for strategic vendor management.

    Represents major enterprise vendors (SAP, Oracle, Microsoft, Workday, etc.)
    with their complete product portfolios and strategic positioning.

    DELIBERATELY NOT TenantMixin — this is shared reference data, and that is a
    decision, not an oversight (ADR-0003). Everything here is a fact about the
    vendor in the world: Gartner quadrant position, market share, revenue, stock
    symbol. It is identical for every customer, and ``name`` is globally unique,
    which only makes sense for one shared catalogue. Giving it a tenant column
    would duplicate all 45 rows per organisation and break that constraint.

    Tenant-specific vendor data belongs elsewhere and is already scoped there:
    commercial terms in ``vendor_contracts``, application links in
    ``contract_applications``, and per-customer coverage assessments in
    ``VendorProductCapability`` below. Sidebar and dashboard vendor counts are
    therefore global on purpose — see app/_bootstrap/context_processors.py.
    Pinned by tests/test_vendor_tenancy_policy.py so this cannot be "fixed" by
    accident.
    """

    __tablename__ = "vendor_organizations"

    id = db.Column(db.Integer, primary_key=True)

    # Organization identity
    name = db.Column(db.String(200), nullable=False, unique=True, index=True)
    display_name = db.Column(db.String(200))  # e.g., "SAP SE", "Oracle Corporation"
    vendor_type = db.Column(
        db.String(50)
    )  # software_vendor, cloud_provider, systems_integrator
    headquarters_location = db.Column(db.String(100))
    website = db.Column(db.String(500))

    # Market intelligence
    gartner_magic_quadrant_position = db.Column(
        db.String(50)
    )  # leader, challenger, visionary, niche
    gartner_position_x = db.Column(db.Float)  # Completeness of Vision (-5 to +5)
    gartner_position_y = db.Column(db.Float)  # Ability to Execute (-5 to +5)
    forrester_wave_position = db.Column(
        db.String(50)
    )  # leader, strong_performer, contender, challenger
    market_share_percentage = db.Column(
        db.Float
    )  # Global market share in primary domain

    # Vendor metadata
    year_founded = db.Column(db.Integer)
    employee_count = db.Column(db.Integer)
    annual_revenue_usd = db.Column(db.Numeric(15, 2))  # In millions
    customer_count = db.Column(db.Integer)
    public_company = db.Column(db.Boolean, default=True)
    stock_symbol = db.Column(db.String(10))

    # Strategic assessment
    strategic_tier = db.Column(
        db.String(30)
    )  # tier_1_strategic, tier_2_preferred, tier_3_approved, tier_4_restricted
    enterprise_readiness_score = db.Column(
        db.Integer
    )  # 0 - 100 (support, stability, roadmap)
    innovation_score = db.Column(db.Integer)  # 0 - 100 (R&D, emerging tech, patents)
    partnership_level = db.Column(
        db.String(50)
    )  # strategic_partner, preferred, approved, none

    # Certifications and compliance
    iso_certifications = db.Column(db.Text)  # JSON array: ["ISO 27001", "ISO 9001"]
    compliance_frameworks = db.Column(db.Text)  # JSON array: ["SOC 2", "GDPR", "HIPAA"]
    industry_certifications = db.Column(db.Text)  # JSON array: industry-specific certs

    # Support and services
    support_tiers = db.Column(db.Text)  # JSON: support level descriptions
    professional_services = db.Column(db.Boolean, default=False)
    training_programs = db.Column(db.Boolean, default=False)
    partner_ecosystem_size = db.Column(db.Integer)  # Number of certified partners

    # Risk and stability
    financial_health_score = db.Column(db.Integer)  # 0 - 100
    acquisition_risk = db.Column(db.String(20))  # low, medium, high
    technology_maturity = db.Column(
        db.String(30)
    )  # emerging, established, mature, legacy
    vendor_lock_in_risk = db.Column(db.Integer)  # 1 - 10 scale

    # Notes and documentation
    description = db.Column(db.Text)
    strengths = db.Column(db.Text)  # JSON array
    weaknesses = db.Column(db.Text)  # JSON array
    strategic_notes = db.Column(db.Text)

    # Status
    status = db.Column(
        db.String(30), default="active"
    )  # active, restricted, deprecated
    evaluation_status = db.Column(db.String(30))  # under_evaluation, approved, rejected
    contract_status = db.Column(
        db.String(30), default="catalog"
    )  # catalog, contracted, deployed, inactive
    contract_start_date = db.Column(db.DateTime)
    contract_end_date = db.Column(db.DateTime)
    contract_value_annual = db.Column(db.Numeric(15, 2))  # Annual contract value in USD
    last_reviewed_at = db.Column(db.DateTime)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Seed data tracking (for UnifiedVendorSeeder)
    # NOTE: Migration backfills existing vendors with generated code/seed_source_id
    # After migration, these columns are required (NOT NULL) and unique
    code = db.Column(
        db.String(50),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: "VEND-" + uuid.uuid4().hex[:10].upper(),
    )  # Stable vendor identifier (VEND-SAP, VEND-SF, etc.)
    seed_source_id = db.Column(
        db.String(100),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: "auto-" + uuid.uuid4().hex,
    )  # ID in seed file (upsert key)
    seed_version = db.Column(
        db.String(50), index=True
    )  # Version of seed that loaded this (v1.0, v2.0, etc.)
    is_seed_data = db.Column(
        db.Boolean, default=False, nullable=False
    )  # True = from seed file, False = manually created
    seeded_at = db.Column(db.DateTime)  # When was this loaded from seed?
    seeded_by = db.Column(
        db.String(100), default="manual", nullable=False
    )  # Who/what loaded it (UnifiedVendorSeeder, manual, etc.)
    seed_checksum = db.Column(
        db.String(64)
    )  # SHA256 of seed data (detects manual edits)
    last_manual_edit_at = db.Column(db.DateTime)  # When was this last manually edited?
    last_manual_edit_by = db.Column(db.String(100))  # Who last manually edited?

    # Relationships
    created_by = db.relationship("User", backref="created_vendor_orgs")
    products = db.relationship(
        "VendorProduct",
        back_populates="vendor_organization",
        cascade="all, delete-orphan",
        lazy="select",
    )
    technology_stacks = db.relationship(
        "TechnologyStack",
        backref="vendor_org",
        foreign_keys="TechnologyStack.vendor_organization_id",
        overlaps="vendor_organization",
    )

    # Semantic EA Intelligence Relationships
    evaluated_in_initiatives = db.relationship(
        "EnterpriseInitiative",
        secondary=initiative_vendors,
        back_populates="evaluated_vendors",
    )
    # NOTE: Commented out due to SQLAlchemy mapper initialization issues
    # The vendor_capability_risks table exists but causes circular import issues
    # Use direct queries via relationship_tables.vendor_capability_risks instead
    # capability_risks = db.relationship(
    #     "BusinessCapability", secondary="vendor_capability_risks", back_populates="vendor_risks"
    # )

    def get_iso_certifications(self):
        """Parse ISO certifications JSON."""
        if self.iso_certifications:
            return json.loads(self.iso_certifications)
        return []

    def set_iso_certifications(self, certs_list):
        """Set ISO certifications from list."""
        self.iso_certifications = json.dumps(certs_list)

    def get_compliance_frameworks(self):
        """Parse compliance frameworks JSON."""
        if self.compliance_frameworks:
            return json.loads(self.compliance_frameworks)
        return []

    def set_compliance_frameworks(self, frameworks_list):
        """Set compliance frameworks from list."""
        self.compliance_frameworks = json.dumps(frameworks_list)

    def get_strengths(self):
        """Parse strengths JSON."""
        if self.strengths:
            try:
                return json.loads(self.strengths)
            except (json.JSONDecodeError, TypeError):
                return [self.strengths] if isinstance(self.strengths, str) else []
        return []

    def get_weaknesses(self):
        """Parse weaknesses JSON."""
        if self.weaknesses:
            try:
                return json.loads(self.weaknesses)
            except (json.JSONDecodeError, TypeError):
                return [self.weaknesses] if isinstance(self.weaknesses, str) else []
        return []

    def get_product_count(self):
        """Get total number of products."""
        return self.products.count()

    def get_capability_coverage(self):
        """
        Calculate overall capability coverage across all products.
        Returns dict with coverage stats.
        """
        # Get all unique capabilities covered by vendor's products
        covered_capabilities = set()
        total_coverage = 0
        product_count = 0

        for product in self.products:
            product_caps = product.capability_mappings.all()
            product_count += 1
            for mapping in product_caps:
                covered_capabilities.add(mapping.business_capability_id)
                total_coverage += mapping.coverage_percentage or 0

        avg_coverage = (
            total_coverage / len(list(self.products)) if product_count > 0 else 0
        )

        return {
            "unique_capabilities": len(covered_capabilities),
            "total_products": product_count,
            "average_coverage": round(avg_coverage, 1),
        }

    def to_dict(self):
        """Convert VendorOrganization to dictionary for API serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "vendor_type": self.vendor_type,
            "headquarters_location": self.headquarters_location,
            "website": self.website,
            "gartner_magic_quadrant_position": self.gartner_magic_quadrant_position,
            "strategic_tier": self.strategic_tier,
            "enterprise_readiness_score": self.enterprise_readiness_score,
            "partnership_level": self.partnership_level,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<VendorOrganization {self.name}>"


class VendorProduct(db.Model):
    """
    Individual vendor product/solution.

    Represents specific products within a vendor's portfolio:
    - SAP: S/4HANA, SuccessFactors, Ariba, SAC
    - Oracle: Fusion Cloud, NetSuite, EPM
    - Microsoft: Dynamics 365, Azure, Power Platform
    """

    __tablename__ = "vendor_products"

    id = db.Column(db.Integer, primary_key=True)

    # Product identity
    vendor_organization_id = db.Column(
        db.Integer, db.ForeignKey("vendor_organizations.id"), nullable=False, index=True
    )
    name = db.Column(db.String(200), nullable=False, index=True)
    product_code = db.Column(db.String(50))  # SKU or product code
    version = db.Column(db.String(50))
    release_date = db.Column(db.DateTime)

    # Product classification
    product_family_name = db.Column(
        "product_family", db.String(100)
    )  # ERP, HCM, CRM, Analytics, etc.
    deployment_model = db.Column(db.String(50))  # cloud, on_premise, hybrid
    licensing_model = db.Column(db.String(50))  # subscription, perpetual, usage_based
    product_type = db.Column(db.String(50))  # suite, platform, application, service
    target_market = db.Column(db.String(50))  # enterprise, mid_market, smb

    # Technical details
    primary_technology = db.Column(db.String(100))  # Java, .NET, Python, etc.
    supported_platforms = db.Column(db.Text)  # JSON array of platforms
    api_availability = db.Column(db.Boolean, default=True)
    integration_methods = db.Column(db.Text)  # JSON: ["REST API", "SOAP", "EDI"]

    # Functionality scope
    functional_scope = db.Column(db.Text)  # High-level description of what it does
    key_features = db.Column(db.Text)  # JSON array of key features
    differentiators = db.Column(db.Text)  # JSON array of unique selling points

    # Cost structure
    base_license_cost_annual = db.Column(db.Numeric(12, 2))  # Per user/instance
    implementation_cost_estimate = db.Column(db.Numeric(12, 2))
    support_cost_percentage = db.Column(db.Float)  # % of license cost
    typical_user_count_min = db.Column(db.Integer)
    typical_user_count_max = db.Column(db.Integer)

    # Market positioning
    market_position = db.Column(db.String(50))  # leader, challenger, niche
    customer_base_size = db.Column(db.Integer)
    industry_focus = db.Column(db.Text)  # JSON array: ["Manufacturing", "Finance"]

    # Maturity and adoption
    product_maturity = db.Column(db.String(30))  # emerging, growth, mature, declining
    adoption_rate = db.Column(db.String(30))  # early_adopter, mainstream, late_majority
    replacement_for = db.Column(db.String(200))  # What legacy product it replaces
    end_of_life_date = db.Column(db.DateTime)

    # Ratings (1 - 10 scale)
    scalability_rating = db.Column(db.Integer)
    security_rating = db.Column(db.Integer)
    usability_rating = db.Column(db.Integer)
    performance_rating = db.Column(db.Integer)
    reliability_rating = db.Column(db.Integer)
    innovation_rating = db.Column(db.Integer)

    # Requirements
    minimum_users = db.Column(db.Integer)
    maximum_users = db.Column(db.Integer)
    infrastructure_requirements = db.Column(db.Text)  # JSON object
    prerequisite_products = db.Column(db.Text)  # JSON array of other products needed

    # Technical Stack (for Application Onboarding)
    programming_languages = db.Column(db.Text)  # e.g., "Java, Python, JavaScript"
    frameworks = db.Column(db.Text)  # e.g., "Spring Boot, React, Angular"
    primary_database = db.Column(
        db.String(200)
    )  # e.g., "PostgreSQL", "Oracle", "MongoDB"

    # Infrastructure & Deployment
    cache_technology = db.Column(db.String(200))  # e.g., "Redis", "Memcached"
    message_queue = db.Column(db.String(200))  # e.g., "RabbitMQ", "Kafka"
    deployment_region = db.Column(db.String(200))  # e.g., "Multi-region (US, EU, APAC)"
    load_balancer = db.Column(db.String(200))  # e.g., "AWS ELB", "NGINX"
    cdn_provider = db.Column(db.String(200))  # e.g., "CloudFront", "Akamai"

    # Security & Authentication
    authentication_method = db.Column(db.Text)  # e.g., "SSO, SAML 2.0, OAuth 2.0, MFA"
    encryption_at_rest = db.Column(db.Boolean)
    encryption_in_transit = db.Column(db.Boolean)

    # Compliance
    compliance_tags = db.Column(db.Text)  # e.g., "SOC2, ISO27001, HIPAA"
    gdpr_compliant = db.Column(db.Boolean)
    pii_data_processed = db.Column(db.Boolean)

    # Operational Tools
    monitoring_tool = db.Column(db.String(200))  # e.g., "Datadog", "New Relic"
    logging_tool = db.Column(db.String(200))  # e.g., "Splunk", "ELK Stack"
    backup_solution = db.Column(db.String(200))  # e.g., "Veeam", "AWS Backup"

    # Storage & Data
    storage_backend = db.Column(db.String(200))  # e.g., "S3", "Azure Blob"
    search_engine = db.Column(db.String(200))  # e.g., "Elasticsearch", "Solr"

    # Documentation & Support
    documentation_url = db.Column(db.String(500))
    api_documentation_url = db.Column(db.String(500))
    support_portal = db.Column(db.String(500))
    training_resources = db.Column(db.String(500))
    community_forum = db.Column(db.String(500))
    github_repo = db.Column(db.String(500))

    # Analytics & Monitoring
    analytics_platform = db.Column(
        db.String(200)
    )  # e.g., "Google Analytics", "Mixpanel"
    monitoring_integration = db.Column(db.String(200))
    logging_framework = db.Column(db.String(200))

    # DNS & Email
    dns_provider = db.Column(db.String(200))
    email_service = db.Column(db.String(200))

    # Status
    status = db.Column(
        db.String(30), default="active"
    )  # active, deprecated, discontinued
    availability_regions = db.Column(db.Text)  # JSON array of geographic regions

    # Notes
    description = db.Column(db.Text)
    implementation_notes = db.Column(db.Text)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Semantic EA Intelligence - Link to ArchiMate
    archimate_product_element_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "archimate_elements.id",
            use_alter=True,
            name="fk_vendor_products_archimate_element_id",
        ),
    )
    # This makes vendor products part of ArchiMate architecture models

    # Relationships
    vendor_organization = db.relationship(
        "VendorOrganization", back_populates="products"
    )
    capability_mappings = db.relationship(
        "VendorProductCapability",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="select",
    )

    # Semantic EA Intelligence Relationships
    technology_stacks = db.relationship(
        "TechnologyStack",
        secondary=vendor_product_tech_stacks,
        back_populates="vendor_products",
    )
    application_components = db.relationship(
        "ArchiMateElement",
        secondary=application_vendor_products,
        back_populates="vendor_products",
    )
    archimate_product_element = db.relationship(
        "ArchiMateElement", foreign_keys=[archimate_product_element_id]
    )

    def get_key_features(self):
        """Parse key features JSON."""
        if self.key_features:
            return json.loads(self.key_features)
        return []

    def get_differentiators(self):
        """Parse differentiators JSON."""
        if self.differentiators:
            return json.loads(self.differentiators)
        return []

    def get_supported_capabilities(self):
        """Get list of supported business capabilities with coverage."""
        return [
            {
                "capability_id": mapping.business_capability_id,
                "capability_name": mapping.business_capability.name,
                "coverage_percentage": mapping.coverage_percentage,
                "maturity_level": mapping.maturity_level,
                "implementation_complexity": mapping.implementation_complexity,
            }
            for mapping in self.capability_mappings.all()
        ]

    def calculate_average_coverage(self):
        """Calculate average capability coverage percentage."""
        mappings = self.capability_mappings.all()
        if not mappings:
            return 0.0

        total = sum(m.coverage_percentage for m in mappings if m.coverage_percentage)
        return round(total / len(mappings), 1)

    def __repr__(self):
        return f"<VendorProduct {self.vendor_organization.name if self.vendor_organization else 'Unknown'}: {self.name}>"


# ADR-0003: tenant-scoped. Unlike VendorOrganization above this is NOT reference
# data — business_capability_id points at a tenant-owned capability, so a row
# states how well a vendor covers ONE customer's capability model.
#
# The table already had an organization_id column, declared here as a bare
# nullable Integer with no ForeignKey. Nothing populated it (all 192 production
# rows NULL) and nothing filtered on it, and its lack of an FK is what made
# TenantMixin's `organization` relationship unjoinable — the first attempt to add
# the mixin broke mapper initialisation app-wide for exactly that reason. The
# bare column is gone; the mixin now supplies it with the FK, index and NOT NULL.
# `flask backfill-layer-tenancy` fills it by reading the tenant off the linked
# capability rather than guessing.
class VendorProductCapability(TenantMixin, db.Model):
    """
    Junction table linking vendor products to business capabilities with coverage metrics.

    Captures HOW WELL a vendor product supports a specific business capability:
    - Coverage percentage (0 - 100%): How much of the capability is supported
    - Maturity level: How mature/production-ready the support is
    - Gap analysis: What's missing
    - Implementation effort: How hard to implement
    """

    __tablename__ = "vendor_product_capabilities"

    id = db.Column(db.Integer, primary_key=True)

    # Core relationship
    vendor_product_id = db.Column(
        db.Integer, db.ForeignKey("vendor_products.id"), nullable=False, index=True
    )
    business_capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id"), nullable=False, index=True
    )

    # Coverage metrics
    coverage_percentage = db.Column(
        db.Float, nullable=False
    )  # 0 - 100: How much is covered
    maturity_level = db.Column(db.Integer)  # 1 - 5 CMM levels
    fit_score = db.Column(db.Integer)  # 0 - 100: Overall fit quality

    # Gap analysis
    gaps = db.Column(db.Text)  # JSON array of missing functionality
    workarounds = db.Column(db.Text)  # JSON array of workaround strategies
    customization_required = db.Column(db.Boolean, default=False)
    customization_effort = db.Column(db.String(30))  # low, medium, high, very_high

    # Implementation metrics
    implementation_complexity = db.Column(db.Integer)  # 1 - 10 scale
    estimated_implementation_weeks = db.Column(db.Integer)
    configuration_required = db.Column(db.Boolean, default=True)
    integration_complexity = db.Column(db.String(30))  # low, medium, high

    # Feature support detail
    out_of_box_percentage = db.Column(
        db.Float
    )  # 0 - 100: Available without customization
    requires_add_ons = db.Column(db.Boolean, default=False)
    required_add_ons = db.Column(db.Text)  # JSON array of additional products needed

    # Cost impact
    additional_licensing_cost = db.Column(
        db.Numeric(12, 2)
    )  # Extra cost for this capability

    # --- Vendor Pricing Pipeline (Wave 1) ---
    data_source_type = db.Column(db.String(30), nullable=False, default='seeded',
                                  server_default='seeded')
    confirmed_by_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    # organization_id comes from TenantMixin: it previously stood here as a bare
    # nullable Integer with NO ForeignKey, which is why the column existed in the
    # database, was never populated (all 192 production rows NULL), was never
    # filtered on, and made TenantMixin's `organization` relationship unjoinable.

    # Quality metrics
    performance_rating = db.Column(db.Integer)  # 1 - 10: How well does it perform
    usability_rating = db.Column(db.Integer)  # 1 - 10: How easy to use
    reliability_rating = db.Column(db.Integer)  # 1 - 10: How stable

    # Evidence and validation
    reference_customers = db.Column(db.Text)  # JSON array of customer names
    case_studies = db.Column(db.Text)  # JSON array of URLs to case studies
    vendor_documentation_url = db.Column(db.String(500))
    last_validated_at = db.Column(db.DateTime)
    validated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Notes
    notes = db.Column(db.Text)
    strengths = db.Column(db.Text)  # JSON array
    weaknesses = db.Column(db.Text)  # JSON array

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    product = db.relationship("VendorProduct", back_populates="capability_mappings")
    business_capability = db.relationship(
        "BusinessCapability", backref="vendor_product_mappings"
    )
    validated_by = db.relationship("User", backref="validated_capability_mappings")

    # Semantic EA Intelligence Relationships
    compliance_requirements = db.relationship(
        "ComplianceRequirement",
        secondary=compliance_vendor_coverage,
        back_populates="vendor_capability_coverages",
    )

    # Unique constraint: One mapping per product-capability pair
    __table_args__ = (
        db.UniqueConstraint(
            "vendor_product_id",
            "business_capability_id",
            name="uq_vendor_product_capability",
        ),
    )

    def get_gaps(self):
        """Parse gaps JSON."""
        if self.gaps:
            return json.loads(self.gaps)
        return []

    def get_workarounds(self):
        """Parse workarounds JSON."""
        if self.workarounds:
            return json.loads(self.workarounds)
        return []

    def get_required_add_ons(self):
        """Parse required add-ons JSON."""
        if self.required_add_ons:
            return json.loads(self.required_add_ons)
        return []

    def get_strengths(self):
        """Parse strengths JSON."""
        if self.strengths:
            try:
                return json.loads(self.strengths)
            except (json.JSONDecodeError, TypeError):
                return [self.strengths] if isinstance(self.strengths, str) else []
        return []

    def get_weaknesses(self):
        """Parse weaknesses JSON."""
        if self.weaknesses:
            try:
                return json.loads(self.weaknesses)
            except (json.JSONDecodeError, TypeError):
                return [self.weaknesses] if isinstance(self.weaknesses, str) else []
        return []

    def calculate_overall_fit(self):
        """Calculate overall fit score based on all metrics."""
        scores = []

        # Coverage (most important - 40% weight)
        if self.coverage_percentage is not None:
            scores.append(self.coverage_percentage * 0.4)

        # Maturity (20% weight)
        if self.maturity_level:
            scores.append((self.maturity_level / 5) * 100 * 0.2)

        # Implementation (20% weight) - inverse of complexity
        if self.implementation_complexity:
            impl_score = (10 - self.implementation_complexity) / 10 * 100
            scores.append(impl_score * 0.2)

        # Quality ratings (20% weight)
        quality_scores = []
        if self.performance_rating:
            quality_scores.append(self.performance_rating * 10)
        if self.usability_rating:
            quality_scores.append(self.usability_rating * 10)
        if self.reliability_rating:
            quality_scores.append(self.reliability_rating * 10)

        if quality_scores:
            avg_quality = sum(quality_scores) / len(quality_scores)
            scores.append(avg_quality * 0.2)

        if not scores:
            return 0

        return round(sum(scores), 1)

    def __repr__(self):
        return f"<VendorProductCapability {self.product.name if self.product else 'Unknown'} → {self.business_capability.name if self.business_capability else 'Unknown'}: {self.coverage_percentage}%>"


# ============================================================================
# ENTERPRISE INITIATIVE MODEL - Strategic Transformation Programs
# ============================================================================


class EnterpriseInitiative(TenantMixin, db.Model):
    """
    Strategic transformation programs and initiatives.

    Represents major enterprise programs like:
    - SAP S/4HANA Transformation
    - Cloud Migration Initiative
    - Application Consolidation
    - Digital Transformation
    - Technology Modernization

    Links capabilities, vendors, and technology stacks to strategic programs
    for complete transformation traceability and ROI analysis.

    ADR-0008 note (2 Sep 2026): this is a SECOND "programme" aggregate root,
    independent of StrategicInitiative (app/models/strategic.py) — the one
    with real programme governance (ProgrammeWorkstream, ProgrammeRoleAssignment,
    ProgrammeOutcomeCommitment) and the one WorkPackage/Benefit/Solution treat
    as canonical. This model is where the Portfolio module's demand-intake →
    benefit → assumption workflow (app/modules/portfolio/) actually lives, and
    it is real, used data — not dead weight to delete on sight. It has not been
    merged into StrategicInitiative because that requires matching real rows
    (by name/date/owner) to know which pairs describe the same programme,
    which is a judgement call outside what a schema change can decide safely.
    `linked_strategic_initiative_id` below lets a specific pair be tied
    together once someone with that context confirms the match — link, don't
    silently duplicate further. See WorkPackage.enterprise_initiative_id /
    .strategic_initiative_id, which already sit side by side with nothing
    requiring they agree.
    """

    __tablename__ = "enterprise_initiatives"

    # This model held every tenant's transformation programmes — budgets, spend,
    # sponsors — with no organization_id at all, so it was not merely unfiltered
    # but structurally impossible to filter (tenant_isolation attaches
    # `organization_id == g.current_org_id` to SELECTs on TenantMixin models).
    # Nothing surfaced it in the UI, which is the only reason it had not leaked.
    #
    # Nullable for the same reason as Principle: enterprise_initiatives is an
    # existing table and reconcile-schema can only ADD nullable columns (ADR
    # 0002). Isolation keys on isinstance(TenantMixin), not on nullability.
    # Pre-existing rows are hidden until backfilled:
    #     flask --app manage backfill-initiative-org
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    id = db.Column(db.Integer, primary_key=True)

    # See the ADR-0008 note in this class's docstring: set only when someone
    # with direct knowledge confirms this row and a StrategicInitiative row
    # describe the same real-world programme.
    linked_strategic_initiative_id = db.Column(
        db.Integer, db.ForeignKey("strategic_initiatives.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    # Initiative identity
    name = db.Column(db.String(200), nullable=False, index=True)
    code = db.Column(
        db.String(50), unique=True
    )  # Project code: SAP - 2025, CLOUD-MIGRATE - 01
    description = db.Column(db.Text)

    # Initiative classification
    initiative_type = db.Column(
        db.String(50)
    )  # 'transformation', 'upgrade', 'consolidation', 'modernization', 'migration'
    program_type = db.Column(
        db.String(50)
    )  # 'strategic', 'operational', 'compliance', 'innovation'
    scope = db.Column(
        db.String(30)
    )  # 'global', 'regional', 'business_unit', 'department'

    # Strategic alignment
    strategic_objective = db.Column(db.Text)
    business_drivers = db.Column(db.Text)  # JSON array
    expected_benefits = db.Column(db.Text)  # JSON array
    success_metrics = db.Column(db.Text)  # JSON: KPIs and targets

    # Timeline
    planned_start_date = db.Column(db.DateTime)
    planned_end_date = db.Column(db.DateTime)
    actual_start_date = db.Column(db.DateTime)
    actual_end_date = db.Column(db.DateTime)
    current_phase = db.Column(
        db.String(50)
    )  # 'planning', 'design', 'implementation', 'testing', 'deployment', 'closure'

    # Budget and resources
    approved_budget = db.Column(db.Numeric(15, 2))  # In currency units
    spent_to_date = db.Column(db.Numeric(15, 2))
    forecast_cost = db.Column(db.Numeric(15, 2))
    budget_variance_percentage = db.Column(db.Float)

    # Team and governance
    executive_sponsor = db.Column(db.String(200))
    program_manager = db.Column(db.String(200))
    business_owner = db.Column(db.String(200))
    it_owner = db.Column(db.String(200))
    team_size = db.Column(db.Integer)

    # Status and health
    status = db.Column(
        db.String(30), default="planning"
    )  # 'planning', 'active', 'on_hold', 'completed', 'cancelled'
    health_status = db.Column(db.String(20))  # 'green', 'yellow', 'red'
    risk_level = db.Column(db.String(20))  # 'low', 'medium', 'high', 'critical'
    completion_percentage = db.Column(db.Integer)  # 0 - 100%

    # Impact assessment
    impacted_business_units = db.Column(db.Text)  # JSON array
    impacted_user_count = db.Column(db.Integer)
    change_magnitude = db.Column(
        db.String(20)
    )  # 'low', 'medium', 'high', 'transformational'

    # Decision tracking
    vendor_selection_status = db.Column(
        db.String(30)
    )  # 'open', 'evaluating', 'selected', 'approved'
    selected_vendor_id = db.Column(db.Integer, db.ForeignKey("vendor_organizations.id"))
    vendor_selection_date = db.Column(db.DateTime)
    vendor_selection_rationale = db.Column(db.Text)

    # Documentation
    business_case_url = db.Column(db.String(500))
    project_charter_url = db.Column(db.String(500))
    architecture_decision_records = db.Column(db.Text)  # JSON array of ADR URLs
    lessons_learned = db.Column(db.Text)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    created_by = db.relationship("User", backref="created_initiatives")
    selected_vendor = db.relationship(
        "VendorOrganization", foreign_keys=[selected_vendor_id]
    )

    target_capabilities = db.relationship(
        "BusinessCapability",
        secondary="initiative_capabilities",
        viewonly=True,
    )
    evaluated_vendors = db.relationship(
        "VendorOrganization",
        secondary=initiative_vendors,
        back_populates="evaluated_in_initiatives",
    )
    technology_stacks = db.relationship(
        "TechnologyStack",
        secondary=initiative_tech_stacks,
        back_populates="initiatives",
    )

    # Strategy-to-Implementation Traceability (EA Intelligence Enhancement)
    strategic_goals = db.relationship(
        "Goal", secondary="initiative_goals", back_populates="initiatives"
    )
    principles = db.relationship(
        "Principle", secondary="initiative_principles", back_populates="initiatives"
    )

    # Note: Enterprise Architecture relationships (CapabilityInitiativeMapping, ArchiMateComponent,
    # InitiativeDependency, InitiativeResource, InitiativeMilestone) are defined in
    # app.models.enterprise_architecture and use backref to link to this model.

    def calculate_budget_variance(self):
        """Calculate budget variance percentage."""
        if self.approved_budget and self.forecast_cost:
            variance = (
                (self.forecast_cost - self.approved_budget) / self.approved_budget
            ) * 100
            self.budget_variance_percentage = round(variance, 2)
            db.session.commit()
        return self.budget_variance_percentage

    def get_business_drivers(self):
        """Parse business drivers JSON."""
        if self.business_drivers:
            return json.loads(self.business_drivers)
        return []

    def get_expected_benefits(self):
        """Parse expected benefits JSON."""
        if self.expected_benefits:
            return json.loads(self.expected_benefits)
        return []

    def get_impacted_business_units(self):
        """Parse impacted business units JSON."""
        if self.impacted_business_units:
            return json.loads(self.impacted_business_units)
        return []

    def to_dict(self):
        """Convert model to dictionary for API serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "vendor_type": self.vendor_type,
            "headquarters_location": self.headquarters_location,
            "website": self.website,
            "gartner_magic_quadrant_position": self.gartner_magic_quadrant_position,
            "strategic_tier": self.strategic_tier,
            "enterprise_readiness_score": self.enterprise_readiness_score,
            "partnership_level": self.partnership_level,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<EnterpriseInitiative {self.code or self.name}: {self.status}>"


# ============================================================================
# PRD-V02: VENDOR DATA SOURCE TRACKING MODELS
# ============================================================================


class VendorDataSource(db.Model):
    """Track where vendor data comes from for auditability (PRD-V02)."""

    __tablename__ = "vendor_data_sources"

    id = db.Column(db.Integer, primary_key=True)
    vendor_id = db.Column(
        db.Integer, db.ForeignKey("vendor_organizations.id"), nullable=False
    )
    data_field = db.Column(
        db.String(100), nullable=False
    )  # e.g., "gartner_mq_position", "g2_rating"
    source_type = db.Column(
        db.String(50), nullable=False
    )  # "manual", "api", "import", "ai_estimated"
    source_name = db.Column(
        db.String(200)
    )  # "Gartner MQ 2024", "G2 API", "Vendor Website"
    source_url = db.Column(db.String(500))
    retrieved_at = db.Column(db.DateTime, default=datetime.utcnow)
    confidence = db.Column(
        db.Float, default=1.0
    )  # 0.0 - 1.0 confidence in data accuracy
    verified_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    verified_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    vendor = db.relationship("VendorOrganization", backref="data_sources")
    verified_by = db.relationship("User", foreign_keys=[verified_by_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return f"<VendorDataSource {self.vendor.name if self.vendor else 'Unknown'}: {self.data_field}>"


class VendorProductPricing(db.Model):
    """Detailed pricing information for vendor products (PRD-V02)."""

    __tablename__ = "vendor_product_pricing"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("vendor_products.id"), nullable=False
    )
    pricing_model = db.Column(
        db.String(50), nullable=False
    )  # "per_user", "per_core", "revenue_share", "flat_fee"
    tier_name = db.Column(
        db.String(100), nullable=False
    )  # "Standard", "Professional", "Enterprise"
    list_price_annual = db.Column(db.Numeric(15, 2))  # Annual list price in USD
    typical_discount_percent = db.Column(
        db.Integer, default=0
    )  # 10 - 40% typical discount
    min_users = db.Column(db.Integer)
    max_users = db.Column(db.Integer)
    includes_support = db.Column(db.Boolean, default=True)
    currency = db.Column(db.String(3), default="USD")
    effective_date = db.Column(db.Date, default=datetime.utcnow().date)
    expiry_date = db.Column(db.Date)
    source = db.Column(db.String(200))  # "Vendor Website", "Sales Quote", "Contract"

    # Additional pricing details
    billing_frequency = db.Column(
        db.String(30), default="annual"
    )  # monthly, quarterly, annual
    contract_term_months = db.Column(db.Integer, default=12)
    setup_fee = db.Column(db.Numeric(15, 2), default=0)
    implementation_fee = db.Column(db.Numeric(15, 2), default=0)
    training_included = db.Column(db.Boolean, default=False)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # --- Vendor Pricing Pipeline (Wave 1) ---
    # Data provenance — separate from existing confidence_level (TCO confidence)
    data_source_type = db.Column(db.String(30), nullable=False, default='seeded',
                                  server_default='seeded')
    # How many architects have validated this price
    confirmed_by_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    # Staleness detection
    last_verified_at = db.Column(db.DateTime, nullable=True)
    # Multi-tenancy hook — bare integer, NO FK
    organization_id = db.Column(db.Integer, nullable=True, index=True)

    # Relationships
    product = db.relationship("VendorProduct", backref="pricing_tiers")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def calculate_effective_price(self, user_count: int = None) -> Decimal:
        """Calculate effective price based on user count and discounts."""
        base_price = self.list_price_annual or 0

        # Apply typical discount
        if self.typical_discount_percent:
            discount_amount = base_price * (self.typical_discount_percent / 100)
            base_price = base_price - discount_amount

        # Calculate per-user price if applicable
        if self.pricing_model == "per_user" and user_count and self.min_users:
            if user_count < self.min_users:
                # Charge for minimum users
                effective_users = self.min_users
            elif self.max_users and user_count > self.max_users:
                # Charge for maximum users or negotiate enterprise pricing
                effective_users = self.max_users
            else:
                effective_users = user_count

            return base_price * effective_users

        return base_price

    def __repr__(self):
        return f"<VendorProductPricing {self.product.name if self.product else 'Unknown'}: {self.tier_name}>"


# ============================================================================
# PRD-V03: TCO CALCULATION MODELS
# ============================================================================


class TCOCalculation(db.Model):
    """TCO calculation results with 12 - category breakdown (PRD-V03)."""

    __tablename__ = "tco_calculations"

    id = db.Column(db.Integer, primary_key=True)
    vendor_product_id = db.Column(
        db.Integer, db.ForeignKey("vendor_products.id"), nullable=False
    )
    user_count = db.Column(db.Integer, nullable=False)
    org_size = db.Column(db.String(50))  # "smb", "midmarket", "enterprise"
    industry = db.Column(db.String(100))
    tco_period_years = db.Column(db.Integer, default=5, nullable=False)

    # TCO breakdown (all in USD)
    software_licensing_total = db.Column(db.Numeric(15, 2))
    support_maintenance_total = db.Column(db.Numeric(15, 2))
    cloud_infrastructure_total = db.Column(db.Numeric(15, 2))
    implementation_services_total = db.Column(db.Numeric(15, 2))
    data_migration_total = db.Column(db.Numeric(15, 2))
    integration_development_total = db.Column(db.Numeric(15, 2))
    customization_total = db.Column(db.Numeric(15, 2))
    training_total = db.Column(db.Numeric(15, 2))
    change_management_total = db.Column(db.Numeric(15, 2))
    internal_labor_total = db.Column(db.Numeric(15, 2))
    ongoing_enhancements_total = db.Column(db.Numeric(15, 2))
    exit_costs_total = db.Column(db.Numeric(15, 2))

    # Summary calculations
    total_tco = db.Column(db.Numeric(15, 2))
    annual_average = db.Column(db.Numeric(15, 2))
    per_user_annual = db.Column(db.Numeric(10, 2))

    # Benchmark comparison
    industry_median_tco = db.Column(db.Numeric(15, 2))
    org_size_median_tco = db.Column(db.Numeric(15, 2))
    vs_industry_percentage = db.Column(db.Float)  # % difference from industry median
    vs_org_size_percentage = db.Column(db.Float)  # % difference from org size median

    # Sensitivity analysis
    min_tco = db.Column(db.Numeric(15, 2))  # Best case (-20%)
    max_tco = db.Column(db.Numeric(15, 2))  # Worst case (+20%)
    sensitivity_factors = db.Column(db.Text)  # JSON: factors affecting TCO

    # Assumptions and confidence
    confidence_level = db.Column(db.String(20))  # "high", "medium", "low"
    key_assumptions = db.Column(db.Text)  # JSON array of assumptions

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    vendor_product = db.relationship("VendorProduct", backref="tco_calculations")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def get_yearly_breakdown(self) -> List[Dict]:
        """Generate year-by-year cost breakdown."""
        if not self.total_tco:
            return []

        yearly_data = []

        for year in range(1, self.tco_period_years + 1):
            year_cost = {
                "year": year,
                "licensing": self.software_licensing_total / self.tco_period_years,
                "support": self.support_maintenance_total / self.tco_period_years,
                "infrastructure": self.cloud_infrastructure_total
                / self.tco_period_years,
                "internal_labor": self.internal_labor_total / self.tco_period_years,
                "enhancements": self.ongoing_enhancements_total / self.tco_period_years
                if year > 1
                else 0,
            }

            # One-time costs in Year 1
            if year == 1:
                year_cost["implementation"] = self.implementation_services_total
                year_cost["data_migration"] = self.data_migration_total
                year_cost["integration"] = self.integration_development_total
                year_cost["customization"] = self.customization_total
                year_cost["training"] = self.training_total
                year_cost["change_management"] = self.change_management_total

            # Exit costs in final year
            if year == self.tco_period_years:
                year_cost["exit_reserve"] = self.exit_costs_total

            year_cost["total"] = sum(v for k, v in year_cost.items() if k != "year")
            yearly_data.append(year_cost)

        return yearly_data

    def __repr__(self):
        return f"<TCOCalculation {self.vendor_product.name if self.vendor_product else 'Unknown'}: {self.total_tco}>"


# ============================================================================
# PRD-V05: VENDOR RISK ASSESSMENT MODELS
# ============================================================================


class VendorRiskAssessment(db.Model):
    """Multi-dimensional risk scoring for vendor products (PRD-V05)."""

    __tablename__ = "vendor_risk_assessments"

    id = db.Column(db.Integer, primary_key=True)
    vendor_product_id = db.Column(
        db.Integer, db.ForeignKey("vendor_products.id"), nullable=False
    )
    assessment_date = db.Column(db.DateTime, default=datetime.utcnow)

    # Risk category scores (1 - 10 scale, 10 = highest risk)
    financial_risk_score = db.Column(db.Integer)  # Vendor financial stability
    implementation_risk_score = db.Column(
        db.Integer
    )  # Implementation complexity/failure
    market_risk_score = db.Column(db.Integer)  # Market position/longevity
    technology_risk_score = db.Column(db.Integer)  # Technology obsolescence
    vendor_lock_in_risk_score = db.Column(db.Integer)  # Switching costs
    security_risk_score = db.Column(db.Integer)  # Security vulnerabilities
    compliance_risk_score = db.Column(db.Integer)  # Regulatory compliance
    support_risk_score = db.Column(db.Integer)  # Support quality/availability

    # Overall risk metrics
    overall_risk_score = db.Column(db.Integer)  # Weighted average of all risks
    risk_level = db.Column(db.String(20))  # "low", "medium", "high", "critical"

    # Risk factors and mitigations
    risk_factors = db.Column(db.Text)  # JSON array of identified risk factors
    mitigation_strategies = db.Column(db.Text)  # JSON array of mitigation approaches
    contingency_plans = db.Column(db.Text)  # JSON array of backup plans

    # Risk scoring weights (customizable by organization)
    financial_weight = db.Column(db.Float, default=0.15)
    implementation_weight = db.Column(db.Float, default=0.20)
    market_weight = db.Column(db.Float, default=0.15)
    technology_weight = db.Column(db.Float, default=0.10)
    vendor_lock_in_weight = db.Column(db.Float, default=0.15)
    security_weight = db.Column(db.Float, default=0.10)
    compliance_weight = db.Column(db.Float, default=0.10)
    support_weight = db.Column(db.Float, default=0.05)

    # Assessment metadata
    assessment_methodology = db.Column(
        db.String(100)
    )  # "automated", "manual", "hybrid"
    data_sources = db.Column(db.Text)  # JSON array of data sources used
    confidence_score = db.Column(db.Float)  # 0.0 - 1.0 confidence in assessment
    next_review_date = db.Column(db.DateTime)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    vendor_product = db.relationship("VendorProduct", backref="risk_assessments")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def calculate_overall_risk(self) -> int:
        """Calculate weighted overall risk score."""
        scores = [
            (self.financial_risk_score or 0, self.financial_weight),
            (self.implementation_risk_score or 0, self.implementation_weight),
            (self.market_risk_score or 0, self.market_weight),
            (self.technology_risk_score or 0, self.technology_weight),
            (self.vendor_lock_in_risk_score or 0, self.vendor_lock_in_weight),
            (self.security_risk_score or 0, self.security_weight),
            (self.compliance_risk_score or 0, self.compliance_weight),
            (self.support_risk_score or 0, self.support_weight),
        ]

        weighted_sum = sum(score * weight for score, weight in scores)
        total_weight = sum(weight for _, weight in scores)

        return round(weighted_sum / total_weight) if total_weight > 0 else 0

    def get_risk_level(self) -> str:
        """Determine risk level based on overall score."""
        score = self.overall_risk_score or self.calculate_overall_risk()

        if score <= 3:
            return "low"
        elif score <= 6:
            return "medium"
        elif score <= 8:
            return "high"
        else:
            return "critical"

    def get_risk_factors(self) -> List[str]:
        """Parse risk factors JSON."""
        if self.risk_factors:
            return json.loads(self.risk_factors)
        return []

    def set_risk_factors(self, factors: List[str]):
        """Set risk factors from list."""
        self.risk_factors = json.dumps(factors)

    def get_mitigation_strategies(self) -> List[str]:
        """Parse mitigation strategies JSON."""
        if self.mitigation_strategies:
            return json.loads(self.mitigation_strategies)
        return []

    def set_mitigation_strategies(self, strategies: List[str]):
        """Set mitigation strategies from list."""
        self.mitigation_strategies = json.dumps(strategies)

    def __repr__(self):
        return f"<VendorRiskAssessment {self.vendor_product.name if self.vendor_product else 'Unknown'}: {self.risk_level} risk>"


class VendorArchiMateTemplate(db.Model):
    """
    Canonical vendor→ArchiMate element mapping.

    Provides deterministic, versioned vendor templates so that when an architect
    links an SAP or Microsoft product, Entelim can auto-populate the correct
    Technology layer elements without fuzzy search.

    Populated by: flask seed-vendor-templates
    Used by: VendorTemplateService.populate_from_vendor()
    """

    __tablename__ = "vendor_archimate_templates"

    # Canonical vendor keys — must match VENDOR_KEY_MAP in VendorTemplateService
    VENDOR_KEYS = ["SAP", "MICROSOFT_POWER", "MICROSOFT_DYNAMICS"]

    id = db.Column(db.Integer, primary_key=True)
    vendor_key = db.Column(db.String(50), nullable=False, index=True)   # "SAP"|"MICROSOFT_POWER"|"MICROSOFT_DYNAMICS"
    element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id", ondelete="SET NULL"), nullable=True)
    element_name = db.Column(db.String(255), nullable=False)   # Denormalised — preserved if element deleted
    element_type = db.Column(db.String(100))   # Node|SystemSoftware|ApplicationComponent
    archimate_layer = db.Column(db.String(50))   # Technology|Application
    mandatory = db.Column(db.Boolean, default=True)   # True = always linked; False = suggested
    version = db.Column(db.String(20), default="2025.1")   # Template version
    display_order = db.Column(db.Integer, default=0)   # Order in review panel
    # JSON: {"fields": [...], "business_rules": [...]} — pre-populated spec_data for DataObject templates.
    # When a vendor template is applied to a solution, these fields are written to the SAE junction
    # with fields_status="vendor_seeded" so architects can spot-check rather than confirm from scratch.
    spec_data_seed = db.Column(db.Text, nullable=True)

    def get_spec_data_seed(self):
        """Return parsed spec_data_seed dict, or None if not set."""
        import json
        if not self.spec_data_seed:
            return None
        try:
            return json.loads(self.spec_data_seed)
        except (ValueError, TypeError):
            return None

    def to_dict(self):
        return {
            "id": self.id,
            "vendor_key": self.vendor_key,
            "element_id": self.element_id,
            "element_name": self.element_name,
            "element_type": self.element_type,
            "archimate_layer": self.archimate_layer,
            "mandatory": self.mandatory,
            "version": self.version,
            "display_order": self.display_order,
        }

    def __repr__(self):
        return f"<VendorArchiMateTemplate vendor={self.vendor_key} element={self.element_name}>"
