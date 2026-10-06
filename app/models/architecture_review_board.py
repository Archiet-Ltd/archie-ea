"""
Architecture Review Board (ARB) Models

Implements TOGAF-aligned Architecture Review Board process for enterprise governance.
Integrates with ArchiMate 3.2 viewpoints and capability-based planning.

TOGAF ADM Phases Supported:
- Phase A: Architecture Vision
- Phase B: Business Architecture
- Phase C: Information Systems Architecture
- Phase D: Technology Architecture
- Phase E: Opportunities and Solutions
- Phase F: Migration Planning
- Phase G: Implementation Governance
- Phase H: Architecture Change Management

ArchiMate 3.2 Viewpoints for Review:
- Motivation Viewpoint
- Strategy Viewpoint
- Business Layer Viewpoints
- Application Layer Viewpoints
- Technology Layer Viewpoints
- Implementation & Migration Viewpoints
"""

import logging
import os
import re
import uuid
from datetime import datetime, timedelta  # dead-code-ok
from enum import Enum
from typing import Any, Dict, List, Optional  # dead-code-ok

from sqlalchemy import event  # dead-code-ok
from sqlalchemy.ext.hybrid import hybrid_property  # dead-code-ok
from sqlalchemy.orm import declared_attr

from .. import db
from .mixins import OptimisticLockMixin, TenantMixin

logger = logging.getLogger(__name__)

_FAST_INIT = os.getenv("APP_FAST_INIT", "0") == "1"


class ARBReviewStatus(str, Enum):
    """Status of an ARB review."""

    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    PENDING_INFO = "pending_information"
    APPROVED = "approved"
    APPROVED_WITH_CONDITIONS = "approved_with_conditions"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    WITHDRAWN = "withdrawn"


# These are the state groups used by operational ARB reporting.  Keeping them
# next to the canonical status vocabulary prevents each dashboard from quietly
# choosing a different meaning for "open".
ARB_OPEN_STATUSES = frozenset(
    {
        ARBReviewStatus.SUBMITTED.value,
        ARBReviewStatus.UNDER_REVIEW.value,
        ARBReviewStatus.PENDING_INFO.value,
        "pending_info",  # legacy spelling present in existing ARB records
        "pending",       # legacy queue spelling present in existing ARB records
    }
)
ARB_BLOCKED_OR_NOT_READY_STATUSES = frozenset(
    {
        ARBReviewStatus.PENDING_INFO.value,
        "pending_info",
        ARBReviewStatus.DEFERRED.value,
    }
)
ARB_DECIDED_STATUSES = frozenset(
    {
        ARBReviewStatus.APPROVED.value,
        ARBReviewStatus.APPROVED_WITH_CONDITIONS.value,
        ARBReviewStatus.REJECTED.value,
        ARBReviewStatus.DEFERRED.value,
    }
)
ARB_REVIEW_SLA_DAYS = 21


class TOGAFPhase(str, Enum):
    """TOGAF ADM Phases."""

    PRELIMINARY = "preliminary"
    PHASE_A = "phase_a_vision"
    PHASE_B = "phase_b_business"
    PHASE_C = "phase_c_information_systems"
    PHASE_D = "phase_d_technology"
    PHASE_E = "phase_e_opportunities"
    PHASE_F = "phase_f_migration"
    PHASE_G = "phase_g_implementation"
    PHASE_H = "phase_h_change_management"
    REQUIREMENTS_MANAGEMENT = "requirements_management"


class ARBExceptionStatus(str, Enum):
    """Status values for ARB exception requests."""

    REQUESTED = "requested"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ReviewType(str, Enum):
    """Types of architecture reviews."""

    SOLUTION_DESIGN = "solution_design"
    ARCHITECTURE_CHANGE = "architecture_change"
    TECHNOLOGY_SELECTION = "technology_selection"
    CAPABILITY_IMPLEMENTATION = "capability_implementation"
    INTEGRATION_PATTERN = "integration_pattern"
    SECURITY_REVIEW = "security_review"
    COMPLIANCE_REVIEW = "compliance_review"
    EXCEPTION_REQUEST = "exception_request"
    STANDARD_DEVIATION = "standard_deviation"
    RETIREMENT_REVIEW = "retirement_review"


