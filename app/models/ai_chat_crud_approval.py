"""
AI Chat CRUD Approval Model

Tracks pending CRUD operations from AI chat for user approval.
Prevents immediate execution of data modifications via natural language.
"""

import json
from datetime import datetime
from enum import Enum

from app import db
from app.models.mixins.core import TenantMixin, _default_org_id


class ApprovalStatus(Enum):
    """Status of CRUD approval request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class AIChatCRUDApproval(TenantMixin, db.Model):
    """
    Tracks pending CRUD operations from AI chat interactions.

    When a user issues a CRUD command via chat (e.g., "Create a Customer
    Management capability"), the system creates a pending approval record
    instead of executing immediately. The user must review and confirm
    before the operation is executed.
    """

    __tablename__ = "ai_chat_crud_approvals"
    __table_args__ = (
        # (source_table, source_id) is the consolidation's foreign key from
        # the canonical row back to its source row -- at most one
        # canonical approval per source row. PostgreSQL treats NULL as
        # distinct from any other NULL, so this does not block the normal
        # case of many directly-created rows with both columns NULL.
        db.UniqueConstraint("source_table", "source_id", name="uq_approval_source"),
    )

    id = db.Column(db.Integer, primary_key=True)

    # User who initiated the request. Nullable per the consolidation: a row backfilled
    # from a source with no live acting user (a background job, e.g. the
    # confidence-review pipeline) or created directly by one has no requester
    # to attribute the request to — same ADD-only-column reasoning as
    # organization_id below. Approving or rejecting a row still requires a
    # real human actor regardless of whether the row's own user_id is set
    # (AIChatApprovalService enforces this; see _acting_user()).
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Override TenantMixin's normally non-nullable column for an ADD-only
    # rollout: reconcile-schema must be able to add this to legacy tables, then
    # the backfill derives ownership from the requester. The mixin is still
    # essential: tenant middleware identifies tenant-owned models by it and
    # applies the automatic query fence even while old NULL rows are repaired.
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        default=_default_org_id,
    )

    # Operation details
    operation_type = db.Column(db.String(50), nullable=False)  # create, update, delete
    entity_type = db.Column(db.String(50), nullable=False)  # capability, application, vendor, etc.
    entity_id = db.Column(db.Integer, nullable=True)  # For update/delete operations

    # The natural language command that triggered this
    original_command = db.Column(db.Text, nullable=False)

    # JSON payload for the operation
    operation_payload = db.Column(db.Text, nullable=False)

    # Human-readable summary of what will happen
    summary = db.Column(db.Text, nullable=False)

    # Approval status
    status = db.Column(db.Enum(ApprovalStatus), default=ApprovalStatus.PENDING, nullable=False)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    # Overdue-not-expired: a due-by marker, not an auto-expiry — past this time and
    # still PENDING, the row is "overdue" (is_overdue()) and stays actionable;
    # nothing filters rows out by this column any more.
    expires_at = db.Column(db.DateTime, nullable=False)
    approved_at = db.Column(db.DateTime, nullable=True)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Rejection reason
    rejected_reason = db.Column(db.Text, nullable=True)

    # Execution result (if approved and executed)
    execution_result = db.Column(db.Text, nullable=True)  # JSON
    executed_at = db.Column(db.DateTime, nullable=True)

    # Session/chat context
    chat_session_id = db.Column(db.String(100), nullable=True)

    # ARCH-020: identifies the specific agent turn/run that raised this approval,
    # distinct from chat_session_id (which identifies the conversation). Nullable
    # per CLAUDE.md's schema rules (reconcile-schema is ADD-COLUMN-only and every
    # new column must tolerate NULL on a pre-existing production row).
    agent_turn_id = db.Column(db.String(64), nullable=True)

    # Consolidation: which superseded store (if any) this row was
    # backfilled from. NULL for a row created directly here (the normal case
    # going forward). Set together, never one without the other.
    source_table = db.Column(db.String(64), nullable=True, index=True)
    source_id = db.Column(db.Integer, nullable=True)

    # Overdue-not-expired: when this row was first found overdue (expires_at in the
    # past while still PENDING) and its organisation's administrators were
    # notified. NULL means either not yet overdue or not yet escalated. Being
    # overdue never removes a row from a pending/inbox query or blocks
    # approval — see is_overdue()/escalate_overdue() and every query method
    # below, none of which filter on expires_at any more.
    escalated_at = db.Column(db.DateTime, nullable=True)

    # The persona under which this tool call was queued. Stored so the charter
    # can be enforced at approval-execution time — the approver may be a
    # different user than the requester, and the charter must still apply.
    persona = db.Column(db.String(80), nullable=True)

    def to_dict(self):
        """Convert approval record to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "operation_type": self.operation_type,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "original_command": self.original_command,
            "operation_payload": json.loads(self.operation_payload) if self.operation_payload else None,
            "summary": self.summary,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "approved_by_id": self.approved_by_id,
            "rejected_reason": self.rejected_reason,
            "execution_result": json.loads(self.execution_result) if self.execution_result else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "chat_session_id": self.chat_session_id,
            "agent_turn_id": self.agent_turn_id,
            "persona": self.persona,
        }

    def approve(self, user_id):
        """Mark approval as approved by a user."""
        self.status = ApprovalStatus.APPROVED
        self.approved_at = datetime.utcnow()
        self.approved_by_id = user_id

    def reject(self, reason=None):
        """Mark approval as rejected."""
        self.status = ApprovalStatus.REJECTED
        self.rejected_reason = reason

    def execute(self, result):
        """Mark as executed with result."""
        self.execution_result = json.dumps(result)
        self.executed_at = datetime.utcnow()

    def is_expired(self):
        """Historical name for is_overdue() — an approval never actually
        expires (overdue-not-expired); kept so any remaining reader sees the same boolean.
        """
        return self.is_overdue()

    def is_overdue(self):
        """Past its expires_at while still PENDING. Never excludes the row
        from a query or blocks approve/reject — see AIChatApprovalService.
        """
        return self.status == ApprovalStatus.PENDING and datetime.utcnow() > self.expires_at

    @classmethod
    def get_pending_for_user(cls, user_id, organization_id):
        """Get pending approvals for a user inside one explicit organization.

        Includes overdue rows (overdue-not-expired) — being overdue never removes
        an approval from a pending query.
        """
        return cls.query.filter_by(
            user_id=user_id,
            organization_id=organization_id,
            status=ApprovalStatus.PENDING
        ).all()

    @classmethod
    def get_by_id_and_user(cls, approval_id, user_id, organization_id):
        """Get one approval by requester and explicit organization ownership."""
        return cls.query.filter_by(
            id=approval_id,
            user_id=user_id,
            organization_id=organization_id,
        ).first()

    @classmethod
    def get_pending_for_session(cls, user_id, organization_id, chat_session_id):
        """Pending approvals for one user scoped to one chat session (ARCH-020).

        Lets the agent answer "what's still pending in *this* conversation"
        instead of only "what's pending for this user anywhere" — the gap that
        let a second, identical approval get queued when the user said
        "I approve" and the agent had no way to see the first one was already
        sitting there for this session.
        """
        if not chat_session_id:
            return []
        return (
            cls.query.filter_by(
                user_id=user_id,
                organization_id=organization_id,
                chat_session_id=chat_session_id,
                status=ApprovalStatus.PENDING,
            )
            .order_by(cls.created_at.desc())
            .all()
        )

    @classmethod
    def get_overdue_unescalated(cls):
        """Every PENDING row past expires_at that has not yet been escalated.

        Platform-wide (no organisation predicate): the escalation sweep runs
        outside a request/tenant context — same shape as the digest-email
        jobs in app/_bootstrap/_digest_emails.py — and groups results by each
        row's own organization_id itself, one notification per organisation.
        """
        return (  # tenant-scoping-ok: platform-wide sweep, grouped by each row's own org
            cls.query.filter(
                cls.status == ApprovalStatus.PENDING,
                cls.expires_at < datetime.utcnow(),
                cls.escalated_at.is_(None),
            )
            .order_by(cls.organization_id, cls.created_at)
            .all()
        )


