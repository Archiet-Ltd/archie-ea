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


def test_a_figure_only_the_injected_context_carries_is_not_a_returned_figure():
    grounding = _grounded(IMPACT_13, context={"applications": 14})
    assert _figures(_gate("The change touches 14 connections in the estate.", grounding)) == ["14"]


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
    assert out["text"] == detector.FIGURES_NOT_CONFIRMED + "\n\n" + detector.NEXT_STEP


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


# --------------------------------------------------------------------------- #
# Only a tool's value fields support a figure                                  #
# --------------------------------------------------------------------------- #

import datetime as _real_datetime
import types

# Tool results carry timestamps, durations, ids and hashes next to their values.
# None of them is a figure the answer may quote.
WITH_METADATA = {
    "summary": {"total": 13, "latency_ms": 20.977989},
    "computed_at": "2026-11-21T11:10:52.552000Z",
    "freshness": {"computed_at": "2026-11-21T11:10:52.552000Z", "stale_rows": 0},
    "address": "383400e1b1fa497c8515e89eaba70b08",
    "rows": [{
        "element_id": 21,
        "relation": {
            "depth": 1, "chain": [55, 52],
            "provenance": {"recorded_at": "2026-11-10T04:26:19.392796", "relationship_id": 10},
        },
    }],
    "message": "13 row(s). Answer address 383400e1b1fa497c8515e89eaba70b08.",
}


@pytest.mark.parametrize("wrong", ["21", "55", "52", "11", "10", "20.98"])
def test_a_timestamp_a_duration_or_an_id_does_not_support_a_figure(wrong):
    claims = _gate("The change touches %s connections in the estate." % wrong, _grounded(WITH_METADATA))
    assert _figures(claims) == [wrong]


def test_the_value_fields_of_the_same_result_still_support_a_figure():
    assert _gate("The change touches 13 connections in the estate.", _grounded(WITH_METADATA)) == []


def test_an_identifier_a_tool_returned_is_a_figure_only_when_named_as_an_identifier():
    grounding = _grounded({"rows": [], "elements": {"316": {"id": 316, "name": "Calibration Ledger"}}})
    assert _figures(_gate("The record id 316 is in the register.", grounding)) == []
    assert _figures(_gate("The record #316 is in the register.", grounding)) == []
    assert _figures(_gate("There are 316 connections in the register.", grounding)) == ["316"]


# --------------------------------------------------------------------------- #
# A name's number, a scale                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "The method is TOGAF 9.2 for this work.",
        "The notation is ArchiMate 3.2 throughout.",
        "The control set is NIST 800-53 here.",
        "The control set is NIST SP 800-53 Rev 5 here.",
        "Reporting uses NIST CSF 2.0 here.",
        "Web checks use the OWASP Top 10 here.",
        "Support runs 24/7 for the estate.",
        "It runs on Java 17 in production.",
        "It runs on Windows 11 for staff.",
        "It runs on Python 3.12 in production.",
        "It runs on PostgreSQL 16 in production.",
        "It runs on Ubuntu 22.04 in production.",
        "Payment follows PCI DSS 4.0 here.",
        "The record cites ISO 27001 and ISO/IEC 27001:2022.",
        "Follow SOX 404 for this control.",
    ],
)
def test_a_version_or_number_that_belongs_to_a_named_standard_or_product_is_not_a_figure(text):
    assert _gate(text) == []


@pytest.mark.parametrize(
    "question,answer",
    [
        ("Is a £2m budget enough?", "The £2m budget stands."),
        ("Is a budget of 1.5 million enough?", "The 1.5 million budget stands."),
        ("Is a $250k spend fine?", "The $250k spend stands."),
        ("Is £1,500,000 too much?", "The £1.5m figure stands."),
    ],
)
def test_a_scaled_amount_the_person_typed_is_kept(question, answer):
    assert _figures(_gate(answer)) != []
    assert _gate(answer, question_text=question) == []


def test_a_scaled_amount_in_tool_text_supports_the_same_amount_in_the_answer():
    grounding = _grounded({"message": "The cost is £1.2m this year", "rows": []})
    assert _gate("The cost is £1.2m for the year.", grounding) == []
    assert _figures(_gate("The cost is £1.3m for the year.", grounding)) == ["£1.3m"]


# --------------------------------------------------------------------------- #
# A context count supports only its own noun, only for the organisation        #
# --------------------------------------------------------------------------- #

CONTEXT = {"total_elements": 40, "total_applications": 20, "vendor_count": 16, "total_vendors": 16}


