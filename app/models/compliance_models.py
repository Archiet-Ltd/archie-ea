"""
Compliance Requirements Models for Manufacturing

Captures regulatory, quality, security, and constraint requirements
that must be enforced in architecture decisions.

Supports:
- Regulatory compliance (GDPR, SOX, PCI-DSS, HIPAA, etc.)
- Manufacturing standards (ISO 9001, ISO 27001, OSHA, EPA, FDA)
- Quality attributes (NFRs with measurable thresholds)
- Budget and timeline constraints
- Security and audit requirements
"""

from datetime import datetime

from app import db

from .relationship_tables import capability_compliance_requirements

# ============================================================================
# Compliance Control
# ============================================================================


class ComplianceControl(db.Model):
    """
    Individual controls within a regulatory framework (e.g., NIST - 800 - 53 AC - 2, ISO 27001 A.9.2.1).

    These are the actual requirements/controls, NOT architectural components.
    Applications reference these to track compliance, not implement them as ElementTemplates.
    """

    __tablename__ = "compliance_controls"

    id = db.Column(db.Integer, primary_key=True)
    framework_id = db.Column(db.Integer, db.ForeignKey("regulatory_frameworks.id"), nullable=False)

    # Control identification
    control_code = db.Column(db.String(50), nullable=False)  # e.g., "AC - 2", "A.9.2.1"
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)

    # Organization
    category = db.Column(db.String(100))  # Access Control, Cryptography, etc.
    subcategory = db.Column(db.String(100))
    parent_control_code = db.Column(db.String(50))
    level = db.Column(db.Integer, default=1)

    # Severity and priority
    priority = db.Column(db.String(20), default="medium")  # critical, high, medium, low
    implementation_effort = db.Column(db.String(20))  # low, medium, high

    # Reference
    official_reference = db.Column(db.String(500))

    # Cross-framework harmonisation
    harmonized_control_id = db.Column(
        db.Integer, db.ForeignKey("compliance_controls.id"), nullable=True, index=True
    )
    harmonization_status = db.Column(
        db.String(20), nullable=True
    )  # proposed, confirmed, rejected
    harmonization_notes = db.Column(db.Text)

    # Metadata
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    framework = db.relationship("RegulatoryFramework", back_populates="controls")
    harmonized_control = db.relationship(
        "ComplianceControl", remote_side="ComplianceControl.id", backref="harmonized_controls"
    )

    __table_args__ = (
        db.UniqueConstraint("framework_id", "control_code", name="uq_framework_control"),
    )

    def __repr__(self):
        return f"<ComplianceControl {self.control_code}: {self.title}>"


# ============================================================================
# Regulatory Frameworks
# ============================================================================


class RegulatoryFramework(db.Model):
    """
    Master list of regulatory frameworks applicable to the organization.

    Examples:
    - GDPR (General Data Protection Regulation)
    - ISO 9001 (Quality Management)
    - ISO 27001 (Information Security)
    - OSHA (Occupational Safety and Health)
    - FDA 21 CFR Part 11 (Electronic Records)
    - EPA (Environmental Protection)
    """

    __tablename__ = "regulatory_frameworks"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(
        db.String(50), unique=True, nullable=False
    )  # e.g., 'ISO - 9001', 'GDPR', 'OSHA'
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    category = db.Column(
        db.String(50)
    )  # quality, security, safety, environmental, privacy, financial
    jurisdiction = db.Column(db.String(100))  # EU, US, Global, etc.
    enforcement_level = db.Column(db.String(20))  # mandatory, recommended, optional
    penalty_risk = db.Column(db.String(20))  # critical, high, medium, low

    # Applicability
    applies_to_manufacturing = db.Column(db.Boolean, default=True)
    applies_to_region = db.Column(db.String(100))  # US, EU, APAC, Global
    industry_specific = db.Column(db.String(100))  # pharmaceutical, automotive, food, general

    # References
    official_url = db.Column(db.String(500))
    standard_version = db.Column(db.String(50))
    last_updated = db.Column(db.DateTime)
    next_review_date = db.Column(db.DateTime)

    # Metadata
    status = db.Column(db.String(20), default="active")  # active, deprecated, superseded
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    requirements = db.relationship("ComplianceRequirement", backref="framework", lazy="dynamic")
    controls = db.relationship("ComplianceControl", back_populates="framework", lazy="dynamic")

    def __repr__(self):
        return f"<RegulatoryFramework {self.code}: {self.name}>"


