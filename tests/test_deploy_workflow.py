"""The production deploy workflow and its pre-flight helpers.

Nothing here talks to production, to GitHub or to a droplet. The workflow file
is checked statically, the pre-flight logic is exercised with a fake GitHub API
and a real throwaway git repository, and the ssh material is written into a
temporary directory.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
MODULE_PATH = ROOT / "scripts" / "deploy_workflow.py"

spec = importlib.util.spec_from_file_location("deploy_workflow_under_test", MODULE_PATH)
dw = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dw
spec.loader.exec_module(dw)

GOOD_SHA = "a" * 40
OTHER_SHA = "b" * 40


def load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def all_steps(workflow: dict):
    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            yield job_name, step


# ---------------------------------------------------------------------------
# The workflow file
# ---------------------------------------------------------------------------
def test_triggers_only_on_manual_dispatch_with_the_two_inputs():
    workflow = load_workflow()
    triggers = workflow.get(True, workflow.get("on"))  # PyYAML reads a bare `on` as True

    assert list(triggers) == ["workflow_dispatch"]
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"ref", "dry_run"}
    assert inputs["ref"]["required"] is True
    assert inputs["ref"]["type"] == "string"
    assert inputs["dry_run"]["type"] == "boolean"
    assert inputs["dry_run"]["default"] is True


def test_no_expression_reaches_a_shell_body():
    """Untrusted input must be passed through env:, never interpolated."""
    offenders = [
        (job, step.get("name"))
        for job, step in all_steps(load_workflow())
        if "${{" in step.get("run", "")
    ]
    assert offenders == []
    assert not re.search(r"\$\{\{\s*(github\.event\.)?inputs\.", "\n".join(
        step.get("run", "") for _, step in all_steps(load_workflow())
    ))


def test_secrets_are_referenced_only_by_the_one_step_in_the_deploy_job():
    workflow = load_workflow()
    text = WORKFLOW.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))

    assert re.findall(r"secrets\.([A-Za-z0-9_]+)", code) == ["DROPLET_DEPLOY_KEY", "DROPLET_KNOWN_HOSTS"]
    assert "secrets." not in json.dumps({k: v for k, v in workflow.items() if k != "jobs"})
    assert "secrets." not in json.dumps(workflow["jobs"]["preflight"])
    assert "secrets." not in json.dumps(workflow["jobs"]["deploy"].get("env", {}))
    holders = [s.get("id") for job, s in all_steps(workflow) if "secrets." in json.dumps(s)]
    assert holders == ["ssh_setup"]
    assert "DROPLET_SSH_KEY" not in text  # the read-only log-scan key is never reused


def test_only_the_deploy_job_uses_the_protected_environment():
    jobs = load_workflow()["jobs"]

    assert jobs["deploy"]["environment"]["name"] == "production"
    assert "environment" not in jobs["preflight"]
    assert jobs["deploy"]["needs"] == "preflight"


def test_concurrency_timeouts_and_least_privilege_permissions():
    workflow = load_workflow()

    assert workflow["concurrency"] == {"group": "production-deploy", "cancel-in-progress": False}
    assert workflow["permissions"] == {"contents": "read"}
    for name, job in workflow["jobs"].items():
        assert isinstance(job["timeout-minutes"], int) and 0 < job["timeout-minutes"] <= 120, name
        assert set(job["permissions"].values()) == {"read"}, name
        assert set(job["permissions"]) <= {"contents", "checks", "actions"}, name


def test_ssh_is_strict_pinned_and_never_traced():
    text = WORKFLOW.read_text(encoding="utf-8")
    script = MODULE_PATH.read_text(encoding="utf-8")

    for source in (text, script):
        assert "StrictHostKeyChecking no" not in source
        assert "StrictHostKeyChecking=no" not in source
        assert "ssh-keyscan" not in re.sub(r"#.*", "", source)
        assert "set -x" not in source
        assert "xtrace" not in source
    assert "StrictHostKeyChecking yes" in dw.render_ssh_config("/tmp/x")
    assert "UserKnownHostsFile" in dw.render_ssh_config("/tmp/x")
    assert "--logs" not in text  # post_deploy_verify --logs uses StrictHostKeyChecking=no


def test_dry_run_baseline_and_deploy_share_one_health_budget_and_every_bound_is_enforced():
    workflow = load_workflow()
    steps = {s.get("id"): s for _, s in all_steps(workflow)}

    budgets = {sid: steps[sid]["env"]["HEALTH_TIMEOUT_SECONDS"] for sid in ("dry", "baseline", "deploy")}
    assert set(budgets.values()) == {"900"}, budgets   # a slow container must not cost the deploy its rollback target
    health_minutes = int(budgets["baseline"]) / 60
    for sid in ("dry", "baseline"):
        bound = steps[sid]["timeout-minutes"]           # enforced by Actions, not assumed
        assert isinstance(bound, int) and bound >= health_minutes + 5, sid
    # A real run is baseline then deploy (the dry run is the alternative to both).
    sequence = steps["baseline"]["timeout-minutes"] + steps["deploy"]["timeout-minutes"]
    assert sequence + 5 <= workflow["jobs"]["deploy"]["timeout-minutes"]


def test_the_first_ssh_step_filters_the_droplets_stderr_like_the_others():
    steps = {s.get("id"): s for _, s in all_steps(load_workflow())}
    body = steps["ssh"]["run"]

    assert '2>"$LOG_DIR/ssh-check.err"' in body
    assert 'filter-log < "$LOG_DIR/ssh-check.err"' in body
    for sid in ("dry", "baseline", "deploy"):
        assert "filter-log" in steps[sid]["run"]


def test_the_key_is_removed_by_an_always_step():
    steps = list(load_workflow()["jobs"]["deploy"]["steps"])
    last = steps[-1]

    assert last["if"] == "always()"
    assert "deploy-ssh" in last["run"] and "rm -rf" in last["run"]


def test_deploy_verified_is_wrapped_with_skip_deploy_only_in_dry_run():
    steps = {s.get("id"): s for _, s in all_steps(load_workflow())}
    dry, baseline, deploy = steps["dry"], steps["baseline"], steps["deploy"]

    assert dry["if"] == "steps.gate.outputs.mode == 'dry-run'"
    assert "scripts/deploy_verified.sh" in dry["run"] and "--skip-deploy" in dry["run"]
    assert baseline["if"] == "steps.gate.outputs.mode == 'deploy'"
    assert "--skip-deploy" in baseline["run"]
    assert deploy["if"] == "steps.gate.outputs.mode == 'deploy'"
    assert "--skip-deploy" not in deploy["run"]
    assert deploy["env"]["AUTO_ROLLBACK"] == "1"
    assert "steps.gate.outputs.sha" in deploy["env"]["DEPLOY_SHA"]
    assert steps["post"]["run"] == "python3 scripts/post_deploy_verify.py --json"
    assert steps["post"]["if"] == "steps.gate.outputs.mode == 'deploy'"


def test_actions_are_pinned_to_a_full_commit_sha():
    for _, step in all_steps(load_workflow()):
        if "uses" in step:
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["uses"]


def test_both_checkouts_leave_the_job_token_out_of_the_git_config():
    """With persist-credentials left on, the runner's .git/config would hold the job
    token in jobs that go on to run shell scripts and an ssh wrapper."""
    checkouts = [(job, step) for job, step in all_steps(load_workflow()) if step.get("uses", "").startswith("actions/checkout@")]

    assert [job for job, _ in checkouts] == ["preflight", "deploy"]
    for job, step in checkouts:
        assert step["with"].get("persist-credentials") is False, job


def test_both_jobs_run_the_preflight_and_only_from_main():
    workflow = load_workflow()
    runs = [s["run"] for _, s in all_steps(workflow) if "preflight" in s.get("run", "")]

    assert len(runs) == 2
    for run in runs:
        assert "--require-branch main" in run and "--check-environment production" in run


def ci_job_names() -> set:
    ci = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    names = set()
    for job in ci["jobs"].values():
        template = job["name"]
        matrix = job.get("strategy", {}).get("matrix", {})
        if "${{ matrix." in template:
            key = re.search(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", template).group(1)
            for value in matrix[key]:
                # YAML types matrix values: `shard: [0, 1]` yields ints, and a
                # check run's name carries each value as text.
                names.add(re.sub(r"\$\{\{[^}]*\}\}", str(value), template))
        else:
            names.add(template)
    return names


def test_every_ci_job_is_required_or_deliberately_excluded():
    required, excluded = set(dw.REQUIRED_CHECKS), set(dw.EXCLUDED_CHECKS)

    assert not required & excluded
    assert ci_job_names() == required | excluded   # a new, renamed or removed job fails here until decided


def test_the_only_exclusion_is_the_image_build_and_it_says_why():
    assert set(dw.EXCLUDED_CHECKS) == {"Build immutable release image"}
    reason = dw.EXCLUDED_CHECKS["Build immutable release image"]
    assert "GHCR" in reason and "bind-mounted" in reason
    # Everything else the CI runs is required, including the jobs that are red today.
    for name in ("Tests (pytest + coverage)", "SAST (bandit)", "Browser journeys (one per archetype)",
                 "Browser compatibility (webkit)", "Browser compatibility (firefox)"):
        assert name in dw.REQUIRED_CHECKS
    for shard in range(8):
        assert f"Tests (pytest + coverage) \u2014 shard {shard}" in dw.REQUIRED_CHECKS
    assert len(dw.REQUIRED_CHECKS) == 19


@pytest.mark.parametrize("dry_run", ["true", "false"])
def test_there_is_no_path_that_skips_the_ci_requirement_for_dry_runs(preflight_env, dry_run):
    """Dry runs hold the same key and run the same script; they get the same pre-flight."""
    env, _, out = preflight_env
    env["DEPLOY_DRY_RUN"] = dry_run
    runs = all_green()
    runs[4] = run(dw.REQUIRED_CHECKS[4], "failure", run_id=91)
    code, text = preflight(env, FakeGitHub(runs=runs))

    assert code == 1 and dw.REQUIRED_CHECKS[4] in text and not out.exists()


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    ["", "main", "origin/main", "v1.0.0", "abc1234", "A" * 40, "a" * 39, "a" * 41,
     "g" * 40, GOOD_SHA + "\n", " " + GOOD_SHA, GOOD_SHA + " ", GOOD_SHA + ";id", "$(id)" + "a" * 35],
)
def test_ref_that_is_not_a_full_lowercase_sha_is_refused(value):
    assert dw.validate_ref(value)


def test_a_full_lowercase_sha_is_accepted():
    assert dw.validate_ref(GOOD_SHA) is None
    assert dw.validate_ref("0123456789abcdef" * 2 + "01234567") is None


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False), ("", None), ("True", None), ("1", None), ("yes", None)])
def test_dry_run_is_exactly_true_or_false(value, expected):
    assert dw.parse_dry_run(value) is expected


# ---------------------------------------------------------------------------
# CI evidence
# ---------------------------------------------------------------------------
def run(name, conclusion="success", status="completed", run_id=1, app="github-actions"):
    return {"id": run_id, "name": name, "status": status, "conclusion": conclusion, "app": {"slug": app}}


def all_green():
    return [run(name, run_id=i + 1) for i, name in enumerate(dw.REQUIRED_CHECKS)]


def test_all_required_checks_green_passes():
    assert dw.evaluate_checks(all_green()) == []


def test_a_missing_required_check_is_refused():
    runs = [r for r in all_green() if r["name"] != "SAST (bandit)"]
    problems = dw.evaluate_checks(runs)
    assert len(problems) == 1 and "SAST (bandit)" in problems[0]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "neutral", "timed_out", None])
def test_a_check_that_did_not_succeed_is_refused(conclusion):
    runs = all_green()
    runs[3] = run(dw.REQUIRED_CHECKS[3], conclusion=conclusion, run_id=99)
    assert dw.evaluate_checks(runs)


def test_a_check_that_is_still_running_is_refused():
    runs = all_green()
    runs[0] = run(dw.REQUIRED_CHECKS[0], status="in_progress", conclusion=None, run_id=99)
    problems = dw.evaluate_checks(runs)
    assert problems and "not finished" in problems[0]


def test_the_newest_run_of_a_check_decides():
    name = dw.REQUIRED_CHECKS[1]
    reran_green = [r for r in all_green() if r["name"] != name] + [
        run(name, "failure", run_id=5), run(name, "success", run_id=50)]
    reran_red = [r for r in all_green() if r["name"] != name] + [
        run(name, "success", run_id=5), run(name, "failure", run_id=50)]

    assert dw.evaluate_checks(reran_green) == []
    assert dw.evaluate_checks(reran_red)


def test_a_check_published_by_another_app_does_not_count():
    runs = [run(n, app="some-other-app", run_id=i + 1) for i, n in enumerate(dw.REQUIRED_CHECKS)]
    assert len(dw.evaluate_checks(runs)) == len(dw.REQUIRED_CHECKS)


# ---------------------------------------------------------------------------
# Fake GitHub API
# ---------------------------------------------------------------------------
class FakeResponse(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload).encode())
        self.status = status
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code, message="nope", headers=None):
    body = io.BytesIO(json.dumps({"message": message}).encode())
    return urllib.error.HTTPError(url, code, message, headers or {}, body)


class FakeGitHub:
    """Answers the three endpoints the pre-flight uses."""

    def __init__(self, runs=None, shadow=None, environment=None, checks_error=None, shadow_error=None):
        self.runs = all_green() if runs is None else runs
        self.shadow = shadow or []
        self.environment = environment
        self.checks_error = checks_error
        self.shadow_error = shadow_error
        self.requests = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.requests.append(url)
        assert request.headers["Authorization"] == "Bearer test-token"
        if "/check-runs" in url:
            if self.checks_error:
                raise http_error(url, self.checks_error)
            page = int(re.search(r"[?&]page=(\d+)", url).group(1))
            chunk = self.runs[(page - 1) * 100: page * 100]
            return FakeResponse({"total_count": len(self.runs), "check_runs": chunk})
        if "/git/matching-refs/" in url:
            if self.shadow_error:
                raise http_error(url, self.shadow_error)
            prefix = "refs/" + url.split("/git/matching-refs/", 1)[1]      # matching-refs is a prefix match
            return FakeResponse([r for r in self.shadow if r["ref"].startswith(prefix)])
        if "/environments/" in url:
            if isinstance(self.environment, Exception):
                raise self.environment
            if isinstance(self.environment, int):
                raise http_error(url, self.environment)
            return FakeResponse(self.environment)
        raise AssertionError("unexpected request " + url)


def protected_environment(reviewers=1, branch_policy=True, reviewers_key=True):
    rules = [{"type": "branch_policy"}]
    if reviewers is not None and reviewers_key:
        rules.append({"type": "required_reviewers", "reviewers": [{"type": "User"}] * reviewers})
    elif reviewers is not None:
        rules.append({"type": "required_reviewers"})              # a rule that does not say who
    return {
        "name": "production",
        "protection_rules": rules,
        "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True} if branch_policy else None,
    }


def test_pagination_collects_every_page():
    runs = [run("n%d" % i, run_id=i + 1) for i in range(230)]
    fetched, problem = dw.fetch_check_runs("o/r", GOOD_SHA, "test-token", "https://api.example.test", FakeGitHub(runs=runs))
    assert problem == "" and len(fetched) == 230


def test_a_checks_api_error_is_a_refusal_not_a_pass():
    fetched, problem = dw.fetch_check_runs("o/r", GOOD_SHA, "test-token", "https://api.example.test", FakeGitHub(checks_error=403))
    assert fetched is None and "403" in problem


def test_a_non_https_api_url_is_refused():
    assert dw.api_get("http://api.example.test/x", "t").status == 0


def test_a_transport_failure_never_raises():
    def boom(request, timeout=None):
        raise TimeoutError("timed out")
    assert dw.api_get("https://api.example.test/x", "t", boom).status == 0


# ---------------------------------------------------------------------------
# Ancestry, against a real throwaway repository
# ---------------------------------------------------------------------------
def git_in(path, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test"}
    result = subprocess.run(["git", *args], cwd=path, env=env, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    """main = A -> B (origin/main points at B); side = A -> C, not on main."""
    path = tmp_path / "repo"
    path.mkdir()
    git_in(path, "init", "-q", "-b", "main")
    commits = {}
    for name in ("A", "B"):
        (path / "f.txt").write_text(name)
        git_in(path, "add", "f.txt")
        git_in(path, "commit", "-q", "-m", name)
        commits[name] = git_in(path, "rev-parse", "HEAD")
    git_in(path, "update-ref", "refs/remotes/origin/main", commits["B"])
    git_in(path, "checkout", "-q", "-b", "side", commits["A"])
    (path / "f.txt").write_text("C")
    git_in(path, "commit", "-q", "-am", "C")
    commits["C"] = git_in(path, "rev-parse", "HEAD")
    git_in(path, "checkout", "-q", "main")
    return path, commits


def test_a_commit_on_main_is_an_ancestor(repo):
    path, commits = repo
    for name in ("A", "B"):
        assert dw.check_ancestry(commits[name], "origin/main", runner=lambda a, **k: subprocess.run(a, cwd=path, **k)) == []


def test_a_commit_off_main_is_refused(repo):
    path, commits = repo
    problems = dw.check_ancestry(commits["C"], "origin/main", runner=lambda a, **k: subprocess.run(a, cwd=path, **k))
    assert problems and "not an ancestor" in problems[0]


def test_a_commit_that_does_not_exist_is_refused(repo):
    path, _ = repo
    problems = dw.check_ancestry("f" * 40, "origin/main", runner=lambda a, **k: subprocess.run(a, cwd=path, **k))
    assert problems and "not present" in problems[0]


def test_a_missing_origin_main_is_refused_not_assumed(repo):
    path, commits = repo
    git_in(path, "update-ref", "-d", "refs/remotes/origin/main")
    problems = dw.check_ancestry(commits["A"], "origin/main", runner=lambda a, **k: subprocess.run(a, cwd=path, **k))
    assert problems and "cannot resolve" in problems[0]


# ---------------------------------------------------------------------------
# The whole pre-flight
# ---------------------------------------------------------------------------
@pytest.fixture()
def preflight_env(repo, tmp_path, monkeypatch):
    path, commits = repo
    monkeypatch.chdir(path)
    out = tmp_path / "github_output"
    summary = tmp_path / "github_summary"
    env = {
        "DEPLOY_REF": commits["A"],
        "DEPLOY_DRY_RUN": "true",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_TOKEN": "test-token",
        "GITHUB_API_URL": "https://api.example.test",
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    return env, commits, out


def preflight(env, api, **flags):
    args = SimpleNamespace(check_environment=flags.get("environment", ""), require_branch=flags.get("branch", "main"))
    lines = []
    code = dw.run_preflight(env, args, opener=api, out=lines.append)
    return code, "\n".join(lines)


def test_a_valid_request_passes_and_publishes_the_sha_and_mode(preflight_env):
    env, commits, out = preflight_env
    code, text = preflight(env, FakeGitHub())

    assert code == 0 and "PRE-FLIGHT OK" in text
    assert out.read_text().splitlines() == ["sha=%s" % commits["A"], "mode=dry-run"]


def test_a_real_deploy_request_publishes_deploy_mode(preflight_env):
    env, commits, out = preflight_env
    env["DEPLOY_DRY_RUN"] = "false"
    code, _ = preflight(env, FakeGitHub())

    assert code == 0 and out.read_text().splitlines()[1] == "mode=deploy"


@pytest.mark.parametrize("bad", ["main", "abc123", "A" * 40, "a" * 41, "$(id)"])
def test_a_ref_that_is_not_a_full_sha_is_refused_and_nothing_is_published(preflight_env, bad):
    env, _, out = preflight_env
    env["DEPLOY_REF"] = bad
    api = FakeGitHub()
    code, text = preflight(env, api)

    assert code == 1 and "PRE-FLIGHT REFUSED" in text
    assert not out.exists() and api.requests == []  # refused before any API call


def test_a_commit_not_on_main_is_refused(preflight_env):
    env, commits, out = preflight_env
    env["DEPLOY_REF"] = commits["C"]
    code, text = preflight(env, FakeGitHub())

    assert code == 1 and "not an ancestor" in text and not out.exists()


def test_a_commit_without_a_green_required_ci_run_is_refused(preflight_env):
    env, _, out = preflight_env
    runs = all_green()
    runs[2] = run(dw.REQUIRED_CHECKS[2], "failure", run_id=77)
    code, text = preflight(env, FakeGitHub(runs=runs))

    assert code == 1 and dw.REQUIRED_CHECKS[2] in text and "%d of %d" % (len(dw.REQUIRED_CHECKS) - 1, len(dw.REQUIRED_CHECKS)) in open(env["GITHUB_STEP_SUMMARY"]).read()
    assert not out.exists()


def test_a_commit_with_no_ci_at_all_is_refused(preflight_env):
    env, _, _ = preflight_env
    code, text = preflight(env, FakeGitHub(runs=[]))
    assert code == 1 and text.count("no CI check named") == len(dw.REQUIRED_CHECKS)


def test_dispatch_from_a_branch_other_than_main_is_refused(preflight_env):
    env, _, _ = preflight_env
    env["GITHUB_REF"] = "refs/heads/feature"
    code, text = preflight(env, FakeGitHub())
    assert code == 1 and "must be dispatched from main" in text


def test_an_ambiguous_dry_run_value_is_refused(preflight_env):
    env, _, _ = preflight_env
    env["DEPLOY_DRY_RUN"] = ""
    code, text = preflight(env, FakeGitHub())
    assert code == 1 and "dry_run must be exactly" in text


@pytest.mark.parametrize("template", ["refs/heads/%s", "refs/tags/%s", "refs/tags/origin/%s", "refs/heads/origin/%s"])
def test_a_branch_or_tag_named_like_the_sha_is_refused(preflight_env, template):
    """Branches, tags, and tags named origin/<sha> (which git resolves ahead of a
    remote-tracking branch) are all refused, whichever namespace they are in."""
    env, commits, out = preflight_env
    code, text = preflight(env, FakeGitHub(shadow=[{"ref": template % commits["A"]}]))

    assert code == 1 and "a ref named like the SHA exists" in text and not out.exists()


@pytest.mark.parametrize("template", ["refs/heads/%s-revert", "refs/tags/%s-rc1"])
def test_a_ref_that_only_starts_with_the_sha_is_refused_and_the_message_says_how_to_clear_it(preflight_env, template):
    """The refs lookup is a prefix match, so a longer name is caught too."""
    env, commits, out = preflight_env
    code, text = preflight(env, FakeGitHub(shadow=[{"ref": template % commits["A"]}]))

    assert code == 1 and not out.exists()
    assert "merely starts with the SHA" in text and "rename or delete that ref" in text


def test_refs_that_merely_share_a_short_prefix_do_not_block(preflight_env):
    env, commits, _ = preflight_env
    code, _ = preflight(env, FakeGitHub(shadow=[{"ref": "refs/tags/v1.0"}, {"ref": "refs/heads/" + commits["B"][:8]}]))
    assert code == 0


def test_a_failing_refs_lookup_is_a_refusal(preflight_env):
    env, _, _ = preflight_env
    code, text = preflight(env, FakeGitHub(shadow_error=500))
    assert code == 1 and "could not check refs named like the SHA" in text


def test_the_sha_of_a_tag_object_is_refused(repo):
    path, commits = repo
    git_in(path, "tag", "-a", "-m", "release", "rel", commits["B"])
    tag_object = git_in(path, "rev-parse", "refs/tags/rel")
    assert tag_object != commits["B"]

    problems = dw.check_ancestry(tag_object, "origin/main", runner=lambda a, **k: subprocess.run(a, cwd=path, **k))
    assert problems and "not itself a commit" in problems[0]


def test_the_checks_api_failing_is_a_refusal(preflight_env):
    env, _, _ = preflight_env
    code, text = preflight(env, FakeGitHub(checks_error=500))
    assert code == 1 and "checks API returned HTTP 500" in text


def test_the_token_never_appears_in_output_or_summary(preflight_env):
    env, _, _ = preflight_env
    env["DEPLOY_REF"] = "main"
    _, text = preflight(env, FakeGitHub())
    assert "test-token" not in text and "test-token" not in open(env["GITHUB_STEP_SUMMARY"]).read()


# --- environment protection --------------------------------------------------
def test_an_environment_with_reviewers_and_a_branch_policy_passes(preflight_env):
    env, _, out = preflight_env
    code, text = preflight(env, FakeGitHub(environment=protected_environment()), environment="production")
    assert code == 0 and "environment: verified" in text and out.exists()


def test_an_environment_without_required_reviewers_is_refused(preflight_env):
    env, _, out = preflight_env
    code, text = preflight(env, FakeGitHub(environment=protected_environment(reviewers=None)), environment="production")
    assert code == 1 and "no required reviewers" in text and not out.exists()


def test_an_environment_with_an_empty_reviewer_list_is_refused(preflight_env):
    env, _, _ = preflight_env
    code, text = preflight(env, FakeGitHub(environment=protected_environment(reviewers=0)), environment="production")
    assert code == 1 and "no required reviewers" in text


def test_an_environment_open_to_every_branch_is_refused(preflight_env):
    env, _, _ = preflight_env
    code, text = preflight(env, FakeGitHub(environment=protected_environment(branch_policy=False)), environment="production")
    assert code == 1 and "any branch" in text


def test_a_missing_environment_is_refused(preflight_env):
    env, _, _ = preflight_env
    code, text = preflight(env, FakeGitHub(environment=404), environment="production")
    assert code == 1 and "does not exist" in text


@pytest.mark.parametrize(
    "answer",
    [403, 500, 502, 429, TimeoutError("timed out"), urllib.error.URLError("no route")],
    ids=["403", "500", "502", "429", "timeout", "transport"],
)
def test_an_environment_that_cannot_be_read_is_refused(preflight_env, answer):
    """The reviewer requirement cannot be confirmed, so the request is refused."""
    env, _, out = preflight_env
    code, text = preflight(env, FakeGitHub(environment=answer), environment="production")

    assert code == 1 and not out.exists()
    assert "could not be read" in text and "refused because the required-reviewer setting cannot be confirmed" in text
    assert "REFUSED" in open(env["GITHUB_STEP_SUMMARY"]).read()
    assert "NOT CHECKED" not in text and "::warning" not in text


def test_a_required_reviewers_rule_that_names_nobody_is_not_protection(preflight_env):
    env, _, out = preflight_env
    code, text = preflight(env, FakeGitHub(environment=protected_environment(reviewers_key=False)), environment="production")

    assert code == 1 and "no required reviewers" in text and not out.exists()
    state, _ = dw.evaluate_environment(dw.ApiResponse(200, protected_environment(reviewers_key=False), {}), "production")
    assert state == "unprotected"


def test_a_request_refused_on_ci_still_reports_the_environment_result(preflight_env):
    """The rehearsal in the runbook: with no commit passing CI, one dispatch still
    shows whether the environment is set up, and never reaches the droplet."""
    env, _, out = preflight_env
    runs = all_green()
    runs[0] = run(dw.REQUIRED_CHECKS[0], "failure", run_id=90)
    code, _ = preflight(env, FakeGitHub(runs=runs, environment=protected_environment()), environment="production")
    ok_summary = open(env["GITHUB_STEP_SUMMARY"]).read()
    code_open, _ = preflight(env, FakeGitHub(runs=runs, environment=protected_environment(reviewers=None)), environment="production")
    all_summaries = open(env["GITHUB_STEP_SUMMARY"]).read()

    assert code == 1 and code_open == 1 and not out.exists()
    assert "Environment protection: verified (required reviewers: 1" in ok_summary
    assert "Environment protection: REFUSED: environment 'production' has no required reviewers" in all_summaries


def test_the_summary_says_when_the_environment_was_not_evaluated(preflight_env):
    env, _, _ = preflight_env
    env["DEPLOY_REF"] = "main"
    preflight(env, FakeGitHub(), environment="production")
    asked = open(env["GITHUB_STEP_SUMMARY"]).read()
    open(env["GITHUB_STEP_SUMMARY"], "w").close()
    preflight(env, FakeGitHub(), environment="")
    not_asked = open(env["GITHUB_STEP_SUMMARY"]).read()

    assert "not evaluated (the request was refused before the environment was read)" in asked
    assert "Environment protection: not requested" in not_asked and "not evaluated" not in not_asked.split("Environment")[1]


def test_evaluate_environment_reports_reviewer_count():
    state, detail = dw.evaluate_environment(dw.ApiResponse(200, protected_environment(reviewers=2), {}), "production")
    assert state == "ok" and "required reviewers: 2" in detail


# ---------------------------------------------------------------------------
# SSH material
# ---------------------------------------------------------------------------
SECRET_MARKER = "DO-NOT-PRINT-THIS-KEY-BODY"
# Built from parts so that no source line looks like a committed private key to
# the repository's secret scanner.
PEM_BEGIN = "-----BEGIN OPENSSH" + " PRIVATE KEY-----"
PEM_END = "-----END OPENSSH" + " PRIVATE KEY-----"
FAKE_KEY = "\n".join([PEM_BEGIN, SECRET_MARKER, PEM_END])
HOST = "134.122.105.56"
KNOWN = "%s ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExampleExample1" % HOST


def keygen_ok(*a, **k):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def keygen_bad(*a, **k):
    return SimpleNamespace(returncode=1, stdout="", stderr="invalid format")


def test_ssh_material_is_written_pinned_and_private(tmp_path):
    target = tmp_path / "ssh"
    problems = dw.setup_ssh(str(target), HOST, "root", FAKE_KEY, KNOWN, real_ssh="/usr/bin/ssh", keygen=keygen_ok)

    assert problems == []
    config = (target / "config").read_text()
    assert "StrictHostKeyChecking yes" in config and "IdentitiesOnly yes" in config
    assert str(target) in config and "UpdateHostKeys no" in config and "ForwardAgent no" in config
    assert (target / "known_hosts").read_text().strip() == KNOWN
    assert (target / "id_deploy").read_text().endswith("KEY-----\n")
    wrapper = (target / "bin" / "ssh").read_text()
    assert wrapper.startswith("#!/bin/sh") and "/usr/bin/ssh" in wrapper and "-F" in wrapper
    if os.name == "posix":
        assert (target / "id_deploy").stat().st_mode & 0o777 == 0o600
        assert (target / "known_hosts").stat().st_mode & 0o777 == 0o600
        assert target.stat().st_mode & 0o777 == 0o700


def test_crlf_and_missing_trailing_newline_in_the_secret_are_normalised(tmp_path):
    key = FAKE_KEY.replace("\n", "\r\n")
    assert dw.setup_ssh(str(tmp_path / "s"), HOST, "root", key, KNOWN, real_ssh="/usr/bin/ssh", keygen=keygen_ok) == []
    text = (tmp_path / "s" / "id_deploy").read_bytes()
    assert b"\r" not in text and text.endswith(b"\n")


@pytest.mark.parametrize(
    "key",
    ["", "   \n", "not a key", "\n".join([PEM_BEGIN, "ENCRYPTED", PEM_END])],
)
def test_a_missing_or_unusable_key_is_refused(tmp_path, key):
    problems = dw.setup_ssh(str(tmp_path / "s"), HOST, "root", key, KNOWN, real_ssh="/usr/bin/ssh", keygen=keygen_ok)
    assert problems and not (tmp_path / "s").exists()


def test_a_key_that_ssh_keygen_cannot_parse_is_refused_and_removed(tmp_path):
    if not shutil.which("ssh-keygen"):
        pytest.skip("ssh-keygen not installed")
    problems = dw.setup_ssh(str(tmp_path / "s"), HOST, "root", FAKE_KEY, KNOWN, real_ssh="/usr/bin/ssh")
    assert problems and "could not be parsed" in problems[0] and not (tmp_path / "s").exists()


@pytest.mark.parametrize(
    "known",
    [
        "",
        "# only a comment",
        "1.2.3.4 ssh-ed25519 AAAAexample",                              # a different host
        "|1|abcd=|efgh= ssh-ed25519 AAAAexample",                       # hashed entry
        "@cert-authority %s ssh-ed25519 AAAAexample" % HOST,             # CA marker
        "%s ssh-ed25519" % HOST,                                         # no key
        "%s not-a-key-type AAAAexample" % HOST,
        KNOWN + "\n1.2.3.4 ssh-ed25519 AAAAexample",                    # one good, one foreign
        "%s,evil.example ssh-ed25519 AAAAexample" % HOST,                # a host list that includes the droplet
        "evil.example,%s ssh-ed25519 AAAAexample" % HOST,
        "*.105.56 ssh-ed25519 AAAAexample",                             # a pattern
    ],
)
def test_the_pinned_host_key_must_be_plain_and_for_this_host_only(tmp_path, known):
    problems = dw.setup_ssh(str(tmp_path / "s"), HOST, "root", FAKE_KEY, known, real_ssh="/usr/bin/ssh", keygen=keygen_ok)
    assert problems and not (tmp_path / "s").exists()


def test_secrets_are_never_printed_by_the_setup_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DROPLET_DEPLOY_KEY", FAKE_KEY)
    monkeypatch.setenv("DROPLET_KNOWN_HOSTS", KNOWN)
    monkeypatch.setattr(dw.shutil, "which", lambda name: "/usr/bin/" + name if name != "ssh-keygen" else None)
    code = dw.main(["setup-ssh", "--dir", str(tmp_path / "s"), "--host", HOST])
    captured = capsys.readouterr()

    assert code == 0
    assert SECRET_MARKER not in captured.out + captured.err


def test_a_refused_setup_never_echoes_the_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DROPLET_DEPLOY_KEY", "BEGIN-but-broken " + SECRET_MARKER)
    monkeypatch.setenv("DROPLET_KNOWN_HOSTS", KNOWN)
    code = dw.main(["setup-ssh", "--dir", str(tmp_path / "s"), "--host", HOST])
    captured = capsys.readouterr()

    assert code == 1 and SECRET_MARKER not in captured.out + captured.err


def test_the_wrapper_puts_every_pinning_option_before_the_callers_arguments():
    wrapper = dw.render_ssh_wrapper("/usr/bin/ssh", "/d")
    caller_position = wrapper.rindex('"$@"')

    options = dw.hard_ssh_options("/d")
    assert ("StrictHostKeyChecking", "yes") in options and ("UserKnownHostsFile", "/d/known_hosts") in options
    for key, value in options:
        marker = "-o %s=%s" % (key, value)
        assert marker in wrapper and wrapper.index(marker) < caller_position, key
    assert wrapper.rstrip().endswith('"$@"')


@pytest.mark.skipif(
    shutil.which("ssh") is None or shutil.which("ssh-keygen") is None or shutil.which("sh") is None,
    reason="needs an OpenSSH client and sh",
)
def test_a_command_line_override_cannot_loosen_host_key_checking(tmp_path):
    """OpenSSH keeps the first value it obtains, and -o is obtained before -F. A config
    file alone is therefore overridable; the wrapper's leading options are what hold."""
    key_file = tmp_path / "k"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key_file)], check=True)
    target = tmp_path / "ssh"
    assert dw.setup_ssh(target.as_posix(), HOST, "root", key_file.read_text(), KNOWN) == []

    def resolved(command):
        out = subprocess.run(command, capture_output=True, text=True, check=True).stdout.lower().splitlines()
        return {line.partition(" ")[0]: line.partition(" ")[2] for line in out}

    overrides = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                 "-o", "BatchMode=no", "-o", "ForwardAgent=yes", "-o", "UpdateHostKeys=yes"]
    control = resolved(["ssh", "-F", str(target / "config"), *overrides, "-G", "root@" + HOST])
    through_wrapper = resolved(["sh", str(target / "bin" / "ssh"), *overrides, "-G", "root@" + HOST])

    assert control["stricthostkeychecking"] == "false"                    # the config alone loses to -o
    assert through_wrapper["stricthostkeychecking"] == "true"
    assert through_wrapper["userknownhostsfile"].endswith("known_hosts")
    assert through_wrapper["batchmode"] in ("yes", "true")
    assert through_wrapper["forwardagent"] in ("no", "false")
    assert through_wrapper["updatehostkeys"] in ("no", "false")


