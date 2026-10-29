"""The design map holds each page template once: a key written twice is reported by the check."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = "app/templates/zz/doubled.html"


@pytest.fixture(scope="module")
def tool():
    path = ROOT / "scripts" / "check_design_map.py"
    spec = importlib.util.spec_from_file_location("check_design_map_duplicates_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_doubled_key_is_reported_with_both_line_numbers(tool, tmp_path):
    entry = '{\n   "block": "form"\n  }'
    text = '{\n "pages": {\n  "%s": %s,\n  "%s": %s\n }\n}\n' % (PAGE, entry, PAGE, entry)
    path = tmp_path / "design-map.json"
    path.write_text(text, encoding="utf-8")
    problems = tool.duplicate_key_problems(path)
    assert len(problems) == 1
    assert PAGE in problems[0]
    assert "lines 3 and 6" in problems[0]


def test_a_map_with_each_key_once_reports_nothing(tool, tmp_path):
    path = tmp_path / "design-map.json"
    path.write_text(json.dumps({"pages": {PAGE: {"block": "form"}}}, indent=1), encoding="utf-8")
    assert tool.duplicate_key_problems(path) == []


def test_the_real_map_has_no_doubled_key(tool):
    assert tool.duplicate_key_problems() == []
