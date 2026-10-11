"""
ADM Kanban Models - TOGAF ADM Phase Tracking with ArchiMate Integration
"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.ext.declarative import declared_attr  # dead-code-ok
from sqlalchemy.orm import relationship

from app import db
from app.models.mixins.core import TenantMixin
from app.services.sap_activate_mapping import get_activate_stage

VALID_BOARD_TYPES = ['architecture_development', 'change_management']
VALID_PHASE_H_TRIGGER_TYPES = ['business_event', 'technology_eol', 'regulation', 'org_change', 'market', 'performance']
VALID_PHASE_H_DISPOSITIONS = ['full_adm_cycle', 'incremental_update', 'exception', 'rejected', 'deferred']


class ADMPhase(db.Model):
    """TOGAF ADM Phase definitions with comprehensive methodology metadata"""

    __tablename__ = "adm_phases"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    code = Column(String(10), nullable=False, unique=True)
    description = Column(Text)
    order = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True)

    # TOGAF Methodology Metadata
    togaf_objectives = Column(Text)
    required_inputs = Column(JSON)
    expected_outputs = Column(JSON)
    architecture_board_gate_criteria = Column(Text)
    governance_checkpoints = Column(JSON)

    # Relationships
    kanban_cards = relationship("KanbanCard", back_populates="adm_phase")
    phase_steps = relationship("ADMPhaseStep", back_populates="adm_phase", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ADMPhase {self.code}: {self.name}>"


class KanbanBoard(TenantMixin, db.Model):
    """Kanban board for tracking ADM phases"""

    __tablename__ = "kanban_boards"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)

    # Project/Architecture context
    project_name = Column(String(200))

    # ADM-specific fields
    current_adm_phase = Column(String(10))  # Current phase code (A, B, C, etc.)

    # Enterprise Architecture Integration
    application_ids = Column(JSON)  # List of application IDs this board relates to
    system_ids = Column(JSON)  # List of system IDs this board relates to
    initiative_ids = Column(JSON)  # List of business initiative IDs
    project_ids = Column(JSON)  # List of project IDs

    # Architect role targeting
    primary_architect_role = Column(
        String(50)
    )  # enterprise, business, solutions, application, system, integration

    # ARB-003: board type distinguishes architecture development from Phase H change management
    board_type = Column(String(30), nullable=False, default='architecture_development')
    # Values: 'architecture_development' (default ADM work) | 'change_management' (Phase H)

    # Phase H specific fields (only used when board_type = 'change_management')
    phase_h_trigger_type = Column(String(50), nullable=True)
    # Values: business_event / technology_eol / regulation / org_change / market / performance
    phase_h_disposition = Column(String(30), nullable=True)
    # Values: full_adm_cycle / incremental_update / exception / rejected / deferred

    # Audit fields
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    cards = relationship("KanbanCard", back_populates="board", cascade="all, delete-orphan")
    created_by = relationship("User", foreign_keys=[created_by_id], backref="kanban_boards")

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'board_type': self.board_type,
            'phase_h_trigger_type': self.phase_h_trigger_type,
            'phase_h_disposition': self.phase_h_disposition,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<KanbanBoard {self.name}>"


class KanbanCard(TenantMixin, db.Model):
    """Individual kanban cards representing architectural work items"""

    __tablename__ = "kanban_cards"
    __table_args__ = {"extend_existing": True}

    # KanbanBoard carries TenantMixin; KanbanCard did not. Cards were therefore
    # protected only when reached *through* a board — a direct
    # `KanbanCard.query.get(id)` or `.filter_by(...)` returned any tenant's card,
    # and card titles, descriptions and assignees are business content.
    #
    # Nullable for the usual reason: kanban_cards is an existing table and
    # reconcile-schema can only ADD nullable columns (ADR 0002). Isolation keys
    # on isinstance(TenantMixin), not on nullability.
    #
    # Unlike the other backfills in this series, this one does not have to guess
    # or ask: board_id is NOT NULL and boards are already scoped, so each card's
    # true owner is derivable exactly.
    #     flask --app manage backfill-kanban-card-org
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    id = Column(Integer, primary_key=True)
    title = Column(String(300), nullable=False)
    description = Column(Text)

    # ADM Phase tracking
    adm_phase_id = Column(Integer, ForeignKey("adm_phases.id"), nullable=False, index=True)
    board_id = Column(Integer, ForeignKey("kanban_boards.id"), nullable=False, index=True)

    # Work item type (business requirement, technical design, etc.)
    card_type = Column(String(50), nullable=False)  # requirement, design, implementation, etc.

    # Enterprise Architecture Integration
    application_ids = Column(JSON)  # List of application IDs this card affects/implements
    system_ids = Column(JSON)  # List of system IDs this card affects/implements
    initiative_ids = Column(JSON)  # List of business initiative IDs this card supports

    # ArchiMate element relationships (expanded)
    affects_applications = Column(JSON)  # Application IDs this card affects
    affects_systems = Column(JSON)  # System IDs this card affects
    implements_capabilities = Column(JSON)  # Capability IDs this card implements

    # ArchiMate element IDs from the archimate_elements table
    archimate_element_ids = Column(JSON, default=list)  # List of ArchiMateElement.id values

    # Status and priority
    status = Column(String(50), default="todo")  # backlog, todo, in_progress, review, done
    priority = Column(String(20), default="medium")  # low, medium, high, critical

    # Dependencies
    depends_on = Column(JSON)  # List of card IDs this depends on
    blocks = Column(JSON)  # List of card IDs this blocks

    # Assignment and ownership
    assigned_to_id = Column(Integer, ForeignKey("users.id"), index=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # ARB integration (optional link to review items)
    arb_review_id = Column(Integer, ForeignKey("arb_review_items.id"), index=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # ArchiMate 3.2 / TOGAF ADM semantic classification (ADM-001)
    # Replaces the generic card_type for EA-meaningful work item categorisation
    arch_element_type = Column(
        String(50), nullable=True
    )  # WorkPackage | Plateau | Gap | ImplementationEvent | Deliverable
    arch_domain = Column(
        String(50), nullable=True
    )  # Business | Data | Application | Technology | Cross-cutting
    togaf_deliverable = Column(
        String(100), nullable=True
    )  # e.g. "Architecture Vision", "Architecture Contract"
    closes_gap_id = Column(
        Integer, ForeignKey("kanban_cards.id", ondelete="SET NULL"), nullable=True, index=True
    )  # Gap card this WorkPackage resolves
    requires_arb_signoff = Column(Boolean, default=False, nullable=False)
    target_plateau_id = Column(
        Integer, ForeignKey("kanban_cards.id", ondelete="SET NULL"), nullable=True, index=True
    )  # Plateau card this work delivers toward

    # Jira integration — populated after successful push
    jira_issue_key = Column(String(50), nullable=True, unique=True, index=True)   # e.g. "ARCH-142"
    jira_push_status = Column(String(50), nullable=True)  # "pushed" | "failed" | NULL (not yet pushed)
    jira_subtask_key = Column(String(50), nullable=True, index=True)   # Jira Subtask key for depends_on link

    # Gantt linkage (GNT-001)
    target_start_date = Column(DateTime, nullable=True)
    target_end_date = Column(DateTime, nullable=True)
    work_package_id = Column(Integer, ForeignKey('roadmap_work_packages.id', ondelete='SET NULL'), nullable=True, index=True)
    # The one work package store. work_package_id above is the retired
    # roadmap store's id and is only read to find a merged row.
    unified_work_package_id = Column(
        BigInteger, ForeignKey('unified_work_packages.id', ondelete='SET NULL'), nullable=True, index=True
    )

    # ArchiMate 3.2 Motivation linkage
    requirement_ids = db.Column(db.JSON, nullable=True)
    goal_ids = db.Column(db.JSON, nullable=True)
    driver_ids = db.Column(db.JSON, nullable=True)
    principle_ids = db.Column(db.JSON, nullable=True)

    # Jira fields
    issue_type = db.Column(db.String(50), nullable=True)
    assignee = db.Column(db.String(200), nullable=True)
    story_points = db.Column(db.Integer, nullable=True)
    labels = db.Column(db.JSON, nullable=True)
    acceptance_criteria = db.Column(db.Text, nullable=True)

    # Effort actuals, pulled from Jira. Only assignee/status/story_points were
    # ever synced, so nothing in the platform could observe how much effort a
    # piece of work had actually consumed — which is why EnterpriseInitiative's
    # spent_to_date was a column nothing ever wrote. Jira reports both in
    # seconds; kept in seconds to avoid a lossy conversion on the way in.
    # Nullable: existing rows have no history, and "no worklog" must stay
    # distinguishable from "zero effort".
    time_spent_seconds = db.Column(db.Integer, nullable=True)
    time_estimate_seconds = db.Column(db.Integer, nullable=True)
    effort_synced_at = db.Column(db.DateTime, nullable=True)

    # Effort has to reach an initiative for spend to be computable, and it could
    # not. `work_package_id` above points at roadmap_work_packages, which stores
    # business_capability as a String and carries no foreign keys at all — a dead
    # end. The canonical work package is the ArchiMate Implementation & Migration
    # element in `work_packages`, which does link to the initiative. This is the
    # missing hop: card -> work package -> initiative.
    implementation_work_package_id = db.Column(
        db.Integer, db.ForeignKey("work_packages.id", ondelete="SET NULL"), nullable=True, index=True
    )

    @property
    def time_spent_hours(self):
        """Effort actually logged, in hours. None when Jira reported nothing."""
        if self.time_spent_seconds is None:
            return None
        return round(self.time_spent_seconds / 3600.0, 2)

    @property
    def effort_variance_pct(self):
        """Logged vs originally estimated. None unless both are known."""
        if not self.time_estimate_seconds or self.time_spent_seconds is None:
            return None
        return round(
            (self.time_spent_seconds - self.time_estimate_seconds)
            / self.time_estimate_seconds * 100,
            1,
        )

    # Gantt / progress
    arch_layer = db.Column(db.String(50), nullable=True)
    progress_pct = db.Column(db.Integer, nullable=True, default=0)

    # Workflow integration — links card to a running EA workflow instance
    workflow_instance_id = Column(
        Integer,
        ForeignKey("ea_workflow_instances.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    adm_phase = relationship("ADMPhase", back_populates="kanban_cards")
    board = relationship("KanbanBoard", back_populates="cards")
    assigned_to = relationship(
        "User", foreign_keys=[assigned_to_id], backref="assigned_kanban_cards"
    )
    created_by = relationship("User", foreign_keys=[created_by_id], backref="created_kanban_cards")
    workflow_instance = relationship(
        "EAWorkflowInstance",
        foreign_keys=[workflow_instance_id],
        backref=db.backref("workflow_kanban_cards", lazy="dynamic"),
    )
    # Self-referential ArchiMate relationships (ADM-001)
    closes_gap = relationship(
        "KanbanCard", foreign_keys=[closes_gap_id], remote_side="KanbanCard.id",
        backref="resolved_by", uselist=False,
    )
    target_plateau = relationship(
        "KanbanCard", foreign_keys=[target_plateau_id], remote_side="KanbanCard.id",
        backref="delivering_cards", uselist=False,
    )

    def __repr__(self):
        return f"<KanbanCard {self.title} ({self.adm_phase.code if self.adm_phase else 'Unknown'})>"


class KanbanCardComment(db.Model):
    """Comments and updates on kanban cards"""

    __tablename__ = "kanban_card_comments"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey("kanban_cards.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    comment = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    card = relationship("KanbanCard", backref="comments")
    user = relationship("User", backref="kanban_comments")

    def __repr__(self):
        return f"<KanbanCardComment by {self.user.full_name() if self.user else 'Unknown'}>"


class KanbanCardAttachment(db.Model):
    """File attachments for kanban cards"""

    __tablename__ = "kanban_card_attachments"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey("kanban_cards.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_type = Column(String(50))
    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    card = relationship("KanbanCard", backref="attachments")
    uploaded_by = relationship("User", backref="kanban_attachments")

    def __repr__(self):
        return f"<KanbanCardAttachment {self.filename}>"


class ADMPhaseStep(db.Model):
    """Individual steps/activities within an ADM Phase (TOGAF-compliant)"""

    __tablename__ = "adm_phase_steps"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    phase_id = Column(Integer, ForeignKey("adm_phases.id"), nullable=False, index=True)

    # Step metadata
    step_number = Column(Integer, nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text)

    # TOGAF-specific fields
    activity_type = Column(String(50))
    inputs_required = Column(JSON)
    outputs_produced = Column(JSON)
    techniques_approaches = Column(JSON)

    # Governance
    is_mandatory = Column(Boolean, default=False)
    requires_approval = Column(Boolean, default=False)
    approval_gate = Column(String(100))

    # Ordering and status
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    adm_phase = relationship("ADMPhase", back_populates="phase_steps")

    def __repr__(self):
        return f"<ADMPhaseStep {self.step_number}: {self.name}>"


# ADM Phase definitions
ADM_PHASES = [
    {
        "code": "PRELIM",
        "name": "Preliminary Phase",
        "order": 1,
        "description": "Framework and principles understanding, organizational readiness",
        "togaf_objectives": "Define organization expecting to execute ADM, identify and scope architecture assets, identify architecture principles",
        "required_inputs": ["TOGAF Library", "Other frameworks", "Strategic plans", "Organizational model"],
        "expected_outputs": ["Tailored ADM", "Architecture Principles", "Repository structure"],
        "architecture_board_gate_criteria": "Principles approved by leadership, Repository established",
        "governance_checkpoints": ["Maturity baseline established", "Principles concurrence"]
    },
    {
        "code": "A",
        "name": "Phase A: Architecture Vision",
        "order": 2,
        "description": "Define scope, stakeholders, business requirements, and architecture vision",
        "togaf_objectives": "Obtain approval for Statement of Architecture Work, define baseline and target architecture scope",
        "required_inputs": ["Repository baseline", "Principles", "Request for Architecture Work"],
        "expected_outputs": ["Statement of Architecture Work approved", "Architecture scope", "Capability Assessment"],
        "architecture_board_gate_criteria": "Statement of Architecture Work approved, stakeholder map defined",
        "governance_checkpoints": ["Architecture Board approval", "Risk register acknowledged"]
    },
    {
        "code": "B",
        "name": "Phase B: Business Architecture",
        "order": 3,
        "description": "Develop target business architecture to support agreed architecture vision",
        "togaf_objectives": "Develop target Business Architecture describing how enterprise needs to operate",
        "required_inputs": ["Statement of Architecture Work", "Capability Assessment", "Business Strategy"],
        "expected_outputs": ["Target Business Architecture", "Gap analysis results", "Candidate roadmap"],
        "architecture_board_gate_criteria": "Target Business Architecture approved, gap analysis reviewed",
        "governance_checkpoints": ["Architecture Board review", "Business stakeholder validation"]
    },
    {
        "code": "C",
        "name": "Phase C: Information Systems Architectures",
        "order": 4,
        "description": "Develop target data and application architectures",
        "togaf_objectives": "Develop target Data and Application Architectures to support business architecture",
        "required_inputs": ["Statement of Architecture Work", "Business Architecture"],
        "expected_outputs": ["Data Architecture", "Application Architecture", "Gap analysis"],
        "architecture_board_gate_criteria": "Data and Application Architectures approved",
        "governance_checkpoints": ["Architecture Board approval", "IT stakeholder concurrence"]
    },
    {
        "code": "D",
        "name": "Phase D: Technology Architecture",
        "order": 5,
        "description": "Develop target technology architecture",
        "togaf_objectives": "Develop target Technology Architecture describing technology infrastructure",
        "required_inputs": ["Statement of Architecture Work", "Application Architecture", "Technology standards"],
        "expected_outputs": ["Target Technology Architecture", "Gap analysis", "Standards compliance report"],
        "architecture_board_gate_criteria": "Technology Architecture approved, standards compliance verified",
        "governance_checkpoints": ["Architecture Board review", "Infrastructure team concurrence"]
    },
    {
        "code": "E",
        "name": "Phase E: Opportunities & Solutions",
        "order": 6,
        "description": "Identify implementation projects, migration planning",
        "togaf_objectives": "Identify implementation projects and group into work packages",
        "required_inputs": ["Statement of Architecture Work", "All architecture descriptions"],
        "expected_outputs": ["Consolidated gaps and solutions", "Architectural Roadmap", "Transition Architectures"],
        "architecture_board_gate_criteria": "Implementation strategy approved, work packages defined",
        "governance_checkpoints": ["Implementation strategy review", "Business priority alignment"]
    },
    {
        "code": "F",
        "name": "Phase F: Migration Planning",
        "order": 7,
        "description": "Develop detailed implementation and migration plan",
        "togaf_objectives": "Prioritize implementation projects and develop detailed Implementation and Migration Plan",
        "required_inputs": ["Statement of Architecture Work", "Migration Strategy", "Work packages"],
        "expected_outputs": ["Implementation and Migration Plan", "Project charters"],
        "architecture_board_gate_criteria": "Implementation and Migration Plan approved, project charters established",
        "governance_checkpoints": ["Migration plan approved", "Project governance established"]
    },
    {
        "code": "G",
        "name": "Phase G: Implementation Governance",
        "order": 8,
        "description": "Provide architecture oversight for implementation",
        "togaf_objectives": "Provide architecture oversight for implementation projects",
        "required_inputs": ["Statement of Architecture Work", "Implementation and Migration Plan", "Architecture contracts"],
        "expected_outputs": ["Architecture contracts signed", "Compliance assessments", "Change requests disposition"],
        "architecture_board_gate_criteria": "Implementation compliance verified, deviation requests approved",
        "governance_checkpoints": ["Compliance assessments completed", "Architecture Board oversight"]
    },
    {
        "code": "H",
        "name": "Phase H: Architecture Change Management",
        "order": 9,
        "description": "Manage changes to architecture framework and principles",
        "togaf_objectives": "Manage changes to architecture framework and principles, manage new architecture requirements",
        "required_inputs": ["Current architecture descriptions", "Change requests", "Post-implementation reviews"],
        "expected_outputs": ["Architecture updates", "Architecture Change Request disposition", "Architecture compliance reviews"],
        "architecture_board_gate_criteria": "Change management process approved, change impact assessed",
        "governance_checkpoints": ["Change impact assessment", "Architecture Board change approval"]
    },
    {
        "code": "REQ",
        "name": "Requirements Management",
        "order": 10,
        "description": "Ongoing management of requirements throughout ADM cycle",
        "togaf_objectives": "Manage architecture requirements throughout ADM cycle",
        "required_inputs": ["Requirements from all phases", "Stakeholder concerns", "Assumptions and constraints"],
        "expected_outputs": ["Requirements impact assessment", "Requirements repository", "Requirements traceability"],
        "architecture_board_gate_criteria": "Requirements management process established, traceability verified",
        "governance_checkpoints": ["Requirements baseline established", "Requirements change control"]
    },
]

for _phase_data in ADM_PHASES:
    _phase_data["activate_stage"] = get_activate_stage(_phase_data["code"])
del _phase_data


def create_adm_phases():
    """Initialize ADM phases in database with TOGAF methodology metadata"""
    for phase_data in ADM_PHASES:
        phase = ADMPhase.query.filter_by(code=phase_data["code"]).first()
        if not phase:
            phase = ADMPhase(
                name=phase_data["name"],
                code=phase_data["code"],
                description=phase_data.get("description"),
                order=phase_data["order"],
                togaf_objectives=phase_data.get("togaf_objectives"),
                required_inputs=phase_data.get("required_inputs"),
                expected_outputs=phase_data.get("expected_outputs"),
                architecture_board_gate_criteria=phase_data.get("architecture_board_gate_criteria"),
                governance_checkpoints=phase_data.get("governance_checkpoints"),
            )
            db.session.add(phase)
        else:
            # Update existing phases with new TOGAF metadata
            phase.togaf_objectives = phase_data.get("togaf_objectives")
            phase.required_inputs = phase_data.get("required_inputs")
            phase.expected_outputs = phase_data.get("expected_outputs")
            phase.architecture_board_gate_criteria = phase_data.get("architecture_board_gate_criteria")
            phase.governance_checkpoints = phase_data.get("governance_checkpoints")
    db.session.commit()


# ArchiMate 3.2 Element Types for card tagging
ARCHIMATE_ELEMENTS = {
    "strategy": ["capability", "resource", "course_of_action", "value_stream"],
    "business": [
        "business_actor",
        "business_role",
        "business_collaboration",
        "business_interface",
        "business_process",
        "business_function",
        "business_service",
        "business_event",
        "business_object",
        "contract",
        "product",
        "business_interaction",
        "value",
    ],
    "application": [
        "application_component",
        "application_collaboration",
        "application_interface",
        "application_service",
        "application_function",
        "application_interaction",
        "application_process",
        "application_event",
        "data_object",
    ],
    "technology": [
        "node",
        "device",
        "system_software",
        "technology_collaboration",
        "technology_interface",
        "path",
        "communication_network",
        "technology_service",
        "technology_function",
        "artifact",
    ],
    "physical": [
        "equipment",
        "facility",
        "distribution_network",
        "material",
    ],
    "composite": [
        "grouping",
        "location",
    ],
    "motivation": [
        "stakeholder",
        "driver",
        "assessment",
        "goal",
        "outcome",
        "principle",
        "requirement",
        "constraint",
        "meaning",
        "value",
    ],
    "implementation": ["work_package", "deliverable", "implementation_event", "plateau", "gap"],
}


KANBAN_STATUSES = [
    {"id": "backlog", "label": "Backlog", "icon": "inbox", "color": "muted-foreground"},
    {"id": "todo", "label": "To Do", "icon": "circle", "color": "primary"},
    {"id": "in_progress", "label": "In Progress", "icon": "loader", "color": "warning"},
    {"id": "review", "label": "In Review", "icon": "eye", "color": "info"},
    {"id": "done", "label": "Done", "icon": "check-circle", "color": "success"},
]
