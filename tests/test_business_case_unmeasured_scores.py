"""The business case renders unmeasured dimensions as "Not measured".

A cost or vendor score of null means the input was never measured. The
generated business case must not coerce it to 0, must not raise a risk bullet
for it, and must still say no critical risk indicator was found when nothing
measured is out of range. Runs the real helpers from the script under node.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "applications" / "rationalization.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _function_source(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    depth = 0
    for i in range(source.index("{", start), len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(name)


def _render(score):
    source = SCRIPT.read_text(encoding="utf-8")
    program = "\n".join(
        [
            "const escapeForDoc = (s) => String(s);",
            "const SCORE_NOT_MEASURED = 'Not measured';",
            _function_source(source, "hasScore"),
            _function_source(source, "buildScoreTableHtml"),
            _function_source(source, "buildRiskItemsHtml"),
            "const score = JSON.parse(process.argv[1]);",
            "console.log(JSON.stringify({table: buildScoreTableHtml(score), risks: buildRiskItemsHtml(score)}));",
        ]
    )
    out = subprocess.run(
        ["node", "-e", program, json.dumps(score)], capture_output=True, text=True, check=True, timeout=30
    )
    return json.loads(out.stdout)


def test_no_cost_and_no_vendor_data_reads_not_measured_and_raises_no_risk():
    result = _render(
        {"technical_score": 80, "business_score": 60, "cost_score": None, "vendor_score": None}
    )
    table = result["table"]
    assert "Cost Efficiency</td><td>Not measured" in table
    assert "Vendor Risk</td><td>Not measured" in table
    assert "<td>0.0/100" not in table
    assert "Cost Inefficiency Risk" not in result["risks"]
    assert "Vendor Concentration Risk" not in result["risks"]
    assert "No critical risk indicators detected" in result["risks"]


def test_a_measured_low_score_still_raises_its_risk():
    result = _render(
        {"technical_score": 80, "business_score": 60, "cost_score": 10, "vendor_score": 20}
    )
    assert "Cost Inefficiency Risk" in result["risks"]
    assert "Vendor Concentration Risk" in result["risks"]
    assert "No critical risk indicators detected" not in result["risks"]
    assert "10.0/100" in result["table"]


def test_the_script_does_not_coerce_dimension_scores_to_zero():
    source = SCRIPT.read_text(encoding="utf-8")
    body = _function_source(source, "generateBusinessCase")
    assert "cost_score || 0" not in body and "vendor_score || 0" not in body