@pytest.mark.skipif(shutil.which("ssh") is None or shutil.which("ssh-keygen") is None, reason="needs an OpenSSH client")
def test_openssh_resolves_the_generated_config_to_strict_pinned_identity_only(tmp_path):
    """`ssh -G` prints the effective configuration without connecting anywhere."""
    key_file = tmp_path / "k"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key_file)], check=True)
    target = tmp_path / "ssh"
    problems = dw.setup_ssh(str(target), HOST, "root", key_file.read_text(), KNOWN)
    assert problems == []

    resolved = subprocess.run(
        ["ssh", "-F", str(target / "config"), "-G", "root@" + HOST], capture_output=True, text=True, check=True
    ).stdout.lower().splitlines()
    settings = {}
    for line in resolved:
        key, _, value = line.partition(" ")
        settings.setdefault(key, []).append(value)

    assert settings["stricthostkeychecking"] == ["true"]
    assert settings["identitiesonly"] == ["yes"] or settings["identitiesonly"] == ["true"]
    assert settings["batchmode"] == ["yes"] or settings["batchmode"] == ["true"]
    assert settings["passwordauthentication"] == ["no"] or settings["passwordauthentication"] == ["false"]
    assert settings["forwardagent"] == ["no"] or settings["forwardagent"] == ["false"]
    assert settings["updatehostkeys"] == ["no"] or settings["updatehostkeys"] == ["false"]
    assert len(settings["identityfile"]) == 1 and settings["identityfile"][0].endswith("id_deploy")
    assert len(settings["userknownhostsfile"]) == 1 and settings["userknownhostsfile"][0].endswith("known_hosts")


