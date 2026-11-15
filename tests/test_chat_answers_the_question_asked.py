"""The chat answers the question asked, searches before it says a fact is missing, and claims only what ran.

A catalogue answer is given for the catalogue question, not for any longer question that
contains its words. A question naming business facts is searched first. A reply that says it
searched, or that nothing is missing, is backed by a lookup in the same turn. A fact the person
told the assistant earlier is marked as theirs.
"""

import json
import uuid

import pytest

from app import db
from app.models import ArchiMateElement
from tests.test_aggregate_answers import _make_app_with_cost
from tests.test_sample_company_working_data import _architect

CATALOGUE_QUESTION = "What do we run and who owns each one?"
RISK_QUESTION = "Which risks could stop our paid launch and who owns each one?"
SCENARIO = (
    "Run a scenario: if paid launch slips three months and Ifiok's AI cost is 35% of its "
    "revenue, what breaks if Ifiok fails?"
)
BOARD = (
    "Search the model and answer as our strategy adviser: what is our monthly burn, what "
    "funding are we raising and when, and what revenue goals have we set with their dates?"
)
RAISE = "Pre-seed raise of up to USD 250,000, landing April 2027"


def _ask(client, message):
    return client.post("/ai-chat/estate-answer", json={"message": message}).get_json()


def _element(org, name, kind="Goal", layer="motivation"):
    row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org.id)
    db.session.add(row)
    db.session.commit()
    return row


def test_the_exact_catalogue_question_still_gets_the_catalogue_answer(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "answer-asked-a@example.com")
    _make_app_with_cost(org.id, "Asked Ledger", annual_cost=10)
    db.session.commit()
    login_as(client, user)
    body = _ask(client, CATALOGUE_QUESTION)
    assert body["found"] is True and body["entry_id"] == "applications_and_owners"
    assert "Asked Ledger" in body["html"]


def test_a_risk_question_that_says_who_owns_each_is_not_the_application_list(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "answer-asked-b@example.com")
    _make_app_with_cost(org.id, "Unrelated Ledger", annual_cost=10)
    db.session.commit()
    login_as(client, user)
    body = _ask(client, RISK_QUESTION)
    assert body.get("entry_id") != "applications_and_owners"
    assert "Unrelated Ledger" not in body.get("html", "")


def test_a_scenario_does_not_get_the_what_breaks_card(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "answer-asked-c@example.com")
    _make_app_with_cost(org.id, "Billing Ledger", annual_cost=10)
    db.session.commit()
    login_as(client, user)
    body = _ask(client, SCENARIO)
    assert body.get("entry_id") != "what_breaks_if_fails"
    assert "What breaks" not in body.get("html", "")


def test_the_three_part_burn_question_is_not_a_card_about_the_element_named_burn(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "answer-asked-d@example.com")
    _element(org, "Monthly burn", kind="Metric", layer="motivation")
    login_as(client, user)
    body = _ask(client, BOARD)
    assert body["found"] is False
    assert "Nothing is missing" not in body.get("html", "")


def test_the_one_question_about_an_element_still_gets_its_card(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "answer-asked-e@example.com")
    _element(org, "Monthly burn", kind="Metric", layer="motivation")
    login_as(client, user)
    body = _ask(client, "What initiatives are tracking Monthly burn?")
    assert body["found"] is True and body.get("lens") == "strategy"


# --- the reply's words about searching ---------------------------------------


def _lookup(query, result, for_question=False):
    from app.modules.ai_chat.services.turn_honesty import record_lookup

    return record_lookup({"query": query}, result, for_question)


FOUND = {"success": True, "result": [{"name": "Monthly burn"}]}
NOTHING = {"success": True, "result": []}
FAILED = {"success": False, "error": "The lookup failed."}


