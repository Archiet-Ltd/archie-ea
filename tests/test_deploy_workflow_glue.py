"""Run the deploy workflow's own shell steps against a fake droplet.

The deploy job's `run:` bodies are executed the way GitHub runs them (bash
-eo pipefail, GITHUB_OUTPUT / GITHUB_ENV / GITHUB_STEP_SUMMARY files, earlier
step outputs substituted into `env:`), with the fake droplet from
test_deploy_verified_fake_droplet.py behind `ssh`. This checks the glue between
the steps: baseline, deploy, classification, the job summary and cleanup.

Not executed here: checkout, the pre-flight (needs the GitHub API; covered in
test_deploy_workflow.py), installing the key (needs a real sshd), and the
public-page check (it would call the live site). Its outcome is injected.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_SIBLING = Path(__file__).with_name("test_deploy_verified_fake_droplet.py")
_spec = importlib.util.spec_from_file_location("deploy_fake_droplet_support", _SIBLING)
support = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = support
_spec.loader.exec_module(support)

ROOT = support.ROOT
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
BASH = support.BASH
SKIPPED_STEPS = {"gate", "ssh_setup", "post"}

pytestmark = pytest.mark.skipif(BASH is None or shutil.which("git") is None, reason="needs bash and git")


@pytest.fixture()
def droplet(tmp_path):
    return support.FakeDroplet(tmp_path)


def sha(droplet, name):
    return droplet.commits[name]


class Job:
    def __init__(self, droplet, mode: str, requested: str, post_outcome: str = "success"):
        self.d = droplet
        self.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.steps = self.workflow["jobs"]["deploy"]["steps"]
        self.mode, self.sha, self.post_outcome = mode, requested, post_outcome
        self.temp = droplet.tmp / "runner_temp"
        self.temp.mkdir()
        self.ws = droplet.tmp / "workspace"
        (self.ws / "scripts").mkdir(parents=True)
        shutil.copy(support.HELPERS, self.ws / "scripts" / "deploy_workflow.py")
        (self.ws / "scripts" / "deploy_verified.sh").write_bytes(support.SCRIPT.read_bytes().replace(b"\r\n", b"\n"))
        # Cross-organisation check script called by deploy_verified.sh step 6
        cross_org = ROOT / "scripts" / "deploy_verify_cross_org.py"
        if cross_org.is_file():
            shutil.copy(cross_org, self.ws / "scripts" / "deploy_verify_cross_org.py")
        # Stand-in for the wrapper that `setup-ssh` installs.
        (self.temp / "deploy-ssh" / "bin").mkdir(parents=True)
        shutil.copy(droplet.bin / "ssh", self.temp / "deploy-ssh" / "bin" / "ssh")
        self.files = {n: droplet.tmp / ("gh_" + n) for n in ("output", "env", "path", "summary")}
        for f in self.files.values():
            f.write_text("")
        self.outputs: dict = {"gate": {"sha": requested, "mode": mode}}
        self.outcomes: dict = {"gate": "success", "ssh_setup": "success", "post": "skipped"}
        self.failed = False
        self.transcript: dict = {}
        self.extra_env: dict = {}
        artifact_dir = self.temp / "release-artifact"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "release.json").write_text(
            (
                '{\n'
                '  "commit": "%s",\n'
                '  "digest": "sha256:%s",\n'
                '  "image": "ghcr.io/anioko/archie@sha256:%s",\n'
                '  "schema_version": 1,\n'
                '  "workflow_run": "1"\n'
                '}\n'
            )
            % (requested, "a" * 64, "a" * 64),
            encoding="utf-8",
        )

    def _expr(self, match) -> str:
        expr = match.group(1).strip()
        m = re.fullmatch(r"steps\.(\w+)\.outputs\.(\w+)", expr)
        if m:
            return self.outputs.get(m.group(1), {}).get(m.group(2), "")
        m = re.fullmatch(r"steps\.(\w+)\.outcome", expr)
        if m:
            return self.outcomes.get(m.group(1), "skipped")
        return {
            "github.server_url": "https://github.com",
            "github.repository": "o/r",
            "github.run_id": "1",
            "github.token": "test-token",
            "runner.temp": self.temp.as_posix(),
        }[expr]

    def _should_run(self, step: dict) -> bool:
        cond = step.get("if")
        if cond is None:
            return not self.failed
        if cond == "always()":
            return True
        m = re.fullmatch(r"steps\.(\w+)\.outputs\.(\w+) == '([\w-]+)'", cond)
        assert m, "unsupported condition " + cond
        return self.outputs.get(m.group(1), {}).get(m.group(2), "") == m.group(3) and not self.failed

    def run_step(self, key: str):
        step = next(s for s in self.steps if key in (s.get("id"), s.get("name"), s.get("uses")))
        sid = step.get("id")
        if sid in SKIPPED_STEPS or "uses" in step:
            return None
        if not self._should_run(step):
            if sid:
                self.outcomes[sid] = "skipped"
            return None
        env = {
            **os.environ,
            "PATH": os.pathsep.join([str(self.temp / "deploy-ssh" / "bin"), str(self.d.bin), os.environ["PATH"]]),
            "RUNNER_TEMP": self.temp.as_posix(),
            "GITHUB_OUTPUT": self.files["output"].as_posix(),
            "GITHUB_ENV": self.files["env"].as_posix(),
            "GITHUB_PATH": self.files["path"].as_posix(),
            "GITHUB_STEP_SUMMARY": self.files["summary"].as_posix(),
            "FAKE_STATE": self.d.state.as_posix(),
            "DROPLET": "root@fake-droplet",
            "APP_DIR": self.d.app.as_posix(),
        }
        for line in self.files["env"].read_text().splitlines():  # what earlier steps put in GITHUB_ENV
            name, _, value = line.partition("=")
            env[name] = value
        for name, value in step.get("env", {}).items():
            env[name] = re.sub(r"\$\{\{([^}]*)\}\}", self._expr, str(value))
        # The workflow's own HEALTH_TIMEOUT_SECONDS (900) is used as written. The fake `date`
        # advances by this many "seconds" per call, so an unhealthy container exhausts the
        # budget after two polls whatever the machine is doing.
        env["FAKE_CLOCK_STEP"] = "300"
        env.update(self.extra_env)
        assert "${{" not in step["run"]
        script = self.ws / "step.sh"
        script.write_text(step["run"], encoding="utf-8", newline="\n")
        before = self.files["output"].read_text()
        result = support.run_bounded(
            [BASH, "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
            cwd=self.ws, env=env, timeout=900,
        )
        written = self.files["output"].read_text()[len(before):]
        if sid:
            self.outputs[sid] = dict(line.split("=", 1) for line in written.splitlines() if "=" in line)
            self.outcomes[sid] = "success" if result.returncode == 0 else "failure"
        if result.returncode != 0:
            self.failed = True
        self.transcript[sid or step["name"]] = result.stdout + result.stderr
        return result

    def run_all(self):
        """Every step in order, with GitHub's skip-after-failure semantics."""
        for step in self.steps:
            if step.get("id") == "post":
                if self.mode == "deploy" and not self.failed:
                    self.outcomes["post"] = self.post_outcome
                    self.failed = self.post_outcome == "failure"
                continue
            if "uses" in step:
                continue
            self.run_step(step.get("id") or step["name"])
        return self

    def summary(self) -> str:
        return self.files["summary"].read_text()


