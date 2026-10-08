"""
Capability Framework Models for COBIT+ITIL+ArchiMate Integration
"""

import json
import logging
from datetime import datetime

from .. import db
from .mixins import TenantMixin

logger = logging.getLogger(__name__)


class COBITDomain(db.Model):
    """COBIT 2019 Governance and Management Domains"""

    __tablename__ = "cobit_domains"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)  # EDM, APO, BAI, DSS, MEA
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    domain_type = db.Column(db.String(20), nullable=False)  # 'governance' or 'management'

    # Relationships
    processes = db.relationship("COBITProcess", backref="domain", lazy="dynamic")

    def __repr__(self):
        return f"<COBITDomain {self.code}: {self.name}>"


class COBITProcess(db.Model):
    """COBIT 2019 Processes within each domain"""

    __tablename__ = "cobit_processes"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)  # EDM01, APO01, etc.
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    purpose = db.Column(db.Text)

    # Foreign Keys
    domain_id = db.Column(db.Integer, db.ForeignKey("cobit_domains.id"), nullable=False)

    # Relationships
    capabilities = db.relationship(
        "EnterpriseCapability",
        secondary="cobit_capability_mapping",
        back_populates="cobit_processes",
        overlaps="capability_mappings,cobit_mappings,capability,cobit_process",
    )

    def __repr__(self):
        return f"<COBITProcess {self.code}: {self.name}>"


class ITILPractice(db.Model):
    """ITIL 4 Service Management Practices"""

    __tablename__ = "itil_practices"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    practice_type = db.Column(db.String(30), nullable=False)  # general, service, technical
    purpose = db.Column(db.Text)
    key_activities = db.Column(db.Text)  # JSON array of activities

    # Relationships
    capabilities = db.relationship(
        "EnterpriseCapability",
        secondary="itil_capability_mapping",
        back_populates="itil_practices",
        overlaps="capability_mappings,itil_mappings,capability,itil_practice",
    )

    def __repr__(self):
        return f"<ITILPractice {self.code}: {self.name}>"


