"""
Capability Framework Configuration System

Unified framework with configurable extensions approach.
Provides single canonical framework with industry and organizational customization.
Supports manufacturing focus while maintaining architectural integrity.

Design Principles:
- Single canonical framework (no competing frameworks)
- Configuration-driven customization
- Industry-specific extensions
- Organizational customization
- Migration support from legacy frameworks
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from .. import db
from .mixins import HybridTenantMixin, TenantMixin

logger = logging.getLogger(__name__)


class CapabilityFrameworkConfiguration(HybridTenantMixin, db.Model):
    """
    Framework Configuration Model

    Single canonical framework with configurable extensions.
    Replaces multiple framework selection with configuration-driven customization.
    """

    __tablename__ = "capability_framework_configuration"

    id = Column(db.Integer, primary_key=True)

    # Configuration identity
    configuration_name = Column(db.String(256), nullable=False, index=True)
    configuration_description = Column(db.Text)
    configuration_code = Column(db.String(50), unique=True, index=True)  # e.g., MFG_EXCELLENCE_2024

    # Base framework (always unified)
    base_framework = Column(
        db.String(50), nullable=False, default="Unified_Manufacturing_Excellence"
    )
    framework_version = Column(db.String(20), default="1.0")

    # Configuration scope
    organization_name = Column(db.String(100))
    organization_type = Column(db.String(50))  # manufacturing, healthcare, financial, retail
    industry_focus = Column(db.String(50), default="Manufacturing")
    geographic_scope = Column(db.String(50))  # global, regional, local

    # Domain configuration
    enabled_domains = Column(db.Text)  # JSON array of enabled domain codes
    domain_customizations = Column(db.Text)  # JSON with domain-specific customizations

    # Capability hierarchy configuration
    capability_levels = Column(db.Integer, default=3)  # L1 - L3 or L1 - L4
    max_capabilities_per_level = Column(db.JSON)  # {"L1": 7, "L2": 10, "L3": 15}
    capability_naming_convention = Column(db.String(50))  # standard, custom, hybrid

    # Industry extensions
    enabled_extensions = Column(db.Text)  # JSON array of enabled extensions
    industry_specific_capabilities = Column(db.Text)  # JSON array of industry-specific capabilities
    industry_kpis = Column(db.Text)  # JSON array of industry-specific KPIs

    # Custom capabilities
    custom_capabilities = Column(db.Text)  # JSON array of custom capabilities
    custom_domains = Column(db.Text)  # JSON array of custom domains
    custom_relationships = Column(db.Text)  # JSON array of custom relationships

    # KPI configuration
    enabled_kpis = Column(db.Text)  # JSON array of enabled KPIs
    custom_kpis = Column(db.Text)  # JSON array of custom KPIs
    kpi_targets = Column(db.Text)  # JSON object with KPI targets
    kpi_benchmarks = Column(db.Text)  # JSON object with industry benchmarks

    # Value stream configuration
    enabled_value_streams = Column(db.Text)  # JSON array of enabled value streams
    custom_value_streams = Column(db.Text)  # JSON array of custom value streams
    value_stream_customizations = Column(db.Text)  # JSON with VS customizations

    # Process integration
    enabled_process_frameworks = Column(db.Text)  # JSON array: ["PCF", "APQC", "Custom"]
    process_customizations = Column(db.Text)  # JSON with process customizations

    # Governance configuration
    approval_workflows = Column(db.Text)  # JSON workflow configuration
    review_frequency = Column(
        db.String(20), default="quarterly"
    )  # monthly, quarterly, semi_annually, annual
    governance_levels = Column(db.Text)  # JSON array of governance levels
    approval_authorities = Column(db.Text)  # JSON approval matrix

    # ArchiMate configuration
    archimate_version = Column(db.String(20), default="3.2")
    enabled_viewpoints = Column(db.Text)  # JSON array of enabled viewpoints
    custom_viewpoints = Column(db.Text)  # JSON array of custom viewpoints
    modeling_standards = Column(db.Text)  # JSON array of modeling standards

    # Manufacturing specific configuration
    manufacturing_model = Column(
        db.String(50)
    )  # make_to_order, make_to_stock, engineer_to_order, mixed
    manufacturing_complexity = Column(db.String(20))  # low, medium, high, very_high
    quality_standards = Column(db.Text)  # JSON array of quality standards
    safety_requirements = Column(db.Text)  # JSON array of safety requirements

    # Integration configuration
    enabled_integrations = Column(db.Text)  # JSON array of system integrations
    integration_mappings = Column(db.Text)  # JSON integration mapping rules
    data_exchange_formats = Column(db.Text)  # JSON array of data formats

    # Status and lifecycle
    status = Column(db.String(20), default="draft")  # draft, active, deprecated, archived
    version = Column(db.String(20), default="1.0")
    effective_date = Column(db.Date)
    expiry_date = Column(db.Date)

    # Governance
    configuration_owner = Column(db.String(100))
    technical_owner = Column(db.String(100))
    business_approver = Column(db.String(100))

    # Metadata
    last_assessed = Column(db.DateTime, default=datetime.utcnow)
    confidence_level = Column(db.Integer, default=3)  # 1 - 5 confidence level
    configuration_notes = Column(db.Text)

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    framework_instances = relationship("FrameworkInstance", back_populates="configuration")
    migration_mappings = relationship(
        "FrameworkMigrationMapping", back_populates="target_configuration"
    )

    def __repr__(self):
        return f"<CapabilityFrameworkConfiguration {self.configuration_name}>"

    def get_enabled_domains(self):
        """Parse JSON enabled domains"""
        if self.enabled_domains:
            try:
                import json

                return json.loads(self.enabled_domains)
            except (ValueError, KeyError, TypeError):
                return ["CUST", "PROD", "OPER", "FIN"]  # Default domains
        return ["CUST", "PROD", "OPER", "FIN"]

    def set_enabled_domains(self, domains_list):
        """Set enabled domains as JSON"""
        import json

        self.enabled_domains = json.dumps(domains_list)

    def get_enabled_extensions(self):
        """Parse JSON enabled extensions"""
        if self.enabled_extensions:
            try:
                import json

                return json.loads(self.enabled_extensions)
            except (ValueError, KeyError, TypeError):
                return ["manufacturing"]  # Default extension
        return ["manufacturing"]

    def set_enabled_extensions(self, extensions_list):
        """Set enabled extensions as JSON"""
        import json

        self.enabled_extensions = json.dumps(extensions_list)

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "configuration_name": self.configuration_name,
            "configuration_description": self.configuration_description,
            "configuration_code": self.configuration_code,
            "base_framework": self.base_framework,
            "framework_version": self.framework_version,
            "organization_name": self.organization_name,
            "organization_type": self.organization_type,
            "industry_focus": self.industry_focus,
            "geographic_scope": self.geographic_scope,
            "capability_levels": self.capability_levels,
            "capability_naming_convention": self.capability_naming_convention,
            "manufacturing_model": self.manufacturing_model,
            "manufacturing_complexity": self.manufacturing_complexity,
            "review_frequency": self.review_frequency,
            "archimate_version": self.archimate_version,
            "status": self.status,
            "version": self.version,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "configuration_owner": self.configuration_owner,
            "technical_owner": self.technical_owner,
            "business_approver": self.business_approver,
            "enabled_domains": self.get_enabled_domains(),
            "enabled_extensions": self.get_enabled_extensions(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class FrameworkInstance(TenantMixin, db.Model):
    """
    Framework Instance Model

    Active instance of a framework configuration.
    Tracks actual implementation and usage.

    Unlike its siblings in this file, an instance records one organisation's
    own adoption (implementation team, adoption rate, maturity) of a shared
    configuration -- real per-organisation data, not a reusable template, so
    it is fenced (TenantMixin) rather than left shared (HybridTenantMixin).
    """

    __tablename__ = "framework_instances"

    id = Column(db.Integer, primary_key=True)

    # Instance identity
    instance_name = Column(db.String(256), nullable=False, index=True)
    instance_description = Column(db.Text)

    # Link to configuration
    configuration_id = Column(
        db.Integer, db.ForeignKey("capability_framework_configuration.id"), nullable=False
    )

    # Instance context
    organization_unit = Column(db.String(100))  # Business unit, department, etc.
    geographic_location = Column(db.String(100))
    implementation_scope = Column(db.String(50))  # enterprise, division, department, team

    # Implementation details
    implementation_date = Column(db.Date)
    implementation_team = Column(db.String(100))
    implementation_methodology = Column(db.String(50))  # big_bang, phased, pilot

    # Current state
    current_maturity_level = Column(db.Integer, default=1)  # 1 - 5 maturity
    capabilities_implemented = Column(db.Integer, default=0)
    capabilities_total = Column(db.Integer, default=0)
    implementation_percentage = Column(db.Float, default=0.0)

    # Performance metrics
    user_adoption_rate = Column(db.Float)  # Percentage of users actively using
    data_quality_score = Column(db.Float)  # Data quality score 0 - 100
    compliance_score = Column(db.Float)  # Compliance score 0 - 100

    # Usage statistics
    active_users = Column(db.Integer, default=0)
    daily_transactions = Column(db.Integer, default=0)
    system_uptime = Column(db.Float)  # System uptime percentage

    # Issues and improvements
    open_issues = Column(db.Integer, default=0)
    critical_issues = Column(db.Integer, default=0)
    improvement_initiatives = Column(db.Text)  # JSON array of improvement initiatives

    # Status and lifecycle
    status = Column(
        db.String(20), default="implementing"
    )  # planning, implementing, operational, optimizing, retiring
    last_health_check = Column(db.DateTime, default=datetime.utcnow)
    next_review_date = Column(db.Date)

    # Governance
    instance_owner = Column(db.String(100))
    technical_contact = Column(db.String(100))
    business_sponsor = Column(db.String(100))

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    configuration = relationship(
        "CapabilityFrameworkConfiguration", back_populates="framework_instances"
    )

    def __repr__(self):
        return f"<FrameworkInstance {self.instance_name}>"


class FrameworkExtension(HybridTenantMixin, db.Model):
    """
    Framework Extension Model

    Available extensions for the unified framework.
    Supports industry-specific and organizational extensions.
    """

    __tablename__ = "framework_extensions"

    id = Column(db.Integer, primary_key=True)

    # Extension identity
    extension_name = Column(db.String(256), nullable=False, index=True)
    extension_description = Column(db.Text)
    extension_code = Column(
        db.String(50), unique=True, index=True
    )  # e.g., MFG_ADVANCED, HEALTHCARE_BASE

    # Extension classification
    extension_type = Column(db.String(50))  # industry, organizational, technology, process
    extension_category = Column(db.String(50))  # manufacturing, healthcare, financial, retail
    extension_version = Column(db.String(20), default="1.0")

    # Extension scope
    target_framework = Column(db.String(50), default="Unified_Manufacturing_Excellence")
    compatible_versions = Column(db.Text)  # JSON array of compatible framework versions

    # Extension content
    additional_domains = Column(db.Text)  # JSON array of additional domains
    additional_capabilities = Column(db.Text)  # JSON array of additional capabilities
    additional_kpis = Column(db.Text)  # JSON array of additional KPIs
    additional_value_streams = Column(db.Text)  # JSON array of additional value streams

    # Extension features
    custom_workflows = Column(db.Text)  # JSON array of custom workflows
    custom_viewpoints = Column(db.Text)  # JSON array of custom viewpoints
    custom_integrations = Column(db.Text)  # JSON array of custom integrations

    # Requirements and dependencies
    prerequisites = Column(db.Text)  # JSON array of prerequisites
    dependencies = Column(db.Text)  # JSON array of dependencies
    resource_requirements = Column(db.Text)  # JSON array of resource requirements

    # Extension metadata
    provider = Column(db.String(100))  # Extension provider
    license_type = Column(db.String(30))  # open_source, commercial, custom
    support_level = Column(db.String(30))  # basic, standard, premium, custom

    # Status and lifecycle
    status = Column(db.String(20), default="active")  # active, deprecated, archived
    release_date = Column(db.Date)
    last_updated = Column(db.Date)
    expiry_date = Column(db.Date)

    # Quality and compliance
    quality_score = Column(db.Integer, default=3)  # 1 - 5 quality score
    compliance_status = Column(
        db.String(20), default="compliant"
    )  # compliant, non_compliant, pending
    testing_coverage = Column(db.Integer, default=0)  # Percentage test coverage

    # Usage statistics
    download_count = Column(db.Integer, default=0)
    active_installations = Column(db.Integer, default=0)
    user_rating = Column(db.Float)  # Average user rating 1 - 5

    # Documentation
    documentation_url = Column(db.String(255))
    release_notes = Column(db.Text)
    known_issues = Column(db.Text)

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<FrameworkExtension {self.extension_name}>"


class FrameworkMigrationMapping(HybridTenantMixin, db.Model):
    """
    Framework Migration Mapping

    Maps from legacy frameworks to unified framework configuration.
    Supports migration from existing capability frameworks.
    """

    __tablename__ = "framework_migration_mappings"

    id = Column(db.Integer, primary_key=True)

    # Migration identity
    migration_name = Column(db.String(256), nullable=False, index=True)
    migration_description = Column(db.Text)
    migration_code = Column(db.String(50), unique=True, index=True)

    # Source framework
    source_framework_name = Column(db.String(100))  # Name of source framework
    source_framework_version = Column(db.String(20))
    source_framework_type = Column(db.String(50))  # custom, industry_standard, vendor_specific

    # Target configuration
    target_configuration_id = Column(
        db.Integer, db.ForeignKey("capability_framework_configuration.id"), nullable=False
    )

    # Mapping rules
    domain_mappings = Column(db.Text)  # JSON object mapping source domains to target domains
    capability_mappings = Column(db.Text)  # JSON array of capability mappings
    kpi_mappings = Column(db.Text)  # JSON array of KPI mappings
    relationship_mappings = Column(db.Text)  # JSON array of relationship mappings

    # Migration strategy
    migration_approach = Column(db.String(30))  # manual, automated, hybrid
    migration_phases = Column(db.Text)  # JSON array of migration phases
    rollback_strategy = Column(db.Text)  # JSON rollback strategy

    # Data transformation
    data_transformation_rules = Column(db.Text)  # JSON array of transformation rules
    validation_rules = Column(db.Text)  # JSON array of validation rules
    quality_assurance_checks = Column(db.Text)  # JSON array of QA checks

    # Migration timeline
    planned_start_date = Column(db.Date)
    planned_end_date = Column(db.Date)
    actual_start_date = Column(db.Date)
    actual_end_date = Column(db.Date)

    # Migration resources
    required_resources = Column(db.Text)  # JSON array of required resources
    skill_requirements = Column(db.Text)  # JSON array of skill requirements
    tool_requirements = Column(db.Text)  # JSON array of tool requirements

    # Risk assessment
    migration_risks = Column(db.Text)  # JSON array of migration risks
    mitigation_strategies = Column(db.Text)  # JSON array of mitigation strategies
    contingency_plans = Column(db.Text)  # JSON array of contingency plans

    # Migration metrics
    total_items_to_migrate = Column(db.Integer, default=0)
    items_migrated = Column(db.Integer, default=0)
    migration_percentage = Column(db.Float, default=0.0)
    migration_errors = Column(db.Integer, default=0)
    data_quality_score = Column(db.Float)  # Post-migration data quality score

    # Status and lifecycle
    status = Column(
        db.String(20), default="planned"
    )  # planned, in_progress, completed, failed, cancelled
    last_progress_update = Column(db.DateTime, default=datetime.utcnow)

    # Governance
    migration_owner = Column(db.String(100))
    technical_lead = Column(db.String(100))
    business_sponsor = Column(db.String(100))

    # Validation results
    validation_passed = Column(db.Boolean, default=False)
    validation_errors = Column(db.Text)  # JSON array of validation errors
    user_acceptance_test_results = Column(db.Text)  # JSON UAT results

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    target_configuration = relationship(
        "CapabilityFrameworkConfiguration", back_populates="migration_mappings"
    )

    def __repr__(self):
        return f"<FrameworkMigrationMapping {self.migration_name}>"


class FrameworkConfigurationTemplate(HybridTenantMixin, db.Model):
    """
    Framework Configuration Template

    Pre-defined templates for common configurations.
    Accelerates framework deployment for standard scenarios.
    """

    __tablename__ = "framework_configuration_templates"

    id = Column(db.Integer, primary_key=True)

    # Template identity
    template_name = Column(db.String(256), nullable=False, index=True)
    template_description = Column(db.Text)
    template_code = Column(
        db.String(50), unique=True, index=True
    )  # e.g., MFG_SME, HEALTHCARE_HOSPITAL

    # Template classification
    template_type = Column(db.String(50))  # industry, organization_size, complexity
    template_category = Column(db.String(50))  # manufacturing, healthcare, financial, retail
    organization_size = Column(db.String(30))  # small, medium, large, enterprise

    # Template scope
    target_industries = Column(db.Text)  # JSON array of target industries
    target_organization_types = Column(db.Text)  # JSON array of target org types
    complexity_level = Column(db.String(20))  # simple, moderate, complex, very_complex

    # Template configuration (JSON)
    template_configuration = Column(db.Text)  # Complete configuration as JSON
    default_domains = Column(db.Text)  # JSON array of default domains
    default_extensions = Column(db.Text)  # JSON array of default extensions
    default_capabilities = Column(db.Text)  # JSON array of default capabilities

    # Template features
    preconfigured_kpis = Column(db.Text)  # JSON array of preconfigured KPIs
    preconfigured_workflows = Column(db.Text)  # JSON array of preconfigured workflows
    preconfigured_viewpoints = Column(db.Text)  # JSON array of preconfigured viewpoints

    # Template metadata
    provider = Column(db.String(100))  # Template provider
    version = Column(db.String(20), default="1.0")
    last_updated = Column(db.Date)

    # Usage statistics
    usage_count = Column(db.Integer, default=0)
    success_rate = Column(db.Float)  # Success rate percentage
    average_implementation_time = Column(db.Integer)  # Days

    # Quality assessment
    quality_score = Column(db.Integer, default=3)  # 1 - 5 quality score
    user_ratings = Column(db.Text)  # JSON array of user ratings
    feedback_summary = Column(db.Text)

    # Status and lifecycle
    status = Column(db.String(20), default="active")  # active, deprecated, archived
    certification_status = Column(db.String(20))  # certified, pending, not_certified

    # Documentation
    implementation_guide = Column(db.Text)
    best_practices = Column(db.Text)
    common_issues = Column(db.Text)

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<FrameworkConfigurationTemplate {self.template_name}>"


class FrameworkValidationRule(HybridTenantMixin, db.Model):
    """
    Framework Validation Rule

    Validation rules for framework configurations.
    Ensures configuration integrity and compliance.
    """

    __tablename__ = "framework_validation_rules"

    id = Column(db.Integer, primary_key=True)

    # Rule identity
    rule_name = Column(db.String(256), nullable=False, index=True)
    rule_description = Column(db.Text)
    rule_code = Column(db.String(50), unique=True, index=True)

    # Rule classification
    rule_category = Column(db.String(50))  # syntax, semantic, completeness, consistency, compliance
    rule_type = Column(db.String(30))  # validation, warning, recommendation, error
    severity_level = Column(db.String(20))  # critical, high, medium, low

    # Rule scope
    applicable_configurations = Column(db.Text)  # JSON array of applicable configuration types
    applicable_extensions = Column(db.Text)  # JSON array of applicable extensions
    applicable_industries = Column(db.Text)  # JSON array of applicable industries

    # Validation logic
    validation_conditions = Column(db.Text)  # JSON array of validation conditions
    success_criteria = Column(db.Text)  # JSON array of success criteria
    failure_conditions = Column(db.Text)  # JSON array of failure conditions

    # Rule implementation
    validation_script = Column(db.Text)  # Validation script or logic
    error_messages = Column(db.Text)  # JSON array of error messages
    correction_suggestions = Column(db.Text)  # JSON array of correction suggestions

    # Rule metadata
    rule_version = Column(db.String(20), default="1.0")
    last_updated = Column(db.Date)

    # Status
    status = Column(db.String(20), default="active")  # active, inactive, deprecated

    # Usage statistics
    execution_count = Column(db.Integer, default=0)
    failure_rate = Column(db.Float)  # Failure rate percentage

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<FrameworkValidationRule {self.rule_name}>"
