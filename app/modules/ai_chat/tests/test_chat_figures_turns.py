"""A whole assistant turn: figures reach a person only as the engine's rendering.

A scripted model writes references (or digits, or neither) and the turn is run through
the real runner, tools and gate, in organisations with real, different figures.

Fixtures (app, db_session, make_org) come from app/modules/conftest.py.
"""

from __future__ import annotations

import datetime as _real_datetime
import re
import types

import pytest

from app.modules.ai_chat.services import chat_figures as cf
from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.tests.test_answer_tool_and_citation_gate import (
    _call_answer,
    _element,
    _enforce_mode,  # noqa: F401 - the module's autouse fixture, reused here
    _estate,
    _link,
    _run_turn,
    _user,
)

NOT_CONFIRMED = cf.PLAIN_LINE


def _applications(db_session, org_id, count, prefix="App"):
    from app.models.application_portfolio import ApplicationComponent

    rows = []
    for index in range(count):
        row = ApplicationComponent(name="%s %02d" % (prefix, index + 1), organization_id=org_id)
        db_session.add(row)
        rows.append(row)
    db_session.flush()
    return rows


def _key_in(messages, field):
    """The key of the figure a tool result shows for *field* ("total", "annual_cost" ...): what
    a model that reads the results copies."""
    blob = " ".join(str(m.get("content")) for m in messages)
    found = re.findall(r"'%s': \{'fig': '(F\d+)'" % field, blob) or re.findall(
        r'"%s": \{"fig": "(F\d+)"' % field, blob)
    assert found, (field, blob[-600:])
    return found[-1]


def _summary_key(messages, pattern):
    """The key the organisation summary line gives the figure whose phrase matches *pattern*."""
    blob = " ".join(str(m.get("content")) for m in messages)
    found = re.findall(r'\[fig:(F\d+)\] reads "%s' % pattern, blob)
    assert found, (pattern, blob[:800])
    return found[0]


# --------------------------------------------------------------------------- #
# A turn with no tool call                                                     #
# --------------------------------------------------------------------------- #


def test_a_turn_without_a_tool_call_refuses_a_written_count(app, db_session, make_org, monkeypatch):
    org_a, org_b = make_org("fig-notool-a"), make_org("fig-notool-b")
    user = _user(db_session, org_a.id)
    _applications(db_session, org_a.id, 3, "Alpha")
    _applications(db_session, org_b.id, 5, "Beta")
    db_session.commit()
    wrong = {"text": "You have 999 applications."}

    result, _e, _r, seen = _run_turn(
        app, monkeypatch, [wrong, wrong], user=user, message="How many applications do we have?"
    )

    assert len(seen) == 2, "one revision"
    assert "999" not in result["response"]
    assert "5 applications" not in result["response"], "the other organisation's count never appears"
    assert result["response"].startswith(NOT_CONFIRMED)
    assert "3 applications" in result["response"], "the true count is restored from the summary"


def test_a_turn_without_a_tool_call_renders_the_summary_reference(app, db_session, make_org, monkeypatch):
    org_a, org_b = make_org("fig-summary-a"), make_org("fig-summary-b")
    user_a, user_b = _user(db_session, org_a.id), _user(db_session, org_b.id)
    _applications(db_session, org_a.id, 3, "Alpha")
    _applications(db_session, org_b.id, 5, "Beta")
    db_session.commit()

    def says(messages):
        key = _summary_key(messages, "[0-9]+ application")
        return {"text": "You have [fig:%s] in the register." % key}

    result_a, *_ = _run_turn(app, monkeypatch, [says], user=user_a, message="How many applications do we have?")
    result_b, *_ = _run_turn(app, monkeypatch, [says], user=user_b, message="How many applications do we have?")

    assert result_a["response"] == "You have 3 applications in the register."
    assert result_b["response"] == "You have 5 applications in the register."
    assert "[fig:" not in result_a["response"] + result_b["response"]
    assert result_a["figures"][0]["value"] == 3 and result_a["figures"][0]["tool"] == "organisation_summary"
    assert result_a["figure_check"]["prompt_version"] == "figures-v1"