def test_dry_run_verifies_what_is_running_and_changes_nothing(droplet):
    job = Job(droplet, "dry-run", sha(droplet, "B")).run_all()

    assert job.outcomes["dry"] == "success" and job.outcomes["ssh"] == "success"
    assert job.outputs["ssh"]["before_sha"] == sha(droplet, "A")
    assert "Dry run passed" in job.summary()
    assert sha(droplet, "A") in job.summary() and sha(droplet, "B") in job.summary()
    assert droplet.running() == sha(droplet, "A")
    assert not any(c.startswith("docker compose") for c in droplet.docker_calls())
    assert not (job.temp / "deploy-ssh").exists()  # the always() step removed the key directory


def test_dry_run_fails_when_production_is_unhealthy(droplet):
    droplet.mark_bad("A")
    job = Job(droplet, "dry-run", sha(droplet, "B")).run_all()

    assert job.outcomes["dry"] == "failure"
    assert "Dry run failed" in job.summary()
    assert not any(c.startswith("docker compose") for c in droplet.docker_calls())


def test_real_deploy_succeeds_only_when_the_script_verifies_the_requested_commit(droplet):
    job = Job(droplet, "deploy", sha(droplet, "B")).run_all()

    assert job.outputs["baseline"]["result"] == "passed"
    assert job.outputs["release"]["image"] == "ghcr.io/anioko/archie@sha256:%s" % ("a" * 64)
    assert job.outcomes["deploy"] == "success"
    assert job.outputs["deploy"] == {"deploy_result": "verified", "rollback": "not-attempted"}
    assert droplet.running() == sha(droplet, "B")
    assert "Deployed and verified" in job.summary()
    assert "Running after | `%s`" % sha(droplet, "B") in job.summary()


def test_release_manifest_must_match_the_requested_commit(droplet):
    job = Job(droplet, "deploy", sha(droplet, "B"))
    (job.temp / "release-artifact" / "release.json").write_text(
        (
            '{\n'
            '  "commit": "%s",\n'
            '  "digest": "sha256:%s",\n'
            '  "image": "ghcr.io/anioko/archie@sha256:%s",\n'
            '  "schema_version": 1,\n'
            '  "workflow_run": "1"\n'
            '}\n'
        )
        % (sha(droplet, "A"), "a" * 64, "a" * 64),
        encoding="utf-8",
    )

    for name in ("Prepare runner directories", "ssh", "baseline", "release"):
        result = job.run_step(name)

    assert result.returncode != 0
    assert job.outcomes["release"] == "failure"
    assert "does not match requested commit" in job.transcript["release"]
    assert "deploy" not in job.outcomes