class ARBAuditAction(str, Enum):
    """Audit actions for ARB entities."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    STATUS_CHANGE = "status_change"
    DECISION = "decision"
    ASSIGNMENT = "assignment"
    SCORE_UPDATE = "score_update"
    COMMENT_ADD = "comment_add"
    EXCEPTION_REQUEST = "exception_request"
    EXCEPTION_DECISION = "exception_decision"
    READINESS_CHECK = "readiness_check"
    DECISION_REOPEN = "decision_reopen"


class ARBAuditLog(TenantMixin, db.Model):
    """Lightweight audit log model for ARB actions.

    This is a compact compatibility shim used by services that expect
    an ARBAuditLog model to exist. It intentionally contains only the
    fields required by the audit service.
    """

    __tablename__ = "arb_audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    # Phase B (Wave 4): TenantMixin enabled — backfill completed in Phase A.
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True, index=True)
    entity_type = db.Column(db.String(100), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)
    entity_reference = db.Column(db.String(255))
    action = db.Column(db.String(50), nullable=False)
    action_description = db.Column(db.Text)
    old_value = db.Column(db.JSON)
    new_value = db.Column(db.JSON)
    changed_fields = db.Column(db.JSON)
    user_id = db.Column(db.Integer)
    user_email = db.Column(db.String(255))
    ip_address = db.Column(db.String(100))
    user_agent = db.Column(db.String(500))
    request_id = db.Column(db.String(100))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    # Set when this row has been copied into the one audit store
    # (soc2_audit_log); points at the copy. NULL until copied.
    retired_into_id = db.Column(db.BigInteger, db.ForeignKey("soc2_audit_log.id"), nullable=True)


class ARBException(TenantMixin, db.Model):
    """Exception request model for architecture governance standards."""

    __tablename__ = "arb_exceptions"

    id = db.Column(db.Integer, primary_key=True)
    # Phase B (Wave 4): TenantMixin enabled — backfill completed in Phase A.
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True, index=True)
    exception_number = db.Column(db.String(50), unique=True)
    standard_id = db.Column(db.Integer)
    exception_type = db.Column(db.String(100))
    status = db.Column(db.String(50))

    # Request tracking
    business_justification = db.Column(db.Text)
    risk_mitigation = db.Column(db.Text)
    scope = db.Column(db.Text)
    exception_reason = db.Column(db.Text)
    review_item_id = db.Column(db.Integer)

    # Requester
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    requested_at = db.Column(db.DateTime)

    # Review
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    review_notes = db.Column(db.Text)

    # Approval
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    approval_notes = db.Column(db.Text)

    # Denial
    denied_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    denied_at = db.Column(db.DateTime)
    denial_reason = db.Column(db.Text)

    # Revocation
    revoked_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    revoked_at = db.Column(db.DateTime)
    revocation_reason = db.Column(db.Text)

    # Expiration and renewal
    expires_at = db.Column(db.DateTime)
    parent_exception_id = db.Column(db.Integer, db.ForeignKey("arb_exceptions.id"))
    renewal_count = db.Column(db.Integer, default=0)
    reminder_sent_at = db.Column(db.DateTime)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    requester = db.relationship(
        "User", foreign_keys=[requested_by_id], backref="requested_exceptions"
    )
    reviewer = db.relationship("User", foreign_keys=[reviewed_by_id], backref="reviewed_exceptions")
    approver = db.relationship("User", foreign_keys=[approved_by_id], backref="approved_exceptions")
    denier = db.relationship("User", foreign_keys=[denied_by_id], backref="denied_exceptions")
    parent = db.relationship("ARBException", remote_side=[id], backref="renewals")


# Default workflow stages and lightweight ARBWorkflowStage model
DEFAULT_WORKFLOW_STAGES = [
    {"code": "draft", "name": "Draft", "order": 1},
    {"code": "submitted", "name": "Submitted", "order": 2},
    {"code": "under_review", "name": "Under Review", "order": 3},
    {"code": "approved", "name": "Approved", "order": 4},
    {"code": "rejected", "name": "Rejected", "order": 5},
]


# Global reference data (shared across tenants) — intentionally NOT TenantMixin; org column unused. See wave-4 Task-2 review.
class ARBWorkflowStage(db.Model):
    """Lightweight workflow stage model for ARB processes."""

    __tablename__ = "arb_workflow_stages"

    id = db.Column(db.Integer, primary_key=True)
    # Global reference data (shared across tenants) — intentionally NOT TenantMixin; org column unused. See wave-4 Task-2 review.
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True, index=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False)
    order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {"id": self.id, "code": self.code, "name": self.name, "order": self.order}

    def can_transition_to(self, target_stage: "ARBWorkflowStage") -> bool:
        """Return whether this stage can transition to the target stage."""
        if not target_stage:
            return False
        if not self.is_active or not target_stage.is_active:
            return False
        if self.code == target_stage.code:
            return True
        # Default fallback for legacy rows: forward-only transitions by order.
        return (target_stage.order or 0) > (self.order or 0)

    def evaluate_gate_conditions(self, review_item: Any) -> Dict[str, Any]:
        """
        Evaluate gate conditions for a transition into this stage.

        This model currently does not persist explicit gate rules, so we return
        a permissive, structured result to keep the workflow engine stable.
        """
        return {"passed": True, "checks": [], "blocking_issues": []}


def create_default_workflow_stages():
    """Return default workflow stage definitions (non-DB representations)."""
    return [dict(s) for s in DEFAULT_WORKFLOW_STAGES]


class ArchitectureReviewBoard(TenantMixin, db.Model, OptimisticLockMixin):
    """
    Architecture Review Board session model.

    Represents an ARB meeting/session where multiple review items are discussed.
    """

    __tablename__ = "architecture_review_boards"

    id = db.Column(db.Integer, primary_key=True)
    board_number = db.Column(db.String(50), unique=True, nullable=False)  # ARB - 2026 - 001
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)

    # Scheduling
    scheduled_date = db.Column(db.DateTime, nullable=False)
    duration_minutes = db.Column(db.Integer, default=120)
    location = db.Column(db.String(255))  # Physical location or meeting link
    meeting_link = db.Column(db.String(500))  # Video conference URL

    # Board composition
    chair_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    secretary_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Status
    status = db.Column(
        db.String(30), default="scheduled"
    )  # scheduled, in_progress, completed, cancelled

    # Agenda and minutes
    agenda = db.Column(db.JSON)  # Structured agenda with items
    minutes = db.Column(db.Text)
    decisions_summary = db.Column(db.JSON)  # Summary of all decisions made

    # ArchiMate element linkage
    impacted_element_ids = db.Column(db.JSON, default=list)

    # Governance metrics
    items_reviewed = db.Column(db.Integer, default=0)
    items_approved = db.Column(db.Integer, default=0)
    items_rejected = db.Column(db.Integer, default=0)
    items_deferred = db.Column(db.Integer, default=0)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    # Relationships
    chair = db.relationship("User", foreign_keys=[chair_id], backref="chaired_arb_sessions")
    secretary = db.relationship(
        "User", foreign_keys=[secretary_id], backref="arb_secretary_sessions"
    )
    review_items = db.relationship(
        "ARBReviewItem", back_populates="arb_session", cascade="all, delete-orphan"
    )
    board_members = db.relationship(
        "ARBBoardMember", back_populates="arb_session", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<ARB {self.board_number}: {self.name}>"

    @staticmethod
    def generate_board_number():
        """Generate the next ARB session number.

        ``board_number`` is UNIQUE across the whole table while this model is a
        ``TenantMixin``, so the ORM query this used to run was narrowed to the
        caller's organisation by ``do_orm_execute``. A second tenant scheduling
        its first session of the year computed ``ARB-<year>-001``, which another
        tenant already held, and the INSERT died on the unique index — which is
        why ``POST /arb/sessions/create`` returned 500 for every submission and
        no session could be scheduled at all.
        """
        from app.utils.reference_numbers import next_reference

        year = datetime.utcnow().year
        return next_reference(
            "architecture_review_boards", "board_number", f"ARB-{year}-"
        )

    def to_dict(self):
        return {
            "id": self.id,
            "board_number": self.board_number,
            "name": self.name,
            "description": self.description,
            "scheduled_date": self.scheduled_date.isoformat() if self.scheduled_date else None,
            "duration_minutes": self.duration_minutes,
            "location": self.location,
            "meeting_link": self.meeting_link,
            "status": self.status,
            "items_reviewed": self.items_reviewed,
            "items_approved": self.items_approved,
            "items_rejected": self.items_rejected,
            "items_deferred": self.items_deferred,
            "chair": {
                "id": self.chair.id,
                "name": f"{self.chair.first_name} {self.chair.last_name}",
            }
            if self.chair
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ARBBoardMember(TenantMixin, db.Model):
    """Board members for an ARB session."""

    __tablename__ = "arb_board_members"

    id = db.Column(db.Integer, primary_key=True)
    # Phase B (Wave 4): TenantMixin enabled — backfill completed in Phase A.
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True, index=True)
    arb_session_id = db.Column(
        db.Integer, db.ForeignKey("architecture_review_boards.id"), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Role on the board
    role = db.Column(
        db.String(50), nullable=False
    )  # enterprise_architect, solution_architect, business_architect, data_architect, security_architect, integration_architect
    voting_member = db.Column(db.Boolean, default=True)

    # Attendance
    attendance_status = db.Column(
        db.String(30), default="pending"
    )  # pending, confirmed, declined, attended, absent
    attendance_notes = db.Column(db.Text)

    # Relationships
    arb_session = db.relationship("ArchitectureReviewBoard", back_populates="board_members")
    user = db.relationship("User", backref="arb_memberships")

    __table_args__ = (db.UniqueConstraint("arb_session_id", "user_id", name="uix_arb_member"),)


_ARB_OPEN_CYCLE_STATUSES = (
    "submitted",
    "under_review",
    "pending_information",
    "pending_info",
    "pending",
)
_ARB_TERMINAL_CYCLE_STATUSES = (
    "approved",
    "approved_with_conditions",
    "rejected",
    "deferred",
    "withdrawn",
    "returned_for_evidence",
    "returned_for_options",
)
_ARB_OPEN_CYCLE_SQL = ", ".join(f"'{value}'" for value in _ARB_OPEN_CYCLE_STATUSES)
_ARB_TERMINAL_CYCLE_SQL = ", ".join(
    f"'{value}'" for value in _ARB_TERMINAL_CYCLE_STATUSES
)


_ARB_REVIEW_CYCLE_SHAPE = (
    "subject_type IS NOT NULL AND subject_id IS NOT NULL "
    "AND review_number IS NOT NULL AND cycle_number IS NOT NULL "
    "AND cycle_number > 0 AND status IS NOT NULL AND opened_at IS NOT NULL "
    "AND ((cycle_number = 1 AND predecessor_cycle_id IS NULL) "
    "OR (cycle_number > 1 AND predecessor_cycle_id IS NOT NULL)) "
    "AND ((status = 'historical_unverified' "
    "AND migration_gap_reason IS NOT NULL AND legacy_source_type IS NOT NULL "
    "AND legacy_source_id IS NOT NULL AND closed_at IS NOT NULL "
    "AND terminal_outcome IS NOT NULL "
    "AND terminal_outcome = 'historical_unverified' "
    "AND decision_brief_version_id IS NULL "
    "AND solution_evidence_snapshot_id IS NULL "
    "AND subject_evidence_snapshot_id IS NULL "
    "AND source_table IS NULL AND source_id IS NULL AND source_org_id IS NULL "
    "AND source_checksum IS NULL AND retired_into_id IS NULL "
    "AND ((subject_type = 'solution' AND subject_id = solution_id "
    "AND solution_id IS NOT NULL AND decision_brief_id IS NULL "
    "AND architecture_model_id IS NULL AND adr_id IS NULL) "
    "OR (subject_type = 'architecture_model' "
    "AND subject_id = architecture_model_id "
    "AND architecture_model_id IS NOT NULL AND decision_brief_id IS NULL "
    "AND solution_id IS NULL AND adr_id IS NULL) "
    "OR (subject_type = 'adr' AND subject_id = adr_id AND adr_id IS NOT NULL "
    "AND decision_brief_id IS NULL AND solution_id IS NULL "
    "AND architecture_model_id IS NULL))) "
    "OR (status <> 'historical_unverified' "
    "AND migration_gap_reason IS NULL AND legacy_source_type IS NULL "
    "AND legacy_source_id IS NULL "
    "AND ((subject_type = 'decision_brief' "
    "AND subject_id = decision_brief_id AND decision_brief_id IS NOT NULL "
    "AND solution_id IS NULL AND architecture_model_id IS NULL AND adr_id IS NULL "
    "AND decision_brief_version_id IS NOT NULL "
    "AND solution_evidence_snapshot_id IS NULL "
    "AND subject_evidence_snapshot_id IS NULL) "
    "OR (subject_type = 'solution' AND subject_id = solution_id "
    "AND solution_id IS NOT NULL AND decision_brief_id IS NULL "
    "AND architecture_model_id IS NULL AND adr_id IS NULL "
    "AND decision_brief_version_id IS NULL "
    "AND solution_evidence_snapshot_id IS NOT NULL "
    "AND subject_evidence_snapshot_id IS NULL) "
    "OR (subject_type = 'architecture_model' "
    "AND subject_id = architecture_model_id "
    "AND architecture_model_id IS NOT NULL AND decision_brief_id IS NULL "
    "AND solution_id IS NULL AND adr_id IS NULL "
    "AND decision_brief_version_id IS NULL "
    "AND solution_evidence_snapshot_id IS NULL "
    "AND subject_evidence_snapshot_id IS NOT NULL) "
    "OR (subject_type = 'adr' AND subject_id = adr_id AND adr_id IS NOT NULL "
    "AND decision_brief_id IS NULL AND solution_id IS NULL "
    "AND architecture_model_id IS NULL AND decision_brief_version_id IS NULL "
    "AND solution_evidence_snapshot_id IS NULL "
    "AND subject_evidence_snapshot_id IS NOT NULL)) "
    f"AND ((status IN ({_ARB_OPEN_CYCLE_SQL}) "
    "AND closed_at IS NULL AND terminal_outcome IS NULL) "
    f"OR (status IN ({_ARB_TERMINAL_CYCLE_SQL}) "
    "AND closed_at IS NOT NULL AND terminal_outcome IS NOT NULL "
    "AND (terminal_outcome = status OR (terminal_outcome = 'approved_with_conditions' "
    "AND status = 'approved' AND condition_projection_revision > 0))))))"
)


class ARBReviewCycle(TenantMixin, db.Model):
    """Immutable subject/evidence identity around the sole ARB review item."""

    __tablename__ = "arb_review_cycles"

    id = db.Column(db.Integer, primary_key=True)
    subject_type = db.Column(db.String(40), nullable=True, index=True)
    subject_id = db.Column(db.Integer, nullable=True, index=True)
    decision_brief_id = db.Column(
        db.Integer,
        db.ForeignKey("decision_briefs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    solution_id = db.Column(
        db.Integer,
        db.ForeignKey("solutions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    architecture_model_id = db.Column(
        db.Integer,
        db.ForeignKey("architecture_models.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # E2E-H: repointed from architecture_decision_records (0 rows in
    # production, unreachable via any real UI) to architecture_decisions,
    # the model the real Decision Register writes.
    adr_id = db.Column(
        db.Integer,
        db.ForeignKey("architecture_decisions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    decision_brief_version_id = db.Column(
        db.Integer,
        db.ForeignKey("decision_brief_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    solution_evidence_snapshot_id = db.Column(
        db.Integer,
        db.ForeignKey("arb_submission_evidence_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    subject_evidence_snapshot_id = db.Column(
        db.Integer,
        db.ForeignKey("arb_subject_evidence_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    review_number = db.Column(db.String(50), nullable=True)
    cycle_number = db.Column(db.Integer, nullable=True)
    predecessor_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("arb_review_cycles.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status = db.Column(db.String(40), nullable=True)
    migration_gap_reason = db.Column(db.Text, nullable=True)
    legacy_source_type = db.Column(db.String(80), nullable=True)
    legacy_source_id = db.Column(db.Integer, nullable=True)
    opened_at = db.Column(
        db.DateTime(timezone=True), nullable=True, server_default=db.func.now()
    )
    closed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    terminal_outcome = db.Column(db.String(80), nullable=True)
    condition_projection_revision = db.Column(db.Integer, nullable=True)

    # Consolidation provenance: tracks legacy row origin and retirement
    source_table = db.Column(db.String(128), nullable=True, index=True)
    source_id = db.Column(db.String(255), nullable=True, index=True)
    source_org_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_checksum = db.Column(db.String(64), nullable=True)
    retired_into_id = db.Column(
        db.BigInteger,
        db.ForeignKey("arb_review_cycles.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Explicit organization relationship to disambiguate from source_org_id
    @declared_attr
    def organization(cls):
        from app import db
        return db.relationship(
            "Organization",
            foreign_keys=[cls.organization_id],
            lazy="select",
        )

    __table_args__ = (
        db.CheckConstraint(_ARB_REVIEW_CYCLE_SHAPE, name="ck_arb_review_cycle_shape"),
        db.UniqueConstraint(
            "organization_id",
            "subject_type",
            "subject_id",
            "cycle_number",
            name="uq_arb_review_cycle_number",
        ),
        db.UniqueConstraint(
            "organization_id",
            "review_number",
            name="uq_arb_review_cycle_review_number",
        ),
        db.UniqueConstraint(
            "predecessor_cycle_id",
            name="uq_arb_review_cycle_predecessor",
        ),
        db.Index(
            "uq_arb_review_cycle_open_subject",
            "organization_id",
            "subject_type",
            "subject_id",
            unique=True,
            postgresql_where=db.text("closed_at IS NULL"),
        ),
    )

    # Consolidation relationship
    retired_into = db.relationship(
        "ARBReviewCycle",
        foreign_keys=[retired_into_id],
        remote_side="ARBReviewCycle.id",
        backref="retired_duplicates",
    )


_ARB_TYPED_REVIEW_SHAPE = (
    "(review_cycle_id IS NULL AND subject_type IS NULL AND subject_id IS NULL "
    "AND decision_brief_id IS NULL AND decision_brief_version_id IS NULL "
    "AND solution_evidence_snapshot_id IS NULL "
    "AND subject_evidence_snapshot_id IS NULL "
    "AND source_table IS NULL AND source_id IS NULL AND source_org_id IS NULL "
    "AND source_checksum IS NULL AND retired_into_id IS NULL) "
    "OR (review_cycle_id IS NOT NULL AND status = 'historical_unverified' "
    "AND subject_type IS NOT NULL AND subject_id IS NOT NULL "
    "AND decision_brief_id IS NULL AND decision_brief_version_id IS NULL "
    "AND solution_evidence_snapshot_id IS NULL "
    "AND subject_evidence_snapshot_id IS NULL "
    "AND decision IS NULL AND decision_rationale IS NULL AND conditions IS NULL "
    "AND decision_date IS NULL AND decided_by_id IS NULL "
    "AND governance_checklist IS NULL AND compliance_score IS NULL "
    "AND risk_score IS NULL AND quality_score IS NULL AND overall_score IS NULL "
    "AND ((subject_type = 'solution' AND subject_id = solution_id "
    "AND solution_id IS NOT NULL AND architecture_model_id IS NULL "
    "AND adr_id IS NULL) "
    "OR (subject_type = 'architecture_model' "
    "AND subject_id = architecture_model_id AND solution_id IS NULL "
    "AND architecture_model_id IS NOT NULL AND adr_id IS NULL) "
    "OR (subject_type = 'adr' AND subject_id = adr_id AND solution_id IS NULL "
    "AND architecture_model_id IS NULL AND adr_id IS NOT NULL))) "
    "OR (review_cycle_id IS NOT NULL AND status <> 'historical_unverified' "
    "AND subject_type IS NOT NULL "
    "AND subject_id IS NOT NULL AND ((subject_type = 'decision_brief' "
    "AND subject_id = decision_brief_id AND decision_brief_id IS NOT NULL "
    "AND solution_id IS NULL AND architecture_model_id IS NULL AND adr_id IS NULL "
    "AND decision_brief_version_id IS NOT NULL "
    "AND solution_evidence_snapshot_id IS NULL "
    "AND subject_evidence_snapshot_id IS NULL) "
    "OR (subject_type = 'solution' AND subject_id = solution_id "
    "AND solution_id IS NOT NULL AND decision_brief_id IS NULL "
    "AND architecture_model_id IS NULL AND adr_id IS NULL "
    "AND decision_brief_version_id IS NULL "
    "AND solution_evidence_snapshot_id IS NOT NULL "
    "AND subject_evidence_snapshot_id IS NULL) "
    "OR (subject_type = 'architecture_model' "
    "AND subject_id = architecture_model_id "
    "AND architecture_model_id IS NOT NULL AND decision_brief_id IS NULL "
    "AND solution_id IS NULL AND adr_id IS NULL "
    "AND decision_brief_version_id IS NULL "
    "AND solution_evidence_snapshot_id IS NULL "
    "AND subject_evidence_snapshot_id IS NOT NULL) "
    "OR (subject_type = 'adr' AND subject_id = adr_id AND adr_id IS NOT NULL "
    "AND decision_brief_id IS NULL AND solution_id IS NULL "
    "AND architecture_model_id IS NULL AND decision_brief_version_id IS NULL "
    "AND solution_evidence_snapshot_id IS NULL "
    "AND subject_evidence_snapshot_id IS NOT NULL)) "
    f"AND ((status IN ({_ARB_OPEN_CYCLE_SQL}) AND decision IS NULL) "
    f"OR (status IN ({_ARB_TERMINAL_CYCLE_SQL}) "
    "AND decision IS NOT NULL AND (decision = status OR "
    "(decision = 'approved_with_conditions' AND status = 'approved' "
    "AND condition_projection_revision > 0)))))"
)


class ARBReviewItem(TenantMixin, db.Model, OptimisticLockMixin):
    """
    Individual item submitted for ARB review.

    Links solutions, capabilities, and ADRs to the review process.
    """

    __tablename__ = "arb_review_items"

    id = db.Column(db.Integer, primary_key=True)
    # Phase B (Wave 4): TenantMixin enabled — backfill completed in Phase A.
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True, index=True)
    review_number = db.Column(db.String(50), unique=True, nullable=False)  # REV - 2026 - 001
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)

    # Review classification
    review_type = db.Column(db.String(50), nullable=False)  # From ReviewType enum
    togaf_phase = db.Column(db.String(50))  # From TOGAFPhase enum
    archimate_layer = db.Column(
        db.String(30)
    )  # motivation, strategy, business, application, technology, implementation

    # Priority and urgency
    priority = db.Column(db.String(20), default="medium")  # critical, high, medium, low
    business_impact = db.Column(db.String(20))  # critical, high, medium, low
    estimated_effort = db.Column(db.String(20))  # small, medium, large, xl

    # Linkages to existing entities
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id"))
    architecture_model_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))
    # E2E-H: repointed from architecture_decision_records (0 rows in
    # production, unreachable via any real UI -- and the field arb.
    # api_form_data's "New Review" ADR dropdown, fixed in 73795d4d, feeds)
    # to architecture_decisions, the model the real Decision Register writes.
    adr_id = db.Column(db.Integer, db.ForeignKey("architecture_decisions.id"))
    subject_type = db.Column(db.String(40), nullable=True, index=True)
    subject_id = db.Column(db.Integer, nullable=True, index=True)
    decision_brief_id = db.Column(
        db.Integer,
        db.ForeignKey("decision_briefs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    decision_brief_version_id = db.Column(
        db.Integer,
        db.ForeignKey("decision_brief_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    solution_evidence_snapshot_id = db.Column(
        db.Integer,
        db.ForeignKey("arb_submission_evidence_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    subject_evidence_snapshot_id = db.Column(
        db.Integer,
        db.ForeignKey("arb_subject_evidence_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    review_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "arb_review_cycles.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_arb_review_item_cycle",
        ),
        nullable=True,
        unique=True,
    )

    # Status and workflow
    status = db.Column(db.String(30), default="draft")  # From ARBReviewStatus enum
    condition_projection_revision = db.Column(db.Integer, nullable=True)
    arb_session_id = db.Column(db.Integer, db.ForeignKey("architecture_review_boards.id", ondelete="CASCADE"))

    # Submission details
    submitter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    submitted_at = db.Column(db.DateTime)

    # Review details
    reviewer_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    review_started_at = db.Column(db.DateTime)
    review_completed_at = db.Column(db.DateTime)

    # Decision
    decision = db.Column(db.String(50))  # approved, approved_with_conditions, rejected, deferred
    decision_rationale = db.Column(db.Text)
    conditions = db.Column(db.JSON)  # Conditions for approval
    decision_date = db.Column(db.DateTime)
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Review checklist and scoring
    governance_checklist = db.Column(db.JSON)  # Checklist items and completion status
    compliance_score = db.Column(db.Float)  # 0 - 100
    risk_score = db.Column(db.Float)  # 0 - 100
    quality_score = db.Column(db.Float)  # 0 - 100
    overall_score = db.Column(db.Float)  # Weighted average

    # Capability impact analysis
    capability_impacts = db.Column(db.JSON)  # List of impacted capabilities with analysis

    # ArchiMate viewpoint analysis
    archimate_viewpoints = db.Column(db.JSON)  # Relevant viewpoints and assessments

    # Supporting documents
    attachments = db.Column(db.JSON)  # List of attached documents

    # ENH-020: Implementation status tracking
    implementation_status = db.Column(
        db.String(30), default="not_started"
    )  # not_started, in_progress, completed, blocked, deferred
    implementation_notes = db.Column(db.Text)
    implementation_started_at = db.Column(db.DateTime)
    implementation_completed_at = db.Column(db.DateTime)
    conditions_response = db.Column(db.JSON)  # Response to conditions_required

    # COM-009: Jira integration
    jira_issue_key = db.Column(db.String(50), nullable=True)  # e.g. ARCH-42

    # Follow-up tracking
    follow_up_required = db.Column(db.Boolean, default=False)
    follow_up_date = db.Column(db.Date)
    follow_up_notes = db.Column(db.Text)

    # External integrations
    servicenow_change_id = db.Column(db.String(100), nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Consolidation provenance: tracks legacy row origin and retirement
    source_table = db.Column(db.String(128), nullable=True, index=True)
    source_id = db.Column(db.String(255), nullable=True, index=True)
    source_org_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_checksum = db.Column(db.String(64), nullable=True)
    retired_into_id = db.Column(
        db.BigInteger,
        db.ForeignKey("arb_review_items.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    arb_session = db.relationship("ArchitectureReviewBoard", back_populates="review_items")
    solution = db.relationship("Solution", foreign_keys=[solution_id], backref="arb_reviews")
    architecture_model = db.relationship("ArchitectureModel", backref="arb_reviews")
    adr = db.relationship("ArchitectureDecision", backref="arb_reviews")
    submitter = db.relationship("User", foreign_keys=[submitter_id], backref="submitted_arb_items")
    reviewer = db.relationship("User", foreign_keys=[reviewer_id], backref="reviewed_arb_items")
    decided_by = db.relationship("User", foreign_keys=[decided_by_id], backref="arb_decisions")
    review_cycle = db.relationship(
        "ARBReviewCycle",
        foreign_keys=[review_cycle_id],
        backref=db.backref("review_item", uselist=False),
    )
    comments = db.relationship(
        "ARBReviewComment", back_populates="review_item", cascade="all, delete-orphan"
    )
    capability_links = db.relationship(
        "ARBCapabilityImpact", back_populates="review_item", cascade="all, delete-orphan"
    )

    # Consolidation relationship
    retired_into = db.relationship(
        "ARBReviewItem",
        foreign_keys=[retired_into_id],
        remote_side="ARBReviewItem.id",
        backref="retired_duplicates",
    )

    # Explicit organization relationship to disambiguate from source_org_id
    @declared_attr
    def organization(cls):
        from app import db
        return db.relationship(
            "Organization",
            foreign_keys=[cls.organization_id],
            lazy="select",
        )

    __table_args__ = (
        db.CheckConstraint(_ARB_TYPED_REVIEW_SHAPE, name="ck_arb_review_item_typed_shape"),
    )

    def __repr__(self):
        return f"<ARBReviewItem {self.review_number}: {self.title}>"

    @staticmethod
    def generate_review_number():
        """Generate a sequential, table-wide-unique review number.

        ``ARBReviewItem`` is tenant-scoped but ``review_number`` has a global
        unique constraint. A sequential "last row + 1" ORM query would be
        silently filtered to the current tenant while the unique index spans
        every tenant, so two organizations could collide. ``next_reference``
        allocates against the whole table with raw SQL (not subject to
        ``do_orm_execute``) and takes the maximum suffix rather than the last
        row, which is both tenant-safe and matches the seeded ``ARB-YYYY-NNN``
        format a reader expects — rather than an opaque ``REV-YYYY-<uuid>``.
        """
        from app.utils.reference_numbers import next_reference

        year = datetime.utcnow().year
        return next_reference(
            "arb_review_items", "review_number", f"ARB-{year}-", width=3
        )

    def calculate_overall_score(self):
        """Calculate weighted overall score."""
        weights = {"compliance": 0.35, "risk": 0.30, "quality": 0.35}

        scores = []
        if self.compliance_score is not None:
            scores.append(self.compliance_score * weights["compliance"])
        if self.risk_score is not None:
            # Risk is inverted - lower risk = higher score
            scores.append((100 - self.risk_score) * weights["risk"])
        if self.quality_score is not None:
            scores.append(self.quality_score * weights["quality"])

        if scores:
            total_weight = sum(
                weights[k]
                for k in ["compliance", "risk", "quality"]
                if getattr(self, f"{k}_score") is not None
            )
            self.overall_score = sum(scores) / total_weight if total_weight > 0 else 0

        return self.overall_score

    def to_dict(self, include_details=True):
        base_dict = {
            "id": self.id,
            "review_number": self.review_number,
            "title": self.title,
            "review_type": self.review_type,
            "togaf_phase": self.togaf_phase,
            "archimate_layer": self.archimate_layer,
            "priority": self.priority,
            "status": self.status,
            "decision": self.decision,
            "overall_score": self.overall_score,
            "submitter": {
                "id": self.submitter.id,
                "name": f"{self.submitter.first_name} {self.submitter.last_name}",
            }
            if self.submitter
            else None,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

        if include_details:
            base_dict.update(
                {
                    "description": self.description,
                    "business_impact": self.business_impact,
                    "estimated_effort": self.estimated_effort,
                    "solution_id": self.solution_id,
                    "adr_id": self.adr_id,
                    "decision_rationale": self.decision_rationale,
                    "conditions": self.conditions,
                    "governance_checklist": self.governance_checklist,
                    "compliance_score": self.compliance_score,
                    "risk_score": self.risk_score,
                    "quality_score": self.quality_score,
                    "capability_impacts": self.capability_impacts,
                    "archimate_viewpoints": self.archimate_viewpoints,
                    "follow_up_required": self.follow_up_required,
                    "follow_up_date": self.follow_up_date.isoformat()
                    if self.follow_up_date
                    else None,
                    "implementation_status": self.implementation_status,
                    "implementation_notes": self.implementation_notes,
                    "implementation_started_at": self.implementation_started_at.isoformat()
                    if self.implementation_started_at
                    else None,
                    "implementation_completed_at": self.implementation_completed_at.isoformat()
                    if self.implementation_completed_at
                    else None,
                    "conditions_response": self.conditions_response,
                }
            )

        return base_dict


def _arb_membership_function_sql(quoted_schema):
    return f"""
    CREATE OR REPLACE FUNCTION {quoted_schema}.archie_validate_arb_cycle_membership()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, {quoted_schema}
    AS $$
    DECLARE
        matching_review_id integer;
    BEGIN
        IF NEW.subject_type IS NOT NULL AND NEW.subject_id IS NOT NULL THEN
            PERFORM pg_advisory_xact_lock(hashtextextended(
                'archie-arb-subject:' || NEW.subject_type || ':' || NEW.subject_id::text,
                0
            ));
        END IF;
        IF TG_TABLE_NAME = 'arb_subject_evidence_snapshots' THEN
            IF NEW.subject_type = 'architecture_model' AND NOT EXISTS (
                SELECT 1 FROM architecture_models model
                WHERE model.id = NEW.architecture_model_id
                  AND model.organization_id = NEW.organization_id
            ) THEN
                RAISE EXCEPTION 'ARB snapshot subject is outside its tenant'
                    USING ERRCODE = '23514';
            ELSIF NEW.subject_type = 'adr' AND NOT EXISTS (
                SELECT 1 FROM architecture_decisions adr
                WHERE adr.id = NEW.adr_id
                  AND adr.organization_id = NEW.organization_id
            ) THEN
                RAISE EXCEPTION 'ARB snapshot subject is outside its tenant'
                    USING ERRCODE = '23514';
            END IF;

        ELSIF TG_TABLE_NAME = 'arb_review_cycles' THEN
            IF NEW.subject_type = 'decision_brief' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM decision_briefs brief
                    WHERE brief.id = NEW.decision_brief_id
                      AND brief.organization_id = NEW.organization_id
                ) THEN
                    RAISE EXCEPTION 'ARB cycle subject is outside its tenant'
                        USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM decision_brief_versions version
                    WHERE version.id = NEW.decision_brief_version_id
                      AND version.brief_id = NEW.decision_brief_id
                      AND version.organization_id = NEW.organization_id
                ) THEN
                    RAISE EXCEPTION 'ARB cycle version does not belong to its brief and tenant'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.subject_type = 'solution' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM solutions solution
                    WHERE solution.id = NEW.solution_id
                      AND solution.organization_id = NEW.organization_id
                ) THEN
                    RAISE EXCEPTION 'ARB cycle subject is outside its tenant'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.status <> 'historical_unverified' AND NOT EXISTS (
                    SELECT 1 FROM arb_submission_evidence_snapshots snapshot
                    WHERE snapshot.id = NEW.solution_evidence_snapshot_id
                      AND snapshot.solution_id = NEW.solution_id
                      AND snapshot.organization_id = NEW.organization_id
                ) THEN
                    RAISE EXCEPTION 'ARB cycle snapshot does not belong to its subject and tenant'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.subject_type = 'architecture_model' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM architecture_models model
                    WHERE model.id = NEW.architecture_model_id
                      AND model.organization_id = NEW.organization_id
                ) THEN
                    RAISE EXCEPTION 'ARB cycle subject is outside its tenant'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.status <> 'historical_unverified' AND NOT EXISTS (
                    SELECT 1 FROM arb_subject_evidence_snapshots snapshot
                    WHERE snapshot.id = NEW.subject_evidence_snapshot_id
                      AND snapshot.subject_type = NEW.subject_type
                      AND snapshot.subject_id = NEW.subject_id
                      AND snapshot.architecture_model_id = NEW.architecture_model_id
                      AND snapshot.organization_id = NEW.organization_id
                ) THEN
                    RAISE EXCEPTION 'ARB cycle snapshot does not belong to its subject and tenant'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.subject_type = 'adr' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM architecture_decisions adr
                    WHERE adr.id = NEW.adr_id
                      AND adr.organization_id = NEW.organization_id
                ) THEN
                    RAISE EXCEPTION 'ARB cycle subject is outside its tenant'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.status <> 'historical_unverified' AND NOT EXISTS (
                    SELECT 1 FROM arb_subject_evidence_snapshots snapshot
                    WHERE snapshot.id = NEW.subject_evidence_snapshot_id
                      AND snapshot.subject_type = NEW.subject_type
                      AND snapshot.subject_id = NEW.subject_id
                      AND snapshot.adr_id = NEW.adr_id
                      AND snapshot.organization_id = NEW.organization_id
                ) THEN
                    RAISE EXCEPTION 'ARB cycle snapshot does not belong to its subject and tenant'
                        USING ERRCODE = '23514';
                END IF;
            END IF;

            IF NEW.predecessor_cycle_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM arb_review_cycles predecessor
                WHERE predecessor.id = NEW.predecessor_cycle_id
                  AND predecessor.organization_id = NEW.organization_id
                  AND predecessor.subject_type = NEW.subject_type
                  AND predecessor.subject_id = NEW.subject_id
                  AND predecessor.cycle_number = NEW.cycle_number - 1
                  AND predecessor.status IN ({_ARB_TERMINAL_CYCLE_SQL})
                  AND predecessor.closed_at IS NOT NULL
                  AND predecessor.terminal_outcome IS NOT NULL
                  AND predecessor.terminal_outcome = predecessor.status
                  AND predecessor.closed_at <= NEW.opened_at
                  AND EXISTS (
                      SELECT 1 FROM arb_review_items predecessor_review
                      WHERE predecessor_review.review_cycle_id = predecessor.id
                        AND predecessor_review.organization_id = predecessor.organization_id
                        AND predecessor_review.status = predecessor.status
                        AND predecessor_review.decision IS NOT NULL
                        AND predecessor_review.decision = predecessor.status
                  )
            ) THEN
                RAISE EXCEPTION 'ARB cycle predecessor is not monotonic for its typed subject'
                    USING ERRCODE = '23514';
            END IF;

            SELECT review.id INTO matching_review_id
            FROM arb_review_items review
            WHERE review.review_cycle_id = NEW.id
              AND review.organization_id = NEW.organization_id
              AND review.review_number = NEW.review_number
              AND review.status = NEW.status
              AND review.subject_type = NEW.subject_type
              AND review.subject_id = NEW.subject_id
              AND review.decision_brief_id IS NOT DISTINCT FROM NEW.decision_brief_id
              AND review.solution_id IS NOT DISTINCT FROM NEW.solution_id
              AND review.architecture_model_id IS NOT DISTINCT FROM NEW.architecture_model_id
              AND review.adr_id IS NOT DISTINCT FROM NEW.adr_id
              AND review.decision_brief_version_id IS NOT DISTINCT FROM NEW.decision_brief_version_id
              AND review.solution_evidence_snapshot_id IS NOT DISTINCT FROM NEW.solution_evidence_snapshot_id
              AND review.subject_evidence_snapshot_id IS NOT DISTINCT FROM NEW.subject_evidence_snapshot_id;
            IF matching_review_id IS NULL THEN
                RAISE EXCEPTION 'ARB cycle review projection is missing or disagrees'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.subject_type = 'solution'
               AND NEW.status <> 'historical_unverified'
               AND NOT EXISTS (
                   SELECT 1 FROM arb_submission_evidence_snapshots snapshot
                   WHERE snapshot.id = NEW.solution_evidence_snapshot_id
                     AND snapshot.review_item_id = matching_review_id
               ) THEN
                RAISE EXCEPTION 'ARB Solution snapshot does not belong to its review'
                    USING ERRCODE = '23514';
            END IF;

        ELSIF TG_TABLE_NAME = 'arb_review_items' AND NEW.review_cycle_id IS NOT NULL THEN
            IF NOT EXISTS (
                SELECT 1 FROM arb_review_cycles cycle
                WHERE cycle.id = NEW.review_cycle_id
                  AND cycle.organization_id = NEW.organization_id
                  AND cycle.review_number = NEW.review_number
                  AND cycle.status = NEW.status
                  AND cycle.subject_type = NEW.subject_type
                  AND cycle.subject_id = NEW.subject_id
                  AND cycle.decision_brief_id IS NOT DISTINCT FROM NEW.decision_brief_id
                  AND cycle.solution_id IS NOT DISTINCT FROM NEW.solution_id
                  AND cycle.architecture_model_id IS NOT DISTINCT FROM NEW.architecture_model_id
                  AND cycle.adr_id IS NOT DISTINCT FROM NEW.adr_id
                  AND cycle.decision_brief_version_id IS NOT DISTINCT FROM NEW.decision_brief_version_id
                  AND cycle.solution_evidence_snapshot_id IS NOT DISTINCT FROM NEW.solution_evidence_snapshot_id
                  AND cycle.subject_evidence_snapshot_id IS NOT DISTINCT FROM NEW.subject_evidence_snapshot_id
            ) THEN
                RAISE EXCEPTION 'ARB review projection disagrees with its cycle'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.subject_type = 'solution'
               AND NEW.solution_evidence_snapshot_id IS NOT NULL
               AND NOT EXISTS (
                SELECT 1 FROM arb_submission_evidence_snapshots snapshot
                WHERE snapshot.id = NEW.solution_evidence_snapshot_id
                  AND snapshot.review_item_id = NEW.id
                  AND snapshot.organization_id = NEW.organization_id
                  AND snapshot.solution_id = NEW.solution_id
            ) THEN
                RAISE EXCEPTION 'ARB Solution snapshot does not belong to its review'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$
    """


def _arb_history_function_sql(quoted_schema):
    return f"""
    CREATE OR REPLACE FUNCTION {quoted_schema}.archie_guard_arb_cycle_history()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, {quoted_schema}
    AS $$
    BEGIN
        IF TG_TABLE_NAME = 'arb_review_cycles' THEN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'ARB review cycle history is append-only'
                    USING ERRCODE = '55000';
            END IF;
            IF ROW(
                OLD.organization_id, OLD.subject_type, OLD.subject_id,
                OLD.decision_brief_id, OLD.solution_id, OLD.architecture_model_id,
                OLD.adr_id, OLD.decision_brief_version_id,
                OLD.solution_evidence_snapshot_id, OLD.subject_evidence_snapshot_id,
                OLD.review_number, OLD.cycle_number, OLD.predecessor_cycle_id,
                OLD.migration_gap_reason, OLD.legacy_source_type,
                OLD.legacy_source_id, OLD.opened_at
            ) IS DISTINCT FROM ROW(
                NEW.organization_id, NEW.subject_type, NEW.subject_id,
                NEW.decision_brief_id, NEW.solution_id, NEW.architecture_model_id,
                NEW.adr_id, NEW.decision_brief_version_id,
                NEW.solution_evidence_snapshot_id, NEW.subject_evidence_snapshot_id,
                NEW.review_number, NEW.cycle_number, NEW.predecessor_cycle_id,
                NEW.migration_gap_reason, NEW.legacy_source_type,
                NEW.legacy_source_id, NEW.opened_at
            ) THEN
                RAISE EXCEPTION 'ARB review cycle identity and evidence are immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.closed_at IS NOT NULL AND ROW(
                OLD.status, OLD.closed_at, OLD.terminal_outcome,
                OLD.condition_projection_revision
            ) IS DISTINCT FROM ROW(
                NEW.status, NEW.closed_at, NEW.terminal_outcome,
                NEW.condition_projection_revision
            ) AND NOT (
                OLD.terminal_outcome = 'approved_with_conditions'
                AND NEW.terminal_outcome = OLD.terminal_outcome
                AND NEW.closed_at = OLD.closed_at
                AND OLD.status IN ('approved_with_conditions', 'approved')
                AND NEW.status IN ('approved_with_conditions', 'approved')
                AND NEW.condition_projection_revision =
                    COALESCE(OLD.condition_projection_revision, 0) + 1
                AND NEW.status = CASE WHEN NOT EXISTS (
                    SELECT 1 FROM arb_canonical_conditions aggregate_condition
                    WHERE aggregate_condition.review_cycle_id = NEW.id
                      AND aggregate_condition.organization_id = NEW.organization_id
                      AND aggregate_condition.status NOT IN ('fulfilled', 'waived')
                ) THEN 'approved' ELSE 'approved_with_conditions' END
                AND EXISTS (
                    SELECT 1 FROM arb_condition_events condition_event
                    JOIN command_idempotency_records receipt
                      ON receipt.id = condition_event.command_receipt_id
                     AND receipt.organization_id = condition_event.organization_id
                     AND receipt.actor_id = condition_event.actor_id
                     AND receipt.lease_generation = condition_event.command_generation
                     AND receipt.operation = CASE condition_event.event_type
                         WHEN 'submit_evidence' THEN 'arb.condition.evidence.submit'
                         WHEN 'verify' THEN 'arb.condition.evidence.verify'
                         WHEN 'waive' THEN 'arb.condition.waive'
                         WHEN 'waiver_expired' THEN 'arb.condition.waiver.expire'
                       END
                     AND receipt.natural_key = 'arb-condition:' ||
                         condition_event.organization_id::text || ':' ||
                         condition_event.condition_id::text || ':' ||
                         condition_event.event_type || ':' ||
                         condition_event.condition_revision::text
                    WHERE condition_event.review_cycle_id = NEW.id
                      AND condition_event.organization_id = NEW.organization_id
                      AND condition_event.projection_status = NEW.status
                      AND condition_event.projection_revision =
                          NEW.condition_projection_revision
                )
            ) THEN
                RAISE EXCEPTION 'closed ARB review cycle history is immutable'
                    USING ERRCODE = '55000';
            END IF;
        ELSIF TG_TABLE_NAME = 'arb_review_items' THEN
            IF TG_OP = 'DELETE' AND OLD.review_cycle_id IS NOT NULL THEN
                RAISE EXCEPTION 'typed ARB review history is append-only'
                    USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.review_cycle_id IS NOT NULL AND ROW(
                OLD.organization_id, OLD.subject_type, OLD.subject_id,
                OLD.decision_brief_id, OLD.solution_id, OLD.architecture_model_id,
                OLD.adr_id, OLD.decision_brief_version_id,
                OLD.solution_evidence_snapshot_id, OLD.subject_evidence_snapshot_id,
                OLD.review_cycle_id
            ) IS DISTINCT FROM ROW(
                NEW.organization_id, NEW.subject_type, NEW.subject_id,
                NEW.decision_brief_id, NEW.solution_id, NEW.architecture_model_id,
                NEW.adr_id, NEW.decision_brief_version_id,
                NEW.solution_evidence_snapshot_id, NEW.subject_evidence_snapshot_id,
                NEW.review_cycle_id
            ) THEN
                RAISE EXCEPTION 'typed ARB review projection is immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.review_cycle_id IS NOT NULL
               AND OLD.status = 'historical_unverified'
               AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
                RAISE EXCEPTION 'historical unverified ARB review is immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.review_cycle_id IS NOT NULL
               AND OLD.decision IS NOT NULL
               AND ROW(OLD.status, OLD.decision,
                       OLD.condition_projection_revision)
                   IS DISTINCT FROM
                   ROW(NEW.status, NEW.decision,
                       NEW.condition_projection_revision)
               AND NOT (
                   OLD.decision = 'approved_with_conditions'
                   AND NEW.decision = OLD.decision
                   AND OLD.status IN ('approved_with_conditions', 'approved')
                   AND NEW.status IN ('approved_with_conditions', 'approved')
                   AND NEW.condition_projection_revision =
                       COALESCE(OLD.condition_projection_revision, 0) + 1
                   AND NEW.status = CASE WHEN NOT EXISTS (
                       SELECT 1 FROM arb_canonical_conditions aggregate_condition
                       WHERE aggregate_condition.review_item_id = NEW.id
                         AND aggregate_condition.organization_id = NEW.organization_id
                         AND aggregate_condition.status NOT IN ('fulfilled', 'waived')
                   ) THEN 'approved' ELSE 'approved_with_conditions' END
                   AND EXISTS (
                       SELECT 1 FROM arb_condition_events condition_event
                       JOIN command_idempotency_records receipt
                         ON receipt.id = condition_event.command_receipt_id
                        AND receipt.organization_id = condition_event.organization_id
                        AND receipt.actor_id = condition_event.actor_id
                        AND receipt.lease_generation = condition_event.command_generation
                        AND receipt.operation = CASE condition_event.event_type
                            WHEN 'submit_evidence' THEN 'arb.condition.evidence.submit'
                            WHEN 'verify' THEN 'arb.condition.evidence.verify'
                            WHEN 'waive' THEN 'arb.condition.waive'
                            WHEN 'waiver_expired' THEN 'arb.condition.waiver.expire'
                          END
                        AND receipt.natural_key = 'arb-condition:' ||
                            condition_event.organization_id::text || ':' ||
                            condition_event.condition_id::text || ':' ||
                            condition_event.event_type || ':' ||
                            condition_event.condition_revision::text
                       WHERE condition_event.review_item_id = NEW.id
                         AND condition_event.organization_id = NEW.organization_id
                         AND condition_event.projection_status = NEW.status
                         AND condition_event.projection_revision =
                             NEW.condition_projection_revision
                   )
               ) THEN
                RAISE EXCEPTION 'typed ARB condition projection is invalid'
                    USING ERRCODE = '55000';
            END IF;
        END IF;
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END;
    $$
    """


def _arb_parent_tenant_function_sql(quoted_schema):
    return f"""
    CREATE OR REPLACE FUNCTION {quoted_schema}.archie_guard_arb_subject_tenant()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, {quoted_schema}
    AS $$
    DECLARE
        typed_subject text;
    BEGIN
        IF NEW.organization_id IS NOT DISTINCT FROM OLD.organization_id THEN
            RETURN NEW;
        END IF;
        typed_subject := CASE TG_TABLE_NAME
            WHEN 'decision_briefs' THEN 'decision_brief'
            WHEN 'solutions' THEN 'solution'
            WHEN 'architecture_models' THEN 'architecture_model'
            WHEN 'architecture_decisions' THEN 'adr'
            -- Fail closed: a NULL typed_subject would make every EXISTS below
            -- match nothing, so an unlisted table would silently be granted the
            -- tenant change this guard exists to refuse.
            ELSE NULL
        END;
        IF typed_subject IS NULL THEN
            -- The offending table goes in DETAIL rather than a format
            -- placeholder: this body ships through exec_driver_sql, where a bare
            -- percent sign is read as a DBAPI parameter marker.
            RAISE EXCEPTION 'guard attached to unsupported table'
                USING ERRCODE = '23514',
                      DETAIL = 'archie_guard_arb_subject_tenant on '
                               || TG_TABLE_NAME;
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'archie-arb-subject:' || typed_subject || ':' || OLD.id::text,
            0
        ));
        IF EXISTS (
            SELECT 1 FROM arb_review_cycles cycle
            WHERE cycle.subject_type = typed_subject
              AND cycle.subject_id = OLD.id
              AND cycle.organization_id IS DISTINCT FROM NEW.organization_id
        ) OR EXISTS (
            SELECT 1 FROM arb_review_items review
            WHERE review.review_cycle_id IS NOT NULL
              AND review.subject_type = typed_subject
              AND review.subject_id = OLD.id
              AND review.organization_id IS DISTINCT FROM NEW.organization_id
        ) OR (
            typed_subject IN ('architecture_model', 'adr') AND EXISTS (
                SELECT 1 FROM arb_subject_evidence_snapshots snapshot
                WHERE snapshot.subject_type = typed_subject
                  AND snapshot.subject_id = OLD.id
                  AND snapshot.organization_id IS DISTINCT FROM NEW.organization_id
            )
        ) THEN
            RAISE EXCEPTION 'subject tenant change would invalidate typed ARB history'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """


_ARB_CHECK_SPECS = {
    "ck_arb_subject_evidence_snapshot_shape": (
        "arb_subject_evidence_snapshots",
        "ARBSubjectEvidenceSnapshot",
    ),
    "ck_arb_review_cycle_shape": ("arb_review_cycles", "ARBReviewCycle"),
    "ck_arb_review_item_typed_shape": ("arb_review_items", "ARBReviewItem"),
}
_ARB_INDEX_SPECS = {
    "uq_arb_review_cycle_number": (
        "arb_review_cycles",
        ("organization_id", "subject_type", "subject_id", "cycle_number"),
        None,
    ),
    "uq_arb_review_cycle_review_number": (
        "arb_review_cycles",
        ("organization_id", "review_number"),
        None,
    ),
    "uq_arb_review_cycle_predecessor": (
        "arb_review_cycles",
        ("predecessor_cycle_id",),
        None,
    ),
    "uq_arb_review_cycle_open_subject": (
        "arb_review_cycles",
        ("organization_id", "subject_type", "subject_id"),
        "closed_at is null",
    ),
    "uq_arb_review_item_cycle": ("arb_review_items", ("review_cycle_id",), None),
}
_ARB_FK_SPECS = {
    "fk_arb_subject_snapshot_architecture_model": (
        "arb_subject_evidence_snapshots",
        ("architecture_model_id",),
        "architecture_models",
        ("id",),
    ),
    "fk_arb_subject_snapshot_adr": (
        "arb_subject_evidence_snapshots",
        ("adr_id",),
        "architecture_decisions",
        ("id",),
    ),
    "fk_arb_subject_snapshot_captured_by": (
        "arb_subject_evidence_snapshots",
        ("captured_by_id",),
        "users",
        ("id",),
    ),
    "fk_arb_review_cycle_decision_brief": (
        "arb_review_cycles", ("decision_brief_id",), "decision_briefs", ("id",)
    ),
    "fk_arb_review_cycle_solution": (
        "arb_review_cycles", ("solution_id",), "solutions", ("id",)
    ),
    "fk_arb_review_cycle_architecture_model": (
        "arb_review_cycles", ("architecture_model_id",), "architecture_models", ("id",)
    ),
    "fk_arb_review_cycle_adr": (
        "arb_review_cycles", ("adr_id",), "architecture_decisions", ("id",)
    ),
    "fk_arb_review_cycle_decision_brief_version": (
        "arb_review_cycles",
        ("decision_brief_version_id",),
        "decision_brief_versions",
        ("id",),
    ),
    "fk_arb_review_cycle_solution_snapshot": (
        "arb_review_cycles",
        ("solution_evidence_snapshot_id",),
        "arb_submission_evidence_snapshots",
        ("id",),
    ),
    "fk_arb_review_cycle_subject_snapshot": (
        "arb_review_cycles",
        ("subject_evidence_snapshot_id",),
        "arb_subject_evidence_snapshots",
        ("id",),
    ),
    "fk_arb_review_cycle_predecessor": (
        "arb_review_cycles", ("predecessor_cycle_id",), "arb_review_cycles", ("id",)
    ),
    "fk_arb_review_item_decision_brief": (
        "arb_review_items", ("decision_brief_id",), "decision_briefs", ("id",)
    ),
    "fk_arb_review_item_decision_brief_version": (
        "arb_review_items",
        ("decision_brief_version_id",),
        "decision_brief_versions",
        ("id",),
    ),
    "fk_arb_review_item_solution_snapshot": (
        "arb_review_items",
        ("solution_evidence_snapshot_id",),
        "arb_submission_evidence_snapshots",
        ("id",),
    ),
    "fk_arb_review_item_subject_snapshot": (
        "arb_review_items",
        ("subject_evidence_snapshot_id",),
        "arb_subject_evidence_snapshots",
        ("id",),
    ),
    "fk_arb_review_item_cycle": (
        "arb_review_items", ("review_cycle_id",), "arb_review_cycles", ("id",)
    ),
}
_ARB_TRIGGER_NO_FILTER = (None, 0, "")
_ARB_TRIGGER_SPECS = {
    ("arb_subject_evidence_snapshots", "trg_arb_subject_snapshot_membership"):
        ("archie_validate_arb_cycle_membership", 21, True, True, True, (),
         *_ARB_TRIGGER_NO_FILTER),
    ("arb_review_cycles", "trg_arb_cycle_membership"):
        ("archie_validate_arb_cycle_membership", 21, True, True, True, (),
         *_ARB_TRIGGER_NO_FILTER),
    ("arb_review_cycles", "trg_arb_cycle_history"):
        ("archie_guard_arb_cycle_history", 27, False, False, False, (),
         *_ARB_TRIGGER_NO_FILTER),
    ("arb_review_items", "trg_arb_review_cycle_membership"):
        ("archie_validate_arb_cycle_membership", 21, True, True, True, (),
         *_ARB_TRIGGER_NO_FILTER),
    ("arb_review_items", "trg_arb_review_cycle_history"):
        ("archie_guard_arb_cycle_history", 27, False, False, False, (),
         *_ARB_TRIGGER_NO_FILTER),
    ("arb_subject_evidence_snapshots", "trg_reject_arb_subject_snapshot_mutation"):
        ("archie_reject_arb_subject_snapshot_mutation", 27, False, False, False, (),
         *_ARB_TRIGGER_NO_FILTER),
    ("decision_briefs", "trg_arb_decision_brief_tenant_history"):
        ("archie_guard_arb_subject_tenant", 19, False, False, False,
         ("organization_id",), *_ARB_TRIGGER_NO_FILTER),
    ("solutions", "trg_arb_solution_tenant_history"):
        ("archie_guard_arb_subject_tenant", 19, False, False, False,
         ("organization_id",), *_ARB_TRIGGER_NO_FILTER),
    ("architecture_models", "trg_arb_architecture_model_tenant_history"):
        ("archie_guard_arb_subject_tenant", 19, False, False, False,
         ("organization_id",), *_ARB_TRIGGER_NO_FILTER),
    ("architecture_decisions", "trg_arb_adr_tenant_history"):
        ("archie_guard_arb_subject_tenant", 19, False, False, False,
         ("organization_id",), *_ARB_TRIGGER_NO_FILTER),
}


_ARB_CHECK_TOKEN_PATTERN = re.compile(
    r"'(?:''|[^'])*'|<>|>=|<=|=|>|<|[(),]|[a-z_][a-z0-9_]*|\d+"
)


def _arb_check_tokens(value):
    """Retain boolean grouping while normalizing PostgreSQL's type/IN rewrites."""
    value = value.lower().replace('"', "")
    value = re.sub(r"::(?:character\s+varying|text)(?:\[\])?", "", value)
    value = re.sub(
        r"\b([a-z_][a-z0-9_]*)\s*=\s*any\s*"
        r"\(\s*array\s*\[(.*?)\]\s*\)",
        lambda match: f"{match.group(1)} in ({match.group(2)})",
        value,
        flags=re.S,
    )
    tokens = []
    position = 0
    for match in _ARB_CHECK_TOKEN_PATTERN.finditer(value):
        # Anything skipped between tokens is a character the pattern cannot
        # express (arithmetic, regex or subscript operators). Dropping it would
        # let a weaker predicate normalize to the canonical token stream and be
        # accepted as equivalent, so refuse to compare rather than guess.
        if value[position:match.start()].strip():
            raise ValueError(
                "unrecognized token in ARB check constraint: "
                f"{value[position:match.start()].strip()!r}"
            )
        tokens.append(match.group(0))
        position = match.end()
    if value[position:].strip():
        raise ValueError(
            "unrecognized token in ARB check constraint: "
            f"{value[position:].strip()!r}"
        )
    return tokens


