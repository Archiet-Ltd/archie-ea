"""The chat shows an estate answer as Ask's answer card.

A typed question, a starter and the Ask page's address all end at the same
stored answer shown as the same card. Pins: the card's address and citations are
those of the stored answer for the same question; a starter makes no model call;
a question the model holds nothing for says "not recorded" and shows no figure;
the Ask address opens the chat with the starter already run; one organisation's
chat never shows another organisation's answer.

Fixtures (app, db_session, make_org, client, login_as) come from
app/modules/conftest.py; the scripted-model turn runner is the one the citation
gate tests already use.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from flask import g

from app.modules.ai_chat.tests.test_answer_tool_and_citation_gate import (
    _call_answer,
    _element,
    _estate,
    _run_turn,
    _user,
)


def _card_of(result):
    cards = result.get("answer_cards") or []
    assert len(cards) == 1, "exactly one card for one answered question"
    return cards[0]


def test_a_typed_estate_question_shows_a_card_with_the_stored_answers_citations(
    app, db_session, make_org, monkeypatch
):
    org = make_org("askchat-typed")
    user = _user(db_session, org.id)
    root, mid, leaf = _estate(db_session, org.id, "Typed")
    script = [_call_answer(root.id), {"text": "Two items are affected."}]

    result, events, _runner, _seen = _run_turn(
        app, monkeypatch, script, user=user, stream=True,
        message="What breaks if Typed Root is unavailable?",
    )

    card = _card_of(result)
    assert "data-answer-card" in card and 'data-state="answered"' in card
    assert "Typed Mid" in card and "Typed Leaf" in card
    # The tool_result event carries the same card to the browser.
    assert [e["card"] for e in events if e.get("type") == "tool_result"] == [card]

    from app.modules.intelligence.services import answer_envelope as envelope

    address = re.search(r'data-answer-address="([0-9a-f]{32})"', card).group(1)
    with app.test_request_context("/"):
        g.current_org_id = org.id
        shown = envelope.open_answer(address)
        again = envelope.ask_and_store("impact", {"element_id": root.id}, user_id=user.id)
        assert shown.citations == again.citations and shown.citations
        assert f'data-citation-count="{len(again.citations)}"' in card
        assert f'href="/intelligence/answers/{address}"' in card


# --- a typed question no tool is called for -----------------------------------


def _unowned_applications(db_session, org_id, names):
    from app.models.application_portfolio import ApplicationComponent

    for name in names:
        db_session.add(ApplicationComponent(name=name, organization_id=org_id))
    db_session.commit()


def test_a_typed_question_the_agent_does_not_route_is_read_by_the_interpreter(
    app, db_session, make_org, monkeypatch
):
    org = make_org("askchat-interpret")
    user = _user(db_session, org.id)
    _unowned_applications(db_session, org.id, ["Ledger Core", "Payroll Hub"])
    script = [{"text": "Every application has an owner, I am sure."}]

    result, events, _runner, _seen = _run_turn(
        app, monkeypatch, script, user=user, stream=True,
        message="Which applications have no owner?",
    )

    card = _card_of(result)
    assert "Ledger Core" in card and "Payroll Hub" in card
    assert "Which applications have no owner?" in card
    # The model's own words never reach the person for this question.
    assert "I am sure" not in result["response"]
    assert result["response"].startswith("2 recorded items")
    assert not result.get("error")


def test_a_question_that_maps_to_nothing_keeps_the_models_reply(
    app, db_session, make_org, monkeypatch
):
    org = make_org("askchat-unmapped")
    user = _user(db_session, org.id)

    result, _events, _runner, _seen = _run_turn(
        app, monkeypatch, [{"text": "It is sunny."}], user=user,
        message="What is the weather today?",
    )

    assert result["response"] == "It is sunny."
    assert not result.get("answer_cards")


@pytest.fixture
def chat_client(app, db_session, make_org, client, login_as):
    def _start(slug):
        org = make_org(slug)
        user = _user(db_session, org.id)
        login_as(client, user)
        return org, user

    return _start


def _stream_events(client, payload):
    response = client.post("/ai-chat/message/stream", json=payload)
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    events = []
    for line in response.get_data(as_text=True).splitlines():
        if line.startswith("data:") and line[5:].strip() != "[DONE]":
            events.append(json.loads(line[5:]))
    return events


def test_with_no_model_a_typed_question_is_answered_by_keyword_reading(
    app, db_session, make_org, client, chat_client
):
    org, _user_row = chat_client("askchat-nomodel")
    _unowned_applications(db_session, org.id, ["Treasury Feed"])

    events = _stream_events(client, {"message": "which applications have no owner?"})

    done = events[-1]
    assert done["type"] == "done" and len(done["answer_cards"]) == 1
    assert "Treasury Feed" in done["answer_cards"][0]
    assert not done.get("error")


def test_with_no_model_a_question_nothing_matches_gets_the_unavailable_reply(
    app, db_session, make_org, client, chat_client
):
    chat_client("askchat-nomodel-miss")

    response = client.post("/ai-chat/message/stream", json={"message": "what is the weather?"})

    assert response.status_code == 503


def test_the_chat_page_says_what_it_can_answer_without_a_model(
    app, db_session, make_org, client, chat_client
):
    chat_client("askchat-nomodel-page")

    page = client.get("/ai-chat/").get_data(as_text=True)

    assert "data-without-model" in page
    assert "Which applications have no owner?" in page


# --- a question the model holds nothing for --------------------------------------


def test_a_question_with_no_rows_shows_not_recorded_and_no_figure(
    app, db_session, make_org, monkeypatch
):
    org = make_org("askchat-norows")
    user = _user(db_session, org.id)
    lonely = _element(db_session, org.id, "Lonely System")
    db_session.commit()
    script = [_call_answer(lonely.id), {"text": "Exactly 14 systems break if it fails."}]

    result, _events, _runner, _seen = _run_turn(
        app, monkeypatch, script, user=user, stream=True,
        message="What breaks if Lonely System fails?",
    )

    card = _card_of(result)
    assert 'data-state="not_recorded"' in card and "data-not-recorded" in card
    assert "Not recorded" in card
    assert "data-answer-count" not in card and "x-data=" not in card, "no rows, no cascade, no count"
    assert result["response"].startswith("Not recorded")
    assert "14" not in result["response"], "a figure the model made up is never shown"


def test_an_element_in_another_organisation_is_not_recorded(
    app, db_session, make_org, monkeypatch
):
    mine, theirs = make_org("askchat-mine"), make_org("askchat-theirs")
    user = _user(db_session, mine.id)
    foreign = _element(db_session, theirs.id, "Their System")
    db_session.commit()

    result, _events, _runner, _seen = _run_turn(
        app, monkeypatch, [_call_answer(foreign.id), {"text": "It has 3 dependants."}],
        user=user, message="What breaks if Their System fails?",
    )

    card = _card_of(result)
    assert 'data-state="not_recorded"' in card and "Their System" not in card
    assert "3" not in result["response"]


# --- starters ------------------------------------------------------------------


@pytest.fixture
def no_model_calls(monkeypatch):
    """Fail the test if anything asks a model: the agent, the gateway or the
    interpreter's model step."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_service_impl import LLMService
    from app.modules.intelligence.services import nl_query_interpreter

    def _refuse(*_a, **_k):
        raise AssertionError("a model was asked for a starter")

    monkeypatch.setattr(AgentRunner, "run", _refuse)
    monkeypatch.setattr(AgentRunner, "_call_llm", _refuse)
    monkeypatch.setattr(LLMService, "generate_from_prompt", staticmethod(_refuse))
    monkeypatch.setattr(LLMService, "_call_llm_with_failover", staticmethod(_refuse))
    monkeypatch.setattr(nl_query_interpreter, "_llm_match", _refuse)


