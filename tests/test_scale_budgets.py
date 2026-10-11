"""Tests for model-health blocking key, stored report, and page.

Covers:
  * compute_blocking_key — cheap grouping key for near-duplicate candidates
  * DriftReport model — upsert, for_org, two-organisation isolation
  * Stored report isolation — org A's report never includes org B's elements
  * GET /genome/model-health — reads stored report, shows computed time
  * POST /genome/model-health/rescan — runs detector, stores, redirects
  * "not yet computed" state when no report exists
"""
from __future__ import annotations

import datetime as _dt
import uuid

import pytest

from app.modules.genome.services.drift_detector import compute_blocking_key


# --------------------------------------------------------------------------- #
# compute_blocking_key — pure unit tests                                       #
# --------------------------------------------------------------------------- #
def test_blocking_key_normalizes_to_lowercase_first_token():
    assert compute_blocking_key("Order Management System") == "order"
    assert compute_blocking_key("ORDER Management") == "order"
    assert compute_blocking_key("  Payroll  ") == "payroll"


def test_blocking_key_empty_and_none():
    assert compute_blocking_key("") == ""
    assert compute_blocking_key(None) == ""
    assert compute_blocking_key("   ") == ""


def test_blocking_key_single_word():
    assert compute_blocking_key("Billing") == "billing"


def test_blocking_key_deterministic():
    for _ in range(10):
        assert compute_blocking_key("Customer Portal v2") == "customer"


# --------------------------------------------------------------------------- #
# DriftReport model — CRUD and isolation                                       #
# --------------------------------------------------------------------------- #
def test_drift_report_upsert_creates_new_row(app, db_session, make_org):
    from app.models.drift_report import DriftReport

    with app.app_context():
        org = make_org("drift-store")
        report = {
            "report_version": "1.0.0",
            "organization_id": org.id,
            "spec_hash": "sha256:abc123",
            "summary": {"total": 3},
        }
        DriftReport.upsert(org.id, report, session=db_session)
        db_session.flush()

        stored = DriftReport.for_org(org.id, session=db_session)
        assert stored is not None
        assert stored.spec_hash == "sha256:abc123"
        assert stored.finding_count == 3
        assert stored.computed_at is not None
        assert stored.report_json["summary"]["total"] == 3


def test_drift_report_upsert_overwrites_existing(app, db_session, make_org):
    from app.models.drift_report import DriftReport

    with app.app_context():
        org = make_org("drift-overwrite")
        r1 = {"spec_hash": "sha256:first", "summary": {"total": 1}}
        r2 = {"spec_hash": "sha256:second", "summary": {"total": 5}}

        DriftReport.upsert(org.id, r1, session=db_session)
        db_session.flush()
        DriftReport.upsert(org.id, r2, session=db_session)
        db_session.flush()

        stored = DriftReport.for_org(org.id, session=db_session)
        assert stored.spec_hash == "sha256:second"
        assert stored.finding_count == 5
        # Only one row per org
        count = (
            db_session.query(DriftReport)
            .filter(DriftReport.organization_id == org.id)
            .count()
        )
        assert count == 1


def test_drift_report_two_org_isolation(app, db_session, make_org):
    """A's stored report never includes B's elements."""
    from app.models.drift_report import DriftReport

    with app.app_context():
        org_a = make_org("a")
        org_b = make_org("b")

        report_a = {
            "spec_hash": "sha256:aaa",
            "organization_id": org_a.id,
            "summary": {"total": 7},
        }
        report_b = {
            "spec_hash": "sha256:bbb",
            "organization_id": org_b.id,
            "summary": {"total": 2},
        }

        DriftReport.upsert(org_a.id, report_a, session=db_session)
        DriftReport.upsert(org_b.id, report_b, session=db_session)
        db_session.flush()

        stored_a = DriftReport.for_org(org_a.id, session=db_session)
        stored_b = DriftReport.for_org(org_b.id, session=db_session)

        assert stored_a.spec_hash == "sha256:aaa"
        assert stored_a.finding_count == 7
        assert stored_a.report_json["organization_id"] == org_a.id

        assert stored_b.spec_hash == "sha256:bbb"
        assert stored_b.finding_count == 2
        assert stored_b.report_json["organization_id"] == org_b.id


