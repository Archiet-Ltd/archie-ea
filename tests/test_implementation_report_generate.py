"""Tests for GET /implementation/reports/generate.

Proves the report page renders (200) with gaps present and with none,
using the real route against the consolidated `gaps` table.

The implementation_planning blueprint is gated behind a feature flag and
the USE_NEW_APPLICATIONS env var.  This module sets both at import time
and defines its own module-scoped app so the session-scoped conftest.py
fixture (which runs earlier) does not lock in the wrong configuration.
"""

import os
import uuid

import pytest

PASSWORD = "test-password-123"

os.environ["USE_NEW_APPLICATIONS"] = "true"


@pytest.fixture(scope="module")
def _module_app():
    """Module-scoped app so the feature flag is seen at create_app time."""
    os.environ.setdefault("FLASK_CONFIG", "testing")
    from app import create_app

    application = create_app("testing")
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    return application


def _seed_feature_flag(db_session):
    from app.models.feature_flags import FeatureFlag, FeatureState, FeatureType

    existing = FeatureFlag.query.filter_by(
        key="architecture_implementation_planning"
    ).first()
    if existing is not None:
        return existing
    flag = FeatureFlag(
        key="architecture_implementation_planning",
        name="Architecture Implementation Planning",
        description="Enable the implementation planning module",
        feature_type=FeatureType.FUNCTIONALITY,
        state=FeatureState.STABLE,
        enabled=True,
    )
    db_session.add(flag)
    db_session.flush()
    return flag


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"RPT {label} {suffix}", slug=f"rpt-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="User").first()
    suffix = uuid.uuid4().hex[:6]
    user = User(
        first_name="RPT",
        last_name=f"User-{suffix}",
        email=f"rpt-{suffix}@example.test",
        password=PASSWORD,
        confirmed=True,
        organization_id=org.id,
        role=role,
        is_org_admin=False,
        is_platform_admin=False,
    )
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "viewer", granted_by_id=user.id)
    db_session.flush()
    return user


def _make_gap(db_session, org, label="test-gap"):
    from app.models.implementation_migration import Gap

    suffix = uuid.uuid4().hex[:8]
    gap = Gap(
        organization_id=org.id,
        name=f"{label}-{suffix}",
        description="A test gap for report generation",
        gap_type="coverage",
        priority="high",
        severity="medium",
        resolution_status="identified",
    )
    db_session.add(gap)
    db_session.flush()
    return gap


def test_gap_to_dict_returns_expected_fields(app, db_session, make_org):
    """Gap.to_dict() returns the fields the report route expects."""
    from app.models.implementation_migration import Gap

    org = make_org("gap-dict")
    gap = _make_gap(db_session, org, "dict-gap")
    db_session.commit()

    d = gap.to_dict()
    assert d["id"] == gap.id
    assert d["name"] == gap.name
    assert d["priority"] == "high"
    assert d["severity"] == "medium"
    assert d["resolution_status"] == "identified"
    assert "description" in d
    assert "gap_type" in d


def test_deliverable_to_dict_returns_expected_fields(app, db_session, make_org):
    """Deliverable.to_dict() returns the fields the report route expects."""
    from app.models.implementation_migration import Deliverable, WorkPackage

    org = make_org("del-dict")
    wp = WorkPackage(
        organization_id=org.id,
        name=f"wp-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(wp)
    db_session.flush()

    suffix = uuid.uuid4().hex[:8]
    d = Deliverable(
        name=f"del-{suffix}",
        description="A test deliverable",
        work_package_id=wp.id,
        delivery_status="planned",
        deliverable_type="document",
    )
    db_session.add(d)
    db_session.commit()

    dd = d.to_dict()
    assert dd["id"] == d.id
    assert dd["name"] == d.name
    assert dd["delivery_status"] == "planned"
    assert "description" in dd


def test_report_route_renders_with_gaps(app, db_session, login_as, client):
    """The report route returns 200 when gaps exist."""
    _seed_feature_flag(db_session)
    org = _org(db_session, "with-gaps")
    user = _user(db_session, org)
    _make_gap(db_session, org, "coverage-gap")
    db_session.commit()

    login_as(client, user)
    resp = client.get("/implementation/reports/generate")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.get_json()}"
    data = resp.get_json()
    assert data["success"] is True
    assert data["report"]["summary"]["total_gaps"] >= 1


def test_report_route_renders_with_no_gaps(app, db_session, login_as, client):
    """The report route returns 200 when no gaps exist."""
    _seed_feature_flag(db_session)
    org = _org(db_session, "no-gaps")
    user = _user(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/implementation/reports/generate")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.get_json()}"
    data = resp.get_json()
    assert data["success"] is True
    assert data["report"]["summary"]["total_gaps"] == 0