def _arb_check_structure(value):
    tokens = _arb_check_tokens(value)
    if tokens and tokens[0] == "check":
        tokens = tokens[1:]
    position = 0

    def combine(operator, nodes):
        flattened = []
        for node in nodes:
            if node[0] == operator:
                flattened.extend(node[1])
            else:
                flattened.append(node)
        return (operator, tuple(flattened))

    def parse_or():
        nonlocal position
        nodes = [parse_and()]
        while position < len(tokens) and tokens[position] == "or":
            position += 1
            nodes.append(parse_and())
        return nodes[0] if len(nodes) == 1 else combine("or", nodes)

    def parse_and():
        nonlocal position
        nodes = [parse_primary()]
        while position < len(tokens) and tokens[position] == "and":
            position += 1
            nodes.append(parse_primary())
        return nodes[0] if len(nodes) == 1 else combine("and", nodes)

    def parse_primary():
        nonlocal position
        if position < len(tokens) and tokens[position] == "(":
            position += 1
            node = parse_or()
            if position >= len(tokens) or tokens[position] != ")":
                raise ValueError("unbalanced typed ARB check definition")
            position += 1
            return node
        atom = []
        nested = 0
        while position < len(tokens):
            token = tokens[position]
            if nested == 0 and token in {"and", "or", ")"}:
                break
            if token == "(":
                nested += 1
            elif token == ")":
                nested -= 1
            atom.append(token)
            position += 1
        if not atom:
            raise ValueError("empty typed ARB check expression")
        return ("atom", tuple(atom))

    structure = parse_or()
    if position != len(tokens):
        raise ValueError("unparsed typed ARB check definition")
    return structure