# ---------------------------------------------------------------------------
# Log handling
# ---------------------------------------------------------------------------
def filtered(lines):
    shown = []
    withheld = dw.filter_stream(lines, shown.append)
    return shown, withheld


def test_only_the_scripts_own_status_lines_pass_the_filter():
    lines = [
        "== deploying %s to root@134.122.105.56:/root/archie-ea" % GOOD_SHA,
        "RESOLVED_COMMIT=" + GOOD_SHA,
        "OK: container reports healthy",
        "/root/archie-ea -> /app",
        "/root/archie-ea -> /appOK: bind mount is present and correct",  # the script omits the newline before OK
        'version endpoint: {"build_id": "aaaaaaaa"}',
        "DEPLOY VERIFIED: commit %s is running, mounted and reachable." % GOOD_SHA,
        "DEPLOY-VERIFY FAIL: container did not report healthy within 900s (last status: unhealthy)",
        "Host key verification failed.",
    ]
    shown, withheld = filtered(lines)
    assert shown == lines and withheld == 0


def test_everything_else_the_droplet_prints_is_withheld():
    lines = [
        "database-acl-1  | postgres://app:hunter2@db:5432/archie",
        "Traceback (most recent call last):",
        "SECRET_KEY=abcdef",
        "::error::injected workflow command",
        "::add-mask::x",
        "   OK: indented lines are not the script's own",
        "",
        "OK: real status line",
    ]
    shown, withheld = filtered(lines)

    assert shown == ["OK: real status line"]
    assert withheld == 6  # the blank line is not counted
    assert not any(line.startswith("::") for line in shown)


