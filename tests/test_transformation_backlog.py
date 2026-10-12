"""The transformation backlog lists unplanned work for one organisation, in priority order."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.models.implementation_migration import Gap
from app.models.unified_work_package import UnifiedWorkPackage
from app.modules.portfolio.services.transformation_backlog import transformation_backlog


def _user(db_session, org, label):
    from app.models.user import User

    user = User(
        email=f"backlog-{label}-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id,
        confirmed=True,
    )
    user.password = "not-used-in-tests-123"
    db_session.add(user)
    db_session.flush()
    return user


def _gap(db_session, org, name, priority="medium", status="identified"):
    gap = Gap(organization_id=org.id, name=name, priority=priority, resolution_status=status)
    db_session.add(gap)
    db_session.flush()
    return gap


def _wp(db_session, org, name, priority="medium", status="planned", start=None, gap=None):
    wp = UnifiedWorkPackage(
        organization_id=org.id,
        name=name,
        priority=priority,
        status=status,
        start_date=start,
        gap_id=gap.id if gap is not None else None,
    )
    db_session.add(wp)
    db_session.flush()
    return wp


@pytest.fixture
def two_orgs(db_session, make_org):
    return make_org("backlog-a"), make_org("backlog-b")


def _names(result):
    return [i["name"] for i in result["items"]]


def test_open_gap_without_work_package_is_listed_until_one_is_linked(app, db_session, two_orgs, tenant_ctx):
    org_a, _ = two_orgs
    gap = _gap(db_session, org_a, "No single customer view")
    with tenant_ctx(org_a.id):
        assert "No single customer view" in _names(transformation_backlog())
    _wp(db_session, org_a, "Build customer hub", start=dt.datetime(2027, 1, 4), gap=gap)
    with tenant_ctx(org_a.id):
        assert "No single customer view" not in _names(transformation_backlog())


def test_resolved_gap_and_finished_or_scheduled_work_are_not_listed(app, db_session, two_orgs, tenant_ctx):
    org_a, _ = two_orgs
    _gap(db_session, org_a, "Closed gap", status="resolved")
    _wp(db_session, org_a, "Done work", status="completed")
    _wp(db_session, org_a, "Dropped work", status="cancelled")
    _wp(db_session, org_a, "Scheduled work", start=dt.datetime(2027, 2, 1))
    _wp(db_session, org_a, "Waiting work")
    with tenant_ctx(org_a.id):
        assert _names(transformation_backlog()) == ["Waiting work"]


def test_order_is_recorded_priority_then_name(app, db_session, two_orgs, tenant_ctx):
    org_a, _ = two_orgs
    _gap(db_session, org_a, "b gap", priority="low")
    _gap(db_session, org_a, "a gap", priority="low")
    _wp(db_session, org_a, "urgent work", priority="critical")
    _gap(db_session, org_a, "odd gap", priority="whenever")
    _wp(db_session, org_a, "high work", priority="high")
    with tenant_ctx(org_a.id):
        result = transformation_backlog()
    assert _names(result) == ["urgent work", "high work", "a gap", "b gap", "odd gap"]
    assert result["gap_count"] == 3 and result["work_package_count"] == 2


def test_other_organisation_items_never_appear(app, db_session, two_orgs, tenant_ctx):
    org_a, org_b = two_orgs
    _gap(db_session, org_a, "A only gap")
    _wp(db_session, org_b, "B only work")
    with tenant_ctx(org_a.id):
        assert _names(transformation_backlog()) == ["A only gap"]
    with tenant_ctx(org_b.id):
        assert _names(transformation_backlog()) == ["B only work"]


def test_a_work_package_of_another_organisation_does_not_hide_a_gap(app, db_session, two_orgs, tenant_ctx):
    org_a, org_b = two_orgs
    gap = _gap(db_session, org_a, "Shared id gap")
    _wp(db_session, org_b, "Foreign link", start=dt.datetime(2027, 3, 1), gap=gap)
    with tenant_ctx(org_a.id):
        assert "Shared id gap" in _names(transformation_backlog())


def test_page_renders_for_each_organisation_and_empty_state(app, db_session, two_orgs, client, login_as):
    org_a, org_b = two_orgs
    _gap(db_session, org_a, "Visible to A")
    user_a, user_b = _user(db_session, org_a, "a"), _user(db_session, org_b, "b")
    db_session.commit()
    login_as(client, user_a)
    body = client.get("/portfolio/backlog").get_data(as_text=True)
    assert "Visible to A" in body
    login_as(client, user_b)
    body = client.get("/portfolio/backlog").get_data(as_text=True)
    assert "Visible to A" not in body
    assert "data-backlog-empty" in body
