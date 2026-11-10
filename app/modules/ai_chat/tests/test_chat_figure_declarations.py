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