def test_the_filter_survives_binary_junk_and_never_raises():
    shown, _ = filtered(["\x00\x01\xff", "OK: fine", None])  # None forces the exception path
    assert shown == ["OK: fine"]


def test_the_filter_drains_its_input_even_if_stdout_is_gone(monkeypatch):
    class Closed:
        def write(self, _):
            raise BrokenPipeError

        def flush(self):
            raise BrokenPipeError

    monkeypatch.setattr(dw.sys, "stdout", Closed())
    monkeypatch.setattr(dw.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"OK: a\nOK: b\nnoise\n")))
    assert dw.cmd_filter_log(None) == 0


VERIFIED_OUT = "== summary\nDEPLOY VERIFIED: commit %s is running, mounted and reachable.\n" % GOOD_SHA


@pytest.mark.parametrize(
    "text,rc,expected",
    [
        (VERIFIED_OUT, 0, ("verified", "not-attempted")),
        (VERIFIED_OUT, 1, ("contradiction", "unknown")),
        ("nothing useful\n", 0, ("contradiction", "unknown")),
        (VERIFIED_OUT.replace(GOOD_SHA, OTHER_SHA), 0, ("contradiction", "unknown")),
        ("== AUTO-ROLLBACK: no different known-good commit on record (x) -- nothing to roll back to\n"
         "DEPLOY NOT VERIFIED — see FAIL lines above.\n", 1, ("failed", "no-known-good")),
        ("== AUTO-ROLLBACK: r (resolved x) failed verification -- redeploying last known-good commit y\n"
         "DEPLOY FAILED, ROLLED BACK SUCCESSFULLY: r did not verify\n", 1, ("rolled_back", "succeeded")),
        ("== AUTO-ROLLBACK: r (resolved x) failed verification -- redeploying last known-good commit y\n"
         "CRITICAL: r did not verify, AND the automatic rollback to y ALSO failed to verify.\n", 1, ("rollback_failed", "failed")),
        ("DEPLOY NOT VERIFIED — see FAIL lines above.\n", 1, ("failed", "not-attempted")),
        ("", 255, ("failed", "not-attempted")),
    ],
)
def test_classification_of_the_scripts_outcome(text, rc, expected):
    assert dw.classify_log(text, rc, GOOD_SHA) == expected


