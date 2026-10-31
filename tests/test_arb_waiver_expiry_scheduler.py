"""Scheduler wiring contracts for the optional typed ARB expiry job."""

from __future__ import annotations

import ast
import inspect

from flask import Flask

# Every job init_scheduler registers on an app with only the waiver settings configured. The jobs the
# waiver job must leave alone are exactly these; dropping one of them makes the preservation tests fail.
ESTABLISHED_JOBS = {
    "ea_workflow_scheduler",
    "data_maturity_digest",
    "company_signals_refresh",
    "executive_summary",
    "error_digest",
    "signup_alerts",
    "signup_report",
    "owner_digest",
    "date_alerts",
    "approval_escalation",
    "teams_subscription_renewal",
    "event_log_relay",
    "change_feed_consumer",
    "event_log_partition_maintenance",
}
WAIVER_JOB = "typed_arb_waiver_expiry"
# Registered only when their own interval setting exists, which this bare test app does not carry.
CONFIG_GATED_JOBS = {
    "capability_projection",
    "derived_facts_recompute",
    "model_health_scan",
    "graph_projection_refresh",
}


class _Scheduler:
    def __init__(self, optional_add_error=None):
        self.jobs = []
        self.started = False
        self.optional_add_error = optional_add_error

    def add_job(self, **kwargs):
        if (
            kwargs["id"] == "typed_arb_waiver_expiry"
            and self.optional_add_error is not None
        ):
            raise self.optional_add_error
        self.jobs.append(kwargs)

    def get_jobs(self):
        _Job = type("_FakeJob", (), {"__init__": lambda self, jid: setattr(self, "id", jid)})
        return [_Job(j["id"]) for j in self.jobs]

    def get_job(self, job_id):
        for j in self.jobs:
            if j["id"] == job_id:
                return j
        return None

    def remove_job(self, job_id):
        self.jobs = [j for j in self.jobs if j["id"] != job_id]

    def start(self):
        self.started = True

    def pause(self):
        return None

    def shutdown(self, wait=False):
        return None


def _start(
    monkeypatch,
    *,
    organization_ids="",
    interval="5",
    optional_add_error=None,
    interval_trigger=None,
):
    import apscheduler.schedulers.background
    import apscheduler.triggers.interval

    from app._bootstrap.extensions import init_scheduler

    scheduler = _Scheduler(optional_add_error=optional_add_error)
    monkeypatch.setattr(
        apscheduler.schedulers.background,
        "BackgroundScheduler",
        lambda: scheduler,
    )
    if interval_trigger is not None:
        monkeypatch.setattr(
            apscheduler.triggers.interval,
            "IntervalTrigger",
            interval_trigger,
        )
    app = Flask("arb-expiry-scheduler-test")
    app.config.update(
        TESTING=False,
        ARB_CONDITION_EXPIRY_ORGANIZATION_IDS=organization_ids,
        ARB_CONDITION_EXPIRY_INTERVAL_MINUTES=interval,
    )
    init_scheduler(app)
    return scheduler


def test_scheduler_disabled_preserves_established_jobs(monkeypatch):
    scheduler = _start(monkeypatch)

    assert scheduler.started is True
    assert {job["id"] for job in scheduler.jobs} == ESTABLISHED_JOBS


def test_scheduler_enabled_registers_typed_arb_expiry(monkeypatch):
    scheduler = _start(monkeypatch, organization_ids="41,42", interval="7")

    jobs = {job["id"]: job for job in scheduler.jobs}
    assert scheduler.started is True
    assert "typed_arb_waiver_expiry" in jobs
    assert set(jobs) == ESTABLISHED_JOBS | {WAIVER_JOB}
    assert str(jobs["typed_arb_waiver_expiry"]["trigger"]) == "interval[0:07:00]"


def test_malformed_optional_interval_does_not_disable_established_jobs(monkeypatch):
    scheduler = _start(monkeypatch, organization_ids="41", interval="invalid")

    assert scheduler.started is True
    assert {job["id"] for job in scheduler.jobs} == ESTABLISHED_JOBS


def test_overflowing_optional_trigger_does_not_disable_established_jobs(monkeypatch):
    from apscheduler.triggers.interval import IntervalTrigger

    def overflow_only_for_arb(*args, **kwargs):
        if kwargs.get("minutes") == 10**100:
            raise OverflowError("interval is too large")
        return IntervalTrigger(*args, **kwargs)

    scheduler = _start(
        monkeypatch,
        organization_ids="41",
        interval=str(10**100),
        interval_trigger=overflow_only_for_arb,
    )

    assert scheduler.started is True
    assert {job["id"] for job in scheduler.jobs} == ESTABLISHED_JOBS


def test_optional_add_job_failure_does_not_disable_established_jobs(monkeypatch):
    scheduler = _start(
        monkeypatch,
        organization_ids="41",
        optional_add_error=RuntimeError("job store rejected optional job"),
    )

    assert scheduler.started is True
    assert {job["id"] for job in scheduler.jobs} == ESTABLISHED_JOBS


def test_every_job_the_scheduler_registers_is_listed_here():
    from app._bootstrap.extensions import init_scheduler

    tree = ast.parse(inspect.getsource(init_scheduler))
    registered = {
        keyword.value.value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "add_job"
        for keyword in call.keywords
        if keyword.arg == "id" and isinstance(keyword.value, ast.Constant)
    }
    assert registered == ESTABLISHED_JOBS | CONFIG_GATED_JOBS | {WAIVER_JOB}