class AIChatApprovalAuditLog(db.Model):
    """Immutable audit trail of every approval state transition (ARCH-022).

    A row is appended, never updated or deleted, for every transition a
    AIChatCRUDApproval record goes through: created, approved, rejected,
    expired, executed, execution_refused. This is the record that lets the
    platform answer "did a human approve this, or did a restart / expiry
    sweep push it through" — a question the approval row alone cannot answer,
    because it only carries the *current* state, not the history of how it
    got there.

    actor_user_id is nullable because a system-initiated transition (an
    expiry sweep) legitimately has no human actor -- but for the "approved"
    and "executed" events specifically, application code refuses to write a
    system-actor row (see AIChatApprovalService._audit): those transitions
    require a human, or they don't happen.
    """

    __tablename__ = "ai_chat_approval_audit_log"

    id = db.Column(db.Integer, primary_key=True)
    approval_id = db.Column(
        db.Integer, db.ForeignKey("ai_chat_crud_approvals.id"), nullable=False, index=True
    )
    from_status = db.Column(db.String(20), nullable=True)
    to_status = db.Column(db.String(20), nullable=False)
    event = db.Column(db.String(30), nullable=False)  # created|approved|rejected|expired|executed|execution_refused
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    actor_type = db.Column(db.String(20), nullable=False, default="user")  # user|system
    reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "approval_id": self.approval_id,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "event": self.event,
            "actor_user_id": self.actor_user_id,
            "actor_type": self.actor_type,
            "reason": self.reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
