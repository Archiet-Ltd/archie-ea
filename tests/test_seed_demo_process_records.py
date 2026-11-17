"""The demonstration organisation's business process elements each carry a process record, a second seed
run makes none, and another organisation's process elements are left as they were."""

from __future__ import annotations

import os

import pytest

from app.commands.seed_demo_company import seed_demo_company


@pytest.fixture
def seeded(app, db_session, make_org):
    from app.models import ArchiMateElement

    other = make_org("process-records-other")
    element = ArchiMateElement(name="Handle claims", type="BusinessProcess", layer="business", organization_id=other.id)
    db_session.add(element)
    db_session.commit()
    other_id, element_id = other.id, element.id
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        first = seed_demo_company()
        second = seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    from app.models.organization import Organization

    demo_id = db_session.query(Organization.id).filter_by(slug="lantern-quay").scalar()
    return {"demo": demo_id, "other": other_id, "other_element": element_id, "first": first, "second": second}


def test_every_demo_process_element_has_its_record_and_a_second_run_adds_none(seeded):
    from app.models import ArchiMateElement
    from app.models.process_data import BusinessProcess

    elements = ArchiMateElement.query.filter_by(organization_id=seeded["demo"], type="BusinessProcess").all()
    records = {p.archimate_element_id for p in BusinessProcess.query.filter_by(organization_id=seeded["demo"])}
    assert len(elements) > 10
    assert {e.id for e in elements} <= records
    assert seeded["second"]["process_records_created"] == 0


def test_another_organisations_process_element_gets_no_record(seeded):
    from app.models.process_data import BusinessProcess

    assert BusinessProcess.query.filter_by(archimate_element_id=seeded["other_element"]).count() == 0
