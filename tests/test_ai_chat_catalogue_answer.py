"""A plain startup question is answered in the chat from the catalogue, each answer citing records."""
from app import db
from tests.test_aggregate_answers import _make_app_with_cost
from tests.test_sample_company_working_data import _architect

INVENTORY = "What applications do we run and who owns each one?"
UNOWNED = "Which applications have no owner recorded?"


def _ask(client, message):
    return client.post("/ai-chat/estate-answer", json={"message": message})


def test_the_inventory_question_cites_each_application_and_the_link_opens_it(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "chat-cat-a@example.com")
    assert user.enterprise_role == "enterprise_architect"
    app_row = _make_app_with_cost(org.id, "Catalogue Ledger", annual_cost=1200)
    db.session.commit()
    login_as(client, user)
    body = _ask(client, INVENTORY).get_json()
    assert body["found"] is True and body["entry_id"] == "applications_and_owners"
    assert "Catalogue Ledger" in body["html"]
    href = "/applications/%d" % app_row.id
    assert 'href="%s"' % href in body["html"]
    page = client.get(href)
    assert page.status_code == 200 and "Catalogue Ledger" in page.get_data(as_text=True)


def test_an_organisation_with_no_applications_is_told_what_would_let_the_chat_answer(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "chat-cat-b@example.com")
    login_as(client, user)
    body = _ask(client, UNOWNED).get_json()
    assert body["found"] is True
    assert "Your records cannot answer this yet" in body["html"]
    assert "No applications are recorded in this organisation" in body["html"]
    assert "every application" not in body["html"].lower()
    assert "Add your applications to the portfolio" in body["html"]
    assert "data-catalogue-record" not in body["html"]


def test_every_application_owned_is_an_answer_not_a_gap(client, login_as, make_org):
    from tests.test_aggregate_answers import _make_user

    org = make_org()
    user = _architect(org, "chat-cat-f@example.com")
    owner = _make_user(db.session, org)
    _make_app_with_cost(org.id, "Owned Ledger", annual_cost=5, owner_user_id=owner.id)
    db.session.commit()
    login_as(client, user)
    html = _ask(client, UNOWNED).get_json()["html"]
    assert "Every application in this organisation has a recorded owner." in html
    assert "cannot answer" not in html and "Add your applications" not in html


def test_another_organisations_applications_are_never_cited(client, login_as, make_org):
    org, other = make_org(), make_org()
    mine = _architect(org, "chat-cat-c@example.com")
    outsider = _architect(other, "chat-cat-d@example.com")
    _make_app_with_cost(org.id, "Mine Only Ledger", annual_cost=10)
    _make_app_with_cost(other.id, "Theirs Only Ledger", annual_cost=10)
    db.session.commit()
    login_as(client, mine)
    html = _ask(client, INVENTORY).get_json()["html"]
    assert "Mine Only Ledger" in html and "Theirs Only Ledger" not in html
    login_as(client, outsider)
    html = _ask(client, INVENTORY).get_json()["html"]
    assert "Theirs Only Ledger" in html and "Mine Only Ledger" not in html


def test_ordinary_conversation_still_goes_to_the_model(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "chat-cat-e@example.com")
    login_as(client, user)
    for message in ("what is the meaning of life", "What if the board says no to the plan?"):
        assert _ask(client, message).get_json() == {"success": True, "found": False}