def test_a_failed_deploy_rolls_back_and_the_run_is_red(droplet):
    droplet.mark_bad("B")
    job = Job(droplet, "deploy", sha(droplet, "B")).run_all()

    assert job.outputs["baseline"]["result"] == "passed"
    assert job.outcomes["deploy"] == "failure"  # the requested commit did not ship
    assert job.outputs["deploy"] == {"deploy_result": "rolled_back", "rollback": "succeeded"}
    assert droplet.running() == sha(droplet, "A")
    assert "rolled back" in job.summary() and "performed and verified" in job.summary()
    assert job.outcomes["post"] == "skipped"


def test_without_a_baseline_the_failure_is_reported_and_no_rollback_is_claimed(droplet):
    droplet.mark_bad("A", "B")  # production unhealthy before the run, and B is bad too
    job = Job(droplet, "deploy", sha(droplet, "B")).run_all()

    assert job.outputs["baseline"]["result"] == "failed"
    assert "::warning title=No known-good baseline" in job.transcript["baseline"]
    assert job.outcomes["deploy"] == "failure"
    assert job.outputs["deploy"]["rollback"] == "no-known-good"
    assert droplet.running() == sha(droplet, "B")  # left as it is, and the summary says so
    assert "No rollback was possible" in job.summary()


def test_a_failing_public_page_check_fails_the_run_after_a_verified_deploy(droplet):
    job = Job(droplet, "deploy", sha(droplet, "B"), post_outcome="failure").run_all()

    assert job.outputs["deploy"]["deploy_result"] == "verified"
    assert "public-page check failed" in job.summary()


def test_droplet_output_is_kept_off_the_log_and_the_raw_copy_is_deleted(droplet):
    job = Job(droplet, "deploy", sha(droplet, "B"))
    for name in ("Prepare runner directories", "ssh", "baseline", "deploy"):
        job.run_step(name)

    shown = job.transcript["deploy"]
    assert "hunter2-SECRET" not in shown and "postgres://" not in shown
    assert "DEPLOY VERIFIED: commit %s is running" % sha(droplet, "B") in shown
    assert "withheld" in shown
    raw = (job.temp / "deploy-logs" / "deploy.log").read_text()
    assert "hunter2-SECRET" in raw  # kept on the runner only ...
    job.run_step("Remove the key, the pinned host key and the raw logs")
    assert not (job.temp / "deploy-logs").exists()  # ... and deleted with it


def test_the_droplets_stderr_in_the_first_ssh_step_is_filtered(droplet):
    job = Job(droplet, "dry-run", sha(droplet, "B"))
    job.extra_env = {"FAKE_SSH_STDERR": "token=SECRET-FROM-DROPLET\nssh: connect to host x port 22: Connection timed out"}
    for name in ("Prepare runner directories", "ssh"):
        job.run_step(name)

    shown = job.transcript["ssh"]
    assert job.outcomes["ssh"] == "success"
    assert "SECRET-FROM-DROPLET" not in shown
    assert "ssh: connect to host x port 22: Connection timed out" in shown   # the client's own error is kept
    assert "withheld" in shown


def test_a_failed_connection_stops_the_run_and_still_shows_the_clients_error(droplet):
    job = Job(droplet, "dry-run", sha(droplet, "B"))
    job.extra_env = {"FAKE_SSH_STDERR": "Permission denied (publickey).", "FAKE_SSH_FAIL": "1"}
    job.run_all()

    assert job.outcomes["ssh"] == "failure"
    assert "Permission denied (publickey)." in job.transcript["ssh"]
    assert "could not read the commit that is running on the droplet" in job.transcript["ssh"]
    assert job.outcomes["dry"] == "skipped"
    assert "SSH setup or the connection to the droplet failed" in job.summary()
    assert not any(c.startswith("docker compose") for c in droplet.docker_calls())


NON_RECORDING_VERIFIER = """#!/bin/bash
# Passes a --skip-deploy verification but never records the last verified commit.
echo "DEPLOY VERIFIED: commit $1 is running, mounted and reachable."
exit 0
"""


def test_a_baseline_that_passes_but_records_no_rollback_target_is_reported_as_failed(droplet):
    """The baseline only counts as passed when the script also left a last-verified
    commit behind; without one there is nothing for an automatic rollback to use."""
    job = Job(droplet, "deploy", sha(droplet, "B"))
    (job.ws / "scripts" / "deploy_verified.sh").write_text(NON_RECORDING_VERIFIER, encoding="utf-8", newline="\n")
    for name in ("Prepare runner directories", "ssh", "baseline"):
        job.run_step(name)

    assert "DEPLOY VERIFIED" in job.transcript["baseline"]           # the stand-in did pass (exit status 0)
    assert job.outcomes["baseline"] == "success"                     # the step warns; it does not stop the run
    assert job.outputs["baseline"]["result"] == "failed"
    assert "::warning title=No known-good baseline" in job.transcript["baseline"]
    assert not (job.temp / "deploy-state" / "last-verified-sha").exists()
