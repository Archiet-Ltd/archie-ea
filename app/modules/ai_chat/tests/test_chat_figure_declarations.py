"""Every chat tool declares its figures, and every declaration is sound.

A figure reaches a person only as the engine's rendering of a value a declared
field returned, so a tool that returns a number must say which field is a figure
and how it reads. A tool that returns none declares an empty ``figures``.
"""

from __future__ import annotations

import copy

from app.modules.ai_chat.tools import registry
from app.modules.ai_chat.tools.registry import (
    ORGANISATION_SUMMARY_FIGURES,
    TOOL_SCHEMAS,
    figure_declaration_problems,
    tools_without_figures,
)


def test_every_registered_tool_declares_its_figures():
    assert tools_without_figures() == []
    for schema in TOOL_SCHEMAS:
        assert figure_declaration_problems(schema["figures"]) == [], schema["name"]
    assert figure_declaration_problems(ORGANISATION_SUMMARY_FIGURES) == []


def test_a_tool_with_no_figures_key_is_reported():
    schemas = copy.deepcopy(TOOL_SCHEMAS)
    del schemas[3]["figures"]
    assert tools_without_figures(schemas) == [schemas[3]["name"]]


def test_a_sound_declaration_is_required_for_each_kind():
    base = {"path": "total", "reads_null": "none recorded", "label": "L", "concept": "c"}
    cases = {
        "money with no currency": {**base, "kind": "money", "reads": "{value}"},
        "score with no scale": {**base, "kind": "score", "reads": "{value}"},
        "duration with no unit": {**base, "kind": "duration", "reads": "{value}"},
        "rank with no total": {**base, "kind": "rank", "reads": "{value}"},
        "count with one phrase": {**base, "kind": "count", "reads": "{value} things"},
        "phrase without the value": {**base, "kind": "year", "reads": "a year"},
        "unknown kind": {**base, "kind": "ratio2", "reads": "{value}"},
    }
    for name, field in cases.items():
        assert figure_declaration_problems({"fields": [field], "rows": [], "names": []}), name


def test_a_row_figure_needs_a_declared_row_list():
    field = {"path": "result[].cost", "kind": "money", "currency": "organisation", "reads": "{value}",
             "reads_null": "none recorded", "label": "Cost", "concept": "application.cost"}
    assert figure_declaration_problems({"fields": [field], "rows": [], "names": []})
    rows = [{"path": "result[]", "id": "id", "name": "name"}]
    assert figure_declaration_problems({"fields": [field], "rows": rows, "names": []}) == []


def test_the_vendor_catalogue_count_is_declared_public():
    vendors = [f for f in ORGANISATION_SUMMARY_FIGURES["fields"] if "vendor" in f["path"]]
    assert vendors and all(f.get("scope") == "public" for f in vendors)
    assert all("Entelim's public catalogue" in str(f["reads"]) for f in vendors)


def test_placeholder_investment_cost_and_timeframe_are_not_declared():
    paths = [f["path"] for f in registry.TOOL_SCHEMA_BY_NAME["get_investment_priorities"]["figures"]["fields"]]
    assert not any("estimated_cost" in p or "timeframe" in p for p in paths)


# --------------------------------------------------------------------------- #
# Each declared field renders from seeded data                                 #
# --------------------------------------------------------------------------- #

import pytest  # noqa: E402

from app.modules.ai_chat.services import chat_figures as cf  # noqa: E402
from app.modules.ai_chat.services.ai_hallucination_detector import Grounding  # noqa: E402
from app.modules.ai_chat.tests.test_answer_tool_and_citation_gate import (  # noqa: E402
    _call,
    _element,
    _estate,
    _link,
    _user,
)


def _view(tool, result, org_id):
    grounding = Grounding(org_id)
    shown = grounding.model_view(tool, result, registry.TOOL_SCHEMA_BY_NAME[tool]["figures"], org_id)
    return grounding, shown


def _fields(grounding):
    return {e["field"]: e for e in grounding.figure_table.entries}


def test_the_impact_answer_declares_its_downstream_count(app, db_session, make_org):
    org = make_org("decl-impact")
    user = _user(db_session, org.id)
    root, mid, leaf = _estate(db_session, org.id, "Imp")
    for tool, arguments in (("answer", {"question": "impact", "element_id": root.id}),
                            ("simulate_impact", {"element_name": "Imp Root"})):
        out = _call(app, user, org.id, tool, arguments)
        assert out["success"], out
        grounding, shown = _view(tool, out, org.id)
        entry = _fields(grounding)["result.summary.total"]
        assert entry["value"] == 2 and entry["rendered"] == "2 connections downstream"
        assert entry["record_name"] == "Imp Root" and str(entry["record_id"]) == str(root.id)
        assert shown["result"]["summary"]["total"] == {"fig": entry["key"], "reads": "2 connections downstream"}
        assert shown["result"]["rows"][0]["element_id"] == mid.id, "ids stay for [ref:]"


