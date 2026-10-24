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
