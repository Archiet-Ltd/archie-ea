"""The recorded-demand table for work packages (capacity slice S3a: table only, no reader).

Demand is recorded as rows, never inferred: a work package with no rows has no recorded demand.
The table is tenant-fenced, and the database refuses effort that is not positive, a period that
ends before it starts, and a unit outside person_day / fte.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError


def _org_and_package(db_session, make_org):
    from app.models.unified_work_package import UnifiedWorkPackage

    org = make_org("demand")
    package = UnifiedWorkPackage(name=f"WP {uuid.uuid4().hex[:6]}", business_capability="Test capability")
    db_session.add(package)
    db_session.flush()
    return org, package


def _row(org, package, **overrides):
    from app.models.work_package_resource_demand import WorkPackageResourceDemand

    fields = dict(
        organization_id=org.id, work_package_id=package.id, role="Solution architect",
        effort_value=10, effort_unit="person_day",
        period_start=date(2026, 10, 1), period_end=date(2026, 10, 31),
    )
    fields.update(overrides)
    return WorkPackageResourceDemand(**fields)


def test_a_demand_row_round_trips(app, db_session, make_org):
    org, package = _org_and_package(db_session, make_org)
    row = _row(org, package, source="planning workshop")
    db_session.add(row)
    db_session.flush()

    data = row.to_dict()
    assert data["role"] == "Solution architect" and data["effort_unit"] == "person_day"
    assert data["capability_id"] is None and data["source"] == "planning workshop"
    assert package.resource_demand == [row]


def test_a_work_package_with_no_rows_has_no_recorded_demand(app, db_session, make_org):
    _org, package = _org_and_package(db_session, make_org)

    assert package.resource_demand == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"effort_value": 0},
        {"effort_value": -1},
        {"effort_unit": "hours"},
        {"period_start": date(2026, 11, 1), "period_end": date(2026, 10, 1)},
    ],
    ids=["zero-effort", "negative-effort", "unknown-unit", "period-ends-before-it-starts"],
)
def test_the_database_refuses_invalid_demand(app, db_session, make_org, overrides):
    org, package = _org_and_package(db_session, make_org)
    db_session.add(_row(org, package, **overrides))

    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_period_of_one_day_and_an_fte_row_are_valid(app, db_session, make_org):
    org, package = _org_and_package(db_session, make_org)
    db_session.add(_row(org, package, effort_unit="fte", effort_value=0.5,
                        period_start=date(2026, 10, 5), period_end=date(2026, 10, 5)))

    db_session.flush()


def test_the_table_is_tenant_fenced(app, db_session, make_org):
    from app.models.mixins import TenantMixin
    from app.models.work_package_resource_demand import WorkPackageResourceDemand

    assert issubclass(WorkPackageResourceDemand, TenantMixin)
    assert WorkPackageResourceDemand.__table__.c.organization_id.nullable is False


def test_the_demand_is_listed_as_a_tenant_table():
    from pathlib import Path

    listed = Path(__file__).resolve().parent.parent.joinpath("scripts", "tenant_tables.txt").read_text().split()

    assert "work_package_resource_demand" in listed


def test_demand_rows_are_isolated_between_two_real_organisations(app, db_session, make_org, tenant_ctx):
    from app.models.work_package_resource_demand import WorkPackageResourceDemand

    org_a, package_a = _org_and_package(db_session, make_org)
    org_b, package_b = _org_and_package(db_session, make_org)
    row_a = _row(org_a, package_a, role="Org A architect")
    row_b = _row(org_b, package_b, role="Org B architect")
    db_session.add_all([row_a, row_b])
    db_session.commit()

    with tenant_ctx(org_a.id):
        seen_a = WorkPackageResourceDemand.query.all()
        assert [r.role for r in seen_a] == ["Org A architect"]
        assert package_a.resource_demand == [row_a]

    with tenant_ctx(org_b.id):
        seen_b = WorkPackageResourceDemand.query.all()
        assert [r.role for r in seen_b] == ["Org B architect"]
        assert package_b.resource_demand == [row_b]
