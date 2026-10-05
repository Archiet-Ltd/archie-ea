"""
Business Capability Models for Capability-Driven MDD

This module implements a comprehensive business capability framework that:
1. Extends the existing EnterpriseCapability model
2. Adds capability-driven requirement generation
3. Provides maturity assessment and gap analysis
4. Enables automatic requirements derivation from capabilities

Integration with existing models:
- Links to EnterpriseCapability (from capabilities.py)
- Links to ArchiMateElement (from models.py)
- Links to TechnologyStack (from models.py)
- Links to SalesforceObject (from salesforce.py)
"""

import json
from datetime import datetime

from sqlalchemy import event

from app.datetime_helpers import utcnow

from .. import db
from .mixins import TenantMixin
from .relationship_tables import capability_compliance_requirements


class BusinessCapability(TenantMixin, db.Model):
    """
    Business Capability (WHAT the business does).

    Capabilities are stable, business-focused expressions of what an organization does.
    They are independent of how the capability is implemented (process, system, people).
    Requirements are DERIVED from capabilities, not invented.
    """

    __tablename__ = "business_capability"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)

    # Capability identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)
    code = db.Column(db.String(50), unique=True, index=True)  # CAP - 001

    # Capability classification
    level = db.Column(
        db.Integer, nullable=False, default=1
    )  # 1=Strategic, 2=Core, 3=Supporting, 4=Operational, 5=Detailed
    category = db.Column(db.String(100))  # Customer, Product, Operations, etc.
    business_domain = db.Column(db.String(100))

    # Hierarchy
    parent_capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id")
    )

    # Capability maturity
    # No default: an unassessed capability must read as NULL -> "—", not as a
    # measured Level 1. These defaulted to 1 and 3, which put a plausible score
    # on every capability nobody had assessed — indistinguishable from a real
    # assessment, and the exact failure CLAUDE.md's "never invent data" rule
    # exists to prevent. maturity_assessment_date is what says "this is real".
    current_maturity_level = db.Column(db.Integer, nullable=True)  # 1 - 5 scale
    target_maturity_level = db.Column(db.Integer, nullable=True)  # 1 - 5 scale
    maturity_gap = db.Column(db.Integer)  # Calculated gap between current and target
    maturity_assessment_date = db.Column(db.DateTime)
    maturity_assessment_notes = db.Column(db.Text)

    # Strategic importance
    strategic_importance = db.Column(db.String(20))  # critical, high, medium, low
    business_value = db.Column(db.Integer)  # 1 - 10 scale
    roi_score = db.Column(db.Float)

    # Ownership and governance
    business_owner = db.Column(db.String(100))
    it_owner = db.Column(db.String(100))
    governance_model = db.Column(db.String(50))  # centralized, federated, hybrid

    # Performance metrics
    kpis = db.Column(db.Text)  # JSON array of KPI definitions
    performance_score = db.Column(db.Float)  # Overall performance score
    last_performance_review = db.Column(db.DateTime)

    # Integration with existing models
    canonical_capability_id = db.Column(
        db.Integer, db.ForeignKey("capabilities.id", ondelete="SET NULL")
    )
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    archimate_id = db.Column(
        db.String(255), unique=True, index=True
    )  # For Abacus EEID storage

    # Discovery metadata
    discovered_by_ai = db.Column(db.Boolean, default=False)
    discovery_confidence = db.Column(db.Float)
    discovery_source = db.Column(db.String(100))  # manual, ai, imported

    # Deprecation marker (for backward compatibility path)
    is_deprecated = db.Column(db.Boolean, default=False, index=True)
    deprecated_as_of = db.Column(db.DateTime)
    deprecated_in_favor_of_id = db.Column(
        db.Integer, db.ForeignKey("unified_capabilities.id", ondelete="SET NULL")
    )
    deprecation_notes = db.Column(db.Text)

    # Timestamps
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    canonical_capability = db.relationship(
        "Capability", back_populates="business_capability", uselist=False
    )
    archimate_element = db.relationship(
        "ArchiMateElement", foreign_keys=[archimate_element_id], post_update=True
    )

    # Vendor risk relationships
    # NOTE: back_populates removed — VendorOrganization.capability_risks is commented out
    # to avoid mapper initialization crash (backref="capability_risks" also exists on
    # RiskAssessment -> BusinessCapability in models.py:2066).
    vendor_risks = db.relationship(
        "VendorOrganization",
        secondary="vendor_capability_risks",
        lazy="dynamic",
        overlaps="capability_risks",
    )

    # Deprecation relationship (for migration path)
    deprecated_in_favor_of = db.relationship(
        "UnifiedCapability", foreign_keys=[deprecated_in_favor_of_id], post_update=True
    )

    # Enterprise initiative relationships
    # NOTE: This relationship is commented out due to SQLAlchemy mapper configuration issues
    # The initiative_capabilities table has multiple foreign key paths that SQLAlchemy
    # cannot automatically resolve. For now, use direct queries instead.
    # transformation_initiatives = db.relationship('EnterpriseInitiative',
    #                                            secondary='initiative_capabilities',
    #                                            back_populates='target_capabilities')

    # Compliance relationships
    compliance_requirements = db.relationship(
        "ComplianceRequirement",
        secondary="capability_compliance_requirements",
        back_populates="capabilities",
    )

    # Process relationships
    supporting_processes = db.relationship(
        "BusinessProcess",
        secondary="process_capability_mapping",
        back_populates="supporting_capabilities",
    )

    # ADR (Architecture Decision Record) relationships
    decision_links = db.relationship(
        "ADRCapabilityLink", back_populates="capability", cascade="all, delete-orphan"
    )

    # NOTE: BusinessCapability does not have a direct relationship to ApplicationComponent
    # The app_business_mapping table should be used for this relationship

    def __repr__(self):
        return f"<BusinessCapability {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "code": self.code,
            "level": self.level,
            "category": self.category,
            "business_domain": self.business_domain,
            "current_maturity_level": self.current_maturity_level,
            "target_maturity_level": self.target_maturity_level,
            "strategic_importance": self.strategic_importance,
            "business_value": self.business_value,
            "business_owner": self.business_owner,
            "it_owner": self.it_owner,
            "performance_score": self.performance_score,
            "discovered_by_ai": self.discovered_by_ai,
            "archimate_element_id": self.archimate_element_id,
            "archimate_id": self.archimate_id,
            "parent_capability_id": self.parent_capability_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Capability(TenantMixin, db.Model):
    """Canonical ArchiMate strategy capability registry entry."""

    __tablename__ = "capabilities"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)
    level = db.Column(db.Integer, nullable=False, default=1)
    archimate_id = db.Column(db.String(64), unique=True, index=True)
    archimate_layer = db.Column(db.String(32), nullable=False, default="strategy")
    parent_capability_id = db.Column(
        db.Integer, db.ForeignKey("capabilities.id", ondelete="SET NULL")
    )
    source_type = db.Column(
        db.String(64), nullable=False, default="business_capability"
    )
    source_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    parent_capability = db.relationship(
        "Capability", remote_side=[id], backref="children"
    )
    business_capability = db.relationship(
        "BusinessCapability", back_populates="canonical_capability", uselist=False
    )

    def __repr__(self):
        return f"<Capability {self.name}>"


