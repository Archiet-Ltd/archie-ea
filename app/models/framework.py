"""
Enterprise Framework Models

Comprehensive framework management for enterprise architecture, including:
- Architecture frameworks (TOGAF, Zachman, etc.)
- Compliance and regulatory frameworks
- Quality and standards frameworks
- Industry-specific frameworks

This module centralizes framework-related models and extends existing
framework configurations for complete enterprise architecture governance.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, event
from sqlalchemy.orm import validates

from app.datetime_helpers import utcnow

from .. import db
from .mixins import HybridTenantMixin


class EnterpriseArchitectureFramework(HybridTenantMixin, db.Model):
    """
    Enterprise Architecture Framework Model

    Manages architectural frameworks (TOGAF, Zachman, etc.) and their
    application within the organization.
    """

    __tablename__ = "enterprise_architecture_frameworks"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)

    # Framework identity
    name = db.Column(db.String(256), nullable=False, index=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.Text)
    framework_type = db.Column(
        db.String(50), nullable=False
    )  # architecture, governance, methodology, reference_model

    # Framework classification
    category = db.Column(db.String(50))  # togaf, zachman, feaf, dodaf, naf, modaf, custom
    version = db.Column(db.String(50))
    standard_body = db.Column(db.String(100))  # The Open Group, ISO, etc.

    # Framework characteristics
    scope = db.Column(db.String(50))  # enterprise, domain, application, technology
    maturity_level = db.Column(db.String(20))  # emerging, established, mature, industry_standard
    complexity_level = db.Column(db.String(20))  # low, medium, high, very_high
    adoption_difficulty = db.Column(db.String(20))  # easy, moderate, challenging, complex

    # Official references
    official_url = db.Column(db.String(500))
    documentation_url = db.Column(db.String(500))
    certification_url = db.Column(db.String(500))
    training_url = db.Column(db.String(500))

    # Framework components
    has_metamodel = db.Column(db.Boolean, default=True)
    has_methodology = db.Column(db.Boolean, default=True)
    has_reference_models = db.Column(db.Boolean, default=False)
    has_viewpoints = db.Column(db.Boolean, default=True)
    has_governance_model = db.Column(db.Boolean, default=False)

    # Framework content (JSON structures)
    metamodel_elements = db.Column(db.Text)  # JSON array of metamodel elements
    architectural_views = db.Column(db.Text)  # JSON array of views/viewpoints
    deliverables = db.Column(db.Text)  # JSON array of framework deliverables
    phases_stages = db.Column(db.Text)  # JSON array of methodology phases
    building_blocks = db.Column(db.Text)  # JSON array of building blocks

    # Organizational adoption
    adoption_status = db.Column(
        db.String(30), default="not_adopted"
    )  # not_adopted, planned, piloting, adopting, adopted, deprecated
    adoption_date = db.Column(db.Date)
    adoption_rationale = db.Column(db.Text)
    customization_notes = db.Column(db.Text)

    # Organizational usage
    primary_framework = db.Column(db.Boolean, default=False)  # Is this the primary EA framework?
    usage_scope = db.Column(db.String(100))  # Which parts of org use this?
    tailoring_approach = db.Column(db.Text)  # How is it customized for the org?

    # Integration with other frameworks
    compatible_frameworks = db.Column(db.Text)  # JSON array of compatible framework IDs
    complementary_frameworks = db.Column(db.Text)  # JSON array of complementary framework IDs
    replaces_framework_ids = db.Column(db.Text)  # JSON array of replaced framework IDs

    # Cost & resources
    license_cost_annual = db.Column(db.Float)
    training_cost_per_person = db.Column(db.Float)
    certification_cost = db.Column(db.Float)
    tool_costs_annual = db.Column(db.Float)
    estimated_implementation_months = db.Column(db.Integer)

    # Governance
    framework_owner = db.Column(db.String(100))
    framework_steward = db.Column(db.String(100))
    approval_status = db.Column(
        db.String(20), default="draft"
    )  # draft, proposed, approved, active, deprecated
    approval_date = db.Column(db.Date)
    review_frequency = db.Column(db.String(20))  # monthly, quarterly, annual
    next_review_date = db.Column(db.Date)

    # Status
    is_active = db.Column(db.Boolean, default=True)
    is_mandatory = db.Column(db.Boolean, default=False)
    status_notes = db.Column(db.Text)

    # Metadata
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "framework_type": self.framework_type,
            "category": self.category,
            "version": self.version,
            "adoption_status": self.adoption_status,
            "primary_framework": self.primary_framework,
            "is_active": self.is_active,
            "is_mandatory": self.is_mandatory,
        }

    def __repr__(self):
        return f"<EnterpriseArchitectureFramework {self.name} ({self.code})>"


class QualityFramework(HybridTenantMixin, db.Model):
    """
    Quality and Standards Framework Model

    Manages quality frameworks (ISO 9001, Six Sigma, TQM, etc.) and their
    application within the organization.
    """

    __tablename__ = "quality_frameworks"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)

    # Framework identity
    name = db.Column(db.String(256), nullable=False, index=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.Text)

    # Framework classification
    framework_type = db.Column(
        db.String(50)
    )  # iso_standard, methodology, process_framework, maturity_model
    quality_domain = db.Column(
        db.String(50)
    )  # quality_management, process_improvement, safety, environmental
    category = db.Column(
        db.String(50)
    )  # iso_9001, six_sigma, lean, tqm, cmmi, iso_27001, iso_14001

    # Standard details
    standard_body = db.Column(db.String(100))  # ISO, ASQ, CMMI Institute, etc.
    standard_number = db.Column(db.String(50))  # ISO 9001:2015, etc.
    version = db.Column(db.String(50))
    publication_date = db.Column(db.Date)
    revision_date = db.Column(db.Date)

    # Framework scope
    applies_to_industry = db.Column(db.String(100))  # manufacturing, services, all
    applies_to_processes = db.Column(db.Text)  # JSON array of applicable processes
    applies_to_departments = db.Column(db.Text)  # JSON array of departments
    geographic_scope = db.Column(db.String(50))  # global, regional, local

    # Framework components
    principles = db.Column(db.Text)  # JSON array of core principles
    requirements = db.Column(db.Text)  # JSON array of requirements
    tools_techniques = db.Column(db.Text)  # JSON array of tools/techniques
    metrics_kpis = db.Column(db.Text)  # JSON array of metrics/KPIs

    # Certification & compliance
    certification_available = db.Column(db.Boolean, default=False)
    certification_body = db.Column(db.String(100))
    certification_levels = db.Column(db.Text)  # JSON array of certification levels
    audit_requirements = db.Column(db.Text)

    # Organizational adoption
    adoption_status = db.Column(
        db.String(30), default="not_adopted"
    )  # not_adopted, planned, implementing, certified, maintaining
    certification_date = db.Column(db.Date)
    certification_expiry_date = db.Column(db.Date)
    certification_number = db.Column(db.String(100))
    last_audit_date = db.Column(db.Date)
    next_audit_date = db.Column(db.Date)
    audit_findings = db.Column(db.Text)  # JSON array of recent audit findings

    # Implementation details
    implementation_start_date = db.Column(db.Date)
    implementation_completion_date = db.Column(db.Date)
    implementation_effort_days = db.Column(db.Integer)
    implementation_cost = db.Column(db.Float)
    implementation_notes = db.Column(db.Text)

    # Benefits & ROI
    expected_benefits = db.Column(db.Text)  # JSON array of expected benefits
    realized_benefits = db.Column(db.Text)  # JSON array of realized benefits
    roi_percentage = db.Column(db.Float)
    payback_period_months = db.Column(db.Integer)

    # Integration with other frameworks
    complementary_frameworks = db.Column(db.Text)  # JSON array of complementary framework IDs
    prerequisite_frameworks = db.Column(db.Text)  # JSON array of prerequisite framework IDs

    # Governance
    framework_owner = db.Column(db.String(100))
    quality_manager = db.Column(db.String(100))
    management_representative = db.Column(db.String(100))
    is_mandatory = db.Column(db.Boolean, default=False)

    # Status
    is_active = db.Column(db.Boolean, default=True)
    status_notes = db.Column(db.Text)

    # Timestamps
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "framework_type": self.framework_type,
            "quality_domain": self.quality_domain,
            "category": self.category,
            "standard_number": self.standard_number,
            "adoption_status": self.adoption_status,
            "certification_available": self.certification_available,
            "is_active": self.is_active,
        }

    def __repr__(self):
        return f"<QualityFramework {self.name} ({self.code})>"


class IndustryFramework(HybridTenantMixin, db.Model):
    """
    Industry-Specific Framework Model

    Manages industry-specific frameworks, best practices, and reference models
    (e.g., APQC PCF, SCOR, eTOM, ISA - 95).
    """

    __tablename__ = "industry_frameworks"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)

    # Framework identity
    name = db.Column(db.String(256), nullable=False, index=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.Text)

    # Framework classification
    framework_type = db.Column(
        db.String(50)
    )  # process_framework, reference_model, best_practice, standard
    industry = db.Column(db.String(50))  # manufacturing, telecommunications, supply_chain, etc.
    category = db.Column(db.String(50))  # apqc_pcf, scor, etom, isa95, bpmi, custom

    # Standard details
    standard_body = db.Column(db.String(100))  # APQC, Supply Chain Council, ISA, etc.
    version = db.Column(db.String(50))
    publication_date = db.Column(db.Date)

    # Framework scope
    domain_coverage = db.Column(db.Text)  # JSON array of covered domains
    process_levels = db.Column(db.Integer)  # Number of hierarchical levels (e.g., 1 - 6 for APQC)
    total_processes = db.Column(db.Integer)  # Total number of processes in framework

    # Framework content
    process_hierarchy = db.Column(db.Text)  # JSON tree of process hierarchy
    capability_mapping = db.Column(db.Text)  # JSON mapping to capabilities
    kpi_library = db.Column(db.Text)  # JSON array of KPIs
    best_practices = db.Column(db.Text)  # JSON array of best practices
    reference_models = db.Column(db.Text)  # JSON array of reference models

    # Organizational adoption
    adoption_status = db.Column(
        db.String(30), default="not_adopted"
    )  # not_adopted, evaluating, adopted, customized
    adoption_date = db.Column(db.Date)
    adoption_scope = db.Column(db.String(100))  # Which parts of org use this?
    customization_level = db.Column(db.String(20))  # none, minor, moderate, extensive
    customization_details = db.Column(db.Text)

    # Usage metrics
    processes_implemented = db.Column(db.Integer)
    implementation_percentage = db.Column(db.Float)  # % of framework implemented
    maturity_score = db.Column(db.Integer)  # 1 - 5 maturity of implementation

    # Integration
    integrated_with_apqc = db.Column(db.Boolean, default=False)
    integrated_with_archimate = db.Column(db.Boolean, default=False)
    integration_mappings = db.Column(db.Text)  # JSON array of integration mappings

    # Governance
    framework_owner = db.Column(db.String(100))
    process_owner = db.Column(db.String(100))
    is_mandatory = db.Column(db.Boolean, default=False)
    review_frequency = db.Column(db.String(20))  # quarterly, annual, biennial
    next_review_date = db.Column(db.Date)

    # Status
    is_active = db.Column(db.Boolean, default=True)
    status_notes = db.Column(db.Text)

    # Timestamps
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @validates("maturity_score")
    def validate_maturity_score(self, key, value):
        """Ensure maturity score is between 1 and 5"""
        if value is not None:
            if value < 1 or value > 5:
                raise ValueError("Maturity score must be between 1 and 5")
        return value

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "framework_type": self.framework_type,
            "industry": self.industry,
            "category": self.category,
            "adoption_status": self.adoption_status,
            "implementation_percentage": self.implementation_percentage,
            "maturity_score": self.maturity_score,
            "is_active": self.is_active,
        }

    def __repr__(self):
        return f"<IndustryFramework {self.name} ({self.code})>"


# Event listeners
@event.listens_for(QualityFramework, "before_update")
def check_certification_expiry(mapper, connection, target):
    """Alert when certification is expiring soon"""
    if target.certification_expiry_date:
        from datetime import date, timedelta

        days_until_expiry = (target.certification_expiry_date - date.today()).days
        if days_until_expiry <= 90 and days_until_expiry > 0:
            # Could trigger a notification here
            print(f"⚠️ Certification for {target.name} expires in {days_until_expiry} days")
