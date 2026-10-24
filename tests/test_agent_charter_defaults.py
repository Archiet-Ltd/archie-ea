"""Charter bounds: a recorded charter is honoured as written; an organisation without one may only read."""
from app import db
from app.models.agent_charter import AgentCharter


def test_an_organisation_without_a_charter_may_read_but_not_write(app, db_session, make_org):
    with app.app_context():
        org = make_org("nocharter")
        for tool in ("simulate_impact", "search_archimate_elements", "find_applications", "answer"):
            assert AgentCharter.tool_allowed("enterprise_architect", tool, org.id), tool
            assert AgentCharter.tool_allowed("solutions_architect", tool, org.id), tool
        for tool in ("merge_capabilities", "create_programme", "update_application_status", "submit_for_arb_review"):
            assert not AgentCharter.tool_allowed("enterprise_architect", tool, org.id), tool
        assert not AgentCharter.tool_allowed("no_such_persona", "find_applications", org.id)


def test_a_recorded_charter_is_honoured_while_another_organisation_gets_the_defaults(app, db_session, make_org):
    with app.app_context():
        a = make_org("a")
        b = make_org("b")
        db.session.add(AgentCharter(persona="enterprise_architect", organization_id=a.id, version=1,
                                    proposable_actions=["create_adr"], forbidden_actions=["find_applications"]))
        db.session.flush()
        # A's policy: the one forbidden read stays forbidden, other reads allowed, only the listed proposal allowed
        assert not AgentCharter.tool_allowed("enterprise_architect", "find_applications", a.id)
        assert AgentCharter.tool_allowed("enterprise_architect", "simulate_impact", a.id)
        assert AgentCharter.tool_allowed("enterprise_architect", "create_adr", a.id)
        assert not AgentCharter.tool_allowed("enterprise_architect", "merge_capabilities", a.id)
        # B has no charter: reads from the defaults, no writes, and A's row never leaks into B
        assert AgentCharter.tool_allowed("enterprise_architect", "find_applications", b.id)
        assert not AgentCharter.tool_allowed("enterprise_architect", "create_adr", b.id)