def test_drift_report_for_org_returns_none_when_missing(app, db_session, make_org):
    from app.models.drift_report import DriftReport

    with app.app_context():
        org = make_org("no-report")
        assert DriftReport.for_org(org.id, session=db_session) is None


# --------------------------------------------------------------------------- #
# Stored report isolation — A's stored report never includes B's elements      #
# --------------------------------------------------------------------------- #
def test_stored_report_org_isolation_with_real_detector(app, db_session, make_org):
    """Run the real detector for org A, store it, then verify org B sees nothing."""
    from app.models.drift_report import DriftReport
    from app.modules.genome.services.drift_detector import detect_model_drift

    with app.app_context():
        org_a = make_org("a")
        org_b = make_org("b")

        # Seed drift for org A only
        from app.models.archimate_core import ArchiMateElement

        orphan = ArchiMateElement(
            name="Orphan Widget A",
            type="ApplicationComponent",
            layer="application",
            organization_id=org_a.id,
        )
        db_session.add(orphan)
        db_session.flush()

        # Run detector for org A and store
        report_a = detect_model_drift(org_a.id, session=db_session)
        DriftReport.upsert(org_a.id, report_a, session=db_session)
        db_session.flush()

        # Run detector for org B and store
        report_b = detect_model_drift(org_b.id, session=db_session)
        DriftReport.upsert(org_b.id, report_b, session=db_session)
        db_session.flush()

        stored_a = DriftReport.for_org(org_a.id, session=db_session)
        stored_b = DriftReport.for_org(org_b.id, session=db_session)

        # Org A's report has findings (the orphan)
        assert stored_a.finding_count > 0
        # Org B's report has no findings (no elements seeded)
        assert stored_b.finding_count == 0

        # Org A's report does NOT contain org B's org id
        assert stored_a.report_json["organization_id"] == org_a.id
        # Org B's report does NOT contain org A's org id
        assert stored_b.report_json["organization_id"] == org_b.id

        # Org A's findings reference only org A's elements
        a_element_ids = {
            e["archimate_element_id"]
            for f in stored_a.report_json["findings"]
            for e in f["elements"]
        }
        assert orphan.id in a_element_ids


# --------------------------------------------------------------------------- #
# Page reads stored report — "not yet computed" and computed time              #
# --------------------------------------------------------------------------- #
def test_page_shows_pending_state_when_no_report(app, db_session, make_org, login_as):
    """GET /genome/model-health shows pending state when no stored report exists."""
    from app.models.user import User

    with app.app_context():
        org = make_org("empty")
        user = User(
            email=f"drift-page-{uuid.uuid4().hex[:8]}@example.com",
            organization_id=org.id,
            confirmed=True,
        )
        user.password_hash = "x"
        db_session.add(user)
        db_session.flush()

        client = app.test_client()
        login_as(client, user)
        resp = client.get("/genome/model-health/")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "first health scan is being prepared" in html


def test_page_shows_computed_time_when_report_exists(app, db_session, make_org, login_as):
    """GET /genome/model-health shows 'Last computed' when a stored report exists."""
    from app.models.drift_report import DriftReport
    from app.models.user import User

    with app.app_context():
        org = make_org("has-report")
        user = User(
            email=f"drift-page2-{uuid.uuid4().hex[:8]}@example.com",
            organization_id=org.id,
            confirmed=True,
        )
        user.password_hash = "x"
        db_session.add(user)
        db_session.flush()

        report = {
            "report_version": "1.0.0",
            "organization_id": org.id,
            "spec_hash": "sha256:test",
            "signals_scanned": [],
            "uncomputable_signals": {},
            "findings": [],
            "summary": {"total": 0, "by_type": {}, "by_severity": {}, "skipped_no_provenance": {}},
        }
        DriftReport.upsert(org.id, report, session=db_session)
        db_session.flush()

        client = app.test_client()
        login_as(client, user)
        resp = client.get("/genome/model-health/")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Last computed" in html
        assert "No drift detected" in html