def test_the_no_tool_fallback_shows_the_summary_figures(app, db_session, make_org, monkeypatch):
    org = make_org("fig-notool-block")
    user = _user(db_session, org.id)
    _applications(db_session, org.id, 4, "Gamma")
    db_session.commit()
    wrong = {"text": "Roughly 70 applications, I would say."}

    result, *_ = _run_turn(app, monkeypatch, [wrong, wrong], user=user, message="Give me a one-line summary of our estate.")

    lines = result["response"].split("\n\n")
    assert lines[0] == NOT_CONFIRMED
    assert lines[1].startswith(cf.RECORDS_HEADING)
    assert "4 applications" in lines[1] and "70" not in result["response"]
    assert lines[-1] == cf.NEXT_STEP


# --------------------------------------------------------------------------- #
# Revision and fallback                                                        #
# --------------------------------------------------------------------------- #


def test_one_revision_is_sent_with_each_violation_named(app, db_session, make_org, monkeypatch):
    org = make_org("fig-revise")
    user = _user(db_session, org.id)
    root, mid, leaf = _estate(db_session, org.id, "Rev")
    wrong = "Rev Mid [ref:%d] is hit, 14 in all, and [fig:F99] too." % mid.id
    right = lambda messages: {"text": "Rev Mid [ref:%d] is hit, [fig:%s] in all." % (mid.id, _key_in(messages, "total"))}  # noqa: E731
    script = [_call_answer(root.id), {"text": wrong}, right]

    result, _e, _r, seen = _run_turn(app, monkeypatch, script, user=user, message="what breaks if Rev Root fails?")

    sent = str(seen[2][-1]["content"])
    assert sent.startswith("Your answer cannot be shown as written. Fix each point and send the whole answer again:")
    assert '"14" in "Rev Mid [ref:%d] is hit, 14 in all, and [fig:F99] too." is written as text.' % mid.id in sent
    assert "Use its reference from the tool results, or leave it out." in sent
    assert "[fig:F99] does not exist in this turn." in sent
    assert len(seen) == 3, "exactly one revision"
    assert result["response"] == "Rev Mid [ref:%d] is hit, 2 connections downstream in all." % mid.id
    assert result["figure_check"]["revision_ran"] is True and result["figure_check"]["revision_fixed"] is True
    assert result["figure_check"]["first_draft_violations"] == {"written_number": 1, "unknown_reference": 1}


def test_a_revision_that_calls_a_tool_can_fix_a_no_tool_draft(app, db_session, make_org, monkeypatch):
    org = make_org("fig-revise-tool")
    user = _user(db_session, org.id)
    first = _applications(db_session, org.id, 3, "Delta")[0]
    db_session.commit()
    call = {"tool_calls": [{"id": "t9", "name": "find_applications", "arguments": {}}]}
    final = lambda messages: {"text": "Delta 01 [ref:%d] is one of [fig:%s]." % (first.id, _key_in(messages, "total"))}  # noqa: E731
    script = [{"text": "You have 999 applications."}, call, final]

    result, _e, _r, seen = _run_turn(app, monkeypatch, script, user=user, message="How many applications do we have?")

    assert "No tool was called in this turn." in str(seen[1][-1]["content"])
    assert "You may also call a tool." in str(seen[1][-1]["content"])
    assert result["response"] == "Delta 01 [ref:%d] is one of 3 applications match this search." % first.id
    assert "999" not in result["response"]


def test_a_draft_that_ignores_the_contract_twice_shows_the_records_block(app, db_session, make_org, monkeypatch):
    org = make_org("fig-ignore")
    user = _user(db_session, org.id)
    root, mid, leaf = _estate(db_session, org.id, "Ign")
    wrong = "Ign Root [ref:%d] reaches 2 connections downstream." % root.id
    script = [_call_answer(root.id), {"text": wrong}, {"text": wrong}]

    result, _e, _r, seen = _run_turn(app, monkeypatch, script, user=user, message="what breaks if Ign Root fails?")

    assert len(seen) == 3
    parts = result["response"].split("\n\n")
    assert parts[0] == NOT_CONFIRMED
    assert parts[1].startswith(cf.RECORDS_HEADING)
    assert "- Ign Root [ref:%d]: 2 connections downstream" % root.id in parts[1]
    assert parts[2] == cf.NEXT_STEP
    assert "2 connections" in result["response"] and "reaches 2" not in result["response"]
    assert result["figure_check"]["fallback"]["clauses_removed"] >= 1
    assert result["citation_gate"]["refused"] is False