def test_a_starter_runs_the_answer_without_asking_a_model(
    app, db_session, make_org, client, chat_client, no_model_calls
):
    org, user = chat_client("askchat-starter")
    root, mid, leaf = _estate(db_session, org.id, "Starter")

    events = _stream_events(client, {
        "message": "What breaks if Starter Root fails?",
        "starter": {"question": "impact", "element_id": root.id},
    })

    done = events[-1]
    assert done["type"] == "done" and not done.get("error")
    assert len(done["answer_cards"]) == 1
    card = done["answer_cards"][0]
    assert "Starter Mid" in card and "Starter Leaf" in card
    assert [e["card"] for e in events if e.get("type") == "tool_result"] == [card]


def test_a_risk_starter_runs_the_same_way(
    app, db_session, make_org, client, chat_client, no_model_calls
):
    org, _user_row = chat_client("askchat-starter-risk")
    root, *_ = _estate(db_session, org.id, "Hurt")

    events = _stream_events(client, {
        "message": "What could hurt Hurt Root?",
        "starter": {"question": "risk", "element_id": root.id},
    })

    done = events[-1]
    assert len(done["answer_cards"]) == 1
    assert 'data-state="not_recorded"' in done["answer_cards"][0], "no risk is recorded here"


@pytest.mark.parametrize("starter", [
    {"question": "strategy", "element_id": 1},
    {"question": "impact"},
    {"question": "impact", "element_id": "abc"},
    {"question": "impact", "element_id": True},
    "impact",
])
def test_a_malformed_starter_is_refused(
    app, db_session, make_org, client, chat_client, no_model_calls, starter
):
    chat_client("askchat-starter-bad")

    response = client.post("/ai-chat/message/stream", json={"message": "x", "starter": starter})

    assert response.status_code == 400