class BusinessFunction(TenantMixin, db.Model):
    """
    Business Function (HOW capability is performed).

    Functions are the decomposition of capabilities into specific activities/processes.
    Requirements are GENERATED from functions, not invented.
    """

    __tablename__ = "business_function"

    id = db.Column(db.Integer, primary_key=True)
    capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id"), nullable=False
    )

    # Function identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)
    function_type = db.Column(db.String(50))  # process, service, activity, decision

    # Function characteristics
    is_automated = db.Column(db.Boolean, default=False)
    automation_level = db.Column(db.Integer, default=0)  # 0 - 100%
    automation_potential = db.Column(db.Integer)  # Potential for automation (0 - 100%)

    # Performance metrics
    volume_per_day = db.Column(db.Integer)
    average_duration_seconds = db.Column(db.Integer)
    error_rate_percent = db.Column(db.Float)
    cost_per_execution = db.Column(db.Float)

    # Inputs/Outputs (Data Objects)
    inputs = db.Column(db.Text)  # JSON array
    outputs = db.Column(db.Text)  # JSON array

    # Supporting systems
    primary_application = db.Column(db.String(256))
    supporting_applications = db.Column(db.Text)  # JSON array

    # Integration with existing models
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Discovery metadata
    discovered_by_ai = db.Column(db.Boolean, default=True)
    decomposition_confidence = db.Column(db.Float)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    capability = db.relationship("BusinessCapability", backref="functions")

    def __repr__(self):
        return f"<BusinessFunction {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "function_type": self.function_type,
            "is_automated": self.is_automated,
            "automation_level": self.automation_level,
            "primary_application": self.primary_application,
            "inputs": json.loads(self.inputs) if self.inputs else [],
            "outputs": json.loads(self.outputs) if self.outputs else [],
        }