def test_a_droplet_line_cannot_fake_a_verified_result():
    """The success line must start the line; text echoed inside other output does not count."""
    text = "noise: DEPLOY VERIFIED: commit %s is running\n" % GOOD_SHA
    assert dw.classify_log(text, 0, GOOD_SHA)[0] == "contradiction"


# ---------------------------------------------------------------------------
# Job summary
# ---------------------------------------------------------------------------
def summary_env(**overrides):
    base = {
        "SUMMARY_SHA": GOOD_SHA, "SUMMARY_MODE": "deploy", "SUMMARY_BEFORE_SHA": OTHER_SHA,
        "SUMMARY_AFTER_SHA": GOOD_SHA, "SUMMARY_GATE_OUTCOME": "success", "SUMMARY_SSH_OUTCOME": "success",
        "SUMMARY_DRY_OUTCOME": "skipped", "SUMMARY_BASELINE": "passed", "SUMMARY_DEPLOY_OUTCOME": "success",
        "SUMMARY_DEPLOY_RESULT": "verified", "SUMMARY_ROLLBACK": "not-attempted", "SUMMARY_POST_OUTCOME": "success",
        "SUMMARY_RUN_URL": "https://github.com/o/r/actions/runs/1",
    }
    base.update(overrides)
    return base


def test_summary_states_every_required_fact():
    text = dw.render_summary(summary_env())
    for fragment in (GOOD_SHA, OTHER_SHA, "real deploy", "Rollback", "https://github.com/o/r/actions/runs/1",
                     "Deployed and verified"):
        assert fragment in text


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"SUMMARY_MODE": "dry-run", "SUMMARY_DRY_OUTCOME": "success", "SUMMARY_DEPLOY_OUTCOME": "skipped",
          "SUMMARY_DEPLOY_RESULT": "", "SUMMARY_POST_OUTCOME": "skipped", "SUMMARY_BASELINE": ""},
         "Dry run passed"),
        ({"SUMMARY_MODE": "dry-run", "SUMMARY_DRY_OUTCOME": "failure", "SUMMARY_DEPLOY_OUTCOME": "skipped",
          "SUMMARY_DEPLOY_RESULT": ""}, "Dry run failed"),
        ({"SUMMARY_POST_OUTCOME": "failure"}, "public-page check failed. Not reported as success"),
        ({"SUMMARY_DEPLOY_OUTCOME": "failure", "SUMMARY_DEPLOY_RESULT": "rolled_back", "SUMMARY_ROLLBACK": "succeeded",
          "SUMMARY_POST_OUTCOME": "skipped"}, "rolled back"),
        ({"SUMMARY_DEPLOY_OUTCOME": "failure", "SUMMARY_DEPLOY_RESULT": "rollback_failed", "SUMMARY_ROLLBACK": "failed"},
         "CRITICAL"),
        ({"SUMMARY_DEPLOY_OUTCOME": "failure", "SUMMARY_DEPLOY_RESULT": "failed", "SUMMARY_ROLLBACK": "no-known-good",
          "SUMMARY_BASELINE": "failed"}, "No rollback was possible"),
        ({"SUMMARY_GATE_OUTCOME": "failure", "SUMMARY_MODE": ""}, "Refused before any change"),
        ({"SUMMARY_SSH_OUTCOME": "failure", "SUMMARY_DEPLOY_OUTCOME": "skipped"}, "SSH setup or the connection to the droplet failed"),
    ],
)
def test_summary_verdicts(overrides, expected):
    assert expected in dw.render_summary(summary_env(**overrides))


