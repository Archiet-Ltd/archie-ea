"""A persona with no charter recorded works within the product's default bounds instead of refusing every tool."""
from app.models.agent_charter import AgentCharter


def test_read_tools_are_allowed_when_no_charter_is_recorded(app, db_session):
    with app.app_context():
        assert AgentCharter.current_for("enterprise_architect", 424242) is None
        for tool in ("simulate_impact", "search_archimate_elements", "find_applications", "answer"):
            assert AgentCharter.tool_allowed("enterprise_architect", tool, 424242), tool
        assert AgentCharter.tool_allowed("solutions_architect", "simulate_impact", 424242)


def test_default_forbidden_actions_still_apply_without_a_charter(app, db_session):
    with app.app_context():
        assert not AgentCharter.tool_allowed("enterprise_architect", "submit_for_arb_review", 424242)
        assert not AgentCharter.tool_allowed("solutions_architect", "merge_capabilities", 424242)


def test_an_unknown_persona_still_fails_closed(app, db_session):
    with app.app_context():
        assert not AgentCharter.tool_allowed("no_such_persona", "find_applications", 424242)