def test_the_records_block_leaves_out_figures_already_shown(app, db_session, make_org, monkeypatch):
    org = make_org("fig-block")
    user = _user(db_session, org.id)
    root, mid, leaf = _estate(db_session, org.id, "Blk")

    def draft(messages):
        key = _key_in(messages, "total")
        return {"text": "Blk Root [ref:%d] has [fig:%s]; it also has 14 owners, which is odd." % (root.id, key)}

    script = [_call_answer(root.id), draft, draft]
    result, *_ = _run_turn(app, monkeypatch, script, user=user, message="what breaks if Blk Root fails?")

    assert "2 connections downstream" in result["response"]
    assert result["response"].count("2 connections downstream") == 1, "already shown, so not repeated"
    assert "14" not in result["response"]
    assert cf.RECORDS_HEADING not in result["response"]


def test_the_revision_is_skipped_when_the_turn_budget_is_nearly_spent(app, db_session, make_org, monkeypatch):
    org = make_org("fig-budget")
    user = _user(db_session, org.id)
    _applications(db_session, org.id, 2, "Eps")
    db_session.commit()
    monkeypatch.setattr(AgentRunner, "_budget_left", lambda self: 5.0)
    wrong = {"text": "You have 999 applications."}

    result, _e, _r, seen = _run_turn(app, monkeypatch, [wrong], user=user, message="How many applications do we have?")

    assert len(seen) == 1, "no second model call"
    assert result["response"].startswith(NOT_CONFIRMED)
    assert "2 applications" in result["response"]
    assert result["figure_check"]["revision_ran"] is False


def test_the_existing_nudge_and_a_figure_violation_go_in_one_message(app, db_session, make_org, monkeypatch):
    org = make_org("fig-nudge")
    user = _user(db_session, org.id)
    root, mid, leaf = _estate(db_session, org.id, "Ndg")
    script = [{"text": "Ndg Root has 14 connections."}, _call_answer(root.id),
              lambda messages: {"text": "Ndg Root [ref:%d] has [fig:%s]." % (root.id, _key_in(messages, "total"))}]

    result, _e, _r, seen = _run_turn(app, monkeypatch, script, user=user, message="what breaks if Ndg Root fails?")

    message = str(seen[1][-1]["content"])
    assert "call the answer tool for this question" in message and "Your answer cannot be shown as written" in message
    assert result["response"] == "Ndg Root [ref:%d] has 2 connections downstream." % root.id


# --------------------------------------------------------------------------- #
# Kept cases, under references                                                 #
# --------------------------------------------------------------------------- #


def test_one_clause_out_keeps_the_rest_of_the_sentence_and_adds_the_plain_line(app, db_session, make_org, monkeypatch):
    org = make_org("fig-clause")
    user = _user(db_session, org.id)
    root, mid, leaf = _estate(db_session, org.id, "Cls")
    wrong = "Cls Mid [ref:%d] is impacted; about 14 more follow behind it." % mid.id
    script = [_call_answer(root.id), {"text": wrong}, {"text": wrong}]

    result, *_ = _run_turn(app, monkeypatch, script, user=user, message="what breaks if Cls Root fails?")

    assert result["response"].startswith("Cls Mid [ref:%d] is impacted." % mid.id)
    assert not re.search(r"(?<![\w:])14(?![\w])", result["response"]) and NOT_CONFIRMED in result["response"]


def test_a_figure_in_brackets_is_taken_out_and_the_sentence_stays(app, db_session, make_org, monkeypatch):
    org = make_org("fig-bracket")
    user = _user(db_session, org.id)
    root, mid, leaf = _estate(db_session, org.id, "Brk")
    wrong = "Brk Mid [ref:%d] is impacted (14 in total) by the failure." % mid.id
    script = [_call_answer(root.id), {"text": wrong}, {"text": wrong}]

    result, *_ = _run_turn(app, monkeypatch, script, user=user, message="what breaks if Brk Root fails?")

    assert result["response"].startswith("Brk Mid [ref:%d] is impacted by the failure." % mid.id)
    assert "14" not in result["response"]