def test_the_starters_name_questions_the_answer_tool_asks():
    from app.modules.ai_chat.services.answer_card import STARTER_QUESTIONS, STARTER_TOOL_QUESTION
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

    offered = TOOL_SCHEMA_BY_NAME["answer"]["parameters"]["properties"]["question"]["enum"]
    assert {q["key"] for q in STARTER_QUESTIONS} == set(STARTER_TOOL_QUESTION)
    assert set(STARTER_TOOL_QUESTION.values()) <= set(offered)


def test_the_chat_page_carries_the_starters_in_the_empty_state_and_above_the_input(
    app, db_session, make_org, client, chat_client
):
    chat_client("askchat-page")

    page = client.get("/ai-chat/").get_data(as_text=True)

    assert 'data-starters="empty-state"' in page and 'data-starters="above-input"' in page
    assert "chat-start-picker-input" in page and "chat-ask-picker-input" in page
    assert page.count("What breaks if it fails?") >= 2 and page.count("What could hurt it?") >= 2
    assert "js/ai_chat/starters.js" in page and "js/intelligence/picker.js" in page


# --- another organisation ---------------------------------------------------------


def test_a_second_organisations_chat_never_shows_the_first_organisations_answer(
    app, db_session, make_org, client, login_as, monkeypatch
):
    org_a, org_b = make_org("askchat-iso-a"), make_org("askchat-iso-b")
    user_a, user_b = _user(db_session, org_a.id), _user(db_session, org_b.id)
    root_a, mid_a, _leaf_a = _estate(db_session, org_a.id, "Alpha")
    _estate(db_session, org_b.id, "Beta")

    # Organisation A asks, and gets a stored answer.
    result_a, _e, _r, _s = _run_turn(
        app, monkeypatch, [_call_answer(root_a.id), {"text": "Two."}], user=user_a,
        message="What breaks if Alpha Root fails?",
    )
    card_a = _card_of(result_a)
    address_a = re.search(r'data-answer-address="([0-9a-f]{32})"', card_a).group(1)
    assert "Alpha Mid" in card_a

    # Organisation B asks the same question about A's element: its chat shows
    # nothing of A's, and A's address does not open for it.
    other = app.test_client()
    login_as(other, user_b)
    events = _stream_events(other, {
        "message": "What breaks if that fails?",
        "starter": {"question": "impact", "element_id": root_a.id},
    })
    card_b = events[-1]["answer_cards"][0]
    assert 'data-state="not_recorded"' in card_b
    assert "Alpha" not in card_b and address_a not in card_b
    assert other.get(f"/intelligence/answers/{address_a}").status_code == 404


# --- the Ask address opens the chat ------------------------------------------------


def test_the_ask_address_opens_the_chat_and_pre_runs_the_same_answer(
    app, db_session, make_org, client, chat_client, no_model_calls
):
    org, user = chat_client("askchat-redirect")
    root, mid, leaf = _estate(db_session, org.id, "Redirect")

    response = client.get(f"/intelligence/ask?element={root.id}&question=impact")

    assert response.status_code == 302
    target = urlparse(response.headers["Location"])
    query = parse_qs(target.query)
    assert target.path.rstrip("/") == "/ai-chat"
    # The page it opens is the chat, and the script that runs the starter named
    # in the address is the one the page loads.
    chat_page = client.get(response.headers["Location"])
    assert chat_page.status_code == 200 and "js/ai_chat/app.js" in chat_page.get_data(as_text=True)
    script = (Path(app.root_path) / "static" / "js" / "ai_chat" / "app.js").read_text()
    assert "urlParams.get('starter')" in script and "urlParams.get('element')" in script
    # What it runs is the same stored answer the typed question gets.
    events = _stream_events(client, {
        "message": "What breaks if it fails?",
        "starter": {"question": query["starter"][0], "element_id": int(query["element"][0])},
    })
    card = events[-1]["answer_cards"][0]
    assert "Redirect Mid" in card and "Redirect Leaf" in card
    address = re.search(r'data-answer-address="([0-9a-f]{32})"', card).group(1)
    from app.modules.intelligence.services import answer_envelope as envelope

    with app.test_request_context("/"):
        g.current_org_id = org.id
        typed = envelope.ask_and_store("impact", {"element_id": root.id}, user_id=user.id)
        assert envelope.open_answer(address).citations == typed.citations
        assert envelope.open_answer(address).rows == typed.rows