class FunctionalRequirement(db.Model):
    """
    Functional Requirement GENERATED from business function.

    Requirements are DERIVED, not invented.
    Fully traceable back to capability -> function -> requirement.
    """

    __tablename__ = "functional_requirement"

    id = db.Column(db.Integer, primary_key=True)
    function_id = db.Column(
        db.Integer, db.ForeignKey("business_function.id"), nullable=False
    )
    capability_id = db.Column(db.Integer, db.ForeignKey("business_capability.id"))

    # Requirement identity
    requirement_id = db.Column(
        db.String(50), unique=True, index=True
    )  # CAP - 001 - FUN - 002 - REQ - 003
    title = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text)
    user_story = db.Column(db.Text)  # "As a [role], I need to [action] so that [value]"

    # Requirement classification
    requirement_type = db.Column(
        db.String(50)
    )  # functional, data, integration, ui, business_rule
    priority = db.Column(db.String(20))  # must, should, could, won't (MoSCoW)

    # Acceptance criteria
    acceptance_criteria = db.Column(db.Text)  # JSON array
    test_scenarios = db.Column(db.Text)  # JSON array

    # Generation metadata
    generated_by_ai = db.Column(db.Boolean, default=True)
    generation_confidence = db.Column(db.Float)
    generation_rationale = db.Column(db.Text)
    generation_date = db.Column(db.DateTime, default=datetime.utcnow)

    # Implementation tracking
    implementation_status = db.Column(
        db.String(50)
    )  # pending, in_progress, implemented, verified
    estimated_effort_hours = db.Column(db.Integer)
    actual_effort_hours = db.Column(db.Integer)

    # Integration with existing models
    jira_issue_key = db.Column(db.String(50))
    jira_issue_id = db.Column(db.Integer, db.ForeignKey("jira_issues.id"))
    code_artifact_id = db.Column(db.Integer, db.ForeignKey("code_artifacts.id"))
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id")
    )  # Link to ArchiMate Requirement element

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    function = db.relationship(
        "BusinessFunction",
        foreign_keys=[function_id],
        backref="functional_requirements",
    )
    capability = db.relationship(
        "BusinessCapability",
        foreign_keys=[capability_id],
        overlaps="functional_requirements",
    )
    # Import ArchiMateElement here to avoid circular imports
    archimate_element = db.relationship(
        "ArchiMateElement", foreign_keys=[archimate_element_id], post_update=True
    )

    def __repr__(self):
        return f"<FunctionalRequirement {self.requirement_id}: {self.title}>"

    def to_dict(self):
        return {
            "id": self.id,
            "requirement_id": self.requirement_id,
            "title": self.title,
            "description": self.description,
            "user_story": self.user_story,
            "requirement_type": self.requirement_type,
            "priority": self.priority,
            "acceptance_criteria": json.loads(self.acceptance_criteria)
            if self.acceptance_criteria
            else [],
            "implementation_status": self.implementation_status,
            "generated_by_ai": self.generated_by_ai,
            "generation_confidence": self.generation_confidence,
            "capability": self.capability.name if self.capability else None,
            "function": self.function.name if self.function else None,
        }


