"""
Agent Oversight State Model

One row per organisation tracking the pause/stop-all-writes switches.
Used by the oversight controls to block mutating tool calls before they
reach the approval queue.
"""

from datetime import datetime, timezone

from app import db
from app.models.mixins.core import TenantMixin


def _utcnow() -> datetime:
    """Naive UTC datetime for TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AgentOversightState(TenantMixin, db.Model):
    """
    Per-organisation oversight state for AI agent write operations.

    A single row per organisation stores:
    - writes_paused: whether all agent writes are paused (stop-all-writes)
    - paused_by: user who activated the pause
    - paused_at: when the pause was activated
    - reason: human-readable reason for the pause
    """

    __tablename__ = "agent_oversight_state"

    id = db.Column(db.Integer, primary_key=True)

    # The organisation this state belongs to (via TenantMixin.organization_id)
    # TenantMixin provides organization_id with FK to organizations.id

    # Pause state
    writes_paused = db.Column(db.Boolean, default=False, nullable=False)
    paused_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    paused_at = db.Column(db.DateTime, nullable=True)
    reason = db.Column(db.Text, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    # Relationship to the user who paused
    paused_by = db.relationship("User", foreign_keys=[paused_by_id])

    def __repr__(self):
        return (
            f"<AgentOversightState org_id={self.organization_id} "
            f"writes_paused={self.writes_paused}>"
        )

    @classmethod
    def get_for_org(cls, organization_id):
        """Get or create the oversight state row for an organisation."""
        state = cls.query.filter_by(organization_id=organization_id).first()
        if state is None:
            state = cls(organization_id=organization_id)
            db.session.add(state)
            db.session.flush()
        return state

    def pause(self, user_id, reason):
        """Activate the pause-all-writes switch."""
        self.writes_paused = True
        self.paused_by_id = user_id
        self.paused_at = _utcnow()
        self.reason = reason
        self.updated_at = _utcnow()

    def resume(self):
        """Deactivate the pause-all-writes switch."""
        self.writes_paused = False
        self.paused_by_id = None
        self.paused_at = None
        self.reason = None
        self.updated_at = _utcnow()

    def is_paused(self):
        """Check if writes are currently paused."""
        return self.writes_paused