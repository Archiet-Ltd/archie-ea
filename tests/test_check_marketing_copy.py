"""Pins scripts/check_marketing_copy.py: it must catch the defects it exists for, not merely run."""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_marketing_copy.py"


def _tree(tmp_path, body, name="content/pages/site/x.md", baseline=None):
    page = tmp_path / name
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntitle: X\nslug: roadmaps\n---\n" + body + "\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "marketing_copy_baseline.json").write_text(
        json.dumps({"guardrails": baseline or {}}), encoding="utf-8")
    return tmp_path


def _run(root, *args):
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), *args],
                          capture_output=True, text=True)


def test_clean_page_passes(tmp_path):
    assert _run(_tree(tmp_path, "Capture every decision in one place.")).returncode == 0


def test_reintroduced_quarter_fails_naming_file_line_and_guardrail(tmp_path):
    proc = _run(_tree(tmp_path, "Intro.\nOwners arrive in Q1 2027."))
    assert proc.returncode == 1
    assert "[roadmap-language]" in proc.stdout and "content/pages/site/x.md:6" in proc.stdout


def test_filler_word_fails(tmp_path):
    proc = _run(_tree(tmp_path, "Unlock seamless insight."))
    assert proc.returncode == 1 and "[generic-filler]" in proc.stdout


def test_roadmaps_module_name_and_link_targets_are_not_hits(tmp_path):
    body = "Open [Roadmaps](/modules/roadmaps) or [plans](/uc-target-state-roadmap)."
    assert _run(_tree(tmp_path, body)).returncode == 0


def test_baselined_hit_passes_but_a_new_one_in_another_file_fails(tmp_path):
    root = _tree(tmp_path, "The next quarter.", baseline={"roadmap-language": {"content/pages/site/x.md": 1}})
    assert _run(root).returncode == 0
    other = root / "content/pages/site/y.md"
    other.write_text("---\ntitle: Y\n---\nA quarter later.\n", encoding="utf-8")
    assert _run(root).returncode == 1


def test_copy_ok_marker_needs_a_reason(tmp_path):
    assert _run(_tree(tmp_path, "A quarter. copy-ok: names the billing period")).returncode == 0
    assert _run(_tree(tmp_path, "A quarter. copy-ok:")).returncode == 1


def test_update_baseline_refuses_to_rise(tmp_path):
    root = _tree(tmp_path, "A quarter.", baseline={"roadmap-language": {"content/pages/site/x.md": 0}})
    assert _run(root, "--update-baseline").returncode == 1


def test_absolute_claims_are_informational_only(tmp_path):
    root = _tree(tmp_path, "Every change is always approved.")
    assert _run(root).returncode == 0
    proc = _run(root, "--claims")
    assert proc.returncode == 0 and "UNCITED" in proc.stdout
    cited = _run(_tree(tmp_path, "Every change is approved. claim-cited: approvals.routes"), "--claims")
    assert "UNCITED" not in cited.stdout.split("\n")[0]
