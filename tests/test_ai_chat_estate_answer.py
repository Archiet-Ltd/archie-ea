"""A question about the organisation's own estate is answered in the chat, as cited cards."""
import re

from app import db
from tests.test_sample_company_working_data import _architect

QUESTION = "What breaks if Payments service fails"


def _load(client, login_as, user):
    login_as(client, user)
    assert client.post("/architecture/sample-company/load", headers={"Accept": "application/json"}).status_code == 200


def _ask(client, message):
    return client.post("/ai-chat/estate-answer", json={"message": message})


def test_the_chat_answers_what_breaks_with_a_cited_card_for_every_connection(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "chat-ask-a@example.com")
    assert user.enterprise_role == "enterprise_architect"
    _load(client, login_as, user)
    body = _ask(client, QUESTION).get_json()
    assert body["found"] is True
    html = body["html"]
    assert "What breaks if Payments service fails" in html
    rows = html.count("data-answer-row")
    assert rows >= 10
    # Every connection says where it was read from; none is unsourced.
    assert html.count("data-citation>") == rows
    assert "Source not recorded" not in html
    assert "Imported from Sample company" in html
    # Applications carry the health the dashboard shows, and an owner named once.
    assert 'aria-label="Health: Healthy"' in html
    assert 'aria-label="Owner: Sam Founder"' in html
    assert "Sam Founder, Sam Founder" not in html
    # The card opens the stored answer, which has content.
    assert body["page_url"] == "/intelligence/answers/" + body["address"]
    page = client.get(body["page_url"])
    assert page.status_code == 200
    assert "Payments service" in page.get_data(as_text=True)


def test_the_total_on_the_card_is_the_number_of_cards(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "chat-ask-b@example.com")
    _load(client, login_as, user)
    html = _ask(client, QUESTION).get_json()["html"]
    total = int(re.search(r"(\d+) connections? in your records", html).group(1))
    assert html.count("data-answer-row") == total


def test_another_organisation_gets_no_answer_and_cannot_open_the_address(client, login_as, make_org):
    org, other = make_org(), make_org()
    owner = _architect(org, "chat-ask-c@example.com")
    outsider = _architect(other, "chat-ask-d@example.com")
    _load(client, login_as, owner)
    mine = _ask(client, QUESTION).get_json()
    assert mine["found"] is True
    login_as(client, outsider)
    assert _ask(client, QUESTION).get_json() == {"success": True, "found": False}
    assert client.get(mine["page_url"]).status_code == 404


def test_a_question_that_names_nothing_of_the_organisation_goes_to_the_model(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "chat-ask-e@example.com")
    _load(client, login_as, user)
    assert _ask(client, "What breaks if the moon falls").get_json()["found"] is False
    assert _ask(client, "Tell me about Payments service").get_json()["found"] is False
    assert _ask(client, "").status_code == 400


def test_the_old_ask_address_opens_the_chat_with_the_question(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "chat-ask-f@example.com")
    login_as(client, user)
    response = client.get("/intelligence/ask?q=" + QUESTION.replace(" ", "%20"))
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/ai-chat")
    assert "q=What+breaks+if+Payments+service+fails" in response.headers["Location"]
    assert client.get("/intelligence/ask").headers["Location"].endswith("/ai-chat")
    page = client.get(response.headers["Location"])
    assert page.status_code == 200


def test_the_sidebar_question_link_opens_the_chat(app):
    from app.utils.role_access import SIDEBAR_ZONES

    for role, zones in SIDEBAR_ZONES.items():
        my_work = next(z for z in zones if z["zone"] == "my_work")["links"]
        assert my_work[0]["label"] == "Ask a question"
        assert my_work[0]["endpoint"] == "unified_ai_chat.index"
        every = [link["endpoint"] for z in zones for link in z["links"]]
        assert every.count("unified_ai_chat.index") == 1, role