def _arb_normalize_catalog_definition(value):
    return " ".join(value.lower().replace('"', "").split())


def _arb_check_matches(row, table_name, expected_definition):
    if not (
        row is not None
        and row.relname == table_name
        and row.contype == "c"
        and row.convalidated
    ):
        return False
    # The expected definition is ours, so an untokenizable one is a bug here and
    # must surface. The live definition is not ours: a predicate the strict
    # tokenizer cannot read is drift to be rebuilt, not a reason to fail boot.
    expected_structure = _arb_check_structure(expected_definition)
    try:
        actual_structure = _arb_check_structure(row.definition)
    except ValueError:
        return False
    return actual_structure == expected_structure


def _arb_trigger_matches(state, expected, schema_name):
    if state is None or state[0] != "O":
        return False
    if state[3] != schema_name:
        return False
    actual = (state[2], state[1], *state[4:])
    return actual == expected


def _arb_function_body(function_sql):
    match = re.search(r"\bAS\s+\$\$(.*?)\$\$\s*$", function_sql, re.I | re.S)
    if not match:
        raise RuntimeError("typed ARB function SQL has no canonical body")
    return match.group(1).strip()


def _arb_normalize_function_body(value):
    return re.sub(r"\s+", " ", value).strip()


def _arb_expected_function_state(quoted_schema, schema_name):
    from app.models.transformation_decision import (
        _ARB_SUBJECT_SNAPSHOT_IMMUTABILITY_BODY,
    )

    schema_search_path = f"search_path=pg_catalog, {schema_name}"
    return {
        "archie_validate_arb_cycle_membership": (
            _arb_function_body(_arb_membership_function_sql(quoted_schema)),
            (schema_search_path,),
        ),
        "archie_guard_arb_cycle_history": (
            _arb_function_body(_arb_history_function_sql(quoted_schema)),
            (schema_search_path,),
        ),
        "archie_guard_arb_subject_tenant": (
            _arb_function_body(_arb_parent_tenant_function_sql(quoted_schema)),
            (schema_search_path,),
        ),
        "archie_reject_arb_subject_snapshot_mutation": (
            _ARB_SUBJECT_SNAPSHOT_IMMUTABILITY_BODY,
            ("search_path=pg_catalog",),
        ),
    }


