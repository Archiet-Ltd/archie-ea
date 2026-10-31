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
    # Applications carry the health the dashboard shows. Corrected: this already
    # failed on the line, asserting an owner "named once" -- the signed-in person,
    # Sam Founder, who merely clicked Load the sample company. The sample names no
    # real owner, so none is invented; every row reads "No owner recorded" instead.
    assert 'aria-label="Health: Healthy"' in html
    assert "No owner recorded" in html
    assert "Sam Founder" not in html
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
    theirs = _ask(client, QUESTION).get_json()
    # The outsider's organisation has no such service: the chat says it cannot answer, with
    # no stored answer, no connection from the owner's organisation and no record to open.
    assert "page_url" not in theirs and "address" not in theirs
    html = theirs.get("html", "")
    assert "data-answer-row" not in html and "data-catalogue-record" not in html
    # The empty state repeats the asked name (the outsider's own question) and nothing else: no
    # connection, owner, source or record of the owner's organisation.
    assert "Nothing recorded matches" in html
    for fact in ("Imported from Sample company", "Sam Founder", "data-citation", "Health:"):
        assert fact not in html, fact
    for name in re.findall(r"data-answer-row[^>]*>\s*<a[^>]*>([^<]+)</a>", mine["html"]):
        assert name.strip() == "Payments service" or name.strip() not in html, name
    assert "Add &#34;Payments service&#34; to your model" in theirs.get("html", "")
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


def _history(client):
    return client.get("/ai-chat/threads").get_json()["threads"]


def test_an_estate_answer_is_kept_in_the_conversation_history(client, login_as, make_org):
    org, other = make_org(), make_org()
    owner = _architect(org, "chat-hist-a@example.com")
    outsider = _architect(other, "chat-hist-b@example.com")
    _load(client, login_as, owner)
    assert _history(client) == []  # the rail was read once before any answer, as the page does on load
    body = _ask(client, QUESTION).get_json()
    assert body["thread_id"]
    threads = _history(client)
    assert [t["id"] for t in threads] == [body["thread_id"]]
    saved = client.get("/ai-chat/threads/" + body["thread_id"]).get_json()["messages"]
    assert [m["role"] for m in saved] == ["user", "assistant"]
    assert saved[0]["content"] == QUESTION
    assert "connections. [Open this answer](" + body["page_url"] + ")" in saved[1]["content"]
    # A second question in the same conversation joins it rather than starting another.
    again = client.post("/ai-chat/estate-answer", json={"message": QUESTION, "thread_id": body["thread_id"]}).get_json()
    assert again["thread_id"] == body["thread_id"]
    assert len(client.get("/ai-chat/threads/" + body["thread_id"]).get_json()["messages"]) == 4
    # Nobody else sees it.
    login_as(client, outsider)
    assert _history(client) == []
    assert client.get("/ai-chat/threads/" + body["thread_id"]).status_code == 404


def _seed_initiative(org, name):
    from app.models.archimate_core import ArchiMateElement
    from app.models.enterprise_intelligence import PortfolioInitiative

    element = ArchiMateElement.query.filter_by(organization_id=org.id, name="Payments service").first()
    db.session.add(PortfolioInitiative(
        organization_id=org.id, name=name, archimate_element_id=element.id, status="Active",
        completion_percentage=45, total_budget=200000.0, spent_to_date=180000.0,
    ))
    db.session.commit()


STRATEGY = "What are we trying to achieve at Payments service and how is it tracking"


def test_the_strategy_question_is_answered_in_the_chat_for_its_own_organisation_only(client, login_as, make_org):
    from app.models.user import User

    org, other = make_org(), make_org()
    architect = _architect(org, "chat-lens-a@example.com")
    outsider = _architect(other, "chat-lens-b@example.com")
    _load(client, login_as, architect)
    _seed_initiative(org, "Payments modernisation")
    body = _ask(client, STRATEGY).get_json()
    assert body["found"] is True and body["lens"] == "strategy"
    html = body["html"]
    assert "Payments modernisation" in html and "45% complete" in html
    # An architect has no budget authority: the figure is withheld, not shown.
    assert architect.enterprise_role == "enterprise_architect"
    assert "Restricted to roles with budget authority" in html and "Budget variance" not in html
    assert "1 initiative recorded" in client.get("/ai-chat/threads/" + body["thread_id"]).get_json()["messages"][1]["content"]
    # A person with budget authority sees it.
    architect.enterprise_role = "cto"
    db.session.commit()
    assert User.query.get(architect.id).enterprise_role == "cto"
    assert "Budget variance" in _ask(client, STRATEGY).get_json()["html"]
    # The other organisation has its own Payments service and none of this.
    _load(client, login_as, outsider)
    theirs = _ask(client, STRATEGY).get_json()
    assert theirs["found"] is True
    assert "Payments modernisation" not in theirs["html"]
    assert "No initiatives recorded against this element" in theirs["html"]


def test_every_lens_question_is_answered_with_cards_or_a_plain_empty_state(client, login_as, make_org):
    org = make_org()
    architect = _architect(org, "chat-lens-c@example.com")
    _load(client, login_as, architect)
    asked = {
        "risk": "What could hurt Payments service and what does it touch",
        "portfolio": "What is the rationalization status of Payments service",
        "programme": "What's changing at Payments service and is it on track",
        "accountability": "Who's accountable for Payments service",
        "data": "What data does Payments service hold or produce and where does it flow",
        "compliance": "Which regulations and controls apply to Payments service",
    }
    for lens, question in asked.items():
        body = _ask(client, question).get_json()
        assert body["found"] is True and body["lens"] == lens, lens
        assert "data-lens-cards" in body["html"] and "Payments service" in body["html"]
        assert "We could not look that up" not in body["html"], lens
