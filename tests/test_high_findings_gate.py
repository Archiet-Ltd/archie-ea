"""The release gate on HIGH-severity static-analysis findings.

A release must not ship while a HIGH finding is open. The security-sast job
compares against .bandit-baseline.json and so fails only on NEW findings; a
HIGH one that was once baselined passed every change after it. The
`high-findings` gate counts every HIGH finding regardless of the baseline, and
honours only a `# nosec <test id> -- <reason>` a reviewer can read.

Each case seeds a minimal tree and drives the real gate function against it,
in both directions: a gate that is red for everything would pass a red-only
assertion.
"""

import pytest

pytest.importorskip("bandit", reason="bandit is pinned in requirements-test.txt")

from scripts.verify import FAIL, PASS, build_gates, gate_high_findings  # noqa: E402

HIGH = (
    "import subprocess\n"
    "\n"
    "def run(cmd):\n"
    "    return subprocess.call(cmd, shell=True){suffix}\n"
)


def _seed(tmp_path, suffix=""):
    target = tmp_path / "seeded"
    target.mkdir()
    (target / "job.py").write_text(HIGH.format(suffix=suffix), encoding="utf-8")
    return str(target)


def test_gate_fails_on_a_seeded_high_finding(tmp_path):
    result = gate_high_findings(targets=[_seed(tmp_path)])
    assert result.status == FAIL
    assert result.measured == 1
    assert "B602" in result.detail


def test_gate_passes_once_the_finding_is_gone(tmp_path):
    target = tmp_path / "clean"
    target.mkdir()
    (target / "job.py").write_text(
        "import subprocess\n\ndef run(args):\n    return subprocess.call(args)\n",
        encoding="utf-8",
    )
    result = gate_high_findings(targets=[str(target)])
    assert result.status == PASS
    assert result.measured == 0


def test_a_bare_nosec_does_not_close_a_high_finding(tmp_path):
    result = gate_high_findings(targets=[_seed(tmp_path, "  # nosec")])
    assert result.status == FAIL
    assert result.measured == 1


def test_a_reasoned_disposition_on_the_line_closes_it(tmp_path):
    suffix = "  # nosec B602 -- cmd is a constant built two lines above"
    result = gate_high_findings(targets=[_seed(tmp_path, suffix)])
    assert result.status == PASS
    assert result.measured == 0


def test_a_disposition_naming_another_test_does_not_close_it(tmp_path):
    result = gate_high_findings(targets=[_seed(tmp_path, "  # nosec B608 -- wrong check")])
    assert result.status == FAIL


def test_gate_is_registered_and_runs_in_the_static_tier():
    gate = next((g for g in build_gates({}) if g.name == "high-findings"), None)
    assert gate is not None, "high-findings is not registered in build_gates()"
    assert gate.kind == "zero"
    assert "static" in gate.tags