def test_the_set_impact_answer_declares_its_count(app, db_session, make_org):
    org = make_org("decl-set")
    user = _user(db_session, org.id)
    root, mid, _leaf = _estate(db_session, org.id, "Set")
    out = _call(app, user, org.id, "answer", {"question": "set_impact", "element_ids": [root.id, mid.id]})
    assert out["success"], out
    grounding, _shown = _view("answer", out, org.id)
    entry = _fields(grounding)["result.summary.total"]
    assert entry["rendered"].endswith("downstream of these records") and entry["value"] == out["result"]["summary"]["total"]


def test_the_risk_and_data_flow_answers_name_what_they_count(make_org):
    org = make_org("decl-kinds")
    for kind, text in (("risk_propagation", "3 risks reaching it"), ("data_flow", "3 data flows")):
        result = {"success": True, "result": {"question_kind": kind, "summary": {"total": 3}, "rows": [],
                                              "subject_elements": {"5": {"name": "Subject"}}}}
        grounding, _ = _view("answer", result, org.id)
        entry = _fields(grounding)["result.summary.total"]
        assert entry["rendered"] == text and entry["record_name"] == "Subject"


def test_the_application_list_declares_its_total_and_each_annual_cost(app, db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("decl-apps")
    user = _user(db_session, org.id)
    org.reporting_currency = "GBP"
    priced = ApplicationComponent(name="Priced App", organization_id=org.id, total_cost_of_ownership=470000)
    free = ApplicationComponent(name="Unpriced App", organization_id=org.id)
    db_session.add_all([priced, free])
    db_session.commit()
    out = _call(app, user, org.id, "find_applications", {})
    assert out["success"]
    grounding, shown = _view("find_applications", out, org.id)
    by_record = {str(e["record_id"]): e for e in grounding.figure_table.entries if e["field"].endswith("annual_cost")}
    assert by_record[str(priced.id)]["rendered"] == "an annual cost of £470,000"
    assert by_record[str(free.id)]["rendered"] == "no annual cost recorded"
    total = _fields(grounding)["total"]
    assert total["value"] == 2 and total["rendered"] == "2 applications match this search"


def test_the_capability_gap_list_declares_its_total_and_supporting_applications(app, db_session, make_org):
    from app.models.business_capabilities import BusinessCapability

    org = make_org("decl-caps")
    user = _user(db_session, org.id)
    cap = BusinessCapability(name="Gap Cap", organization_id=org.id, current_maturity_level=1,
                             target_maturity_level=3)
    db_session.add(cap)
    db_session.commit()
    out = _call(app, user, org.id, "query_capability_gaps", {})
    assert out["success"], out
    grounding, _ = _view("query_capability_gaps", out, org.id)
    fields = _fields(grounding)
    assert fields["total"]["rendered"] == "1 capability at or below the maturity searched"
    assert fields["result[].supporting_apps"]["rendered"] == "0 supporting applications"
    assert fields["result[].supporting_apps"]["record_name"] == "Gap Cap"


def test_the_solution_summary_declares_its_counts_and_maturity(app, db_session, make_org):
    from app.models.solution_models import Solution

    org = make_org("decl-solution")
    user = _user(db_session, org.id)
    solution = Solution(name="Decl Solution", organization_id=org.id, maturity_current=3)
    db_session.add(solution)
    db_session.commit()
    out = _call(app, user, org.id, "get_solution_summary", {"solution_id": solution.id})
    assert out["success"], out
    grounding, _ = _view("get_solution_summary", out, org.id)
    fields = _fields(grounding)
    assert fields["result.maturity_level"]["rendered"] == "a maturity of 3 out of 5"
    assert fields["result.applications_count"]["rendered"].endswith("applications in the solution")
    assert {e["record_name"] for e in fields.values()} == {"Decl Solution"}


def test_the_investment_priorities_declare_each_score_and_the_tier_counts(make_org):
    """The shape is the executor's: ranked rows with no id (a row label), and the tier split."""
    org = make_org("decl-invest")
    result = {"success": True, "result": {
        "ranked": [
            {"capability_name": "Field Service Management", "priority_level": "HIGH",
             "investment_priority_score": 80, "estimated_cost": None, "timeframe_months": None},
            {"capability_name": "Billing", "priority_level": "LOW", "investment_priority_score": 32.5,
             "estimated_cost": None, "timeframe_months": None},
        ],
        "split": {"critical": 1, "high": 0, "medium": 1, "low": 1}, "mapping_count": 9}}
    grounding, shown = _view("get_investment_priorities", result, org.id)
    entries = grounding.figure_table.entries
    scores = [e for e in entries if e["field"].endswith("investment_priority_score")]
    assert [e["rendered"] for e in scores] == ["a priority score of 80 out of 100",
                                                "a priority score of 32.5 out of 100"]
    assert [e["label"] for e in scores] == ["Field Service Management", "Billing"]
    tiers = {e["field"]: e["rendered"] for e in entries if e["field"].startswith("result.split")}
    assert tiers["result.split.critical"] == "1 critical investment"
    assert tiers["result.split.high"] == "0 high-priority investments"
    # The placeholder fields stay as returned (null): nothing is declared for them.
    assert shown["result"]["ranked"][0]["estimated_cost"] is None


def test_the_completeness_score_declares_one_percentage(make_org):
    org = make_org("decl-complete")
    result = {"success": True, "result": {"overall_pct": 62, "sections": {"a": 10, "b": 90}}}
    grounding, shown = _view("get_completeness_score", result, org.id)
    assert [e["rendered"] for e in grounding.figure_table.entries] == ["62% blueprint completeness"]
    assert shown["result"]["sections"] == {"a": 10, "b": 90}, "undeclared sections are not figures"


def test_declared_durations_render_in_their_unit(make_org):
    org = make_org("decl-duration")
    declaration = {"fields": [
        {"path": "result.duration_months", "kind": "duration", "unit": "month", "reads": "a timeframe of {value}",
         "reads_null": "no timeframe recorded", "label": "Timeframe", "concept": "demo.duration"},
        {"path": "result.response_time", "kind": "duration", "unit": "minute", "reads": "a response time of {value}",
         "reads_null": "no response time recorded", "label": "Response time", "concept": "demo.duration"},
    ], "rows": [], "names": []}
    grounding = Grounding(org.id)
    grounding.model_view("demo", {"success": True, "result": {"duration_months": 18, "response_time": 1}},
                         declaration, org.id)
    assert [e["rendered"] for e in grounding.figure_table.entries] == [
        "a timeframe of 18 months", "a response time of 1 minute"]


def test_an_organisation_stamp_is_the_organisation_the_tool_ran_for(app, db_session, make_org):
    org_a, org_b = make_org("stamp-a"), make_org("stamp-b")
    user_a, user_b = _user(db_session, org_a.id), _user(db_session, org_b.id)
    root_a, _m, _l = _estate(db_session, org_a.id, "Stamp")
    root_b = _element(db_session, org_b.id, "Stamp Root")
    _link(db_session, org_b.id, root_b, _element(db_session, org_b.id, "Stamp Only Dep"))
    db_session.commit()
    out_a = _call(app, user_a, org_a.id, "answer", {"question": "impact", "element_id": root_a.id})
    out_b = _call(app, user_b, org_b.id, "answer", {"question": "impact", "element_id": root_b.id})
    ga, _ = _view("answer", out_a, org_a.id)
    gb, _ = _view("answer", out_b, org_b.id)
    assert {e["org"] for e in ga.figure_table.entries} == {org_a.id}
    assert {e["org"] for e in gb.figure_table.entries} == {org_b.id}
    assert _fields(ga)["result.summary.total"]["rendered"] == "2 connections downstream"
    assert _fields(gb)["result.summary.total"]["rendered"] == "1 connection downstream"
    key = _fields(ga)["result.summary.total"]["key"]
    assert [v["reason"] for v in cf.violations_in("Stamp Root [ref:1] has [fig:%s]." % key, ga.figure_table, org_b.id)
            if v["reason"] != cf.REASON_WRITTEN] == [cf.REASON_OTHER_ORG]


# --------------------------------------------------------------------------- #
# Coverage of the declarations, and the connector                              #
# --------------------------------------------------------------------------- #

RENDERED_FROM_SEEDED_DATA = {
    "answer", "simulate_impact", "find_applications", "query_capability_gaps",
    "get_investment_priorities", "get_completeness_score", "get_solution_summary",
}


def test_every_tool_that_declares_a_figure_is_rendered_from_seeded_data_by_a_test_here():
    declaring = {s["name"] for s in TOOL_SCHEMAS if (s["figures"] or {}).get("fields")}
    assert declaring == RENDERED_FROM_SEEDED_DATA, (
        "a tool that declares a figure needs a test that renders every declared field: " + str(declaring ^ RENDERED_FROM_SEEDED_DATA))


def test_the_connector_figures_carry_the_text_each_figure_reads_as(app, db_session, make_org):
    from app.modules.intelligence.services.answer_envelope import connector_figures

    org = make_org("decl-connector")
    user = _user(db_session, org.id)
    root, _mid, _leaf = _estate(db_session, org.id, "Con")
    out = _call(app, user, org.id, "answer", {"question": "impact", "element_id": root.id})
    figures = connector_figures("answer", out, org.id)
    assert [f["reads"] for f in figures] == ["2 connections downstream"]
    assert figures[0]["value"] == 2 and figures[0]["field"] == "result.summary.total"
    assert "start" not in figures[0], "no offsets: the client writes its own prose"
    assert connector_figures("create_solution", {"success": True, "result": {"id": 3}}, org.id) == []


def test_the_connector_tells_the_clients_assistant_to_quote_the_text_each_figure_reads_as():
    from app.modules.ai_chat.tools.route_read_tools import connector_tools

    by_name = {t["name"]: t for t in connector_tools()}
    assert "quote that text, never your own digits" in by_name["answer"]["description"]
    assert registry.FIGURE_TOOL_NOTE not in by_name["get_genome_slice"]["description"], "it declares no figure"
