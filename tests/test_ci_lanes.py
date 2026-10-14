"""Which jobs run in the fast lane and which only in the full run."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.test_ci_browser_matrix import _eval_github_expr, _strip_expr

REPO = Path(__file__).resolve().parent.parent
CI = REPO / ".github" / "workflows" / "ci.yml"

SHARD_NAME = "Tests (pytest + coverage) — shard ${{ matrix.shard }}"
REQUIRED_NAMES = {
    "static-gates": "Static gates (compile + ratchets)",
    "db-gates": "Database gates (schema drift)",
    "boot-health": "Boot health (no database)",
    "security-sast": "SAST (bandit)",
    "secret-scan": "Secret scan (gitleaks)",
    "dependency-audit": "Dependency CVEs (pip-audit)",
    "tests": "Tests (pytest + coverage)",
    "tests-shard": SHARD_NAME,
}
FULL_ONLY = [
    "tests-shard", "tests", "smoke-main", "smoke-adversarial", "smoke-other",
    "smoke", "browser-compatibility", "walkthrough", "quarantine-tests",
]
ALWAYS = ["secret-scan", "static-gates", "boot-health", "db-gates",
          "security-sast", "dependency-audit"]


def _jobs():
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _ctx(lane, event_name="pull_request", action="synchronize", labels=(), label=""):
    return {
        "github": {
            "event_name": event_name,
            "event": {
                "action": action,
                "label": {"name": label},
                "pull_request": {"labels": [{"name": n} for n in labels]},
            },
        },
        "needs": {"changes": {"result": "success",
                              "outputs": {"is_content_only": "false", "lane": lane}}},
        "_labels": list(labels),
    }


def _runs(job_id, ctx):
    expr = _strip_expr(str(_jobs()[job_id]["if"])).replace("!cancelled()", "1 == 1")
    return bool(_eval_github_expr(expr, ctx))


def test_no_required_check_was_renamed_and_the_shard_count_is_eight():
    jobs = _jobs()
    for job_id, name in REQUIRED_NAMES.items():
        assert jobs[job_id]["name"] == name, job_id
    assert jobs["tests-shard"]["strategy"]["matrix"]["shard"] == list(range(8))
    assert jobs["tests-shard"]["env"]["CI_SHARD_TOTAL"] == "8"


def test_a_fast_lane_push_runs_the_cheap_gates_and_the_changed_file_tests_only():
    ctx = _ctx("fast")
    for job_id in ALWAYS + ["fast-tests"]:
        assert _runs(job_id, ctx), job_id
    for job_id in FULL_ONLY:
        assert not _runs(job_id, ctx), f"{job_id} must be skipped (not faked) in the fast lane"


def test_the_full_lane_runs_everything_and_not_the_fast_job():
    ctx = _ctx("full", labels=["full-ci"])
    for job_id in ALWAYS + FULL_ONLY:
        assert _runs(job_id, ctx), job_id
    assert not _runs("fast-tests", ctx)


def test_adding_the_full_ci_label_triggers_the_run_and_other_labels_do_not():
    labeled = _ctx("full", action="labeled", labels=["full-ci"], label="full-ci")
    for job_id in ALWAYS + FULL_ONLY:
        assert _runs(job_id, labeled), job_id
    other = _ctx("full", action="labeled", labels=["full-ci", "bug"], label="bug")
    for job_id in ALWAYS + FULL_ONLY + ["fast-tests"]:
        assert not _runs(job_id, other), job_id


def test_an_unanswered_lane_runs_the_full_suite():
    ctx = _ctx("")
    for job_id in FULL_ONLY:
        assert _runs(job_id, ctx), job_id
    assert not _runs("fast-tests", ctx)


def test_the_fast_job_uses_the_same_runner_choice_as_the_shards():
    jobs = _jobs()
    assert jobs["fast-tests"]["runs-on"] == jobs["tests-shard"]["runs-on"]
    assert jobs["quarantine-tests"]["runs-on"] == jobs["tests-shard"]["runs-on"]


def test_release_image_still_waits_for_the_full_suite():
    jobs = _jobs()
    assert "tests" in jobs["release-image"]["needs"]
    assert jobs["release-image"]["if"] == "github.event_name != 'pull_request'"


def _lane_script():
    step = next(s for s in _jobs()["changes"]["steps"] if s.get("id") == "lane")
    return step["run"]


def _working_bash():
    if shutil.which("bash") is None:
        return False
    try:
        probe = subprocess.run(["bash", "-c", "echo ok"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.stdout.strip() == "ok"


@pytest.mark.skipif(not _working_bash(), reason="needs a working bash")
@pytest.mark.parametrize(
    "event, has_label, lane",
    [
        ("pull_request", "false", "fast"),
        ("pull_request", "true", "full"),
        ("push", "false", "full"),
        ("workflow_dispatch", "false", "full"),
    ],
)
def test_the_lane_choice(tmp_path, event, has_label, lane):
    out = tmp_path / "out"
    out.touch()
    env = {**os.environ, "EVENT_NAME": event, "HAS_FULL_CI": has_label,
           "GITHUB_OUTPUT": out.as_posix()}
    subprocess.run(["bash", "-e", "-c", _lane_script()], env=env, check=True,
                   capture_output=True)
    assert out.read_text().strip() == f"lane={lane}"


def test_the_fast_lane_selection_runs_from_the_base_branch_copy():
    step = next(s for s in _jobs()["fast-tests"]["steps"] if s.get("id") == "select")
    assert "git show" in step["run"] and "changed_test_selection.py" in step["run"]


def test_the_fast_job_script_is_safe_for_shellcheck():
    runs = "\n".join(s.get("run", "") for s in _jobs()["fast-tests"]["steps"])
    assert "$(cat fast-tests.txt)" not in runs
    assert "mapfile -t files < fast-tests.txt" in runs
    assert 'read -ra extra <<< "$EXTRA_BROWSERS"' in runs and '"${extra[@]}"' in runs
    assert "xargs -r -d '\\n' pytest" in runs
    assert "/tmp/" not in runs.replace("/tmp/archie-ci-browser-install.lock", "")
    assert "python -I" in runs


def test_the_lane_can_be_raised_to_full_by_the_selector():
    changes = _jobs()["changes"]
    assert changes["outputs"]["lane"] == "${{ steps.final.outputs.lane }}"
    step = next(s for s in changes["steps"] if s.get("id") == "final")
    assert "--escalate" in step["run"] and "lane=full" in step["run"]


def test_an_empty_selection_skips_every_expensive_fast_lane_step():
    steps = _jobs()["fast-tests"]["steps"]
    names = [s.get("name") or s.get("uses") for s in steps]
    select = next(i for i, s in enumerate(steps) if s.get("id") == "select")
    # Nothing heavy runs before the selection is known.
    for step in steps[:select]:
        assert "pip install" not in step.get("run", "")
    for step in steps[select + 1:]:
        if str(step.get("uses", "")).startswith("actions/upload-artifact"):
            continue
        assert step.get("if") == "${{ steps.select.outputs.any == 'true' }}", names
    assert 'echo "any=false"' in steps[select]["run"]
