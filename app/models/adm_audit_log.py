"""
ADM Audit Log Models and Service

Comprehensive audit trail for all ADM activities.
Tracks who moved cards, when, from which phase to which,
approval chains, compliance evidence, and Architecture Board decisions.
Supports regulatory audit requirements.
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app import db


class ADMAuditAction(str, Enum):
    """Types of audit actions tracked in ADM."""

    # Card operations
    CARD_CREATED = "card_created"
    CARD_UPDATED = "card_updated"
    CARD_DELETED = "card_deleted"
    CARD_MOVED = "card_moved"

    # Phase operations
    PHASE_TRANSITION_REQUESTED = "phase_transition_requested"
    PHASE_TRANSITION_APPROVED = "phase_transition_approved"
    PHASE_TRANSITION_REJECTED = "phase_transition_rejected"
    PHASE_TRANSITION_EXECUTED = "phase_transition_executed"

    # Approval workflow
    APPROVAL_CREATED = "approval_created"
    APPROVAL_SUBMITTED = "approval_submitted"
    APPROVAL_ASSIGNED = "approval_assigned"
    APPROVAL_DECISION_RECORDED = "approval_decision_recorded"
    APPROVAL_CANCELLED = "approval_cancelled"

    # Compliance
    CHECKPOINT_COMPLETED = "checkpoint_completed"
    CHECKPOINT_VERIFIED = "checkpoint_verified"

    # Stakeholder
    STAKEHOLDER_CONCURRENCE_REQUESTED = "stakeholder_concurrence_requested"
    STAKEHOLDER_CONCURRENCE_RECORDED = "stakeholder_concurrence_recorded"

    # Board operations
    BOARD_CREATED = "board_created"
    BOARD_UPDATED = "board_updated"
    BOARD_DELETED = "board_deleted"

    # Comment operations
    COMMENT_ADDED = "comment_added"
    COMMENT_UPDATED = "comment_updated"
    COMMENT_DELETED = "comment_deleted"

    # Attachment operations
    ATTACHMENT_ADDED = "attachment_added"
    ATTACHMENT_REMOVED = "attachment_removed"

    # ArchiMate associations
    ARCHIMATE_ELEMENT_LINKED = "archimate_element_linked"
    ARCHIMATE_ELEMENT_UNLINKED = "archimate_element_unlinked"

    # Application/System associations
    APPLICATION_LINKED = "application_linked"
    APPLICATION_UNLINKED = "application_unlinked"
    SYSTEM_LINKED = "system_linked"
    SYSTEM_UNLINKED = "system_unlinked"

    # ARB integration
    ARB_REVIEW_CREATED = "arb_review_created"
    ARB_REVIEW_LINKED = "arb_review_linked"


class ADMAuditLog(db.Model):
    """
    Comprehensive audit log for all ADM Kanban activities.

    Supports regulatory audit requirements with full traceability
    of who did what, when, and why.
    """

    __tablename__ = "adm_audit_logs"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)

    # Audit reference
    audit_id = Column(String(100), unique=True, nullable=False)  # ADM-AUDIT-2026-001

    # Entity being audited
    entity_type = Column(String(50), nullable=False)  # card, board, approval, checkpoint, etc.
    entity_id = Column(Integer, nullable=False)
    entity_reference = Column(String(255))  # Human-readable reference (e.g., card title)

    # Action details
    action = Column(String(50), nullable=False)  # From ADMAuditAction
    action_description = Column(Text)

    # Changed values (for updates)
    old_values = Column(JSON)
    new_values = Column(JSON)
    changed_fields = Column(JSON)  # List of field names that changed

    # Context
    board_id = Column(Integer, ForeignKey("kanban_boards.id", ondelete="SET NULL"))
    card_id = Column(Integer, ForeignKey("kanban_cards.id", ondelete="SET NULL"))
    approval_id = Column(Integer, ForeignKey("adm_phase_approvals.id", ondelete="SET NULL"))
    phase_id = Column(Integer, ForeignKey("adm_phases.id", ondelete="SET NULL"))

    # Phase transition context (if applicable)
    source_phase_id = Column(Integer, ForeignKey("adm_phases.id", ondelete="SET NULL"))
    target_phase_id = Column(Integer, ForeignKey("adm_phases.id", ondelete="SET NULL"))
    source_phase_code = Column(String(10))
    target_phase_code = Column(String(10))

    # Actor information
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    actor_email = Column(String(255))
    actor_role = Column(String(100))  # Role at time of action

    # Request context
    ip_address = Column(String(100))
    user_agent = Column(String(500))
    request_id = Column(String(100))  # For correlating with web requests
    session_id = Column(String(100))

    # Business context
    justification = Column(Text)  # Business justification provided
    approval_chain = Column(JSON)  # List of approvals in chain

    # Timestamps
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    board = relationship("KanbanBoard", backref="audit_logs")
    card = relationship("KanbanCard", backref="audit_logs")
    approval = relationship("ADMPhaseApproval", backref="audit_logs")
    phase = relationship("ADMPhase", foreign_keys=[phase_id])
    source_phase = relationship("ADMPhase", foreign_keys=[source_phase_id])
    target_phase = relationship("ADMPhase", foreign_keys=[target_phase_id])
    actor = relationship("User", backref="adm_audit_actions")

    def __repr__(self):
        return f"<ADMAuditLog {self.audit_id}: {self.action} on {self.entity_type}>"

    @staticmethod
    def generate_audit_id():
        """Generate unique audit ID."""
        year = datetime.utcnow().year
        last_audit = (
            ADMAuditLog.query.filter(ADMAuditLog.audit_id.like(f"ADM-AUDIT-{year}-%"))
            .order_by(ADMAuditLog.id.desc())
            .first()
        )

        if last_audit:
            try:
                last_num = int(last_audit.audit_id.split("-")[-1])
                next_num = last_num + 1
            except ValueError:
                next_num = 1
        else:
            next_num = 1

        return f"ADM-AUDIT-{year}-{next_num:06d}"