class ArchiMateCapability(db.Model):
    """
    ArchiMate Strategy Layer Capabilities

    DEPRECATED: This model is maintained for backward compatibility only.
    New implementations should use BusinessCapability model directly.

    Design Decision: BusinessCapability is the single source of truth.
    ArchiMateCapability now serves as a legacy mapping/view layer that
    references BusinessCapability records.

    Migration Path:
    1. Existing ArchiMateCapability records link to BusinessCapability via business_capability_id
    2. New capability modeling uses BusinessCapability with archimate_id field
    3. This model can be phased out once all references are migrated
    """

    __tablename__ = "archimate_capabilities"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    capability_level = db.Column(db.String(20))  # level - 0, level - 1, level - 2, level - 3
    parent_id = db.Column(db.Integer, db.ForeignKey("archimate_capabilities.id"))

    # ArchiMate specific properties
    archimate_id = db.Column(db.String(50), unique=True)  # UUID from ArchiMate models
    layer = db.Column(db.String(20), default="strategy")
    element_type = db.Column(db.String(30), default="capability")

    # Strategic Alignment Fields (for solution architects)
    strategic_importance = db.Column(db.String(20))  # critical, high, medium, low
    investment_priority = db.Column(db.Integer)  # 1 - 10 ranking
    strategic_objective = db.Column(db.Text)  # Alignment with business objectives
    # T-002 (ADR 0008 rule 3): superseded by the single maturity authority,
    # `UnifiedCapability.current_maturity_level` / `.target_maturity_level`
    # (projected from `business_capability`, not from this table). Not the
    # current-value read path — read `UnifiedCapability.maturity_for_source`
    # or `.maturity_for_sources` instead. Retained, not dropped: this model
    # is a separate ArchiMate-framework capability catalogue, not itself a
    # copy with declared provenance.
    target_maturity = db.Column(db.Integer)  # Target maturity level (1 - 5)
    current_maturity = db.Column(db.Integer)  # Current maturity level (1 - 5)
    maturity_gap = db.Column(db.Integer)  # Calculated gap

    # Dependency Mapping Fields
    depends_on = db.Column(db.Text)  # JSON array of capability IDs this depends on
    enables = db.Column(db.Text)  # JSON array of capability IDs this enables
    criticality_score = db.Column(db.Float)  # 0 - 100 criticality score
    dependency_complexity = db.Column(db.String(20))  # low, medium, high, critical

    # Gap Analysis Fields
    has_capability_gap = db.Column(db.Boolean, default=False)
    gap_description = db.Column(db.Text)
    gap_impact = db.Column(db.String(20))  # low, medium, high, critical
    remediation_plan = db.Column(db.Text)
    estimated_remediation_cost = db.Column(db.Float)

    # Architectural Pattern Fields (for software architects)
    reference_architecture_id = db.Column(db.Integer)  # Link to reference architecture
    design_patterns = db.Column(db.Text)  # JSON array of applicable patterns
    architectural_principles = db.Column(db.Text)  # JSON array of principles
    technology_constraints = db.Column(db.Text)  # Technical limitations/requirements

    # Quality Attributes
    performance_requirements = db.Column(db.Text)  # JSON dict of performance metrics
    scalability_requirements = db.Column(db.Text)  # JSON dict of scalability needs
    security_requirements = db.Column(db.Text)  # JSON dict of security controls
    compliance_requirements = db.Column(db.Text)  # JSON array of compliance needs

    # Integration Architecture
    integration_patterns = db.Column(db.Text)  # JSON array of integration patterns
    api_architecture = db.Column(db.Text)  # JSON dict of API design
    data_integration = db.Column(db.Text)  # JSON dict of data integration patterns

    # Governance & Standards
    governance_status = db.Column(db.String(20))  # compliant, review_needed, non_compliant
    architectural_debt = db.Column(db.Text)  # JSON array of tech debt items
    compliance_status = db.Column(db.Text)  # JSON dict of compliance statuses
    last_architecture_review = db.Column(db.DateTime)
    next_review_date = db.Column(db.DateTime)

    # NEW: Link to unified BusinessCapability (single source of truth)
    business_capability_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "business_capability.id",
            name="fk_archimate_capability_business",
            ondelete="SET NULL",
            use_alter=True,
        ),
    )

    # Set once by `flask backfill-capability-catalogs` (ADR 0008): points at
    # the `unified_capabilities` row this legacy row's capability is now
    # represented by -- the linked `business_capability`'s own projection when
    # `business_capability_id` is set, otherwise the reference-scope row
    # created (or matched by archimate_id / normalised name) for this row.
    # NULL means "not yet processed"; the backfill is idempotent on it.
    retired_into_id = db.Column(
        db.BigInteger,
        db.ForeignKey("unified_capabilities.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
        index=True,
    )

    # Relationships
    parent = db.relationship(
        "ArchiMateCapability", remote_side="ArchiMateCapability.id", backref="children"
    )
    enterprise_capabilities = db.relationship(
        "EnterpriseCapability",
        secondary="archimate_capability_mapping",
        back_populates="archimate_capabilities",
    )
    business_capability = db.relationship(
        "BusinessCapability",
        foreign_keys=[business_capability_id],
        backref="legacy_archimate_references",
    )

    def __repr__(self):
        return f"<ArchiMateCapability {self.name}>"

    def get_or_create_business_capability(self):
        """
        Get the linked BusinessCapability or create one if it doesn't exist.
        This method helps with migration from legacy ArchiMateCapability to unified model.
        """
        if self.business_capability:
            return self.business_capability

        # Create a new BusinessCapability from this ArchiMateCapability
        from app.models.business_capabilities import BusinessCapability

        bc = BusinessCapability(
            name=self.name,
            description=self.description,
            level=int(self.capability_level.split("-")[1]) if self.capability_level else 1,
            archimate_id=self.archimate_id,
            archimate_layer=self.layer,
            archimate_capability_id=self.id,
        )
        db.session.add(bc)
        db.session.flush()

        bc.ensure_canonical_capability()
        self.business_capability_id = bc.id
        db.session.commit()

        return bc


class EnterpriseCapability(db.Model):
    """
    Enterprise Capability Model linking COBIT+ITIL+ArchiMate

    DEPRECATED: This model is maintained for backward compatibility only.
    New implementations should use BusinessCapability model directly.

    Design Decision: BusinessCapability is the single source of truth.
    EnterpriseCapability now serves as a legacy governance mapping layer
    that references BusinessCapability records.

    The COBIT/ITIL governance fields (criticality, status, roadmap_priority, etc.)
    have been added directly to BusinessCapability model.

    Migration Path:
    1. Existing EnterpriseCapability records link to BusinessCapability via business_capability_id
    2. New capability modeling uses BusinessCapability with COBIT/ITIL fields directly
    3. This model maintained for existing COBIT/ITIL mapping tables
    """

    __tablename__ = "enterprise_capabilities"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(50))  # business, technical, governance, etc.
    maturity_level = db.Column(db.Integer, default=1)  # 1 - 5 CMM levels
    criticality = db.Column(db.String(20))  # critical, important, supporting

    # Business context
    business_value = db.Column(db.Text)
    success_metrics = db.Column(db.Text)  # JSON array of KPIs

    # Lifecycle management
    status = db.Column(
        db.String(30), default="defined"
    )  # defined, developing, operational, retiring
    roadmap_priority = db.Column(db.String(20))  # high, medium, low
    target_state_description = db.Column(db.Text)

    # Audit trail
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    # NEW: Link to unified BusinessCapability (single source of truth)
    business_capability_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "business_capability.id",
            name="fk_enterprise_capability_business",
            ondelete="SET NULL",
            use_alter=True,
        ),
    )

    # Set once by `flask backfill-capability-catalogs` (ADR 0008): points at
    # the `unified_capabilities` row this legacy row's capability is now
    # represented by. See ArchiMateCapability.retired_into_id for the full
    # explanation; the two tables are backfilled by the same command.
    retired_into_id = db.Column(
        db.BigInteger,
        db.ForeignKey("unified_capabilities.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
        index=True,
    )

    # Relationships - Many-to-Many associations
    cobit_processes = db.relationship(
        "COBITProcess",
        secondary="cobit_capability_mapping",
        back_populates="capabilities",
        overlaps="capability_mappings,cobit_mappings,capability,cobit_process",
    )

    itil_practices = db.relationship(
        "ITILPractice",
        secondary="itil_capability_mapping",
        back_populates="capabilities",
        overlaps="capability_mappings,itil_mappings,capability,itil_practice",
    )

    archimate_capabilities = db.relationship(
        "ArchiMateCapability",
        secondary="archimate_capability_mapping",
        back_populates="enterprise_capabilities",
    )

    technology_stacks = db.relationship(
        "TechnologyStack",
        secondary="capability_technology_mapping",
        back_populates="supported_capabilities",
    )

    # User relationship
    creator = db.relationship("User", backref="created_capabilities")

    # Link to unified BusinessCapability
    business_capability = db.relationship(
        "BusinessCapability",
        foreign_keys=[business_capability_id],
        backref="legacy_enterprise_references",
    )

    def get_success_metrics(self):
        """Parse JSON success metrics"""
        if self.success_metrics:
            try:
                return json.loads(self.success_metrics)
            except (ValueError, KeyError, TypeError):
                return []
        return []

    def set_success_metrics(self, metrics_list):
        """Set success metrics as JSON"""
        self.success_metrics = json.dumps(metrics_list)

    def get_capability_score(self):
        """Calculate overall capability maturity score"""
        # Weighted score based on COBIT, ITIL, and technology alignment
        weights = {"cobit": 0.3, "itil": 0.3, "technology": 0.4}

        # COBIT alignment score - use len() for lists
        cobit_score = len(self.cobit_processes) * 20  # Max 100 for 5 processes

        # ITIL alignment score - use len() for lists
        itil_score = len(self.itil_practices) * 25  # Max 100 for 4 practices

        # Technology enablement score - use len() for lists
        tech_score = len(self.technology_stacks) * 33  # Max 100 for 3 tech stacks

        weighted_score = (
            min(cobit_score, 100) * weights["cobit"]
            + min(itil_score, 100) * weights["itil"]
            + min(tech_score, 100) * weights["technology"]
        )

        return round(weighted_score, 2)

    def __repr__(self):
        return f"<EnterpriseCapability {self.name}>"


# Association Tables for Many-to-Many relationships


class COBITCapabilityMapping(db.Model):
    """Maps COBIT processes to Enterprise Capabilities"""

    __tablename__ = "cobit_capability_mapping"

    id = db.Column(db.Integer, primary_key=True)
    cobit_process_id = db.Column(db.Integer, db.ForeignKey("cobit_processes.id"), nullable=False)
    capability_id = db.Column(
        db.Integer, db.ForeignKey("enterprise_capabilities.id"), nullable=False
    )
    alignment_strength = db.Column(db.String(20), default="strong")  # strong, medium, weak
    notes = db.Column(db.Text)

    # Relationships with overlaps to resolve conflicts
    cobit_process = db.relationship(
        "COBITProcess",
        backref=db.backref("capability_mappings", overlaps="capabilities,cobit_processes"),
        overlaps="capabilities,cobit_processes",
    )
    capability = db.relationship(
        "EnterpriseCapability",
        backref=db.backref("cobit_mappings", overlaps="capabilities,cobit_processes"),
        overlaps="capabilities,cobit_processes",
    )

    # Unique constraint
    __table_args__ = (db.UniqueConstraint("cobit_process_id", "capability_id"),)


class ITILCapabilityMapping(db.Model):
    """Maps ITIL practices to Enterprise Capabilities"""

    __tablename__ = "itil_capability_mapping"

    id = db.Column(db.Integer, primary_key=True)
    itil_practice_id = db.Column(db.Integer, db.ForeignKey("itil_practices.id"), nullable=False)
    capability_id = db.Column(
        db.Integer, db.ForeignKey("enterprise_capabilities.id"), nullable=False
    )
    implementation_level = db.Column(db.String(20), default="full")  # full, partial, planned
    notes = db.Column(db.Text)

    # Relationships with overlaps to resolve conflicts
    itil_practice = db.relationship(
        "ITILPractice",
        backref=db.backref("capability_mappings", overlaps="capabilities,itil_practices"),
        overlaps="capabilities,itil_practices",
    )
    capability = db.relationship(
        "EnterpriseCapability",
        backref=db.backref("itil_mappings", overlaps="capabilities,itil_practices"),
        overlaps="capabilities,itil_practices",
    )

    # Unique constraint
    __table_args__ = (db.UniqueConstraint("itil_practice_id", "capability_id"),)


class ArchiMateCapabilityMapping(db.Model):
    """Maps ArchiMate capabilities to Enterprise Capabilities"""

    __tablename__ = "archimate_capability_mapping"

    id = db.Column(db.Integer, primary_key=True)
    archimate_capability_id = db.Column(
        db.Integer, db.ForeignKey("archimate_capabilities.id"), nullable=False
    )
    capability_id = db.Column(
        db.Integer, db.ForeignKey("enterprise_capabilities.id"), nullable=False
    )
    semantic_relationship = db.Column(
        db.String(30), default="realizes"
    )  # realizes, enables, supports
    notes = db.Column(db.Text)

    # Relationships with overlaps to resolve conflicts
    archimate_capability = db.relationship(
        "ArchiMateCapability",
        backref=db.backref(
            "enterprise_mappings", overlaps="archimate_capabilities,enterprise_capabilities"
        ),
        overlaps="archimate_capabilities,enterprise_capabilities",
    )
    capability = db.relationship(
        "EnterpriseCapability",
        backref=db.backref(
            "archimate_mappings", overlaps="archimate_capabilities,enterprise_capabilities"
        ),
        overlaps="archimate_capabilities,enterprise_capabilities",
    )

    # Unique constraint
    __table_args__ = (db.UniqueConstraint("archimate_capability_id", "capability_id"),)


# Import CapabilityTechnologyMapping from unified_capability to avoid duplication
from app.models.unified_capability import CapabilityTechnologyMapping  # dead-code-ok

# Create the missing capability_technology_mapping table for legacy compatibility
capability_technology_mapping = db.Table(
    "capability_technology_mapping",
    db.Column(
        "enterprise_capability_id",
        db.Integer,
        db.ForeignKey("enterprise_capabilities.id"),
        nullable=False,
    ),
    db.Column(
        "technology_stack_id", db.Integer, db.ForeignKey("technology_stacks.id"), nullable=False
    ),
    db.Column("implementation_complexity", db.String(20)),
    db.Column("integration_effort_person_days", db.Integer),
    db.Column("technical_risk", db.String(20)),
    db.Column("dependency_strength", db.String(20)),
    db.Column("technology_maturity", db.Integer, default=1),
    db.Column("scalability_rating", db.String(20)),
    db.Column("performance_rating", db.String(20)),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    db.Column("updated_at", db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)


class CapabilityAssessment(TenantMixin, db.Model):
    """Capability Maturity Assessments"""

    __tablename__ = "capability_assessments"

    id = db.Column(db.Integer, primary_key=True)
    capability_id = db.Column(
        db.Integer, db.ForeignKey("enterprise_capabilities.id"), nullable=False
    )
    assessment_date = db.Column(db.DateTime, default=datetime.utcnow)
    assessor_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Maturity dimensions (1 - 5 scale)
    process_maturity = db.Column(db.Integer)  # How well-defined are processes
    technology_maturity = db.Column(db.Integer)  # Technology enablement level
    skills_maturity = db.Column(db.Integer)  # Skills and competency level
    governance_maturity = db.Column(db.Integer)  # Governance and controls

    # Overall scores
    current_state_score = db.Column(db.Float)
    target_state_score = db.Column(db.Float)
    gap_analysis = db.Column(db.Text)
    recommendations = db.Column(db.Text)

    # Relationships
    capability = db.relationship("EnterpriseCapability", backref="assessments")
    assessor = db.relationship("User", backref="capability_assessments")

    def __repr__(self):
        return f"<CapabilityAssessment {self.capability.name} - {self.assessment_date}>"
