"""The design map: every page follows one block, and the merge check refuses a page outside it."""
import copy
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIST_PAGE = "app/templates/applications/list_simple.html"
NEW_PAGE = "app/templates/admin/zz_new_page.html"
# One row per page template in the tree. The designers' port map was cut at 406 pages; the tree holds 404
# today, so the page count and every total derived from it below are the tree's, not the snapshot's. The map
# itself had 409 rows until five rows left behind by deleted pages were removed (traceability, APQC
# recommendations, architect synthesis, EA briefing, vendor ArchiMate mapping). A page added without a row, or
# a row left behind by a deleted page, fails test_every_page_template_is_mapped_and_every_spec_exists.
PAGES_IN_MAP = 404


@pytest.fixture(scope="module")
def tool():
    path = ROOT / "scripts" / "check_design_map.py"
    spec = importlib.util.spec_from_file_location("check_design_map_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def design_map(tool):
    return tool.load_map()


def _page(marker=None, macro=None):
    lines = ['{% extends "layouts/base.html" %}']
    if marker:
        lines.append("{# design-block: %s #}" % marker)
    if macro:
        lines.append('{%% from "%s" import x %%}' % macro)
    lines.append("{% block content %}x{% endblock %}")
    return "\n".join(lines)


def test_page_inside_its_block_passes(tool, design_map):
    assert tool.check_block(LIST_PAGE, _page("data-table", "components/data_table.html"), design_map) == []


def test_page_using_a_macro_its_block_does_not_allow_is_refused(tool, design_map):
    problems = tool.check_block(LIST_PAGE, _page("data-table", "components/stepper.html"), design_map)
    assert len(problems) == 1
    assert "components/stepper.html" in problems[0] and "data-table block does not allow" in problems[0]


def test_marker_disagreeing_with_the_map_is_refused(tool, design_map):
    problems = tool.check_block(LIST_PAGE, _page("form", "components/data_table.html"), design_map)
    assert any("marker says 'form'" in p and "'data-table'" in p for p in problems)


def test_changed_page_without_a_marker_is_refused(tool, design_map):
    problems = tool.check_block(LIST_PAGE, _page(None), design_map)
    assert any("carries no block marker" in p for p in problems)


def test_new_page_must_declare_its_block(tool, design_map):
    undeclared = tool.check_block(NEW_PAGE, _page(None), design_map)
    assert undeclared and "must declare its block" in undeclared[0]
    declared = tool.check_block(NEW_PAGE, _page("form"), design_map)
    assert declared and "not in docs/design-map.json" in declared[0]


def test_a_fragment_that_extends_nothing_is_not_a_page(tool, design_map):
    assert tool.check_block(NEW_PAGE, "<div>fragment</div>", design_map) == []


def test_every_page_template_is_mapped_and_every_spec_exists(tool):
    result = tool.measure()
    assert result["errors"] == []
    assert result["mapped"] == result["pages"] > 400


def test_every_startup_page_has_a_spec_with_a_side_by_side(design_map):
    startup = design_map["startup_path"]
    assert len(startup) == 12
    for rel in startup:
        entry = design_map["pages"][rel]
        spec = ROOT / entry["spec"]
        text = spec.read_text(encoding="utf-8")
        assert entry["status"] in ("specified", "implemented", "verified")
        assert "## Side by side" in text and "-current.webp" in text and "../reference/" in text
        for link in __import__("re").findall(r"\]\(([^)]+\.webp)\)", text):
            assert (spec.parent / link).resolve().stat().st_size > 0, link


def test_merge_check_runs_from_the_command_line_and_names_the_page():
    proc = subprocess.run(
        [sys.executable, "scripts/check_design_map.py", "--json"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    data = json.loads(proc.stdout)
    assert data["errors"] == []
    assert data["outside_block_count"] >= 0 and data["unmarked_count"] > 0


# ---- persona and components on every row

def test_every_row_names_a_persona_from_the_role_list_and_lists_components(design_map):
    from app.models.user import VALID_ROLES

    personas = design_map["personas"]
    assert set(VALID_ROLES) <= set(personas), "every enterprise role is a persona the map can name"
    assert len(design_map["pages"]) == PAGES_IN_MAP
    for rel, entry in design_map["pages"].items():
        assert entry["persona"] in personas, rel
        assert entry["components"], rel
        assert set(entry["components"]) <= set(design_map["components"]), rel


def test_a_row_with_an_unknown_persona_or_no_components_is_an_error(tool, design_map):
    import copy

    broken = copy.deepcopy(design_map)
    rel = LIST_PAGE
    broken["pages"][rel]["persona"] = "not_a_persona"
    broken["pages"][rel]["components"] = []
    pages = {rel: {"extends": ["layouts/base.html"], "macros": [], "marker": broken["pages"][rel]["block"]}}
    errors = tool.measure(broken, pages)["errors"]
    assert any(rel in e and "persona" in e for e in errors)
    assert any(rel in e and "lists no components" in e for e in errors)


def test_a_page_whose_macro_is_outside_its_rows_components_is_counted_and_named(tool, design_map):
    import copy

    narrowed = copy.deepcopy(design_map)
    rel = LIST_PAGE
    narrowed["pages"][rel]["components"] = ["page-header"]
    narrowed["pages"][rel]["inhouse_components"] = []
    pages = {rel: {"extends": ["layouts/base.html"], "macros": ["components/data_table.html"],
                   "marker": narrowed["pages"][rel]["block"]}}
    result = tool.measure(narrowed, pages)
    assert [o["template"] for o in result["outside_components"]] == [rel]
    assert result["outside_components"][0]["components"] == ["table"]
    assert tool.check_block(rel, _page("data-table", "components/data_table.html"), narrowed) == [], \
        "a page not yet rebuilt to its list is counted by the ratchet, not refused on the change"
    narrowed["pages"][rel]["status"] = "implemented"
    problems = tool.check_block(rel, _page("data-table", "components/data_table.html"), narrowed)
    assert len(problems) == 1 and "component list" in problems[0] and "'table'" in problems[0]
    assert tool.check_block(rel, _page("data-table", "components/data_table.html"), design_map) == []


def test_the_gate_holds_the_count_of_pages_outside_their_components():
    baseline = json.loads((ROOT / "verification_baseline.json").read_text(encoding="utf-8"))["ratchets"]
    proc = subprocess.run([sys.executable, "scripts/check_design_map.py", "--json"],
                          cwd=ROOT, capture_output=True, text=True, timeout=300)
    data = json.loads(proc.stdout[proc.stdout.index("{"):])
    assert data["outside_components_count"] <= baseline["design_map_outside_components"]


# ---- the designers' port map is folded into every row

TIER_TOTALS = {"A": 18, "B": 337, "C1": 9, "C2": 14, "C3": 10, "C4": 4, "C5": 12}
PORT_FIELDS = ("tier", "page_type", "type_changed", "original", "frame", "charts", "inhouse_components",
               "confidence", "reason")


def test_every_row_carries_the_port_map_fields_and_the_tier_totals_are_the_designers(design_map):
    pages = design_map["pages"]
    assert len(pages) == PAGES_IN_MAP
    for rel, entry in pages.items():
        for field in PORT_FIELDS:
            assert field in entry, f"{rel} lacks {field}"
        assert entry["reason"].strip() and entry["original"] and entry["frame"], rel
    totals = {}
    for entry in pages.values():
        totals[entry["tier"]] = totals.get(entry["tier"], 0) + 1
    assert totals == TIER_TOTALS
    assert sum(1 for e in pages.values() if e["type_changed"]) == 72
    assert sum(1 for e in pages.values() if e["confidence"] == "low") == 91


def test_the_port_map_wins_on_persona_and_components_and_the_vocabulary_knows_every_component(design_map):
    row = design_map["pages"]["app/modules/architecture_assistant/templates/metamodel/properties.html"]
    assert row["persona"] == "data_architect"
    # The port map names four components; the row also carries the data-table block's core components.
    assert {"table", "input-group", "pagination", "badge"} <= set(row["components"])
    assert row["components"] == ["alert", "badge", "breadcrumb", "button", "empty", "field", "input", "page-header",
                                 "page-shell", "pagination", "select", "table", "input-group"]
    assert row["inhouse_components"] == ["page-shell:Replace"]
    for entry in design_map["pages"].values():
        assert set(entry["components"]) <= set(design_map["components"])


def test_a_row_with_an_illegal_tier_or_confidence_or_no_reason_is_an_error(tool, design_map):
    import copy

    broken = copy.deepcopy(design_map)
    rel = LIST_PAGE
    broken["pages"][rel].update(tier="D", confidence="maybe", reason="")
    pages = {rel: {"extends": ["layouts/base.html"], "macros": [], "marker": broken["pages"][rel]["block"]}}
    errors = tool.measure(broken, pages)["errors"]
    assert any(rel in e and "tier 'D'" in e for e in errors)
    assert any(rel in e and "confidence 'maybe'" in e for e in errors)
    assert any(rel in e and "no reason" in e for e in errors)


# ---- each page is measured against the checks of its tier

def _tier_row(design_map, tier, inhouse=()):
    import copy

    broken = copy.deepcopy(design_map)
    row = broken["pages"][LIST_PAGE]
    row["tier"], row["inhouse_components"] = tier, list(inhouse)
    return broken


def _measure_source(tool, design_map, source, macros=()):
    pages = {LIST_PAGE: {"extends": ["layouts/base.html"], "macros": list(macros),
                         "marker": design_map["pages"][LIST_PAGE]["block"]}}
    return tool.tier_checks(design_map, pages, read=lambda rel: source)


def test_a_page_still_using_a_component_marked_replace_is_counted_by_tier(tool, design_map):
    marked = _tier_row(design_map, "A", ["page-shell:Replace", "provenance:Compose"])
    found = _measure_source(tool, marked, "<p>x</p>", ["macros/page_shell.html", "components/provenance.html"])
    assert found["replace_in_use"]["by_tier"] == {"A": 1} and found["replace_in_use"]["pages"] == [LIST_PAGE]
    assert found["in_house_beyond_tier"]["by_tier"] == {"A": 1}
    composed = _tier_row(design_map, "B", ["provenance:Compose"])
    found = _measure_source(tool, composed, "<p>x</p>", ["components/provenance.html"])
    assert found["replace_in_use"]["count"] == 0 and found["in_house_beyond_tier"]["count"] == 0


def test_a_template_with_a_colour_literal_an_inline_style_an_arbitrary_class_or_three_charts_is_counted(tool, design_map):
    row = _tier_row(design_map, "B")
    source = ('<div class="h-[250px] p-2" style="width: 40px">'
              '<span style="color: #ff0000"></span><canvas></canvas><canvas></canvas><canvas></canvas></div>')
    found = _measure_source(tool, row, source)
    for check in ("colour_literals", "inline_style", "arbitrary_values", "charts_over_two"):
        assert found[check]["by_tier"] == {"B": 1}, check
    clean = '<div class="p-2" style="color: hsl(var(--muted-foreground))"><canvas></canvas></div>'
    found = _measure_source(tool, row, clean)
    assert found["colour_literals"]["count"] == 0 and found["arbitrary_values"]["count"] == 0
    assert found["charts_over_two"]["count"] == 0


def test_geometry_properties_are_allowed_on_a_tier_c_page_and_nowhere_else(tool, design_map):
    source = '<div style="--x: 4; --w: 20"></div>'
    assert _measure_source(tool, _tier_row(design_map, "C3"), source)["inline_style"]["count"] == 0
    assert _measure_source(tool, _tier_row(design_map, "B"), source)["inline_style"]["count"] == 1


def test_the_gate_reports_each_tier_and_holds_every_tier_check_at_its_baseline():
    baseline = json.loads((ROOT / "verification_baseline.json").read_text(encoding="utf-8"))["ratchets"]
    proc = subprocess.run([sys.executable, "scripts/check_design_map.py", "--json"],
                          cwd=ROOT, capture_output=True, text=True, timeout=300)
    data = json.loads(proc.stdout[proc.stdout.index("{"):])
    assert data["tiers"] == TIER_TOTALS
    assert set(data["tier_checks"]) == {k[len("design_map_tier_"):] for k in baseline if k.startswith("design_map_tier_")}
    for check, found in data["tier_checks"].items():
        assert found["count"] <= baseline["design_map_tier_" + check], check
    assert "A2-A5" in data["tier_checks_pending"] and "B7" in data["tier_checks_pending"]


# ---------------------------------------------------------------- patterns, states, departures and grid

def test_every_row_carries_patterns_states_departures_and_a_grid_in_the_counts_of_the_final_port_map(design_map):
    rows = design_map["pages"].values()
    assert len(design_map["pages"]) == PAGES_IN_MAP
    assert all(r["states"] and isinstance(r["departures"], list) and "grid" in r for r in rows)
    departures = Counter(d["rule"] for r in rows for d in r["departures"])
    assert departures == {"R10": 221, "C-form": 49, "R9": 39, "brand": 24}
    empty = sorted(rel for rel, r in design_map["pages"].items() if not r["patterns"])
    assert len(empty) == 7 and all(design_map["pages"][rel]["pattern_gap"] for rel in empty)
    assert sum(1 for r in rows if r["grid"] == "G4") == sum(1 for r in rows if r["tier"].startswith("C"))
    assert {r["grid"] for r in rows} == {"G1", "G3", "G4", None}


def test_a_row_with_a_missing_or_unknown_pattern_state_departure_or_grid_is_an_error(tool, design_map):
    row = copy.deepcopy(design_map["pages"][LIST_PAGE])
    assert tool.port_map_problems(LIST_PAGE, row, design_map) == []
    for field, value, text in (("patterns", ["P99-nothing"], "not in the pattern register"),
                               ("patterns", [], "does not say which gap"),
                               ("states", [], "no states"),
                               ("departures", [{"rule": "R1", "what": "x"}], "needs a rule"),
                               ("grid", "G9", "not one of")):
        broken = {**row, field: value}
        assert any(text in p for p in tool.port_map_problems(LIST_PAGE, broken, design_map)), field
    no_grid = {k: v for k, v in row.items() if k != "grid"}
    assert any("no content grid" in p for p in tool.port_map_problems(LIST_PAGE, no_grid, design_map))
    gap = {**row, "patterns": [], "pattern_gap": "G-1: no pattern yet"}
    assert tool.port_map_problems(LIST_PAGE, gap, design_map) == []


def _with(design_map, tier="B", **fields):
    broken = _tier_row(design_map, tier)
    broken["pages"][LIST_PAGE].update(fields)
    return broken


def test_a_page_doing_a_job_its_patterns_do_not_cover_is_counted(tool, design_map):
    table = '<table class="w-full"></table>'
    uncovered = _with(design_map, patterns=["P01-page-header"], grid="G1")
    assert _measure_source(tool, uncovered, table)["pattern_outside_named"]["by_tier"] == {"B": 1}
    covered = _with(design_map, patterns=["P01-page-header", "P03-data-table"], grid="G1")
    assert _measure_source(tool, covered, table)["pattern_outside_named"]["count"] == 0
    tabs = '<div role="tablist"></div>'
    assert _measure_source(tool, uncovered, tabs)["pattern_outside_named"]["count"] == 1
    assert _measure_source(tool, _with(design_map, patterns=["P02-section-tabs"]), tabs)["pattern_outside_named"]["count"] == 0


def test_a_region_wrapper_outside_the_named_grid_or_a_grey_box_or_a_bento_span_is_counted(tool, design_map):
    row = _with(design_map, patterns=["P03-data-table"], grid="G1")
    inside = ('<div class="flex flex-col gap-4 py-4 md:gap-6 md:py-6">'
              '<div class="grid grid-cols-1 gap-4 @xl/main:grid-cols-2"></div></div>')
    assert _measure_source(tool, row, inside)["grid_outside_named"]["count"] == 0
    for source in ('<div class="flex flex-col gap-3"></div>',
                   '<div class="grid gap-4 grid-cols-5"></div>',
                   '<div class="aspect-video rounded-xl bg-muted/50"></div>',
                   '<div class="grid gap-4"><div class="col-span-2"></div></div>'):
        assert _measure_source(tool, row, source)["grid_outside_named"]["count"] == 1, source
    pair = '<div class="grid grid-cols-2 gap-4"></div>'
    form = _with(design_map, patterns=["P10-sectioned-form"], grid="G3")
    assert _measure_source(tool, form, pair)["grid_outside_named"]["count"] == 0
    assert _measure_source(tool, _with(design_map, patterns=["P03-data-table"], grid="G3"), pair)["grid_outside_named"]["count"] == 1
    assert _measure_source(tool, _with(design_map, grid=None), '<div class="grid gap-9"></div>')["grid_outside_named"]["count"] == 0
