"""TB-0069: architecture monitoring baselines and alerts are per-organisation.

Fixtures (app, db_session, make_org, client, login_as) come from
tests/conftest.py. The architecture monitoring API is off by default
(ARCHITECTURE_MONITORING_API_ENABLED) so it is registered directly on the
shared session app the same way
app/modules/architecture/tests/test_architecture.py:170 turns it on for its
own test, rather than booting a second Flask app that would not share the
db_session fixture's transaction.
"""

from __future__ import annotations

import uuid

import pytest


def _enable_monitoring_api(app, monkeypatch):
    """Mount the monitoring blueprint on the shared session app.

    A Flask Blueprint locks its setup methods (``before_request`` etc.) the
    first time it is registered to ANY app -- that lock is a process-global
    attribute on the blueprint object, not a per-app one. This module's own
    real registration path (app/modules/architecture/v2/__init__.py) always
    calls ``mark_blueprint_guardrailed()`` -- which adds exactly one
    ``before_request`` handler, guarded by its own idempotency flag -- before
    ``register_blueprint()``. Registering here without doing the same first
    would let this fixture consume the one-shot lock, so the next real
    ``create_app()`` call in the process (e.g.
    app/modules/architecture/tests/test_architecture.py's own
    ARCHITECTURE_MONITORING_API_ENABLED test) fails trying to add that same
    handler afterwards. Calling it here, in the same order production does,
    keeps that call a no-op wherever it runs later.
    """
    monkeypatch.setenv("ARCHITECTURE_MONITORING_API_ENABLED", "true")
    if "architecture_monitoring" not in app.blueprints:
        from app.core.compat import mark_blueprint_guardrailed
        from app.modules.architecture.routes.architecture_monitoring_routes import (
            architecture_monitoring_bp,
        )

        mark_blueprint_guardrailed(architecture_monitoring_bp)
        # Flask._check_setup_finished (and flasgger's own wrapped
        # add_url_rule, which this blueprint's swagger-decorated views
        # trigger a second time from inside register_blueprint itself) both
        # key off this one flag. The shared session app has already handled
        # a first request by the time a later test module runs this fixture
        # (e.g. after tests/test_account_mail_flows.py in the same
        # invocation) -- flip it off for the registration call only, so
        # both guards see a fresh app, then restore it immediately.
        got_first_request = app._got_first_request
        app._got_first_request = False
        try:
            app.register_blueprint(architecture_monitoring_bp)
        finally:
            app._got_first_request = got_first_request


def _user(db_session, org, *, enterprise_role):
    """An admin-capable user (write permissions) with the given persona."""
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=f"tb0069-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Monitoring",
        last_name="Tester",
        organization_id=org.id,
        role=admin_role,
        is_org_admin=True,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def monitoring_users(app, db_session, make_org, monkeypatch):
    """Two organisations, each with a signed-in-capable enterprise architect."""
    _enable_monitoring_api(app, monkeypatch)

    org_a = make_org("monitoring-a")
    org_b = make_org("monitoring-b")
    user_a = _user(db_session, org_a, enterprise_role="enterprise_architect")
    user_b = _user(db_session, org_b, enterprise_role="enterprise_architect")
    db_session.commit()

    return {"org_a": org_a, "org_b": org_b, "user_a": user_a, "user_b": user_b}