def test_summary_never_renders_unvalidated_text():
    text = dw.render_summary(summary_env(SUMMARY_SHA="<script>x</script>", SUMMARY_BEFORE_SHA="; rm -rf /",
                                          SUMMARY_RUN_URL="javascript:alert(1)"))
    assert "<script>" not in text and "rm -rf" not in text and "javascript:" not in text


# ---------------------------------------------------------------------------
# Documentation contract: the claims the runbook must keep making plainly
# ---------------------------------------------------------------------------
RUNBOOK = ROOT / "deploy" / "DEPLOY_WORKFLOW.md"
CLAUDE_MD = ROOT / "CLAUDE.md"


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_approval_step_is_described_as_what_it_is_on_the_first_page():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    first_page = flat(runbook.split("## What it does")[0])
    header = flat(WORKFLOW.read_text(encoding="utf-8").replace("# ", " ").split("run-name:")[0])
    approval = flat(runbook.split("## How approval works")[1].split("## Rollback")[0])

    for text in (first_page, header):
        assert "confirmation prompt with an audit trail" in text
        assert "not a separation of duties" in text
    assert "Decide first" in first_page
    assert "confirmation prompt" in approval and "second account" in approval


def test_the_decision_comes_before_setup_and_names_both_options():
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert runbook.index("## Decide first") < runbook.index("## One-time setup")
    decision = runbook.split("## Decide first")[1].split("## One-time setup")[0]
    assert "Option (a)" in decision and "Option (b)" in decision and "prevent self-review" in decision
    assert '"prevent_self_review": $PREVENT_SELF_REVIEW' in runbook
    assert "Recommended" not in runbook