def _arb_model_check_definitions():
    model_tables = {
        "ARBSubjectEvidenceSnapshot": db.metadata.tables[
            "arb_subject_evidence_snapshots"
        ],
        "ARBReviewCycle": ARBReviewCycle.__table__,
        "ARBReviewItem": ARBReviewItem.__table__,
    }
    definitions = {}
    for name, (_table_name, model_name) in _ARB_CHECK_SPECS.items():
        constraint = next(
            item for item in model_tables[model_name].constraints if item.name == name
        )
        definitions[name] = f"CHECK ({constraint.sqltext})"
    return definitions


def _arb_expected_index_definition(schema_name, name, table_name, columns, predicate):
    definition = (
        f"CREATE UNIQUE INDEX {name} ON {schema_name}.{table_name} "
        f"USING btree ({', '.join(columns)})"
    )
    if predicate:
        definition += f" WHERE ({predicate})"
    return definition


def _arb_fk_matches(row, schema_name, expected):
    source_table, source_columns, target_table, target_columns = expected
    return bool(
        row is not None
        and row.contype == "f"
        and row.source_table == source_table
        and tuple(row.source_columns) == source_columns
        and row.target_schema == schema_name
        and row.target_table == target_table
        and tuple(row.target_columns) == target_columns
        and row.confupdtype == "a"
        and row.confdeltype == "r"
        and row.confmatchtype == "s"
        and not row.condeferrable
        and not row.condeferred
        and row.convalidated
    )


