"""The figures gold set: 26 questions, three scripted behaviours, no true figure lost, no wrong one shown.

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


def test_the_set_has_twenty_six_questions_with_frozen_expectations_for_both_organisations():
    document, fixture = gold_replay.load_set("figures")
    questions = document["questions"]
    assert [q["id"] for q in questions] == list(range(1, 27))
    for question in questions:
        for key in ("expected", "forbidden"):
            assert set(question[key]) == {"A", "B"}, (question["id"], key)
        assert question["compliant"] and question["adversarial"]["kind"]
    asked = {q["ask"] for q in questions}
    assert "What breaks if Orders Hub fails?" in asked and "Why is that so high?" in asked
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


def test_the_replay_runs_all_seventy_eight_runs_with_nothing_lost_and_nothing_wrong():
    done = subprocess.run(
        [sys.executable, "-m", "app.modules.intelligence.services.gold_replay", "--set", "figures", "--json"],
        cwd=ROOT, capture_output=True, text=True, timeout=1700, env={**os.environ, "PYTHONPATH": ROOT},
    )
    body = done.stdout[done.stdout.index('{\n "summary"'):]
    report = json.loads(body)
    assert done.returncode == 0, report["failed"]
    assert sorted(report["summary"]) == ["adversarial", "compliant", "ignores"]
    for behaviour, row in report["summary"].items():
        assert row["runs"] == 26, behaviour
        assert row["true_figures_lost"] == 0 and row["wrong_figures_shown"] == 0, (behaviour, row)
    assert report["summary"]["compliant"]["fallback_rate"] == 0.0, "a compliant model is never sent back"
    assert report["failed"] == []
