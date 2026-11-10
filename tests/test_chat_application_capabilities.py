"""The chat answers "which capabilities does <application> support?" from the model.

The answer is read at question time from the same mapping the application page
reads, lists the same capabilities the page lists, and cites each to its own
fact sheet. Two organisations: an application of the same name in the other
organisation never leaks in.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", first_name=label.capitalize(),
        last_name="Test", organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect",
    )
    user.role = Role.query.filter_by(name="User").one()
    db_session.add(user)
    db_session.flush()
    return user


def _capability(db_session, org, name):
    from app.models.business_capabilities import BusinessCapability

    row = BusinessCapability(name=f"{name} {uuid.uuid4().hex[:5]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _map_legacy(db_session, org, application, capability, support="primary"):
    from app.models.application_capability import ApplicationCapabilityMapping

    db_session.add(ApplicationCapabilityMapping(
        application_component_id=application.id, business_capability_id=capability.id,
        organization_id=org.id, support_level=support,
    ))
    db_session.flush()


def _map_unified(db_session, org, application, capability):
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    # Creating a business capability projects its unified twin.
    unified = UnifiedCapability.query.filter_by(
        organization_id=org.id, source_table="business_capability", source_id=str(capability.id),
    ).one()
    db_session.add(UnifiedApplicationCapabilityMapping(
        application_component_id=application.id, unified_capability_id=unified.id,
    ))
    db_session.flush()


@pytest.fixture
def world(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = make_org("cap-a"), make_org("cap-b")
    name = f"Ifiok {uuid.uuid4().hex[:5]}"
    apps = {}
    for key, org in (("a", org_a), ("b", org_b)):
        row = ApplicationComponent(name=name, organization_id=org.id, deployment_status="production")
        db_session.add(row)
        db_session.flush()
        apps[key] = row
    caps_a = [_capability(db_session, org_a, "Payments"), _capability(db_session, org_a, "Onboarding")]
    cap_b = _capability(db_session, org_b, "Secret other-org capability")
    _map_legacy(db_session, org_a, apps["a"], caps_a[0])
    _map_unified(db_session, org_a, apps["a"], caps_a[1])
    _map_legacy(db_session, org_b, apps["b"], cap_b)
    return {
        "org_a": org_a, "org_b": org_b, "apps": apps, "name": name, "caps_a": caps_a, "cap_b": cap_b,
        "user_a": _user(db_session, org_a, "usera"), "user_b": _user(db_session, org_b, "userb"),
    }


def _ask(client, question):
    resp = client.post("/ai-chat/estate-answer", json={"message": question, "remember": False})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def test_the_answer_lists_the_capabilities_the_application_page_lists(world, client, login_as):
    login_as(client, world["user_a"])
    answer = _ask(client, f"Which capabilities does {world['name']} support?")
    assert answer["found"] is True and answer["entry_id"] == "capabilities_of_application"
    html = answer["html"]
    names = [c.name for c in world["caps_a"]]
    for name in names:
        assert name in html, name
    assert world["cap_b"].name not in html, "another organisation's capability leaked into the answer"

    # Each is a link to its own fact sheet, which opens with the capability on it.
    links = re.findall(r'href="(/enterprise/capabilities/\d+/fact-sheet)"', html)
    assert len(links) == len(names) == 2
    for link in links:
        page = client.get(link)
        assert page.status_code == 200
    assert any(name in client.get(link).get_data(as_text=True) for link in links for name in names)

    # The application page shows the same capabilities, and a count that agrees.
    page = client.get(f"/applications/{world['apps']['a'].id}").get_data(as_text=True)
    for name in names:
        assert name in page, name
    assert re.search(r"Mapped Capabilities.{0,400}?<[^>]*>\s*2\s*<", page, re.S), "the page count is not 2"


def test_the_other_organisation_gets_only_its_own_capabilities(world, client, login_as):
    login_as(client, world["user_b"])
    html = _ask(client, f"Which capabilities does {world['name']} support?")["html"]
    assert world["cap_b"].name in html
    for capability in world["caps_a"]:
        assert capability.name not in html


def test_an_application_with_no_mapped_capability_says_so_and_links_to_where_one_is_mapped(
    world, client, login_as, db_session
):
    from app.models.application_portfolio import ApplicationComponent

    bare = ApplicationComponent(name=f"Bare {uuid.uuid4().hex[:5]}", organization_id=world["org_a"].id)
    db_session.add(bare)
    db_session.flush()
    login_as(client, world["user_a"])
    html = _ask(client, f"Which capabilities does {bare.name} support?")["html"]
    assert "has no capability mapped to it yet" in html
    assert f"/applications/{bare.id}?tab=capabilities" in html


def test_a_name_that_is_not_in_the_model_is_not_answered_with_a_guess(world, client, login_as):
    login_as(client, world["user_a"])
    html = _ask(client, "Which capabilities does Nonexistent platform support?")["html"]
    assert "Nothing is recorded under that name" in html
    for capability in world["caps_a"]:
        assert capability.name not in html


def test_the_other_questions_still_go_where_they_went(world):
    from app.modules.intelligence.services.nl_query_interpreter import evaluation_set, interpret

    for item in evaluation_set():
        got = interpret(item["question"])["entry_id"]
        assert got == item["expected_entry_id"], item["question"]