def test_a_claim_of_a_search_is_removed_when_no_lookup_ran():
    from app.modules.ai_chat.services.turn_honesty import NO_SEARCH_NOTE, remove_unbacked_search_claims

    text = "I searched and found no such element. The launch is in April."
    out = remove_unbacked_search_claims(text, [])
    assert "searched" not in out and "found no such" not in out
    assert "The launch is in April." in out and NO_SEARCH_NOTE in out
    assert remove_unbacked_search_claims("Nothing is missing from this answer.", []).endswith(NO_SEARCH_NOTE)
    assert remove_unbacked_search_claims(text, [_lookup("launch", NOTHING)], "when is the launch") == text


def test_a_lookup_that_failed_never_backs_a_claim():
    from app.modules.ai_chat.services.turn_honesty import NO_SEARCH_NOTE, remove_unbacked_search_claims

    text = "I searched and found no such element. Nothing is missing from this answer."
    out = remove_unbacked_search_claims(text, [_lookup("burn", FAILED, True)], "what is our burn")
    assert "searched" not in out and "Nothing is missing" not in out and NO_SEARCH_NOTE in out


def test_a_lookup_about_something_else_does_not_back_a_claim():
    from app.modules.ai_chat.services.turn_honesty import NO_SEARCH_NOTE, remove_unbacked_search_claims

    text = "I searched and found no such element. Nothing is missing from this answer."
    out = remove_unbacked_search_claims(text, [_lookup("payroll", NOTHING)], "who owns the billing ledger")
    assert "searched" not in out and "Nothing is missing" not in out and NO_SEARCH_NOTE in out


def test_a_matching_lookup_that_succeeded_keeps_the_claim():
    from app.modules.ai_chat.services.turn_honesty import remove_unbacked_search_claims

    text = "I searched and found no such element named payroll."
    assert remove_unbacked_search_claims(text, [_lookup("payroll", NOTHING)], "who owns payroll") == text
    assert remove_unbacked_search_claims(text, [_lookup("burn", NOTHING, True)], "what is our burn") == text


def test_nothing_is_missing_is_kept_only_when_a_matching_lookup_found_nothing():
    from app.modules.ai_chat.services.turn_honesty import remove_unbacked_search_claims

    text = "I searched the model. Nothing is missing from this answer."
    found = remove_unbacked_search_claims(text, [_lookup("burn", FOUND, True)], "what is our burn")
    assert "searched" in found and "Nothing is missing" not in found
    assert remove_unbacked_search_claims(text, [_lookup("burn", NOTHING, True)], "what is our burn") == text


def test_looking_for_a_record_is_a_search_claim():
    from app.modules.ai_chat.services.turn_honesty import NO_SEARCH_NOTE, remove_unbacked_search_claims

    out = remove_unbacked_search_claims("I looked for it but there is no record. The launch is in April.", [])
    assert "looked for" not in out and "The launch is in April." in out and NO_SEARCH_NOTE in out


def test_a_figure_the_person_gave_earlier_is_marked_as_from_the_conversation():
    from app.modules.ai_chat.services.turn_honesty import CONVERSATION_MARK, mark_conversation_facts

    earlier = ["Assume the AI cost per generation is not measured in our model today."]
    text = "The model records that the AI cost per generation is not measured in our model today. Revenue is 5."
    evidence = ['{"result": [{"name": "Revenue"}]}']
    out = mark_conversation_facts(text, earlier, evidence)
    assert out.startswith(CONVERSATION_MARK) and out.count(CONVERSATION_MARK) == 1
    backed = mark_conversation_facts(text, earlier, evidence + [text])
    assert CONVERSATION_MARK not in backed


def test_business_fact_words_name_what_to_search():
    from app.modules.ai_chat.services.turn_honesty import fact_searches

    assert "raise" in fact_searches("what funding do we plan to raise, how much, and when?")
    assert {"burn", "revenue"} <= set(fact_searches(BOARD))
    assert fact_searches("Who leads our company?") == []


