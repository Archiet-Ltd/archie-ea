"""Restore drill workflow contract."""

from pathlib import Path

import yaml


WORKFLOW = Path(".github/workflows/restore-drill.yml")


def load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_restore_drill_runs_only_on_schedule_and_manual_dispatch():
    workflow = load_workflow()
    triggers = workflow.get(True, workflow.get("on"))

    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] == [{"cron": "17 3 1 * *"}]
    assert "push" not in triggers
    assert "pull_request" not in triggers


def test_restore_drill_job_runs_the_wrapper_and_prints_timing():
    workflow = load_workflow()
    job = workflow["jobs"]["restore-drill"]
    run_step = next(step for step in job["steps"] if step.get("name") == "Run restore drill and print timing")

    assert "bash scripts/restore_drill.sh" in run_step["run"]
    assert "RESTORE_TARGET_TIME" in run_step["env"]
    assert "date -u +%Y-%m-%dT%H:%M:%SZ" in run_step["run"]