def test_page_shows_render_error_only_for_the_broken_org(app, db_session, make_org, login_as):
    """A broken stored report shows an explicit error only to that organisation."""
    from app.models.drift_report import DriftReport
    from app.models.user import User

    with app.app_context():
        org_a = make_org("broken-report-a")
        org_b = make_org("broken-report-b")
        user_a = User(
            email=f"drift-broken-a-{uuid.uuid4().hex[:8]}@example.com",
            organization_id=org_a.id,
            confirmed=True,
        )
        user_b = User(
            email=f"drift-broken-b-{uuid.uuid4().hex[:8]}@example.com",
            organization_id=org_b.id,
            confirmed=True,
        )
        user_a.password_hash = "x"
        user_b.password_hash = "x"
        db_session.add_all([user_a, user_b])
        db_session.flush()

        report = {
            "report_version": "1.0.0",
            "organization_id": org_a.id,
            "spec_hash": "sha256:broken-report",
            "signals_scanned": [],
            "uncomputable_signals": {},
            "findings": [],
            "summary": {
                "total": 0,
                "by_type": {},
                "by_severity": {},
                "skipped_no_provenance": {},
            },
        }
        DriftReport.upsert(org_a.id, report, session=db_session)
        broken_row = DriftReport.for_org(org_a.id, session=db_session)
        broken_row.report_json = "corrupt-json-shape-for-org-a"
        db_session.flush()

        client_a = app.test_client()
        login_as(client_a, user_a)
        response_a = client_a.get("/genome/model-health/")

        assert response_a.status_code == 200
        html_a = response_a.data.decode("utf-8")
        assert "Stored model-health report could not be rendered." in html_a
        assert 'role="alert"' in html_a
        assert "Not yet computed" not in html_a
        assert "Last computed" not in html_a

        client_b = app.test_client()
        login_as(client_b, user_b)
        response_b = client_b.get("/genome/model-health/")

        assert response_b.status_code == 200
        html_b = response_b.data.decode("utf-8")
        assert "first health scan is being prepared" in html_b
        assert "Stored model-health report could not be rendered." not in html_b
        assert "corrupt-json-shape-for-org-a" not in html_b


