"""
Agent Charter model — versioned records of each persona's scope and bounds.

Each charter defines what a persona may read, propose and is forbidden from
doing. The executor reads the current version for the acting organisation and
refuses any tool call outside the charter bounds, recording the refusal.

Charters are per-organisation so organisation A can tailor its charter without
affecting organisation B.
"""
import json
import os
from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


_READ_ENTITIES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "modules", "ai_chat", "tools", "tool_read_entities.json",
)
_read_map_cache = None


def _read_map() -> dict:
    global _read_map_cache
    if _read_map_cache is None:
        with open(_READ_ENTITIES_PATH, encoding="utf-8") as handle:
            _read_map_cache = json.load(handle)
    return _read_map_cache


def read_entities_for(tool_name):
    """Entities a read-class tool reads, or None when the tool is not mapped."""
    return _read_map()["tools"].get(tool_name)


def restricted_entities(organization_id=None) -> frozenset:
    """Entities no read goes through the open rule for: the named list in tool_read_entities.json,
    plus any the organisation marks restricted in its settings (``restricted_entities``)."""
    entities = set(_read_map()["restricted_entities"])
    if organization_id is not None:
        try:
            from app.models.organization import Organization

            settings = db.session.query(Organization.settings).filter(Organization.id == organization_id).scalar()
            extra = settings.get("restricted_entities") if isinstance(settings, dict) else None
            if isinstance(extra, list):
                entities.update(str(e) for e in extra)
        except Exception:  # noqa: BLE001 - an unreadable setting never changes the named list
            db.session.rollback()
    return frozenset(entities)


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
    def effective_for(cls, persona: str, organization_id: int):
        """The charter in force: the stored latest version, else the persona's current default.

        An organisation created before charters existed (or whose charters were never seeded)
        holds none for a persona; refusing everything would break its chat, so the executor
        falls back to the default charter, which is never written. A persona with no default
        either gets None.
        """
        stored = cls.current_for(persona, organization_id)
        if stored is not None:
            return stored
        from types import SimpleNamespace

        from app.commands.seed_agent_charters import default_charter_fields

        fields = default_charter_fields(persona)
        if fields is None:
            return None
        return SimpleNamespace(version=1, persona=persona, organization_id=organization_id, **fields)

    @classmethod
    def tool_allowed(cls, persona: str, tool_name: str, organization_id: int) -> bool:
        """Check whether a tool call is within the persona's charter bounds."""
        charter = cls.effective_for(persona, organization_id)
        if charter is None:
            # No stored charter and no default for this persona (unknown persona) — fail CLOSED.
            return False
        forbidden = charter.forbidden_actions or []
        if tool_name in forbidden:
            return False
        proposable = charter.proposable_actions or []
        from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

        risk_class = TOOL_SCHEMA_BY_NAME.get(tool_name, {}).get("risk_class")
        if risk_class == "read":
            # ADR-0022: a read of the model is open to every persona of the organisation unless the
            # charter forbids the tool by name (above). The exception is a restricted entity: a read
            # that touches one is allowed only when the persona's readable_entities names it.
            restricted = restricted_entities(organization_id)
            touched = [e for e in (read_entities_for(tool_name) or []) if e in restricted]
            readable = set(charter.readable_entities or [])
            return all(entity in readable for entity in touched)
        if not proposable:
            # Empty proposable list: unchanged from genome01 (this round only adds the restricted check).
            return risk_class == "propose"
        if isinstance(proposable, str) and proposable == "all":
            return True
        return tool_name in proposable