def test_setup_puts_the_rehearsal_before_the_key_and_the_secrets():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    order = [runbook.index(h) for h in (
        "### 1. Create the `production` environment", "### 2. Merge the workflow", "### 3. Rehearse the guard",
        "### 4. Create a dedicated deploy key", "### 5. Authorise the key", "### 7. Add the two secrets")]

    assert order == sorted(order)
    rehearsal = flat(runbook.split("### 3. Rehearse the guard")[1].split("### 4.")[0])
    assert "never reaches the droplet" in rehearsal and "no key or secret exists" in rehearsal
    assert "Environment protection: verified" in rehearsal
    assert "could not be read" in rehearsal and "refused" in rehearsal
    assert "not evaluated (the request was refused before the environment was read)" in rehearsal
    assert "says nothing about the environment" in rehearsal


def test_the_runbook_says_red_ci_refuses_every_dispatch_and_who_decides():
    runbook = flat(RUNBOOK.read_text(encoding="utf-8"))

    assert "every dispatch is refused" in runbook and "dry runs included" in runbook
    assert "every real dispatch is refused by design until CI on `main` is green" in runbook
    for job in ("Tests (pytest + coverage)", "SAST (bandit)", "Browser journeys (one per archetype)",
                "Browser compatibility (webkit)"):
        assert job in runbook
    assert "decision for the repository owner" in runbook
    assert "no path that skips the CI requirement for dry runs" in runbook
    assert "Build immutable release image" in runbook and "GHCR" in runbook


