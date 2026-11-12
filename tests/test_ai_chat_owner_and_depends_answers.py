"""In the chat, "who owns X" gets X's owners and "what does X depend on" gets what it
depends on; only "what breaks" gets the "what breaks" card."""
import re

from app import db
from tests.test_sample_company_working_data import _architect

THREE = "What does Ifiok run on, which suppliers does it depend on, and who owns it?"


def _ask(client, message, thread_id=None):
    return client.post("/ai-chat/estate-answer", json={"message": message, "thread_id": thread_id}).get_json()


def _seed(org, owner_first, owner_last, node, supplier):
    """Ifiok with one named owner, one technology node it runs on and one supplier."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.user import User
    from app.services.element_ownership_service import set_element_owner

    app = ArchiMateElement(name="Ifiok", type="ApplicationComponent", layer="application", organization_id=org.id)
    host = ArchiMateElement(name=node, type="Node", layer="technology", organization_id=org.id)
    vendor = ArchiMateElement(name=supplier, type="BusinessActor", layer="business", organization_id=org.id)
    db.session.add_all([app, host, vendor])
    db.session.flush()
    component = ApplicationComponent(name="Ifiok", organization_id=org.id, archimate_element_id=app.id)
    person = User(
        email="%s.%s@example.test" % (owner_first.lower(), org.id), first_name=owner_first, last_name=owner_last,
        organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect",
    )
    db.session.add_all([component, person])
    db.session.flush()
    set_element_owner(
        element_type="application", element_id=component.id, user_id=person.id,
        organization_id=org.id, ownership_type="primary", commit=False,
    )
    for source in (host, vendor):
        db.session.add(ArchiMateRelationship(type="Serving", source_id=source.id, target_id=app.id, organization_id=org.id))
    db.session.commit()
    return app


def _two_orgs(make_org):
    org_a, org_b = make_org(), make_org()
    _seed(org_a, "Olive", "Ashby", "Ashby Cloud Node", "Ashby Hosting Ltd")
    _seed(org_b, "Bram", "Brook", "Brook Server Node", "Brook Supplies Ltd")
    return (org_a, _architect(org_a, "own-a@example.com")), (org_b, _architect(org_b, "own-b@example.com"))


def test_who_owns_names_the_owner_and_is_not_the_what_breaks_card(client, login_as, make_org):
    (_, user), _ = _two_orgs(make_org)
    login_as(client, user)
    body = _ask(client, "Who owns Ifiok?")
    assert body["found"] is True
    assert "Olive Ashby" in body["html"]
    assert "What breaks if" not in body["html"]


def test_what_does_it_depend_on_names_what_it_runs_on_under_its_own_heading(client, login_as, make_org):
    (_, user), _ = _two_orgs(make_org)
    login_as(client, user)
    for question in ("What does Ifiok depend on?", "What does Ifiok run on?"):
        html = _ask(client, question)["html"]
        assert "What Ifiok depends on" in html, question
        assert "Ashby Cloud Node" in html and "Ashby Hosting Ltd" in html, question
        assert "What breaks if" not in html, question


def test_what_breaks_keeps_its_card(client, login_as, make_org):
    (_, user), _ = _two_orgs(make_org)
    login_as(client, user)
    html = _ask(client, "What breaks if Ifiok fails?")["html"]
    assert "What breaks if Ifiok fails" in html
    # Ifiok depends on the node; the node does not depend on Ifiok, so it is not shown as broken:
    # the card says nothing depends on Ifiok, and names the node only as something Ifiok is connected to.
    assert "Nothing is recorded as depending on Ifiok" in html
    assert "If Ifiok fails," not in html
    affected = re.findall(r'data-testid="answer-sentence">(.*?)</p>', html, re.S)
    assert not any("Ashby Cloud Node" in sentence for sentence in affected), affected
    assert re.search(r"data-depends-on>Ifiok is connected to [^<]*Ashby Cloud Node", html)
    assert "Brook Server Node" not in html


def test_the_three_part_question_answers_every_part_in_order_in_one_reply(client, login_as, make_org):
    (_, user), _ = _two_orgs(make_org)
    login_as(client, user)
    body = _ask(client, THREE)
    html = body["html"]
    assert body["cards"] == ["upstream", "owner"]
    assert "What Ifiok depends on" in html and "Olive Ashby" in html
    assert "Ashby Cloud Node" in html and "Ashby Hosting Ltd" in html
    assert html.index("Ashby Cloud Node") < html.index("Olive Ashby")
    assert "What breaks if" not in html


def test_each_organisation_sees_only_its_own_owner_and_dependencies(client, login_as, make_org):
    (_, user_a), (_, user_b) = _two_orgs(make_org)
    login_as(client, user_a)
    mine = _ask(client, THREE)["html"]
    assert "Olive Ashby" in mine and "Ashby Cloud Node" in mine
    assert "Bram" not in mine and "Brook" not in mine
    login_as(client, user_b)
    theirs = _ask(client, THREE)["html"]
    assert "Bram Brook" in theirs and "Brook Server Node" in theirs
    assert "Olive" not in theirs and "Ashby" not in theirs
    assert "Olive" not in _ask(client, "Who owns Ifiok?")["html"]


def test_every_follow_up_gets_its_own_answer_and_the_thread_count_follows(client, login_as, make_org):
    (_, user), _ = _two_orgs(make_org)
    login_as(client, user)
    first = _ask(client, THREE)
    thread = first["thread_id"]
    # The rail reads the list after the first answer, as the page does, and again later.
    assert [t["message_count"] for t in client.get("/ai-chat/threads").get_json()["threads"]] == [2]
    second = _ask(client, "Who owns Ifiok?", thread)
    third = _ask(client, "Who owns Ifiok?", thread)
    assert second["thread_id"] == third["thread_id"] == thread
    for body in (second, third):
        assert "Olive Ashby" in body["html"] and "What breaks if" not in body["html"]
    threads = client.get("/ai-chat/threads").get_json()["threads"]
    assert [t["message_count"] for t in threads if t["id"] == thread] == [6]
    saved = client.get("/ai-chat/threads/" + thread).get_json()["messages"]
    assert len(saved) == 6
    assert not any(re.search(r"What breaks if", m["content"]) for m in saved)