# ComplianceControl is imported from compliance_frameworks.py to avoid duplication

# ============================================================================
# Compliance Requirements
# ============================================================================


class ComplianceRequirement(db.Model):
    """
    Specific compliance requirements that must be met in architecture/implementation.

    Links regulatory controls to actual system requirements.
    """

    __tablename__ = "compliance_requirements"

    id = db.Column(db.Integer, primary_key=True)

    # Link to ArchiMate (Basecoat pattern)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    archimate_element = db.relationship("ArchiMateElement", backref="compliance_requirements")

    # Requirement details
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text, nullable=False)
    requirement_type = db.Column(
        db.String(50), nullable=False
    )  # regulatory, quality, security, constraint

    # Regulatory linkage
    framework_id = db.Column(db.Integer, db.ForeignKey("regulatory_frameworks.id"), index=True)
    control_id = db.Column(db.Integer, db.ForeignKey("compliance_controls.id"))

    # Hierarchical structure for requirement decomposition
    parent_requirement_id = db.Column(db.Integer, db.ForeignKey("compliance_requirements.id"))
    hierarchy_level = db.Column(db.Integer, default=0)  # 0=root, 1=child, 2=grandchild, etc.

    # Priority and risk
    priority = db.Column(db.String(20), default="medium")  # critical, high, medium, low
    risk_if_not_met = db.Column(db.String(20))  # critical, high, medium, low
    penalty_description = db.Column(db.Text)  # What happens if violated

    # Measurable criteria
    acceptance_criteria = db.Column(db.Text)  # How to verify compliance
    measurement_method = db.Column(db.Text)  # How to measure
    threshold_value = db.Column(db.String(100))  # e.g., "99.9%", "<200ms", "100% audit trail"

    # Applicability
    applies_to_capability_id = db.Column(db.Integer, db.ForeignKey("business_capability.id"))
    applies_to_region = db.Column(db.String(100))
    applies_to_department = db.Column(db.String(100))

    # Status tracking
    status = db.Column(db.String(20), default="active")  # active, implemented, waived, deferred
    implementation_status = db.Column(
        db.String(20)
    )  # not_started, in_progress, completed, verified
    waiver_reason = db.Column(db.Text)  # If waived, why?
    waiver_approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    waiver_expires_at = db.Column(db.DateTime)

    # Evidence and verification
    evidence_location = db.Column(db.String(500))  # URL to evidence (SharePoint, etc.)
    last_verified_at = db.Column(db.DateTime)
    verified_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    next_review_date = db.Column(db.DateTime)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    control = db.relationship("ComplianceControl", backref="requirements")
    capability = db.relationship("BusinessCapability", foreign_keys=[applies_to_capability_id])
    capabilities = db.relationship(
        "BusinessCapability",
        secondary=capability_compliance_requirements,
        back_populates="compliance_requirements",
    )

    # Hierarchical relationships (self-referential)
    children = db.relationship(
        "ComplianceRequirement",
        backref=db.backref("parent", remote_side="ComplianceRequirement.id"),
        lazy="dynamic",
    )

    # Semantic EA Intelligence Relationships (imported from vendor_organization)
    vendor_capability_coverages = db.relationship(
        "VendorProductCapability",
        secondary="compliance_vendor_coverage",
        back_populates="compliance_requirements",
    )

    def __repr__(self):
        return f"<ComplianceRequirement {self.title}>"

    def is_compliant(self):
        """Check if requirement is met"""
        return self.implementation_status == "completed" and self.status == "active"

    def is_overdue(self):
        """Check if review is overdue"""
        if not self.next_review_date:
            return False
        return datetime.utcnow() > self.next_review_date

    def get_all_children(self):
        """Recursively get all descendant requirements"""
        descendants = []
        for child in self.children:
            descendants.append(child)
            descendants.extend(child.get_all_children())
        return descendants

    def get_root_requirement(self):
        """Get the top-level parent requirement"""
        if self.parent_requirement_id is None:
            return self
        return self.parent.get_root_requirement()

    def to_dict(self, include_children=False):
        """Convert requirement to dictionary for JSON serialization"""
        data = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "requirement_type": self.requirement_type,
            "priority": self.priority,
            "hierarchy_level": self.hierarchy_level,
            "parent_requirement_id": self.parent_requirement_id,
            "framework_id": self.framework_id,
            "control_id": self.control_id,
            "archimate_element_id": self.archimate_element_id,
            "status": self.status,
            "implementation_status": self.implementation_status,
            "acceptance_criteria": self.acceptance_criteria,
            "threshold_value": self.threshold_value,
        }
        if include_children:
            data["children"] = [child.to_dict(include_children=True) for child in self.children]
        return data


