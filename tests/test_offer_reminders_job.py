"""The Startup credit reminders: the scheduled job survives start-up and reaches only the right organisation."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

import pytest

REMINDER_JOB = "offer_reminders"


class _RecordingScheduler:
    """Stands in for the background scheduler: keeps every job init_scheduler adds, runs none."""

    def __init__(self):
        self.jobs = {}

    def add_job(self, **kwargs):
        self.jobs[kwargs["id"]] = kwargs

    def get_jobs(self):
        return [type("Job", (), {"id": job_id})() for job_id in self.jobs]

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)

    def start(self):
        return None

    def pause(self):
        return None

    def shutdown(self, wait=False):
        return None


def _start_scheduler(monkeypatch, app, remove_undeclared=True):
    """Build the scheduler the way the app does at start-up; return the jobs recorded as added."""
    import apscheduler.schedulers.background

    from app._bootstrap import extensions
    from app.jobs import tenant_safe_job

    scheduler = _RecordingScheduler()
    added = []
    real_add = scheduler.add_job

    def record(**kwargs):
        added.append(kwargs)
        real_add(**kwargs)

    scheduler.add_job = record
    monkeypatch.setattr(apscheduler.schedulers.background, "BackgroundScheduler", lambda: scheduler)
    monkeypatch.setattr(extensions, "_scheduler_belongs_in_this_process", lambda: True)
    if not remove_undeclared:
        monkeypatch.setattr(tenant_safe_job, "_remove_undeclared_jobs", lambda _scheduler: None)
    # The real app, started as production starts it, with every optional job's setting present.
    monkeypatch.setitem(app.config, "TESTING", False)
    for key, value in (
        ("ARB_CONDITION_EXPIRY_ORGANIZATION_IDS", "1"),
        ("ARB_CONDITION_EXPIRY_INTERVAL_MINUTES", "5"),
        ("DERIVED_RECOMPUTE_INTERVAL_MINUTES", "10"),
        ("CAPABILITY_PROJECTION_INTERVAL_MINUTES", "15"),
        ("MODEL_HEALTH_SCAN_INTERVAL_MINUTES", "10"),
        ("GRAPH_PROJECTION_INTERVAL_MINUTES", "10"),
    ):
        monkeypatch.setitem(app.config, key, value)
    extensions.init_scheduler(app)
    return app, scheduler, added


def test_every_job_the_app_registers_is_declared(app, monkeypatch):
    from app.jobs.tenant_safe_job import PLATFORM_JOBS, TENANT_JOBS

    _app, _scheduler, added = _start_scheduler(monkeypatch, app, remove_undeclared=False)
    registered = {job["id"] for job in added}
    assert REMINDER_JOB in registered
    assert registered - (PLATFORM_JOBS | TENANT_JOBS) == set()


def test_starting_the_app_removes_no_job(app, monkeypatch, caplog):
    with caplog.at_level(logging.ERROR):
        _app, scheduler, added = _start_scheduler(monkeypatch, app)
    assert "is in neither PLATFORM_JOBS nor TENANT_JOBS" not in caplog.text
    assert {job["id"] for job in added} == set(scheduler.jobs)
    assert REMINDER_JOB in scheduler.jobs


def _organisation_with_credit(db_session, make_org, label, ends_in):
    from app.models.org_role import OrgRole
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus
    from app.models.user import Role, User

    org = make_org(label)
    owner = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", first_name=label, last_name="Owner",
        organization_id=org.id, confirmed=True,
    )
    owner.role = Role.query.filter_by(name="Administrator").first()
    db_session.add(owner)
    db_session.flush()
    OrgRole.set_role(org.id, owner.id, "org_admin")
    db_session.add(Subscription(
        organization_id=org.id, plan=SubscriptionPlan.startup, status=SubscriptionStatus.trialing,
        seats_purchased=10, offer="startup_credit", current_period_end=datetime.utcnow() + ends_in,
    ))
    db_session.commit()
    return org, owner


@pytest.fixture
def mailbox(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app._bootstrap._digest_emails._safe_send_email",
        lambda _app, subject, recipients, body: sent.append((subject, list(recipients), body)) or True,
    )
    return sent


@pytest.mark.parametrize("days", [14, 3])
def test_the_reminder_goes_to_the_organisation_whose_credit_ends_then(
    app, monkeypatch, db_session, make_org, mailbox, days,
):
    from app.models.models import Notification

    # Ends a little inside the threshold, so the reminder is due now.
    org_a, owner_a = _organisation_with_credit(db_session, make_org, "ending", timedelta(days=days) - timedelta(hours=1))
    org_b, owner_b = _organisation_with_credit(db_session, make_org, "later", timedelta(days=30))
    _app, scheduler, _added = _start_scheduler(monkeypatch, app)
    run = scheduler.jobs[REMINDER_JOB]["func"]

    # Another organisation on a shared database may also be near the end of its credit: only this test's own
    # organisations are looked at.
    _bystander, owner_c = _organisation_with_credit(db_session, make_org, "alsoending", timedelta(days=days) - timedelta(hours=1))
    ours = {owner_a.email, owner_b.email}

    def mine():
        return [m for m in mailbox if ours & set(m[1])]

    run()
    assert len(mine()) == 1
    subject, recipients, body = mine()[0]
    assert recipients == [owner_a.email]
    assert owner_b.email not in recipients
    ends = (datetime.utcnow() + timedelta(days=days) - timedelta(hours=1)).strftime("%d %b %Y").lstrip("0")
    for text in (f"end on {ends}", f"{days} days from now", "Buy Startup before then"):
        assert text in body

    db_session.expire_all()
    assert Notification.query.filter_by(user_id=owner_a.id, event_type="startup_credit_ending").count() == 1
    assert Notification.query.filter_by(user_id=owner_b.id, event_type="startup_credit_ending").count() == 0

    run()
    assert len(mine()) == 1
    assert Notification.query.filter_by(user_id=owner_a.id, event_type="startup_credit_ending").count() == 1
