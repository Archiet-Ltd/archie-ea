"""The conformance statement says only what the audited run measured."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("accessibility_conformance", ROOT / "scripts" / "accessibility_conformance.py")
conformance = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conformance)

TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def _page(**over):
    page = {"name": "Shared heatmap", "path": "/shared/x", "violations": [], "passes": 40,
            "keyboard": {"controls": 3, "unnamed": [], "unreachable": [], "no_focus_ring": []}}
    page.update(over)
    return page


def _run(pages, tags=TAGS):
    return {"journey": "Read a shared dashboard", "tags": tags, "generated_at": "2026-09-29T10:00:00Z", "pages": pages}


def test_a_clean_run_meets_wcag_22_aa_and_lists_what_was_checked():
    statement = conformance.build_statement(_run([_page(), _page(name="After reload")]))
    assert statement["meets"] is True
    text = statement["text"]
    assert "This journey meets WCAG 2.2 level AA." in text
    assert "wcag22aa" in text and "2026-09-29T10:00:00Z" in text
    assert "| Shared heatmap | 40 | 3 | 0 |" in text
    assert "| After reload | 40 | 3 | 0 |" in text


def test_a_violation_means_it_does_not_meet_and_names_the_rule():
    statement = conformance.build_statement(_run([_page(violations=[{"id": "color-contrast", "impact": "serious", "nodes": 2}])]))
    assert statement["meets"] is False
    assert "does not yet meet" in statement["text"]
    assert "color-contrast (serious, 2 element(s))" in statement["text"]


def test_a_keyboard_failure_means_it_does_not_meet():
    for key, words in (("unnamed", "control with no name"), ("unreachable", "control not reached by keyboard"),
                       ("no_focus_ring", "control with no visible focus")):
        kb = {"controls": 2, "unnamed": [], "unreachable": [], "no_focus_ring": []}
        kb[key] = ["Print"]
        statement = conformance.build_statement(_run([_page(keyboard=kb)]))
        assert statement["meets"] is False
        assert f"{words}: Print" in statement["text"]
    statement = conformance.build_statement(_run([_page(keyboard=None)]))
    assert statement["meets"] is False and "keyboard walk not recorded" in statement["text"]


def test_nothing_audited_or_a_narrowed_tag_set_is_never_stated_as_meeting():
    empty = conformance.build_statement(_run([]))
    assert empty["meets"] is False and "has not been audited" in empty["text"]
    narrowed = conformance.build_statement(_run([_page()], tags=["wcag2a", "wcag2aa"]))
    assert narrowed["meets"] is False
    assert "Not audited against: wcag21a, wcag21aa, wcag22aa." in narrowed["text"]


def test_the_command_writes_the_statement_and_exits_by_the_result(tmp_path):
    run = tmp_path / "run.json"
    out = tmp_path / "statement.md"
    run.write_text(json.dumps(_run([_page()])), encoding="utf-8")
    assert conformance.main([str(run), "-o", str(out)]) == 0
    assert "meets WCAG 2.2 level AA" in out.read_text(encoding="utf-8")
    run.write_text(json.dumps(_run([_page(violations=[{"id": "label", "impact": "critical", "nodes": 1}])])), encoding="utf-8")
    assert conformance.main([str(run), "-o", str(out)]) == 1