# --------------------------------------------------------------------------- #
# Organisations                                                                #
# --------------------------------------------------------------------------- #


def test_a_turn_for_one_organisation_never_shows_the_others_count(app, db_session, make_org, monkeypatch):
    """Two organisations whose estates have near-identical names and different figures: A's
    root has 14 dependants, B's has 2. B's turn that writes A's count is refused."""
    org_a, org_b = make_org("fig-twin-a"), make_org("fig-twin-b")
    user_a, user_b = _user(db_session, org_a.id), _user(db_session, org_b.id)
    root_a = _element(db_session, org_a.id, "Twin Hub")
    for index in range(14):
        _link(db_session, org_a.id, root_a, _element(db_session, org_a.id, "Twin Dep %02d" % index))
    root_b, _mid, _leaf = _estate(db_session, org_b.id, "Twin")
    root_b.name = "Twin Hub"
    db_session.commit()

    def right(root):
        return lambda messages: {"text": "Twin Hub [ref:%d] has [fig:%s]." % (root.id, _key_in(messages, "total"))}

    result_a, *_ = _run_turn(app, monkeypatch, [_call_answer(root_a.id), right(root_a)], user=user_a,
                             message="what breaks if Twin Hub fails?")
    result_b, *_ = _run_turn(app, monkeypatch, [_call_answer(root_b.id), right(root_b)], user=user_b,
                             message="what breaks if Twin Hub fails?")
    wrong_b = {"text": "Twin Hub [ref:%d] has 14 connections downstream." % root_b.id}
    refused_b, *_ = _run_turn(app, monkeypatch, [_call_answer(root_b.id), wrong_b, wrong_b], user=user_b,
                              message="what breaks if Twin Hub fails?")

    assert "14 connections downstream" in result_a["response"]
    assert "2 connections downstream" in result_b["response"] and "14" not in result_b["response"]
    assert "14" not in refused_b["response"] and "2 connections downstream" in refused_b["response"]


def test_public_catalogue_figures_render_as_the_catalogue(app, db_session, make_org, monkeypatch):
    org = make_org("fig-public")
    user = _user(db_session, org.id)
    db_session.commit()

    def says(messages):
        key = _summary_key(messages, "[0-9]+ vendors? in Entelim's public catalogue")
        return {"text": "There are [fig:%s]." % key}

    result, *_ = _run_turn(app, monkeypatch, [says], user=user, message="How many vendors are there?")

    assert re.fullmatch(r"There are \d+ vendors? in Entelim's public catalogue\.", result["response"])
    figure = result["figures"][0]
    assert figure["tool"] == "organisation_summary" and figure["concept"] == "vendor-catalogue"


# --------------------------------------------------------------------------- #
# Modes and delivery                                                           #
# --------------------------------------------------------------------------- #


def test_log_mode_records_but_keeps_written_numbers_and_still_resolves(app, db_session, make_org, monkeypatch):
    org = make_org("fig-log")
    user = _user(db_session, org.id)
    _applications(db_session, org.id, 3, "Zeta")
    db_session.commit()
    monkeypatch.setattr(AgentRunner, "_gate_mode", staticmethod(lambda: "log"))

    def says(messages):
        key = _summary_key(messages, "[0-9]+ application")
        return {"text": "You have [fig:%s] and about 14 others. Also [fig:F99] is odd." % key}

    result, _e, _r, seen = _run_turn(app, monkeypatch, [says], user=user, message="How many applications do we have?")

    assert len(seen) == 1, "log mode never sends a revision"
    assert "3 applications and about 14 others" in result["response"], "a written number is kept in log mode"
    assert "[fig:" not in result["response"], "a raw reference is never shown, in any mode"
    assert result["figure_check"]["mode"] == "log"
    assert result["figure_check"]["first_draft_violations"] == {"unknown_reference": 1, "written_number": 1}


