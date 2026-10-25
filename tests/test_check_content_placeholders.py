"""The placeholder-phrase counter sees each banned phrase and honours the marker."""
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_content_placeholders", ROOT / "scripts" / "check_content_placeholders.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _count_in(tmp_path, monkeypatch, text, suffix="html"):
    folder = tmp_path / "app" / "templates"
    folder.mkdir(parents=True)
    (folder / f"page.{suffix}").write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return len(_load().occurrences())


def test_each_banned_phrase_is_counted(tmp_path, monkeypatch):
    text = "<p>Nothing here yet</p>\n<td>No data</td>\n<td>N/A</td>\n"
    assert _count_in(tmp_path, monkeypatch, text) == 3


def test_clean_page_counts_zero(tmp_path, monkeypatch):
    text = "<p>No risks yet. Add the first risk.</p>\n"
    assert _count_in(tmp_path, monkeypatch, text) == 0


def test_marked_line_is_excluded(tmp_path, monkeypatch):
    text = "<td>N/A</td> {# placeholder-ok: the standard's own example #}\n"
    assert _count_in(tmp_path, monkeypatch, text) == 0


def test_tree_count_matches_recorded_baseline():
    import json
    baseline = json.loads((ROOT / "verification_baseline.json").read_text())["content_placeholders"]
    os.chdir(ROOT)
    assert len(_load().occurrences()) <= baseline