def test_a_context_count_supports_a_figure_for_its_own_noun_only():
    grounding = _grounded(IMPACT_13, context=CONTEXT)
    assert _gate("Your estate has 40 elements in all.", grounding) == []
    assert _gate("Your estate has 20 applications in all.", grounding) == []
    assert _figures(_gate("The change touches 40 connections in the estate.", grounding)) == ["40"]
    assert _figures(_gate("The change touches 20 records in the estate.", grounding)) == ["20"]


def test_a_shared_catalogue_count_supports_nothing():
    grounding = _grounded(IMPACT_13, context=CONTEXT)
    assert _figures(_gate("The catalogue lists 16 vendors for you.", grounding)) == ["16"]
    assert 16 not in grounding.context_counts
    assert "vendor" not in grounding.context_nouns


# --------------------------------------------------------------------------- #
# Removal keeps the rest                                                       #
# --------------------------------------------------------------------------- #


def test_removing_a_figure_keeps_the_rest_of_the_sentence():
    text = "Fig Mid [ref:2] is impacted, and 14 connections depend on it too."
    out = detector.remove_unsupported_figures(text, _grounded(IMPACT_13))
    assert out["removed"] == ["14"]
    assert out["text"].startswith("Fig Mid [ref:2] is impacted.")
    assert "14" not in out["text"]
    assert out["text"].endswith(detector.FIGURES_NOT_CONFIRMED)


def test_removing_a_figure_in_brackets_keeps_the_sentence():
    out = detector.remove_unsupported_figures(
        "Fig Mid [ref:2] is impacted (14 connections in all) by the change.", _grounded(IMPACT_13)
    )
    assert out["removed"] == ["14"]
    assert out["text"].startswith("Fig Mid [ref:2] is impacted by the change.")


def test_a_one_sentence_answer_leaves_the_plain_line_what_the_records_show_and_a_next_step():
    grounding = _grounded({
        "rows": [],
        "elements": {"7": {"id": 7, "name": "Orders Hub"}, "8": {"id": 8, "name": "Orders Client"}},
    })
    out = detector.remove_unsupported_figures("Orders Hub [ref:7] has 14 connections.", grounding)
    assert out["text"].splitlines()[0] == detector.FIGURES_NOT_CONFIRMED
    assert "The records I read do show Orders Hub [ref:7], Orders Client [ref:8]." in out["text"]
    assert out["text"].endswith(detector.NEXT_STEP)
    assert "14" not in out["text"]


def test_a_figure_next_to_a_marker_after_the_full_stop_is_removed():
    out = detector.remove_unsupported_figures(
        "$14m. [ref:7]", _grounded(IMPACT_13)
    )
    assert out["removed"] == ["$14m"]
    assert "$14m" not in out["text"]


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


# The clock digits that used to count as support: minute 14, and a moment whose
# parts are 21, 11, 10, 52 and 55 (the wrong counts below).
MOMENTS = [
    _real_datetime.datetime(2026, 11, 10, 3, 14, 0),
    _real_datetime.datetime(2026, 11, 21, 11, 10, 52, 552000),
]


@pytest.mark.parametrize("moment", MOMENTS, ids=["minute-14", "21-11-10-52-55"])
def test_wrong_counts_are_removed_whatever_the_clock_says(app, db_session, make_org, monkeypatch, moment):
    _freeze_answer_clock(monkeypatch, moment)
    org_a = make_org("fig-clock-a")
    org_b = make_org("fig-clock-b")
    user = _user(db_session, org_a.id)
    root, mid, leaf = _estate(db_session, org_a.id, "Clk")
    _estate(db_session, org_b.id, "Other")
    db_session.commit()
    wrong = (
        f"Clk Mid [ref:{mid.id}] and Clk Leaf [ref:{leaf.id}] are impacted. "
        "That is 21 connections in all. Elsewhere 55 depend on them. About 52 more follow. "
        "Another 11 sit behind them. A further 10 do too. 14 is the total."
    )
    script = [_call_answer(root.id), {"text": wrong}, {"text": wrong}]

    result, _events, _runner, _seen = _run_turn(
        app, monkeypatch, script, user=user, message="what breaks if Clk Root fails?"
    )

    for count in ("21", "55", "52", "11", "10", "14"):
        assert count not in result["response"].replace(f"[ref:{mid.id}]", "").replace(f"[ref:{leaf.id}]", ""), count
    assert result["citation_gate"]["figures_removed"] == 6
    assert f"Clk Mid [ref:{mid.id}]" in result["response"]
