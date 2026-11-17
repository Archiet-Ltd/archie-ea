"""The demonstration organisation carries stakeholders and RACI assignments, a
second seed run adds none, and no other organisation receives any."""

from __future__ import annotations

import os

import pytest

from app.commands.seed_demo_company import seed_demo_company


@pytest.fixture
def seeded(app, db_session, make_org):
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        first = seed_demo_company()
        second = seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    from app.models.organization import Organization

    demo_id = db_session.query(Organization.id).filter_by(slug="lantern-quay").scalar()
    other = make_org("raci-other")
    db_session.commit()
    return {"demo": demo_id, "other": other.id, "first": first, "second": second}


def _rows(org_id):
    from app import db
    from app.models.organization_model import EnterpriseRaciAssignment

    return db.session.query(EnterpriseRaciAssignment).filter(
        EnterpriseRaciAssignment.organization_id == org_id).all()


def test_demo_company_has_stakeholders_with_assignments(seeded):
    rows = _rows(seeded["demo"])
    assert seeded["first"]["raci_assignments_created"] == len(rows) > 0
    assert {r.stakeholder_type for r in rows} == {"user"}
    assert len({r.stakeholder_id for r in rows}) >= 5
    assert {r.raci for r in rows} == {"R", "A", "C", "I"}


def test_second_run_adds_no_rows(seeded):
    assert seeded["second"]["raci_assignments_created"] == 0
    assert len(_rows(seeded["demo"])) == seeded["first"]["raci_assignments_created"]


def test_stakeholders_are_the_demo_companys_existing_people(seeded):
    from app import db
    from app.models.user import User

    user_ids = {u.id for u in db.session.query(User).filter(User.organization_id == seeded["demo"])}
    assert {r.stakeholder_id for r in _rows(seeded["demo"])} <= user_ids


def test_other_organisations_receive_nothing(seeded):
    assert _rows(seeded["other"]) == []
