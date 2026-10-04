"""
Agent Run Record model — one record per agent run, replayable.
"""
from datetime import datetime

from app import db


class AgentRunRecord(db.Model):
    """One record per agent invocation, capturing everything needed for audit and replay."""

    __tablename__ = "agent_run_records"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True, index=True
    )
    persona = db.Column(db.String(80), nullable=True)
    charter_version = db.Column(db.Integer, nullable=True)
    # What the user asked and the context supplied
    inputs = db.Column(db.JSON, nullable=True)
    # Tools called: list of {tool, arguments, result}
    tools_called = db.Column(db.JSON, nullable=True)
    # Entity records read: list of {type, id, name}
    records_read = db.Column(db.JSON, nullable=True)
    # Proposals queued or made: list of {approval_id, tool, summary}
    proposals = db.Column(db.JSON, nullable=True)
    # Cost estimate from the LLM call
    estimated_cost_usd = db.Column(db.Float, nullable=True)
    # Outcome: final text response or error
    outcome = db.Column(db.Text, nullable=True)
    success = db.Column(db.Boolean, default=True)
    # Domain and model info
    domain = db.Column(db.String(50), nullable=True)
    model_used = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "persona": self.persona,
            "charter_version": self.charter_version,
            "inputs": self.inputs,
            "tools_called": self.tools_called,
            "records_read": self.records_read,
            "proposals": self.proposals,
            "estimated_cost_usd": self.estimated_cost_usd,
            "outcome": self.outcome,
            "success": self.success,
            "domain": self.domain,
            "model_used": self.model_used,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def for_organization(cls, organization_id: int):
        """Return all run records for an organisation, newest first."""
        return (
            cls.query.filter_by(organization_id=organization_id)
            .order_by(cls.created_at.desc())
            .all()
        )