def _capture_baseline(client, login_as, user, name):
    login_as(client, user)
    resp = client.post(
        "/api/architecture-monitoring/baseline",
        json={"name": name, "set_as_active": True},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["baseline"]["id"]


class TestBaselineTenancy:
    """Baselines captured by one organisation are invisible to another."""

    def test_org_b_list_does_not_show_org_a_baseline(
        self, client, login_as, monitoring_users
    ):
        baseline_id = _capture_baseline(
            client, login_as, monitoring_users["user_a"], "Org A Baseline"
        )

        login_as(client, monitoring_users["user_b"])
        resp = client.get("/api/architecture-monitoring/baseline")
        assert resp.status_code == 200
        listed_ids = [b["id"] for b in resp.get_json()["data"]["baselines"]]
        assert baseline_id not in listed_ids

    def test_org_a_list_shows_its_own_baseline(self, client, login_as, monitoring_users):
        baseline_id = _capture_baseline(
            client, login_as, monitoring_users["user_a"], "Org A Baseline Visible"
        )

        login_as(client, monitoring_users["user_a"])
        resp = client.get("/api/architecture-monitoring/baseline")
        assert resp.status_code == 200
        listed_ids = [b["id"] for b in resp.get_json()["data"]["baselines"]]
        assert baseline_id in listed_ids

    def test_org_b_cannot_fetch_org_a_baseline(self, client, login_as, monitoring_users):
        baseline_id = _capture_baseline(
            client, login_as, monitoring_users["user_a"], "Org A Fetch Target"
        )

        login_as(client, monitoring_users["user_b"])
        resp = client.get(f"/api/architecture-monitoring/baseline/{baseline_id}")
        assert resp.status_code == 404

    def test_org_b_cannot_delete_org_a_baseline(self, client, login_as, monitoring_users):
        baseline_id = _capture_baseline(
            client, login_as, monitoring_users["user_a"], "Org A Delete Target"
        )

        login_as(client, monitoring_users["user_b"])
        resp = client.delete(f"/api/architecture-monitoring/baseline/{baseline_id}")
        assert resp.status_code == 404

        # And it is still there for A.
        login_as(client, monitoring_users["user_a"])
        resp = client.get(f"/api/architecture-monitoring/baseline/{baseline_id}")
        assert resp.status_code == 200

    def test_org_b_cannot_activate_org_a_baseline(self, client, login_as, monitoring_users):
        baseline_id = _capture_baseline(
            client, login_as, monitoring_users["user_a"], "Org A Activate Target"
        )

        login_as(client, monitoring_users["user_b"])
        resp = client.post(f"/api/architecture-monitoring/baseline/{baseline_id}/activate")
        assert resp.status_code == 404


class TestAlertTenancy:
    """An alert raised for one organisation's drift is invisible to another."""

    def test_alert_raised_for_org_a_not_listed_for_org_b(
        self, app, db_session, monitoring_users
    ):
        """Insert an alert directly (as capture would, via the tenant listener)
        and confirm the ORM tenant filter, not application code, hides it."""
        from flask import g

        from app.models.policy_monitoring import MonitoringAlert

        org_a_id = monitoring_users["org_a"].id
        org_b_id = monitoring_users["org_b"].id

        with app.test_request_context("/"):
            g.current_org_id = org_a_id
            alert = MonitoringAlert(
                alert_id=str(uuid.uuid4()),
                alert_type="new_gap",
                severity="warning",
                title="Org A Alert",
                description="raised for org A",
            )
            db_session.add(alert)
            db_session.commit()
            alert_id = alert.alert_id

        db_session.remove()

        with app.test_request_context("/"):
            g.current_org_id = org_b_id
            from app.models.policy_monitoring import MonitoringAlert as MAModel

            assert MAModel.query.filter_by(alert_id=alert_id).first() is None
            assert alert_id not in [a.alert_id for a in MAModel.query.all()]

        db_session.remove()

        with app.test_request_context("/"):
            g.current_org_id = org_a_id
            from app.models.policy_monitoring import MonitoringAlert as MAModel

            assert MAModel.query.filter_by(alert_id=alert_id).first() is not None


class TestUnassessedBaselineDrift:
    """Drift on an unassessed baseline is 'unknown', not a crash."""

    def test_drift_on_baseline_with_no_capability_snapshot_returns_unknown(
        self, app, db_session, monitoring_users
    ):
        from flask import g

        with app.test_request_context("/"):
            g.current_org_id = monitoring_users["org_a"].id

            from app.modules.architecture.services.architecture_monitoring_service import (
                ArchitectureMonitoringService,
            )

            service = ArchitectureMonitoringService()
            result = service.capture_baseline(name="Unassessed", created_by="tester")
            assert result["success"] is True
            baseline_id = result["baseline"]["id"]

            drift = service.analyze_drift(baseline_id)

        assert drift["success"] is True
        assert drift["status"] == "unknown"
        assert "reason" in drift

    def test_drift_route_returns_200_not_500_for_unassessed_baseline(
        self, client, login_as, monitoring_users
    ):
        baseline_id = _capture_baseline(
            client, login_as, monitoring_users["user_a"], "Unassessed Route"
        )

        login_as(client, monitoring_users["user_a"])
        resp = client.get(f"/api/architecture-monitoring/drift/{baseline_id}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "unknown"

    def test_drift_returns_unknown_not_crash_when_no_health_scores_exist(
        self, app, db_session, monitoring_users
    ):
        """Regression: a capability that has an assessed maturity level but no
        health score anywhere in the organisation makes average_health None
        (by design -- it means "nothing assessed", not zero). Comparing that
        None against another None used to raise
        ``TypeError: unsupported operand type(s) for -: 'NoneType' and
        'NoneType'`` inside _analyze_health_drift, which the route surfaced as
        a 400. It must report "unknown" instead."""
        from flask import g

        from app.models.unified_capability import UnifiedCapability

        org_id = monitoring_users["org_a"].id

        with app.test_request_context("/"):
            g.current_org_id = org_id

            cap = UnifiedCapability(
                name="Assessed But No Health",
                code=f"CAP{uuid.uuid4().hex[:8].upper()}",
                level=1,
                current_maturity_level=3,
            )
            db_session.add(cap)
            db_session.commit()

            from app.modules.architecture.services.architecture_monitoring_service import (
                ArchitectureMonitoringService,
            )

            service = ArchitectureMonitoringService()
            result = service.capture_baseline(name="No Health Scores", created_by="tester")
            assert result["success"] is True
            baseline_id = result["baseline"]["id"]

            drift = service.analyze_drift(baseline_id)

        assert drift["success"] is True
        assert drift["status"] == "unknown"
        assert "health" in drift["reason"].lower()

    def test_drift_route_returns_200_not_400_when_no_health_scores_exist(
        self, app, db_session, client, login_as, monitoring_users
    ):
        from flask import g

        from app.models.unified_capability import UnifiedCapability

        org_id = monitoring_users["org_a"].id

        with app.test_request_context("/"):
            g.current_org_id = org_id
            cap = UnifiedCapability(
                name="Assessed But No Health Route",
                code=f"CAP{uuid.uuid4().hex[:8].upper()}",
                level=1,
                current_maturity_level=3,
            )
            db_session.add(cap)
            db_session.commit()

        baseline_id = _capture_baseline(
            client, login_as, monitoring_users["user_a"], "No Health Scores Route"
        )

        login_as(client, monitoring_users["user_a"])
        resp = client.get(f"/api/architecture-monitoring/drift/{baseline_id}")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["status"] == "unknown"


class TestNoSharedState:
    """Two service instances never share baseline or alert data."""

    def test_two_instances_share_no_baseline_state(self, app, db_session, monitoring_users):
        from flask import g

        from app.modules.architecture.services.architecture_monitoring_service import (
            ArchitectureMonitoringService,
        )

        with app.test_request_context("/"):
            g.current_org_id = monitoring_users["org_a"].id
            instance_one = ArchitectureMonitoringService()
            result = instance_one.capture_baseline(name="Instance One Baseline", created_by="t1")
            assert result["success"] is True

            # A second, freshly constructed instance in the same organisation
            # must see the baseline via the database, not via any shared
            # class-level cache -- and no class attribute on the type itself
            # holds baseline or alert data for this to have leaked through.
            instance_two = ArchitectureMonitoringService()
            listed = instance_two.list_baselines()
            assert result["baseline"]["id"] in [b["id"] for b in listed["baselines"]]

        for attr in ("_baselines", "_alerts", "_active_baseline_id", "_db_loaded"):
            assert not hasattr(ArchitectureMonitoringService, attr), (
                f"{attr} must not be class-level state on ArchitectureMonitoringService"
            )

    def test_instance_mutation_does_not_leak_across_instances(self, app, monitoring_users):
        """Mutating one instance's configuration must not affect a sibling instance."""
        from flask import g

        from app.modules.architecture.services.architecture_monitoring_service import (
            ArchitectureMonitoringService,
        )

        with app.test_request_context("/"):
            g.current_org_id = monitoring_users["org_a"].id
            instance_one = ArchitectureMonitoringService()
            instance_two = ArchitectureMonitoringService()

            instance_one.configure_monitoring(scan_interval_minutes=120)
            instance_one.set_monitoring_status("paused")

            assert instance_two._scan_interval_minutes == 60
            assert instance_two.get_monitoring_status()["status"] == "active"