# ============================================================================
# Quality Attributes (Non-Functional Requirements)
# ============================================================================


class QualityAttribute(db.Model):
    """
    Non-functional requirements with measurable thresholds.

    Categories based on ISO/IEC 25010:
    - Performance (response time, throughput)
    - Reliability (uptime, MTBF, MTTR)
    - Security (authentication, encryption)
    - Usability (learnability, accessibility)
    - Maintainability (modularity, testability)
    - Portability (adaptability, installability)
    """

    __tablename__ = "quality_attributes"

    id = db.Column(db.Integer, primary_key=True)

    # Link to ArchiMate (Basecoat pattern)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    archimate_element = db.relationship("ArchiMateElement", backref="quality_attributes")

    # Quality attribute details
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False)  # performance, reliability, security, etc.
    subcategory = db.Column(db.String(50))  # response_time, uptime, encryption, etc.
    description = db.Column(db.Text)

    # Measurable thresholds
    metric_name = db.Column(db.String(100))  # e.g., "Response Time", "Uptime", "Throughput"
    metric_unit = db.Column(db.String(50))  # ms, %, requests/sec, GB, etc.
    target_value = db.Column(db.String(100), nullable=False)  # e.g., "<200", "99.9", ">1000"
    minimum_acceptable = db.Column(db.String(100))  # Fallback if target can't be met
    current_value = db.Column(db.String(100))  # Actual measured value

    # Measurement
    measurement_method = db.Column(db.Text)  # How to measure (APM tool, load test, manual)
    measurement_frequency = db.Column(db.String(50))  # continuous, daily, weekly, monthly
    measurement_tool = db.Column(db.String(100))  # e.g., "Datadog", "New Relic", "JMeter"

    # Priority and source
    priority = db.Column(db.String(20), default="medium")  # critical, high, medium, low
    source = db.Column(db.String(50))  # SLA, regulatory, business, technical
    rationale = db.Column(db.Text)  # Why this threshold?

    # Applicability
    applies_to_capability_id = db.Column(db.Integer, db.ForeignKey("business_capability.id"))
    applies_to_environment = db.Column(db.String(50))  # production, staging, all

    # Status
    status = db.Column(db.String(20), default="active")
    is_met = db.Column(db.Boolean)  # True if current_value meets target_value
    last_measured_at = db.Column(db.DateTime)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    capability = db.relationship("BusinessCapability", backref="quality_attributes")

    def __repr__(self):
        return f"<QualityAttribute {self.name}: {self.target_value} {self.metric_unit}>"

    def evaluate_compliance(self):
        """Evaluate if current value meets target"""
        if not self.current_value or not self.target_value:
            return None

        # Simple comparison (extend for complex comparisons)
        try:
            if "<" in self.target_value:
                threshold = float(self.target_value.replace("<", "").strip())
                current = float(self.current_value)
                return current < threshold
            elif ">" in self.target_value:
                threshold = float(self.target_value.replace(">", "").strip())
                current = float(self.current_value)
                return current > threshold
            else:
                # Exact match
                return self.current_value == self.target_value
        except ValueError:
            return None


# ============================================================================
# Project Constraints
# ============================================================================