@pytest.mark.parametrize("question,name", [
    ("What does it cost us to serve one customer?", "cost to serve"),
    ("What is the cost of serving a customer?", "cost to serve"),
    ("What is our cost per customer?", "cost per"),
    ("When does the paid launch happen?", "date"),
    ("By when must the money land?", "date"),
    ("What date do we open the round?", "date"),
])
def test_business_facts_are_found_by_the_words_a_person_uses(question, name):
    from app.modules.ai_chat.services.turn_honesty import fact_searches

    assert name in fact_searches(question)


# --- the streamed turn: the lookup runs, the footer and the words agree -----------


def _stream(client, message, replies, monkeypatch):
    from app.modules.ai_chat.services import agent_runner as ar
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    chain = [("openrouter", "test/model", "key", None)]
    monkeypatch.setattr(LLMService, "_get_configured_provider", staticmethod(lambda *a, **k: chain[0][:2]))
    monkeypatch.setattr(LLMService, "_get_all_api_keys", staticmethod(lambda p: ["key"]))
    monkeypatch.setattr(ar, "build_provider_chain", lambda *a, **k: list(chain))
    seen = []

    def fake(self, provider, model, api_key, system_prompt, messages, tools, **k):
        seen.append(messages[-1]["content"])
        return {"text": replies.pop(0), "tool_calls": [], "raw": None}

    monkeypatch.setattr(ar.AgentRunner, "_call_llm", fake)
    monkeypatch.setattr(ar.AgentRunner, "_record_run", lambda *a, **k: None)
    monkeypatch.setattr(ar.AgentRunner, "_gate_mode", staticmethod(lambda: "off"))
    response = client.post("/ai-chat/message/stream", json={"message": message, "domain": "general"})
    events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith("data: ")]
    return events, seen


def test_a_funding_question_runs_a_lookup_and_the_model_is_given_the_goal(client, login_as, make_org, monkeypatch):
    org = make_org()
    user = _architect(org, "answer-asked-f@example.com")
    _element(org, RAISE)
    login_as(client, user)
    events, seen = _stream(
        client, "what funding do we plan to raise, how much, and when?", ["The plan is " + RAISE + "."], monkeypatch)
    started = [e for e in events if e["type"] == "tool_start"]
    assert started and all(e["tool"] == "search_elements" for e in started)
    assert any(e["type"] == "tool_result" and RAISE in json.dumps(e["result"]) for e in events)
    assert RAISE in seen[0]
    done = [e for e in events if e["type"] == "done"][0]
    assert RAISE in done["response"]


def test_a_reply_that_says_it_searched_when_nothing_ran_loses_the_claim(client, login_as, make_org, monkeypatch):
    org = make_org()
    user = _architect(org, "answer-asked-g@example.com")
    login_as(client, user)
    events, _seen = _stream(
        client, "Who leads our company?", ["I searched and found no such record. Ask your administrator."], monkeypatch)
    assert not [e for e in events if e["type"] == "tool_start"]
    done = [e for e in events if e["type"] == "done"][0]
    assert "searched" not in done["response"] and "found no such" not in done["response"]
    assert "No search ran" in done["response"]
    # Nothing is streamed as the model wrote it: the person reads only the corrected reply.
    assert not [e for e in events if e["type"] == "token" and "searched" in json.dumps(e)]


def test_a_reply_that_says_it_searched_is_kept_when_the_trail_has_a_lookup(client, login_as, make_org, monkeypatch):
    org = make_org()
    user = _architect(org, "answer-asked-h@example.com")
    login_as(client, user)
    events, _seen = _stream(
        client, "what is our monthly burn?", ["I searched and found no such element named burn."], monkeypatch)
    assert [e for e in events if e["type"] == "tool_start"]
    done = [e for e in events if e["type"] == "done"][0]
    assert "I searched" in done["response"]


# --- a claim is backed only by a lookup about the fact it is about ---------------