def _arb_catalog_state(connection):
    from sqlalchemy import text

    rows = connection.execute(
        text(
            """
            SELECT cls.relname, trigger.tgname, trigger.tgenabled,
                   trigger.tgtype, procedure.proname,
                   procedure_namespace.nspname AS procedure_schema,
                   (trigger.tgconstraint <> 0) AS is_constraint,
                   COALESCE(constraint_row.condeferrable, false) AS is_deferrable,
                   COALESCE(constraint_row.condeferred, false) AS is_deferred,
                   ARRAY(
                       SELECT attribute.attname
                       FROM unnest(trigger.tgattr::smallint[])
                            WITH ORDINALITY update_column(attnum, ord)
                       JOIN pg_attribute attribute
                         ON attribute.attrelid = trigger.tgrelid
                        AND attribute.attnum = update_column.attnum
                       ORDER BY update_column.ord
                   ) AS update_columns,
                   pg_get_expr(trigger.tgqual, trigger.tgrelid, true)
                       AS predicate,
                   trigger.tgnargs AS argument_count,
                   encode(trigger.tgargs, 'hex') AS arguments_hex
            FROM pg_trigger trigger
            JOIN pg_class cls ON cls.oid = trigger.tgrelid
            JOIN pg_namespace namespace ON namespace.oid = cls.relnamespace
            JOIN pg_proc procedure ON procedure.oid = trigger.tgfoid
            JOIN pg_namespace procedure_namespace
              ON procedure_namespace.oid = procedure.pronamespace
            LEFT JOIN pg_constraint constraint_row
              ON constraint_row.oid = trigger.tgconstraint
            WHERE namespace.nspname = current_schema()
              AND NOT trigger.tgisinternal
            """
        )
    ).all()
    return {
        (row.relname, row.tgname): (
            row.tgenabled,
            row.tgtype,
            row.proname,
            row.procedure_schema,
            row.is_constraint,
            row.is_deferrable,
            row.is_deferred,
            tuple(row.update_columns),
            row.predicate,
            row.argument_count,
            row.arguments_hex,
        )
        for row in rows
    }


def _arb_check_state(connection, schema_name):
    from sqlalchemy import text

    rows = connection.execute(
        text(
            """
            SELECT constraint_row.conname, cls.relname, constraint_row.contype,
                   constraint_row.convalidated,
                   pg_get_constraintdef(constraint_row.oid, true) AS definition
            FROM pg_constraint constraint_row
            JOIN pg_class cls ON cls.oid = constraint_row.conrelid
            JOIN pg_namespace namespace ON namespace.oid = cls.relnamespace
            WHERE namespace.nspname = :schema_name
              AND constraint_row.conname = ANY(:names)
            """
        ),
        {"schema_name": schema_name, "names": list(_ARB_CHECK_SPECS)},
    ).all()
    # conname is unique per table, not per schema: a same-named decoy on another
    # table would otherwise evict the real constraint from this dict.
    return {(row.relname, row.conname): row for row in rows}


def _arb_index_state(connection, schema_name):
    from sqlalchemy import text

    rows = connection.execute(
        text(
            """
            SELECT index_cls.relname AS index_name, table_cls.relname AS table_name,
                   index_row.indisunique, index_row.indisvalid, index_row.indisready,
                   ARRAY(
                       SELECT pg_get_indexdef(index_row.indexrelid, ordinal, true)
                       FROM generate_series(1, index_row.indnkeyatts) ordinal
                   ) AS columns,
                   pg_get_expr(index_row.indpred, index_row.indrelid) AS predicate,
                   pg_get_indexdef(index_row.indexrelid, 0, false) AS definition
            FROM pg_index index_row
            JOIN pg_class index_cls ON index_cls.oid = index_row.indexrelid
            JOIN pg_class table_cls ON table_cls.oid = index_row.indrelid
            JOIN pg_namespace namespace ON namespace.oid = table_cls.relnamespace
            WHERE namespace.nspname = :schema_name
              AND index_cls.relname = ANY(:names)
            """
        ),
        {"schema_name": schema_name, "names": list(_ARB_INDEX_SPECS)},
    ).all()
    return {row.index_name: row for row in rows}


def _arb_fk_state(connection, schema_name):
    from sqlalchemy import text

    rows = connection.execute(
        text(
            """
            SELECT constraint_row.conname, constraint_row.contype,
                   source.relname AS source_table,
                   target_namespace.nspname AS target_schema,
                   target.relname AS target_table, constraint_row.convalidated,
                   constraint_row.confupdtype, constraint_row.confdeltype,
                   constraint_row.confmatchtype, constraint_row.condeferrable,
                   constraint_row.condeferred,
                   ARRAY(
                       SELECT attribute.attname
                       FROM unnest(constraint_row.conkey) WITH ORDINALITY key(attnum, ord)
                       JOIN pg_attribute attribute
                         ON attribute.attrelid = source.oid
                        AND attribute.attnum = key.attnum
                       ORDER BY key.ord
                   ) AS source_columns,
                   ARRAY(
                       SELECT attribute.attname
                       FROM unnest(constraint_row.confkey) WITH ORDINALITY key(attnum, ord)
                       JOIN pg_attribute attribute
                         ON attribute.attrelid = target.oid
                        AND attribute.attnum = key.attnum
                       ORDER BY key.ord
                   ) AS target_columns
            FROM pg_constraint constraint_row
            JOIN pg_class source ON source.oid = constraint_row.conrelid
            LEFT JOIN pg_class target ON target.oid = constraint_row.confrelid
            LEFT JOIN pg_namespace target_namespace
              ON target_namespace.oid = target.relnamespace
            JOIN pg_namespace namespace ON namespace.oid = source.relnamespace
            WHERE namespace.nspname = :schema_name
              AND constraint_row.conname = ANY(:names)
            """
        ),
        {"schema_name": schema_name, "names": list(_ARB_FK_SPECS)},
    ).all()
    # Keyed by (table, name) for the same reason as _arb_check_state.
    return {(row.source_table, row.conname): row for row in rows}


def inspect_arb_cycle_constraints(connection):
    """Return actionable drift for typed ARB checks, indexes and triggers."""
    if connection.dialect.name != "postgresql":
        return ["unsupported_dialect"]
    from sqlalchemy import text

    schema_name = connection.scalar(text("SELECT current_schema()"))
    tables = set(
        connection.scalars(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = :schema_name"
            ),
            {"schema_name": schema_name},
        )
    )
    required_tables = {
        table for table, _name in _ARB_TRIGGER_SPECS
    } | {spec[0] for spec in _ARB_FK_SPECS.values()}
    drift = [f"table_missing:{table}" for table in sorted(required_tables - tables)]
    if drift:
        return drift

    check_state = _arb_check_state(connection, schema_name)
    check_definitions = _arb_model_check_definitions()
    for name, (table_name, _model_name) in _ARB_CHECK_SPECS.items():
        row = check_state.get((table_name, name))
        if row is None:
            drift.append(f"constraint_missing:{name}")
        elif not _arb_check_matches(row, table_name, check_definitions[name]):
            drift.append(f"constraint_malformed:{name}")

    index_state = _arb_index_state(connection, schema_name)
    for name, (table_name, columns, predicate) in _ARB_INDEX_SPECS.items():
        row = index_state.get(name)
        if row is None:
            drift.append(f"index_missing:{name}")
            continue
        expected_definition = _arb_expected_index_definition(
            schema_name, name, table_name, columns, predicate
        )
        if (
            row.table_name != table_name
            or not row.indisunique
            or not row.indisvalid
            or not row.indisready
            or _arb_normalize_catalog_definition(row.definition)
            != _arb_normalize_catalog_definition(expected_definition)
        ):
            drift.append(f"index_malformed:{name}")

    fk_state = _arb_fk_state(connection, schema_name)
    for name, (source_table, source_columns, target_table, target_columns) in (
        _ARB_FK_SPECS.items()
    ):
        row = fk_state.get((source_table, name))
        if row is None:
            drift.append(f"foreign_key_missing:{name}")
        elif not _arb_fk_matches(
            row,
            schema_name,
            (source_table, source_columns, target_table, target_columns),
        ):
            drift.append(f"foreign_key_malformed:{name}")

    trigger_state = _arb_catalog_state(connection)
    for key, expected in _ARB_TRIGGER_SPECS.items():
        state = trigger_state.get(key)
        if state is None:
            drift.append(f"trigger_missing:{key[0]}.{key[1]}")
        elif state[0] != "O":
            drift.append(f"trigger_disabled:{key[0]}.{key[1]}")
        elif not _arb_trigger_matches(state, expected, schema_name):
            drift.append(f"trigger_malformed:{key[0]}.{key[1]}")

    quote = connection.dialect.identifier_preparer.quote
    expected_functions = _arb_expected_function_state(quote(schema_name), schema_name)
    function_rows = connection.execute(
        text(
            """
            SELECT procedure.proname, procedure.prosrc AS source,
                   procedure.proconfig, language.lanname,
                   format_type(procedure.prorettype, NULL) AS result_type
            FROM pg_proc procedure
            JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
            JOIN pg_language language ON language.oid = procedure.prolang
            WHERE namespace.nspname = :schema_name
              AND procedure.proname = ANY(:names)
              AND procedure.pronargs = 0
            """
        ),
        {"schema_name": schema_name, "names": list(expected_functions)},
    ).all()
    functions = {row.proname: row for row in function_rows}
    for name, (expected_body, expected_config) in expected_functions.items():
        row = functions.get(name)
        if row is None:
            drift.append(f"function_missing:{name}")
        elif (
            _arb_normalize_function_body(row.source)
            != _arb_normalize_function_body(expected_body)
            or tuple(row.proconfig or ()) != expected_config
            or row.lanname != "plpgsql"
            or row.result_type != "trigger"
        ):
            drift.append(f"function_malformed:{name}")
    return sorted(drift)


def _arb_unreconciled_shape(connection, schema_name, model_tables):
    """Return a description of typed ARB columns the live schema is still missing.

    These guards presuppose the fully reconciled column shape: the CHECK
    constraints, foreign keys and indexes all name columns that reconcile-schema
    ADDs to the long-lived arb_review_items table.

    create_all() reaches this function through ARBReviewCycle's after_create
    hook, and the deploy chain runs init-db BEFORE reconcile-schema. On every
    existing database that ordering puts us here while arb_review_items still
    lacks review_cycle_id and its six siblings, so ADD CONSTRAINT fails with
    UndefinedColumn, create_all() aborts, init-db exits non-zero and the
    container never reaches gunicorn.

    So report the gap instead of installing a half-built guard set. Whichever
    columns are missing, reconcile-schema adds them and then calls this function
    again -- at which point the shape is complete and every guard installs.
    """
    from sqlalchemy import text

    missing = []
    for table in model_tables.values():
        live_columns = set(
            connection.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema_name AND table_name = :table_name"
                ),
                {"schema_name": schema_name, "table_name": table.name},
            )
        )
        if not live_columns:
            missing.append(f"{table.name} (table absent)")
            continue
        absent = {column.name for column in table.columns} - live_columns
        if absent:
            missing.append(f"{table.name}.({','.join(sorted(absent))})")
    return missing


def _validate_without_aborting_boot(connection, qualified_table, constraint_name, kind):
    """VALIDATE a NOT VALID constraint; never let legacy rows abort the boot.

    This runs from an ``after_create`` metadata event, i.e. inside
    ``db.create_all()`` — which is what ``flask init-db`` calls (manage.py) and
    what the container runs on EVERY start before gunicorn. Postgres'
    ``VALIDATE CONSTRAINT`` scans the whole table and raises if any *existing*
    row fails, so validating unconditionally here means one legacy
    ``arb_review_cycles`` row stops the application from booting at all. That
    is exactly what happened: the constraint is dropped and re-added whenever
    its definition drifts from the model (see the caller), so any release that
    changes the typed-ARB shape re-validates it against all historical rows.

    Leaving the constraint ``NOT VALID`` on failure is not a loosening of the
    guarantee that matters. Postgres enforces a ``NOT VALID`` constraint on
    every INSERT and UPDATE; ``NOT VALID`` only means "the rows that were
    already here have not been proven to conform". So new and modified ARB
    cycles are still fully checked, and the pre-existing offenders are
    reported rather than silently accepted — the failure becomes an actionable
    administrative state instead of a crash loop.

    The statement runs in a SAVEPOINT because a failed statement otherwise
    poisons the surrounding transaction and every later DDL in create_all()
    fails with InFailedSqlTransaction.
    """
    from sqlalchemy.exc import IntegrityError

    try:
        with connection.begin_nested():
            connection.exec_driver_sql(
                f"ALTER TABLE {qualified_table} VALIDATE CONSTRAINT {constraint_name}"
            )
        return True
    except IntegrityError:
        logger.error(
            "typed ARB %s %s on %s could not be validated: existing rows do not "
            "match the current shape. The constraint is INSTALLED AND ENFORCED "
            "for all new and updated rows, but the pre-existing rows have not "
            "been proven to conform, so it remains NOT VALID. Boot continues. "
            "To find the offenders run: SELECT * FROM %s WHERE NOT (<the "
            "constraint's CHECK expression, from pg_get_constraintdef); then, "
            "once corrected, run: ALTER TABLE %s VALIDATE CONSTRAINT %s;",
            kind,
            constraint_name,
            qualified_table,
            qualified_table,
            qualified_table,
            constraint_name,
        )
        return False