class NonFunctionalRequirement(db.Model):
    """
    Non-Functional Requirement GENERATED from capability maturity target.

    NFRs are DERIVED from maturity level, not guessed.
    Based on ISO 25010 quality model.
    """

    __tablename__ = "non_functional_requirement"

    id = db.Column(db.Integer, primary_key=True)
    capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id"), nullable=False
    )

    # NFR identity
    requirement_id = db.Column(
        db.String(50), unique=True, index=True
    )  # NFR-CAP - 001 - PERF - 001
    title = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text)

    # NFR category (ISO 25010)
    nfr_category = db.Column(
        db.String(50)
    )  # performance, scalability, security, availability, usability
    nfr_subcategory = db.Column(
        db.String(50)
    )  # response_time, throughput, encryption, etc.

    # Maturity-driven
    required_for_maturity_level = db.Column(
        db.Integer
    )  # Which maturity level requires this NFR

    # Specific metrics (measurable!)
    metric_name = db.Column(db.String(100))
    metric_value = db.Column(db.String(100))
    metric_unit = db.Column(db.String(50))
    metric_measurement_method = db.Column(db.Text)

    # Acceptance criteria
    acceptance_criteria = db.Column(db.Text)  # JSON array
    test_approach = db.Column(db.Text)

    # Generation metadata
    generated_by_ai = db.Column(db.Boolean, default=True)
    generation_rationale = db.Column(db.Text)
    generation_date = db.Column(db.DateTime, default=datetime.utcnow)

    # Implementation tracking
    implementation_status = db.Column(db.String(50))
    validation_status = db.Column(db.String(50))  # not_tested, passed, failed
    last_test_date = db.Column(db.DateTime)
    last_test_result = db.Column(db.Text)  # JSON object

    # Integration with existing models
    validation_gate_id = db.Column(db.Integer, db.ForeignKey("validation_gates.id"))
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id")
    )  # Link to ArchiMate Requirement element

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    capability = db.relationship(
        "BusinessCapability",
        foreign_keys=[capability_id],
        overlaps="nfrs",
    )
    # Import ArchiMateElement here to avoid circular imports
    archimate_element = db.relationship(
        "ArchiMateElement", foreign_keys=[archimate_element_id], post_update=True
    )

    def __repr__(self):
        return f"<NonFunctionalRequirement {self.requirement_id}: {self.title}>"

    def to_dict(self):
        return {
            "id": self.id,
            "requirement_id": self.requirement_id,
            "title": self.title,
            "description": self.description,
            "nfr_category": self.nfr_category,
            "nfr_subcategory": self.nfr_subcategory,
            "required_for_maturity_level": self.required_for_maturity_level,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "metric_unit": self.metric_unit,
            "implementation_status": self.implementation_status,
            "validation_status": self.validation_status,
            "capability": self.capability.name if self.capability else None,
        }


class ApplicationCapabilityCoverage(TenantMixin, db.Model):
    """
    Tracks which applications support which business capabilities.

    This mapping table connects ApplicationComponent to BusinessCapability,
    providing coverage analysis and capability-driven application management.
    """

    __tablename__ = "application_capability_coverage"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)

    # Foreign keys
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False
    )
    capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id"), nullable=False
    )

    # Coverage details
    support_level = db.Column(
        db.String(20), default="partial"
    )  # full, partial, minimal
    coverage_percentage = db.Column(db.Float, default=0.0)  # 0 - 100%
    confidence_score = db.Column(db.Float, default=0.5)  # 0 - 1

    # Business context
    is_strategic = db.Column(db.Boolean, default=False)
    investment_priority = db.Column(
        db.String(20), default="medium"
    )  # critical, high, medium, low

    # Metadata
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    capability = db.relationship(
        "BusinessCapability", backref="application_coverage_mappings"
    )

    def __repr__(self):
        return f"<ApplicationCapabilityCoverage {self.capability.name}>"

    def to_dict(self):
        """Convert coverage to dictionary for API responses."""
        return {
            "id": self.id,
            "application_component_id": self.application_component_id,
            "capability_id": self.capability_id,
            "support_level": self.support_level,
            "coverage_percentage": self.coverage_percentage,
            "confidence_score": self.confidence_score,
            "is_strategic": self.is_strategic,
            "investment_priority": self.investment_priority,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "capability_name": self.capability.name if self.capability else None,
        }


# ============================================================================
# SQLAlchemy Event Listeners - Auto-create ArchiMateElements
# ============================================================================


@event.listens_for(BusinessCapability, "before_insert")
def create_capability_archimate_element(mapper, connection, target):
    """Automatically create ArchiMateElement when BusinessCapability is created."""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        # BusinessCapability.name is String(256) but ArchiMateElement.name is
        # String(100); an over-long capability name would raise
        # StringDataRightTruncation here and 500 the create. Truncate the mirrored
        # name (the element IS the field — it must never fail to be created because
        # the field was long); the full name still lives on the capability row.
        full_name = target.name or ""
        element_name = full_name if len(full_name) <= 100 else full_name[:99] + "…"
        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                name=element_name,
                type="Capability",
                layer="Strategy",
                description=target.description or f"Business capability: {full_name}",
                organization_id=target.organization_id,
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]
