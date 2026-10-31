"""
Application Capability Mapping Model

Maps applications to business capabilities for gap analysis and portfolio optimization.
This model bridges business capabilities (what the business does) with application
components (how IT enables it).

Key Features:
- Application-capability relationship tracking
- Coverage and maturity assessment
- Gap analysis for capability mapping
- Technical debt tracking per capability
- Replacement planning support
"""

from datetime import datetime

from sqlalchemy import event
from sqlalchemy.orm import validates

from app.datetime_helpers import utcnow

from .. import db


class ApplicationCapabilityMapping(db.Model):
    """
    Application Capability Mapping

    Maps ApplicationComponent to BusinessCapability with comprehensive support metrics.
    Enables capability-driven application portfolio optimization and gap analysis.
    """

    __tablename__ = "application_capability_mapping"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)

    # Specialization type marker
    specialization_type = db.Column(
        db.String(50), default="APPLICATION", index=True
    )  # Explicit type: APPLICATION

    # Organization (tenant isolation)
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Link entities (Foreign Keys)
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False, index=True
    )
    business_capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id"), nullable=False, index=True
    )

    # Mapping characteristics
    support_level = db.Column(db.String(50), default="partial")  # full, partial, minimal, none, gap
    coverage_percentage = db.Column(db.Integer, default=0)  # 0 - 100% capability coverage
    support_quality_score = db.Column(db.Integer)  # 1 - 10 quality rating

    # Relationship details
    relationship_type = db.Column(
        db.String(50), default="enables"
    )  # enables, supports, governs, measures
    relationship_strength = db.Column(db.String(20))  # critical, strong, moderate, weak
    is_primary_enabler = db.Column(
        db.Boolean, default=False
    )  # Is this the primary app for this capability?

    # Maturity contribution
    maturity_contribution_score = db.Column(
        db.Integer
    )  # How much this app contributes to capability maturity (0 - 5)
    maturity_gaps = db.Column(db.Text)  # JSON array of specific maturity gaps

    # Gap analysis
    gap_status = db.Column(
        db.String(20), default="unknown"
    )  # fully_covered, partially_covered, gap, excess
    gap_description = db.Column(db.Text)
    gap_severity = db.Column(db.String(20))  # critical, high, medium, low
    gap_impact_description = db.Column(db.Text)
    remediation_plan = db.Column(db.Text)
    remediation_priority = db.Column(db.String(20))  # immediate, high, medium, low

    # Technical health metrics
    technical_debt_score = db.Column(db.Integer)  # 0 - 100 (higher = more technical debt)
    application_age_years = db.Column(db.Integer)
    technology_obsolescence_risk = db.Column(db.String(20))  # high, medium, low
    integration_complexity = db.Column(db.String(20))  # low, medium, high, very_high

    # Replacement & transformation planning
    replacement_priority = db.Column(db.String(20))  # critical, high, medium, low, none
    replacement_cost_estimate = db.Column(db.Float)
    replacement_timeline_months = db.Column(db.Integer)
    replacement_approach = db.Column(
        db.String(50)
    )  # replace, modernize, retire, consolidate, retain
    alternative_solutions = db.Column(db.Text)  # JSON array of alternative solutions

    # Business impact metrics
    business_value_score = db.Column(db.Integer)  # 1 - 10 business value score
    user_satisfaction_score = db.Column(db.Integer)  # 1 - 10 user satisfaction
    business_criticality = db.Column(db.String(20))  # mission_critical, high, medium, low
    user_count = db.Column(db.Integer)  # Number of users using this app for this capability
    transaction_volume_daily = db.Column(db.Integer)  # Daily transaction volume

    # Financial metrics
    annual_operating_cost = db.Column(db.Float)  # Annual cost to operate for this capability
    cost_per_user = db.Column(db.Float)  # Annual cost per user
    cost_efficiency_score = db.Column(db.Integer)  # 1 - 10 cost efficiency rating
    potential_savings = db.Column(db.Float)  # Potential annual savings from optimization

    # Integration details
    integration_method = db.Column(db.String(50))  # API, file, batch, manual, realtime
    data_flow_direction = db.Column(db.String(20))  # bidirectional, inbound, outbound
    integration_frequency = db.Column(db.String(50))  # realtime, hourly, daily, weekly, monthly
    integration_reliability = db.Column(db.Integer)  # 1 - 10 reliability score

    # Compliance & security
    compliance_level = db.Column(db.String(20))  # compliant, partial, non_compliant
    compliance_gaps = db.Column(db.Text)  # JSON array of compliance gaps
    security_risk_level = db.Column(db.String(20))  # low, medium, high, critical
    data_sensitivity = db.Column(db.String(20))  # public, internal, confidential, restricted

    # Governance & ownership
    business_owner = db.Column(db.String(100))
    technical_owner = db.Column(db.String(100))
    governance_model = db.Column(db.String(50))  # centralized, federated, hybrid

    # Assessment metadata
    assessment_status = db.Column(
        db.String(20), default="draft"
    )  # draft, validated, approved, archived
    last_assessed_date = db.Column(db.DateTime)
    assessor_name = db.Column(db.String(100))
    assessment_methodology = db.Column(
        db.String(100)
    )  # survey, interview, data_analysis, automated
    assessment_notes = db.Column(db.Text)
    confidence_score = db.Column(db.Integer)  # 0 - 100 confidence in this mapping

    # Discovery metadata
    discovered_by_ai = db.Column(db.Boolean, default=False)
    discovery_confidence = db.Column(db.Float)  # 0 - 1 AI confidence score
    discovery_source = db.Column(db.String(100))  # manual, ai, imported, vendor_data

    # Timeline
    mapping_start_date = db.Column(db.Date)
    mapping_end_date = db.Column(db.Date)
    is_active = db.Column(db.Boolean, default=True)
    is_validated = db.Column(db.Boolean, default=False)

    # Timestamps
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    organization = db.relationship("Organization", backref="capability_mappings")
    application = db.relationship(
        "ApplicationComponent",
        back_populates="capability_mappings",
        foreign_keys=[application_component_id],
    )
    business_capability = db.relationship(
        "BusinessCapability", foreign_keys=[business_capability_id]
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    @validates("coverage_percentage")
    def validate_coverage_percentage(self, key, value):
        """Ensure coverage percentage is between 0 and 100"""
        if value is not None:
            if value < 0 or value > 100:
                raise ValueError("Coverage percentage must be between 0 and 100")
        return value

    @validates("confidence_score")
    def validate_confidence_score(self, key, value):
        """Ensure confidence score is between 0 and 100"""
        if value is not None:
            if value < 0 or value > 100:
                raise ValueError("Confidence score must be between 0 and 100")
        return value

    def calculate_gap_severity(self):
        """
        Calculate gap severity based on coverage, business criticality, and support level
        """
        if self.coverage_percentage >= 90 and self.support_level == "full":
            return "none"
        elif self.coverage_percentage >= 70:
            return "low"
        elif self.coverage_percentage >= 40:
            return "medium"
        elif self.business_criticality in ["mission_critical", "high"]:
            return "critical"
        else:
            return "high"

    def calculate_replacement_priority(self):
        """
        Calculate replacement priority based on technical debt, business value, and gap severity
        """
        if self.technical_debt_score and self.technical_debt_score > 80:
            if self.business_criticality == "mission_critical":
                return "critical"
            return "high"
        elif self.gap_severity in ["critical", "high"]:
            return "high"
        elif self.technical_debt_score and self.technical_debt_score > 50:
            return "medium"
        else:
            return "low"

    def to_dict(self, include_relationships=False):
        """
        Convert mapping to dictionary representation
        """
        data = {
            "id": self.id,
            "application_component_id": self.application_component_id,
            "business_capability_id": self.business_capability_id,
            "support_level": self.support_level,
            "coverage_percentage": self.coverage_percentage,
            "support_quality_score": self.support_quality_score,
            "relationship_type": self.relationship_type,
            "is_primary_enabler": self.is_primary_enabler,
            "gap_status": self.gap_status,
            "gap_severity": self.gap_severity,
            "technical_debt_score": self.technical_debt_score,
            "replacement_priority": self.replacement_priority,
            "business_value_score": self.business_value_score,
            "user_satisfaction_score": self.user_satisfaction_score,
            "business_criticality": self.business_criticality,
            "is_active": self.is_active,
            "is_validated": self.is_validated,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_relationships:
            data["application"] = (
                self.application.to_dict()
                if self.application and hasattr(self.application, "to_dict")
                else None
            )
            data["business_capability"] = (
                self.business_capability.to_dict()
                if self.business_capability and hasattr(self.business_capability, "to_dict")
                else None
            )

        return data

    def __repr__(self):
        app_name = (
            self.application.name if self.application else f"App#{self.application_component_id}"
        )
        cap_name = (
            self.business_capability.name
            if self.business_capability
            else f"Cap#{self.business_capability_id}"
        )
        return f"<ApplicationCapabilityMapping {app_name} → {cap_name} ({self.support_level})>"


class CapabilityTaxonomyAudit(db.Model):
    """
    Audit trail for capability taxonomy changes.
    Tracks all modifications to capability definitions, levels, and domains.
    """

    __tablename__ = "capability_taxonomy_audit"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    capability_id = db.Column(db.Integer, db.ForeignKey("unified_capabilities.id"), nullable=True)
    audit_type = db.Column(db.String(50), nullable=False)  # create, update, delete, merge, move
    change_type = db.Column(db.String(50))  # level_change, domain_change, name_change, etc.
    entity_type = db.Column(db.String(50), default="capability")  # capability, domain, level
    entity_id = db.Column(db.Integer)
    entity_name = db.Column(db.String(200))
    old_value = db.Column(db.Text)  # JSON of old state
    new_value = db.Column(db.Text)  # JSON of new state
    change_reason = db.Column(db.Text)
    changed_by = db.Column(db.String(100))
    changed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by = db.Column(db.String(100))
    approval_status = db.Column(db.String(20), default="pending")  # pending, approved, rejected
    impact_assessment = db.Column(db.Text)  # JSON describing impact
    rollback_available = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "capability_id": self.capability_id,
            "audit_type": self.audit_type,
            "change_type": self.change_type,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "change_reason": self.change_reason,
            "changed_by": self.changed_by,
            "approval_status": self.approval_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# Event listeners for automatic calculations
@event.listens_for(ApplicationCapabilityMapping, "before_insert")
@event.listens_for(ApplicationCapabilityMapping, "before_update")
def calculate_derived_fields(mapper, connection, target):
    """
    Automatically calculate derived fields before insert/update
    """
    # Calculate gap severity if not already set
    if not target.gap_severity and target.coverage_percentage is not None:
        target.gap_severity = target.calculate_gap_severity()

    # Calculate replacement priority if not already set
    if not target.replacement_priority:
        target.replacement_priority = target.calculate_replacement_priority()


class CapabilityLevelDefinition(db.Model):
    """
    Defines capability levels in the taxonomy hierarchy.
    """

    __tablename__ = "capability_level_definitions"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    level_number = db.Column(db.Integer, nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    characteristics = db.Column(db.Text)  # JSON array of characteristics
    example_capabilities = db.Column(db.Text)  # JSON array of examples
    governance_requirements = db.Column(db.Text)
    typical_owner_role = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "level_number": self.level_number,
            "name": self.name,
            "description": self.description,
            "characteristics": self.characteristics,
            "example_capabilities": self.example_capabilities,
            "governance_requirements": self.governance_requirements,
            "typical_owner_role": self.typical_owner_role,
            "is_active": self.is_active,
        }


class CapabilityDomainDefinition(db.Model):
    """
    Defines business domains for capability organization.
    """

    __tablename__ = "capability_domain_definitions"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), unique=True)
    description = db.Column(db.Text)
    color = db.Column(db.String(20))  # Hex color for UI
    icon = db.Column(db.String(50))  # Icon identifier
    strategic_importance = db.Column(db.Integer, default=5)  # 1 - 10 scale
    business_owner = db.Column(db.String(100))
    it_owner = db.Column(db.String(100))
    parent_domain_id = db.Column(db.Integer, db.ForeignKey("capability_domain_definitions.id"))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    # Self-referential relationship for parent domain
    parent_domain = db.relationship(
        "CapabilityDomainDefinition", remote_side=[id], backref="child_domains"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "color": self.color,
            "icon": self.icon,
            "strategic_importance": self.strategic_importance,
            "business_owner": self.business_owner,
            "it_owner": self.it_owner,
            "parent_domain_id": self.parent_domain_id,
            "is_active": self.is_active,
        }


