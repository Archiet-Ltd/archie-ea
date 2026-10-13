"""The failing-first proof: new tests must be able to fail against the base code.

The script is exercised end to end on temporary git repositories (plan, prepare, run,
judge, with a real pytest run on the base worktree). The GitHub Actions job itself
cannot run locally; its definition is checked structurally at the end.
"""

import json
import re
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from scripts.ci import check_failing_first as ff

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Temporary repository helpers
# ---------------------------------------------------------------------------
def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _write(repo, rel, text):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _write(root, "app/__init__.py", "")
    _write(root, "app/feature.py", "def price():\n    return 1\n")
    _write(root, "tests/test_existing.py", "def test_ok():\n    assert True\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "pr")
    return root


def _commit(repo):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")


def run_check(repo, tmp_path, labels="[]"):
    """Run plan, then prepare/run/judge when the plan says so. Returns (exit code, summary text, plan mode)."""
    summary = tmp_path / "summary.md"
    plan = tmp_path / "plan.json"
    common = ["--repo", str(repo)]
    code = ff.main(
        [
            "plan",
            *common,
            "--base-ref",
            "main",
            "--labels",
            labels,
            "--plan",
            str(plan),
            "--summary",
            str(summary),
        ]
    )
    if not plan.exists():
        return code, summary.read_text(encoding="utf-8"), "done"
    dest = tmp_path / "base-worktree"
    assert ff.main(["prepare", *common, "--plan", str(plan), "--dest", str(dest)]) == 0
    assert not (dest / "app" / "feature.py").read_text(encoding="utf-8").count("def surcharge"), (
        "PR app code leaked"
    )
    junit = tmp_path / "out.xml"
    assert ff.main(["run", "--plan", str(plan), "--dest", str(dest), "--junit", str(junit)]) == 0
    code = ff.main(["judge", "--plan", str(plan), "--junit", str(junit), "--summary", str(summary)])
    return code, summary.read_text(encoding="utf-8"), "run"


def _app_change(repo):
    _write(repo, "app/feature.py", "def price():\n    return 1\n\ndef surcharge():\n    return 2\n")


# ---------------------------------------------------------------------------
# The five scenarios of the brief
# ---------------------------------------------------------------------------
def test_app_change_with_a_failing_first_test_passes(repo, tmp_path):
    _app_change(repo)
    _write(
        repo,
        "tests/test_surcharge.py",
        """
        from app.feature import surcharge, price

        def test_surcharge():
            assert surcharge() == 2

        def test_price_unchanged():
            assert price() == 1
    """,
    )
    _commit(repo)
    code, summary, mode = run_check(repo, tmp_path)
    assert (code, mode) == (0, "run")
    assert "PASSED" in summary
    # Both tests import a symbol the base lacks, so the whole file errors on collection.
    assert "test_surcharge" in summary


def test_app_change_whose_test_passes_on_base_fails_and_lists_it(repo, tmp_path):
    _app_change(repo)
    _write(
        repo,
        "tests/test_vacuous.py",
        """
        from app.feature import price

        def test_price():
            assert price() == 1
    """,
    )
    _commit(repo)
    code, summary, _ = run_check(repo, tmp_path)
    assert code == 1
    assert "FAILED" in summary
    assert "Also pass on base" in summary and "test_price" in summary
    assert "files added by this PR" in summary


def test_mixed_file_passes_and_warns_about_the_test_that_also_passes_on_base(repo, tmp_path):
    _app_change(repo)
    _write(
        repo,
        "tests/test_mixed.py",
        """
        from app import feature

        def test_new_behaviour():
            assert feature.surcharge() == 2

        def test_vacuous():
            assert feature.price() == 1
    """,
    )
    _commit(repo)
    code, summary, _ = run_check(repo, tmp_path)
    assert code == 0
    assert "PASSED" in summary
    assert "Failed or errored on base (1)" in summary
    assert "candidates for vacuous tests (1)" in summary and "test_vacuous" in summary


def test_no_application_change_passes_with_a_note(repo, tmp_path):
    _write(repo, "docs/readme.md", "hello\n")
    _write(repo, "content/pages/a.md", "x\n")
    _commit(repo)
    code, summary, mode = run_check(repo, tmp_path)
    assert (code, mode) == (0, "done")
    assert "No application code changed" in summary


def test_test_only_change_passes_with_a_note_that_tests_were_not_run(repo, tmp_path):
    _write(repo, "tests/test_more.py", "def test_x():\n    assert True\n")
    _commit(repo)
    code, summary, mode = run_check(repo, tmp_path)
    assert (code, mode) == (0, "done")
    assert (
        "No application code changed" in summary
        and "1 changed test file(s) were not run" in summary
    )


def test_exempt_label_passes_even_without_tests(repo, tmp_path):
    _app_change(repo)
    _commit(repo)
    code, summary, mode = run_check(repo, tmp_path, labels=json.dumps(["bug", ff.EXEMPT_LABEL]))
    assert (code, mode) == (0, "done")
    assert "Exempted" in summary


# ---------------------------------------------------------------------------
# Edge cases from the brief
# ---------------------------------------------------------------------------
def test_app_change_with_no_changed_tests_fails_with_a_clear_message(repo, tmp_path):
    _app_change(repo)
    _commit(repo)
    code, summary, mode = run_check(repo, tmp_path)
    assert (code, mode) == (1, "done")
    assert "no runnable test file was added or changed" in summary
    assert ff.EXEMPT_LABEL in summary


def test_a_missing_module_or_table_on_base_counts_as_failing(repo, tmp_path):
    _write(repo, "app/newmod.py", "VALUE = 3\n")
    _write(
        repo,
        "tests/test_newmod.py",
        "from app.newmod import VALUE\n\ndef test_value():\n    assert VALUE == 3\n",
    )
    _commit(repo)
    code, summary, _ = run_check(repo, tmp_path)
    assert code == 0 and "PASSED" in summary


def test_more_than_forty_test_files_are_capped_and_the_cap_is_stated(repo, tmp_path):
    _app_change(repo)
    for i in range(45):
        _write(repo, f"tests/test_many_{i:02d}.py", "def test_x():\n    assert True\n")
    _commit(repo)
    summary = tmp_path / "summary.md"
    plan = tmp_path / "plan.json"
    code = ff.main(
        [
            "plan",
            "--repo",
            str(repo),
            "--base-ref",
            "main",
            "--labels",
            "[]",
            "--plan",
            str(plan),
            "--summary",
            str(summary),
        ]
    )
    assert code == 0
    data = json.loads(plan.read_text(encoding="utf-8"))
    assert len(data["test_files"]) == 40 and data["capped"] == 5
    assert "Capped at 40: 5 more" in summary.read_text(encoding="utf-8")


def test_test_support_files_are_overlaid_but_not_run(repo, tmp_path):
    _app_change(repo)
    _write(repo, "tests/helpers.py", "def helper():\n    return 7\n")
    _write(
        repo,
        "tests/test_uses_helper.py",
        "from tests.helpers import helper\nfrom app.feature import surcharge\n\ndef test_h():\n    assert helper() == 7 and surcharge()\n",
    )
    _commit(repo)
    plan = tmp_path / "plan.json"
    ff.main(
        [
            "plan",
            "--repo",
            str(repo),
            "--base-ref",
            "main",
            "--labels",
            "[]",
            "--plan",
            str(plan),
            "--summary",
            str(tmp_path / "s.md"),
        ]
    )
    data = json.loads(plan.read_text(encoding="utf-8"))
    assert data["test_files"] == ["tests/test_uses_helper.py"]
    assert data["support_files"] == ["tests/helpers.py"]


def test_the_plan_uses_the_merge_base_not_the_moved_base_tip(repo, tmp_path):
    _app_change(repo)
    _write(
        repo,
        "tests/test_s.py",
        "from app.feature import surcharge\n\ndef test_s():\n    assert surcharge()\n",
    )
    _commit(repo)
    _git(repo, "checkout", "-q", "main")
    _write(repo, "app/other.py", "X = 1\n")  # main moves on after the branch point
    _commit(repo)
    _git(repo, "checkout", "-q", "pr")
    plan = tmp_path / "plan.json"
    ff.main(
        [
            "plan",
            "--repo",
            str(repo),
            "--base-ref",
            "main",
            "--labels",
            "[]",
            "--plan",
            str(plan),
            "--summary",
            str(tmp_path / "s.md"),
        ]
    )
    data = json.loads(plan.read_text(encoding="utf-8"))
    expected = subprocess.run(
        ["git", "merge-base", "main", "pr"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert data["merge_base"] == expected


def test_judge_fails_on_a_missing_report(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"added_files": [], "test_files": [], "capped": 0}), encoding="utf-8"
    )
    assert (
        ff.main(
            [
                "judge",
                "--plan",
                str(plan),
                "--junit",
                str(tmp_path / "none.xml"),
                "--summary",
                str(tmp_path / "s.md"),
            ]
        )
        == 1
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path, app",
    [
        ("app/modules/x/routes.py", True),
        ("app/templates/base.html", True),
        ("app/static/app.js", True),
        ("app/modules/x/tests/test_routes.py", False),
        ("app/modules/x/tests/helpers.py", False),
        ("app/templates/public/pricing.html", False),
        ("app/modules/x/README.md", False),
        ("tests/test_x.py", False),
        ("migrations/versions/abc.py", False),
        ("scripts/ci/x.py", False),
        ("docs/a.md", False),
    ],
)
def test_application_code_classification(path, app):
    assert ff.is_app_code(path) is app


@pytest.mark.parametrize(
    "path, runnable",
    [
        ("tests/test_x.py", True),
        ("tests/sub/test_x.py", True),
        ("app/modules/x/tests/test_x.py", True),
        ("tests/smoke/test_x.py", False),
        ("tests/conftest.py", False),
        ("tests/helpers.py", False),
        ("app/modules/x/test_x.py", False),
        ("tests/test_x.txt", False),
    ],
)
def test_runnable_test_file_classification(path, runnable):
    assert ff.is_test_file(path) is runnable


# ---------------------------------------------------------------------------
# The CI job definition
# ---------------------------------------------------------------------------
def _job():
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    return workflow["jobs"]["failing-first-proof"]


def test_the_job_name_cannot_be_mistaken_for_a_test_shard():
    name = _job()["name"]
    assert name == "Failing-first proof"
    # The merge gate recognises the pytest shards by this pattern (and a substring).
    assert not re.match(r"^Tests \(pytest \+ coverage\) .{1,3} shard ([0-7])$", name)
    assert "Tests (pytest + coverage)" not in name


def test_the_job_is_informational_and_pull_request_only():
    from scripts import deploy_workflow as dw

    job = _job()
    assert "failing-first-proof" not in " ".join(
        str(j.get("needs", ""))
        for k, j in yaml.safe_load(
            (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )["jobs"].items()
        if k != "failing-first-proof"
    ), "no other job may depend on it"
    assert "github.event_name == 'pull_request'" in job["if"]
    assert job["name"] in dw.EXCLUDED_CHECKS and job["name"] not in dw.REQUIRED_CHECKS


def test_the_job_runs_the_script_in_order_with_the_labels_from_the_event():
    job = _job()
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    order = [runs.index(f"check_failing_first.py {c}") for c in ("plan", "prepare", "run", "judge")]
    assert order == sorted(order)
    assert "failing-first-exempt" in job["if"] or "PR_LABELS" in json.dumps(job)
