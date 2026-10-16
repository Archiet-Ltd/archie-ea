"""Tenant scope for the two rationalization benefits aggregates:
/applications/rationalization/tracking (projected/actual savings sums) and
/applications/rationalization/api/calibration-report (measured/validated
tracker counts and variance). RationalizationBenefitsTracker carries no
organization_id of its own (not a TenantMixin model), so both aggregates
read every organisation's tracker rows unless scoped through a join to
ApplicationComponent (TenantMixin), which does carry the automatic
per-organization SELECT filter.

Follows tests/test_connector_config_tenant_scope.py's pattern: the shared
db_session / make_org / login_as fixtures from tests/conftest.py, never a
hand-rolled module-scoped app fixture.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# --------------------------------------------------------------------- #
# Helpers                                                                #
# --------------------------------------------------------------------- #


def _make_user(db_session, org_id, label):
    """A real User row pinned to *org_id*, with the Administrator role --
    same shape as tests/test_connector_config_tenant_scope.py's _make_admin."""
    from app.models.user import Permission, Role, User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="platform_administrator",
    )
    db_session.add(user)
    db_session.flush()

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(
            name="Administrator",
            permissions=Permission.ADMINISTER,
            index="main",
            default=False,
        )
        db_session.add(role)
        db_session.flush()
    user.role = role
    db_session.flush()
    return user


def _make_application(db_session, org_id, label):
    from app.models.application_portfolio import ApplicationComponent

    app_obj = ApplicationComponent(
        name=f"{label} {uuid.uuid4().hex[:8]}", organization_id=org_id
    )
    db_session.add(app_obj)
    db_session.flush()
    return app_obj


def _make_tracker(db_session, application_id, projected, actual, status="measured"):
    from app.models.application_rationalization import RationalizationBenefitsTracker

    tracker = RationalizationBenefitsTracker(
        application_id=application_id,
        projected_annual_savings=Decimal(str(projected)),
        actual_annual_savings=Decimal(str(actual)),
        tracking_status=status,
    )
    tracker.calculate_variance()
    db_session.add(tracker)
    db_session.flush()
    return tracker


@pytest.fixture
def org_a(make_org):
    return make_org("benefits-a")


@pytest.fixture
def org_b(make_org):
    return make_org("benefits-b")


@pytest.fixture
def user_a(db_session, org_a):
    return _make_user(db_session, org_a.id, "UserA")


@pytest.fixture
def user_b(db_session, org_b):
    return _make_user(db_session, org_b.id, "UserB")


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def org_b_with_measured_tracker(db_session, org_b):
    """Org B has one application with a measured tracker: projected 100000,
    actual 50000 -- the exact figures the brief specifies."""
    b_app = _make_application(db_session, org_b.id, "B App")
    tracker = _make_tracker(
        db_session, b_app.id, projected=100000, actual=50000, status="measured"
    )
    db_session.commit()
    return b_app, tracker


# --------------------------------------------------------------------- #
# /applications/rationalization/tracking -- savings sums                #
# --------------------------------------------------------------------- #


class TestTrackingPageAggregateIsTenantScoped:
    def test_org_a_sees_zero_savings_when_only_org_b_has_trackers(
        self, db_session, org_a, org_b_with_measured_tracker, user_a, client, login_as
    ):
        login_as(client, user_a)
        resp = client.get("/applications/rationalization/tracking")

        assert resp.status_code == 200
        body = resp.data.decode()
        assert "100,000" not in body, (
            "TENANT LEAK: org A's tracking page showed org B's projected savings."
        )
        assert "50,000" not in body, (
            "TENANT LEAK: org A's tracking page showed org B's actual savings."
        )

    def test_org_b_still_sees_its_own_savings(
        self, db_session, org_a, org_b_with_measured_tracker, user_b, client, login_as
    ):
        login_as(client, user_b)
        resp = client.get("/applications/rationalization/tracking")

        assert resp.status_code == 200
        body = resp.data.decode()
        assert "100,000" in body, "org B's own projected savings should still render"
        assert "50,000" in body, "org B's own actual savings should still render"


# --------------------------------------------------------------------- #
# /applications/rationalization/api/calibration-report                  #
# --------------------------------------------------------------------- #


class TestCalibrationReportIsTenantScoped:
    def test_org_a_sees_zero_data_points_when_only_org_b_has_measured_trackers(
        self, db_session, org_a, org_b_with_measured_tracker, user_a, client, login_as
    ):
        login_as(client, user_a)
        resp = client.get("/applications/rationalization/api/calibration-report")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["calibration_data_points"] == 0, (
            "TENANT LEAK: org A's calibration report counted org B's measured trackers."
        )

    def test_org_b_still_sees_its_own_data_point(
        self, db_session, org_a, org_b_with_measured_tracker, user_b, client, login_as
    ):
        login_as(client, user_b)
        resp = client.get("/applications/rationalization/api/calibration-report")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["calibration_data_points"] == 1, (
            "org B's own measured tracker should still be counted"
        )
