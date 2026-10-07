"""
Agent Registration model — R1-B56 (TB-0394, PB-0220).

R1-B22's AgentCharter (app/models/agent_charter.py) is the charter half of
agent governance: what a persona may read, propose and is forbidden from
doing. Its own reuse note says "no single registry records their owners" —
this model is that one missing registry row: who owns a given agent, what
charter version it runs under, and what it may act within, with activation
refused until all three are set. Never a second charter store.
"""
from datetime import datetime

from app import db
from app.models.mixins import TenantMixin

STATUSES = ["draft", "active", "paused", "suspended", "retired"]


class AgentRegistration(TenantMixin, db.Model):
    """One agent's registry row: purpose, owner, charter, delegated limits,
    and its lifecycle state."""

    __tablename__ = "agent_registrations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    purpose = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="draft")

    owner_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    charter_persona = db.Column(db.String(80), nullable=True)
    charter_version_id = db.Column(
        db.Integer, db.ForeignKey("agent_charters.id", ondelete="SET NULL"), nullable=True,
    )
    delegated_limits = db.Column(db.JSON, nullable=True)

    # TB-0496 (owner-leaver pause + successor recommendation) is not built in
    # this PR -- it depends on a leaver signal R1-B26 (identity-provider
    # directory-sync provisioning) has not merged yet. These two columns
    # exist so the follow-up PR has somewhere to write without a second
    # migration, but nothing in this PR sets them.
    owner_departed_at = db.Column(db.DateTime, nullable=True)
    successor_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = db.relationship("User", foreign_keys=[owner_user_id])
    successor = db.relationship("User", foreign_keys=[successor_user_id])
    charter_version = db.relationship("AgentCharter", foreign_keys=[charter_version_id])

    def missing_for_activation(self):
        """Fields still unset that activation requires -- named, not just
        refused, so the caller can show the operator exactly what to fill in
        (the brief's own acceptance criterion)."""
        missing = []
        if not self.owner_user_id:
            missing.append("owner")
        if not self.charter_persona or not self.charter_version_id:
            missing.append("charter")
        if not self.delegated_limits:
            missing.append("delegated_limits")
        return missing

    def activate(self):
        """Refuses activation while any required field is unset. Returns
        (ok, missing_fields)."""
        missing = self.missing_for_activation()
        if missing:
            return False, missing
        self.status = "active"
        return True, []

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "name": self.name,
            "purpose": self.purpose,
            "status": self.status,
            "owner_user_id": self.owner_user_id,
            "charter_persona": self.charter_persona,
            "charter_version_id": self.charter_version_id,
            "delegated_limits": self.delegated_limits,
            "missing_for_activation": self.missing_for_activation(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