class ProjectConstraint(db.Model):
    """
    Budget, timeline, resource, and technical debt constraints.

    These are hard limits that architecture decisions must respect.
    """

    __tablename__ = "project_constraints"

    id = db.Column(db.Integer, primary_key=True)

    # Link to ArchiMate (Basecoat pattern)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    archimate_element = db.relationship("ArchiMateElement", backref="project_constraints")

    # Constraint details
    name = db.Column(db.String(200), nullable=False)
    constraint_type = db.Column(
        db.String(50), nullable=False
    )  # budget, timeline, resource, technical, policy
    description = db.Column(db.Text, nullable=False)

    # Constraint value
    limit_type = db.Column(db.String(50))  # maximum, minimum, exact, range
    limit_value = db.Column(db.String(100))  # e.g., "$500K", "6 months", "5 FTE", "Python 3.9+"
    limit_unit = db.Column(db.String(50))  # USD, months, FTE, etc.

    # Budget-specific fields
    budget_amount = db.Column(db.Numeric(15, 2))
    budget_currency = db.Column(db.String(10), default="USD")
    budget_type = db.Column(db.String(50))  # capex, opex, total

    # Timeline-specific fields
    deadline = db.Column(db.DateTime)
    duration_months = db.Column(db.Integer)
    milestone_description = db.Column(db.Text)

    # Resource-specific fields
    resource_type = db.Column(db.String(50))  # human, infrastructure, license
    resource_availability = db.Column(db.String(200))  # e.g., "2 Java developers available Q2"

    # Technical debt-specific
    technical_debt_description = db.Column(db.Text)
    legacy_system_constraint = db.Column(db.Text)
    technology_limitation = db.Column(db.Text)

    # Priority and flexibility
    priority = db.Column(db.String(20), default="high")  # critical, high, medium, low
    is_hard_constraint = db.Column(db.Boolean, default=True)  # True = cannot be violated
    flexibility_notes = db.Column(db.Text)  # If soft constraint, what's negotiable?

    # Ownership
    constraint_owner_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    decision_authority = db.Column(db.String(100))  # Who can waive this constraint?

    # Status
    status = db.Column(db.String(20), default="active")
    is_violated = db.Column(db.Boolean, default=False)
    violation_notes = db.Column(db.Text)

    # Applicability
    applies_to_capability_id = db.Column(db.Integer, db.ForeignKey("business_capability.id"))
    applies_to_project = db.Column(db.String(100))

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    capability = db.relationship("BusinessCapability", backref="project_constraints")
    constraint_owner = db.relationship(
        "User", foreign_keys=[constraint_owner_id], backref="owned_constraints"
    )

    def __repr__(self):
        return f"<ProjectConstraint {self.name}: {self.limit_value}>"

    def is_constraint_met(self, proposed_value):
        """Check if proposed value violates constraint"""
        # Implement constraint validation logic
        # This is simplified - extend based on limit_type
        return True  # Placeholder


# ============================================================================
# Compliance Gap Analysis
# ============================================================================


class ComplianceGap(db.Model):
    """
    Identified gaps between current state and required compliance.

    Generated automatically by compliance analysis service.
    """

    __tablename__ = "compliance_gaps"

    id = db.Column(db.Integer, primary_key=True)

    # What's the gap?
    gap_type = db.Column(
        db.String(50), nullable=False
    )  # missing_requirement, inadequate_control, expired_certification
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text, nullable=False)

    # Link to requirement
    compliance_requirement_id = db.Column(db.Integer, db.ForeignKey("compliance_requirements.id"))
    quality_attribute_id = db.Column(db.Integer, db.ForeignKey("quality_attributes.id"))

    # Risk assessment
    risk_level = db.Column(db.String(20), nullable=False)  # critical, high, medium, low
    impact_description = db.Column(db.Text)
    likelihood = db.Column(db.String(20))  # certain, likely, possible, unlikely

    # Recommended remediation
    remediation_action = db.Column(db.Text)
    estimated_effort = db.Column(db.String(50))  # person-weeks
    estimated_cost = db.Column(db.Numeric(15, 2))
    target_completion_date = db.Column(db.DateTime)

    # Status
    status = db.Column(db.String(20), default="open")  # open, in_progress, resolved, accepted_risk
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    resolution_notes = db.Column(db.Text)
    resolved_at = db.Column(db.DateTime)

    # Metadata
    identified_at = db.Column(db.DateTime, default=datetime.utcnow)
    identified_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    compliance_requirement = db.relationship("ComplianceRequirement", backref="gaps")
    quality_attribute = db.relationship("QualityAttribute", backref="gaps")
    assigned_to = db.relationship(
        "User", foreign_keys=[assigned_to_id], backref="assigned_compliance_gaps"
    )

    def __repr__(self):
        return f"<ComplianceGap {self.title} ({self.risk_level})>"


# ============================================================================
# Compliance Policy and Violation Tracking (Enterprise CRUD)
# ============================================================================