def test_off_mode_still_resolves_and_records_nothing(app, db_session, make_org, monkeypatch):
    org = make_org("fig-off")
    user = _user(db_session, org.id)
    _applications(db_session, org.id, 3, "Eta")
    db_session.commit()
    monkeypatch.setattr(AgentRunner, "_gate_mode", staticmethod(lambda: "off"))
    says = lambda messages: {"text": "You have [fig:%s]." % _summary_key(messages, "[0-9]+ application")}  # noqa: E731

    result, *_ = _run_turn(app, monkeypatch, [says], user=user, message="How many applications do we have?")

    assert result["response"] == "You have 3 applications."
    assert "figure_check" not in result and "citation_gate" not in result


def test_no_model_text_reaches_the_stream_before_the_gate(app, db_session, make_org, monkeypatch):
    org = make_org("fig-stream")
    user = _user(db_session, org.id)
    _applications(db_session, org.id, 3, "Theta")
    db_session.commit()
    says = lambda messages: {"text": "You have [fig:%s] and 999 more." % _summary_key(messages, "[0-9]+ application")}  # noqa: E731

    result, events, _r, _seen = _run_turn(
        app, monkeypatch, [says, says], user=user, stream=True, message="How many applications do we have?",
    )

    assert [e for e in events if e.get("type") == "token"] == [], "no model token is streamed"
    assert "999" not in result["response"] and "3 applications" in result["response"]


def test_the_contract_and_its_version_are_sent_in_every_mode():
    from app.modules.ai_chat.services.agent_runner import _AGENT_PREFIX, FIGURES_CONTRACT, FIGURES_PROMPT_VERSION

    assert FIGURES_PROMPT_VERSION == "figures-v1"
    assert FIGURES_CONTRACT in _AGENT_PREFIX
    assert FIGURES_CONTRACT.startswith("Figures. You never write a figure yourself.")
    assert "[fig:F3]" in FIGURES_CONTRACT and "ISO 27001, TOGAF 9.2, TLS 1.3, Java 17" in FIGURES_CONTRACT


# --------------------------------------------------------------------------- #
# The turn does not depend on the clock                                        #
# --------------------------------------------------------------------------- #


def _freeze_answer_clock(monkeypatch, moment):
    from app.modules.intelligence.services import answer_envelope as envelope

    class Frozen(_real_datetime.datetime):
        @classmethod
        def utcnow(cls):
            return moment

    shim = types.SimpleNamespace(**{k: getattr(_real_datetime, k) for k in dir(_real_datetime) if not k.startswith("__")})
    shim.datetime = Frozen
    monkeypatch.setattr(envelope, "_dt", shim)


MOMENTS = [
    _real_datetime.datetime(2026, 11, 10, 3, 14, 0),
    _real_datetime.datetime(2026, 11, 21, 11, 10, 52, 552000),
]


@pytest.mark.parametrize("moment", MOMENTS, ids=["minute-14", "21-11-10-52-55"])
def test_wrong_counts_are_removed_whatever_the_clock_says(app, db_session, make_org, monkeypatch, moment):
    _freeze_answer_clock(monkeypatch, moment)
    org_a, org_b = make_org("fig-clock-a"), make_org("fig-clock-b")
    user = _user(db_session, org_a.id)
    root, mid, leaf = _estate(db_session, org_a.id, "Clk")
    _estate(db_session, org_b.id, "Other")
    db_session.commit()
    wrong = (
        "Clk Mid [ref:%d] and Clk Leaf [ref:%d] are impacted. "
        "That is 21 connections in all. Elsewhere 55 depend on them. About 52 more follow. "
        "Another 11 sit behind them. A further 10 do too. 14 is the total." % (mid.id, leaf.id)
    )
    script = [_call_answer(root.id), {"text": wrong}, {"text": wrong}]

    result, *_ = _run_turn(app, monkeypatch, script, user=user, message="what breaks if Clk Root fails?")

    body = result["response"].replace("[ref:%d]" % mid.id, "").replace("[ref:%d]" % leaf.id, "")
    for count in ("21", "55", "52", "11", "10", "14"):
        assert not re.search(r"(?<![\w.])%s(?![\w])" % count, body), count
    assert "Clk Mid [ref:%d]" % mid.id in result["response"]
    assert "2 connections downstream" in result["response"], "the true count comes back from the records"


