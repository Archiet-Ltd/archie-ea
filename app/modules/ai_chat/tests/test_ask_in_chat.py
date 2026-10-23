"""One place to ask: the AI chat answers with the Ask page's answer card.

Pins: the Ask address with an element and a question opens the chat with the
same starter; the chat page carries the starters (the element picker and the
question chips) and the one answer card; a starter reaches the answer service
without a model call; the card is built from the stored answer, so its sources
are the ones the answer service returned; a question the model holds nothing
for is shown as not recorded; another organisation's answer never reaches the
chat.

Fixtures (app, db_session, make_org, client, login_as) come from
app/modules/conftest.py.
"""

from __future__ import annotations

import re
import uuid
from urllib.parse import parse_qs, urlparse

import pytest


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"ask-chat-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Ask",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    row = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _link(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    row = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _estate(db_session, org_id, prefix):
    root = _element(db_session, org_id, f"{prefix} Root")
    mid = _element(db_session, org_id, f"{prefix} Mid")
    leaf = _element(db_session, org_id, f"{prefix} Leaf")
    _link(db_session, org_id, root, mid)
    _link(db_session, org_id, mid, leaf)
    db_session.commit()
    return root, mid, leaf


# --------------------------------------------------------------------------- #
# The Ask address opens the chat                                               #
# --------------------------------------------------------------------------- #


def test_the_ask_address_with_an_element_and_a_question_opens_the_chat(
    app, db_session, make_org, client, login_as
):
    org = make_org("ask-chat-redirect")
    login_as(client, _user(db_session, org.id))

    response = client.get("/intelligence/ask?element=42&question=impact")

    assert response.status_code == 302
    target = urlparse(response.headers["Location"])
    assert target.path.rstrip("/") == "/ai-chat"
    query = parse_qs(target.query)
    assert query["starter"] == ["impact"]
    assert query["element"] == ["42"]


def test_a_risk_question_on_the_ask_address_opens_the_chat_with_that_starter(
    app, db_session, make_org, client, login_as
):
    org = make_org("ask-chat-redirect-risk")
    login_as(client, _user(db_session, org.id))

    response = client.get("/intelligence/ask?element=7&question=risk")

    assert response.status_code == 302
    assert parse_qs(urlparse(response.headers["Location"]).query)["starter"] == ["risk"]


def test_an_element_alone_on_the_ask_address_asks_what_breaks(
    app, db_session, make_org, client, login_as
):
    org = make_org("ask-chat-redirect-default")
    login_as(client, _user(db_session, org.id))

    response = client.get("/intelligence/ask?element=9")

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["Location"]).query)
    assert query["starter"] == ["impact"] and query["element"] == ["9"]


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?element=3&question=strategy",
        "?element=3&question=portfolio",
        "?element=3&question=programme",
        "?element=3&question=data",
        "?element=3&question=compliance",
        "?element=3&question=accountability",
        "?question=impact",
        "?question=risk",
    ],
)
def test_the_ask_address_still_shows_the_ask_page_for_every_other_request(
    app, db_session, make_org, client, login_as, query
):
    org = make_org("ask-chat-no-redirect")
    login_as(client, _user(db_session, org.id))

    response = client.get("/intelligence/ask" + query)

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'x-data="askSurface()"' in page
    assert 'data-ask-in-chat-link' in page


def test_the_ask_page_points_to_the_chat_for_the_impact_and_risk_questions(
    app, db_session, make_org, client, login_as
):
    org = make_org("ask-chat-link")
    login_as(client, _user(db_session, org.id))

    page = client.get("/intelligence/ask").get_data(as_text=True)

    assert re.search(r'<a[^>]*data-ask-in-chat-link[^>]*href="/ai-chat"', page) or re.search(
        r'<a[^>]*href="/ai-chat"[^>]*data-ask-in-chat-link', page
    )
    assert "what breaks" in page.lower() and "risk" in page.lower()


@pytest.mark.parametrize("element", ["abc", "0", "-3", "1.5"])
def test_an_element_that_is_not_a_whole_number_does_not_redirect(
    app, db_session, make_org, client, login_as, element
):
    org = make_org("ask-chat-bad-element")
    login_as(client, _user(db_session, org.id))

    response = client.get(f"/intelligence/ask?question=impact&element={element}")

    assert response.status_code == 200
