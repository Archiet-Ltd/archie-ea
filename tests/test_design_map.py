"""The design map: every page follows one block, and the merge check refuses a page outside it."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIST_PAGE = "app/templates/applications/list_simple.html"
NEW_PAGE = "app/templates/admin/zz_new_page.html"


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
    assert len(design_map["pages"]) == 406
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
    pages = {rel: {"extends": ["layouts/base.html"], "macros": ["components/data_table.html"],
                   "marker": narrowed["pages"][rel]["block"]}}
    result = tool.measure(narrowed, pages)
    assert [o["template"] for o in result["outside_components"]] == [rel]
    assert result["outside_components"][0]["components"] == ["table"]
    problems = tool.check_block(rel, _page("data-table", "components/data_table.html"), narrowed)
    assert len(problems) == 1 and "component list" in problems[0] and "'table'" in problems[0]
    assert tool.check_block(rel, _page("data-table", "components/data_table.html"), design_map) == []


def test_the_gate_holds_the_count_of_pages_outside_their_components():
    baseline = json.loads((ROOT / "verification_baseline.json").read_text(encoding="utf-8"))["ratchets"]
    proc = subprocess.run([sys.executable, "scripts/check_design_map.py", "--json"],
                          cwd=ROOT, capture_output=True, text=True, timeout=300)
    data = json.loads(proc.stdout[proc.stdout.index("{"):])
    assert data["outside_components_count"] <= baseline["design_map_outside_components"]
