"""
Vendor Product Catalog Models

Three-level vendor hierarchy: Organization → Product Family → Product
Enhanced vendor matching with AI extraction and version tracking.
"""

import json
from datetime import datetime, timedelta

from .. import db


class VendorProductFamily(db.Model):
    """Product family level (e.g., SAP S/4HANA, Salesforce Sales Cloud)."""

    __tablename__ = "vendor_product_families"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.BigInteger, primary_key=True)
    vendor_id = db.Column(db.BigInteger, db.ForeignKey("vendor_organizations.id"), nullable=False)
    family_name = db.Column(db.String(200), nullable=False, index=True)
    family_code = db.Column(db.String(50), unique=True, index=True)  # e.g., "S4H", "SF"
    category = db.Column(db.String(100))  # "ERP", "CRM", "HCM", "SCM", "BI"
    description = db.Column(db.Text)

    # Product family metadata
    target_market = db.Column(db.String(100))  # "enterprise", "mid_market", "small_business"
    deployment_models = db.Column(db.Text)  # JSON: ["cloud", "on_premise", "hybrid"]
    integration_complexity = db.Column(db.Integer)  # 1 - 10 scale
    typical_implementation_time = db.Column(db.Integer)  # months
    average_total_cost = db.Column(db.Numeric(15, 2))  # USD

    # Status and lifecycle
    status = db.Column(db.String(30), default="active")  # active, deprecated, emerging
    first_release_date = db.Column(db.DateTime)
    last_update_date = db.Column(db.DateTime)
    end_of_life_date = db.Column(db.DateTime)

    # Market position
    market_leader = db.Column(db.Boolean, default=False)
    market_share_percentage = db.Column(db.Numeric(5, 2))
    gartner_recognition = db.Column(db.Text)  # JSON array of Gartner recognitions

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    vendor = db.relationship("VendorOrganization", backref="product_families")
    products = db.relationship(
        "VendorProductDetail",
        back_populates="product_family",
        cascade="all, delete-orphan",
        lazy="select",
    )
    created_by = db.relationship("User", backref="created_product_families")

    @property
    def is_stale_vendor_data(self) -> bool:
        """True when last_update_date is null or more than 90 days old."""
        if self.last_update_date is None:
            return True
        return datetime.utcnow() - self.last_update_date > timedelta(days=90)

    def __repr__(self):
        return f'<VendorProductFamily {self.family_name} ({self.vendor.name if self.vendor else "Unknown"})>'

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor.name if self.vendor else None,
            "family_name": self.family_name,
            "family_code": self.family_code,
            "category": self.category,
            "description": self.description,
            "target_market": self.target_market,
            "deployment_models": json.loads(self.deployment_models)
            if self.deployment_models
            else [],
            "integration_complexity": self.integration_complexity,
            "typical_implementation_time": self.typical_implementation_time,
            "average_total_cost": float(self.average_total_cost)
            if self.average_total_cost
            else None,
            "status": self.status,
            "first_release_date": self.first_release_date.isoformat()
            if self.first_release_date
            else None,
            "last_update_date": self.last_update_date.isoformat()
            if self.last_update_date
            else None,
            "end_of_life_date": self.end_of_life_date.isoformat()
            if self.end_of_life_date
            else None,
            "market_leader": self.market_leader,
            "market_share_percentage": float(self.market_share_percentage)
            if self.market_share_percentage
            else None,
            "gartner_recognition": json.loads(self.gartner_recognition)
            if self.gartner_recognition
            else [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class VendorProductDetail(db.Model):
    """Specific product with version tracking (e.g., SAP S/4HANA 2023)."""

    __tablename__ = "vendor_product_details"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.BigInteger, primary_key=True)
    family_id = db.Column(
        db.BigInteger, db.ForeignKey("vendor_product_families.id"), nullable=False
    )
    product_name = db.Column(db.String(200), nullable=False, index=True)
    product_code = db.Column(db.String(50), index=True)  # e.g., "S4F", "SFE"
    # The public catalogue's address for this shared record (ADR-0021); null when
    # the product is not in the catalogue.
    catalogue_slug = db.Column(db.String(80), unique=True, index=True)
    current_version = db.Column(db.String(50))  # e.g., "2023", "2023 Q2", "v15.0"
    deployment_model = db.Column(db.String(50))  # "Cloud", "On-Premise", "Hybrid", "SaaS"

    # Product details
    description = db.Column(db.Text)
    key_features = db.Column(db.Text)  # JSON array of key features
    target_industries = db.Column(db.Text)  # JSON array of target industries
    company_sizes = db.Column(db.Text)  # JSON array: ["enterprise", "mid_market", "small"]
    geographic_availability = db.Column(db.Text)  # JSON array of regions/countries

    # Technical specifications
    minimum_requirements = db.Column(db.Text)  # JSON: hardware, software, network requirements
    supported_integrations = db.Column(db.Text)  # JSON array of supported systems
    api_capabilities = db.Column(db.Text)  # JSON: REST, SOAP, GraphQL, etc.
    compliance_standards = db.Column(db.Text)  # JSON: SOC2, ISO27001, GDPR, etc.

    # Pricing and licensing
    pricing_model = db.Column(
        db.String(50)
    )  # "per_user", "per_instance", "revenue_share", "custom"
    price_range_min = db.Column(db.Numeric(15, 2))  # USD per user/month or annual
    price_range_max = db.Column(db.Numeric(15, 2))  # USD per user/month or annual
    licensing_complexity = db.Column(db.Integer)  # 1 - 10 scale
    free_tier_available = db.Column(db.Boolean, default=False)
    trial_period_days = db.Column(db.Integer)

    # Lifecycle and support
    release_date = db.Column(db.DateTime)
    support_level = db.Column(db.String(30))  # "basic", "standard", "premium", "enterprise"
    sla_uptime_percentage = db.Column(db.Numeric(5, 2))  # e.g., 99.9
    support_response_time = db.Column(db.String(50))  # e.g., "24x7", "business_hours"
    end_of_support_date = db.Column(db.DateTime)

    # Market position
    competitive_positioning = db.Column(db.String(100))  # "market_leader", "challenger", "niche"
    differentiation_factors = db.Column(db.Text)  # JSON array of key differentiators
    customer_satisfaction_score = db.Column(db.Numeric(5, 2))  # 0 - 5 scale from reviews
    implementation_complexity = db.Column(db.Integer)  # 1 - 10 scale

    # Status
    status = db.Column(db.String(30), default="active")  # active, deprecated, beta, preview
    availability_status = db.Column(
        db.String(30)
    )  # "generally_available", "limited", "coming_soon"

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    product_family = db.relationship("VendorProductFamily", back_populates="products")
    aliases = db.relationship(
        "VendorProductAlias", back_populates="product", cascade="all, delete-orphan", lazy="select"
    )
    created_by = db.relationship("User", backref="created_vendor_products")

    def __repr__(self):
        return f"<VendorProduct {self.product_name} v{self.current_version}>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "family_id": self.family_id,
            "family_name": self.product_family.family_name if self.product_family else None,
            "product_name": self.product_name,
            "product_code": self.product_code,
            "current_version": self.current_version,
            "deployment_model": self.deployment_model,
            "description": self.description,
            "key_features": json.loads(self.key_features) if self.key_features else [],
            "target_industries": json.loads(self.target_industries)
            if self.target_industries
            else [],
            "company_sizes": json.loads(self.company_sizes) if self.company_sizes else [],
            "geographic_availability": json.loads(self.geographic_availability)
            if self.geographic_availability
            else [],
            "minimum_requirements": json.loads(self.minimum_requirements)
            if self.minimum_requirements
            else {},
            "supported_integrations": json.loads(self.supported_integrations)
            if self.supported_integrations
            else [],
            "api_capabilities": json.loads(self.api_capabilities) if self.api_capabilities else [],
            "compliance_standards": json.loads(self.compliance_standards)
            if self.compliance_standards
            else [],
            "pricing_model": self.pricing_model,
            "price_range_min": float(self.price_range_min) if self.price_range_min else None,
            "price_range_max": float(self.price_range_max) if self.price_range_max else None,
            "licensing_complexity": self.licensing_complexity,
            "free_tier_available": self.free_tier_available,
            "trial_period_days": self.trial_period_days,
            "release_date": self.release_date.isoformat() if self.release_date else None,
            "support_level": self.support_level,
            "sla_uptime_percentage": float(self.sla_uptime_percentage)
            if self.sla_uptime_percentage
            else None,
            "support_response_time": self.support_response_time,
            "end_of_support_date": self.end_of_support_date.isoformat()
            if self.end_of_support_date
            else None,
            "competitive_positioning": self.competitive_positioning,
            "differentiation_factors": json.loads(self.differentiation_factors)
            if self.differentiation_factors
            else [],
            "customer_satisfaction_score": float(self.customer_satisfaction_score)
            if self.customer_satisfaction_score
            else None,
            "implementation_complexity": self.implementation_complexity,
            "status": self.status,
            "availability_status": self.availability_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class VendorProductAlias(db.Model):
    """Handle naming variations and alternative product names."""

    __tablename__ = "vendor_product_aliases"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.BigInteger, primary_key=True)
    product_id = db.Column(
        db.BigInteger, db.ForeignKey("vendor_product_details.id"), nullable=False
    )
    alias = db.Column(db.String(200), nullable=False, index=True)
    alias_type = db.Column(
        db.String(50)
    )  # "common_name", "abbreviation", "former_name", "marketing_name"
    confidence_score = db.Column(
        db.Numeric(3, 2)
    )  # 0.0 - 1.0 confidence that this alias refers to the product
    source = db.Column(
        db.String(100)
    )  # "vendor_site", "industry_report", "user_generated", "ai_extracted"

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    product = db.relationship("VendorProductDetail", back_populates="aliases")
    created_by = db.relationship("User", backref="created_product_aliases")

    def __repr__(self):
        return f'<VendorProductAlias {self.alias} -> {self.product.product_name if self.product else "Unknown"}>'


# Compatibility export for callers that historically imported the mapping from
# this module.  There is one canonical application-to-product aggregate and one
# table mapping; the former duplicate declared different foreign keys and could
# invalidate every mapper in a worker once this catalogue was imported.
from ..capability_to_vendor_mapping import ApplicationVendorProductMapping  # noqa: E402,F401
