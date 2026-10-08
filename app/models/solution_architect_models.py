"""
Solution Architect Workspace Models

Enterprise-grade database models for solution options analysis with:
- Session management and versioning
- Multi-capability mapping with metadata
- ArchiMate 3.2 Motivational Layer elements
- Persistent recommendations
- ADR integration
"""

import enum
from datetime import datetime
from decimal import Decimal  # dead-code-ok: used in column defaults via server_default expressions

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app import db
from app.models.mixins import TenantMixin

# Re-export the canonical Solution model. Many call sites do
# `from app.models.solution_architect_models import Solution` (the Solution
# Architect workspace lives here), but Solution itself is defined in
# solution_models. Without this module-level re-export those imports raised
# ImportError and 500'd ~10 enterprise/solution API routes. solution_models
# only imports this module inside functions, so there is no import cycle.
from app.models.solution_models import Solution  # noqa: E402,F401

# ============================================================================
# ENUMS
# ============================================================================


class SolutionSessionStatus(enum.Enum):
    """Status of a solution analysis session"""

    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class SupportLevel(enum.Enum):
    """Support level for capability mapping"""

    CRITICAL = "critical"  # Must have, business critical
    MAJOR = "major"  # Important, significant impact
    MINOR = "minor"  # Nice to have, low impact
    NICE_TO_HAVE = "nice_to_have"  # Optional, minimal impact


class DriverType(enum.Enum):
    """Types of business drivers"""

    TECHNOLOGY = "technology"  # Technology change/innovation
    STAKEHOLDER = "stakeholder"  # Stakeholder demand
    EXTERNAL = "external"  # Market, regulatory, competition
    INTERNAL = "internal"  # Internal business needs


class RequirementType(enum.Enum):
    """Types of requirements"""

    FUNCTIONAL = "functional"  # Functional capabilities
    QUALITY = "quality"  # Non-functional/quality attributes
    CONSTRAINT = "constraint"  # Hard constraints


class ConstraintType(enum.Enum):
    """Types of constraints"""

    BUDGET = "budget"
    TIMELINE = "timeline"
    RESOURCE = "resource"
    COMPLIANCE = "compliance"
    TECHNICAL = "technical"
    ORGANIZATIONAL = "organizational"


class RecommendationOptionType(enum.Enum):
    """Solution option types"""

    BUY = "buy"  # Buy commercial solution
    BUILD = "build"  # Build custom solution
    REUSE = "reuse"  # Reuse existing application
    PARTNER = "partner"  # Partner with vendor
    HYBRID = "hybrid"  # Combination approach


# ============================================================================
# JUNCTION TABLES
# ============================================================================

