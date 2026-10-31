"""An organisation with no charter for a persona uses that persona's current default charter;
it is never refused everything (QA-048)."""
from app.models.agent_charter import AgentCharter
from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
from tests.test_agent_charter_and_run_records import _make_user


def test_an_organisation_with_no_charter_uses_the_default_not_a_refusal(db_session, make_org):
    org = make_org("NoCharter")
    assert AgentCharter.query.filter_by(organization_id=org.id).count() == 0
    for persona in ("cto_founder", "cto", "enterprise_architect"):
        assert AgentCharter.tool_allowed(persona, "find_applications", org.id) is True, persona
    # the default still bounds: a read-only persona cannot propose, an unknown persona gets nothing
    assert AgentCharter.tool_allowed("cto_founder", "update_application_status", org.id) is False
    assert AgentCharter.tool_allowed("not_a_persona", "find_applications", org.id) is False
    assert AgentCharter.query.filter_by(organization_id=org.id).count() == 0  # the fallback is never written


def test_the_executor_does_not_refuse_a_read_for_an_organisation_with_no_charter(db_session, make_org, app):
    from flask import g

    org = make_org("NoCharterChat")
    user = _make_user(org.id, db_session)
    with app.test_request_context("/"):
        g.current_org_id = org.id
        for persona in ("cto_founder", "cto", "enterprise_architect"):
            result = ToolExecutor(user.id, persona=persona).execute(
                ToolCall(id="t", name="find_applications", arguments={}))
            assert result.get("code") != "CHARTER_REFUSED", persona
        refused = ToolExecutor(user.id, persona="cto_founder").execute(
            ToolCall(id="t2", name="update_application_status",
                     arguments={"application_name": "X", "new_status": "production", "rationale": "t"}))
        assert refused.get("charter_refused") is True