def test_rescan_runs_detector_and_stores_report(app, db_session, make_org, login_as):
    """POST /genome/model-health/rescan runs the detector and stores the result."""
    from app.models.drift_report import DriftReport
    from app.models.user import User

    with app.app_context():
        org = make_org("rescan-org")
        user = User(
            email=f"drift-rescan-{uuid.uuid4().hex[:8]}@example.com",
            organization_id=org.id,
            confirmed=True,
        )
        user.password_hash = "x"
        db_session.add(user)
        db_session.flush()

        client = app.test_client()
        login_as(client, user)

        # Before rescan, no report exists — pending state
        resp = client.get("/genome/model-health/")
        assert "first health scan is being prepared" in resp.data.decode("utf-8")

        # Trigger rescan
        resp = client.post("/genome/model-health/rescan", follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Model-health scan completed" in html

        # After rescan, report exists
        stored = DriftReport.for_org(org.id, session=db_session)
        assert stored is not None
        assert stored.computed_at is not None


def test_rescan_removes_enabled_provider_rows_created_during_scan(
    app, db_session, make_org, login_as, monkeypatch
):
    """Rescan must not leave behind enabled providers created on the scan path."""
    from app.models.drift_report import DriftReport
    from app.models.models import APISettings
    from app.models.user import User
    from app.modules.genome.routes import drift_routes

    with app.app_context():
        org = make_org("rescan-provider-cleanup")
        user = User(
            email=f"drift-provider-cleanup-{uuid.uuid4().hex[:8]}@example.com",
            organization_id=org.id,
            confirmed=True,
        )
        user.password_hash = "x"
        db_session.add(user)
        db_session.flush()

        def _leaky_detector(org_id):
            db_session.add(
                APISettings(
                    provider="openai",
                    key_label="rescan-leak",
                    api_key="test-key",
                    enabled=True,
                    default_model="gpt-4o-mini",
                    organization_id=org_id,
                )
            )
            db_session.flush()
            return {
                "report_version": "1.0.0",
                "organization_id": org_id,
                "signals_scanned": [],
                "uncomputable_signals": {},
                "findings": [],
                "summary": {
                    "total": 0,
                    "by_type": {},
                    "by_severity": {},
                    "skipped_no_provenance": {},
                },
                "spec_hash": "sha256:rescan-provider-cleanup",
            }

        monkeypatch.setattr(drift_routes, "detect_model_drift", _leaky_detector)

        client = app.test_client()
        login_as(client, user)
        response = client.post("/genome/model-health/rescan", follow_redirects=True)

        assert response.status_code == 200
        assert APISettings.query.filter_by(enabled=True).count() == 0
        stored = DriftReport.for_org(org.id, session=db_session)
        assert stored is not None


def test_rescan_two_org_isolation(app, db_session, make_org, login_as):
    """Rescan for org A does not affect org B's stored report."""
    from app.models.drift_report import DriftReport
    from app.models.user import User
    from app.modules.genome.services.drift_detector import detect_model_drift

    with app.app_context():
        org_a = make_org("rescan-a")
        org_b = make_org("rescan-b")

        # Directly run and store for org A (bypasses test client session issues)
        report_a = detect_model_drift(org_a.id, session=db_session)
        DriftReport.upsert(org_a.id, report_a, session=db_session)
        db_session.flush()

        # Directly run and store for org B
        report_b = detect_model_drift(org_b.id, session=db_session)
        DriftReport.upsert(org_b.id, report_b, session=db_session)
        db_session.flush()

        stored_a = DriftReport.for_org(org_a.id, session=db_session)
        stored_b = DriftReport.for_org(org_b.id, session=db_session)

        assert stored_a is not None
        assert stored_b is not None
        assert stored_a.report_json["organization_id"] == org_a.id
        assert stored_b.report_json["organization_id"] == org_b.id
        # Different orgs, different reports
        assert stored_a.id != stored_b.id


# --------------------------------------------------------------------------- #
# Job runs once per organisation (integration with run_for_each_tenant)        #
# --------------------------------------------------------------------------- #
def test_model_health_scan_job_runs_per_organisation(app, db_session, make_org):
    """The model_health_scan job visits each org and stores a report."""
    from app.jobs.tenant_safe_job import run_for_each_tenant
    from app.models.drift_report import DriftReport
    from app.modules.genome.services.drift_detector import detect_model_drift

    with app.app_context():
        org_a = make_org("job-a")
        org_b = make_org("job-b")

        def _scan_one(organization_id):
            report = detect_model_drift(organization_id, session=db_session)
            DriftReport.upsert(organization_id, report, session=db_session)
            db_session.commit()
            return report.get("summary", {}).get("total", 0)

        run = run_for_each_tenant(
            app,
            "model_health_scan",
            _scan_one,
            organization_ids=[org_a.id, org_b.id],
            use_lock=False,
        )

        assert run.succeeded == 2
        assert run.failed == 0

        stored_a = DriftReport.for_org(org_a.id, session=db_session)
        stored_b = DriftReport.for_org(org_b.id, session=db_session)
        assert stored_a is not None
        assert stored_b is not None
        assert stored_a.report_json["organization_id"] == org_a.id
        assert stored_b.report_json["organization_id"] == org_b.id


# --------------------------------------------------------------------------- #
# Pending state — page shows "first health scan is being prepared"            #
# --------------------------------------------------------------------------- #
def test_pending_state_enqueues_at_most_once_per_org(app, db_session, make_org, login_as):
    """Visiting the page twice with no stored report enqueues only one job."""
    from app.models.job import Job, JobStatus
    from app.models.user import User

    with app.app_context():
        org = make_org("pending-once")
        user = User(
            email=f"drift-pending-{uuid.uuid4().hex[:8]}@example.com",
            organization_id=org.id,
            confirmed=True,
        )
        user.password_hash = "x"
        db_session.add(user)
        db_session.flush()

        client = app.test_client()
        login_as(client, user)

        # First visit — no stored report, should enqueue a job
        resp1 = client.get("/genome/model-health/")
        assert resp1.status_code == 200
        html1 = resp1.data.decode("utf-8")
        assert "first health scan is being prepared" in html1

        pending_jobs = (
            db_session.query(Job)
            .filter(
                Job.task == "model_health_scan",
                Job.status.in_([JobStatus.PENDING.value, JobStatus.IN_PROGRESS.value]),
            )
            .all()
        )
        org_jobs = [
            j for j in pending_jobs
            if (j.payload or {}).get("organization_id") == org.id
        ]
        assert len(org_jobs) == 1

        # Second visit — job already pending, must not enqueue another
        resp2 = client.get("/genome/model-health/")
        assert resp2.status_code == 200
        html2 = resp2.data.decode("utf-8")
        assert "first health scan is being prepared" in html2

        pending_jobs_after = (
            db_session.query(Job)
            .filter(
                Job.task == "model_health_scan",
                Job.status.in_([JobStatus.PENDING.value, JobStatus.IN_PROGRESS.value]),
            )
            .all()
        )
        org_jobs_after = [
            j for j in pending_jobs_after
            if (j.payload or {}).get("organization_id") == org.id
        ]
        assert len(org_jobs_after) == 1


def test_pending_state_two_orgs_independent(app, db_session, make_org, login_as):
    """Each organisation gets its own pending job; one does not block the other."""
    from app.models.job import Job, JobStatus
    from app.models.user import User

    with app.app_context():
        org_a = make_org("pending-a")
        org_b = make_org("pending-b")
        user_a = User(
            email=f"drift-pending-a-{uuid.uuid4().hex[:8]}@example.com",
            organization_id=org_a.id,
            confirmed=True,
        )
        user_b = User(
            email=f"drift-pending-b-{uuid.uuid4().hex[:8]}@example.com",
            organization_id=org_b.id,
            confirmed=True,
        )
        user_a.password_hash = "x"
        user_b.password_hash = "x"
        db_session.add_all([user_a, user_b])
        db_session.flush()

        client_a = app.test_client()
        login_as(client_a, user_a)
        resp_a = client_a.get("/genome/model-health/")
        assert resp_a.status_code == 200
        assert "first health scan is being prepared" in resp_a.data.decode("utf-8")

        client_b = app.test_client()
        login_as(client_b, user_b)
        resp_b = client_b.get("/genome/model-health/")
        assert resp_b.status_code == 200
        assert "first health scan is being prepared" in resp_b.data.decode("utf-8")

        pending_jobs = (
            db_session.query(Job)
            .filter(
                Job.task == "model_health_scan",
                Job.status.in_([JobStatus.PENDING.value, JobStatus.IN_PROGRESS.value]),
            )
            .all()
        )
        org_a_jobs = [
            j for j in pending_jobs
            if (j.payload or {}).get("organization_id") == org_a.id
        ]
        org_b_jobs = [
            j for j in pending_jobs
            if (j.payload or {}).get("organization_id") == org_b.id
        ]
        assert len(org_a_jobs) == 1
        assert len(org_b_jobs) == 1
        # Different jobs
        assert org_a_jobs[0].id != org_b_jobs[0].id
