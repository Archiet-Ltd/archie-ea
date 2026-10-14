"""Pins the CI fail-fast ordering and the concurrency rules.

- the heavy jobs wait for the cheap fast_gate job and skip when it fails, but
  still run when its state is unknown (skipped / cancelled / missing);
- main pushes and unlabeled PR events cancel superseded runs; nothing else does;
- no deploy-related workflow cancels a run in progress.
"""
from pathlib import Path

import yaml

from tests.test_ci_browser_matrix import _eval_github_expr, _strip_expr

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
HEAVY = ["tests-shard", "tests", "smoke-main", "smoke-adversarial", "smoke-other",
         "smoke", "browser-compatibility", "walkthrough"]
REQUIRED_NAMES = {
    "static-gates": "Static gates (compile + ratchets)",
    "boot-health": "Boot health (no database)",
    "db-gates": "Database gates (schema drift)",
    "security-sast": "SAST (bandit)",
    "secret-scan": "Secret scan (gitleaks)",
    "dependency-audit": "Dependency CVEs (pip-audit)",
}


def _ci():
    return yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))


def _needs(job):
    n = job.get("needs", [])
    return [n] if isinstance(n, str) else list(n)


def _runs(job, fast_gate_result, labels=()):
    ctx = {
        "github": {"event_name": "pull_request",
                   "event": {"action": "synchronize",
                             "pull_request": {"labels": [{"name": x} for x in labels]}}},
        "needs": {"changes": {"result": "success", "outputs": {"is_content_only": "false"}},
                  "fast_gate": {"result": fast_gate_result}},
        "_labels": list(labels),
    }
    expr = _strip_expr(str(job["if"])).replace("!cancelled()", "1 == 1")
    return bool(_eval_github_expr(expr, ctx))


def test_heavy_jobs_need_the_fast_gate_and_keep_their_guards():
    jobs = _ci()["jobs"]
    assert "fast_gate" in jobs
    for name in HEAVY:
        job = jobs[name]
        assert "fast_gate" in _needs(job), name
        assert "changes" in _needs(job), name
        expr = _strip_expr(str(job["if"]))
        assert expr.startswith("!cancelled() &&"), name
        assert "always()" not in expr and "success()" not in expr, name
        assert "github.event.action != 'labeled'" in expr, name


def test_heavy_jobs_skip_only_when_the_fast_gate_failed():
    jobs = _ci()["jobs"]
    for name in HEAVY:
        assert not _runs(jobs[name], "failure"), f"{name} must skip when fast_gate fails"
        for state in ("success", "skipped", "cancelled", ""):
            assert _runs(jobs[name], state), f"{name} must run when fast_gate is {state!r}"


def test_fast_gate_is_cheap_and_does_not_replace_a_required_check():
    jobs = _ci()["jobs"]
    fast = jobs["fast_gate"]
    assert "needs" not in fast
    text = yaml.safe_dump(fast["steps"])
    assert "--tag fast" in text
    assert "requirements.txt" not in text
    for job_id, name in REQUIRED_NAMES.items():
        assert jobs[job_id]["name"] == name
        assert "fast_gate" not in _needs(jobs[job_id]), f"{job_id} must not wait for fast_gate"
    assert fast["name"] not in REQUIRED_NAMES.values()


def test_job_names_the_merge_gate_parses_are_unchanged():
    jobs = _ci()["jobs"]
    assert jobs["tests-shard"]["name"] == "Tests (pytest + coverage) \u2014 shard ${{ matrix.shard }}"
    assert jobs["tests-shard"]["strategy"]["matrix"]["shard"] == list(range(8))
    assert jobs["changes"]["name"] == "Detect changed paths"
    assert jobs["content-page-tests"]["name"] == "Content page tests"
    assert "fast_gate" not in _needs(jobs["content-page-tests"])


