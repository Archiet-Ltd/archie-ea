"""
Agent Charter model — versioned records of each persona's scope and bounds.

Each charter defines what a persona may read, propose and is forbidden from
doing. The executor reads the current version for the acting organisation and
refuses any tool call outside the charter bounds, recording the refusal.

Charters are per-organisation so organisation A can tailor its charter without
affecting organisation B.
"""
from datetime import datetime

from app import db


class AgentCharter(db.Model):
    """A versioned charter that governs one AI persona in one organisation."""

    __tablename__ = "agent_charters"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True
    )
    persona = db.Column(db.String(80), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    purpose = db.Column(db.Text, nullable=False, default="")
    readable_entities = db.Column(db.JSON, nullable=False, default=list)
    proposable_actions = db.Column(db.JSON, nullable=False, default=list)
    forbidden_actions = db.Column(db.JSON, nullable=False, default=list)
    charter_text = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "organization_id", "persona", "version",
            name="uq_agent_charter_org_persona_version",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "persona": self.persona,
            "version": self.version,
            "purpose": self.purpose,
            "readable_entities": self.readable_entities,
            "proposable_actions": self.proposable_actions,
            "forbidden_actions": self.forbidden_actions,
            "charter_text": self.charter_text,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def current_for(cls, persona: str, organization_id: int):
        """Return the latest-version charter for a persona in this organisation."""
        return (
            cls.query.filter_by(persona=persona, organization_id=organization_id)
            .order_by(cls.version.desc())
            .first()
        )

    @classmethod
    def tool_allowed(cls, persona: str, tool_name: str, organization_id: int) -> bool:
        """Check whether a tool call is within the persona's charter bounds."""
        charter = cls.current_for(persona, organization_id)
        if charter is None:
            # No charter at all — fail CLOSED.
            return False
        forbidden = charter.forbidden_actions or []
        if tool_name in forbidden:
            return False
        proposable = charter.proposable_actions or []
        if not proposable:
            # Empty proposable list — persona can only read.
            from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

            schema = TOOL_SCHEMA_BY_NAME.get(tool_name, {})
            risk_class = schema.get("risk_class")
            if risk_class in ("read", "propose"):
                return True
            return False
        if isinstance(proposable, str) and proposable == "all":
            return True
        return tool_name in proposable