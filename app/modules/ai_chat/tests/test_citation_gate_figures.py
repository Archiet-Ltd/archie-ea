"""Every figure in a chat answer comes from a tool result of the same turn.

An answer that states a number no tool returned is sent back once with the
reason. If the number is still unsupported it is taken out of the answer, and
the answer says a figure could not be confirmed.

Fixtures (app, db_session, make_org) come from app/modules/conftest.py.
"""

from __future__ import annotations

import pytest

from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.services import ai_hallucination_detector as detector
from app.modules.ai_chat.services.ai_hallucination_detector import (
    Grounding,
    find_uncited_claims,
)
from app.modules.ai_chat.tests.test_answer_tool_and_citation_gate import (
    _call_answer,
    _element,
    _enforce_mode,  # noqa: F401 - the module's autouse fixture, reused here
    _estate,
    _link,
    _run_turn,
    _user,
)

IMPACT_13 = {
    "summary": {"total": 13, "connections": 13},
    "message": "13 connections depend on this record.",
    "share": 0.45,
    "cost": 1234567.0,
}


def _grounded(*results, context=None):
    grounding = Grounding()
    if context is not None:
        grounding.add_context(context)
    for result in results:
        grounding.add_tool_result({"success": True, "result": result})
    return grounding


def _gate(text, grounding=None, org_names=None, question_text=""):
    return find_uncited_claims(
        text,
        grounding if grounding is not None else _grounded(IMPACT_13),
        org_names=org_names or set(),
        question_text=question_text,
    )


def _figures(claims):
    return [
        reason.split(": ", 1)[1]
        for claim in claims for reason in claim["unsupported"]
        if reason.startswith("figure no tool returned this turn")
    ]


def test_a_figure_no_tool_returned_fails_with_a_reason_that_names_it():
    claims = _gate("The change touches 14 connections in the estate.")
    assert _figures(claims) == ["14"]


def test_a_figure_a_tool_returned_passes():
    assert _gate("The change touches 13 connections in the estate.") == []


@pytest.mark.parametrize(
    "text",
    [
        "The change touches 1,234,567 in cost.",
        "About 45% of the load sits here.",
        "The cost is $1.2m for the year.",
        "The share is 45.0% of traffic.",
    ],
)
def test_thousands_separators_rounding_and_percentages_match_the_tool_number(text):
    assert _gate(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "The register was last reviewed in 2024.",
        "It was reviewed on 2026-11-09 by the team.",
        "It was reviewed on 9 November 2026 by the team.",
        "The review is booked for 14:30 on Friday.",
        "The review ran on 09/11/2026 for the team.",
        "The platform runs on version 2.14.1 of the agent.",
        "The platform runs on v3.10 of the agent today.",
        "The record cites ISO 27001 for access control.",
        "Fiscal years 2024-2026 are covered by the plan.",
        "Step 3 comes first and 2 follow after it.",
        "Item APP-4821 is the owner of the register.",
    ],
)
def test_years_dates_versions_and_names_are_not_figures(text):
    assert _gate(text) == []


def test_a_number_inside_a_records_own_name_is_not_a_figure():
    grounding = _grounded({"rows": [], "elements": {"7": {"id": 7, "name": "Risk Register 2200"}}})
    claims = _gate(
        "Risk Register 2200 [ref:7] is the register of record.",
        grounding=grounding,
        org_names={"risk register 2200"},
    )
    assert _figures(claims) == []


def test_a_number_the_user_typed_is_not_a_figure():
    text = "Yes, those 14 connections are in the estate."
    assert _figures(_gate(text)) == ["14"]
    assert _figures(_gate(text, question_text="Do 14 connections depend on it?")) == []


def test_an_answer_with_no_tool_result_and_no_estate_question_is_not_checked():
    assert find_uncited_claims("TOGAF has 10 phases in the cycle.", Grounding()) == []


def test_removal_leaves_no_unsupported_figure_and_says_so():
    grounding = _grounded(IMPACT_13)
    text = "Intro line for the answer.\nThe change touches 14 connections [ref:7].\n- 13 connections depend on it."
    out = detector.remove_unsupported_figures(text, grounding)
    assert out["removed"] == ["14"]
    assert "14" not in out["text"]
    assert "13 connections depend on it." in out["text"]
    assert out["text"].endswith(detector.FIGURES_NOT_CONFIRMED)
    assert "[ref:7]" not in out["text"]