def test_the_runbook_covers_key_rotation_after_an_unexplained_run_and_the_parked_run():
    runbook = flat(RUNBOOK.read_text(encoding="utf-8"))

    assert "unexplained run" in runbook and "rotating the key" in runbook and "read the private key" in runbook
    assert "gh run cancel" in runbook and "Reject or cancel the parked run first" in runbook


def test_the_runbook_limits_match_what_the_filter_and_the_tests_can_claim():
    runbook = flat(RUNBOOK.read_text(encoding="utf-8"))

    assert "matches by line prefix and cannot tell who wrote a line" in runbook
    assert "not a guarantee against a hostile droplet" in runbook
    assert "protects against mistakes, not against someone with write access" in runbook
    assert "only run on POSIX" in runbook and "Linux runner in CI" in runbook


def test_claude_md_pointer_lets_a_session_dispatch_but_not_approve_its_own_run():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    paragraph = flat(text[text.index("**Deploying without droplet SSH.**"):text.index("## Schema management")])

    assert "may dispatch but must not approve its own production run" in paragraph
    assert "deploy-in-the-same-session rule" in paragraph
    assert "not one GitHub enforces" in paragraph
    assert "Rehearse first" in paragraph
    assert paragraph.index("-f dry_run=true") < paragraph.index("-f dry_run=false")   # the safe form leads
    assert "Do not end a session by offering deployment as a menu option" in flat(text)   # the existing rule is intact


# ---------------------------------------------------------------------------
# First-parent history of main
# ---------------------------------------------------------------------------
@pytest.fixture()
def merged_repo(tmp_path):
    """main = A -> B -> M, where M merges the branch side (A -> S1 -> S2) with --no-ff.

    A, B and M are first-parent commits of main. S1 and S2 are ancestors of main
    only through the merge. U sits on a branch that was never merged.
    """
    path = tmp_path / "merged"
    path.mkdir()
    git_in(path, "init", "-q", "-b", "main")
    commits = {}

    def commit(name):
        (path / (name + ".txt")).write_text(name)     # one file per commit, so the merge cannot conflict
        git_in(path, "add", name + ".txt")
        git_in(path, "commit", "-q", "-m", name)
        commits[name] = git_in(path, "rev-parse", "HEAD")

    commit("A")
    commit("B")
    git_in(path, "checkout", "-q", "-b", "side", commits["A"])
    commit("S1")
    commit("S2")
    git_in(path, "checkout", "-q", "-b", "other", commits["A"])
    commit("U")
    git_in(path, "checkout", "-q", "main")
    git_in(path, "merge", "-q", "--no-ff", "-m", "M", "side")
    commits["M"] = git_in(path, "rev-parse", "HEAD")
    git_in(path, "update-ref", "refs/remotes/origin/main", commits["M"])
    return path, commits


def in_repo(path):
    return lambda a, **k: subprocess.run(a, cwd=path, **k)


def test_commits_on_the_first_parent_chain_of_main_pass(merged_repo):
    path, commits = merged_repo
    for name in ("A", "B", "M"):
        assert dw.check_ancestry(commits[name], "origin/main", runner=in_repo(path)) == [], name
        assert dw.check_first_parent(commits[name], "origin/main", runner=in_repo(path)) == [], name


def test_a_commit_that_reached_main_only_through_a_merged_branch_is_refused(merged_repo):
    path, commits = merged_repo
    for name in ("S1", "S2"):
        assert dw.check_ancestry(commits[name], "origin/main", runner=in_repo(path)) == [], name   # an ancestor ...
        problems = dw.check_first_parent(commits[name], "origin/main", runner=in_repo(path))
        assert len(problems) == 1 and "first-parent" in problems[0] and "merged branch" in problems[0], name   # ... but refused


def test_an_unrelated_commit_is_refused_as_not_an_ancestor(merged_repo):
    path, commits = merged_repo
    problems = dw.check_ancestry(commits["U"], "origin/main", runner=in_repo(path))
    assert problems and "not an ancestor" in problems[0]


def test_a_history_that_cannot_be_listed_is_a_refusal(merged_repo):
    path, commits = merged_repo
    problems = dw.check_first_parent(commits["A"], "origin/no-such-branch", runner=in_repo(path))
    assert problems and "cannot prove" in problems[0]


@pytest.fixture()
def merged_env(merged_repo, tmp_path, monkeypatch):
    path, commits = merged_repo
    monkeypatch.chdir(path)
    out = tmp_path / "github_output"
    summary = tmp_path / "github_summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    env = {
        "DEPLOY_REF": commits["M"], "DEPLOY_DRY_RUN": "false", "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_REF": "refs/heads/main", "GITHUB_TOKEN": "test-token", "GITHUB_API_URL": "https://api.example.test",
    }
    return env, commits, out


def test_the_preflight_refuses_a_merged_branch_commit_even_when_every_check_is_green(merged_env):
    env, commits, out = merged_env
    env["DEPLOY_REF"] = commits["S2"]
    code, text = preflight(env, FakeGitHub())   # all required checks green for that SHA

    assert code == 1 and "first-parent" in text and not out.exists()


def test_the_preflight_accepts_the_merge_commit_and_a_first_parent_commit(merged_env):
    env, commits, out = merged_env
    for name in ("M", "B"):
        env["DEPLOY_REF"] = commits[name]
        code, text = preflight(env, FakeGitHub())
        assert code == 0 and "PRE-FLIGHT OK" in text, name
    assert out.read_text().splitlines()[-2:] == ["sha=%s" % commits["B"], "mode=deploy"]


def test_the_preflight_refuses_an_unrelated_commit_with_the_ancestor_message_only(merged_env):
    env, commits, _ = merged_env
    env["DEPLOY_REF"] = commits["U"]
    code, text = preflight(env, FakeGitHub())

    assert code == 1 and "not an ancestor" in text and "first-parent history" not in text


def test_the_runbook_states_the_first_parent_rule_the_prefix_rule_and_the_enforced_bounds():
    runbook = flat(RUNBOOK.read_text(encoding="utf-8"))
    steps = {s.get("id"): s for _, s in all_steps(load_workflow())}
    job_minutes = load_workflow()["jobs"]["deploy"]["timeout-minutes"]

    assert "first-parent history" in runbook and "Deploy the merge commit instead" in runbook
    assert "no push-triggered CI run ever ran on its own tree" in runbook
    assert "a first-parent commit of `main` with green CI" in runbook
    assert "`<sha>-revert`" in runbook and "the refs lookup is a prefix match" in runbook
    assert "renaming or deleting that ref clears the refusal" in runbook
    assert "bracketed `[<ip>]:<port>` form" in runbook
    # The numbers in the runbook are the ones the workflow enforces.
    assert "stopped after %d minutes" % job_minutes in runbook
    assert "each stopped after %d" % steps["baseline"]["timeout-minutes"] in runbook
    assert "the deploy step after %d" % steps["deploy"]["timeout-minutes"] in runbook
