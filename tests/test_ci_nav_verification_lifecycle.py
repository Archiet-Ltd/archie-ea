"""CI lifecycle for the navigation-route verification evidence.

``route_verification.json`` is deliberately ignored, local test-run evidence.
The gate is meaningful only when the same CI job first creates a fresh file by
running the non-browser suite with the audit plugin.
"""

import inspect
from pathlib import Path

from scripts.verify import build_gates, load_baseline


REPO = Path(__file__).resolve().parent.parent


def _ci_jobs() -> dict:
    """The CI workflow's jobs, parsed."""
    import yaml

    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    return yaml.safe_load(workflow)["jobs"]


def _step_index(steps: list, needle: str) -> int:
    """Index of the first step whose ``run`` contains ``needle``, or -1."""
    return next((i for i, step in enumerate(steps) if needle in step.get("run", "")), -1)


def test_nav_verification_is_not_a_static_gate():
    """Ignored pytest evidence cannot be evaluated by the dependency-only job."""
    gates = {gate.name: gate for gate in build_gates(load_baseline())}

    assert "static" not in gates["nav-verified"].tags, (
        "nav-verified consumes ignored route_verification.json evidence, so it "
        "must run after pytest generates that evidence in the tests job"
    )


def test_full_verifier_collects_nav_evidence_before_enforcing_it():
    """A full local release run must not consume stale evidence from an earlier test."""
    gates = build_gates(load_baseline())
    names = [gate.name for gate in gates]
    tests_gate = gates[names.index("tests")]

    assert names.index("tests") < names.index("nav-verified"), (
        "the full verifier must run pytest before evaluating its generated "
        "route-verification evidence"
    )
    assert "scripts.route_verification_audit" in inspect.getsource(tests_gate.runner), (
        "the verifier's non-smoke pytest phase must load the route audit plugin"
    )


def test_tests_job_creates_fresh_nav_evidence_then_enforces_the_gate():
    """A stale local audit file must never satisfy CI's nav-verification gate.

    The suite runs as matrix shards, so the lifecycle spans two jobs: each
    shard deletes stale evidence, collects fresh endpoint coverage for its
    slice and uploads it whatever its outcome; the ``tests`` job then deletes
    any stale copy, rebuilds the file as the union of EVERY shard's evidence
    (refusing a missing shard) and enforces nav-verified once over it."""
    jobs = _ci_jobs()
    shard_job, combine_job = jobs["tests-shard"], jobs["tests"]
    shards = shard_job["strategy"]["matrix"]["shard"]

    # -- each shard: clean, then collect fresh evidence for its slice --------
    shard_steps = shard_job["steps"]
    run_index = _step_index(shard_steps, "-p scripts.route_verification_audit")
    assert run_index >= 0, "a shard must run pytest with the route audit plugin"
    run = shard_steps[run_index]["run"]
    assert "rm -f route_verification.json" in run, (
        "each shard must remove ignored, stale route-verification evidence "
        "before collecting fresh endpoint coverage"
    )
    assert run.index("rm -f route_verification.json") < run.index("pytest")
    assert "--ignore=tests/smoke" in run, (
        "the shards must together collect the full non-smoke suite; browser "
        "smoke tests remain a separate CI job"
    )
    assert "-p scripts.ci.test_sharding" in run
    assert shard_job["env"]["CI_SHARD_TOTAL"] == str(len(shards)), (
        "CI_SHARD_TOTAL must equal the matrix length, or some files run in no shard"
    )
    assert shard_steps[run_index]["env"]["CI_SHARD_INDEX"] == "${{ matrix.shard }}"
    assert sorted(shards) == list(range(len(shards)))

    uploads = [
        step for step in shard_steps[run_index + 1:]
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
        and "route_verification.json" in step["with"]["path"]
    ]
    assert uploads, "each shard must upload its fresh route_verification.json"
    assert uploads[0].get("if") == "always()", (
        "a shard with failing tests must still hand over its evidence"
    )
    assert "${{ matrix.shard }}" in uploads[0]["with"]["name"]

    # -- tests job: union every shard's evidence, then enforce once ---------
    assert combine_job["needs"] == "tests-shard"
    combine_steps = combine_job["steps"]
    download = next(
        (i for i, step in enumerate(combine_steps)
         if str(step.get("uses", "")).startswith("actions/download-artifact")),
        -1,
    )
    merge = _step_index(combine_steps, "scripts/ci/combine_shard_results.py")
    gate = _step_index(combine_steps, "python scripts/verify.py --gate nav-verified")
    assert 0 <= download < merge < gate, (
        "the tests job must download the shards' evidence, merge it, then "
        "enforce nav-verified against the merged file"
    )
    merge_run = combine_steps[merge]["run"]
    assert "rm -f route_verification.json" in merge_run
    assert merge_run.index("rm -f route_verification.json") < merge_run.index("combine_shard_results.py")
    expected = merge_run.split("combine_shard_results.py", 1)[1].split()[1]
    assert expected == str(len(shards)), (
        "the merge must demand evidence from every shard in the matrix"
    )


def test_every_shard_hands_its_coverage_data_to_the_combine_step():
    """Each shard writes .coverage.<shard>, a dotfile upload-artifact skips
    unless include-hidden-files is set; without it the tests job's coverage
    combine has no data at all."""
    jobs = _ci_jobs()
    shard_job = jobs["tests-shard"]
    coverage_file = shard_job["env"]["COVERAGE_FILE"]
    upload = next(
        step for step in shard_job["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    )

    assert coverage_file in upload["with"]["path"]
    if coverage_file.startswith("."):
        assert upload["with"].get("include-hidden-files") is True
    combine = _step_index(jobs["tests"]["steps"], "coverage combine")
    assert combine >= 0


def test_shard_coverage_records_checkout_relative_paths():
    """Shards run on different machines, each checking out into its own
    directory. With absolute paths, coverage combine keeps one entry per
    machine for the same file instead of merging them."""
    import configparser

    config = configparser.ConfigParser()
    assert config.read(REPO / ".coveragerc"), "no .coveragerc at the repository root"
    assert config.getboolean("run", "relative_files") is True


def test_merge_is_the_union_of_every_shard_and_refuses_a_missing_one(tmp_path, monkeypatch):
    import json
    import sys

    from scripts.ci import combine_shard_results as combine

    root = tmp_path / "artifacts"
    for shard, endpoints in enumerate((["a.index", "b.view"], ["b.view", "c.edit"])):
        (root / f"tests-shard-{shard}").mkdir(parents=True)
        (root / f"tests-shard-{shard}" / "route_verification.json").write_text(json.dumps(endpoints))
    monkeypatch.setattr(combine, "REPO", tmp_path)

    monkeypatch.setattr(sys, "argv", ["combine", str(root), "3"])
    assert combine.main() == 1
    assert not (tmp_path / "route_verification.json").exists()

    monkeypatch.setattr(sys, "argv", ["combine", str(root), "2"])
    assert combine.main() == 0
    merged = json.loads((tmp_path / "route_verification.json").read_text())
    assert merged == ["a.index", "b.view", "c.edit"]