def test_ci_concurrency_groups_and_cancellation():
    conc = _ci()["concurrency"]
    assert "github.event.pull_request.number || github.ref" in conc["group"]
    expr = conc["cancel-in-progress"]
    assert "github.event_name == 'pull_request' && github.event.action != 'labeled'" in expr
    assert "github.event_name == 'push' && github.ref == 'refs/heads/main'" in expr
    # evaluate: labeled PR, other-branch push and dispatch never cancel
    def cancels(event, action=None, ref="refs/heads/x"):
        ctx = {"github": {"event_name": event, "ref": ref, "event": {"action": action}}}
        return bool(_eval_github_expr(_strip_expr(expr), ctx))
    assert cancels("pull_request", "synchronize")
    assert not cancels("pull_request", "labeled")
    assert cancels("push", ref="refs/heads/main")
    assert not cancels("push", ref="refs/heads/feature")
    assert not cancels("workflow_dispatch", ref="refs/heads/main")


def test_no_deploy_workflow_cancels_in_progress():
    for path in WORKFLOWS.glob("*.yml"):
        wf = yaml.safe_load(path.read_text(encoding="utf-8"))
        conc = wf.get("concurrency")
        if path.name in ("deploy.yml", "release.yml") or "deploy" in path.name:
            assert conc and conc["cancel-in-progress"] is False, path.name
        for job in wf.get("jobs", {}).values():
            jc = job.get("concurrency")
            if jc and "deploy" in str(jc.get("group", "")):
                assert jc["cancel-in-progress"] is False, path.name


def test_every_workflow_parses():
    for path in WORKFLOWS.glob("*.yml"):
        assert yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"], path.name


def test_fast_gate_skips_on_unrelated_label_events_and_runs_for_ci_fast():
    job = _ci()["jobs"]["fast_gate"]

    def runs(action, labels):
        ctx = {"github": {"event_name": "pull_request",
                          "event": {"action": action,
                                    "pull_request": {"labels": [{"name": x} for x in labels]}}},
               "_labels": list(labels)}
        return bool(_eval_github_expr(_strip_expr(str(job["if"])), ctx))

    assert not runs("labeled", ["bug"])
    assert runs("labeled", ["ci-fast"])
    assert runs("synchronize", [])


def _actionlint_step():
    steps = _ci()["jobs"]["fast_gate"]["steps"]
    found = [s for s in steps if "actionlint" in str(s.get("run", "")) and "actionlint" in s.get("name", "").lower()]
    assert len(found) == 1, "fast_gate must have exactly one actionlint step"
    return found[0]


def test_fast_gate_lints_every_workflow_with_a_pinned_checksummed_actionlint():
    import re

    step = _actionlint_step()
    env, run = step["env"], step["run"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", env["ACTIONLINT_VERSION"]), "version must be pinned exactly"
    assert re.fullmatch(r"[0-9a-f]{64}", env["ACTIONLINT_SHA256"])
    assert "github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/" in run
    assert "sha256sum --check" in run and "ACTIONLINT_SHA256" in run
    assert "set -euo pipefail" in run
    assert ".github/workflows/*.yml" in run
    assert not re.search(r"\|\s*(ba)?sh\b", run), "never pipe a download into a shell"
    assert "continue-on-error" not in step
    # the checksum check must come before the archive is unpacked or run
    assert run.index("sha256sum") < run.index("tar -x") < run.index("/actionlint\" -color")


def test_actionlint_runs_on_workflow_only_changes():
    wf = _ci()
    triggers = wf.get("on") or wf.get(True)
    for event in ("push", "pull_request"):
        assert "paths" not in triggers[event] and "paths-ignore" not in triggers[event]
    fast = wf["jobs"]["fast_gate"]
    assert "needs" not in fast and "changes" not in str(fast["if"])
    # a workflow-only pull request (not labeled) is not skipped
    ctx = {"github": {"event_name": "pull_request",
                      "event": {"action": "synchronize", "pull_request": {"labels": []}}},
           "_labels": []}
    assert _eval_github_expr(_strip_expr(str(fast["if"])), ctx)