class CapabilityTaxonomyRule(db.Model):
    """
    Taxonomy enforcement rules for capability validation.
    """

    __tablename__ = "capability_taxonomy_rules"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    rule_name = db.Column(db.String(200), nullable=False, unique=True)
    rule_type = db.Column(db.String(50), nullable=False)  # naming, hierarchy, domain, level
    rule_category = db.Column(db.String(50))  # structural, semantic, format
    capability_level = db.Column(db.Integer)  # Optional: applies to specific level
    domain = db.Column(db.String(100))  # Optional: applies to specific domain
    rule_pattern = db.Column(db.Text)  # JSON pattern for rule matching
    validation_logic = db.Column(db.Text)  # Description of validation logic
    is_active = db.Column(db.Boolean, default=True)
    severity = db.Column(db.String(20), default="warning")  # error, warning, info
    auto_correct = db.Column(db.Boolean, default=False)
    correction_suggestion = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        import json

        return {
            "id": self.id,
            "rule_name": self.rule_name,
            "rule_type": self.rule_type,
            "rule_category": self.rule_category,
            "capability_level": self.capability_level,
            "domain": self.domain,
            "rule_pattern": json.loads(self.rule_pattern) if self.rule_pattern else None,
            "validation_logic": self.validation_logic,
            "is_active": self.is_active,
            "severity": self.severity,
            "auto_correct": self.auto_correct,
            "correction_suggestion": self.correction_suggestion,
        }