class CompliancePolicy(db.Model):
    """
    Compliance policy definition for organizational compliance tracking.

    Policies define compliance requirements and frameworks that the
    organization must adhere to (NIST, CIS, ISO, SOX, HIPAA, etc.).
    """

    __tablename__ = "compliance_policies"

    id = db.Column(db.Integer, primary_key=True)

    # Policy identification
    name = db.Column(db.String(200), nullable=False, unique=True)
    policy_type = db.Column(
        db.String(50), nullable=False, default="NIST"
    )  # NIST, CIS, ISO, SOX, HIPAA, GDPR, PCI-DSS, COBIT
    description = db.Column(db.Text)

    # Policy reference
    policy_version = db.Column(db.String(50))  # e.g., "SP 800-53", "v3.1"
    external_reference = db.Column(db.String(500))  # Link to framework documentation

    # Status
    is_active = db.Column(db.Boolean, default=True)
    enforcement_date = db.Column(db.DateTime)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    violations = db.relationship("ComplianceViolation", backref="policy", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<CompliancePolicy {self.name} ({self.policy_type})>"

    def to_dict(self):
        """Convert policy to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "policy_type": self.policy_type,
            "description": self.description,
            "policy_version": self.policy_version,
            "external_reference": self.external_reference,
            "is_active": self.is_active,
            "enforcement_date": self.enforcement_date.isoformat() if self.enforcement_date else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "violation_count": len(self.violations),
        }


class ComplianceViolation(db.Model):
    """
    Compliance violation tracking and remediation management.

    Records specific violations of compliance policies and tracks
    remediation efforts and status.
    """

    __tablename__ = "compliance_violations"

    id = db.Column(db.Integer, primary_key=True)

    # Violation reference
    policy_id = db.Column(db.Integer, db.ForeignKey("compliance_policies.id"), nullable=False)
    violation_code = db.Column(db.String(100))  # e.g., "AC-2.1", "CIS-2.3"

    # Violation details
    severity = db.Column(
        db.String(20), nullable=False, default="Medium"
    )  # Critical, High, Medium, Low
    description = db.Column(db.Text, nullable=False)
    affected_system = db.Column(db.String(500))  # Which application/system has the violation
    root_cause = db.Column(db.Text)

    # Remediation tracking
    status = db.Column(
        db.String(50), nullable=False, default="Open"
    )  # Open, In Progress, Resolved
    remediation_plan = db.Column(db.Text)
    remediation_target_date = db.Column(db.DateTime)
    remediation_completed_at = db.Column(db.DateTime)

    # Assigned ownership
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id], backref="assigned_violations")

    # Risk assessment
    business_impact = db.Column(db.Text)  # Potential impact of this violation
    regulatory_impact = db.Column(db.Text)  # Regulatory consequences
    mitigation_status = db.Column(db.String(50))  # Temporary mitigation while fix is developed

    # Evidence and documentation
    evidence_link = db.Column(db.String(500))  # Link to evidence/documentation
    audit_evidence_location = db.Column(db.String(500))  # Where to find audit evidence

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    created_by = db.relationship("User", foreign_keys=[created_by_id], backref="created_violations")

    def __repr__(self):
        return f"<ComplianceViolation {self.violation_code} ({self.severity}) - {self.status}>"

    def to_dict(self):
        """Convert violation to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "policy_id": self.policy_id,
            "policy_name": self.policy.name if self.policy else None,
            "violation_code": self.violation_code,
            "severity": self.severity,
            "description": self.description,
            "affected_system": self.affected_system,
            "root_cause": self.root_cause,
            "status": self.status,
            "remediation_plan": self.remediation_plan,
            "remediation_target_date": (
                self.remediation_target_date.isoformat() if self.remediation_target_date else None
            ),
            "remediation_completed_at": (
                self.remediation_completed_at.isoformat()
                if self.remediation_completed_at
                else None
            ),
            "assigned_to": self.assigned_to.username if self.assigned_to else None,
            "business_impact": self.business_impact,
            "regulatory_impact": self.regulatory_impact,
            "mitigation_status": self.mitigation_status,
            "evidence_link": self.evidence_link,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class ComplianceRequirementModel(db.Model):
    """Placeholder model for compliance_requirements_model table.

    This table is referenced by a FK in ComplianceCheck
    (compliance_checks.compliance_requirement_id). The table exists in the DB
    and is separate from compliance_requirements (ComplianceRequirement).
    This class registers it with SQLAlchemy metadata so FK resolution works
    during migration autogenerate.
    """

    __tablename__ = "compliance_requirements_model"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    compliance_framework = db.Column(db.String(50))
    requirement_id = db.Column(db.String(50))
    category = db.Column(db.String(50))
    description = db.Column(db.Text)
