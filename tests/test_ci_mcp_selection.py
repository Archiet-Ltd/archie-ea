"""Every test that needs the connector switched on is in the one MCP selection.

The selection is scripts/ci/mcp_selection.py. These tests fail when a test that
needs the OAuth or MCP routes is added outside it (it would fail with a 404 in
the shards and the fast lane), when a listed test no longer exists, and when the
workflow stops running the selection with the connector on.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts.ci import mcp_selection as sel

REPO = Path(__file__).resolve().parent.parent
CI = REPO / ".github" / "workflows" / "ci.yml"

# Signs that a test file builds on the connector's routes or flag.
NEEDS_CONNECTOR = re.compile(
    r"MCP_ENABLED"
    r"|oauth_provider\.token"
    r"|tools/list"
    r"|[\"'](/mcp|/oauth/(token|authorize|register|revoke)|/\.well-known/oauth)"
)

# Files that match the pattern without needing the routes, with the reason.
NOT_NEEDED = {
    "tests/test_ci_mcp_selection.py": "this guard names the patterns",
    "app/modules/oauth_provider/tests/test_mcp_flag.py": "asserts the connector is absent; runs in the ordinary session",
}
# Browser journeys start their own server with the connector on (tests/smoke/conftest.py).
SELF_HOSTED_PREFIXES = ("tests/smoke/",)


def _test_files():
    for top in ("tests", "app/modules"):
        for path in (REPO / top).rglob("test_*.py"):
            yield path.relative_to(REPO).as_posix()


def _in_selection(rel: str, source: str) -> bool:
    if rel.startswith(sel.MCP_DIRS):
        return True
    names = re.findall(r"^\s*def (test_\w+)", source, re.M)
    listed = [n for n in names if f"{rel}::{n}" in sel.MCP_TESTS]
    return bool(listed)


def test_every_test_that_needs_the_connector_is_in_the_selection():
    outside = []
    for rel in sorted(_test_files()):
        if rel.startswith(SELF_HOSTED_PREFIXES) or rel in NOT_NEEDED:
            continue
        source = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if NEEDS_CONNECTOR.search(source) and not _in_selection(rel, source):
            outside.append(rel)
    assert not outside, (
        "these files use the connector's routes or flag but are not in the MCP selection "
        f"(add them to scripts/ci/mcp_selection.py): {outside}"
    )


def test_a_file_in_the_selection_that_does_not_need_the_connector_is_named_here():
    """Keeps NOT_NEEDED honest: each entry must still exist."""
    for rel in NOT_NEEDED:
        assert (REPO / rel).is_file(), f"{rel} no longer exists; remove it from NOT_NEEDED"


def test_every_listed_test_exists():
    for entry in sel.MCP_TESTS:
        rel, name = entry.split("::")
        source = (REPO / rel).read_text(encoding="utf-8")
        assert re.search(rf"^def {re.escape(name)}\(", source, re.M), f"{entry} is not defined"
    for d in sel.MCP_DIRS:
        assert any((REPO / d).glob("test_*.py")), f"{d} holds no tests"
    for f in sel.NEEDS_MCP_OFF:
        assert (REPO / f).is_file()


def test_classifier():
    assert sel.is_mcp_test("app/modules/mcp/tests/test_tools.py::test_x")
    assert sel.is_mcp_test("app/modules/oauth_provider/tests/test_oauth_flow.py::T::test_x[a]")
    assert not sel.is_mcp_test("app/modules/oauth_provider/tests/test_mcp_flag.py::test_x")
    assert sel.is_mcp_test("tests/test_api_token_migration.py::test_migration_runs_twice_without_error")
    assert not sel.is_mcp_test("tests/test_api_token_migration.py::test_migration_chain_has_a_single_head_and_short_revision_ids")
    assert not sel.is_mcp_test("tests/test_models.py::test_x")


def test_enabled_reads_the_flag_the_app_reads():
    assert sel.enabled({"MCP_ENABLED": "true"})
    assert sel.enabled({"MCP_ENABLED": "1"})
    assert not sel.enabled({"MCP_ENABLED": "false"})
    assert not sel.enabled({})


def _steps(job_id):
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]
    return jobs[job_id]["steps"]


def _mcp_steps(job_id):
    return [s for s in _steps(job_id) if (s.get("env") or {}).get("MCP_ENABLED") == "true"]


def test_the_shards_run_the_selection_once_with_the_connector_on():
    steps = _mcp_steps("tests-shard")
    assert len(steps) == 1, "exactly one shard step runs the MCP selection"
    assert steps[0].get("if") == "matrix.shard == 0"
    assert "scripts/ci/mcp_selection.py --paths" in steps[0]["run"]
    assert steps[0]["env"]["PUBLIC_BASE_URL"].startswith("https://")


def test_the_fast_lane_runs_the_selection_with_the_connector_on():
    steps = _mcp_steps("fast-tests")
    assert len(steps) == 1, "the fast lane runs its selected MCP tests in one second session"
    assert steps[0]["env"]["PUBLIC_BASE_URL"].startswith("https://")
    assert "fast-tests.txt" in steps[0]["run"]


def test_no_other_job_sets_the_flag_for_a_pytest_session():
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]
    for job_id, job in jobs.items():
        if job_id in ("tests-shard", "fast-tests"):
            continue
        for step in job.get("steps", []):
            if (step.get("env") or {}).get("MCP_ENABLED") and "pytest" in str(step.get("run", "")):
                raise AssertionError(f"{job_id} runs pytest with the connector on; the selection would run twice")