# ENH-014: Many-to-many link between solutions and roadmap initiatives
solution_roadmap_initiatives = db.Table(
    "solution_roadmap_initiatives",
    Column("id", Integer, primary_key=True),
    Column(
        "solution_id",
        Integer,
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "initiative_id",
        Integer,
        ForeignKey("technology_roadmap_initiatives.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("linked_at", DateTime, default=datetime.utcnow),
    UniqueConstraint("solution_id", "initiative_id", name="uq_solution_initiative"),
    extend_existing=True,
)


# ============================================================================
# CORE SESSION MANAGEMENT
# ============================================================================


class SolutionAnalysisSession(TenantMixin, db.Model):
    """
    Main session for solution options analysis.

    Tracks the complete analysis lifecycle with versioning support.
    """

    __tablename__ = "solution_analysis_sessions"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)

    # Status and ownership
    status = Column(
        Enum(SolutionSessionStatus), default=SolutionSessionStatus.DRAFT, nullable=False
    )
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Version tracking
    current_version = Column(Integer, default=1, nullable=False)

    # Metadata
    tags = Column(JSON)  # Searchable tags
    custom_metadata = Column(JSON)  # Additional custom data

    # Relationships
    created_by = relationship("User", foreign_keys=[created_by_id])
    problem_definition = relationship(
        "SolutionProblemDefinition",
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
    )
    recommendations = relationship(
        "SolutionRecommendation", back_populates="session", cascade="all, delete-orphan"
    )
    versions = relationship(
        "SolutionSessionVersion",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SolutionSessionVersion.version_number.desc()",
    )
    adr_links = relationship(
        "SolutionADRLink", back_populates="session", cascade="all, delete-orphan"
    )

    # Indexes
    __table_args__ = (
        Index("idx_solution_session_status", "status"),
        Index("idx_solution_session_created_by", "created_by_id"),
        Index("idx_solution_session_created_at", "created_at"),
    )

    def __repr__(self):
        return f"<SolutionAnalysisSession {self.id}: {self.name}>"


# ============================================================================
# PROBLEM DEFINITION
# ============================================================================


class SolutionProblemDefinition(TenantMixin, db.Model):
    """
    Detailed problem definition for solution analysis.

    Captures the business problem, context, and constraints.
    """

    __tablename__ = "solution_problem_definitions"

    id = Column(Integer, primary_key=True)
    session_id = Column(
        Integer, ForeignKey("solution_analysis_sessions.id"), unique=True, nullable=False
    )

    # Problem description
    problem_description = Column(Text, nullable=False)
    business_context = Column(Text)  # Additional context

    # Scope and priority
    is_critical = Column(Boolean, default=False)
    priority = Column(Integer)  # 1 - 5 scale

    # Budget constraints
    budget_min = Column(Numeric(15, 2))
    budget_max = Column(Numeric(15, 2))
    budget_currency = Column(String(3), default="GBP")

    # Timeline
    timeline_months = Column(Integer)
    target_start_date = Column(DateTime)
    target_completion_date = Column(DateTime)

    # Organizational context
    organization_size = Column(String(50))  # smb, midmarket, enterprise
    industry_vertical = Column(String(100))
    geographic_scope = Column(String(100))  # regional, national, global

    # Technical context
    user_count = Column(Integer)
    transaction_volume = Column(Integer)
    data_volume_gb = Column(Integer)

    # Compliance and standards
    compliance_requirements = Column(JSON)  # List of compliance needs
    standards_requirements = Column(JSON)  # List of standards to follow

    # Existing constraints
    existing_technology_stack = Column(JSON)
    integration_requirements = Column(JSON)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    session = relationship("SolutionAnalysisSession", back_populates="problem_definition")
    drivers = relationship("SolutionDriver", back_populates="problem", cascade="all, delete-orphan")
    goals = relationship("SolutionGoal", back_populates="problem", cascade="all, delete-orphan")
    requirements = relationship(
        "SolutionRequirement", back_populates="problem", cascade="all, delete-orphan"
    )
    constraints = relationship(
        "SolutionConstraint", back_populates="problem", cascade="all, delete-orphan"
    )
    principles = relationship(
        "SolutionPrinciple", back_populates="problem", cascade="all, delete-orphan"
    )
    assessments = relationship(
        "SolutionAssessment", back_populates="problem", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<SolutionProblemDefinition {self.id} for Session {self.session_id}>"


# ============================================================================
# ARCHIMATE MOTIVATIONAL LAYER ELEMENTS
# ============================================================================


class SolutionDriver(TenantMixin, db.Model):
    """
    Business drivers motivating the need for solution.

    ArchiMate 3.2: Motivation Layer - Driver element
    """

    __tablename__ = "solution_drivers"

    id = Column(Integer, primary_key=True)
    problem_id = Column(Integer, ForeignKey("solution_problem_definitions.id"), nullable=False)
    # The ArchiMate mirror of this row. Added 31 Aug 2026: CLAUDE.md requires
    # every motivation entity to have a matching ArchiMateElement, but this
    # model had nowhere to record one, so its creation paths could never
    # comply. Nullable and SET NULL so reconcile-schema can add it to
    # existing databases; populated automatically by
    # app/services/archimate_backbone.py.
    archimate_element_id = Column(
        Integer, ForeignKey("archimate_elements.id", ondelete="SET NULL"),
        index=True, nullable=True,
    )

    # Driver details
    name = Column(String(200), nullable=False)
    description = Column(Text)
    driver_type = Column(Enum(DriverType), nullable=False)

    # Impact assessment
    impact_level = Column(Integer)  # 1 - 5 scale
    urgency = Column(Integer)  # 1 - 5 scale

    # Source
    source = Column(String(200))  # Who/what identified this driver
    ai_generated = Column(Boolean, default=False)
    ai_confidence = Column(Float)

    # Relationships
    problem = relationship("SolutionProblemDefinition", back_populates="drivers")

    __table_args__ = (
        Index("idx_solution_driver_problem", "problem_id"),
        Index("idx_solution_driver_type", "driver_type"),
    )

    def __repr__(self):
        return f"<SolutionDriver {self.id}: {self.name}>"


class SolutionGoal(TenantMixin, db.Model):
    """
    Desired outcomes and goals for the solution.

    ArchiMate 3.2: Motivation Layer - Goal element
    """

    __tablename__ = "solution_goals"

    id = Column(Integer, primary_key=True)
    problem_id = Column(Integer, ForeignKey("solution_problem_definitions.id"), nullable=False)
    # The ArchiMate mirror of this row. Added 31 Aug 2026: CLAUDE.md requires
    # every motivation entity to have a matching ArchiMateElement, but this
    # model had nowhere to record one, so its creation paths could never
    # comply. Nullable and SET NULL so reconcile-schema can add it to
    # existing databases; populated automatically by
    # app/services/archimate_backbone.py.
    archimate_element_id = Column(
        Integer, ForeignKey("archimate_elements.id", ondelete="SET NULL"),
        index=True, nullable=True,
    )
    driver_id = Column(Integer, ForeignKey("solution_drivers.id", ondelete="SET NULL"), nullable=True, index=True)  # migration-exempt: nullable column, safe via db.create_all()

    # Goal details
    name = Column(String(200), nullable=False)
    description = Column(Text)

    # Measurement
    target_date = Column(DateTime)
    measurement_criteria = Column(Text)  # How success is measured
    kpis = Column(JSON)  # Key performance indicators

    # Priority
    priority = Column(Integer)  # 1 - 5 scale

    # Source
    ai_generated = Column(Boolean, default=False)
    ai_confidence = Column(Float)

    # Relationships
    problem = relationship("SolutionProblemDefinition", back_populates="goals")
    driver = relationship("SolutionDriver", foreign_keys=[driver_id])

    __table_args__ = (Index("idx_solution_goal_problem", "problem_id"),)

    def __repr__(self):
        return f"<SolutionGoal {self.id}: {self.name}>"


VALID_REQ_TYPES = ['functional', 'non_functional', 'constraint', 'data', 'integration', 'compliance', 'operational']

VALID_APPROVAL_STATUSES = ['draft', 'in_review', 'approved', 'deferred', 'rejected']


class SolutionRequirement(TenantMixin, db.Model):
    """
    Functional and non-functional requirements.

    ArchiMate 3.2: Motivation Layer - Requirement element
    """

    __tablename__ = "solution_requirements"

    id = Column(Integer, primary_key=True)
    problem_id = Column(Integer, ForeignKey("solution_problem_definitions.id"), nullable=True)
    # Direct solution link — used in capability-based planning path (no analysis session required)
    solution_id = Column(Integer, ForeignKey("solutions.id", ondelete="CASCADE"), nullable=True, index=True)

    # Requirement details
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    requirement_type = Column(Enum(RequirementType), nullable=True)

    # Priority and criticality
    priority = Column(Integer)  # 1 - 5 scale (1=highest)
    is_mandatory = Column(Boolean, default=False)

    # Traceability
    source = Column(String(200))  # Originating document/stakeholder
    rationale = Column(Text)

    # Verification
    acceptance_criteria = Column(Text)

    # Workflow and ownership
    status = Column(String(50), nullable=True, default='open')
    owner = Column(String(200), nullable=True)
    assumptions = Column(Text, nullable=True)
    dependencies_text = Column(Text, nullable=True)

    # Capability-based planning traceability columns
    capability_id = Column(Integer, ForeignKey("business_capability.id", ondelete="SET NULL"), nullable=True, index=True)
    apqc_process_id = Column(Integer, ForeignKey("apqc_process.id", ondelete="SET NULL"), nullable=True)
    togaf_phase = Column(String(10), nullable=True)  # ADM phase code: A, B, C, D, E, F, G, H, REQ
    moscow_priority = Column(String(10), nullable=True)  # MUST, SHOULD, WONT, or optional

    # ArchiMate 3.2 Motivation Layer linkage
    # Stakeholder identifies Driver → Assessment → Goal → Requirement (this model)
    driver_id = Column(Integer, ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True, index=True)
    goal_id = Column(Integer, ForeignKey("goals.id", ondelete="SET NULL"), nullable=True, index=True)
    stakeholder_id = Column(Integer, ForeignKey("stakeholders.id", ondelete="SET NULL"), nullable=True, index=True)
    archimate_requirement_id = Column(Integer, ForeignKey("requirements.id", ondelete="SET NULL"), nullable=True)
    # archimate_requirement_id above points at the ENTERPRISE Requirement row,
    # not at an ArchiMate element. The backbone link is separate; see
    # app/services/archimate_backbone.py.
    archimate_element_id = Column(
        Integer, ForeignKey("archimate_elements.id", ondelete="SET NULL"),
        index=True, nullable=True,
    )
    # migration-exempt: nullable column, safe via db.create_all()
    principle_id = Column(Integer, ForeignKey("solution_principles.id", ondelete="SET NULL"), nullable=True, index=True)

    # Source
    ai_generated = Column(Boolean, default=False)
    ai_confidence = Column(Float)

    # JIRA integration
    jira_issue_key = db.Column(db.String(50), nullable=True)
    jira_push_status = db.Column(db.String(20), nullable=True, default='not_pushed')

    # User Story / Epic fields (TPM-003)
    epic_parent_id = db.Column(db.Integer, db.ForeignKey('solution_requirements.id', ondelete='SET NULL'), nullable=True)
    story_points = db.Column(db.Integer, nullable=True)
    dod_complete = db.Column(db.Boolean, nullable=False, default=False)
    item_type = db.Column(db.String(32), default='requirement')  # epic, story, sub_task, requirement

    # Prioritisation scoring (TPM-006)
    rice_reach = db.Column(db.Integer, nullable=True)       # 0-1000 users
    rice_impact = db.Column(db.Integer, nullable=True)      # 1,2,3 (Minimal/Medium/Massive)
    rice_confidence = db.Column(db.Integer, nullable=True)  # 20,50,80,100 percent
    rice_effort = db.Column(db.Integer, nullable=True)      # person-months * 10
    wsjf_cost_of_delay = db.Column(db.Integer, nullable=True)  # 1-21
    wsjf_job_duration = db.Column(db.Integer, nullable=True)   # 1-21
    dod_checklist = db.Column(db.JSON, nullable=True, default=list)

    # Architecture layer and template traceability (RRT-002)
    layer = db.Column(db.String(30), nullable=True)  # Business/Application/Technology/CrossCutting
    req_type = db.Column(db.String(30), nullable=True)   # functional/non_functional/constraint/data/integration/compliance/operational
    template_id = db.Column(db.Integer, db.ForeignKey('requirement_template.id', ondelete='SET NULL'), nullable=True)

    # Stakeholder and approval (PRQ-002)
    stakeholder_name = db.Column(db.String(200), nullable=True)   # who raised this requirement
    stakeholder_role = db.Column(db.String(100), nullable=True)   # e.g. Product Owner, Business Analyst
    source_document = db.Column(db.String(300), nullable=True)    # e.g. "PRD v2.docx", "workshop notes"
    approval_status = db.Column(db.String(20), nullable=True, default='draft')  # draft/in_review/approved/deferred/rejected
    approved_by_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)

    # Release scoping (PRQ-004)
    sprint_id = db.Column(db.String(50), nullable=True)      # Jira sprint ID or internal label
    milestone = db.Column(db.String(100), nullable=True)     # e.g. "Release 1.0", "MVP", "Phase 2"
    target_release_date = db.Column(db.Date, nullable=True)

    # Compliance tagging (PRQ-008)
    compliance_tags = db.Column(db.JSON, nullable=True, default=list)

    # Verification method — how the requirement will be tested
    # Values: automated_test, performance_test, penetration_test, load_test,
    #         dr_test, code_review, manual_review, api_contract_test
    # migration-exempt: nullable column, safe via db.create_all()
    verification_method = db.Column(db.String(50), nullable=True)

    # Roadmap traceability — link to kanban work package (REQ-013)
    work_package_id = Column(Integer, ForeignKey("kanban_cards.id", ondelete="SET NULL"), nullable=True, index=True)

    # Soft-delete support — prevents CASCADE DELETE data loss
    deleted_at = Column(DateTime, nullable=True, default=None)
    deleted_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    @property
    def rice_score(self):
        if all([self.rice_reach, self.rice_impact, self.rice_confidence, self.rice_effort]):
            return round((self.rice_reach * self.rice_impact * (self.rice_confidence / 100)) / self.rice_effort, 1)
        return None

    @property
    def wsjf_score(self):
        if self.wsjf_cost_of_delay and self.wsjf_job_duration and self.wsjf_job_duration > 0:
            return round(self.wsjf_cost_of_delay / self.wsjf_job_duration, 2)
        return None

    @property
    def is_deleted(self):
        return bool(self.deleted_at)

    def soft_delete(self, user_id=None):
        self.deleted_at = datetime.utcnow()
        if user_id is not None:
            self.deleted_by_id = user_id

    # Relationships
    problem = relationship("SolutionProblemDefinition", back_populates="requirements", foreign_keys=[problem_id])
    capability = relationship("BusinessCapability", foreign_keys=[capability_id])
    apqc_process = relationship("APQCProcess", foreign_keys=[apqc_process_id])
    driver = relationship("Driver", foreign_keys=[driver_id])
    goal = relationship("Goal", foreign_keys=[goal_id])
    stakeholder = relationship("Stakeholder", foreign_keys=[stakeholder_id])
    principle = relationship("SolutionPrinciple", foreign_keys=[principle_id])
    work_package = relationship("KanbanCard", foreign_keys=[work_package_id], lazy="select")
    approved_by = db.relationship('User', foreign_keys=[approved_by_id], lazy='select')
    epic_children = db.relationship(
        'SolutionRequirement',
        foreign_keys='SolutionRequirement.epic_parent_id',
        backref=db.backref('epic_parent', remote_side='SolutionRequirement.id'),
        lazy='dynamic',
    )

    __table_args__ = (
        Index("idx_solution_requirement_problem", "problem_id"),
        Index("idx_solution_requirement_type", "requirement_type"),
    )

    def __repr__(self):
        return f"<SolutionRequirement {self.id}: {self.name}>"


class SolutionConstraint(TenantMixin, db.Model):
    """
    Hard constraints limiting solution options.

    ArchiMate 3.2: Motivation Layer - Constraint element
    """

    __tablename__ = "solution_constraints"

    id = Column(Integer, primary_key=True)
    problem_id = Column(Integer, ForeignKey("solution_problem_definitions.id"), nullable=False)
    # The ArchiMate mirror of this row. Added 31 Aug 2026: CLAUDE.md requires
    # every motivation entity to have a matching ArchiMateElement, but this
    # model had nowhere to record one, so its creation paths could never
    # comply. Nullable and SET NULL so reconcile-schema can add it to
    # existing databases; populated automatically by
    # app/services/archimate_backbone.py.
    archimate_element_id = Column(
        Integer, ForeignKey("archimate_elements.id", ondelete="SET NULL"),
        index=True, nullable=True,
    )

    # Constraint details
    constraint_type = Column(Enum(ConstraintType), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)

    # Value and measurement
    value = Column(String(200))  # e.g., "£500K", "6 months", "GDPR"
    unit = Column(String(50))  # e.g., "GBP", "months", "compliance"

    # Impact
    severity = Column(Integer)  # 1 - 5 scale (5=hard constraint)

    # Source
    source = Column(String(200))
    ai_generated = Column(Boolean, default=False)

    # Relationships
    problem = relationship("SolutionProblemDefinition", back_populates="constraints")

    __table_args__ = (
        Index("idx_solution_constraint_problem", "problem_id"),
        Index("idx_solution_constraint_type", "constraint_type"),
    )

    def __repr__(self):
        return f"<SolutionConstraint {self.id}: {self.name}>"


class SolutionPrinciple(TenantMixin, db.Model):
    """
    Architecture principles guiding solution design.

    ArchiMate 3.2: Motivation Layer - Principle element
    """

    __tablename__ = "solution_principles"

    id = Column(Integer, primary_key=True)
    problem_id = Column(Integer, ForeignKey("solution_problem_definitions.id"), nullable=False)

    # Principle details
    name = Column(String(200), nullable=False)
    statement = Column(Text, nullable=False)
    rationale = Column(Text)  # Why this principle matters
    implications = Column(Text)  # Impact on solution design

    # Priority
    priority = Column(Integer)  # 1 - 5 scale

    # Source
    source = Column(String(200))  # e.g., "Enterprise Architecture", "CTO"
    ai_generated = Column(Boolean, default=False)
    ai_confidence = Column(Float)

    # Relationships
    problem = relationship("SolutionProblemDefinition", back_populates="principles")

    __table_args__ = (Index("idx_solution_principle_problem", "problem_id"),)


class RequirementDependency(db.Model):
    """PRQ-001: Structured dependency between two requirements."""
    __tablename__ = 'requirement_dependencies'

    id = db.Column(db.Integer, primary_key=True)
    req_id = db.Column(db.Integer, db.ForeignKey('solution_requirements.id', ondelete='CASCADE'), nullable=False, index=True)
    depends_on_id = db.Column(db.Integer, db.ForeignKey('solution_requirements.id', ondelete='CASCADE'), nullable=False, index=True)
    dependency_type = db.Column(db.String(20), nullable=False, default='relates')  # blocks / relates / duplicates
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    req = db.relationship('SolutionRequirement', foreign_keys=[req_id], backref=db.backref('outgoing_deps', lazy='dynamic', cascade='all, delete-orphan'))
    depends_on = db.relationship('SolutionRequirement', foreign_keys=[depends_on_id], backref=db.backref('incoming_deps', lazy='dynamic'))

    __table_args__ = (
        db.UniqueConstraint('req_id', 'depends_on_id', name='uq_req_dep'),
    )


class RequirementChangeLog(db.Model):
    """PRQ-006: Audit trail for requirement field changes."""
    __tablename__ = 'requirement_change_log'

    id = db.Column(db.Integer, primary_key=True)
    req_id = db.Column(db.Integer, db.ForeignKey('solution_requirements.id', ondelete='CASCADE'), nullable=False, index=True)
    changed_by_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    changed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    field_name = db.Column(db.String(60), nullable=False)   # e.g. 'acceptance_criteria', 'status'
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    change_type = db.Column(db.String(20), nullable=False, default='update')  # create / update / delete

    req = db.relationship('SolutionRequirement', foreign_keys=[req_id], backref=db.backref('change_logs', lazy='dynamic', cascade='all, delete-orphan'))
    changed_by = db.relationship('User', foreign_keys=[changed_by_id], lazy='select')

    def to_dict(self):
        return {
            'id': self.id,
            'req_id': self.req_id,
            'changed_by_id': self.changed_by_id,
            'changed_by_name': self.changed_by.full_name() if self.changed_by and hasattr(self.changed_by, 'full_name') else (str(self.changed_by_id) if self.changed_by_id else None),
            'changed_at': self.changed_at.isoformat() if self.changed_at else None,
            'field_name': self.field_name,
            'old_value': self.old_value,
            'new_value': self.new_value,
            'change_type': self.change_type
        }

    def __repr__(self):
        return f"<RequirementChangeLog {self.id}: {self.field_name}>"


class SolutionAssessment(TenantMixin, db.Model):
    """
    Current state assessment and gap analysis.

    ArchiMate 3.2: Motivation Layer - Assessment element
    """

    __tablename__ = "solution_assessments"

    id = Column(Integer, primary_key=True)
    problem_id = Column(Integer, ForeignKey("solution_problem_definitions.id"), nullable=False)

    # Assessment details
    aspect = Column(String(200), nullable=False)  # What is being assessed
    current_state = Column(Text, nullable=False)
    target_state = Column(Text, nullable=False)
    gap_analysis = Column(Text)

    # Severity
    gap_severity = Column(Integer)  # 1 - 5 scale (5=critical gap)

    # Metadata
    assessed_by = Column(String(200))
    assessed_at = Column(DateTime, default=datetime.utcnow)
    ai_generated = Column(Boolean, default=False)

    # Relationships
    problem = relationship("SolutionProblemDefinition", back_populates="assessments")

    __table_args__ = (Index("idx_solution_assessment_problem", "problem_id"),)

    def __repr__(self):
        return f"<SolutionAssessment {self.id}: {self.aspect}>"


# ============================================================================
# RECOMMENDATIONS & RESULTS
# ============================================================================


class SolutionRecommendation(TenantMixin, db.Model):
    """
    Persistent recommendations from solution analysis.

    Stores AI-generated recommendations with scoring and justification.
    """

    __tablename__ = "solution_recommendations"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("solution_analysis_sessions.id"), nullable=False)

    # Recommendation type and ranking
    option_type = Column(Enum(RecommendationOptionType), nullable=False)
    rank = Column(Integer)  # 1 - N ranking
    score = Column(Float)  # 0 - 100 score
    confidence = Column(Float)  # 0 - 1 AI confidence

    # Financial estimates
    estimated_cost_min = Column(Numeric(15, 2))
    estimated_cost_max = Column(Numeric(15, 2))
    cost_currency = Column(String(3), default="GBP")

    # Timeline estimate
    timeline_months = Column(Integer)

    # Detailed analysis (JSON)
    pros = Column(JSON)  # List of advantages
    cons = Column(JSON)  # List of disadvantages
    risks = Column(JSON)  # List of risks with severity
    next_steps = Column(JSON)  # Action items

    # Supporting evidence
    justification = Column(Text)
    data_sources = Column(JSON)  # What data/analysis led to this

    # Display name and selection
    name = Column(String(200))  # User-friendly option name
    is_recommended = Column(Boolean, default=False)  # Architect's pick

    # Related entities
    vendor_products = Column(JSON)  # List of vendor product IDs if BUY
    existing_apps = Column(JSON)  # List of app IDs if REUSE

    # Metadata
    generated_at = Column(DateTime, default=datetime.utcnow)
    generated_by_model = Column(String(100))  # Which AI model generated this

    # Relationships
    session = relationship("SolutionAnalysisSession", back_populates="recommendations")

    __table_args__ = (
        Index("idx_solution_rec_session", "session_id"),
        Index("idx_solution_rec_type", "option_type"),
        Index("idx_solution_rec_rank", "rank"),
    )

    def __repr__(self):
        return f"<SolutionRecommendation {self.id}: {self.option_type.value} (rank {self.rank})>"