# --------------------------------------------------------------------------- #
# A record cited in a streamed answer links to its own page                    #
# --------------------------------------------------------------------------- #


def test_a_record_cited_in_a_streamed_answer_links_to_its_own_page(app, db_session, make_org):
    """The streaming turn has an application context and no request: the link is still the
    record's page, never a stand-in the browser has to guess."""
    from flask import has_request_context

    org = make_org("fig-link")
    row = _applications(db_session, org.id, 1, "Link")[0]
    db_session.commit()
    assert not has_request_context()
    url = AgentRunner._source_url("application", {"id": row.id, "name": row.name})
    assert url and url.endswith("/%d" % row.id), url
    assert AgentRunner._source_url("capability", {"id": 4})


# --------------------------------------------------------------------------- #
# Evidence: the envelope, the event and the audit record                       #
# --------------------------------------------------------------------------- #


def test_each_figure_in_the_envelope_names_its_source_and_where_it_sits_in_the_delivered_text(
    app, db_session, make_org, monkeypatch
):
    org = make_org("fig-envelope")
    user = _user(db_session, org.id)
    root, mid, leaf = _estate(db_session, org.id, "Env")

    def says(messages):
        return {"text": "Env Root [ref:%d] has [fig:%s]." % (root.id, _key_in(messages, "total"))}

    result, *_ = _run_turn(app, monkeypatch, [_call_answer(root.id), says], user=user,
                           message="what breaks if Env Root fails?")
    figures = result["figures"]
    assert len(figures) == 1
    figure = figures[0]
    assert result["response"][figure["start"]:figure["end"]] == "2 connections downstream" == figure["reads"]
    assert (figure["tool"], figure["field"], figure["kind"], figure["value"]) == (
        "answer", "result.summary.total", "count", 2)
    assert str(figure["record_id"]) == str(root.id) and figure["concept"] == "impact-answer"
    assert figure["as_of"], "the answer's own computed-at travels with the figure"
    check = result["figure_check"]
    assert check["mode"] == "enforce" and check["prompt_version"] == "figures-v1"
    assert check["references"] == {"written": 1, "resolved": 1, "unresolved": 0}


def test_every_figure_offset_in_a_fallback_answer_points_at_its_own_text(app, db_session, make_org, monkeypatch):
    org = make_org("fig-offsets")
    user = _user(db_session, org.id)
    root, mid, leaf = _estate(db_session, org.id, "Off")
    wrong = "Off Root [ref:%d] reaches 2 connections downstream, and [fig:F1] too." % root.id
    result, *_ = _run_turn(app, monkeypatch, [_call_answer(root.id), {"text": wrong}, {"text": wrong}], user=user,
                           message="what breaks if Off Root fails?")
    assert result["figures"], "the records block figures are in the envelope"
    for figure in result["figures"]:
        assert result["response"][figure["start"]:figure["end"]] == figure["reads"], figure


def test_the_turn_logs_one_figure_event_and_keeps_the_spans_for_the_run_record(
    app, db_session, make_org, monkeypatch, caplog
):
    import logging

    org = make_org("fig-event")
    user = _user(db_session, org.id)
    _applications(db_session, org.id, 3, "Iota")
    db_session.commit()
    wrong = {"text": "You have 999 applications."}
    with caplog.at_level(logging.INFO):
        result, _e, runner, _seen = _run_turn(app, monkeypatch, [wrong, wrong], user=user,
                                              message="How many applications do we have?")
    events = [r for r in caplog.records if r.getMessage().startswith("chat.figure_check ")]
    assert len(events) == 1
    event = events[0].figure_event
    assert event["organization_id"] == org.id and event["prompt_version"] == "figures-v1"
    assert event["mode"] == "enforce" and event["revision_ran"] is True and event["fallback"] is True
    assert event["written_number_violations"] == 1 and event["first_draft_violations"] == {"written_number": 1}
    assert "annotation_tokens" in event and "conversation_id" in event
    assert "999" not in events[0].getMessage(), "the violating span never reaches the application log"
    audit = runner._figure_audit["violations"]
    assert audit[0]["span"] == "999" and audit[0]["before"] == "You have"
    assert result["response"].startswith(NOT_CONFIRMED)
