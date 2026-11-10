"""The figures gold set: 27 questions, four scripted behaviours and a stale-key run, no true figure lost, no wrong one shown.

The replay runner (``app.modules.intelligence.services.gold_replay``) is the one runner; this file
runs it and checks that the frozen expectations are the figures the estates really yield.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from app.modules.intelligence.services import gold_replay

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))


def test_the_set_has_twenty_seven_questions_with_frozen_expectations_for_both_organisations():
    document, fixture = gold_replay.load_set("figures")
    questions = document["questions"]
    assert [q["id"] for q in questions] == list(range(1, 28))
    for question in questions:
        for key in ("expected", "forbidden"):
            assert set(question[key]) == {"A", "B"}, (question["id"], key)
        assert question["compliant"] and question["adversarial"]["kind"]
    asked = {q["ask"] for q in questions}
    assert "What breaks if Orders Hub fails?" in asked and "Why is that so high?" in asked
    assert "Which breaks more if it fails, Orders Hub or Billing?" in asked
    last = questions[-1]
    assert [step["args"]["element_id"] for step in last["plan"]] == ["@e:Orders Hub", "@e:Billing"]
    assert last["adversarial"]["kind"] == "top_level_swap"
    assert set(fixture.EXPECTED) == {"A", "B"}


def test_gold_values_are_the_estate_values(app, db_session, make_org):
    """A change to the records that moves a frozen value fails here, loudly."""
    from flask import g

    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.modules.ai_chat.tests.test_answer_tool_and_citation_gate import _call, _user
    from app.services.element_ownership_service import application_is_owned

    _document, fixture = gold_replay.load_set("figures")
    for variant in ("A", "B"):
        org = make_org("gold-values-" + variant.lower())
        user = _user(db_session, org.id)
        ids = fixture.build_estate(org.id, variant)
        db_session.commit()
        want = fixture.EXPECTED[variant]
        with app.test_request_context("/"):
            g.current_org_id = org.id
            apps = ApplicationComponent.query.all()
            assert len(apps) == want["applications"], variant
            assert sum(1 for a in apps if not application_is_owned(a)) == want["unowned"], variant
            assert sum(1 for a in apps if a.lifecycle_status == "retired") == want["retired"], variant
            assert BusinessCapability.query.count() == want["capabilities"], variant
        for name, key in (("Orders Hub", "orders_hub"), ("Billing", "billing"), ("Phase 21 Rollout", "phase_21")):
            out = _call(app, user, org.id, "answer", {"question": "impact", "element_id": ids["elements"][name]})
            assert out["result"]["summary"]["total"] == want[key], (variant, name)
        for name, cost in want["cost"].items():
            row = ApplicationComponent.query.filter_by(name=name, organization_id=org.id).one()
            assert (None if row.total_cost_of_ownership is None else float(row.total_cost_of_ownership)) == (
                None if cost is None else float(cost)), (variant, name)


def test_the_scoring_catches_a_wrong_figure_a_lost_one_and_a_forbidden_one():
    figures = [{"start": 4, "end": 7, "reads": "13 connections downstream"}]
    assert gold_replay.rescan("Has 13 here and 21 there.", figures[:0], [], "q") == ["13", "21"]
    assert gold_replay.rescan("Has 13 here.", [{"start": 4, "end": 6}], [], "q") == []
    assert gold_replay.rescan("There are twelve of them.", [], [], "q") == ["twelve"]
    assert gold_replay.rescan("Phase 21 Rollout has two.", [], ["Phase 21 Rollout"], "q") == ["two"]
    assert gold_replay.rescan("Your £2m budget.", [], [], "We have a £2m budget.") == []
    assert gold_replay._forbidden_hits("Billing [ref:21] has 21 links.", ["21"]) == ["21"]
    assert gold_replay._forbidden_hits("Billing [ref:21] has links.", ["21"]) == []
    assert gold_replay._forbidden_hits("It is due in last week's run.", ["last week"]) == ["last week"]


def test_the_replay_runs_all_one_hundred_and_nine_runs_with_nothing_lost_and_nothing_wrong():
    done = subprocess.run(
        [sys.executable, "-m", "app.modules.intelligence.services.gold_replay", "--set", "figures", "--json"],
        cwd=ROOT, capture_output=True, text=True, timeout=2400, env={**os.environ, "PYTHONPATH": ROOT},
    )
    body = done.stdout[done.stdout.index('{\n "summary"'):]
    report = json.loads(body)
    assert done.returncode == 0, report["failed"]
    assert sorted(report["summary"]) == ["adversarial", "compliant", "ignores", "ignores_uncited", "stale_key"]
    for behaviour, row in report["summary"].items():
        assert row["runs"] == (1 if behaviour == "stale_key" else 27), behaviour
        assert row["true_figures_lost"] == 0 and row["wrong_figures_shown"] == 0, (behaviour, row)
    assert sum(row["runs"] for row in report["summary"].values()) == 109
    assert report["summary"]["compliant"]["fallback_rate"] == 0.0, "a compliant model is never sent back"
    assert report["failed"] == []


def test_a_wrong_figure_is_judged_against_the_record_the_sentence_names():
    """A hand-worked swap: Orders Hub's true count stated beside Billing is wrong, though 13 connections
    downstream is a true figure of the estate."""
    names = ["Orders Hub", "Billing"]
    truth = {"org": {"20 applications"},
             "by_record": {"orders hub": {"13 connections downstream"}, "billing": {"4 connections downstream"}}}
    def at(text, phrase, reads=None, nth=0):
        start = -1
        for _ in range(nth + 1):
            start = text.index(phrase, start + 1)
        return {"start": start, "end": start + len(phrase), "reads": reads or phrase}

    swapped = "Billing has 13 connections downstream. Orders Hub has 4 connections downstream."
    figures = [at(swapped, "13 connections downstream"), at(swapped, "4 connections downstream")]
    wrong = gold_replay.record_mismatches(swapped, figures, names, truth)
    assert wrong == ["13 connections downstream is not a figure of billing",
                     "4 connections downstream is not a figure of orders hub"]
    right = "Orders Hub has 13 connections downstream. Billing has 4 connections downstream."
    own = [at(right, "13 connections downstream"), at(right, "4 connections downstream")]
    assert gold_replay.record_mismatches(right, own, names, truth) == []
    # A sentence naming several records is judged against the record in brackets; one naming
    # none, against the organisation.
    both = "Orders Hub and Billing differ: 13 connections downstream (Orders Hub)."
    assert gold_replay.record_mismatches(
        both, [at(both, "13 connections downstream", "13 connections downstream (Orders Hub)")], names, truth) == []
    nothing = "Nothing is named: 13 connections downstream."
    assert gold_replay.record_mismatches(nothing, [at(nothing, "13 connections downstream")], names, truth) == [
        "no record named beside: 13 connections downstream"]
    overall = "Overall, 20 applications."
    assert gold_replay.record_mismatches(overall, [at(overall, "20 applications")], names, truth) == []


def test_the_fourth_behaviour_names_each_record_and_writes_no_reference():
    document, _fixture = gold_replay.load_set("figures")
    question = next(q for q in document["questions"] if q["id"] == 27)
    model = gold_replay.ScriptedModel(question, "ignores_uncited", "A", {"elements": {"Orders Hub": 5, "Billing": 6},
                                                                         "applications": {}})

    class Table:
        entries = [{"tool": "answer", "field": "result.summary.total", "record_name": "Orders Hub",
                    "rendered": "13 connections downstream", "key": "F1"},
                   {"tool": "answer", "field": "result.summary.total", "record_name": "Billing",
                    "rendered": "4 connections downstream", "key": "F2"}]

    text = model._resolve(model._template(question["compliant"]), Table, True)
    assert text == "Orders Hub has 13 connections downstream. Billing has 4 connections downstream."
    assert "[ref:" not in text and "[fig:" not in text


def test_the_judge_scores_four_items_and_needs_no_failure_on_a_and_b():
    runs = [{"id": 1, "delivered": "answer one"}, {"id": 2, "delivered": "answer two"}]
    asks = {1: "first question", 2: "second question"}
    perfect = lambda prompt: '{"A": true, "B": true, "C": true, "D": true}'  # noqa: E731
    result = gold_replay.judge_answers(runs, asks, perfect)
    assert result["ok"] and result["failures"] == {"A": 0, "B": 0, "C": 0, "D": 0}
    one_misread = lambda prompt: '{"A": false, "B": true, "C": true, "D": true}'  # noqa: E731
    assert not gold_replay.judge_answers(runs, asks, one_misread)["ok"]
    unreadable = lambda prompt: "no json here"  # noqa: E731
    assert gold_replay.judge_answers(runs, asks, unreadable)["failures"]["A"] == 2
    prompt = gold_replay.judge_prompt("What breaks?", "Orders Hub has 13.")
    for item in gold_replay.JUDGE_ITEMS.values():
        assert item in prompt