# ============================================================================
# VERSION MANAGEMENT
# ============================================================================


class SolutionSessionVersion(db.Model):
    """
    Version history for solution analysis sessions.

    Enables comparison of different analysis runs and decision tracking.
    """

    __tablename__ = "solution_session_versions"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("solution_analysis_sessions.id"), nullable=False)

    # Version details
    version_number = Column(Integer, nullable=False)
    version_name = Column(String(200))  # Optional descriptive name
    description = Column(Text)  # What changed in this version

    # Snapshot of complete session state
    snapshot = Column(JSON, nullable=False)  # Complete problem + recommendations

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Relationships
    session = relationship("SolutionAnalysisSession", back_populates="versions")
    created_by = relationship("User", foreign_keys=[created_by_id])

    __table_args__ = (
        UniqueConstraint("session_id", "version_number", name="uq_session_version"),
        Index("idx_solution_version_session", "session_id"),
        Index("idx_solution_version_created_at", "created_at"),
    )

    def __repr__(self):
        return (
            f"<SolutionSessionVersion {self.id}: Session {self.session_id} v{self.version_number}>"
        )


# ============================================================================
# ADR INTEGRATION
# ============================================================================


class SolutionADRLink(TenantMixin, db.Model):
    """
    Links solution analysis sessions to architecture decisions.

    Provides traceability from problem to decision. adr_id points at the
    canonical architecture_decisions table, not the superseded
    architecture_decision_records -- the latter takes no new writes and
    nothing creating a link today has a row there to point at.
    """

    __tablename__ = "solution_adr_links"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("solution_analysis_sessions.id"), nullable=False)
    adr_id = Column(Integer, ForeignKey("architecture_decisions.id"), nullable=False)

    # Relationship type
    relationship_type = Column(String(50), default="informs")  # informs, implements, traces_to
    notes = Column(Text)

    # Metadata
    linked_at = Column(DateTime, default=datetime.utcnow)
    linked_by_id = Column(Integer, ForeignKey("users.id"))

    # Relationships
    session = relationship("SolutionAnalysisSession", back_populates="adr_links")
    adr = relationship("ArchitectureDecision")
    linked_by = relationship("User", foreign_keys=[linked_by_id])

    __table_args__ = (
        UniqueConstraint("session_id", "adr_id", name="uq_session_adr"),
        Index("idx_solution_adr_session", "session_id"),
        Index("idx_solution_adr_adr", "adr_id"),
    )

    def __repr__(self):
        return f"<SolutionADRLink {self.id}: Session {self.session_id} -> ADR {self.adr_id}>"


# ============================================================================
# ENH-014: Late-bind roadmap_initiatives relationship onto Solution model
# ============================================================================

def _bind_solution_roadmap_relationship():
    """Attach many-to-many roadmap_initiatives relationship to Solution.

    Uses late-binding so we only need to edit solution_architect_models.py
    (within the allowed file_paths) rather than solution_models.py.
    """
    from app.models.implementation_migration import TechnologyRoadmapInitiative  # noqa: F401

    if not hasattr(Solution, "roadmap_initiatives"):
        Solution.roadmap_initiatives = db.relationship(
            "TechnologyRoadmapInitiative",
            secondary=solution_roadmap_initiatives,
            backref=db.backref("linked_solutions", lazy="dynamic"),
            lazy="dynamic",
        )


_bind_solution_roadmap_relationship()