def ensure_arb_cycle_constraints(connection):
    """Reconcile commit-time membership and immutable typed ARB history."""
    if connection.dialect.name != "postgresql":
        return
    from sqlalchemy import text

    schema_name = connection.scalar(text("SELECT current_schema()"))
    quote = connection.dialect.identifier_preparer.quote
    quoted_schema = quote(schema_name)
    connection.exec_driver_sql(
        "SELECT pg_advisory_xact_lock(hashtext('archie_typed_arb_constraints'))"
    )
    tables = set(
        connection.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname = :schema_name"),
            {"schema_name": schema_name},
        )
    )

    model_tables = {
        "ARBSubjectEvidenceSnapshot": db.metadata.tables[
            "arb_subject_evidence_snapshots"
        ],
        "ARBReviewCycle": ARBReviewCycle.__table__,
        "ARBReviewItem": ARBReviewItem.__table__,
    }
    unreconciled = _arb_unreconciled_shape(connection, schema_name, model_tables)
    if unreconciled:
        # Not an error: reconcile-schema adds these columns and calls us again.
        logger.info(
            "typed ARB guards deferred until reconcile-schema adds: %s",
            "; ".join(unreconciled),
        )
        return

    check_state = _arb_check_state(connection, schema_name)
    for constraint_name, (table_name, model_name) in _ARB_CHECK_SPECS.items():
        if table_name not in tables:
            continue
        constraint = next(
            item
            for item in model_tables[model_name].constraints
            if item.name == constraint_name
        )
        row = check_state.get((table_name, constraint_name))
        expected_definition = f"CHECK ({constraint.sqltext})"
        if row and not _arb_check_matches(row, table_name, expected_definition):
            actual_table = f"{quoted_schema}.{quote(row.relname)}"
            connection.exec_driver_sql(
                f"ALTER TABLE {actual_table} DROP CONSTRAINT {quote(constraint_name)}"
            )
            row = None
        if row:
            continue
        qualified_table = f"{quoted_schema}.{quote(table_name)}"
        connection.exec_driver_sql(
            f"ALTER TABLE {qualified_table} ADD CONSTRAINT {quote(constraint_name)} "
            f"CHECK ({constraint.sqltext}) NOT VALID"
        )
        _validate_without_aborting_boot(
            connection, qualified_table, quote(constraint_name), "check constraint"
        )

    fk_state = _arb_fk_state(connection, schema_name)
    for name, (source_table, source_columns, target_table, target_columns) in (
        _ARB_FK_SPECS.items()
    ):
        if source_table not in tables or target_table not in tables:
            continue
        row = fk_state.get((source_table, name))
        correct = _arb_fk_matches(
            row,
            schema_name,
            (source_table, source_columns, target_table, target_columns),
        )
        if correct:
            continue
        if row:
            actual_table = f"{quoted_schema}.{quote(row.source_table)}"
            connection.exec_driver_sql(
                f"ALTER TABLE {actual_table} DROP CONSTRAINT {quote(name)}"
            )
        qualified_source = f"{quoted_schema}.{quote(source_table)}"
        qualified_target = f"{quoted_schema}.{quote(target_table)}"
        local_sql = ", ".join(quote(column) for column in source_columns)
        remote_sql = ", ".join(quote(column) for column in target_columns)
        connection.exec_driver_sql(
            f"ALTER TABLE {qualified_source} ADD CONSTRAINT {quote(name)} "
            f"FOREIGN KEY ({local_sql}) REFERENCES {qualified_target} ({remote_sql}) "
            "MATCH SIMPLE ON UPDATE NO ACTION ON DELETE RESTRICT "
            "NOT DEFERRABLE NOT VALID"
        )
        _validate_without_aborting_boot(
            connection, qualified_source, quote(name), "foreign key"
        )

    # E2E-H: the ORM-auto-named FK a table got when it was first created
    # (e.g. arb_subject_evidence_snapshots_adr_id_fkey, generated from the
    # model's own db.ForeignKey(...) declaration by create_all()) is a
    # separate constraint object from the custom-named ones above
    # (fk_arb_subject_snapshot_adr). Changing the Python model's FK target
    # string, or the spec above, self-heals the custom-named constraint
    # but never touches this older one -- create_all() does not alter
    # existing tables. Left in place, both remain active on the same
    # column: the model source correctly says "architecture_decisions",
    # the custom guard correctly points there too, and this leftover one
    # still requires "architecture_decision_records", 0 rows, so every
    # insert failed a constraint the fix above never reached. Drop it
    # explicitly wherever it still targets the dead table.
    stale_auto_fk_tables = (
        "arb_subject_evidence_snapshots",
        "arb_review_cycles",
        "arb_review_items",
        "arb_submission_events",
        "arb_decision_events",
        "arb_condition_evidence_records",
    )
    if "architecture_decision_records" in tables:
        stale_rows = connection.execute(
            text(
                """
                SELECT conrelid::regclass::text AS source_table, conname
                FROM pg_constraint
                WHERE confrelid = (
                    quote_ident(:schema_name) || '.architecture_decision_records'
                )::regclass
                  AND conrelid::regclass::text = ANY(:tables)
                """
            ),
            {"schema_name": schema_name, "tables": list(stale_auto_fk_tables)},
        ).all()
        for stale in stale_rows:
            qualified_stale_source = f"{quoted_schema}.{quote(stale.source_table)}"
            connection.exec_driver_sql(
                f"ALTER TABLE {qualified_stale_source} DROP CONSTRAINT {quote(stale.conname)}"
            )

    index_state = _arb_index_state(connection, schema_name)
    for name, (table_name, columns, predicate) in _ARB_INDEX_SPECS.items():
        if table_name not in tables:
            continue
        row = index_state.get(name)
        expected_definition = _arb_expected_index_definition(
            schema_name, name, table_name, columns, predicate
        )
        correct = row and (
            row.table_name == table_name
            and row.indisunique
            and row.indisvalid
            and row.indisready
            and _arb_normalize_catalog_definition(row.definition)
            == _arb_normalize_catalog_definition(expected_definition)
        )
        if correct:
            continue
        if row:
            backing_constraint = connection.execute(
                text(
                    """
                    SELECT constraint_row.conname, cls.relname
                    FROM pg_constraint constraint_row
                    JOIN pg_class cls ON cls.oid = constraint_row.conrelid
                    JOIN pg_namespace namespace ON namespace.oid = cls.relnamespace
                    WHERE namespace.nspname = :schema_name
                      AND constraint_row.conindid = (
                          SELECT index_cls.oid FROM pg_class index_cls
                          JOIN pg_namespace index_namespace
                            ON index_namespace.oid = index_cls.relnamespace
                          WHERE index_namespace.nspname = :schema_name
                            AND index_cls.relname = :index_name
                      )
                    """
                ),
                {"schema_name": schema_name, "index_name": name},
            ).first()
            if backing_constraint:
                actual_table = f"{quoted_schema}.{quote(backing_constraint.relname)}"
                connection.exec_driver_sql(
                    f"ALTER TABLE {actual_table} DROP CONSTRAINT {quote(backing_constraint.conname)}"
                )
            else:
                connection.exec_driver_sql(
                    f"DROP INDEX {quoted_schema}.{quote(name)}"
                )
        qualified_table = f"{quoted_schema}.{quote(table_name)}"
        column_sql = ", ".join(quote(column) for column in columns)
        statement = (
            f"CREATE UNIQUE INDEX {quote(name)} ON {qualified_table} ({column_sql})"
        )
        if predicate:
            statement += f" WHERE {predicate}"
        connection.exec_driver_sql(statement)

    from app.models.transformation_decision import (
        ensure_arb_subject_snapshot_immutability,
    )

    ensure_arb_subject_snapshot_immutability(connection)
    connection.exec_driver_sql(_arb_membership_function_sql(quoted_schema))
    connection.exec_driver_sql(_arb_history_function_sql(quoted_schema))
    connection.exec_driver_sql(_arb_parent_tenant_function_sql(quoted_schema))
    trigger_state = _arb_catalog_state(connection)
    parent_triggers = {
        "trg_arb_decision_brief_tenant_history",
        "trg_arb_solution_tenant_history",
        "trg_arb_architecture_model_tenant_history",
        "trg_arb_adr_tenant_history",
    }
    for (table_name, trigger_name), expected in _ARB_TRIGGER_SPECS.items():
        if table_name not in tables:
            continue
        qualified_table = f"{quoted_schema}.{quote(table_name)}"
        state = trigger_state.get((table_name, trigger_name))
        if _arb_trigger_matches(state, expected, schema_name):
            continue
        if state:
            connection.exec_driver_sql(
                f"DROP TRIGGER {quote(trigger_name)} ON {qualified_table}"
            )
        (
            function_name,
            _tgtype,
            is_constraint,
            _is_deferrable,
            _is_deferred,
            _update_columns,
            _predicate,
            _argument_count,
            _arguments_hex,
        ) = expected
        qualified_function = f"{quoted_schema}.{quote(function_name)}()"
        if is_constraint:
            connection.exec_driver_sql(
                f"CREATE CONSTRAINT TRIGGER {quote(trigger_name)} "
                f"AFTER INSERT OR UPDATE ON {qualified_table} "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                f"EXECUTE FUNCTION {qualified_function}"
            )
        elif trigger_name in parent_triggers:
            connection.exec_driver_sql(
                f"CREATE TRIGGER {quote(trigger_name)} BEFORE UPDATE OF organization_id "
                f"ON {qualified_table} FOR EACH ROW EXECUTE FUNCTION {qualified_function}"
            )
        else:
            connection.exec_driver_sql(
                f"CREATE TRIGGER {quote(trigger_name)} "
                f"BEFORE UPDATE OR DELETE ON {qualified_table} FOR EACH ROW "
                f"EXECUTE FUNCTION {qualified_function}"
            )


@event.listens_for(db.metadata, "after_create")
def _install_arb_cycle_constraints(_target, connection, **_kwargs):
    """Install the typed ARB guards once, after create_all has finished.

    This used to listen on ARBReviewCycle.__table__ and ARBReviewItem.__table__,
    which broke `flask init-db` on a BRAND-NEW database -- so every fresh
    install, which is the one path nobody re-runs to notice:

        DuplicateObject: la restriccion «fk_arb_review_item_cycle»
        para la relacion «arb_review_items» ya existe

    ARBReviewItem.review_cycle_id declares its FK with use_alter=True, so
    SQLAlchemy emits that constraint as a separate ALTER TABLE at the very END
    of create_all, after every table exists (that is what use_alter is for --
    the two tables reference each other). The per-table hook fired in the
    middle of that process and added the same *named* constraint first, so
    SQLAlchemy's own DDL then collided with it and create_all aborted.

    Listening on the metadata fires once, after create_all has emitted
    everything including its use_alter constraints, so the state check inside
    ensure_arb_cycle_constraints sees them and skips what already exists.
    """
    ensure_arb_cycle_constraints(connection)


class ARBReviewComment(TenantMixin, db.Model):
    """Comments and discussion on ARB review items."""

    __tablename__ = "arb_review_comments"

    id = db.Column(db.Integer, primary_key=True)
    # Phase B (Wave 4): TenantMixin enabled — backfill completed in Phase A.
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True, index=True)
    review_item_id = db.Column(db.Integer, db.ForeignKey("arb_review_items.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    comment_type = db.Column(
        db.String(30), default="general"
    )  # general, concern, recommendation, condition, approval
    content = db.Column(db.Text, nullable=False)

    # For threaded discussions
    parent_comment_id = db.Column(db.Integer, db.ForeignKey("arb_review_comments.id"))

    # Status
    resolved = db.Column(db.Boolean, default=False)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    resolved_at = db.Column(db.DateTime)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    review_item = db.relationship("ARBReviewItem", back_populates="comments")
    user = db.relationship("User", foreign_keys=[user_id], backref="arb_comments")
    resolved_by = db.relationship("User", foreign_keys=[resolved_by_id])
    replies = db.relationship("ARBReviewComment", backref=db.backref("parent", remote_side=[id]))


class ARBCapabilityImpact(TenantMixin, db.Model):
    """Links ARB review items to impacted capabilities."""

    __tablename__ = "arb_capability_impacts"

    id = db.Column(db.Integer, primary_key=True)
    # Phase B (Wave 4): TenantMixin enabled — backfill completed in Phase A.
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True, index=True)
    review_item_id = db.Column(db.Integer, db.ForeignKey("arb_review_items.id"), nullable=False)
    capability_id = db.Column(db.Integer, db.ForeignKey("unified_capabilities.id"), nullable=False)

    # Impact analysis
    impact_type = db.Column(
        db.String(50)
    )  # enhances, replaces, deprecates, new_implementation, modifies
    impact_level = db.Column(db.String(20))  # high, medium, low
    impact_description = db.Column(db.Text)

    # Gap analysis integration
    addresses_gap = db.Column(db.Boolean, default=False)
    gap_id = db.Column(db.Integer)  # Reference to capability gap if applicable

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    review_item = db.relationship("ARBReviewItem", back_populates="capability_links")
    capability = db.relationship("UnifiedCapability", backref="arb_impacts")

    __table_args__ = (
        db.UniqueConstraint("review_item_id", "capability_id", name="uix_arb_capability_impact"),
    )


