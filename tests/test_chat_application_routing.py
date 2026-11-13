"""The chat's handling of an application: what counts as a change to one, and every way to ask for its capabilities.

Two organisations throughout. A request is a change to an application only when
its subject is an application of the signed-in organisation; any phrasing of
"what capabilities does it support" gets the recorded answer, through the one
reader the application page uses, whether the keyword path or the model asks.
"""

from __future__ import annotations

import re
import uuid
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

PHRASINGS = (
    "What capabilities does {name} have?",
    "Tell me about {name}",
    "Which capabilities does {name} map to?",
    "List {name}'s capabilities",
)


def _user(db_session, org, label, platform_role="User", persona="enterprise_architect"):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", first_name=label.capitalize(),
        last_name="Test", organization_id=org.id, confirmed=True, enterprise_role=persona,
    )
    user.role = Role.query.filter_by(name=platform_role).one()
    db_session.add(user)
    db_session.flush()
    return user


def _application(db_session, org, name, **columns):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org.id, **columns)
    db_session.add(row)
    db_session.flush()
    return row


def _capability(db_session, org, name):
    from app.models.business_capabilities import BusinessCapability

    row = BusinessCapability(name=f"{name} {uuid.uuid4().hex[:5]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _map(db_session, org, application, capability):
    from app.models.application_capability import ApplicationCapabilityMapping

    db_session.add(ApplicationCapabilityMapping(
        application_component_id=application.id, business_capability_id=capability.id,
        organization_id=org.id, support_level="primary",
    ))
    db_session.flush()


def _map_unified(db_session, org, application, capability):
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    unified = UnifiedCapability.query.filter_by(
        organization_id=org.id, source_table="business_capability", source_id=str(capability.id),
    ).one()
    db_session.add(UnifiedApplicationCapabilityMapping(
        application_component_id=application.id, unified_capability_id=unified.id,
    ))
    db_session.flush()


@pytest.fixture
def world(db_session, make_org):
    org_a, org_b = make_org("route-a"), make_org("route-b")
    name = f"Zephyr {uuid.uuid4().hex[:5]}"
    app_a = _application(db_session, org_a, name, description="Old description a", deployment_status="development")
    app_b = _application(db_session, org_b, name, description="Old description b", deployment_status="development")
    # An application whose name contains "we", beside the application the questions are about.
    tweed = _application(db_session, org_a, f"Tweed {uuid.uuid4().hex[:5]}")
    caps_a = [_capability(db_session, org_a, "Payments"), _capability(db_session, org_a, "Onboarding")]
    cap_b = _capability(db_session, org_b, "Secret other-org capability")
    for cap in caps_a:
        _map(db_session, org_a, app_a, cap)
    _map(db_session, org_b, app_b, cap_b)
    # The unified store holds a mapping for each application too: the reader's guard covers both stores.
    unified_a = _capability(db_session, org_a, "Reporting")
    unified_b = _capability(db_session, org_b, "Secret unified other-org capability")
    _map_unified(db_session, org_a, app_a, unified_a)
    _map_unified(db_session, org_b, app_b, unified_b)
    caps_a.append(unified_a)
    return {
        "org_a": org_a, "org_b": org_b, "name": name, "app_a": app_a, "app_b": app_b, "tweed": tweed,
        "caps_a": caps_a, "cap_b": cap_b, "caps_b": [cap_b, unified_b],
        "editor": _user(db_session, org_a, "editor"),
        "viewer": _user(db_session, org_a, "viewer", platform_role="Viewer", persona=None),
        "user_b": _user(db_session, org_b, "userb"),
    }


def _ask(client, text):
    resp = client.post("/ai-chat/estate-answer", json={"message": text, "remember": False})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


# -- a request is a change to an application only when it names one ----------------


@pytest.mark.parametrize("sentence", (
    "set the description of Payments Solution to Handles card payments",
    "update the status of risk R-12 to mitigated",
    "update the status of work package WP1 to done",
))
def test_a_request_about_something_that_is_not_an_application_is_not_answered_as_one(
    world, client, login_as, sentence
):
    for person in (world["editor"], world["viewer"]):
        login_as(client, person)
        answer = _ask(client, sentence)
        assert "change_state" not in answer, (person.first_name, answer)
        assert "could not find an application" not in (answer.get("html") or ""), answer
        assert answer["found"] is False, "the request did not go on to the model"


def test_a_request_that_names_an_application_is_still_a_change_to_it(world, client, login_as):
    login_as(client, world["editor"])
    shown = _ask(client, f"set {world['name']}'s description to Runs the monthly billing")
    assert shown["change_state"] == "preview"
    assert "Old description a" in shown["html"] and "Runs the monthly billing" in shown["html"]
    login_as(client, world["viewer"])
    assert _ask(client, f"update the status of {world['name']} to production")["change_state"] == "refused"


def test_an_application_of_the_other_organisation_is_not_a_subject(world, client, login_as):
    login_as(client, world["user_b"])
    shown = _ask(client, f"set {world['tweed'].name}'s description to Mine")
    assert "change_state" not in shown and shown["found"] is False


def test_the_press_of_save_after_the_right_to_edit_is_lost_is_refused_and_changes_nothing(
    world, client, login_as, db_session
):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import Role

    login_as(client, world["editor"])
    shown = _ask(client, f"set {world['name']}'s description to Written after losing the right")
    token = re.search(r'data-change-token="([^"]+)"', shown["html"]).group(1)

    editor = world["editor"]
    editor.role = Role.query.filter_by(name="Viewer").one()
    editor.enterprise_role = None
    db_session.flush()
    login_as(client, editor)
    refused = client.post("/ai-chat/application-change/confirm", json={"token": token}).get_json()
    assert refused["success"] is False and refused["change_state"] == "refused"
    assert "I have not changed anything" in refused["html"]
    db_session.expire_all()
    assert ApplicationComponent.query.get(world["app_a"].id).description == "Old description a"


# -- the capability reader holds its organisation guard ---------------------------


def test_the_reader_gives_nothing_for_another_organisations_application(world):
    from app.services.application_capabilities import application_capability_links

    assert len(application_capability_links(world["app_a"].id, world["org_a"].id)) == 3
    assert application_capability_links(world["app_b"].id, world["org_a"].id) == []
    assert application_capability_links(world["app_a"].id, world["org_b"].id) == []


# -- a word that is not an application is not captured as one ------------------------


@pytest.mark.parametrize("question", (
    "Which capabilities do we support?",
    "Which capabilities do our applications support?",
))
def test_words_that_match_no_application_go_to_the_model(world, client, login_as, question):
    from app.modules.intelligence.services.nl_query_interpreter import interpret

    org = world["org_a"]
    assert interpret(question, org.id)["entry_id"] != "capabilities_of_application"
    login_as(client, world["editor"])
    answer = _ask(client, question)
    assert answer.get("entry_id") != "capabilities_of_application"
    assert "nothing recorded matches" not in (answer.get("html") or "").lower()
    assert world["tweed"].name not in (answer.get("html") or ""), "an application containing 'we' answered for 'we'"


def test_an_exact_application_name_is_still_taken_whatever_its_case_and_spacing(world, client, login_as):
    login_as(client, world["editor"])
    spaced = world["name"].upper().replace(" ", "  ")
    answer = _ask(client, f"Which capabilities does {spaced} support?")
    assert answer["entry_id"] == "capabilities_of_application"
    for cap in world["caps_a"]:
        assert cap.name in answer["html"]
    assert world["cap_b"].name not in answer["html"]


# -- the model has one tool for the mapping ----------------------------------------


def _run_with_stub_model(app, user, org, message, application_name):
    """One turn on the stub provider: it calls the capability tool once, then answers in words."""
    from flask import g

    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    calls = {"n": 0}

    def _stub(self, provider, model, api_key, system_prompt, messages, tool_schemas, stream=False, base_url=None):
        calls["n"] += 1
        if calls["n"] == 1:
            arguments = {"application_name": application_name}
            raw = MagicMock()
            raw.content = [{"type": "tool_use", "id": "call_cap", "name": "get_application_capabilities",
                            "input": arguments}]
            return {"text": "", "tool_calls": [
                {"id": "call_cap", "name": "get_application_capabilities", "arguments": arguments}], "raw": raw}
        raw = MagicMock()
        raw.content = [{"type": "text", "text": "Those are the capabilities."}]
        return {"text": "Those are the capabilities.", "tool_calls": [], "raw": raw}

    originals = (LLMService._get_configured_provider, LLMService._get_all_api_keys, AgentRunner._call_llm)
    try:
        LLMService._get_configured_provider = staticmethod(lambda: ("anthropic", "claude-opus-5"))
        LLMService._get_all_api_keys = staticmethod(lambda provider: ["fake-test-key"])
        AgentRunner._call_llm = _stub
        with app.test_request_context("/"):
            g.current_org_id = org.id
            return AgentRunner(user.id, auto_execute=True).run(
                user_message=message, domain="general", context={}, persona="enterprise_architect",
            )
    finally:
        LLMService._get_configured_provider, LLMService._get_all_api_keys, AgentRunner._call_llm = originals


@pytest.mark.parametrize("phrasing", PHRASINGS)
def test_every_phrasing_reaches_the_recorded_answer_through_the_tool_and_is_cited(app, world, phrasing):
    result = _run_with_stub_model(
        app, world["editor"], world["org_a"], phrasing.format(name=world["name"]), world["name"],
    )
    taken = [a for a in result.get("actions_taken", []) if a.get("tool") == "get_application_capabilities"]
    assert taken, result
    rows = taken[0]["result"]
    assert sorted(r["name"] for r in rows) == sorted(c.name for c in world["caps_a"])
    assert world["cap_b"].name not in str(result)
    cited = {s["name"]: s["url"] for s in result.get("sources", []) if s["type"] == "capability"}
    assert set(cited) == {c.name for c in world["caps_a"]}, result.get("sources")
    for capability in world["caps_a"]:
        assert cited[capability.name] == f"/enterprise/capabilities/{capability.id}/fact-sheet"


def test_the_tool_reads_the_organisation_of_the_person_asking_and_says_so_when_nothing_matches(app, world):
    from flask import g

    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    with app.test_request_context("/"):
        g.current_org_id = world["org_b"].id
        tool = ToolExecutor(user_id=world["user_b"].id)
        mine = tool.execute(ToolCall(id="t1", name="get_application_capabilities",
                                     arguments={"application_name": world["name"]}))
        assert sorted(r["name"] for r in mine["result"]) == sorted(c.name for c in world["caps_b"])
        assert not {c.name for c in world["caps_a"]} & {r["name"] for r in mine["result"]}
        none = tool.execute(ToolCall(id="t2", name="get_application_capabilities",
                                     arguments={"application_name": world["tweed"].name}))
        assert none["success"] is True and none["result"] == [] and none["count"] == 0
        assert "nothing recorded matches" in none["message"]
        missing = tool.execute(ToolCall(id="t3", name="get_application_capabilities", arguments={}))
        assert missing["success"] is False


def test_the_tool_is_a_read_the_model_is_offered_and_the_prompt_names_no_tenant_application(world):
    from app.modules.ai_chat.services import agent_runner
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

    schema = TOOL_SCHEMA_BY_NAME["get_application_capabilities"]
    assert schema["mutates"] is False and schema["tier"] == "auto" and schema["risk_class"] == "read"
    assert "get_application_capabilities" in agent_runner._AGENT_PREFIX
    assert "Ifiok" not in agent_runner._AGENT_PREFIX
