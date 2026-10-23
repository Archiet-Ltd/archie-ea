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
from app.models.mixins import TenantMixin


class AgentCharter(TenantMixin, db.Model):
    """A versioned charter that governs one AI persona in one organisation."""

    __tablename__ = "agent_charters"

    id = db.Column(db.Integer, primary_key=True)
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
            # No charter recorded for this organisation yet: the persona works within the
            # product's own default bounds (the same ones the seed writes) instead of
            # refusing every tool, which left a new organisation's chat unable to look
            # anything up. Writes still go through the approval queue as before.
            bounds = _default_bounds(persona)
            if bounds is None:
                return False
            forbidden, proposable = bounds
        else:
            forbidden = charter.forbidden_actions or []
            proposable = charter.proposable_actions or []
        if tool_name in forbidden:
            return False
        # A read-only tool is always within bounds unless the charter forbids it by name:
        # the proposable list governs what a persona may propose or change, not what it
        # may look up. Without this, a persona with a proposable list could not find an
        # application or run an impact query, and sent people to other pages.
        if _risk_class(tool_name) == "read":
            return True
        if not proposable:
            # Empty proposable list — persona can only read.
            if _risk_class(tool_name) in ("read", "propose"):
                return True
            return False
        if isinstance(proposable, str) and proposable == "all":
            return True
        return tool_name in proposable

def _default_bounds(persona: str):
    """(forbidden, proposable) from the product's own charter defaults, or None for an unknown persona."""
    try:
        from app.commands.seed_agent_charters import _CHARTER_BOUNDS
    except ImportError:
        return None
    entry = _CHARTER_BOUNDS.get(persona)
    if not entry:
        return None
    _purpose, _readable, proposable, forbidden = entry
    return list(forbidden or []), (proposable if isinstance(proposable, str) else list(proposable or []))


def _risk_class(tool_name: str):
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

    return TOOL_SCHEMA_BY_NAME.get(tool_name, {}).get("risk_class")