def test_removing_the_only_statement_leaves_just_the_plain_line():
    out = detector.remove_unsupported_figures("About 14 connections depend on it.", _grounded(IMPACT_13))
    assert out["text"] == detector.FIGURES_NOT_CONFIRMED


# --------------------------------------------------------------------------- #
# The whole turn                                                               #
# --------------------------------------------------------------------------- #


def test_a_wrong_count_is_sent_back_once_and_the_corrected_answer_is_shown(
    app, db_session, make_org, monkeypatch
):
    org = make_org("fig-revise")
    user = _user(db_session, org.id)
    root, mid, leaf = _estate(db_session, org.id, "Fig")
    wrong = f"Fig Mid [ref:{mid.id}] and Fig Leaf [ref:{leaf.id}] are impacted, 14 in all."
    right = f"Fig Mid [ref:{mid.id}] and Fig Leaf [ref:{leaf.id}] are impacted, 2 in all."
    script = [_call_answer(root.id), {"text": wrong}, {"text": right}]

    result, _events, _runner, seen = _run_turn(
        app, monkeypatch, script, user=user, message="what breaks if Fig Root fails?"
    )

    assert result["response"] == right
    assert "14" in str(seen[2][-1]["content"]), "the reason names the figure"
    assert len(seen) == 3, "exactly one revision"


def test_a_figure_still_unsupported_after_the_revision_is_removed(
    app, db_session, make_org, monkeypatch
):
    org = make_org("fig-remove")
    user = _user(db_session, org.id)
    root, mid, leaf = _estate(db_session, org.id, "Rem")
    wrong = (
        f"Rem Mid [ref:{mid.id}] and Rem Leaf [ref:{leaf.id}] are impacted. "
        "That is 14 connections in all."
    )
    script = [_call_answer(root.id), {"text": wrong}, {"text": wrong}]

    result, _events, runner, seen = _run_turn(
        app, monkeypatch, script, user=user, message="what breaks if Rem Root fails?"
    )

    assert len(seen) == 3, "one revision, no more"
    assert "14" not in result["response"]
    assert result["response"].endswith(detector.FIGURES_NOT_CONFIRMED)
    assert f"Rem Mid [ref:{mid.id}]" in result["response"]
    assert result["citation_gate"]["refused"] is False
    assert result["citation_gate"]["figures_removed"] == 1
    assert result["response"] != AgentRunner.CITATION_REFUSAL


def test_a_figure_from_another_organisations_turn_does_not_support_an_answer(
    app, db_session, make_org, monkeypatch
):
    """Two organisations: organisation A's root has 14 dependents, organisation
    B's has 2. "14" is a returned figure for A and an unsupported one for B."""
    org_a = make_org("fig-org-a")
    org_b = make_org("fig-org-b")
    user_a = _user(db_session, org_a.id)
    user_b = _user(db_session, org_b.id)
    root_a = _element(db_session, org_a.id, "Alpha Root")
    for index in range(14):
        _link(db_session, org_a.id, root_a, _element(db_session, org_a.id, f"Alpha Dep {index}"))
    root_b, mid_b, _leaf_b = _estate(db_session, org_b.id, "Beta")
    db_session.commit()
    text_a = f"Alpha Root [ref:{root_a.id}] has 14 dependents in all."
    text_b = f"Beta Mid [ref:{mid_b.id}] is impacted, and 14 depend on it."

    result_a, *_ = _run_turn(
        app, monkeypatch, [_call_answer(root_a.id), {"text": text_a}], user=user_a,
        message="what breaks if Alpha Root fails?",
    )
    result_b, *_ = _run_turn(
        app, monkeypatch, [_call_answer(root_b.id), {"text": text_b}, {"text": text_b}],
        user=user_b, message="what breaks if Beta Root fails?",
    )

    assert "14 dependents" in result_a["response"]
    assert "14" not in result_b["response"]
    assert result_b["response"].endswith(detector.FIGURES_NOT_CONFIRMED)
