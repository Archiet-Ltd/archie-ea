"""
Capability to Vendor Mapping Models

Maps the 4 specialization types to vendors and applications:

1. TechnicalCapability (TECHNICAL) ↔ VendorProduct
   - Which vendor products support this technical capability?
   - What's the coverage, maturity, and fit?

2. UnifiedCapability (BUSINESS) ↔ ApplicationComponent
   - Which applications implement this business capability?
   - How well do they support it?

3. UnifiedCapability (BUSINESS) ↔ VendorOrganization (Strategic)
   - Which vendors enable this business capability?
   - Strategic vendor relationship

4. ApplicationComponent ↔ VendorProduct
   - Which vendor products does this application use?
   - Technology stack alignment
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app import db
from app.models.mixins import TenantMixin

# ============================================================================
# MAPPING 1: TechnicalCapability ↔ VendorProduct
# ============================================================================


class TechnicalCapabilityVendorMapping(db.Model):
    __mapper_args__ = {
        "polymorphic_identity": "app.models.capability_to_vendor_mapping.TechnicalCapabilityVendorMapping"
    }

    """
    Maps TechnicalCapability (TECHNICAL) to VendorProduct.

    Answers: Which vendor products can implement this technical capability?
    - SAP S/4HANA implements "Enterprise Resource Planning"
    - Oracle Cloud implements "Cloud Platform Management"
    - Microsoft Azure implements "Compute & Infrastructure"

    Used for:
    - Technology selection: Find vendors that support needed technical capabilities
    - Product fit analysis: Evaluate how well a product covers requirements
    - Architecture planning: Map business capabilities → technical capabilities → vendor products
    """

    __tablename__ = "technical_capability_vendor_mappings"

    id = db.Column(db.Integer, primary_key=True)

    # Core relationships
    technical_capability_id = db.Column(
        db.Integer,
        db.ForeignKey("technical_capabilities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vendor_product_id = db.Column(
        db.Integer,
        db.ForeignKey("vendor_products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Coverage & Fit Metrics
    coverage_percentage = db.Column(
        db.Float, default=75.0
    )  # 0 - 100%: How much capability is covered
    maturity_level = db.Column(db.Integer, default=3)  # 1 - 5 CMM: Production readiness
    fit_score = db.Column(db.Float, default=75.0)  # 0 - 100: Overall fit for this capability

    # Implementation metrics
    implementation_effort = db.Column(db.String(20))  # minimal, low, medium, high, complex
    time_to_value_days = db.Column(db.Integer)  # Estimated days to production value
    customization_required = db.Column(db.Boolean, default=False)

    # Cost impact
    additional_cost_estimate = db.Column(db.Numeric(12, 2))  # Additional cost for this capability
    roi_percentage = db.Column(db.Float)  # Expected ROI for implementing this capability

    # Quality ratings (1 - 10)
    performance_rating = db.Column(db.Integer)  # How well does it perform?
    usability_rating = db.Column(db.Integer)  # How easy to use?
    reliability_rating = db.Column(db.Integer)  # How stable?
    support_rating = db.Column(db.Integer)  # Quality of vendor support?

    # Risk assessment
    risk_level = db.Column(db.String(20))  # low, medium, high, critical
    risk_factors = db.Column(db.Text)  # JSON: Array of risk descriptions

    # Evidence & validation
    reference_customers = db.Column(db.Text)  # JSON: Customer names using this combination
    case_studies_url = db.Column(db.String(500))
    last_validated_date = db.Column(db.DateTime)
    validated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Notes
    mapping_notes = db.Column(db.Text)  # Why/how does this vendor product support this capability?
    gap_description = db.Column(db.Text)  # What's NOT covered?

    # Audit
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    technical_capability = db.relationship(
        "TechnicalCapability",
        backref="vendor_product_mappings",
        foreign_keys=[technical_capability_id],
    )
    vendor_product = db.relationship(
        "VendorProduct", backref="technical_capability_mappings", foreign_keys=[vendor_product_id]
    )
    validated_by = db.relationship("User", foreign_keys=[validated_by_id])

    __table_args__ = (
        Index(
            "idx_tech_cap_vendor_map", "technical_capability_id", "vendor_product_id", unique=True
        ),
    )

    def __repr__(self):
        return f"<TechCapVendorMap {self.technical_capability_id}→{self.vendor_product_id}>"


# ============================================================================
# MAPPING 2: UnifiedCapability ↔ ApplicationComponent
# ============================================================================


class UnifiedCapabilityApplicationMapping(db.Model):
    __mapper_args__ = {
        "polymorphic_identity": "app.models.capability_to_vendor_mapping.UnifiedCapabilityApplicationMapping"
    }

    """
    Maps UnifiedCapability (BUSINESS) to ApplicationComponent.

    Answers: Which applications implement this business capability?
    - Salesforce implements "Customer Management"
    - SAP implements "Order Processing"
    - ServiceNow implements "Incident Management"

    Used for:
    - Portfolio analysis: Which applications support which business capabilities?
    - Gap analysis: Which capabilities lack application support?
    - Rationalization: Which applications are redundant?
    - Modernization: Which applications need replacement?
    """

    __tablename__ = "unified_capability_application_mappings"

    id = db.Column(db.Integer, primary_key=True)

    # Core relationships
    unified_capability_id = db.Column(
        db.Integer,
        db.ForeignKey("unified_capabilities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    application_component_id = db.Column(
        db.Integer,
        db.ForeignKey("application_components.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Support metrics
    support_level = db.Column(db.String(30))  # full, partial, minimal, unsupported
    coverage_percentage = db.Column(db.Float, default=75.0)  # 0 - 100%: How much is covered
    business_criticality = db.Column(db.String(20))  # critical, high, medium, low
    maturity_level = db.Column(db.Integer, default=3)  # 1 - 5 CMM

    # Quality assessment
    functional_fit_score = db.Column(db.Float, default=75.0)  # 0 - 100: How well does it fit?
    performance_rating = db.Column(db.Integer)  # 1 - 10
    usability_rating = db.Column(db.Integer)  # 1 - 10
    reliability_rating = db.Column(db.Integer)  # 1 - 10

    # Financial impact
    annual_cost = db.Column(db.Numeric(12, 2))
    cost_per_transaction = db.Column(db.Numeric(10, 4))
    roi_percentage = db.Column(db.Float)

    # Health & risk
    health_status = db.Column(db.String(20))  # healthy, at_risk, needs_replacement, deprecated
    end_of_life_risk = db.Column(db.String(20))  # low, medium, high
    technical_debt_rating = db.Column(db.Integer)  # 1 - 10

    # Alignment
    strategic_alignment = db.Column(db.String(20))  # aligned, partial, misaligned
    modernization_priority = db.Column(db.String(20))  # critical, high, medium, low, maintain

    # Gap analysis
    gaps_description = db.Column(db.Text)  # JSON: What's missing?
    workarounds = db.Column(db.Text)  # JSON: Current workarounds for gaps

    # Notes
    mapping_notes = db.Column(db.Text)  # Context and rationale

    # Audit
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    unified_capability = db.relationship(
        "UnifiedCapability", backref="application_mappings", foreign_keys=[unified_capability_id]
    )
    application = db.relationship(
        "ApplicationComponent",
        backref="unified_capability_mappings",
        foreign_keys=[application_component_id],
    )

    __table_args__ = (
        Index(
            "idx_unified_cap_app_map",
            "unified_capability_id",
            "application_component_id",
            unique=True,
        ),
    )

    def __repr__(self):
        return f"<UnifiedCapAppMap {self.unified_capability_id}→{self.application_component_id}>"


# ============================================================================
# MAPPING 3: UnifiedCapability ↔ VendorOrganization (Strategic)
# ============================================================================


class UnifiedCapabilityVendorOrganizationMapping(TenantMixin, db.Model):
    __mapper_args__ = {
        "polymorphic_identity": "app.models.capability_to_vendor_mapping.UnifiedCapabilityVendorOrganizationMapping"
    }

    """
    Strategic mapping of UnifiedCapability (BUSINESS) to VendorOrganization.

    Answers: Which vendors are strategic partners for this business capability?
    - SAP is strategic for "Order-to-Cash"
    - Workday is strategic for "Human Capital Management"
    - Salesforce is strategic for "Customer Relationship Management"

    Used for:
    - Vendor strategy: Who are our strategic partners?
    - Vendor risk: What's our dependency on this vendor?
    - Vendor consolidation: Can we reduce vendor count?
    - Contract negotiation: What's our leverage?
    """

    __tablename__ = "unified_capability_vendor_organization_mappings"
    __tenant_parent__ = (('unified_capability_id', 'unified_capabilities'),)

    id = db.Column(db.Integer, primary_key=True)

    # Core relationships
    unified_capability_id = db.Column(
        db.Integer,
        db.ForeignKey("unified_capabilities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vendor_organization_id = db.Column(
        db.Integer,
        db.ForeignKey("vendor_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Strategic relationship
    relationship_type = db.Column(db.String(30))  # primary, secondary, alternative, legacy
    strategic_importance = db.Column(db.String(20))  # critical, high, medium, low, optional
    relationship_strength = db.Column(
        db.Float, default=70.0
    )  # 0 - 100: How strong is relationship?

    # Coverage metrics
    capability_coverage_percentage = db.Column(db.Float, default=70.0)  # 0 - 100%
    number_of_products = db.Column(db.Integer, default=0)  # How many vendor products support this?
    primary_product_id = db.Column(db.Integer, db.ForeignKey("vendor_products.id"))  # Main product

    # Financial metrics
    annual_spend = db.Column(db.Numeric(12, 2))
    multi_year_commitment = db.Column(db.Boolean, default=False)
    contract_end_date = db.Column(db.DateTime)

    # Vendor evaluation
    capability_maturity = db.Column(db.Integer)  # 1 - 5: How mature is vendor's capability?
    market_position = db.Column(db.String(30))  # leader, challenger, niche
    innovation_score = db.Column(db.Float)  # 0 - 100: How innovative?
    financial_stability = db.Column(db.String(20))  # stable, growing, at_risk, declining

    # Risk assessment
    vendor_risk_level = db.Column(db.String(20))  # low, medium, high, critical
    concentration_risk = db.Column(db.String(20))  # low, medium, high
    lock_in_risk = db.Column(db.String(20))  # low, medium, high
    dependency_level = db.Column(db.String(20))  # low, medium, high, critical

    # Strategic options
    lock_in_potential = db.Column(db.String(20))  # low, medium, high
    replacement_difficulty = db.Column(db.String(20))  # easy, moderate, difficult, very_difficult
    alternative_vendor_available = db.Column(db.Boolean, default=False)
    alternative_vendor_id = db.Column(db.Integer, db.ForeignKey("vendor_organizations.id"))

    # Governance
    cio_approved = db.Column(db.Boolean, default=False)
    executive_sponsor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    contract_manager_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Notes
    strategic_notes = db.Column(db.Text)
    risk_mitigation_plan = db.Column(db.Text)

    # Audit
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    unified_capability = db.relationship(
        "UnifiedCapability",
        backref="vendor_organization_mappings",
        foreign_keys=[unified_capability_id],
    )
    vendor_organization = db.relationship(
        "VendorOrganization", backref="capability_mappings", foreign_keys=[vendor_organization_id]
    )
    primary_product = db.relationship("VendorProduct", foreign_keys=[primary_product_id])
    alternative_vendor = db.relationship("VendorOrganization", foreign_keys=[alternative_vendor_id])
    executive_sponsor = db.relationship("User", foreign_keys=[executive_sponsor_id])
    contract_manager = db.relationship("User", foreign_keys=[contract_manager_id])

    __table_args__ = (
        Index(
            "idx_unified_cap_vendor_org_map",
            "unified_capability_id",
            "vendor_organization_id",
            unique=True,
        ),
    )

    def __repr__(self):
        return (
            f"<UnifiedCapVendorOrgMap {self.unified_capability_id}→{self.vendor_organization_id}>"
        )


# ============================================================================
# MAPPING 4: ApplicationComponent ↔ VendorProduct (Technology Stack)
# ============================================================================


class ApplicationVendorProductMapping(TenantMixin, db.Model):
    __mapper_args__ = {
        "polymorphic_identity": "app.models.capability_to_vendor_mapping.ApplicationVendorProductMapping"
    }
    """
    Maps ApplicationComponent to VendorProduct (technology stack).

    Answers: Which vendor products does this application use?
    - Our CRM application uses Salesforce
    - Our ERP uses SAP S/4HANA + various plugins
    - Our Analytics uses Tableau + Snowflake

    Used for:
    - Technology inventory: What are we running?
    - Vendor management: Which vendors support which applications?
    - Upgrade planning: Which applications need vendor updates?
    - Cost management: What's the total cost of applications?
    """

    __tablename__ = "application_vendor_product_mappings"
    __tenant_parent__ = (('application_component_id', 'application_components'),)

    id = db.Column(db.Integer, primary_key=True)

    # Core relationships
    application_component_id = db.Column(
        db.Integer,
        db.ForeignKey("application_components.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vendor_product_id = db.Column(
        db.Integer,
        db.ForeignKey("vendor_products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Role in application
    role_type = db.Column(db.String(50))  # core, supporting, complementary, integration
    criticality = db.Column(db.String(20))  # mission_critical, important, supporting, optional
    primary_product = db.Column(db.Boolean, default=False)  # Is this the main product?

    # Version & deployment
    product_version = db.Column(db.String(50))  # Specific version running
    deployment_model = db.Column(db.String(50))  # on_premise, cloud, hybrid, saas
    number_of_instances = db.Column(db.Integer, default=1)
    number_of_users = db.Column(db.Integer)
    concurrent_users = db.Column(db.Integer)

    # Financial metrics
    license_cost_annual = db.Column(db.Numeric(12, 2))
    maintenance_cost_annual = db.Column(db.Numeric(12, 2))
    support_cost_annual = db.Column(db.Numeric(12, 2))
    total_cost_annual = db.Column(db.Numeric(12, 2))
    cost_per_user = db.Column(db.Numeric(10, 4))

    # Technical metrics
    integration_level = db.Column(db.String(20))  # tightly_coupled, loosely_coupled, standalone
    api_usage = db.Column(db.Boolean, default=False)
    number_of_interfaces = db.Column(db.Integer, default=0)
    data_volume_gb = db.Column(db.Float)  # Approximate data volume

    # Health & performance
    performance_rating = db.Column(db.Integer)  # 1 - 10
    uptime_percentage = db.Column(db.Float)  # 99.9%, etc.
    last_incident_date = db.Column(db.DateTime)
    incident_count_90days = db.Column(db.Integer)

    # Lifecycle
    go_live_date = db.Column(db.DateTime)
    license_expiry_date = db.Column(db.DateTime)
    planned_upgrade_date = db.Column(db.DateTime)
    end_of_life_date = db.Column(db.DateTime)
    sunset_status = db.Column(
        db.String(30)
    )  # active, planned_upgrade, planned_replacement, sunset_planned

    # Risk assessment
    upgrade_risk = db.Column(db.String(20))  # low, medium, high
    vendor_stability_risk = db.Column(db.String(20))
    lock_in_risk = db.Column(db.String(20))

    # Notes
    mapping_notes = db.Column(db.Text)
    known_issues = db.Column(db.Text)
    customizations = db.Column(db.Text)  # JSON: List of customizations

    # Audit
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    application = db.relationship(
        "ApplicationComponent",
        backref="vendor_product_mappings",
        foreign_keys=[application_component_id],
    )
    vendor_product = db.relationship(
        "VendorProduct", backref="application_mappings", foreign_keys=[vendor_product_id]
    )

    __table_args__ = (
        Index(
            "idx_app_vendor_product_map",
            "application_component_id",
            "vendor_product_id",
            unique=True,
        ),
    )

    def __repr__(self):
        return f"<AppVendorProductMap {self.application_component_id}→{self.vendor_product_id}>"


class CapabilityVendorProductMapping(db.Model):
    """Maps a unified capability to a vendor product (hybrid-mapping dashboard)."""

    __tablename__ = "capability_vendor_product_mapping"

    id = db.Column(db.Integer, primary_key=True)
    unified_capability_id = db.Column(db.Integer, index=True)
    vendor_product_id = db.Column(db.Integer, index=True)
    relationship_type = db.Column(db.String(50))
    mapping_strength = db.Column(db.Float)
    coverage_percentage = db.Column(db.Float)
    business_value = db.Column(db.String(30))
    strategic_importance = db.Column(db.String(30))
    created_at = db.Column(db.DateTime)

    def __repr__(self):
        return f"<CapabilityVendorProductMapping cap={self.unified_capability_id} vp={self.vendor_product_id}>"