# Global reference data (shared across tenants) — intentionally NOT TenantMixin; org column unused. See wave-4 Task-2 review.
class ARBGovernanceStandard(db.Model):
    """
    Architecture governance standards and policies.

    Defines the criteria and checklists used for ARB reviews.
    """

    __tablename__ = "arb_governance_standards"

    id = db.Column(db.Integer, primary_key=True)
    # Global reference data (shared across tenants) — intentionally NOT TenantMixin; org column unused. See wave-4 Task-2 review.
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True, index=True)
    code = db.Column(db.String(50), unique=True, nullable=False)  # STD-SEC - 001
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)

    # Classification
    category = db.Column(
        db.String(50)
    )  # security, integration, data, performance, compliance, architecture
    togaf_phase = db.Column(db.String(50))  # Applicable TOGAF ADM phase
    archimate_layer = db.Column(db.String(30))  # Applicable ArchiMate layer

    # Standard details
    requirements = db.Column(db.JSON)  # List of specific requirements
    checklist_items = db.Column(db.JSON)  # Checklist for reviewers
    exceptions_allowed = db.Column(db.Boolean, default=True)
    exception_process = db.Column(db.Text)

    # Applicability
    applies_to_review_types = db.Column(db.JSON)  # List of review types this applies to
    mandatory = db.Column(db.Boolean, default=True)

    # Status
    status = db.Column(db.String(20), default="active")  # draft, active, deprecated
    effective_date = db.Column(db.Date)
    review_date = db.Column(db.Date)  # Next review date

    # Ownership
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    owner = db.relationship("User", backref="owned_arb_standards")

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "togaf_phase": self.togaf_phase,
            "archimate_layer": self.archimate_layer,
            "requirements": self.requirements,
            "checklist_items": self.checklist_items,
            "mandatory": self.mandatory,
            "status": self.status,
        }


# Default governance standards aligned with TOGAF and ArchiMate
DEFAULT_GOVERNANCE_STANDARDS = [
    {
        "code": "STD-ARCH - 001",
        "name": "Architecture Documentation Standard",
        "description": "All architecture artifacts must be properly documented with ArchiMate viewpoints",
        "category": "architecture",
        "requirements": [
            "Solution must include relevant ArchiMate viewpoints",
            "All architecture decisions must be documented as ADRs",
            "Traceability to business requirements must be established",
        ],
        "checklist_items": [
            {"item": "ArchiMate diagrams provided", "required": True},
            {"item": "ADR created for significant decisions", "required": True},
            {"item": "Business requirements traceability matrix", "required": False},
            {"item": "TOGAF phase artifacts complete", "required": True},
        ],
        "mandatory": True,
    },
    {
        "code": "STD-CAP - 001",
        "name": "Capability Alignment Standard",
        "description": "Solutions must align with enterprise capability model",
        "category": "capability",
        "requirements": [
            "Solution must map to one or more business capabilities",
            "Capability gaps addressed must be identified",
            "Impact on existing capability implementations assessed",
        ],
        "checklist_items": [
            {"item": "Capability mapping completed", "required": True},
            {"item": "Gap analysis performed", "required": True},
            {"item": "Existing capability impact assessed", "required": True},
            {"item": "Capability roadmap alignment verified", "required": False},
        ],
        "mandatory": True,
    },
    {
        "code": "STD-SEC - 001",
        "name": "Security Architecture Standard",
        "description": "All solutions must meet enterprise security requirements",
        "category": "security",
        "requirements": [
            "Security architecture review completed",
            "Data classification and protection defined",
            "Authentication and authorization mechanisms specified",
            "Compliance with security policies verified",
        ],
        "checklist_items": [
            {"item": "Security architecture diagram provided", "required": True},
            {"item": "Data classification completed", "required": True},
            {"item": "AuthN/AuthZ approach defined", "required": True},
            {"item": "Threat modeling completed", "required": False},
            {"item": "Security controls mapped", "required": True},
        ],
        "mandatory": True,
    },
    {
        "code": "STD-INT - 001",
        "name": "Integration Architecture Standard",
        "description": "Integration patterns must follow enterprise standards",
        "category": "integration",
        "requirements": [
            "Integration patterns must be from approved catalog",
            "API specifications must follow enterprise standards",
            "Data flow and transformation logic documented",
        ],
        "checklist_items": [
            {"item": "Integration pattern identified", "required": True},
            {"item": "API specification provided (OpenAPI/AsyncAPI)", "required": True},
            {"item": "Data mapping documented", "required": True},
            {"item": "Error handling strategy defined", "required": True},
        ],
        "mandatory": True,
    },
    {
        "code": "STD-TECH - 001",
        "name": "Technology Selection Standard",
        "description": "Technology selections must align with enterprise standards",
        "category": "technology",
        "requirements": [
            "Technologies must be from approved technology radar",
            "Vendor assessment completed for new technologies",
            "Total cost of ownership analyzed",
        ],
        "checklist_items": [
            {"item": "Technologies on approved radar", "required": True},
            {"item": "Vendor assessment completed", "required": False},
            {"item": "TCO analysis provided", "required": True},
            {"item": "Skills availability assessed", "required": True},
        ],
        "mandatory": True,
    },
]


# Backwards-compatible alias used by older modules
ARBSession = ArchitectureReviewBoard


# ---------------------------------------------------------------------------
# AG-003: Derogation — formal architecture waiver with expiry and conditions
# ---------------------------------------------------------------------------

if not _FAST_INIT:

    class Derogation(db.Model):
        """Formal architecture waiver approved by the ARB.

        A Derogation records a time-bounded exception to a governance standard,
        linking to the originating ARBReviewItem, the granted conditions, and
        an expiry date after which the standard must be fully met.

        TOGAF ADM Phase G: Implementation Governance — waiver management.
        """

        __tablename__ = "arb_derogations"
        __table_args__ = {"extend_existing": True}

        id = db.Column(db.Integer, primary_key=True)

        # FK to the ARB review item that requested the waiver
        arb_review_item_id = db.Column(
            db.Integer,
            db.ForeignKey("arb_review_items.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        )

        # Identity
        derogation_number = db.Column(db.String(50), unique=True, nullable=False)
        title = db.Column(db.String(255), nullable=False)
        rationale = db.Column(db.Text, nullable=False)

        # Conditions under which the waiver is granted
        conditions = db.Column(db.Text, nullable=True)

        # Governance lifecycle
        status = db.Column(db.String(30), nullable=False, default="pending")
        # Allowed status values: pending, approved, rejected, expired, revoked

        expiry_date = db.Column(db.Date, nullable=True)
        granted_by = db.Column(db.String(100), nullable=True)
        granted_at = db.Column(db.DateTime, nullable=True)

        # ADM phase reference
        adm_phase = db.Column(db.String(10), nullable=True)  # e.g. "G", "H"

        # Timestamps
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        updated_at = db.Column(
            db.DateTime,
            default=datetime.utcnow,
            onupdate=datetime.utcnow,
            nullable=False,
        )

        # Relationships
        review_item = db.relationship(
            "ARBReviewItem",
            backref=db.backref("derogations", lazy="dynamic"),
            foreign_keys=[arb_review_item_id],
        )

        def __init__(self, **kwargs):
            kwargs.setdefault("status", "pending")
            super().__init__(**kwargs)

        def is_expired(self) -> bool:
            """Return True if expiry_date has passed."""
            if self.expiry_date is None:
                return False
            return datetime.utcnow().date() > self.expiry_date

        def __repr__(self) -> str:
            return f"<Derogation {self.derogation_number} status={self.status}>"


# ---------------------------------------------------------------------------
# CM-001: ChangeRequest — formal architecture change request lifecycle model
# ---------------------------------------------------------------------------

if not _FAST_INIT:

    class ChangeRequest(db.Model):
        """Formal architecture change request for TOGAF Phase H governance.

        Records a proposed change to the current architecture baseline.
        When approved, an ADM cycle is triggered (adm_cycle_triggered=True)
        to process the change through relevant ADM phases.

        TOGAF ADM Phase H: Architecture Change Management.
        """

        __tablename__ = "arb_change_requests"
        __table_args__ = {"extend_existing": True}

        id = db.Column(db.Integer, primary_key=True)

        # Identity
        change_request_number = db.Column(db.String(50), unique=True, nullable=False)
        title = db.Column(db.String(255), nullable=False)
        description = db.Column(db.Text, nullable=False)

        # Classification
        change_type = db.Column(db.String(50), nullable=False)
        # Allowed: simplification, exception, business_change, technology_change,
        #          correction, governance_change

        impact_level = db.Column(db.String(20), nullable=True)
        # Allowed: low, medium, high, critical

        # ADM cycle management
        adm_cycle_triggered = db.Column(db.Boolean, nullable=False, default=False)
        adm_phases_affected = db.Column(db.Text, nullable=True)  # comma-separated phases

        # Lifecycle
        status = db.Column(db.String(30), nullable=False, default="draft")
        # Allowed: draft, submitted, under_review, approved, rejected, withdrawn, implemented

        submitted_by = db.Column(db.String(100), nullable=True)
        submitted_at = db.Column(db.DateTime, nullable=True)
        reviewed_by = db.Column(db.String(100), nullable=True)
        reviewed_at = db.Column(db.DateTime, nullable=True)
        decision_rationale = db.Column(db.Text, nullable=True)

        # Optional FK to the originating ARB review item
        arb_review_item_id = db.Column(
            db.Integer,
            db.ForeignKey("arb_review_items.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        )

        # Timestamps
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        updated_at = db.Column(
            db.DateTime,
            default=datetime.utcnow,
            onupdate=datetime.utcnow,
            nullable=False,
        )

        # Relationships
        review_item = db.relationship(
            "ARBReviewItem",
            backref=db.backref("change_requests", lazy="dynamic"),
            foreign_keys=[arb_review_item_id],
        )

        def __init__(self, **kwargs):
            kwargs.setdefault("status", "draft")
            kwargs.setdefault("adm_cycle_triggered", False)
            super().__init__(**kwargs)

        def __repr__(self) -> str:
            return f"<ChangeRequest {self.change_request_number} type={self.change_type} status={self.status}>"


# ---------------------------------------------------------------------------
# DOC-001: ARBDocument — supporting document attachments for ARB governance
# ---------------------------------------------------------------------------

if not _FAST_INIT:

    class ARBDocument(TenantMixin, db.Model):
        """File attachment for ARB change requests and review items.

        Provides a governance-trail document store. Each row represents one
        uploaded file attached to either a ChangeRequest or an ARBReviewItem.
        Exactly one FK must be non-null (enforced at the application layer).

        Document types:
          supporting   — background context, architecture diagrams
          evidence     — test results, benchmarks, compliance proof
          decision     — decision record, approval memo
          minutes      — meeting minutes or email thread
        """

        __tablename__ = "arb_documents"
        __table_args__ = {"extend_existing": True}

        id = db.Column(db.Integer, primary_key=True)
        # Phase B (Wave 4): TenantMixin enabled — backfill completed in Phase A.
        organization_id = db.Column(
            db.Integer, db.ForeignKey("organizations.id"), nullable=True, index=True
        )

        # Polymorphic parent — exactly one must be set.
        # change_request_id → architecture_change_requests (ArchitectureChangeRequest, Phase H form)
        # review_item_id    → arb_review_items (ARBReviewItem, solution governance review)
        change_request_id = db.Column(
            db.Integer,
            db.ForeignKey("architecture_change_requests.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        )
        review_item_id = db.Column(
            db.Integer,
            db.ForeignKey("arb_review_items.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        )

        # File metadata
        original_name = db.Column(db.String(255), nullable=False)
        stored_name = db.Column(db.String(255), nullable=False)   # secure_filename result
        file_path = db.Column(db.String(512), nullable=False)      # relative to app root
        file_size = db.Column(db.Integer, nullable=True)           # bytes
        mime_type = db.Column(db.String(100), nullable=True)

        # Classification
        document_type = db.Column(db.String(50), nullable=False, default="supporting")

        # Audit
        uploaded_by_id = db.Column(
            db.Integer,
            db.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        )
        uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

        # Relationships
        change_request = db.relationship(
            "ArchitectureChangeRequest",
            backref=db.backref("arb_documents", lazy="dynamic"),
            foreign_keys=[change_request_id],
        )
        review_item = db.relationship(
            "ARBReviewItem",
            backref=db.backref("documents", lazy="dynamic"),
            foreign_keys=[review_item_id],
        )
        uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])

        def to_dict(self):
            return {
                "id": self.id,
                "original_name": self.original_name,
                "file_size": self.file_size,
                "mime_type": self.mime_type,
                "document_type": self.document_type,
                "uploaded_by": self.uploaded_by.email if self.uploaded_by else None,
                "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
            }

        def __repr__(self) -> str:
            parent = f"cr={self.change_request_id}" if self.change_request_id else f"ri={self.review_item_id}"
            return f"<ARBDocument {self.original_name} {parent}>"