class CapabilityTaxonomyViolation(db.Model):
    """
    Records taxonomy violations detected during validation.
    """

    __tablename__ = "capability_taxonomy_violations"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    capability_id = db.Column(db.Integer, db.ForeignKey("unified_capabilities.id"))
    rule_id = db.Column(db.Integer, db.ForeignKey("capability_taxonomy_rules.id"))
    violation_type = db.Column(db.String(50), nullable=False)  # naming, hierarchy, domain
    violation_details = db.Column(db.Text)  # JSON with violation details
    current_value = db.Column(db.String(500))  # Current incorrect value
    suggested_value = db.Column(db.String(500))  # Suggested correction
    severity = db.Column(db.String(20), default="warning")  # error, warning, info
    status = db.Column(db.String(20), default="open")  # open, resolved, ignored
    resolved_at = db.Column(db.DateTime)
    resolved_by = db.Column(db.String(100))
    resolution_notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)

    # Relationships
    rule = db.relationship("CapabilityTaxonomyRule", backref="violations")

    def to_dict(self):
        return {
            "id": self.id,
            "capability_id": self.capability_id,
            "rule_id": self.rule_id,
            "violation_type": self.violation_type,
            "violation_details": self.violation_details,
            "current_value": self.current_value,
            "suggested_value": self.suggested_value,
            "severity": self.severity,
            "status": self.status,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolved_by": self.resolved_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# One home for "which applications support which capabilities" is
# unified_application_capability_mapping (read by the coverage page, the genome
# slice, the chat and MCP). This table is the older home, and a dozen writers
# still insert here. Every insert is mirrored into the unified table in the same
# transaction, the way a BusinessCapability write is projected into
# unified_capabilities, so no writer can leave a system "supporting nothing".
# Idempotent: a pair already in the unified table is left as it is. A capability
# with no unified record (an un-migrated database) is skipped, not failed.
# ---------------------------------------------------------------------------
# The unified mapping is tenant-fenced: the mirrored row takes the organisation of the
# application component it maps, the same owner the fence backfill derives for this table.
# raw-sql-tenancy-writes-ok: organization_id is written from the source application's own
# column, the canonical owner of the mapped row, not from request data.
_MIRROR_MAPPING_SQL = """
INSERT INTO unified_application_capability_mapping
    (unified_capability_id, application_component_id, organization_id, support_level, coverage_percentage,
     support_quality, relationship_type, relationship_strength, dependency_level,
     gap_status, is_active, is_strategic, created_at, updated_at)
SELECT uc.id, ac.id, ac.organization_id, :support_level, :coverage, 3, :relationship_type, 3, 'medium',
       COALESCE(:gap_status, 'unknown'), COALESCE(:is_active, TRUE), FALSE, NOW(), NOW()
  FROM unified_capabilities AS uc
  JOIN application_components AS ac ON ac.id = :app_id
 WHERE uc.source_table = 'business_capability'
   AND uc.source_id = CAST(:bc_id AS VARCHAR)
   AND ac.organization_id IS NOT NULL
   AND NOT EXISTS (
        SELECT 1 FROM unified_application_capability_mapping AS m
         WHERE m.unified_capability_id = uc.id AND m.application_component_id = ac.id)
"""


def _mirror_mapping_into_unified(mapper, connection, target):
    from sqlalchemy import text

    relationship = target.relationship_type if target.relationship_type in ("supports", "enables", "governs", "measures") else "supports"
    try:
        with connection.begin_nested():
            connection.execute(
                text(_MIRROR_MAPPING_SQL),
                {
                    "app_id": target.application_component_id,
                    "bc_id": target.business_capability_id,
                    "support_level": target.support_level or "partial",
                    "coverage": target.coverage_percentage or 0,
                    "relationship_type": relationship,
                    "gap_status": target.gap_status,
                    "is_active": target.is_active,
                },
            )
    except Exception:  # the mirror must never fail the legacy write
        import logging

        logging.getLogger(__name__).exception(
            "could not mirror application_capability_mapping id=%s into the unified table", target.id)


event.listen(ApplicationCapabilityMapping, "after_insert", _mirror_mapping_into_unified)
