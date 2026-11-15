"""ADR-0022: reads of the model are open inside the organisation; restricted entities need readable_entities."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _old_allowed(charter, tool_name, risk_class):
    """The rule at 60cfe66c3 (genome01 before this round), kept as the oracle for the superset test."""
    if tool_name in (charter.forbidden_actions or []):
        return False
    if risk_class == "read":
        return True
    proposable = charter.proposable_actions or []
    if not proposable:
        return risk_class == "propose"
    if isinstance(proposable, str) and proposable == "all":
        return True
    return tool_name in proposable


def test_every_registered_read_tool_is_mapped_and_every_mapped_tool_exists():
    from app.models import agent_charter
    from app.models.agent_charter import read_entities_for
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

    read_tools = {n for n, s in TOOL_SCHEMA_BY_NAME.items() if s.get("risk_class") == "read"}
    unmapped = sorted(n for n in read_tools if read_entities_for(n) is None)
    assert unmapped == [], "map these read tools in tool_read_entities.json: %s" % unmapped
    mapped = set(agent_charter._read_map()["tools"])
    assert sorted(mapped - read_tools) == [], "mapped tools that are not registered read tools"


def test_get_genome_slice_touches_no_restricted_entity():
    from app.models.agent_charter import read_entities_for, restricted_entities

    assert read_entities_for("get_genome_slice")
    assert not set(read_entities_for("get_genome_slice")) & restricted_entities()


def test_no_mapped_read_tool_touches_a_restricted_entity_today():
    from app.models import agent_charter

    restricted = agent_charter.restricted_entities()
    bad = {t: e for t, e in agent_charter._read_map()["tools"].items() if set(e) & restricted}
    assert bad == {}, bad


def test_a_model_read_is_allowed_for_every_default_persona(app, db_session, make_org):
    from app.commands.seed_agent_charters import _CHARTER_BOUNDS
    from app.models import agent_charter
    from app.models.agent_charter import AgentCharter

    org = make_org("open-read")
    for persona in _CHARTER_BOUNDS:
        charter = AgentCharter.effective_for(persona, org.id)
        for tool in agent_charter._read_map()["tools"]:
            if tool in (charter.forbidden_actions or []):
                continue
            assert AgentCharter.tool_allowed(persona, tool, org.id), (persona, tool)
        assert AgentCharter.tool_allowed(persona, "get_genome_slice", org.id), persona


def _persona(db_session, org, name, readable):
    from app.models.agent_charter import AgentCharter

    db_session.add(AgentCharter(organization_id=org.id, persona=name, version=1, purpose="t",
                                readable_entities=readable, proposable_actions=[], forbidden_actions=[]))
    db_session.flush()


@pytest.fixture
def billing_read_tool(monkeypatch):
    from app.models import agent_charter
    from app.modules.ai_chat.tools import registry

    monkeypatch.setitem(registry.TOOL_SCHEMA_BY_NAME, "read_billing_summary",
                        {"name": "read_billing_summary", "risk_class": "read"})
    monkeypatch.setitem(agent_charter._read_map()["tools"], "read_billing_summary", ["billing"])
    return "read_billing_summary"


def test_a_persona_without_billing_is_refused_a_billing_read_and_one_with_it_is_allowed(db_session, make_org, billing_read_tool):
    from app.models.agent_charter import AgentCharter

    org = make_org("billing-read")
    _persona(db_session, org, "no_billing", ["application", "capability"])
    _persona(db_session, org, "with_billing", ["application", "billing"])
    assert AgentCharter.tool_allowed("no_billing", billing_read_tool, org.id) is False
    assert AgentCharter.tool_allowed("with_billing", billing_read_tool, org.id) is True


def test_an_organisation_can_mark_an_entity_restricted(db_session, make_org):
    from app.models.agent_charter import AgentCharter

    org = make_org("marks-restricted")
    _persona(db_session, org, "plain", ["application"])
    _persona(db_session, org, "trusted", ["application", "capability"])
    assert AgentCharter.tool_allowed("plain", "query_capability_gaps", org.id) is True
    org.settings = {"restricted_entities": ["capability"]}
    db_session.flush()
    assert AgentCharter.tool_allowed("plain", "query_capability_gaps", org.id) is False
    assert AgentCharter.tool_allowed("trusted", "query_capability_gaps", org.id) is True


def test_the_persona_tool_matrix_is_a_superset_of_60cfe66c3(db_session, make_org):
    from app.commands.seed_agent_charters import _CHARTER_BOUNDS
    from app.models.agent_charter import AgentCharter
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

    org = make_org("superset")
    lost = []
    for persona in _CHARTER_BOUNDS:
        charter = AgentCharter.effective_for(persona, org.id)
        for tool, schema in TOOL_SCHEMA_BY_NAME.items():
            was = _old_allowed(charter, tool, schema.get("risk_class"))
            now = AgentCharter.tool_allowed(persona, tool, org.id)
            if was and not now:
                lost.append((persona, tool))
    assert lost == []