def test_nothing_is_missing_needs_a_lookup_for_each_fact_the_question_asked():
    from app.modules.ai_chat.services.turn_honesty import NO_SEARCH_NOTE, PARTIAL_SEARCH_NOTE, remove_unbacked_search_claims

    text = "I searched and found no such element. Nothing is missing from this answer."
    out = remove_unbacked_search_claims(text, [_lookup("revenue", NOTHING, True)], SCENARIO)
    assert "Nothing is missing" not in out and PARTIAL_SEARCH_NOTE in out and NO_SEARCH_NOTE not in out
    assert "I searched and found no such element." in out


def test_a_claim_about_owners_is_not_backed_by_a_revenue_lookup():
    from app.modules.ai_chat.services.turn_honesty import PARTIAL_SEARCH_NOTE, remove_unbacked_search_claims

    text = "I searched and found no such owner."
    out = remove_unbacked_search_claims(text, [_lookup("revenue", NOTHING, True)], "what is our revenue and who owns it")
    assert "found no such owner" not in out and PARTIAL_SEARCH_NOTE in out
    assert remove_unbacked_search_claims(text, [_lookup("owner", NOTHING)], "who owns it") == text


def test_nothing_is_missing_is_kept_when_every_fact_asked_was_looked_up_and_found_nothing():
    from app.modules.ai_chat.services.turn_honesty import remove_unbacked_search_claims

    text = "Nothing is missing from this answer."
    question = "what is our monthly burn and our revenue?"
    both = [_lookup("burn", NOTHING, True), _lookup("revenue", NOTHING, True)]
    assert remove_unbacked_search_claims(text, both, question) == text
    assert "Nothing is missing" not in remove_unbacked_search_claims(text, both[:1], question)


def test_could_not_find_any_record_is_removed_when_no_lookup_ran():
    from app.modules.ai_chat.services.turn_honesty import NO_SEARCH_NOTE, remove_unbacked_search_claims

    out = remove_unbacked_search_claims("I could not find any record of that. The launch is in April.", [])
    assert "could not find" not in out and "The launch is in April." in out and NO_SEARCH_NOTE in out
    kept = "I could not find any record of that."
    assert remove_unbacked_search_claims(kept, [_lookup("launch", NOTHING, True)], "when is the launch") == kept


def test_what_each_customer_costs_us_runs_a_cost_search():
    from app.modules.ai_chat.services.turn_honesty import fact_searches

    assert "cost to serve" in fact_searches("What does each customer cost us?")
    assert "cost to serve" not in fact_searches("How much will the migration cost us?")


@pytest.mark.parametrize("question", ["Is our data up to date?", "When is it a good idea to retire an app?"])
def test_a_general_question_with_date_words_runs_no_date_search(question):
    from app.modules.ai_chat.services.turn_honesty import fact_searches

    assert fact_searches(question) == []


def test_a_question_started_lookup_reads_only_the_organisation_asking(app, make_org, tenant_ctx, db_session):
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.turn_honesty import remove_unbacked_search_claims

    org_a, org_b = make_org("lookup-a"), make_org("lookup-b")
    db_session.add(ArchiMateElement(name="Revenue goal of org B", type="Goal", layer="motivation", organization_id=org_b.id))
    db_session.flush()
    runner = AgentRunner.__new__(AgentRunner)
    text = "I searched the model. Nothing is missing from this answer."
    with tenant_ctx(org_a.id):
        seen_a = runner._search_model("revenue", 10)
    with tenant_ctx(org_b.id):
        seen_b = runner._search_model("revenue", 10)
    assert seen_a["success"] and seen_a["result"] == []
    assert [r["name"] for r in seen_b["result"]] == ["Revenue goal of org B"]
    from app.modules.ai_chat.services.turn_honesty import record_lookup

    look_a = record_lookup({"query": "revenue"}, seen_a, True)
    look_b = record_lookup({"query": "revenue"}, seen_b, True)
    assert look_a.found_nothing and not look_b.found_nothing
    assert remove_unbacked_search_claims(text, [look_a], "what is our revenue") == text
    assert "Nothing is missing" not in remove_unbacked_search_claims(text, [look_b], "what is our revenue")
