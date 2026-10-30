"""Stored charters are brought up to the current defaults without losing history, and a persona
with no charter is never refused everything (QA-048)."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from tests.test_agent_charter_and_run_records import _seed_charters

pytestmark = pytest.mark.usefixtures("db_session")

_MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261026_charter_refresh.py"
_OLD_BUSINESS_ARCHITECT = ["application"]


def _run_migration():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app import db

    spec = importlib.util.spec_from_file_location("charter_refresh_migration", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = MigrationContext.configure(db.session.connection())
    with Operations.context(context):
        module.upgrade()


def _versions(org_id, persona):
    from app.models.agent_charter import AgentCharter

    return sorted(c.version for c in AgentCharter.query.filter_by(organization_id=org_id, persona=persona))


def test_the_migration_appends_without_rewriting_and_a_second_run_adds_nothing(app, db_session, make_org):
    from app import db
    from app.models.agent_charter import AgentCharter
    from app.commands.seed_agent_charters import default_charter_fields
    from app.modules.ai_chat.services.agent_registry_service import NON_REMOVABLE_RULES

    org_id = make_org("Stale").id
    _seed_charters([org_id])
    stale = AgentCharter.query.filter_by(organization_id=org_id, persona="business_architect", version=1).one()
    stale.readable_entities = list(_OLD_BUSINESS_ARCHITECT)
    # an organisation-edited charter (version 2) and a registry-authored version 1 stay as they are
    db.session.add(AgentCharter(organization_id=org_id, persona="cto", version=2, purpose="edited",
                                readable_entities=["application"], proposable_actions=[], forbidden_actions=[]))
    authored = AgentCharter.query.filter_by(organization_id=org_id, persona="procurement", version=1).one()
    authored.forbidden_actions = list(authored.forbidden_actions) + list(NON_REMOVABLE_RULES)
    authored.readable_entities = ["vendor"]
    # no charter at all for cto_founder, as in the 97 organisations that hold none
    AgentCharter.query.filter_by(organization_id=org_id, persona="cto_founder").delete()
    db.session.flush()
    db.session.expire_all()

    _run_migration()
    db.session.expire_all()

    assert _versions(org_id, "business_architect") == [1, 2]
    assert AgentCharter.query.filter_by(organization_id=org_id, persona="business_architect", version=1).one().readable_entities == _OLD_BUSINESS_ARCHITECT
    assert AgentCharter.current_for("business_architect", org_id).readable_entities == default_charter_fields("business_architect")["readable_entities"]
    assert _versions(org_id, "cto_founder") == [1]
    assert AgentCharter.current_for("cto_founder", org_id).purpose == default_charter_fields("cto_founder")["purpose"]
    assert _versions(org_id, "cto") == [1, 2] and AgentCharter.current_for("cto", org_id).purpose == "edited"
    assert _versions(org_id, "procurement") == [1]

    before = AgentCharter.query.count()
    _run_migration()
    db.session.expire_all()
    assert AgentCharter.query.count() == before

# ---------------------------------------------------------------------------
# No persona loses a tool it could use before the read-entity enforcement (QA-048).
# ---------------------------------------------------------------------------

def _allowed_before_enforcement(fields, tool, schemas):
    """The rule in force before charters bounded reads by readable_entities (commit 60cfe66c3)."""
    if tool in (fields["forbidden_actions"] or []):
        return False
    proposable = fields["proposable_actions"] or []
    risk_class = schemas.get(tool, {}).get("risk_class")
    if not proposable:
        return risk_class in ("read", "propose")
    if isinstance(proposable, str) and proposable == "all":
        return True
    return tool in proposable


def _losses(allowed_now):
    from app.commands.seed_agent_charters import default_charter_fields
    from app.modules.ai_chat.services.architect_persona_charters import ARCHITECT_PERSONAS
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

    lost = {}
    for persona in ARCHITECT_PERSONAS:
        fields = default_charter_fields(persona)
        gone = sorted(t for t in TOOL_SCHEMA_BY_NAME
                      if _allowed_before_enforcement(fields, t, TOOL_SCHEMA_BY_NAME) and not allowed_now(persona, t))
        if gone:
            lost[persona] = gone
    return lost


def test_no_persona_default_loses_a_tool_it_could_use_before_enforcement(db_session):
    from app.models.agent_charter import AgentCharter

    # an organisation with no charters falls back to the defaults
    assert _losses(lambda p, t: AgentCharter.tool_allowed(p, t, 999999999)) == {}


def test_no_persona_loses_a_tool_with_stored_charters(db_session, make_org):
    from app.models.agent_charter import AgentCharter

    org_id = make_org("Matrix").id
    _seed_charters([org_id])
    assert AgentCharter.query.filter_by(organization_id=org_id).count() > 0
    assert _losses(lambda p, t: AgentCharter.tool_allowed(p, t, org_id)) == {}
